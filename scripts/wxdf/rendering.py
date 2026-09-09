"""AST driven DOCX rendering with a small, auditable RenderMap."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Mapping

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from table_formatting import normalize_table

from .render_plan import build_render_plan
from .template import TemplateBundle


class RenderError(RuntimeError):
    """Raised when a normalized node cannot be rendered with available resources."""


def _profile_styles(normalized: Mapping[str, Any]) -> Mapping[str, str]:
    return normalized.get("presentation", {}).get("profile", {}).get("resolved_styles", {})


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
    return "".join(parts)


def _write_inline(paragraph: Any, inline_content: list[Mapping[str, Any]], resources: Mapping[str, Any]) -> None:
    for item in inline_content:
        kind = item.get("kind")
        if kind == "text":
            paragraph.add_run(str(item.get("text") or ""))
        elif kind == "break":
            paragraph.add_run().add_break()
        elif kind == "tab":
            paragraph.add_run("\t")
        elif kind == "link":
            run = paragraph.add_run(_text(item.get("children", []), resources))
            run.underline = True
        elif kind == "image":
            resource_id = item.get("resource_id")
            resource = resources.get(resource_id)
            if isinstance(resource, (bytes, bytearray)):
                paragraph.add_run().add_picture(io.BytesIO(resource))
            elif isinstance(resource, (str, Path)):
                paragraph.add_run().add_picture(str(resource))
            else:
                raise RenderError(f"resource {resource_id!r} has no renderable bytes or path")


def _style_for_block(block: Mapping[str, Any], styles: Mapping[str, str]) -> str | None:
    role = block.get("role")
    if role == "title":
        return styles.get("title", "文档标题")
    if role == "heading":
        return styles.get(f"heading_{block.get('level', 1)}", "Heading 1")
    if role == "caption":
        return styles.get("caption", "Caption")
    if role in {"note", "numbered_note"}:
        return styles.get(str(role), "Normal")
    return styles.get("body", "Normal")


def render(
    normalized: Mapping[str, Any],
    output_path: Path | str,
    *,
    template_path: Path | str | None = None,
    template_bundle: TemplateBundle | None = None,
    resources: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render normalized nodes in children order and save a DOCX artifact."""

    resources = resources or {}
    plan = build_render_plan(normalized)
    ast = normalized["source_ast"]
    document = ast["document"]
    styles = _profile_styles(normalized)
    selected_template = template_bundle.path if template_bundle is not None else template_path
    doc = Document(str(selected_template)) if selected_template else Document()
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
    paragraph_counter = 0
    table_counter = 0
    initial_cell_paragraphs: set[int] = set()

    def add_paragraph(
        node_id: str,
        block: Mapping[str, Any],
        *,
        list_style: str | None = None,
        numbering: Mapping[str, Any] | None = None,
        parent: Any | None = None,
    ) -> Any:
        nonlocal paragraph_counter
        style = list_style or _style_for_block(block, styles)
        if parent is None:
            try:
                if style:
                    doc.styles[style]
                paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            except KeyError:
                paragraph = doc.add_paragraph()
        else:
            parent_key = id(parent._tc)
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
        _write_inline(paragraph, block.get("inline_content", []), resources)
        actual_style = paragraph.style.name if paragraph.style is not None else ""
        paragraph_counter += 1
        render_map[node_id] = {
            "kind": "paragraph",
            "paragraph": paragraph_counter,
            "text": paragraph.text,
            "role": block.get("role"),
            "style": actual_style,
            "numbered": actual_style in {"List Number", "List Bullet"} or "列项" in actual_style,
        }
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
        try:
            if parent is None:
                table = doc.add_table(rows=max(1, row_count), cols=max(1, column_count), style="Table Grid")
            else:
                table = parent.add_table(rows=max(1, row_count), cols=max(1, column_count))
        except (KeyError, ValueError):
            table = parent.add_table(rows=max(1, row_count), cols=max(1, column_count)) if parent is not None else doc.add_table(rows=max(1, row_count), cols=max(1, column_count))
        if parent is None:
            try:
                table.style = "Table Grid"
            except (KeyError, ValueError):
                pass
        if parent is not None:
            initial_cell_paragraphs.add(id(parent._tc))
        table_body_style = styles.get("table_body")
        if table_body_style and table_body_style in doc.styles:
            normalize_table(
                table,
                {"resolved_styles": dict(styles)},
                0.69,
                "at-least",
                role=str(block.get("table_type") or "data"),
                path=str(len(doc.tables)),
            )
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
                for occupied_row in range(row_index + 1, min(row_count, row_index + row_span)):
                    for occupied_col in range(column_cursor, column_cursor + col_span):
                        occupied.add((occupied_row, occupied_col))
                render_children(cell_id, parent=cell)
                column_cursor += col_span
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
            resource_id = block.get("resource_id")
            resource = resources.get(resource_id)
            if resource is None:
                raise RenderError(f"resource {resource_id!r} is unavailable for rendering")
            if parent is None:
                paragraph = doc.add_paragraph()
            else:
                parent_key = id(parent._tc)
                if parent_key not in initial_cell_paragraphs and parent.paragraphs:
                    paragraph = parent.paragraphs[0]
                    paragraph.clear()
                    initial_cell_paragraphs.add(parent_key)
                else:
                    paragraph = parent.add_paragraph()
            if isinstance(resource, (bytes, bytearray)):
                paragraph.add_run().add_picture(io.BytesIO(resource))
            else:
                paragraph.add_run().add_picture(str(resource))
            paragraph_counter += 1
            render_map[block_id] = {"kind": "image", "paragraph": paragraph_counter, "text": paragraph.text}
        elif block.get("block_type") == "opaque":
            diagnostics.append({"code": "opaque_node_skipped", "node_id": block_id})

    def render_list_item(child_id: str, *, parent: Any | None = None) -> None:
        presentation = normalized.get("presentation", {}).get("nodes", {}).get(child_id, {})
        children = document["children"].get(child_id, [])
        anchor_id = presentation.get("marker_anchor")
        anchor_block = document["blocks"].get(anchor_id)
        if anchor_block is None:
            generated = next((item for item in normalized.get("generated_nodes", []) if item.get("node_id") == anchor_id), None)
            anchor_block = generated or {"block_type": "paragraph", "role": "body", "inline_content": []}
        if presentation.get("marker_kind") == "ordered":
            style_role = "list_letter" if presentation.get("target_level", 0) == 0 else "list_decimal"
            list_style = styles.get(style_role, "List Number")
        else:
            style_role = "list_dash" if presentation.get("target_level", 0) == 0 else "list_bullet"
            list_style = styles.get(style_role, "List Bullet")
        add_paragraph(
            child_id,
            anchor_block,
            list_style=list_style,
            numbering={
                "marker_kind": presentation.get("marker_kind"),
                "target_level": presentation.get("target_level", 0),
                "style_role": style_role,
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
        if node_id in document["blocks"]:
            render_block(node_id, parent=parent)
            return
        container = document["containers"].get(node_id)
        if container is None:
            return
        if container.get("kind") == "list_item":
            render_list_item(node_id, parent=parent)
            return
        render_children(node_id, parent=parent)

    render_children(document["root_id"])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    return {"output_path": str(output), "render_map": render_map, "render_plan": plan, "diagnostics": diagnostics}
