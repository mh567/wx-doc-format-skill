from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any


BLOCK_TYPES = {
    "heading",
    "body",
    "list_item",
    "image",
    "table",
    "caption",
    "appendix",
    "unknown",
}

TABLE_TYPES = {"data", "code_sample", "callout", "layout", "unknown"}
LIST_TYPES = {"lower_letter_paren", "decimal_paren", "bullet_dot", "dash"}


def new_document_model(source_path: Path | str, source_type: str, template_version: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "document": {
            "source": {
                "path": str(source_path),
                "type": source_type,
            },
            "template": {
                "name": "WX",
                "version": template_version,
            },
            "numbering_policy": {
                "headings": "auto",
                "lists_restart_scope": "section",
            },
            "blocks": [],
        },
    }


def source_record(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def heading_block(
    block_id: str,
    text: str,
    level: int,
    *,
    role: str = "heading",
    source: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block = {
        "id": block_id,
        "block_type": "heading",
        "role": role,
        "level": level,
        "text": text,
        "numbering": {"mode": "auto" if level > 0 else "none"},
        "source": source or {},
    }
    if context:
        block["context"] = context
    return block


def body_block(block_id: str, text: str, *, source: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": block_id,
        "block_type": "body",
        "text": text,
        "source": source or {},
    }


def list_item_block(
    block_id: str,
    text: str,
    level: int,
    list_type: str,
    *,
    restart: bool,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": block_id,
        "block_type": "list_item",
        "level": level,
        "list_type": list_type,
        "text": text,
        "restart": restart,
        "source": source or {},
    }


def image_block(
    block_id: str,
    *,
    asset_id: str | None = None,
    alt_text: str = "",
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block = {
        "id": block_id,
        "block_type": "image",
        "alt_text": alt_text,
        "layout": {"align": "center"},
        "source": source or {},
    }
    if asset_id:
        block["asset_id"] = asset_id
    return block


def table_block(
    block_id: str,
    table_type: str,
    rows: list[list[dict[str, Any]]],
    *,
    header_rows: int,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": block_id,
        "block_type": "table",
        "table_type": table_type,
        "header_rows": header_rows,
        "autofit": True,
        "row_height_rule": "atLeast",
        "rows": rows,
        "source": source or {},
    }


def caption_block(
    block_id: str,
    text: str,
    caption_type: str,
    *,
    label: str | None = None,
    raw_number: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": block_id,
        "block_type": "caption",
        "caption_type": caption_type,
        "text": text,
        "numbering": {
            "mode": "auto",
            "label": label or ("图" if caption_type == "figure" else "表"),
            "raw_number": raw_number,
        },
        "source": source or {},
    }


def appendix_block(
    block_id: str,
    title: str,
    *,
    appendix_id: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": block_id,
        "block_type": "appendix",
        "appendix_id": appendix_id,
        "title": title,
        "numbering": {"mode": "auto"},
        "source": source or {},
    }


def unknown_block(
    block_id: str,
    description: str,
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": block_id,
        "block_type": "unknown",
        "description": description,
        "source": source or {},
    }


def append_block(model: dict[str, Any], block: dict[str, Any]) -> None:
    model["document"]["blocks"].append(block)


def block_counts(model: dict[str, Any]) -> dict[str, int]:
    counts = Counter(block.get("block_type", "unknown") for block in model["document"]["blocks"])
    return dict(sorted(counts.items()))


def summarize_document_model(model: dict[str, Any]) -> dict[str, Any]:
    issues = validate_document_model(model)
    return {
        "schema_version": model["schema_version"],
        "block_count": len(model["document"]["blocks"]),
        "block_counts": block_counts(model),
        "issue_count": len(issues),
    }


def compare_document_models(source_model: dict[str, Any] | None, final_model: dict[str, Any]) -> dict[str, Any]:
    if source_model is None:
        return {
            "available": False,
            "warnings": [],
        }
    source_counts = block_counts(source_model)
    final_counts = block_counts(final_model)
    block_count_delta = len(final_model["document"]["blocks"]) - len(source_model["document"]["blocks"])
    type_count_delta = {}
    for block_type in sorted(set(source_counts) | set(final_counts)):
        delta = final_counts.get(block_type, 0) - source_counts.get(block_type, 0)
        if delta:
            type_count_delta[block_type] = delta
    warnings = []
    if block_count_delta:
        warnings.append({"type": "block_count_delta", "delta": block_count_delta})
    for block_type, delta in type_count_delta.items():
        warnings.append({"type": "block_type_count_delta", "block_type": block_type, "delta": delta})
    return {
        "available": True,
        "source_block_count": len(source_model["document"]["blocks"]),
        "final_block_count": len(final_model["document"]["blocks"]),
        "block_count_delta": block_count_delta,
        "source_block_counts": source_counts,
        "final_block_counts": final_counts,
        "type_count_delta": type_count_delta,
        "warnings": warnings,
    }


def manual_heading_number(text: str) -> bool:
    return bool(
        re.match(r"^\d+(?:\.\d+)+(?:[.．])?\s*(?=[A-Za-z\u4e00-\u9fff])", text)
        or re.match(r"^\d+(?:\.\d+)*\s+\S+", text)
        or re.match(r"^第[一二三四五六七八九十百千万0-9]+[章节]\s*[:：、.\s]?\S+", text)
        or re.match(r"^[一二三四五六七八九十]+[、.．]\s*\S+", text)
        or re.match(r"^[（(][一二三四五六七八九十0-9]+[）)]\s*\S+", text)
    )


def validate_document_model(model: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    blocks = model.get("document", {}).get("blocks", [])
    for index, block in enumerate(blocks, 1):
        block_type = block.get("block_type")
        if block_type not in BLOCK_TYPES:
            issues.append({"block": index, "type": "unknown_block_type", "value": block_type})
            continue
        if block_type == "heading":
            text = str(block.get("text") or "")
            level = block.get("level")
            if level is None:
                issues.append({"block": index, "type": "heading_missing_level", "text": text[:120]})
            if level and manual_heading_number(text):
                issues.append({"block": index, "type": "heading_manual_number", "text": text[:120]})
            if level and block.get("numbering", {}).get("mode") != "auto":
                issues.append({"block": index, "type": "heading_not_auto_numbered", "text": text[:120]})
        elif block_type == "list_item":
            text = str(block.get("text") or "")
            if block.get("list_type") not in LIST_TYPES:
                issues.append({"block": index, "type": "list_item_unknown_type", "text": text[:120]})
            if re.match(r"^(?:[a-zA-Z]\)|\d+\)|[（(]\d+[）)]|[-－—·•])\s*", text):
                issues.append({"block": index, "type": "list_item_manual_marker", "text": text[:120]})
        elif block_type == "table":
            if block.get("table_type") not in TABLE_TYPES:
                issues.append({"block": index, "type": "table_unknown_type"})
            if block.get("table_type") == "code_sample" and block.get("header_rows", 0) != 0:
                issues.append({"block": index, "type": "code_sample_has_header_rows"})
            rows = block.get("rows", [])
            if block.get("table_type") == "code_sample":
                for row_index, row in enumerate(rows, 1):
                    for col_index, cell in enumerate(row, 1):
                        if cell.get("cell_role") != "code":
                            issues.append(
                                {
                                    "block": index,
                                    "type": "code_sample_cell_role_not_code",
                                    "row": row_index,
                                    "col": col_index,
                                }
                            )
    return issues
