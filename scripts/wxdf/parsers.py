"""SourceFacts adapters for Markdown and DOCX inputs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from docx.oxml.ns import qn

from list_style_mapping import normalize_wx_list_type, source_list_type
from note_semantics import source_note_role
from text_utils import looks_like_list_item
from unordered_lists import paragraph_layout_evidence

from .contracts import SOURCE_FACTS_SCHEMA_VERSION
from .source_store import SourceHandle, SourceStore
from .validation import validate_source_facts


MARKDOWN_PARSER_VERSION = "1.1.0"
_NUMBERING_LIST_FORMATS = frozenset({
    "decimal", "decimalZero", "lowerLetter", "upperLetter",
    "lowerRoman", "upperRoman", "bullet",
})
_NUMBERING_PROTECTED_STYLES = frozenset({"caption", "题注", "文档标题", "title"})
_EMU_PER_POINT = 12700
_EMU_PER_INCH = 914400
_VML_NS = "{urn:schemas-microsoft-com:vml}"
_WP_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_DOCX_VISIBLE_LIST_MARKER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>(?:\d+(?:[.．-]\d+)+(?:[.)])?|\d+[.)]|[A-Za-z][)]|[-+*•▪◦]))(?=[ \t]+)"
)


def _docx_visible_list_marker(text: str) -> dict[str, Any] | None:
    """Extract a manually typed DOCX list marker without changing source text."""

    match = _DOCX_VISIBLE_LIST_MARKER_RE.match(text)
    if match is None:
        return None
    marker = match.group("marker")
    if marker[0].isdigit():
        source_list_type = "decimal_chain" if re.search(r"\d[.．-]\d", marker) else "decimal_paren"
        ordinal = int(re.match(r"\d+", marker).group(0))
        marker_kind = "ordered"
    elif marker[0].isalpha():
        source_list_type = "lower_letter_paren"
        ordinal = ord(marker[0].lower()) - ord("a") + 1
        marker_kind = "ordered"
    else:
        source_list_type = "dash" if marker in {"-", "+", "*"} else "bullet_dot"
        ordinal = None
        marker_kind = "unordered"
    return {
        "marker_kind": marker_kind,
        "marker_text": marker,
        "source_list_type": source_list_type,
        "ordinal": ordinal,
        "indent_value": len(match.group("indent")),
        "indent_unit": "unicode_code_point",
        "marker_span": {"start": len(match.group("indent")), "end": match.end("marker")},
        "numbering_evidence": ["visible_list_marker"],
    }


def _docx_leading_whitespace(text: str) -> dict[str, Any] | None:
    """Record paragraph-leading tabs and spaces as presentation evidence."""

    match = re.match(r"^(?P<whitespace>[ \t]+)", text)
    if match is None:
        return None
    whitespace = match.group("whitespace")
    return {
        "text": whitespace,
        "tab_count": whitespace.count("\t"),
        "space_count": whitespace.count(" "),
        "length": len(whitespace),
    }


def _digest(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _empty_facts(handle: SourceHandle, parser_id: str, parser_config: str, *, parser_version: str = "1.0.0") -> dict[str, Any]:
    return {
        "facts_schema_version": SOURCE_FACTS_SCHEMA_VERSION,
        "source": {
            "source_id": handle.source_id,
            "source_type": handle.source_type,
            "content_sha256": handle.content_sha256,
            "encoding": handle.encoding,
        },
        "parser": {
            "id": parser_id,
            "version": parser_version,
            "dialect": parser_config,
            "config_sha256": _digest(parser_config),
        },
        "units": {},
        "containers": {"root": {"kind": "document"}},
        "children": {"root": []},
        "evidence": {},
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def _source_ref(handle: SourceHandle, part: str, *, start: int | None = None, end: int | None = None, node_path: list[str | int] | None = None, content: bytes | str | None = None, parser_version: str | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "source_id": handle.source_id,
        "part": part,
        "content_sha256": _digest(content if content is not None else handle.content_sha256),
    }
    if start is not None and end is not None:
        ref["span"] = {"start": start, "end": end, "unit": "unicode_code_point"}
    elif node_path is not None:
        ref["node_path"] = node_path
    else:
        ref["span"] = {"start": 0, "end": 0, "unit": "unicode_code_point"}
    if parser_version is not None:
        ref["parser_version"] = parser_version
    return ref


def _css_length_to_emu(value: str) -> int | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(pt|in)\s*", value, re.IGNORECASE)
    if match is None:
        return None
    amount = float(match.group(1))
    emu = round(amount * (_EMU_PER_INCH if match.group(2).lower() == "in" else _EMU_PER_POINT))
    return emu if emu > 0 else None


def _vml_display_size(imagedata: Any) -> dict[str, int] | None:
    shape = next((ancestor for ancestor in imagedata.iterancestors() if ancestor.tag == f"{_VML_NS}shape"), None)
    if shape is None:
        return None
    declarations = {
        key.strip().lower(): value.strip()
        for key, value in re.findall(r"([^:;]+):([^;]+)", str(shape.get("style") or ""))
    }
    width = _css_length_to_emu(declarations.get("width", ""))
    height = _css_length_to_emu(declarations.get("height", ""))
    if width is None or height is None:
        return None
    return {"width_emu": width, "height_emu": height}


def _drawingml_display_size(blip: Any) -> dict[str, int] | None:
    container = next(
        (ancestor for ancestor in blip.iterancestors() if ancestor.tag in {f"{_WP_NS}inline", f"{_WP_NS}anchor"}),
        None,
    )
    extent = container.find(f"{_WP_NS}extent") if container is not None else None
    if extent is None:
        return None
    try:
        width = int(extent.get("cx"))
        height = int(extent.get("cy"))
    except (TypeError, ValueError):
        return None
    if width < 1 or height < 1:
        return None
    return {"width_emu": width, "height_emu": height}


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _inline_nodes(tokens: Iterable[Any], handle: SourceHandle, part: str, resources: dict[str, Any], diagnostics: list[dict[str, Any]], source_ref: dict[str, Any], parser_version: str | None = None) -> list[dict[str, Any]]:
    tokens = list(tokens)

    def parse_range(start: int, stop_kind: str | None = None, marks: list[str] | None = None) -> tuple[list[dict[str, Any]], int]:
        result: list[dict[str, Any]] = []
        marks = list(marks or [])
        index = start
        while index < len(tokens):
            token = tokens[index]
            kind = getattr(token, "type", "")
            if stop_kind and kind == stop_kind:
                return result, index
            if kind == "text":
                result.append({"kind": "text", "text": token.content, "marks": list(marks)})
            elif kind == "code_inline":
                result.append({"kind": "text", "text": token.content, "marks": [*marks, "code"]})
            elif kind == "softbreak":
                result.append({"kind": "break"})
            elif kind == "hardbreak":
                result.append({"kind": "break"})
            elif kind in {"strong_open", "em_open"}:
                mark = "strong" if kind == "strong_open" else "emphasis"
                nested, index = parse_range(index + 1, "strong_close" if kind == "strong_open" else "em_close", [*marks, mark])
                result.extend(nested)
            elif kind in {"strong_close", "em_close"}:
                if stop_kind is None:
                    diagnostics.append({"code": "unbalanced_inline_mark", "severity": "warning", "message": kind})
                    index += 1
                    continue
                return result, index
            elif kind == "link_open":
                target = str(token.attrGet("href") or "")
                nested, index = parse_range(index + 1, "link_close", marks)
                result.append({"kind": "link", "target": target, "children": nested})
            elif kind == "image":
                target = str(token.attrGet("src") or "")
                alt_text = str(token.content or "")
                resource_id = _stable_id("res", handle.source_id, target)
                if resource_id not in resources:
                    resource_bytes: bytes | None = None
                    target_path = Path(target)
                    if not target_path.is_absolute():
                        target_path = handle.path.parent / target_path
                    try:
                        resource_bytes = target_path.read_bytes()
                    except OSError:
                        diagnostics.append({"code": "external_resource_unresolved", "severity": "warning", "message": target})
                    resource_content = resource_bytes if resource_bytes is not None else target
                    resources[resource_id] = {
                        "kind": "image",
                        "source_ref": _source_ref(handle, target, node_path=["inline", target], parser_version=parser_version),
                        "content_sha256": _digest(resource_content),
                        "dependencies": [],
                    }
                result.append({"kind": "image", "resource_id": resource_id, "alt_text": alt_text})
            elif kind in {"html_inline", "entity"}:
                result.append({"kind": "text", "text": token.content, "marks": list(marks)})
            else:
                diagnostics.append({"code": "unknown_inline_token", "severity": "warning", "message": kind})
                result.append({"kind": "opaque_inline", "source_ref": source_ref, "diagnostic": f"unsupported inline token: {kind}"})
            index += 1
        if stop_kind:
            diagnostics.append({"code": "unclosed_inline_container", "severity": "warning", "message": stop_kind})
        return result, index

    return parse_range(0)[0]


def _marker(line: str) -> tuple[str | None, int | None, int]:
    match = re.match(r"^(?P<indent>[ \t]*)(?P<marker>(?:\d+[.)]|[-+*]))[ \t]+", line)
    if not match:
        return None, None, 0
    marker = match.group("marker")
    if marker[0].isdigit():
        return "ordered", int(re.match(r"\d+", marker).group(0)), len(match.group("indent"))
    return "unordered", None, len(match.group("indent"))


@dataclass
class _MarkdownFactsBuilder:
    source_text: str
    handle: SourceHandle
    part: str

    def __post_init__(self) -> None:
        self.facts = _empty_facts(self.handle, "markdown-source-facts", "commonmark+table")
        self.lines = self.source_text.splitlines()
        self.offsets = _line_offsets(self.source_text)
        self.stack = ["root"]
        self.pending: tuple[str, Any] | None = None
        self.token_index = 0

    def _span(self, token: Any) -> tuple[int, int]:
        token_map = getattr(token, "map", None)
        if token_map is None:
            return 0, len(self.source_text)
        start_line, end_line = token_map
        start = self.offsets[min(start_line, len(self.offsets) - 1)]
        end = self.offsets[min(end_line, len(self.offsets) - 1)]
        if end_line >= len(self.offsets):
            end = len(self.source_text)
        return start, max(start, end)

    def _ref(self, token: Any) -> dict[str, Any]:
        start, end = self._span(token)
        return _source_ref(
            self.handle,
            self.part,
            start=start,
            end=end,
            content=self.source_text[start:end],
            parser_version=self.facts["parser"]["version"],
        )

    def _append(self, node_id: str) -> None:
        self.facts["children"].setdefault(self.stack[-1], []).append(node_id)

    def _new_container(self, kind: str, token: Any, **fields: Any) -> str:
        start, end = self._span(token)
        node_id = _stable_id("c", self.handle.source_id, kind, start, end, self.token_index)
        while node_id in self.facts["containers"]:
            self.token_index += 1
            node_id = _stable_id("c", self.handle.source_id, kind, start, end, self.token_index)
        self.facts["containers"][node_id] = {"kind": kind, **fields, "source_ref": self._ref(token)}
        self.facts["children"][node_id] = []
        self._append(node_id)
        return node_id

    def _new_unit(self, kind: str, token: Any, **fields: Any) -> str:
        start, end = self._span(token)
        node_id = _stable_id("u", self.handle.source_id, kind, start, end, self.token_index)
        while node_id in self.facts["units"]:
            self.token_index += 1
            node_id = _stable_id("u", self.handle.source_id, kind, start, end, self.token_index)
        self.facts["units"][node_id] = {"kind": kind, "source_ref": self._ref(token), **fields}
        self._append(node_id)
        return node_id

    def _record_list_evidence(self, node_id: str, token: Any) -> None:
        start_line = (getattr(token, "map", None) or [0])[0]
        line = self.lines[start_line] if start_line < len(self.lines) else ""
        marker_kind, ordinal, indent = _marker(line)
        if marker_kind is None:
            return
        self.facts["evidence"][node_id] = {
            "marker_kind": marker_kind,
            "marker_text": line[indent:].split(None, 1)[0] if line[indent:].split(None, 1) else None,
            "ordinal": ordinal,
            "explicit_depth": indent,
            "indent_value": indent,
            "indent_unit": "unicode_code_point",
            "numbering_definition_ref": None,
            "restart_hint": ordinal == 1 if ordinal is not None else None,
            "blank_before": start_line == 0 or not self.lines[start_line - 1].strip(),
            "continuation_hint": False,
        }

    def consume(self, tokens: Iterable[Any]) -> dict[str, Any]:
        from markdown_it import MarkdownIt

        for token in tokens:
            self.token_index += 1
            token_type = token.type
            if token_type in {"ordered_list_open", "bullet_list_open"}:
                start = 1
                if token_type == "ordered_list_open":
                    attrs = dict(token.attrs or [])
                    try:
                        start = int(attrs.get("start", 1))
                    except (TypeError, ValueError):
                        start = 1
                container_id = self._new_container(
                    "list",
                    token,
                    list_kind="ordered" if token_type == "ordered_list_open" else "unordered",
                    source_start=start,
                    source_sequence_id=_stable_id("seq", self.handle.source_id, self.token_index),
                )
                self.facts["evidence"][container_id] = {"marker_kind": "ordered" if token_type == "ordered_list_open" else "unordered", "source_token": token_type}
                self.stack.append(container_id)
            elif token_type == "list_item_open":
                item_id = self._new_container(
                    "list_item",
                    token,
                    source_marker="item",
                    source_ordinal=1,
                    source_sequence_id=_stable_id("seq", self.handle.source_id, self.stack[-1]),
                )
                self._record_list_evidence(item_id, token)
                evidence = self.facts["evidence"].get(item_id, {})
                self.facts["containers"][item_id]["source_marker"] = evidence.get("marker_text") or "item"
                self.facts["containers"][item_id]["source_ordinal"] = evidence.get("ordinal") or 1
                self.stack.append(item_id)
            elif token_type == "blockquote_open":
                self.stack.append(self._new_container("quote", token))
            elif token_type == "table_open":
                table_id = self._new_container("table", token)
                self.facts["evidence"][table_id] = {
                    "line_start": (getattr(token, "map", None) or [0])[0] + 1,
                    "source_token": token_type,
                }
                self.stack.append(table_id)
            elif token_type == "tr_open":
                table_id = self.stack[-1]
                row_index = len(self.facts["children"].get(table_id, []))
                self.stack.append(self._new_container("table_row", token, row=row_index))
            elif token_type in {"th_open", "td_open"}:
                row_id = self.stack[-1]
                row = self.facts["containers"][row_id]
                row_index = int(row["row"])
                col_index = len(self.facts["children"].get(row_id, []))
                self.stack.append(
                    self._new_container(
                        "table_cell",
                        token,
                        row=row_index,
                        col=col_index,
                        row_span=1,
                        col_span=1,
                        cell_role="header" if token_type == "th_open" else "body",
                    )
                )
                self.pending = ("body", None)
            elif token_type in {"ordered_list_close", "bullet_list_close", "list_item_close", "blockquote_close", "table_close", "tr_close", "th_close", "td_close"}:
                if len(self.stack) > 1:
                    self.stack.pop()
            elif token_type in {"heading_open", "paragraph_open"}:
                role = "heading" if token_type == "heading_open" else "body"
                level = int(token.tag[1:]) if role == "heading" and token.tag[1:].isdigit() else None
                self.pending = (role, level)
            elif token_type == "inline" and self.pending is not None:
                role, level = self.pending
                ref = self._ref(token)
                inline_content = _inline_nodes(
                    token.children or [],
                    self.handle,
                    self.part,
                    self.facts["resources"],
                    self.facts["diagnostics"],
                    ref,
                    self.facts["parser"]["version"],
                )
                fields: dict[str, Any] = {"role": role, "inline_content": inline_content}
                if level is not None:
                    fields["level"] = level
                unit_id = self._new_unit("paragraph", token, **fields)
                self.facts["evidence"][unit_id] = {"line_start": (getattr(token, "map", None) or [0])[0] + 1, "source_token": "inline", "blank_before": False}
                self.pending = None
            elif token_type in {"fence", "code_block"}:
                unit_id = self._new_unit("code_block", token, raw_text=token.content, language=(token.info or "").strip() or None)
                self.facts["evidence"][unit_id] = {"source_token": token_type}
            elif token_type == "hr":
                self._new_unit("separator", token, raw_text="")
            elif token_type == "html_block":
                unit_id = self._new_unit("opaque", token, raw_text=token.content, diagnostic_ids=["unsupported_html_block"])
                self.facts["evidence"][unit_id] = {"source_token": token_type}
                self.facts["diagnostics"].append({"code": "unsupported_html_block", "severity": "warning", "message": "HTML block is retained as opaque source content"})
            elif token.map is not None and token_type not in {"thead_open", "thead_close", "tbody_open", "tbody_close", "paragraph_close", "heading_close", "table_close"}:
                unit_id = self._new_unit("opaque", token, raw_text=token.content or "", diagnostic_ids=[f"unsupported_{token_type}"])
                self.facts["diagnostics"].append({"code": f"unsupported_{token_type}", "severity": "warning", "message": f"token {token_type} retained as opaque source content"})
        return self.facts


def _parse_markdown(source: Path | str, *, store: SourceStore, text: str | None = None) -> dict[str, Any]:
    from markdown_it import MarkdownIt

    handle = store.open_source(source, text=text)
    source_text = text if text is not None else store.resolve({"source_id": handle.source_id, "part": handle.path.name})
    if isinstance(source_text, bytes):
        source_text = source_text.decode(handle.encoding or "utf-8")
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    builder = _MarkdownFactsBuilder(str(source_text), handle, handle.path.name)
    builder.facts["parser"]["version"] = MARKDOWN_PARSER_VERSION
    facts = builder.consume(parser.parse(str(source_text)))
    validate_source_facts(facts)
    return facts


def _docx_inline_content(paragraph: Any, handle: SourceHandle, node_path: list[str | int], resources: dict[str, Any], parser_version: str) -> list[dict[str, Any]]:
    from docx.oxml.ns import qn

    content: list[dict[str, Any]] = []
    image_tags = {qn("a:blip"), "{urn:schemas-microsoft-com:vml}imagedata"}

    def run_marks(run: Any) -> list[str]:
        marks: list[str] = []
        properties = run.find(qn("w:rPr"))
        if properties is None:
            return marks
        if properties.find(qn("w:b")) is not None:
            marks.append("strong")
        if properties.find(qn("w:i")) is not None:
            marks.append("emphasis")
        return marks

    def append_image(blip: Any, path: list[str | int], target: list[dict[str, Any]]) -> None:
        relationship_id = blip.get(qn("r:embed")) or blip.get(qn("r:id"))
        if not relationship_id:
            return
        resource_id = _stable_id("res", handle.source_id, relationship_id)
        source_embedding = "vml" if blip.tag == "{urn:schemas-microsoft-com:vml}imagedata" else "drawingml"
        display_size = _vml_display_size(blip) if source_embedding == "vml" else _drawingml_display_size(blip)
        if resource_id not in resources:
            part = paragraph.part.related_parts.get(relationship_id)
            blob = getattr(part, "blob", b"")
            media_part = str(getattr(part, "partname", "word/media/unknown")).lstrip("/")
            resource = {
                "kind": "image",
                "source_ref": _source_ref(handle, media_part, node_path=[*path, relationship_id], parser_version=parser_version),
                "content_sha256": _digest(blob),
                "dependencies": [],
            }
            media_type = getattr(part, "content_type", None)
            if media_type:
                resource["media_type"] = str(media_type)
            resources[resource_id] = resource
        token = {"kind": "image", "resource_id": resource_id, "alt_text": "", "source_embedding": source_embedding}
        if display_size is not None:
            token["display_size"] = display_size
        target.append(token)

    def parse_children(parent: Any, target: list[dict[str, Any]], path: list[str | int]) -> None:
        for index, child in enumerate(list(parent)):
            tag = child.tag
            child_path = [*path, index]
            if tag == qn("w:r"):
                marks = run_marks(child)
                for run_child in list(child):
                    run_tag = run_child.tag
                    if run_tag == qn("w:rPr"):
                        continue
                    if run_tag == qn("w:t"):
                        target.append({"kind": "text", "text": run_child.text or "", "marks": list(marks)})
                    elif run_tag == qn("w:tab"):
                        target.append({"kind": "tab"})
                    elif run_tag in {qn("w:br"), qn("w:cr")}:
                        target.append({"kind": "break"})
                    elif run_tag in {qn("w:drawing"), qn("w:pict")} or any(
                        element.tag in image_tags for element in run_child.iter()
                    ):
                        for image_ref in run_child.iter():
                            if image_ref.tag in image_tags:
                                append_image(image_ref, child_path, target)
            elif tag == qn("w:hyperlink"):
                relationship_id = child.get(qn("r:id"))
                relationship = paragraph.part.rels.get(relationship_id) if relationship_id else None
                target_url = getattr(relationship, "target_ref", None) if relationship is not None else None
                if not target_url:
                    anchor = child.get(qn("w:anchor"))
                    target_url = f"#{anchor}" if anchor else None
                children: list[dict[str, Any]] = []
                parse_children(child, children, child_path)
                if target_url and children:
                    target.append({"kind": "link", "target": str(target_url), "children": children})
                else:
                    target.extend(children)
            elif tag != qn("w:pPr"):
                parse_children(child, target, child_path)

    parse_children(paragraph._p, content, node_path)
    return content


def _docx_source_paragraph_format(paragraph: Any) -> dict[str, Any]:
    """Capture the safe direct-format subset needed by layout table cells."""

    from docx.oxml.ns import qn

    properties = paragraph._p.pPr
    if properties is None:
        return {}
    result: dict[str, Any] = {}
    alignment = properties.find(qn("w:jc"))
    if alignment is not None and alignment.get(qn("w:val")) is not None:
        result["alignment"] = alignment.get(qn("w:val"))

    for contract_name, element_name, attributes in (
        (
            "spacing",
            "spacing",
            (
                "before", "beforeLines", "beforeAutospacing",
                "after", "afterLines", "afterAutospacing", "line", "lineRule",
            ),
        ),
        (
            "indentation",
            "ind",
            (
                "left", "leftChars", "right", "rightChars",
                "firstLine", "firstLineChars", "hanging", "hangingChars",
                "start", "startChars", "end", "endChars",
            ),
        ),
    ):
        element = properties.find(qn(f"w:{element_name}"))
        if element is None:
            continue
        captured = {
            attribute: element.get(qn(f"w:{attribute}"))
            for attribute in attributes
            if element.get(qn(f"w:{attribute}")) is not None
        }
        if captured:
            result[contract_name] = captured

    pagination = {}
    for element_name in ("keepNext", "keepLines", "pageBreakBefore", "widowControl"):
        element = properties.find(qn(f"w:{element_name}"))
        if element is not None:
            pagination[element_name] = element.get(qn("w:val"))
    if pagination:
        result["pagination"] = pagination
    return result


def _read_numbering_val(element: Any, attr: str) -> str | None:
    if element is None:
        return None
    return element.get(qn(attr))


def _read_numbering_int(element: Any, attr: str, default: int | None = None) -> int | None:
    raw = _read_numbering_val(element, attr)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _level_ind_values(level: Any) -> dict[str, int | None]:
    """Read the level indentation from ``w:lvl/w:pPr/w:ind``.

    OOXML nests numbering-level indentation inside the level's paragraph
    properties, not directly under ``w:lvl``.
    """
    p_pr = level.find(qn("w:pPr"))
    ind = p_pr.find(qn("w:ind")) if p_pr is not None else None
    if ind is None:
        return {}
    return {
        "left_twips": _read_numbering_int(ind, "w:left"),
        "left_chars": _read_numbering_int(ind, "w:leftChars"),
        "hanging_twips": _read_numbering_int(ind, "w:hanging"),
        "hanging_chars": _read_numbering_int(ind, "w:hangingChars"),
        "first_line_twips": _read_numbering_int(ind, "w:firstLine"),
        "first_line_chars": _read_numbering_int(ind, "w:firstLineChars"),
    }


def _level_child_int(level: Any, tag: str) -> int | None:
    """Read an integer ``w:val`` from a ``w:lvl`` child element."""

    return _read_numbering_int(level.find(qn(tag)), "w:val")


def _level_values(level: Any) -> dict[str, Any]:
    def value(tag: str) -> str | None:
        child = level.find(qn(tag))
        return child.get(qn("w:val")) if child is not None else None

    start = _read_numbering_int(level.find(qn("w:start")), "w:val", 1)
    ind_values = _level_ind_values(level)
    return {
        "num_fmt": value("w:numFmt"),
        "lvl_text": value("w:lvlText"),
        "p_style": value("w:pStyle"),
        "start": start if start is not None else 1,
        "ind": ind_values or None,
        "suff": value("w:suff"),
        "lvl_restart": _level_child_int(level, "w:lvlRestart"),
        "jc": value("w:lvlJc"),
    }


def build_numbering_catalog(document: Any) -> dict[str, Any]:
    """Build a native DOCX numbering catalog covering the full OOXML set.

    Returns per-abstractNum level maps, num-to-abstract links, level overrides
    (lvlOverride / startOverride), and a pStyle-to-ilvl lookup per abstractNum
    that implements the MS NumberingProperties rule: when a numPr is on the
    paragraph STYLE, its ilvl is IGNORED and the effective level comes from the
    w:lvl/w:pStyle association.
    """
    num_to_abstract: dict[int, int] = {}
    abstract_levels: dict[int, dict[int, dict[str, Any]]] = {}
    abstract_pstyle: dict[int, dict[str, int]] = {}
    num_level_overrides: dict[int, dict[int, dict[str, Any]]] = {}
    try:
        root = document.part.numbering_part.element
    except Exception:
        return {
            "num_to_abstract": num_to_abstract,
            "abstract_levels": abstract_levels,
            "abstract_pstyle": abstract_pstyle,
            "num_level_overrides": num_level_overrides,
        }

    for num in root.findall(qn("w:num")):
        try:
            num_id = int(num.get(qn("w:numId")))
        except (TypeError, ValueError):
            continue
        abstract = num.find(qn("w:abstractNumId"))
        if abstract is not None:
            try:
                num_to_abstract[num_id] = int(abstract.get(qn("w:val")))
            except (TypeError, ValueError):
                pass
        overrides: dict[int, dict[str, Any]] = {}
        for override in num.findall(qn("w:lvlOverride")):
            try:
                ilvl = int(override.get(qn("w:ilvl"), "0"))
            except ValueError:
                ilvl = 0
            values: dict[str, Any] = {}
            override_level = override.find(qn("w:lvl"))
            if override_level is not None:
                level_values = _level_values(override_level)
                if override_level.find(qn("w:start")) is None:
                    level_values.pop("start", None)
                values.update({key: value for key, value in level_values.items() if value is not None})
            start_override = override.find(qn("w:startOverride"))
            if start_override is not None:
                values["start"] = _read_numbering_int(start_override, "w:val", 1)
            if values:
                overrides[ilvl] = values
        if overrides:
            num_level_overrides[num_id] = overrides

    for abstract in root.findall(qn("w:abstractNum")):
        try:
            abstract_id = int(abstract.get(qn("w:abstractNumId")))
        except (TypeError, ValueError):
            continue
        levels: dict[int, dict[str, Any]] = {}
        pstyle_map: dict[str, int] = {}
        for level in abstract.findall(qn("w:lvl")):
            try:
                ilvl = int(level.get(qn("w:ilvl"), "0"))
            except ValueError:
                ilvl = 0
            levels[ilvl] = _level_values(level)
            p_style = levels[ilvl].get("p_style")
            if isinstance(p_style, str) and p_style:
                pstyle_map[p_style.casefold()] = ilvl
        abstract_levels[abstract_id] = levels
        abstract_pstyle[abstract_id] = pstyle_map

    return {
        "num_to_abstract": num_to_abstract,
        "abstract_levels": abstract_levels,
        "abstract_pstyle": abstract_pstyle,
        "num_level_overrides": num_level_overrides,
    }


def _paragraph_num_info(paragraph: Any) -> tuple[int | None, int | None, str]:
    """Return ``(ilvl, num_id, source)`` for a paragraph.

    ``source`` is ``"direct"`` when the numPr lives on the paragraph itself,
    ``"style"`` when it is inherited from the paragraph style, or ``"none"``
    when no numbering is applied.
    """
    try:
        p_pr = paragraph._p.pPr
        num_pr = p_pr.numPr if p_pr is not None else None
        if num_pr is not None and getattr(num_pr, "numId", None) is not None:
            ilvl = _read_numbering_int(num_pr.ilvl, "w:val") if getattr(num_pr, "ilvl", None) is not None else 0
            return int(ilvl) if ilvl is not None else 0, int(num_pr.numId.val), "direct"
    except (AttributeError, TypeError, ValueError):
        pass

    try:
        style_p_pr = paragraph.style.element.pPr
        num_pr = style_p_pr.numPr if style_p_pr is not None else None
        if num_pr is not None and getattr(num_pr, "numId", None) is not None:
            ilvl = _read_numbering_int(num_pr.ilvl, "w:val") if getattr(num_pr, "ilvl", None) is not None else 0
            return int(ilvl) if ilvl is not None else 0, int(num_pr.numId.val), "style"
    except (AttributeError, TypeError, ValueError):
        pass
    return None, None, "none"


def _resolve_effective_ilvl(
    paragraph: Any,
    num_id: int,
    source_ilvl: int | None,
    numbering_source: str,
    catalog: dict[str, Any],
) -> int:
    """Resolve the effective ilvl honoring the MS NumberingProperties rule.

    When the numPr is on the paragraph STYLE, its ilvl is IGNORED.  The effective
    level comes from the w:lvl/w:pStyle association inside the abstractNum.  The
    association is keyed by the style identifier, so both the style id and the
    display name are accepted.
    """
    if numbering_source != "style":
        return int(source_ilvl) if source_ilvl is not None else 0
    abstract_id = catalog["num_to_abstract"].get(num_id)
    if abstract_id is None:
        return int(source_ilvl) if source_ilvl is not None else 0
    pstyle_map = catalog["abstract_pstyle"].get(abstract_id, {})
    for candidate in (_style_id(paragraph), _style_name(paragraph)):
        if isinstance(candidate, str) and candidate:
            ilvl = pstyle_map.get(candidate.casefold())
            if ilvl is not None:
                return ilvl
    return int(source_ilvl) if source_ilvl is not None else 0


def _style_name(paragraph: Any) -> str:
    try:
        return paragraph.style.name or ""
    except Exception:
        return ""


def _style_id(paragraph: Any) -> str:
    try:
        return paragraph.style.style_id or ""
    except Exception:
        return ""


def _is_list_style(style_name: str) -> bool:
    """Return whether a DOCX paragraph style is a named list style."""

    normalized = style_name.casefold().replace(" ", "")
    return (
        normalized.startswith("listnumber")
        or normalized.startswith("listbullet")
        or normalized == "listparagraph"
        or "列项" in style_name
    )


def _is_protected_style(style_name: str) -> bool:
    """Return whether a DOCX paragraph style must never become a list item."""

    normalized = style_name.casefold().strip()
    return (
        normalized.startswith(("heading", "toc"))
        or normalized in _NUMBERING_PROTECTED_STYLES
        or source_note_role(style_name) is not None
        or ("附录" in style_name and "标题" in style_name)
        or any(token in normalized for token in ("目录", "题注", "注释", "公式", "附录标题"))
    )


def describe_docx_paragraph(
    document: Any,
    paragraph: Any,
    catalog: dict[str, Any] | None = None,
    *,
    ordinal: int | None = None,
    restart: bool = True,
) -> dict[str, Any] | None:
    """Return native numbering evidence for one paragraph outside the body.

    ``analyze_docx_lists`` keeps the historical top-level source position
    contract.  Nested table cells need the same numbering vocabulary without
    pretending that their paragraphs occupy top-level document positions.

    The catalog covers the full OOXML set: w:start, w:numFmt, w:lvlText,
    w:pStyle, w:ind, w:suff, w:lvlRestart, w:lvlOverride and w:startOverride.
    The MS NumberingProperties rule is applied: when the numPr lives on the
    paragraph STYLE its ilvl is IGNORED and the effective level comes from the
    w:lvl/w:pStyle association.
    """
    catalog = catalog or build_numbering_catalog(document)
    ilvl, num_id, numbering_source = _paragraph_num_info(paragraph)
    if num_id is None:
        return None
    abstract_id = catalog["num_to_abstract"].get(num_id)
    effective_ilvl = _resolve_effective_ilvl(paragraph, num_id, ilvl, numbering_source, catalog)
    # The applied level is the effective one.  A numPr on the paragraph style
    # carries an ilvl that must be ignored, so the format follows the effective
    # level rather than the declared one.
    level_index = effective_ilvl
    base_level: dict[str, Any] = dict(catalog["abstract_levels"].get(abstract_id, {}).get(level_index, {}))
    override = catalog["num_level_overrides"].get(num_id, {}).get(level_index, {})
    level = dict(base_level)
    level.update(override)
    style_name = _style_name(paragraph)
    num_fmt = level.get("num_fmt")
    valid = abstract_id is not None and isinstance(num_fmt, str) and num_fmt in _NUMBERING_LIST_FORMATS
    evidence: list[str] = []
    if valid:
        evidence.append("valid_numbering_definition")
    if _is_list_style(style_name):
        evidence.append("list_style")
    if looks_like_list_item(paragraph.text.strip()):
        evidence.append("visible_list_marker")
    has_drawing = paragraph._p.find(".//" + qn("w:drawing")) is not None
    if has_drawing:
        evidence.append("image_content")
    if numbering_source == "direct":
        evidence.append("direct_num_pr")
    protected = _is_protected_style(style_name)
    has_content = bool(paragraph.text.strip() or has_drawing)
    if not has_content and valid and ("list_style" in evidence or numbering_source == "direct"):
        evidence.append("empty_list_item")
    if protected or num_fmt == "none" or not valid or (not has_content and "empty_list_item" not in evidence):
        status = "ignored"
        confidence = 0.0
        if protected:
            evidence.append("protected_role")
        elif not valid:
            evidence.append("invalid_numbering_definition")
    else:
        status = "detected"
        confidence = 0.98 if "list_style" in evidence else 0.90
    marker_type = source_list_type(num_fmt, level.get("lvl_text"))
    return {
        "num_id": num_id,
        "ilvl": ilvl or 0,
        "abstract_num_id": abstract_id,
        "num_fmt": num_fmt,
        "lvl_text": level.get("lvl_text"),
        "p_style": level.get("p_style"),
        "start": ordinal if ordinal is not None else int(level.get("start", 1) or 1),
        "numbering_source": numbering_source,
        "layout": paragraph_layout_evidence(paragraph),
        "restart": restart,
        "status": status,
        "confidence": confidence,
        "evidence": evidence,
        "semantic_role": None,
        "source_list_type": marker_type,
        "list_type": normalize_wx_list_type(marker_type, int(ilvl or 0)),
        "source_ilvl": ilvl or 0,
        "effective_ilvl": effective_ilvl,
        "start_value": int(_numbering_start_value(level, override) or 1),
        "sequence_id": _numbering_sequence_id(num_id, abstract_id),
        "restart_scope": _numbering_restart_scope(num_id, abstract_id),
        "w_ind": (level.get("ind") or {}).get("left_twips"),
        "suffix": level.get("suff"),
        "p_style_ref": style_name or None,
        "lvl_restart": level.get("lvl_restart"),
        "start_override": override.get("start"),
    }


def _numbering_start_value(level: Mapping[str, Any], override: Mapping[str, Any]) -> int:
    """Return the effective start ordinal, preferring ``w:startOverride``."""

    for candidate in (override.get("start"), level.get("start")):
        if isinstance(candidate, int):
            return candidate
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return 1


def _numbering_sequence_id(num_id: int, abstract_id: int | None) -> str:
    """Return the stable sequence identity for one numbering instance.

    Keeps the ``docx-sequence-<num_id>-<abstract_num_id>`` spelling already used
    by ``wxdf.semantics.lists.json_sequence_id`` so the native catalogue and the
    list semantics agree on one identifier.
    """

    if abstract_id is None:
        return f"docx-sequence-{num_id}"
    return f"docx-sequence-{num_id}-{abstract_id}"


def _numbering_restart_scope(num_id: int, abstract_id: int | None) -> str:
    if abstract_id is not None:
        return f"abstract:{abstract_id}"
    return f"num:{num_id}"


def _numbering_catalogue_evidence(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Return the OOXML catalogue evidence fields for one numbered paragraph.

    The group decision (status / restart / group identity) belongs to
    ``analyze_docx_lists``; these fields come from the native numbering catalog
    and are additive to it.
    """

    return {
        "source_ilvl": descriptor.get("source_ilvl"),
        "effective_ilvl": descriptor.get("effective_ilvl"),
        "start_value": descriptor.get("start_value"),
        "sequence": descriptor.get("sequence_id"),
        "restart_scope": descriptor.get("restart_scope"),
        "num_id": descriptor.get("num_id"),
        "abstract_num_id": descriptor.get("abstract_num_id"),
        "w_ind": descriptor.get("w_ind"),
        "suffix": descriptor.get("suffix"),
        "p_style_ref": descriptor.get("p_style_ref"),
        "lvl_restart": descriptor.get("lvl_restart"),
        "start_override": descriptor.get("start_override"),
    }


def _parse_docx(source: Path | str, *, store: SourceStore) -> dict[str, Any]:
    from docx import Document
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    from list_detector import analyze_docx_lists
    from table_semantics import classify_docx_table
    from .semantics.tables import capture_docx_table_layout

    handle = store.open_source(source)
    document = Document(str(handle.path))
    facts = _empty_facts(handle, "docx-source-facts", "docx-ooxml", parser_version="1.1.0")
    parser_version = facts["parser"]["version"]
    report: dict[str, Any] = {}
    numbering_catalog = build_numbering_catalog(document)
    # One native catalog feeds the group analysis.  ``numbering_maps`` is the
    # legacy positional shape; the level maps now carry the full OOXML coverage
    # (w:ind, w:suff, w:lvlRestart, w:pStyle) built by ``build_numbering_catalog``.
    numbering = analyze_docx_lists(
        document,
        report,
        numbering_maps=(
            numbering_catalog["num_to_abstract"],
            numbering_catalog["abstract_levels"],
            numbering_catalog["num_level_overrides"],
        ),
    )
    body = document.element.body
    position = 0
    nested_list_ordinals: dict[tuple[str, int, int, str | None], int] = {}

    def add_unit(parent_id: str, paragraph: Any, node_path: list[str | int], source_position: int, descriptor: dict[str, Any] | None = None, catalogue: dict[str, Any] | None = None) -> None:
        unit_id = _stable_id("u", handle.source_id, "paragraph", *node_path)
        ref = _source_ref(handle, "word/document.xml", node_path=node_path, parser_version=parser_version)
        facts["units"][unit_id] = {
            "kind": "paragraph",
            "source_ref": ref,
            "inline_content": _docx_inline_content(paragraph, handle, node_path, facts["resources"], parser_version),
            "style": paragraph.style.name if paragraph.style is not None else None,
        }
        source_paragraph_format = _docx_source_paragraph_format(paragraph)
        if source_paragraph_format:
            facts["units"][unit_id]["source_paragraph_format"] = source_paragraph_format
        facts["children"].setdefault(parent_id, []).append(unit_id)
        evidence = {"source_position": source_position, "style": paragraph.style.name if paragraph.style is not None else None}
        if descriptor is not None:
            evidence.update({
                "numbering_status": descriptor.get("status"),
                "num_fmt": descriptor.get("num_fmt"),
                "lvl_text": descriptor.get("lvl_text"),
                "source_list_type": descriptor.get("source_list_type"),
                "numbering_evidence": list(descriptor.get("evidence") or []),
                "indent_value": descriptor.get("layout", {}).get("left_twips", 0),
                "indent_unit": "twips",
                "marker_text": descriptor.get("lvl_text"),
                "ordinal": descriptor.get("start"),
                "explicit_depth": descriptor.get("ilvl"),
                "restart_hint": descriptor.get("restart"),
                "numbering_definition_ref": {
                    "num_id": descriptor.get("num_id"),
                    "abstract_num_id": descriptor.get("abstract_num_id"),
                },
                "blank_before": False,
                "continuation_hint": False,
            })
            if descriptor.get("status") == "detected":
                evidence.update({
                    "marker_kind": "ordered" if descriptor.get("num_fmt") != "bullet" else "unordered",
                })
            if catalogue is not None:
                evidence.update(_numbering_catalogue_evidence(catalogue))
        elif isinstance(paragraph.text, str):
            visible_marker = _docx_visible_list_marker(paragraph.text)
            if visible_marker is not None:
                evidence.update({
                    "numbering_status": "detected",
                    "num_fmt": "bullet" if visible_marker["marker_kind"] == "unordered" else "decimal",
                    "lvl_text": visible_marker["marker_text"],
                    "source_list_type": visible_marker["source_list_type"],
                    "numbering_evidence": list(visible_marker["numbering_evidence"]),
                    "indent_value": visible_marker["indent_value"],
                    "indent_unit": visible_marker["indent_unit"],
                    "marker_text": visible_marker["marker_text"],
                    "ordinal": visible_marker["ordinal"],
                    "marker_kind": visible_marker["marker_kind"],
                    "source_ilvl": None,
                    "effective_ilvl": 0,
                    "num_id": None,
                    "abstract_num_id": None,
                    "p_style_ref": None,
                    "visible_marker": {
                        "marker_text": visible_marker["marker_text"],
                        "source_list_type": visible_marker["source_list_type"],
                        "ordinal": visible_marker["ordinal"],
                        "indent_value": visible_marker["indent_value"],
                        "indent_unit": visible_marker["indent_unit"],
                        "span": visible_marker["marker_span"],
                    },
                })
        leading_whitespace = _docx_leading_whitespace(paragraph.text)
        if leading_whitespace is not None:
            evidence["leading_whitespace"] = leading_whitespace
        facts["evidence"][unit_id] = evidence

    def _grid_span(cell: Any) -> int:
        tc_pr = cell._tc.tcPr
        if tc_pr is None:
            return 1
        grid_span = tc_pr.find(qn("w:gridSpan"))
        try:
            return max(1, int(grid_span.get(qn("w:val")))) if grid_span is not None else 1
        except (TypeError, ValueError):
            return 1

    def _vertical_merge(cell: Any) -> str | None:
        tc_pr = cell._tc.tcPr
        if tc_pr is None:
            return None
        merge = tc_pr.find(qn("w:vMerge"))
        if merge is None:
            return None
        return merge.get(qn("w:val")) or "continue"

    def add_table(parent_id: str, table: Any, node_path: list[str | int]) -> None:
        nonlocal position
        table_position = position
        table_id = _stable_id("c", handle.source_id, "table", *node_path)
        table_semantics = classify_docx_table(table)
        facts["containers"][table_id] = {
            "kind": "table",
            "source_ref": _source_ref(handle, "word/document.xml", node_path=node_path, parser_version=parser_version),
            "table_semantics": table_semantics.as_source_record(),
            "layout": capture_docx_table_layout(table),
        }
        facts["children"][table_id] = []
        facts["children"].setdefault(parent_id, []).append(table_id)
        active_vertical_cells: dict[int, tuple[str, int]] = {}
        for row_index, row in enumerate(table.rows):
            row_path = [*node_path, row_index]
            row_id = _stable_id("c", handle.source_id, "row", *row_path)
            facts["containers"][row_id] = {
                "kind": "table_row",
                "source_ref": _source_ref(handle, "word/document.xml", node_path=row_path, parser_version=parser_version),
            }
            facts["children"][row_id] = []
            facts["children"][table_id].append(row_id)
            merged_cells: list[dict[str, Any]] = []
            facts["containers"][row_id]["merged_cells"] = merged_cells
            column_cursor = 0
            raw_cells = [child for child in row._tr.iterchildren() if child.tag == qn("w:tc")]
            for raw_cell in raw_cells:
                cell = _Cell(raw_cell, table)
                col_index = column_cursor
                col_span = _grid_span(cell)
                merge_state = _vertical_merge(cell)
                if merge_state == "continue" and col_index in active_vertical_cells:
                    anchor_id, start_row = active_vertical_cells[col_index]
                    anchor = facts["containers"][anchor_id]
                    anchor["row_span"] = row_index - start_row + 1
                    merged_cells.append({"cell_id": anchor_id, "col": col_index, "col_span": col_span, "continuation": True})
                    column_cursor += col_span
                    continue
                cell_path = [*node_path, row_index, col_index]
                cell_id = _stable_id("c", handle.source_id, "cell", *cell_path)
                facts["containers"][cell_id] = {
                    "kind": "table_cell",
                    "row": row_index,
                    "col": col_index,
                    "row_span": 1,
                    "col_span": col_span,
                    "source_ref": _source_ref(handle, "word/document.xml", node_path=cell_path, parser_version=parser_version),
                }
                facts["children"][cell_id] = []
                facts["children"][row_id].append(cell_id)
                if merge_state == "restart":
                    for col in range(col_index, col_index + col_span):
                        active_vertical_cells[col] = (cell_id, row_index)
                else:
                    for col in range(col_index, col_index + col_span):
                        active_vertical_cells.pop(col, None)
                paragraph_index = 0
                nested_table_index = 0
                for child in cell._tc.iterchildren():
                    if child.tag == qn("w:p"):
                        paragraph = Paragraph(child, cell)
                        descriptor = describe_docx_paragraph(document, paragraph, numbering_catalog)
                        if descriptor is not None:
                            key = (cell_id, int(descriptor.get("num_id") or 0), int(descriptor.get("ilvl") or 0), descriptor.get("num_fmt"))
                            ordinal = nested_list_ordinals.get(key, 0) + 1
                            nested_list_ordinals[key] = ordinal
                            descriptor = dict(descriptor)
                            descriptor["start"] = ordinal
                            descriptor["restart"] = ordinal == 1
                        child_path = [*cell_path, "w:p", paragraph_index]
                        add_unit(cell_id, paragraph, child_path, position, descriptor, descriptor)
                        paragraph_index += 1
                    elif child.tag == qn("w:tbl"):
                        child_path = [*cell_path, "w:tbl", nested_table_index]
                        add_table(cell_id, Table(child, cell), child_path)
                        nested_table_index += 1
                column_cursor += col_span
        facts["evidence"][table_id] = {"source_position": table_position, "table_scope": "cell_local"}

    for child_index, child in enumerate(body.iterchildren()):
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            catalogue = describe_docx_paragraph(document, paragraph, numbering_catalog)
            add_unit("root", paragraph, ["body", child_index, "w:p"], position, numbering.get(position), catalogue)
            position += 1
        elif child.tag == qn("w:tbl"):
            add_table("root", Table(child, document), ["body", child_index, "w:tbl"])
            position += 1

    facts["extensions"]["docx_numbering_report"] = {"source_lists": report.get("source_lists", {})}
    validate_source_facts(facts)
    return facts


def detect_source_type(input_path: Path | str) -> str:
    """Return the source type the native chain preflights against."""

    return "docx" if Path(input_path).suffix.lower() == ".docx" else "markdown"


def parse(source: Path | str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    store = context.get("source_store") or SourceStore()
    owns_store = "source_store" not in context
    text = context.get("text")
    try:
        suffix = Path(source).suffix.lower()
        if suffix == ".docx":
            return _parse_docx(source, store=store)
        return _parse_markdown(source, store=store, text=text)
    finally:
        if owns_store:
            store.close()


def summarize_source_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """A public consumer that only reads the SourceFacts contract."""
    validate_source_facts(facts)
    return {
        "source_type": facts["source"]["source_type"],
        "unit_count": len(facts["units"]),
        "container_count": len(facts["containers"]),
        "resource_count": len(facts["resources"]),
        "diagnostic_codes": [item["code"] for item in facts["diagnostics"]],
        "marker_kinds": sorted({item.get("marker_kind") for item in facts["evidence"].values() if item.get("marker_kind")}),
    }


def digest_source_facts(facts: dict[str, Any]) -> str:
    """Return a stable digest for the immutable SourceFacts contract."""
    validate_source_facts(facts)
    payload = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _digest(payload)
