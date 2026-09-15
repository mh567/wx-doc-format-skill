from __future__ import annotations

from typing import Callable
import hashlib

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import RGBColor
from table_formatting import normalize_table

from wxdf.list_policy import (
    normalize_wx_list_type,
    wx_list_style_name,
    wx_numbering_abstract_key,
)
from appendix_semantics import APPENDIX_HEADING_STYLES

def style_from_profile(template_profile: dict | None, role: str, fallback: str) -> str:
    if template_profile:
        return template_profile.get("resolved_styles", {}).get(role, fallback)
    return fallback


def list_style_for_model(list_type: str, level: int, template_profile: dict | None = None) -> str:
    return wx_list_style_name(list_type, level, template_profile)


def _new_list_num(doc, abstract_num_id: int) -> int:
    """Clone an abstract numbering definition into a new num instance with start=1.

    Creates a proper <w:num> element with <w:lvlOverride> wrapping <w:startOverride>
    so that the list restarts from the first value.
    """
    numbering = doc.part.numbering_part.element
    max_id = 0
    for num in numbering.findall(qn("w:num")):
        try:
            nid = int(num.get(qn("w:numId")))
            if nid > max_id:
                max_id = nid
        except (ValueError, TypeError):
            pass
    new_id = max_id + 1

    num_el = OxmlElement("w:num")
    num_el.set(qn("w:numId"), str(new_id))

    ref = OxmlElement("w:abstractNumId")
    ref.set(qn("w:val"), str(abstract_num_id))
    num_el.append(ref)

    lvl_override = OxmlElement("w:lvlOverride")
    lvl_override.set(qn("w:ilvl"), "0")
    start_override = OxmlElement("w:startOverride")
    start_override.set(qn("w:val"), "1")
    lvl_override.append(start_override)
    num_el.append(lvl_override)

    numbering.append(num_el)
    return new_id


def _set_list_numbering(paragraph, num_id: int, ilvl: int = 0) -> None:
    """Attach existing numId to a paragraph (the style already defines the rest)."""
    try:
        p_pr = paragraph._element.get_or_add_pPr()
        num_pr = p_pr.find(qn("w:numPr"))
        if num_pr is None:
            num_pr = OxmlElement("w:numPr")
            p_pr.append(num_pr)
        else:
            for child in list(num_pr):
                num_pr.remove(child)
        nid_el = OxmlElement("w:numId")
        nid_el.set(qn("w:val"), str(num_id))
        num_pr.append(nid_el)
        ilvl_el = OxmlElement("w:ilvl")
        ilvl_el.set(qn("w:val"), str(ilvl))
        num_pr.append(ilvl_el)
    except Exception:
        pass


def _add_paragraph_with_soft_break_lines(doc, style: str, lines: list[str]):
    try:
        paragraph = doc.add_paragraph(style=style)
    except Exception:
        paragraph = doc.add_paragraph()
    for index, line in enumerate(lines):
        if index:
            paragraph.add_run().add_break()
        if line:
            paragraph.add_run(line)
    return paragraph


def _add_explicit_page_break(doc) -> None:
    paragraph = doc.add_paragraph()
    paragraph.add_run().add_break(WD_BREAK.PAGE)


def _add_seq_caption(
    doc,
    caption_type: str,
    caption_text: str,
    *,
    template_profile: dict | None = None,
):
    """Render a caption with a Word SEQ field and the template caption style."""
    style = style_from_profile(template_profile, "caption", "Caption")
    try:
        paragraph = doc.add_paragraph(style=style)
    except Exception:
        paragraph = doc.add_paragraph()

    seq_name = "Table" if caption_type == "table" else "Figure"
    prefix = "表 " if caption_type == "table" else "图 "
    paragraph.add_run(prefix)

    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), f"SEQ {seq_name} \\* ARABIC")
    field_run = OxmlElement("w:r")
    field_text = OxmlElement("w:t")
    field_text.text = "1"
    field_run.append(field_text)
    field.append(field_run)
    paragraph._p.append(field)

    paragraph.add_run(f" {caption_text}")
    return paragraph


def render_document_model(
    model: dict,
    doc,
    report: dict,
    row_height_cm: float,
    row_height_rule: str,
    numbering_ids: dict,
    *,
    template_profile: dict | None = None,
) -> None:
    """
    Render a normalized AST model into a template-created document.
    This function ONLY sets paragraph styles from the template.
    No manual numbering XML, no run-level fonts, no direct formatting.
    
    The template's style definitions already carry:
      - Heading styles bound to numbering (numId=1)
      - List styles bound to numbering (numId=3, 8, etc.)
      - Note, caption, appendix styles with their own numbering
    """
    active_list_nums: dict[int, int] = {}
    heading_num_id = numbering_ids.get("heading")
    markdown_semantic_order = 0

    def track_markdown_paragraph(block: dict, paragraph) -> None:
        nonlocal markdown_semantic_order
        source = block.get("source", {})
        if source.get("semantic_origin") != "markdown_token":
            return
        para_id = f"{len(report.setdefault('markdown_render_semantics', {}).setdefault('paragraphs', [])) + 1:08X}"
        markdown_semantic_order += 1
        paragraph._p.set(qn("w14:paraId"), para_id)
        report["markdown_render_semantics"]["paragraphs"].append({
            "para_id": para_id,
            "document_order": markdown_semantic_order,
            "block_type": block.get("block_type"),
            "text": block.get("text"),
            "inline_runs": block.get("inline_runs"),
            "role": block.get("role"),
            "level": block.get("level"),
            "list_type": block.get("list_type"),
            "restart": block.get("restart"),
            "parent_list_item_id": block.get("parent_list_item_id"),
            "numbering": dict(source.get("numbering", {})),
            "task_state": source.get("task_state"),
            "container_quote_depth": source.get("container_quote_depth"),
            "asset_path": source.get("asset_path"),
            "asset_sha256": source.get("asset_sha256"),
            "asset_id": block.get("asset_id"),
            "alt_text": block.get("alt_text"),
            "caption_type": block.get("caption_type"),
            "appendix_id": block.get("appendix_id"),
            "classification": block.get("classification"),
            "title_lines": block.get("title_lines"),
        })

    def record_image_relationship(paragraph) -> str | None:
        for element in paragraph._p.iter():
            relation_id = element.get(qn("r:embed"))
            if not relation_id:
                continue
            try:
                return hashlib.sha256(paragraph.part.related_parts[relation_id].blob).hexdigest()
            except (KeyError, AttributeError):
                return None
        return None

    def track_markdown_table(block: dict, table) -> None:
        nonlocal markdown_semantic_order
        source = block.get("source", {})
        if source.get("semantic_origin") != "markdown_token":
            return
        markdown_semantic_order += 1
        report.setdefault("markdown_render_semantics", {}).setdefault("tables", []).append({
            "table_index": len(doc.tables) - 1,
            "document_order": markdown_semantic_order,
            "table_type": block.get("table_type"),
            "header_rows": block.get("header_rows"),
            "rows": [[{"cell_role": cell.get("cell_role"), "inline_runs": cell.get("inline_runs")} for cell in row] for row in block.get("rows", [])],
        })

    def add_text_paragraph(text: str, style: str):
        try:
            doc.styles[style]
        except KeyError:
            return doc.add_paragraph(text)
        return doc.add_paragraph(text, style=style)

    def write_inline_runs(paragraph, block: dict) -> None:
        runs = block.get("inline_runs") or []
        if not runs:
            return
        paragraph.clear()
        for item in runs:
            text = str(item.get("text") or "")
            if not text:
                continue
            run = paragraph.add_run(text)
            marks = set(item.get("marks") or [])
            href = item.get("href")
            style_key = "Markdown" + "".join(mark.title() for mark in sorted(marks)) + ("Link" if href else "")
            if marks or href:
                try:
                    style = doc.styles[style_key]
                except KeyError:
                    style = doc.styles.add_style(style_key, WD_STYLE_TYPE.CHARACTER)
                    style.font.bold = "strong" in marks
                    style.font.italic = "emphasis" in marks
                    if "code" in marks:
                        style.font.name = "Courier New"
                    if href:
                        style.font.color.rgb = RGBColor(5, 99, 193)
                        style.font.underline = True
                run.style = style
            if href:
                relation_id = paragraph.part.relate_to(href, RT.HYPERLINK, is_external=True)
                hyperlink = OxmlElement("w:hyperlink")
                hyperlink.set(qn("r:id"), relation_id)
                paragraph._p.remove(run._r)
                hyperlink.append(run._r)
                paragraph._p.append(hyperlink)

    for block in model.get("document", {}).get("blocks", []):
        block_type = block.get("block_type")

        # --- Heading ---
        if block_type == "heading":
            text = block.get("text", "")
            level = int(block.get("level") or 0)
            role = block.get("role", "heading")

            if role == "title" or level <= 0:
                style = style_from_profile(template_profile, "title", "文档标题")
                paragraph = add_text_paragraph(text, style)
                write_inline_runs(paragraph, block)
                track_markdown_paragraph(block, paragraph)
                active_list_nums = {}
                continue

            if role == "appendix_heading":
                style = style_from_profile(
                    template_profile,
                    f"appendix_heading_{level}",
                    APPENDIX_HEADING_STYLES.get(level, f"附录{level}级标题"),
                )
                paragraph = add_text_paragraph(text, style)
                write_inline_runs(paragraph, block)
                track_markdown_paragraph(block, paragraph)
                report.setdefault("automatic_numbers", []).append(
                    {
                        "type": "appendix_heading",
                        "text": text,
                        "level": level,
                        "appendix_id": block.get("appendix_id"),
                        "source": "model",
                    }
                )
                active_list_nums = {}
                continue

            style = style_from_profile(template_profile, f"heading_{level}", f"Heading {level}")
            paragraph = add_text_paragraph(text, style)
            write_inline_runs(paragraph, block)
            track_markdown_paragraph(block, paragraph)

            report.setdefault("automatic_numbers", []).append(
                {"type": "heading", "text": text, "level": level, "source": "model"}
            )
            active_list_nums = {}

        # --- List Item ---
        elif block_type == "list_item":
            text = block.get("text", "")
            level = int(block.get("level") or 0)
            list_type = normalize_wx_list_type(
                block.get("list_type", "lower_letter_paren"), level,
            )
            restart = block.get("restart", False)
            style_name = list_style_for_model(list_type, level, template_profile)

            paragraph = add_text_paragraph(text, style_name)
            write_inline_runs(paragraph, block)
            track_markdown_paragraph(block, paragraph)

            # Only numbered lists need manual numId management for restart.
            # Key by (level, list_type) so that letter‑style and decimal‑style
            # lists maintain independent numbering within the same section.
            abstract_key = wx_numbering_abstract_key(list_type, level)
            if abstract_key is not None:
                list_key = (level, list_type)
                if restart or list_key not in active_list_nums:
                    aid = numbering_ids.get(abstract_key)
                    if aid is not None:
                        active_list_nums[list_key] = _new_list_num(doc, aid)
                nid = active_list_nums.get(list_key)
                if nid is not None:
                    _set_list_numbering(doc.paragraphs[-1], nid, 0)

                report.setdefault("automatic_numbers", []).append(
                    {"type": "list", "text": text, "source": "model"}
                )

        # --- Table ---
        elif block_type == "table":
            from text_utils import looks_like_code_sample_table

            rows_data = block.get("rows", [])
            if rows_data:
                col_count = max(len(r) for r in rows_data)
                table = doc.add_table(rows=len(rows_data), cols=col_count)
                for ri, row_data in enumerate(rows_data):
                    for ci in range(col_count):
                        text = row_data[ci].get("text", "") if ci < len(row_data) else ""
                        cell = table.rows[ri].cells[ci]
                        cell.text = text
                        if ci < len(row_data) and row_data[ci].get("inline_runs"):
                            write_inline_runs(cell.paragraphs[0], row_data[ci])
                normalize_table(
                    table,
                    template_profile,
                    row_height_cm,
                    row_height_rule,
                    role=block.get("table_type", "data"),
                    path=str(len(doc.tables)),
                )
                track_markdown_table(block, table)
            active_list_nums = {}

        # --- Image ---
        elif block_type == "image":
            asset_path = str(block.get("source", {}).get("asset_path") or "")
            if not asset_path:
                report.setdefault("content_warnings", []).append({
                    "type": "markdown_image_missing_asset_path",
                    "block_id": block.get("id"),
                })
                continue
            try:
                paragraph = doc.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph.add_run().add_picture(asset_path)
                track_markdown_paragraph(block, paragraph)
                report["markdown_render_semantics"]["paragraphs"][-1]["image_relationship_sha256"] = record_image_relationship(paragraph)
            except Exception as error:
                report.setdefault("content_warnings", []).append({
                    "type": "markdown_image_render_failed",
                    "block_id": block.get("id"),
                    "error": type(error).__name__,
                })
            active_list_nums = {}

        elif block_type == "separator":
            paragraph = doc.add_paragraph()
            p_pr = paragraph._p.get_or_add_pPr()
            borders = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "4")
            bottom.set(qn("w:space"), "1")
            bottom.set(qn("w:color"), "auto")
            borders.append(bottom)
            p_pr.append(borders)
            track_markdown_paragraph(block, paragraph)
            active_list_nums = {}

        # --- Appendix ---
        elif block_type == "appendix":
            if block.get("layout", {}).get("page_break_before", True):
                _add_explicit_page_break(doc)
            style = style_from_profile(template_profile, "appendix_title", "附录标题")
            lines = list(block.get("title_lines") or [])
            if not lines:
                lines = [""]
                classification = block.get("classification")
                if classification:
                    lines.append(f"（{classification}）")
                if block.get("title"):
                    lines.append(str(block["title"]))
            paragraph = _add_paragraph_with_soft_break_lines(doc, style, lines)
            track_markdown_paragraph(block, paragraph)
            report.setdefault("automatic_numbers", []).append(
                {
                    "type": "appendix",
                    "appendix_id": block.get("appendix_id"),
                    "title": block.get("title", ""),
                    "source": "model",
                }
            )
            active_list_nums = {}

        # --- Caption ---
        elif block_type == "caption":
            caption_type = block.get("caption_type", "table")
            caption_text = block.get("text", "")
            appendix_id = str(block.get("appendix_id") or "").strip()
            source_text = str(block.get("source", {}).get("raw_text") or "").strip()
            if appendix_id and source_text:
                text = source_text
                style = style_from_profile(template_profile, "caption", "Caption")
                paragraph = add_text_paragraph(text, style)
            else:
                paragraph = _add_seq_caption(
                    doc,
                    "figure" if caption_type == "figure" else "table",
                    caption_text,
                    template_profile=template_profile,
                )
            track_markdown_paragraph(block, paragraph)
            active_list_nums = {}

        # --- Body / Note / Formula ---
        else:
            source_role = block.get("role") or block.get("source", {}).get("role")
            if source_role == "note":
                style = style_from_profile(template_profile, "note", "3.1注-无编号注")
            elif source_role == "numbered_note":
                style = style_from_profile(template_profile, "numbered_note", "3.2注-有编号注")
            elif source_role == "formula":
                style = style_from_profile(template_profile, "formula", "Normal")
            else:
                style = style_from_profile(template_profile, "body", "Normal")
            paragraph = add_text_paragraph(block.get("text", ""), style)
            write_inline_runs(paragraph, block)
            track_markdown_paragraph(block, paragraph)
            active_list_nums = {}
