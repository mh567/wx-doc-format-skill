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

    def add_paragraph(node_id: str, block: Mapping[str, Any], *, list_style: str | None = None) -> Any:
        style = list_style or _style_for_block(block, styles)
        try:
            if style:
                doc.styles[style]
            paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
        except KeyError:
            paragraph = doc.add_paragraph()
        _write_inline(paragraph, block.get("inline_content", []), resources)
        actual_style = paragraph.style.name if paragraph.style is not None else ""
        render_map[node_id] = {
            "kind": "paragraph",
            "paragraph": len(doc.paragraphs),
            "text": paragraph.text,
            "role": block.get("role"),
            "style": actual_style,
            "numbered": actual_style in {"List Number", "List Bullet"} or "列项" in actual_style,
        }
        return paragraph

    def add_table(node_id: str, block_id: str) -> None:
        block = document["blocks"][block_id]
        row_ids = document["children"].get(block_id, [])
        row_count = len(row_ids)
        column_count = max((len(document["children"].get(row_id, [])) for row_id in row_ids), default=1)
        try:
            table = doc.add_table(rows=max(1, row_count), cols=max(1, column_count), style="Table Grid")
        except (KeyError, ValueError):
            table = doc.add_table(rows=max(1, row_count), cols=max(1, column_count))
        for row_index, row_id in enumerate(row_ids):
            for col_index, cell_id in enumerate(document["children"].get(row_id, [])):
                paragraphs = [child for child in document["children"].get(cell_id, []) if child in document["blocks"]]
                if not paragraphs:
                    continue
                cell_paragraph = table.cell(row_index, col_index).paragraphs[0]
                cell_paragraph.clear()
                _write_inline(cell_paragraph, document["blocks"][paragraphs[0]].get("inline_content", []), resources)
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
        render_map[node_id] = {"kind": "table", "table": len(doc.tables), "rows": row_count, "cols": column_count}

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            if child_id in document["containers"]:
                container = document["containers"][child_id]
                kind = container.get("kind")
                if kind == "list_item":
                    presentation = normalized.get("presentation", {}).get("nodes", {}).get(child_id, {})
                    children = document["children"].get(child_id, [])
                    anchor_id = presentation.get("marker_anchor")
                    anchor_block = document["blocks"].get(anchor_id)
                    if anchor_block is None:
                        generated = next((item for item in normalized.get("generated_nodes", []) if item.get("node_id") == anchor_id), None)
                        anchor_block = generated or {"block_type": "paragraph", "role": "body", "inline_content": []}
                    if presentation.get("marker_kind") == "ordered":
                        list_style = styles.get("list_letter" if presentation.get("target_level", 0) == 0 else "list_decimal", "List Number")
                    else:
                        list_style = styles.get("list_dash" if presentation.get("target_level", 0) == 0 else "list_bullet", "List Bullet")
                    add_paragraph(child_id, anchor_block, list_style=list_style)
                    for nested_child in children:
                        if nested_child == anchor_id:
                            continue
                        walk(nested_child)
                else:
                    walk(child_id)
                continue
            block = document["blocks"].get(child_id)
            if block is None:
                continue
            if block.get("block_type") == "paragraph":
                add_paragraph(child_id, block)
            elif block.get("block_type") == "table":
                add_table(child_id, child_id)
            elif block.get("block_type") == "image":
                resource_id = block.get("resource_id")
                resource = resources.get(resource_id)
                if resource is None:
                    raise RenderError(f"resource {resource_id!r} is unavailable for rendering")
                paragraph = doc.add_paragraph()
                if isinstance(resource, (bytes, bytearray)):
                    paragraph.add_run().add_picture(io.BytesIO(resource))
                else:
                    paragraph.add_run().add_picture(str(resource))
                render_map[child_id] = {"kind": "image", "paragraph": len(doc.paragraphs), "text": paragraph.text}
            elif block.get("block_type") == "opaque":
                diagnostics.append({"code": "opaque_node_skipped", "node_id": child_id})

    walk(document["root_id"])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    return {"output_path": str(output), "render_map": render_map, "render_plan": plan, "diagnostics": diagnostics}
