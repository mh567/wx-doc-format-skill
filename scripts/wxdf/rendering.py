"""AST driven DOCX rendering with a small, auditable RenderMap."""

from __future__ import annotations

import copy
import io
import posixpath
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from lxml import etree

from table_formatting import normalize_table, table_role
from text_utils import CAPTION_SEQUENCE_FALLBACK, caption_parts

from .render_plan import build_render_plan
from .media import MediaEmbedder
from .model import effective_outline_level
from .template import TemplateBundle


class RenderError(RuntimeError):
    """Raised when a normalized node cannot be rendered with available resources."""


def _write_direct_numbering(paragraph: Any, num_id: int, ilvl: int) -> None:
    """Write an explicit list numbering reference on a paragraph.

    The renderer consumes the ``num_id`` / ``ilvl`` the normalized presentation
    resolved for the item, so the emitted reference follows the numbering
    instance the policy bound the item's style role to.  ``get_or_add_numPr``
    inserts ``w:numPr`` in ``CT_PPr`` schema order, so a paragraph that already
    carries ``w:ind``, ``w:jc`` or ``w:rPr`` stays schema valid.
    """

    properties = paragraph._p.get_or_add_pPr()
    num_pr = properties.get_or_add_numPr()
    for child in list(num_pr):
        num_pr.remove(child)
    ilvl_element = OxmlElement("w:ilvl")
    ilvl_element.set(qn("w:val"), str(max(0, int(ilvl))))
    num_pr.append(ilvl_element)
    num_id_element = OxmlElement("w:numId")
    num_id_element.set(qn("w:val"), str(int(num_id)))
    num_pr.append(num_id_element)


def _numbering_levels_by_num_id(doc: Any) -> dict[int, set[int]] | None:
    """Return the levels each numbering instance defines, or ``None`` when absent."""

    try:
        numbering = doc.part.numbering_part.element
    except (KeyError, NotImplementedError, AttributeError, ValueError):
        return None
    abstract_levels: dict[str, set[int]] = {}
    for abstract in numbering.findall(qn("w:abstractNum")):
        abstract_id = abstract.get(qn("w:abstractNumId"))
        levels: set[int] = set()
        for level in abstract.findall(qn("w:lvl")):
            try:
                levels.add(int(level.get(qn("w:ilvl"), "0")))
            except (TypeError, ValueError):
                levels.add(0)
        abstract_levels[str(abstract_id)] = levels
    result: dict[int, set[int]] = {}
    for num in numbering.findall(qn("w:num")):
        reference = num.find(qn("w:abstractNumId"))
        if reference is None:
            continue
        levels = set(abstract_levels.get(reference.get(qn("w:val")), set()))
        for override in num.findall(qn("w:lvlOverride")):
            try:
                levels.add(int(override.get(qn("w:ilvl"), "0")))
            except (TypeError, ValueError):
                levels.add(0)
        try:
            result[int(num.get(qn("w:numId")))] = levels
        except (TypeError, ValueError):
            continue
    return result


def _profile_styles(normalized: Mapping[str, Any]) -> Mapping[str, str]:
    return normalized.get("presentation", {}).get("profile", {}).get("resolved_styles", {})


def _materialize_synthesized_numbering(doc: Any, instances: Any) -> None:
    """Append the per-chapter list numbering instances the policy requested.

    Each entry is a ``w:num`` that references the same abstract definition as the
    style role's template instance, so it is a fresh counter that restarts the
    ordered sequence (one counter per ``w:num``).  Normalization allocates the
    ids and the readback audit expects them, so the renderer writes exactly those
    ids instead of allocating its own.
    """

    if not isinstance(instances, (list, tuple)) or not instances:
        return
    try:
        numbering = doc.part.numbering_part.element
    except (KeyError, NotImplementedError, AttributeError, ValueError):
        return
    existing = {num.get(qn("w:numId")) for num in numbering.findall(qn("w:num"))}
    for entry in instances:
        if not isinstance(entry, Mapping):
            continue
        num_id = entry.get("num_id")
        abstract_id = entry.get("abstract_num_id")
        if num_id is None or abstract_id is None or str(num_id) in existing:
            continue
        num_element = OxmlElement("w:num")
        num_element.set(qn("w:numId"), str(int(num_id)))
        reference = OxmlElement("w:abstractNumId")
        reference.set(qn("w:val"), str(int(abstract_id)))
        num_element.append(reference)
        override = OxmlElement("w:lvlOverride")
        override.set(qn("w:ilvl"), "0")
        start = OxmlElement("w:startOverride")
        start.set(qn("w:val"), "1")
        override.append(start)
        num_element.append(override)
        numbering.append(num_element)
        existing.add(str(num_id))


def _text(inline_content: list[Mapping[str, Any]], resources: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for item in inline_content:
        kind = item.get("kind")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "break":
            parts.append("\n")
        elif kind == "tab":
            parts.append("\t")
        elif kind == "link":
            parts.append(_text(item.get("children", []), resources))
        elif kind == "image":
            resource_id = item.get("resource_id")
            if resource_id not in resources:
                raise RenderError(f"resource {resource_id!r} is unavailable for rendering")
        elif kind in {"field", "opaque_inline"}:
            cached = item.get("cached_result")
            if isinstance(cached, str):
                parts.append(cached)
    return "".join(parts)


def _image_resource_ids(inline_content: list[Mapping[str, Any]]) -> list[str]:
    resource_ids: list[str] = []
    for item in inline_content:
        kind = item.get("kind")
        if kind == "image" and item.get("resource_id") is not None:
            resource_ids.append(str(item["resource_id"]))
        elif kind == "link":
            resource_ids.extend(_image_resource_ids(item.get("children", [])))
    return resource_ids


def _attach_image_artifact_metadata(output_path: Path, render_map: dict[str, dict[str, Any]]) -> None:
    with ZipFile(output_path) as archive:
        document_root = etree.fromstring(archive.read("word/document.xml"))
        relationships_root = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
        drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
        relationship_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        relationships = {
            relationship.get("Id"): relationship.get("Target")
            for relationship in relationships_root
            if relationship.get("Type", "").endswith("/image")
        }
        image_targets = []
        for blip in document_root.findall(f".//{{{drawing_namespace}}}blip"):
            target = relationships.get(blip.get(f"{{{relationship_namespace}}}embed"))
            image_targets.append(
                posixpath.normpath(posixpath.join("word", target.lstrip("/"))) if target else None
            )
        media_parts = sorted(name for name in archive.namelist() if name.startswith("word/media/"))

    render_map["__artifact__"] = {"kind": "artifact", "media_parts": media_parts}
    target_index = 0
    for item in render_map.values():
        resource_ids = item.get("image_resources", [])
        if not resource_ids:
            continue
        count = len(resource_ids)
        item["image_targets"] = image_targets[target_index:target_index + count]
        item["media_parts"] = media_parts
        target_index += count


def _apply_marks(run: Any, item: Mapping[str, Any]) -> None:
    marks = set(item.get("marks", []))
    if "strong" in marks:
        run.bold = True
    if "emphasis" in marks:
        run.italic = True


def _write_image(paragraph: Any, image_token: Mapping[str, Any], media: MediaEmbedder) -> Any:
    run = paragraph.add_run()
    media.embed(run, image_token)
    return run


def _move_run_to_hyperlink(paragraph: Any, hyperlink: Any, run: Any) -> None:
    paragraph._p.remove(run._r)
    hyperlink.append(run._r)


def _write_link(
    paragraph: Any,
    item: Mapping[str, Any],
    media: MediaEmbedder,
    resources: Mapping[str, Any] | None = None,
) -> None:
    target = str(item.get("target") or "")
    hyperlink = OxmlElement("w:hyperlink")
    if target.startswith("#"):
        hyperlink.set(qn("w:anchor"), target[1:])
    else:
        relationship_id = paragraph.part.relate_to(target, RT.HYPERLINK, is_external=True)
        hyperlink.set(qn("r:id"), relationship_id)
    paragraph._p.append(hyperlink)
    for child in item.get("children", []):
        kind = child.get("kind")
        if kind == "text":
            run = paragraph.add_run(str(child.get("text") or ""))
            _apply_marks(run, child)
            run.underline = True
            _move_run_to_hyperlink(paragraph, hyperlink, run)
        elif kind == "break":
            run = paragraph.add_run()
            run.add_break()
            _move_run_to_hyperlink(paragraph, hyperlink, run)
        elif kind == "tab":
            run = paragraph.add_run("\t")
            _move_run_to_hyperlink(paragraph, hyperlink, run)
        elif kind == "image":
            run = _write_image(paragraph, child, media)
            _move_run_to_hyperlink(paragraph, hyperlink, run)
        elif kind in {"field", "math", "bookmark", "opaque_inline"}:
            for element in _retained_inline_elements(child, resources or {}):
                hyperlink.append(element)


def _retained_inline_elements(
    item: Mapping[str, Any],
    resources: Mapping[str, Any],
) -> list[Any]:
    """Parse a retained source element back into the elements to insert.

    A formula, a field and a bookmark cannot be rebuilt from semantics, so the
    reader retained the source element and this returns it verbatim.  The payload
    is one element or a run sequence, so it is parsed inside a wrapper and the
    wrapper's children are returned.
    """

    resource_id = item.get("resource_id")
    payload = resources.get(resource_id) if resource_id is not None else None
    if not isinstance(payload, (bytes, bytearray)):
        return []
    try:
        wrapper = etree.fromstring(b"<wx-inline-fragment>" + bytes(payload) + b"</wx-inline-fragment>")
    except etree.XMLSyntaxError:
        return []
    return [child for child in list(wrapper)]


def _apply_paragraph_outline_level(paragraph: Any, record: Mapping[str, Any] | None) -> None:
    """Write the direct outline level a paragraph's presentation asks for.

    The template's TOC field builds its entries from outline levels, so a
    paragraph whose delivered level differs from its style's level states that
    level directly.  ``w:outlineLvl`` sits after ``w:jc`` and before ``w:rPr`` in
    the CT_PPr sequence, so it is inserted before the run properties when the
    paragraph carries any.
    """

    level = (record or {}).get("outline_level")
    if not isinstance(level, int) or isinstance(level, bool):
        return
    properties = paragraph._p.get_or_add_pPr()
    for existing in properties.findall(qn("w:outlineLvl")):
        properties.remove(existing)
    element = OxmlElement("w:outlineLvl")
    element.set(qn("w:val"), str(level))
    run_properties = properties.find(qn("w:rPr"))
    if run_properties is not None:
        run_properties.addprevious(element)
    else:
        properties.append(element)


def _apply_paragraph_alignment(paragraph: Any, record: Mapping[str, Any] | None) -> None:
    """Write the alignment a paragraph's presentation owns.

    A paragraph the template styles carries no record, so nothing is written and
    the style keeps deciding.  A record means the source authored an alignment
    the style cannot express, such as a centred image paragraph or a right
    aligned formula number.
    """

    alignment = str((record or {}).get("alignment") or "")
    if not alignment:
        return
    properties = paragraph._p.get_or_add_pPr()
    for existing in properties.findall(qn("w:jc")):
        properties.remove(existing)
    element = OxmlElement("w:jc")
    element.set(qn("w:val"), alignment)
    properties.append(element)


def _write_inline(
    paragraph: Any,
    inline_content: list[Mapping[str, Any]],
    media: MediaEmbedder,
    resources: Mapping[str, Any] | None = None,
) -> None:
    retained = resources or {}
    for item in inline_content:
        kind = item.get("kind")
        if kind == "text":
            run = paragraph.add_run(str(item.get("text") or ""))
            _apply_marks(run, item)
        elif kind == "break":
            paragraph.add_run().add_break()
        elif kind == "tab":
            paragraph.add_run("\t")
        elif kind == "link":
            _write_link(paragraph, item, media, retained)
        elif kind == "image":
            _write_image(paragraph, item, media)
        elif kind in {"field", "math", "bookmark", "opaque_inline"}:
            elements = _retained_inline_elements(item, retained)
            for element in elements:
                paragraph._p.append(element)
            if not elements:
                cached = str(item.get("cached_result") or "")
                if cached:
                    paragraph.add_run(cached)


def _caption_sequences(normalized: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the caption identifiers the template supplies for this run."""

    contract = normalized.get("presentation", {}).get("template_contract", {})
    captions = contract.get("captions", {}) if isinstance(contract, Mapping) else {}
    return captions if isinstance(captions, Mapping) else {}


def _caption_sequence(caption_type: str, sequences: Mapping[str, Any] | None) -> str:
    """Return the sequence identifier one caption kind is numbered with.

    The identifier is what Word matches a cross-reference item by, so it comes
    from the template (``SEQ 表`` / ``SEQ 图``) rather than from a name this
    module invents.  The WX label is the fallback for a template that shows no
    caption field of that kind.
    """

    kind = caption_type if caption_type in CAPTION_SEQUENCE_FALLBACK else "table"
    value = (sequences or {}).get(f"{kind}_sequence")
    return str(value) if isinstance(value, str) and value else CAPTION_SEQUENCE_FALLBACK[kind]


def _append_field_char(paragraph: Any, field_type: str) -> None:
    run = OxmlElement("w:r")
    char = OxmlElement("w:fldChar")
    char.set(qn("w:fldCharType"), field_type)
    run.append(char)
    paragraph._p.append(run)


def _append_instr_text(paragraph: Any, instruction: str) -> None:
    run = OxmlElement("w:r")
    text = OxmlElement("w:instrText")
    text.set(qn("xml:space"), "preserve")
    text.text = instruction
    run.append(text)
    paragraph._p.append(run)


def _write_caption(
    paragraph: Any,
    block: Mapping[str, Any],
    resources: Mapping[str, Any],
    sequences: Mapping[str, Any] | None = None,
) -> str:
    """Write a caption run and return its visible text.

    An authored caption keeps its full visible content in the Source AST, and its
    leading label and number are projected away here so that the sequence field
    owns the numbering.  A generated caption carries no content and renders as
    the bare sequence field.

    The field is written the way the template writes it: the label run, then a
    complex field whose identifier is the template's caption label.  A simple
    field and an invented identifier would render, but Word lists a
    cross-reference item by the sequence identifier, so the template's own
    spelling is what keeps 图 and 表 cross-referenceable.

    A caption whose source numbering is preserved, which is what an appendix
    caption carries (``表A.1``), keeps its authored number instead: the sequence
    field numbers the document body and cannot express an appendix number.

    The returned text matches what a reader sees for the paragraph, including
    the placeholder digit of the sequence field, so the saved output audit can
    compare paragraph text without special casing captions.
    """

    caption_type = str(block.get("caption_type") or "table")
    sequence = _caption_sequence(caption_type, sequences)
    inline_content = list(block.get("inline_content") or [])
    # A caption carries anchors its text refers to, so the retained markers are
    # copied around the rebuilt run: a cross reference that targets the caption
    # still resolves after the fields refresh.  A retained field keeps its text
    # in the rebuilt run instead, so the visible text is written once.
    starts = [item for item in inline_content if str(item.get("kind") or "") in {"bookmark", "math", "opaque_inline"} and str(item.get("role") or "") != "end"]
    ends = [item for item in inline_content if str(item.get("kind") or "") in {"bookmark", "math", "opaque_inline"} and str(item.get("role") or "") == "end"]
    for item in starts:
        for element in _retained_inline_elements(item, resources):
            paragraph._p.append(element)
    authored = _text([item for item in inline_content if str(item.get("kind") or "") != "bookmark"], resources)
    if block.get("source_number_preserved") and block.get("appendix_id"):
        paragraph.add_run(authored)
        for item in ends:
            for element in _retained_inline_elements(item, resources):
                paragraph._p.append(element)
        return authored
    paragraph.add_run(sequence)

    _append_field_char(paragraph, "begin")
    _append_instr_text(paragraph, f" SEQ {sequence} \\* ARABIC ")
    _append_field_char(paragraph, "separate")
    paragraph.add_run("1")
    _append_field_char(paragraph, "end")

    _, _, _, body = caption_parts(authored)
    paragraph.add_run(f" {body}")
    for item in ends:
        for element in _retained_inline_elements(item, resources):
            paragraph._p.append(element)
    return f"{sequence}1 {body}"


def _style_for_block(
    block: Mapping[str, Any],
    styles: Mapping[str, str],
    presentation: Mapping[str, Any] | None = None,
) -> str | None:
    appendix_role = block.get("appendix_role")
    if appendix_role == "title":
        return styles.get("appendix_title", "附录标题")
    if appendix_role == "heading":
        level = int(block.get("appendix_relative_level") or block.get("level") or 1)
        return styles.get(f"appendix_heading_{level}", f"附录{level}级标题")
    role = block.get("role")
    if role == "title":
        return styles.get("title", "文档标题")
    if role == "heading":
        level = effective_outline_level(block, presentation) or 1
        return styles.get(f"heading_{level}", "Heading 1")
    if role == "caption":
        return styles.get("caption", "Caption")
    if role in {"note", "numbered_note"}:
        return styles.get(str(role), "Normal")
    return styles.get("body", "Normal")


def _measurement(value: Any, *, default_type: str = "dxa") -> tuple[str | None, str | None]:
    if isinstance(value, Mapping):
        raw_value = value.get("value")
        raw_type = value.get("type")
        return (
            str(raw_value) if raw_value is not None else None,
            str(raw_type) if raw_type is not None else default_type,
        )
    return (str(value), default_type) if value is not None else (None, None)


def _apply_layout_table_contract(table: Any, layout: Any) -> None:
    """Restore captured source formatting for a semantic layout table."""

    tbl_pr = table._tbl.tblPr
    tbl_style = tbl_pr.find(qn("w:tblStyle"))
    if tbl_style is not None:
        tbl_pr.remove(tbl_style)
    if not isinstance(layout, Mapping):
        return

    if "autofit" in layout:
        table.autofit = bool(layout["autofit"])

    if "source_width" in layout:
        width_value, width_type = _measurement(layout["source_width"])
        width = tbl_pr.find(qn("w:tblW"))
        if width is None:
            width = OxmlElement("w:tblW")
            tbl_pr.append(width)
        if width_value is not None:
            width.set(qn("w:w"), width_value)
        if width_type is not None:
            width.set(qn("w:type"), width_type)

    source_margins = layout.get("source_margins")
    if isinstance(source_margins, Mapping):
        existing_margins = tbl_pr.find(qn("w:tblCellMar"))
        if existing_margins is not None:
            tbl_pr.remove(existing_margins)
        margins = OxmlElement("w:tblCellMar")
        for name in ("top", "left", "bottom", "right"):
            if name not in source_margins:
                continue
            margin_value, margin_type = _measurement(source_margins[name])
            margin = OxmlElement(f"w:{name}")
            if margin_value is not None:
                margin.set(qn("w:w"), margin_value)
            if margin_type is not None:
                margin.set(qn("w:type"), margin_type)
            margins.append(margin)
        tbl_pr.append(margins)

    source_borders = layout.get("borders")
    if source_borders == "none":
        source_borders = {
            name: {"val": "none"}
            for name in ("top", "left", "bottom", "right", "insideH", "insideV")
        }
    if isinstance(source_borders, Mapping):
        existing_borders = tbl_pr.find(qn("w:tblBorders"))
        if existing_borders is not None:
            tbl_pr.remove(existing_borders)
        borders = OxmlElement("w:tblBorders")
        for name in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border_contract = source_borders.get(name)
            if not isinstance(border_contract, Mapping):
                continue
            edge = OxmlElement(f"w:{name}")
            for attribute in ("val", "sz", "space", "color"):
                value = border_contract.get(attribute)
                if value is not None:
                    edge.set(qn(f"w:{attribute}"), str(value))
            borders.append(edge)
        tbl_pr.append(borders)

    row_heights = layout.get("row_heights")
    if isinstance(row_heights, list):
        for row, row_height in zip(table.rows, row_heights):
            if not isinstance(row_height, Mapping):
                row_height = {"value": row_height}
            tr_pr = row._tr.get_or_add_trPr()
            existing_height = tr_pr.find(qn("w:trHeight"))
            if existing_height is not None:
                tr_pr.remove(existing_height)
            value = row_height.get("value")
            rule = row_height.get("rule")
            if value is None and rule is None:
                continue
            height = OxmlElement("w:trHeight")
            if value is not None:
                height.set(qn("w:val"), str(value))
            if rule is not None:
                height.set(qn("w:hRule"), str(rule))
            tr_pr.append(height)


_TC_PR_AFTER_SHADING = (
    "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark",
)
_TBL_PR_AFTER_SHADING = ("w:tblLayout", "w:tblCellMar", "w:tblLook")


def _write_shading(properties: Any, fill: str, successors: tuple[str, ...]) -> None:
    """Write a solid-fill ``w:shd`` at the place the OOXML sequence allows.

    ``w:shd`` sits between the borders and the cell margins in both ``w:tcPr`` and
    ``w:tblPr``, so it is inserted before the first successor the element already
    has rather than appended.
    """

    existing = properties.find(qn("w:shd"))
    if existing is not None:
        properties.remove(existing)
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    for tag in successors:
        anchor = properties.find(qn(tag))
        if anchor is not None:
            anchor.addprevious(shading)
            return
    properties.append(shading)


def _apply_source_table_fills(table: Any, table_type: str, layout: Any) -> None:
    """Restore the source fill a table carries when the template does not own its look.

    A grey fill is how a source marks a code sample or a callout, and the captured
    fills are part of the table's layout evidence.  Only a data table loses its
    source fill, because the template owns how a data table looks; the role
    predicate is the one the rest of the table contract reads.
    """

    if table_role(table_type) == "data" or not isinstance(layout, Mapping):
        return
    fills = layout.get("fills")
    if not isinstance(fills, Mapping):
        return
    table_fill = fills.get("table")
    if isinstance(table_fill, str) and table_fill:
        _write_shading(table._tbl.tblPr, table_fill, _TBL_PR_AFTER_SHADING)
    cell_fills = fills.get("cells")
    if not isinstance(cell_fills, list):
        return
    index = 0
    seen_cells: set[Any] = set()
    for row in table.rows:
        for cell in row.cells:
            if cell._tc in seen_cells:
                continue
            seen_cells.add(cell._tc)
            fill = cell_fills[index] if index < len(cell_fills) else None
            if isinstance(fill, str) and fill:
                _write_shading(cell._tc.get_or_add_tcPr(), fill, _TC_PR_AFTER_SHADING)
            index += 1


def _apply_layout_paragraph_format(paragraph: Any, contract: Any) -> None:
    """Restore only the approved direct paragraph properties for layout cells."""

    if not isinstance(contract, Mapping):
        return
    properties = paragraph._p.get_or_add_pPr()

    def replace_element(element_name: str, attributes: Any, allowed: tuple[str, ...]) -> None:
        existing = properties.find(qn(f"w:{element_name}"))
        if existing is not None:
            properties.remove(existing)
        if not isinstance(attributes, Mapping):
            return
        captured = {
            name: attributes[name]
            for name in allowed
            if name in attributes and attributes[name] is not None
        }
        if not captured:
            return
        element = OxmlElement(f"w:{element_name}")
        for name, value in captured.items():
            element.set(qn(f"w:{name}"), str(value))
        properties.append(element)

    pagination = contract.get("pagination")
    if isinstance(pagination, Mapping):
        for element_name in ("keepNext", "keepLines", "pageBreakBefore", "widowControl"):
            if element_name not in pagination:
                continue
            existing = properties.find(qn(f"w:{element_name}"))
            if existing is not None:
                properties.remove(existing)
            element = OxmlElement(f"w:{element_name}")
            value = pagination[element_name]
            if value is not None:
                element.set(qn("w:val"), str(value))
            properties.append(element)
    replace_element(
        "spacing",
        contract.get("spacing"),
        (
            "before", "beforeLines", "beforeAutospacing",
            "after", "afterLines", "afterAutospacing", "line", "lineRule",
        ),
    )
    replace_element(
        "ind",
        contract.get("indentation"),
        (
            "left", "leftChars", "right", "rightChars",
            "firstLine", "firstLineChars", "hanging", "hangingChars",
            "start", "startChars", "end", "endChars",
        ),
    )
    alignment = contract.get("alignment")
    if alignment is not None:
        replace_element("jc", {"val": alignment}, ("val",))


def render(
    normalized: Mapping[str, Any],
    output_path: Path | str,
    *,
    template_path: Path | str | None = None,
    template_bundle: TemplateBundle | None = None,
    resources: Mapping[str, Any] | None = None,
    table_row_height_cm: float = 0.7,
    table_row_height_rule: str = "at-least",
) -> dict[str, Any]:
    """Render normalized nodes in children order and save a DOCX artifact."""

    resources = resources or {}
    plan = build_render_plan(normalized)
    ast = normalized["source_ast"]
    document = ast["document"]
    media = MediaEmbedder(document.get("resources", {}), resources, error_type=RenderError)
    styles = _profile_styles(normalized)
    caption_sequences = _caption_sequences(normalized)
    presentation_profile = normalized.get("presentation", {}).get("profile", {})
    if not isinstance(presentation_profile, Mapping):
        presentation_profile = {}
    numbering_ids = presentation_profile.get("numbering_ids", {})
    if not isinstance(numbering_ids, Mapping):
        numbering_ids = {}
    selected_template = template_bundle.path if template_bundle is not None else template_path
    doc = (
        Document(io.BytesIO(template_bundle.template_bytes))
        if template_bundle is not None
        else Document(str(selected_template)) if selected_template else Document()
    )
    # A per-chapter ordered restart binds each later chapter to its own
    # ``w:num`` instance.  The instances are materialized before the level
    # catalog is read, so the emitted-level clamp sees them.
    _materialize_synthesized_numbering(doc, normalized.get("presentation", {}).get("numbering_instances"))
    # Every list style role binds a numbering instance the template already
    # provides, so the produced document needs no synthesized numbering part.
    # The level catalog is read from the template-backed part the document
    # carries, which is what clamps an emitted level to what the bound instance
    # actually defines.
    supported_numbering_levels = _numbering_levels_by_num_id(doc)
    body = doc._element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    if template_bundle is not None:
        for index, fragment_xml in enumerate(template_bundle.toc_fragment_xml):
            body.insert(index, etree.fromstring(fragment_xml))
        if template_bundle.presentation_contract.get("toc", {}).get("update_fields_on_open"):
            settings = doc.settings.element
            update_fields = settings.find(qn("w:updateFields"))
            if update_fields is None:
                update_fields = OxmlElement("w:updateFields")
                settings.append(update_fields)
            update_fields.set(qn("w:val"), "true")
    render_map: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    presentation_outline = normalized.get("presentation", {}).get("outline", {})
    presentation_paragraphs = normalized.get("presentation", {}).get("paragraphs", {})
    paragraph_counter = 0
    table_counter = 0
    initial_cell_paragraphs: set[Any] = set()
    semantic_table_cells: set[Any] = set()
    layout_table_cells: set[Any] = set()
    pending_page_break = False

    def add_paragraph(
        node_id: str,
        block: Mapping[str, Any],
        *,
        list_style: str | None = None,
        numbering: Mapping[str, Any] | None = None,
        parent: Any | None = None,
    ) -> Any:
        nonlocal paragraph_counter, pending_page_break
        style = list_style or _style_for_block(block, styles, presentation_outline.get(node_id))
        if parent is not None and parent._tc in semantic_table_cells:
            style = styles.get("table_body", "表正文")
        if parent is None:
            try:
                if style:
                    doc.styles[style]
                paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            except KeyError:
                paragraph = doc.add_paragraph()
        else:
            parent_key = parent._tc
            if parent_key not in initial_cell_paragraphs and parent.paragraphs:
                paragraph = parent.paragraphs[0]
                paragraph.clear()
                try:
                    if style:
                        paragraph.style = style
                except KeyError:
                    pass
                initial_cell_paragraphs.add(parent_key)
            else:
                try:
                    paragraph = parent.add_paragraph(style=style) if style else parent.add_paragraph()
                except KeyError:
                    paragraph = parent.add_paragraph()
        caption_text: str | None = None
        if block.get("role") == "caption":
            caption_text = _write_caption(paragraph, block, resources, caption_sequences)
        else:
            _write_inline(paragraph, block.get("inline_content", []), media, resources)
        if parent is not None and parent._tc in layout_table_cells:
            _apply_layout_paragraph_format(
                paragraph,
                block.get("source_paragraph_format"),
            )
        if parent is None or parent._tc not in semantic_table_cells:
            # A semantic table cell is owned by the table format contract, which
            # sets the cell alignment itself, so the paragraph presentation must
            # not override it.
            paragraph_presentation = presentation_paragraphs.get(node_id)
            _apply_paragraph_alignment(paragraph, paragraph_presentation)
            _apply_paragraph_outline_level(paragraph, paragraph_presentation)
        if numbering and numbering.get("num_id") is not None:
            _write_direct_numbering(
                paragraph,
                int(numbering["num_id"]),
                int(numbering.get("num_ilvl") or 0),
            )
        if pending_page_break:
            paragraph.paragraph_format.page_break_before = True
            pending_page_break = False
        actual_style = paragraph.style.name if paragraph.style is not None else ""
        paragraph_counter += 1
        render_map[node_id] = {
            "kind": "paragraph",
            "paragraph": paragraph_counter,
            # The saved output audit compares this text against the paragraph a
            # reader sees.  A retained field or opaque object is copied back as an
            # element whose cached text ``paragraph.text`` cannot see, so the
            # module's own inline projection owns the text this render map
            # reports.
            "text": caption_text if caption_text is not None else _text(block.get("inline_content", []), resources),
            "inline_content": block.get("inline_content", []),
            "role": block.get("role"),
            "style": actual_style,
            "numbered": actual_style in {"List Number", "List Bullet"} or "列项" in actual_style,
        }
        image_resource_ids = _image_resource_ids(block.get("inline_content", []))
        if image_resource_ids:
            render_map[node_id]["image_resources"] = image_resource_ids
        if numbering:
            render_map[node_id]["numbering"] = dict(numbering)
        return paragraph

    def add_table(node_id: str, block_id: str, *, parent: Any | None = None) -> None:
        nonlocal table_counter
        table_counter += 1
        table_index = table_counter
        block = document["blocks"][block_id]
        row_ids = document["children"].get(block_id, [])
        row_count = len(row_ids)
        column_count = max(
            (
                int(document["containers"].get(cell_id, {}).get("col") or 0)
                + max(1, int(document["containers"].get(cell_id, {}).get("col_span") or 1))
                for row_id in row_ids
                for cell_id in document["children"].get(row_id, [])
            ),
            default=1,
        )
        table_type = str(block.get("table_type") or "data")
        is_layout = table_type == "layout"
        try:
            if parent is None:
                table = doc.add_table(
                    rows=max(1, row_count),
                    cols=max(1, column_count),
                    style=None if is_layout else "Table Grid",
                )
            else:
                table = parent.add_table(rows=max(1, row_count), cols=max(1, column_count))
        except (KeyError, ValueError):
            table = parent.add_table(rows=max(1, row_count), cols=max(1, column_count)) if parent is not None else doc.add_table(rows=max(1, row_count), cols=max(1, column_count))
        if is_layout:
            _apply_layout_table_contract(table, block.get("layout"))
        elif parent is None:
            try:
                table.style = "Table Grid"
            except (KeyError, ValueError):
                pass
        if parent is not None:
            initial_cell_paragraphs.add(parent._tc)
        table_body_style = styles.get("table_body")
        occupied: set[tuple[int, int]] = set()
        for row_index, row_id in enumerate(row_ids):
            column_cursor = 0
            for cell_id in document["children"].get(row_id, []):
                source_cell = document["containers"].get(cell_id, {})
                column_cursor = max(column_cursor, int(source_cell.get("col") or 0))
                while (row_index, column_cursor) in occupied:
                    column_cursor += 1
                col_span = max(1, int(source_cell.get("col_span") or 1))
                row_span = max(1, int(source_cell.get("row_span") or 1))
                cell = table.cell(row_index, column_cursor)
                if row_span > 1 or col_span > 1:
                    end_row = min(row_count - 1, row_index + row_span - 1)
                    end_col = min(column_count - 1, column_cursor + col_span - 1)
                    cell = cell.merge(table.cell(end_row, end_col))
                if is_layout:
                    layout_table_cells.add(cell._tc)
                else:
                    semantic_table_cells.add(cell._tc)
                for occupied_row in range(row_index + 1, min(row_count, row_index + row_span)):
                    for occupied_col in range(column_cursor, column_cursor + col_span):
                        occupied.add((occupied_row, occupied_col))
                render_children(cell_id, parent=cell)
                column_cursor += col_span
        _apply_source_table_fills(table, table_type, block.get("layout"))
        if table_body_style and table_body_style in doc.styles:
            # The contract runs once the cells hold their content.  The paragraph
            # half of it (style and alignment) has to reach every rendered line,
            # and a cell only had one placeholder paragraph when the table was
            # created: running it there left every later line to inherit the
            # table body style's own alignment.  That is invisible for a data
            # table, whose policy alignment is the style's, and it centered a
            # code sample whose policy is left.  The renderer owns the content it
            # has just written, so the contract does not strip direct formatting
            # here: that would drop a list item's numbering and a source header's
            # marks.
            normalize_table(
                table,
                {"resolved_styles": dict(styles)},
                table_row_height_cm,
                table_row_height_rule,
                role=table_type,
                path=str(len(doc.tables)),
                strip_direct_format=False,
            )
        render_map[node_id] = {"kind": "table", "table": table_index, "rows": row_count, "cols": column_count}

    def render_block(block_id: str, *, parent: Any | None = None) -> None:
        nonlocal paragraph_counter
        block = document["blocks"].get(block_id)
        if block is None:
            return
        if block.get("block_type") == "paragraph":
            add_paragraph(block_id, block, parent=parent)
        elif block.get("block_type") == "table":
            add_table(block_id, block_id, parent=parent)
        elif block.get("block_type") == "image":
            if (
                block.get("generated_by") == "source_ast.inline_image"
                and block.get("materialization_mode") != "standalone"
            ):
                # The image node materializes an inline token whose pixels are
                # already rendered by the paragraph that carries the token.
                return
            resource_id = block.get("resource_id")
            resource = resources.get(resource_id)
            if resource is None:
                raise RenderError(f"resource {resource_id!r} is unavailable for rendering")
            if parent is None:
                paragraph = doc.add_paragraph()
            else:
                parent_key = parent._tc
                if parent_key not in initial_cell_paragraphs and parent.paragraphs:
                    paragraph = parent.paragraphs[0]
                    paragraph.clear()
                    initial_cell_paragraphs.add(parent_key)
                else:
                    paragraph = parent.add_paragraph()
            media.embed(paragraph.add_run(), block)
            if parent is None or parent._tc not in semantic_table_cells:
                _apply_paragraph_alignment(paragraph, presentation_paragraphs.get(block_id))
            paragraph_counter += 1
            render_map[block_id] = {
                "kind": "image",
                "paragraph": paragraph_counter,
                "text": paragraph.text,
                "image_resources": [str(resource_id)],
            }
        elif block.get("block_type") == "opaque":
            raw_text = str(block.get("raw_text") or "").rstrip("\r\n")
            if raw_text:
                add_paragraph(
                    block_id,
                    {
                        "role": "body",
                        "inline_content": [{"kind": "text", "text": raw_text}],
                    },
                    parent=parent,
                )
                render_map[block_id]["opaque_source"] = True
            else:
                diagnostics.append({"code": "opaque_node_skipped", "node_id": block_id})

    def render_list_item(child_id: str, *, parent: Any | None = None) -> None:
        presentation = normalized.get("presentation", {}).get("nodes", {}).get(child_id, {})
        children = document["children"].get(child_id, [])
        anchor_id = presentation.get("marker_anchor")
        anchor_block = document["blocks"].get(anchor_id)
        if anchor_block is None:
            generated = next((item for item in normalized.get("generated_nodes", []) if item.get("node_id") == anchor_id), None)
            anchor_block = generated or {"block_type": "paragraph", "role": "body", "inline_content": []}
        marker_kind = presentation.get("marker_kind")
        try:
            target_level = max(0, int(presentation.get("target_level", 0)))
        except (TypeError, ValueError):
            target_level = 0
        try:
            semantic_level = max(0, int(presentation.get("semantic_level", target_level)))
        except (TypeError, ValueError):
            semantic_level = target_level
        # The ListPolicy already resolved the WX style role for this item's render
        # level, so the renderer consumes it instead of re-deriving it from the
        # level.  The fallback keeps a hand-built normalized view (a caller that
        # supplies its own presentation nodes) rendering as before.
        style_role = presentation.get("style_role")
        numbering_only = bool(presentation.get("numbering_only"))
        if numbering_only:
            # A table's own numbering column keeps the cell style: the template's
            # plain decimal instance numbers it and no WX list style applies.
            list_style = None
        elif not isinstance(style_role, str) or not style_role:
            if marker_kind == "ordered":
                style_role = "list_letter" if target_level == 0 else "list_decimal"
            else:
                style_role = "list_dash" if target_level == 0 else "list_bullet"
        if not numbering_only:
            list_style = styles.get(style_role, "List Number" if marker_kind == "ordered" else "List Bullet")
        # A per-chapter ordered item carries the concrete numbering instance the
        # normalizer assigned (the template instance for the first chapter, a
        # synthesized restart instance for a later one); every other item falls
        # back to the style role's template instance.
        raw_num_id = presentation.get("numbering_instance")
        if not isinstance(raw_num_id, int) or isinstance(raw_num_id, bool):
            raw_num_id = numbering_ids.get(style_role)
        num_id: int | None = None
        if raw_num_id is not None:
            try:
                num_id = int(raw_num_id)
            except (TypeError, ValueError):
                num_id = None
        # The bound instance may define fewer levels than the render level asks
        # for.  The template's second-level ordered / unordered styles sit on
        # their own single-level instances, so a second-level item emits
        # ``w:ilvl 0`` and the two levels are told apart by the style and the
        # instance (and the visible marker), not by the level attribute.  That is
        # the designed two-level model under AGENTS.md decision 1, so it is not a
        # diagnostic.
        emitted_level = target_level
        if num_id is not None and supported_numbering_levels is not None:
            levels = supported_numbering_levels.get(num_id)
            if levels is not None and target_level not in levels:
                emitted_level = 0
        # A semantic level deeper than the policy cap was pulled back to the
        # second level.  Report it so the author can express the deeper structure
        # with heading levels instead of another list level.
        if presentation.get("render_level_clamped"):
            diagnostics.append({
                "code": "list_render_level_clamped",
                "severity": "warning",
                "node_id": child_id,
                "message": "the source list nests deeper than the two-level WX model; the item renders at the second level. Express deeper structure with heading levels instead of more list levels.",
                "semantic_level": semantic_level,
                "render_level": target_level,
                "max_level": target_level,
                "emitted_level": emitted_level,
                "num_id": num_id,
            })
        add_paragraph(
            child_id,
            anchor_block,
            list_style=list_style,
            numbering={
                "marker_kind": presentation.get("marker_kind"),
                "target_level": presentation.get("target_level", 0),
                "semantic_level": semantic_level,
                "style_role": style_role,
                "num_id": num_id,
                "num_ilvl": emitted_level,
            },
            parent=parent,
        )
        for nested_child in children:
            if nested_child == anchor_id:
                continue
            render_node(nested_child, parent=parent)

    def render_children(node_id: str, *, parent: Any | None = None) -> None:
        for child_id in document["children"].get(node_id, []):
            render_node(child_id, parent=parent)

    def render_node(node_id: str, *, parent: Any | None = None) -> None:
        nonlocal pending_page_break
        if node_id in document["blocks"]:
            render_block(node_id, parent=parent)
            return
        container = document["containers"].get(node_id)
        if container is None:
            return
        if container.get("kind") == "list_item":
            render_list_item(node_id, parent=parent)
            return
        if container.get("kind") == "appendix" and container.get("page_break_before") is True:
            pending_page_break = True
        render_children(node_id, parent=parent)

    render_children(document["root_id"])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    _attach_image_artifact_metadata(output, render_map)
    return {"output_path": str(output), "render_map": render_map, "render_plan": plan, "diagnostics": diagnostics}
