from __future__ import annotations

from pathlib import Path
from typing import Callable

from text_utils import (
    caption_parts as _caption_parts,
    heading_level_from_style,
    heading_level_from_text,
    list_kind_for_text,
    list_level_from_text,
    normalize_heading_style_level,
    paragraph_has_graphics,
    paragraph_num_info,
    resolved_heading_level,
    source_heading_level_shift,
    strip_heading_marker,
    strip_list_marker,
    table_rows_for_model,
)
from document_model import (
    append_block,
    appendix_block,
    body_block,
    caption_block,
    heading_block,
    image_block,
    list_item_block,
    new_document_model,
    source_record,
    table_block,
)
from md_pipeline import model_list_type_for_kind
from list_style_mapping import wx_list_style_name
from note_semantics import source_note_role, strip_source_note_marker
from table_semantics import classify_docx_table
from unordered_lists import annotate_unordered_candidates, paragraph_layout_evidence
from list_group_detection import annotate_semantic_list_groups




def _docx_list_shape(
    text: str,
    source_style: str,
    numbering: dict | None,
) -> tuple[int, str]:
    if numbering:
        return (
            int(numbering.get("ilvl", 0)),
            numbering.get("list_type", "lower_letter_paren"),
        )
    if "1.2一级列项-无编号" in source_style:
        return 0, "dash"
    if "2.2二级列项-无编号" in source_style:
        return 1, "bullet_dot"
    kind = list_kind_for_text(text)
    return list_level_from_text(text), model_list_type_for_kind(kind)


def infer_docx_role(
    paragraph,
    strict_normalize: bool,
    parse_report: dict,
    numbering: dict | None = None,
) -> tuple[str, str | None, str]:
    """
    Determine the semantic role of a DOCX paragraph based on style and text patterns.
    Step 1: parse DOCX paragraph into (text, style_name, role) for AST construction.
    """
    from text_utils import (
        is_caption_text, is_date_like_text, is_toc_title, is_toc_entry,
        looks_like_list_item, list_style_for_text, heading_level_from_text,
        heading_style_for_level, is_compact_numbered_function_heading,
        hierarchical_heading_level_from_text,
        source_numbering_heading_level, looks_like_visual_heading,
        clean_note_prefix, is_formula_text, is_appendix_title,
    )
    import re

    text = paragraph.text.strip()
    style_name = paragraph.style.name if paragraph.style is not None else ""

    if is_toc_entry(text):
        return text, None, "skip"
    note_role = source_note_role(style_name, numbering, text)
    if note_role is not None:
        parse_report.setdefault("inferred_notes", []).append({
            "text": text,
            "role": note_role,
            "source": "docx-note-semantics",
            "source_position": (numbering or {}).get("source_position"),
            "style": style_name,
        })
        return strip_source_note_marker(text, note_role), None, note_role
    if is_caption_text(text):
        return text, "Caption", "caption"
    if is_date_like_text(text):
        return text, None, "body"
    if style_name == "文档标题":
        return text, "文档标题", "title"
    if style_name.startswith("Heading"):
        # API-doc section labels like "请求参数"/"返回示例" are not
        # real headings even though the source styles them as Heading.
        _api_labels = {
            "请求参数", "请求示例", "返回参数", "返回示例",
            "接口说明", "请求体", "响应体", "成功响应", "失败响应",
        }
        _stripped = text.rstrip("：:")
        if _stripped in _api_labels:
            return text, None, "body"
        return text, style_name, "heading"
    if is_toc_title(text):
        return text, None, "body"

    # A dotted Arabic section marker is a complete semantic token.  Resolve it
    # before Word numbering, list styles, and visible list markers so ``6.3``
    # can never be consumed as the list marker ``6.``.
    hierarchical_level = hierarchical_heading_level_from_text(text)
    if hierarchical_level is not None:
        parse_report.setdefault("inferred_headings", []).append({
            "text": text,
            "level": hierarchical_level,
            "source": "docx-hierarchical-text",
        })
        return text, heading_style_for_level(hierarchical_level), "heading"

    if numbering and numbering.get("status") == "detected":
        parse_report.setdefault("inferred_lists", []).append({
            "text": text,
            "source": "docx-numbering",
            "source_position": numbering.get("source_position"),
            "num_id": numbering.get("num_id"),
            "confidence": numbering.get("confidence"),
        })
        list_style = wx_list_style_name(
            numbering.get("list_type"),
            int(numbering.get("ilvl") or 0),
        )
        return text, list_style, "list"
    if numbering and numbering.get("status") == "ambiguous":
        parse_report.setdefault("ambiguous_numbered_paragraphs", []).append({
            "text": text,
            "source_position": numbering.get("source_position"),
            "num_id": numbering.get("num_id"),
            "evidence": numbering.get("evidence", []),
        })
    list_style = style_name == "List Paragraph" or "列项" in style_name
    numbering_status = numbering.get("status") if numbering else None
    if list_style and numbering_status == "ignored":
        parse_report.setdefault("suppressed_list_style_conflicts", []).append({
            "text": text,
            "style": style_name,
            "source_position": numbering.get("source_position"),
            "evidence": numbering.get("evidence", []),
        })
    elif list_style:
        parse_report.setdefault("inferred_lists", []).append({"text": text, "source": "docx-style"})
        target_style = style_name if "列项" in style_name else "1.1一级列项-编号"
        return text, target_style, "list"

    if looks_like_list_item(text):
        parse_report.setdefault("inferred_lists", []).append({"text": text, "source": "docx-text"})
        return text, list_style_for_text(text), "list"

    inferred_level = heading_level_from_text(text)
    if inferred_level is not None:
        parse_report.setdefault("inferred_headings", []).append({"text": text, "level": inferred_level, "source": "docx-text"})
        return text, heading_style_for_level(inferred_level), "heading"

    if strict_normalize and is_compact_numbered_function_heading(text):
        # Short function labels ("2. 配置管理") are valid sub-headings
        # but not top-level visual headings — keep them as list or body.
        if looks_like_visual_heading(paragraph):
            parse_report.setdefault("inferred_headings", []).append({"text": text, "level": 3, "source": "docx-compact"})
            return text, "Heading 3", "heading"
        # Otherwise demote to list — they are list-like function entries.
        parse_report.setdefault("inferred_lists", []).append({"text": text, "source": "docx-compact-demoted"})
        return text, list_style_for_text(text), "list"

    if strict_normalize:
        nlevel = source_numbering_heading_level(paragraph)
        if nlevel is not None:
            parse_report.setdefault("inferred_headings", []).append({"text": text, "level": nlevel, "source": "docx-numbering"})
            return text, heading_style_for_level(nlevel), "heading"

    if strict_normalize and looks_like_visual_heading(paragraph):
        parse_report.setdefault("suspect_visual_headings", []).append({"text": text, "assigned_level": 2})
        return text, "Heading 2", "heading"

    if text.startswith(("\u5907\u6ce8\uff1a", "\u7f16\u5199\u63d0\u793a\uff1a", "\u3010\u5907\u6ce8\u63d0\u793a\u3011", "\u3010\u7f16\u5199\u6837\u4f8b\u3011")):
        return clean_note_prefix(text), None, "note"

    if is_formula_text(text):
        return text, "\u516c\u5f0f", "formula"

    if is_appendix_title(text):
        return text, "\u9644\u5f55\u6807\u9898", "appendix_title"

    if strict_normalize and len(text) <= 30 and not text.endswith(("\u3002", "\uff1b", ";", "\uff0c", ",")):
        parse_report.setdefault("ambiguous_short_paragraphs", []).append(text)

    return text, None, "body"


def heading_level_from_pstyle(pstyle: str | None) -> int | None:
    if not pstyle:
        return None
    return heading_level_from_style(pstyle)


def parse_docx_to_model_simple(
    src: Path,
    src_doc,
    strict_normalize: bool,
    heading_level_shift: int,
    *,
    skill_version: Callable[[], str],
    new_report: Callable[[], dict],
    iter_blocks: Callable,
    paragraph_class: type,
    infer_docx_role: Callable,
    looks_like_code_sample_table: Callable,
    caption_pattern,
    excluded_source_positions: set[int] | None = None,
    numbering_context: dict[int, dict] | None = None,
) -> dict:
    model = new_document_model(src, "docx", skill_version())
    parse_report = new_report()
    block_index = 1
    active_list_levels: set[int] = set()

    def next_id() -> str:
        nonlocal block_index
        block_id = f"b{block_index:04d}"
        block_index += 1
        return block_id

    def reset_lists() -> None:
        active_list_levels.clear()

    excluded_source_positions = excluded_source_positions or set()
    numbering_context = numbering_context or {}

    for source_position, block in enumerate(iter_blocks(src_doc)):
        if source_position in excluded_source_positions:
            parse_report.setdefault("excluded_source_blocks", []).append(source_position)
            continue
        if isinstance(block, paragraph_class):
            text = block.text.strip()
            if not text and not paragraph_has_graphics(block):
                continue
            source_style = block.style.name if block.style is not None else ""
            num_level, num_id = paragraph_num_info(block)
            numbering = numbering_context.get(source_position)
            inferred_text, style, role = infer_docx_role(
                block, strict_normalize, parse_report, numbering,
            ) if text else ("", None, "body")
            if role == "skip":
                continue
            if role == "heading" and heading_level_from_style(source_style) is not None:
                style = normalize_heading_style_level(style, heading_level_shift)
            source = source_record(
                raw_text=text,
                style=source_style,
                num_id=num_id,
                ilvl=num_level,
                source_position=source_position,
                inferred_role=role,
                inferred_style=style,
                numbering=numbering,
                layout=paragraph_layout_evidence(block),
            )
            if paragraph_has_graphics(block):
                if text:
                    if role == "heading":
                        heading_level = resolved_heading_level(style, num_level, inferred_text)
                        append_block(
                            model,
                            heading_block(
                                next_id(),
                                strip_heading_marker(inferred_text),
                                heading_level,
                                source=source_record(**source, had_mixed_graphic=True),
                            ),
                        )
                    elif role == "list":
                        lst_level, list_type = _docx_list_shape(
                            inferred_text, source_style, numbering,
                        )
                        restart = bool(numbering.get("restart")) if numbering else lst_level not in active_list_levels
                        active_list_levels.add(lst_level)
                        append_block(
                            model,
                            list_item_block(
                                next_id(),
                                strip_list_marker(inferred_text),
                                lst_level,
                                list_type,
                                restart=restart,
                                source=source_record(**source, had_mixed_graphic=True),
                            ),
                        )
                    else:
                        append_block(
                            model,
                            body_block(
                                next_id(),
                                inferred_text,
                                role=role if role in {"note", "numbered_note", "formula"} else None,
                                source=source_record(**source, had_mixed_graphic=True),
                            ),
                        )
                    reset_lists()
                    continue
                append_block(model, image_block(next_id(), alt_text="", source=source))
                reset_lists()
                continue
            if not text:
                continue
            if role == "title":
                append_block(
                    model,
                    heading_block(
                        next_id(), inferred_text, 0, role="title", source=source,
                    ),
                )
                reset_lists()
            elif role == "heading":
                heading_level = resolved_heading_level(style, num_level, inferred_text)
                append_block(
                    model,
                    heading_block(
                        next_id(),
                        strip_heading_marker(inferred_text),
                        heading_level,
                        source=source,
                    ),
                )
                reset_lists()
            elif role == "list":
                lst_level, list_type = _docx_list_shape(
                    inferred_text, source_style, numbering,
                )
                restart = bool(numbering.get("restart")) if numbering else lst_level not in active_list_levels
                active_list_levels.add(lst_level)
                append_block(
                    model,
                    list_item_block(
                        next_id(),
                        strip_list_marker(inferred_text),
                        lst_level,
                        list_type,
                        restart=restart,
                        source=source,
                    ),
                )
            elif role == "caption":
                ct, label, raw_number, caption_text = _caption_parts(inferred_text)
                append_block(
                    model,
                    caption_block(
                        next_id(),
                        caption_text,
                        ct,
                        label=label,
                        raw_number=raw_number,
                        source=source,
                    ),
                )
                reset_lists()
            elif role in {"note", "numbered_note"}:
                from text_utils import clean_note_prefix as _cnp
                append_block(
                    model,
                    body_block(
                        next_id(), _cnp(inferred_text), role=role,
                        source={**source, "role": role},
                    ),
                )
                reset_lists()
            else:
                append_block(
                    model,
                    body_block(next_id(), inferred_text, source=source),
                )
                reset_lists()
        else:
            cell_texts = []
            for row in block.rows:
                for cell in row.cells:
                    cell_texts.append(" ".join(p.text for p in cell.paragraphs))
            joined = " ".join(cell_texts)
            if joined:
                ct, label, raw_number, caption_text = _caption_parts(joined)
                if ct != "unknown" and caption_text:
                    append_block(
                        model,
                        caption_block(
                            next_id(), caption_text, ct,
                            label=label, raw_number=raw_number,
                            source=source_record(source_position=source_position),
                        ),
                    )
                    reset_lists()
                    continue
            semantics = classify_docx_table(
                block,
                multi_cell_code_sample=looks_like_code_sample_table(block),
            )
            table_type = semantics.table_type
            header_rows = semantics.header_rows
            rows = table_rows_for_model(block, table_type, header_rows)
            append_block(
                model,
                table_block(
                    next_id(), table_type, rows, header_rows=header_rows,
                    source=source_record(
                        source_position=source_position,
                        table_semantics=semantics.as_source_record(),
                    ),
                ),
            )
            reset_lists()

    annotate_unordered_candidates(model, parse_report)
    annotate_semantic_list_groups(model, parse_report)
    model["parse_report"] = parse_report
    return model


def parse_docx_to_model(
    src: Path,
    src_doc,
    strict_normalize: bool,
    heading_level_shift: int,
    *,
    skill_version: Callable[[], str],
    new_report: Callable[[], dict],
    iter_blocks: Callable,
    paragraph_class: type,
    paragraph_has_graphics: Callable = None,
    paragraph_num_info: Callable = None,
    infer_docx_role: Callable,
    heading_level_from_style: Callable = None,
    normalize_heading_style_level: Callable = None,
    resolved_heading_level: Callable = None,
    strip_heading_marker: Callable = None,
    list_kind_for_text: Callable = None,
    list_level_from_text: Callable = None,
    strip_list_marker: Callable = None,
    clean_note_prefix: Callable = None,
    looks_like_code_sample_table: Callable,
    caption_pattern,
    excluded_source_positions: set[int] | None = None,
    numbering_context: dict[int, dict] | None = None,
) -> dict:
    """Compatibility wrapper that delegates to parse_docx_to_model_simple.
    Ignores injected utility functions since they are now imported directly from text_utils."""
    return parse_docx_to_model_simple(
        src, src_doc, strict_normalize, heading_level_shift,
        skill_version=skill_version,
        new_report=new_report,
        iter_blocks=iter_blocks,
        paragraph_class=paragraph_class,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=looks_like_code_sample_table,
        caption_pattern=caption_pattern,
        excluded_source_positions=excluded_source_positions,
        numbering_context=numbering_context,
    )
