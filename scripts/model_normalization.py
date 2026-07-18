from __future__ import annotations

from copy import deepcopy
from typing import Callable

from text_utils import (
    clean_note_prefix,
    heading_level_from_text,
    is_appendix_title,
    is_caption_text,
    is_formula_text,
    list_kind_for_text,
    looks_like_list_item,
    strip_heading_marker,
    strip_list_marker,
)
from document_model import validate_document_model
from list_style_mapping import normalize_wx_list_type
from table_semantics import table_caption_eligible


def _model_list_type_for_kind(kind: str) -> str:
    mapping = {
        "letter": "lower_letter_paren",
        "decimal": "decimal_paren",
        "dash": "dash",
        "bullet2": "bullet_dot",
    }
    return mapping.get(kind, "lower_letter_paren")


def _summarize(model: dict) -> dict:
    blocks = model.get("document", {}).get("blocks", [])
    counts: dict[str, int] = {}
    for block in blocks:
        bt = block.get("block_type", "unknown")
        counts[bt] = counts.get(bt, 0) + 1
    return {"blocks": len(blocks), **counts}


def summarize_source_document_model(report: dict, model: dict) -> None:
    report["source_document_model_summary"] = _summarize(model)


def normalize_document_model_simple(
    source_model: dict,
    report: dict,
    *,
    strict_normalize: bool = False,
) -> dict:
    model = deepcopy(source_model)
    repairs = []
    active_list_signatures: set[tuple[int, str]] = set()

    for index, block in enumerate(model.get("document", {}).get("blocks", []), 1):
        block_type = block.get("block_type")
        text = str(block.get("text") or block.get("title") or "")

        if block_type == "body":
            source_role = block.get("source", {}).get("role")
            source_numbering = block.get("source", {}).get("numbering", {})
            if source_role in {"note", "numbered_note", "formula"} and block.get("role") != source_role:
                block["role"] = source_role
                repairs.append({"block": index, "type": "body_role_promoted", "role": source_role})
            if block.get("role") == "note" or text.startswith(("\u5907\u6ce8\uff1a", "\u7f16\u5199\u63d0\u793a\uff1a", "\u3010\u5907\u6ce8\u63d0\u793a\u3011", "\u3010\u7f16\u5199\u6837\u4f8b\u3011")):
                clean_text = clean_note_prefix(text)
                if clean_text != text:
                    block["text"] = clean_text
                    repairs.append({"block": index, "type": "note_prefix_normalized", "from": text, "to": clean_text})
            if is_formula_text(text) and block.get("role") != "formula":
                block["role"] = "formula"
                repairs.append({"block": index, "type": "body_role_promoted", "role": "formula"})
            if is_appendix_title(text):
                block["block_type"] = "appendix"
                block["title"] = text
                block.pop("text", None)
                block["numbering"] = {"mode": "auto"}
                active_list_signatures.clear()
                repairs.append({"block": index, "type": "body_retyped_as_appendix"})
                continue
            inferred_level = heading_level_from_text(text)
            if inferred_level is not None:
                block["block_type"] = "heading"
                block["role"] = "heading"
                block["level"] = inferred_level
                block["text"] = strip_heading_marker(text)
                block["numbering"] = {"mode": "auto"}
                active_list_signatures.clear()
                repairs.append({"block": index, "type": "body_retyped_as_heading", "level": inferred_level})
                continue
            if source_numbering.get("status") == "detected":
                block["block_type"] = "list_item"
                block["level"] = int(source_numbering.get("ilvl") or 0)
                block["list_type"] = source_numbering.get("list_type", "decimal_paren")
                block["restart"] = bool(source_numbering.get("restart"))
                repairs.append({
                    "block": index,
                    "type": "body_retyped_from_source_numbering",
                    "list_type": block["list_type"],
                })
                block_type = "list_item"
            elif looks_like_list_item(text):
                kind = list_kind_for_text(text)
                lst_level = 1 if kind in {"decimal", "bullet2"} else 0
                block["block_type"] = "list_item"
                block["level"] = lst_level
                block["list_type"] = _model_list_type_for_kind(kind)
                block["text"] = strip_list_marker(text)
                block["restart"] = False
                repairs.append({"block": index, "type": "body_retyped_as_list_item", "list_type": block["list_type"]})
                block_type = "list_item"
            elif is_caption_text(text):
                block["block_type"] = "caption"
                block["caption_type"] = "unknown"
                block["numbering"] = {"mode": "preserve_text", "label": None, "raw_number": None}
                repairs.append({"block": index, "type": "body_retyped_as_caption"})
                active_list_signatures.clear()
                continue

        if block_type == "heading":
            active_list_signatures.clear()
            level = int(block.get("level") or 0)
            if level > 0:
                original_text = str(block.get("text") or "")
                clean_text = strip_heading_marker(original_text)
                if clean_text != original_text:
                    block["text"] = clean_text
                    repairs.append({"block": index, "type": "heading_manual_number_removed", "from": original_text, "to": clean_text})
                numbering = block.setdefault("numbering", {})
                if numbering.get("mode") != "auto":
                    numbering["mode"] = "auto"
                    repairs.append({"block": index, "type": "heading_numbering_mode_auto", "text": block.get("text", "")})
            continue

        if block_type == "list_item":
            original_text = str(block.get("text") or "")
            clean_text = strip_list_marker(original_text)
            if clean_text != original_text:
                block["text"] = clean_text
                repairs.append({"block": index, "type": "list_manual_marker_removed", "from": original_text, "to": clean_text})
            if block.get("list_type") not in {"lower_letter_paren", "decimal_paren", "bullet_dot", "dash"}:
                kind = list_kind_for_text(original_text)
                block["list_type"] = _model_list_type_for_kind(kind)
                repairs.append({"block": index, "type": "list_type_normalized", "list_type": block["list_type"]})
            level = int(block.get("level") or 0)
            wx_list_type = normalize_wx_list_type(block.get("list_type"), level)
            if block.get("list_type") != wx_list_type:
                block["list_type"] = wx_list_type
                repairs.append({
                    "block": index,
                    "type": "list_type_aligned_to_wx_level",
                    "level": level,
                    "list_type": wx_list_type,
                })
            signature = (level, str(block.get("list_type") or ""))
            source_numbering = block.get("source", {}).get("numbering", {})
            should_restart = bool(source_numbering.get("restart")) or signature not in active_list_signatures
            if bool(block.get("restart")) != should_restart:
                block["restart"] = should_restart
                repairs.append({"block": index, "type": "list_restart_normalized", "restart": should_restart})
            active_list_signatures.add(signature)
            continue

        active_list_signatures.clear()
        if block_type == "image":
            layout = block.setdefault("layout", {})
            if layout.get("align") != "center":
                layout["align"] = "center"
                repairs.append({"block": index, "type": "image_align_center"})
        elif block_type == "caption":
            caption_text = str(block.get("text") or "")
            label = block.get("numbering", {}).get("label")
            raw_number = block.get("numbering", {}).get("raw_number")
            if label and raw_number:
                prefix = f"{label} {raw_number}"
                clean_text = caption_text.removeprefix(prefix).strip()
                if clean_text != caption_text:
                    block["text"] = clean_text
                    repairs.append({"block": index, "type": "caption_prefix_removed", "from": caption_text, "to": clean_text})
        elif block_type == "table":
            table_type = block.get("table_type", "unknown")
            if table_type in {"code_sample", "callout", "layout"} and block.get("header_rows") != 0:
                block["header_rows"] = 0
                repairs.append({
                    "block": index,
                    "type": "non_data_table_header_rows_zero",
                    "table_type": table_type,
                })
            if table_type == "code_sample":
                for row in block.get("rows", []):
                    for cell in row:
                        if cell.get("cell_role") != "code":
                            cell["cell_role"] = "code"
                            repairs.append({"block": index, "type": "code_sample_cell_role_code"})
            elif table_type in {"callout", "layout"}:
                for row in block.get("rows", []):
                    for cell in row:
                        if cell.get("cell_role") != "body":
                            cell["cell_role"] = "body"
                            repairs.append({
                                "block": index,
                                "type": "content_container_cell_role_body",
                                "table_type": table_type,
                            })
            elif table_type == "data":
                header_rows = int(block.get("header_rows") or 0)
                for row_index, row in enumerate(block.get("rows", [])):
                    expected_role = "header" if row_index < header_rows else "body"
                    for cell in row:
                        if cell.get("cell_role") != expected_role:
                            cell["cell_role"] = expected_role
                            repairs.append({"block": index, "type": "data_table_cell_role_normalized", "role": expected_role})
            if block.get("autofit") is not True:
                block["autofit"] = True
                repairs.append({"block": index, "type": "table_autofit_enabled"})
            if block.get("row_height_rule") != "atLeast":
                block["row_height_rule"] = "atLeast"
                repairs.append({"block": index, "type": "table_row_height_rule_at_least"})

    # ── F4: Infer lists from consecutive short body paragraphs ──
    blocks = model.get("document", {}).get("blocks", [])
    short_runs: list[int] = []
    for bi, block in enumerate(blocks):
        text = str(block.get("text", "") or "")
        is_short_body = (
            block.get("block_type") == "body"
            and not block.get("source", {}).get("role") in ("list_item", "note", "numbered_note", "formula", "appendix_title")
            and 15 <= len(text) <= 30
            and not text.endswith(("。", "；", ";", "，", ","))
            and not looks_like_list_item(text)
        )
        if is_short_body:
            if short_runs:
                prev_text = str(blocks[short_runs[-1]].get("text", ""))
                prev_len = len(prev_text)
                curr_len = len(text)
                max_len = max(prev_len, curr_len)
                min_len = min(prev_len, curr_len)
                similar = min_len > 0 and (max_len - min_len) / max_len < 0.5
            else:
                similar = True
            if similar:
                short_runs.append(bi)
                continue
            # Run broken — flush previous if long enough
            if len(short_runs) >= 3:
                for ri in short_runs:
                    rblock = blocks[ri]
                    rblock["block_type"] = "list_item"
                    rblock["level"] = 0
                    rblock["list_type"] = "lower_letter_paren"
                    rblock["restart"] = len(short_runs) == len(blocks) or ri == short_runs[0]
                    repairs.append({"block": ri + 1, "type": "short_para_retyped_as_list_item", "text": rblock.get("text", "")})
            short_runs = [bi]
        else:
            if len(short_runs) >= 3:
                for ri in short_runs:
                    rblock = blocks[ri]
                    rblock["block_type"] = "list_item"
                    rblock["level"] = 0
                    rblock["list_type"] = "lower_letter_paren"
                    rblock["restart"] = ri == short_runs[0]
                    repairs.append({"block": ri + 1, "type": "short_para_retyped_as_list_item", "text": rblock.get("text", "")})
            short_runs = []
    # End-of-blocks flush
    if len(short_runs) >= 3:
        for ri in short_runs:
            rblock = blocks[ri]
            rblock["block_type"] = "list_item"
            rblock["level"] = 0
            rblock["list_type"] = "lower_letter_paren"
            rblock["restart"] = ri == short_runs[0]
            repairs.append({"block": ri + 1, "type": "short_para_retyped_as_list_item", "text": rblock.get("text", "")})

    # Ensure every table has a preceding caption (tables: caption above, figures: caption below).
    blocks = model.get("document", {}).get("blocks", [])
    insert_indexes = []
    for bi in range(len(blocks)):
        if (
            blocks[bi].get("block_type") == "table"
            and table_caption_eligible(blocks[bi].get("table_type"))
        ):
            next_bi = bi + 1
            next_is_caption = next_bi < len(blocks) and blocks[next_bi].get("block_type") == "caption"
            prev_is_caption = bi > 0 and blocks[bi - 1].get("block_type") == "caption"
            if not next_is_caption and not prev_is_caption:
                insert_indexes.append(bi)
    for bi in reversed(insert_indexes):
        from document_model import caption_block
        table_type = blocks[bi].get("table_type", "data")
        caption_type = "table"
        label = "表"
        new_caption = caption_block(
            f"b{bi * 10 + 9999:04d}",
            "",
            caption_type,
            label=label,
            raw_number=None,
        )
        new_caption["_auto_generated"] = True
        blocks.insert(bi, new_caption)  # caption ABOVE table
        repairs.append({"block": bi, "type": "caption_auto_generated_for_table", "table_type": table_type})

    issues = validate_document_model(model)
    report["model_normalization_repairs"] = repairs
    report["document_model_summary"] = _summarize(model)
    report["document_model_issues"] = issues
    return model


def normalize_document_model(
    source_model: dict,
    report: dict,
    *,
    strip_heading_marker=None,
    heading_level_from_text=None,
    looks_like_list_item=None,
    list_kind_for_text=None,
    strip_list_marker=None,
    clean_note_prefix=None,
    is_formula_text=None,
    is_appendix_title=None,
    is_caption_text=None,
    **kwargs,
) -> dict:
    """Compatibility wrapper. Ignores injected functions (now imported from text_utils directly)."""
    return normalize_document_model_simple(
        source_model, report,
        strict_normalize=kwargs.get("strict_normalize", False),
    )
