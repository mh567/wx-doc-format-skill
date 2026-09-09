"""Consume a native SourceProjection through the transitional legacy model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SourceProjectionApplication:
    model: dict[str, Any]
    evidence: dict[str, int]


def _legacy_location(block: Mapping[str, Any]) -> tuple[str, int] | None:
    source = block.get("source") if isinstance(block.get("source"), Mapping) else {}
    for key in ("source_position", "line_start"):
        value = source.get(key)
        if isinstance(value, int) and value >= 0:
            return key, value
    return None


def _projection_location(item: Mapping[str, Any]) -> tuple[str, int] | None:
    location = item.get("location")
    if not isinstance(location, Mapping):
        return None
    kind = location.get("kind")
    value = location.get("value")
    if kind in {"source_position", "line_start"} and isinstance(value, int) and value >= 0:
        return str(kind), value
    return None


def _replace_cell_text(cell: dict[str, Any], text: str, inline_runs: list[Mapping[str, Any]] | None = None) -> None:
    cell["text"] = text
    cell["inline_runs"] = copy.deepcopy(list(inline_runs)) if inline_runs is not None else [{"text": text}]


def apply_source_projection(
    model: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> SourceProjectionApplication:
    """Make native source content authoritative while retaining legacy semantic roles."""

    result = copy.deepcopy(dict(model))
    blocks = list((result.get("document") or {}).get("blocks") or [])
    paragraphs = list(projection.get("paragraphs") or [])
    tables = list(projection.get("tables") or [])
    paragraph_by_location: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for paragraph in paragraphs:
        location = _projection_location(paragraph)
        if location is not None:
            paragraph_by_location.setdefault(location, []).append(paragraph)

    table_blocks = [block for block in blocks if isinstance(block, dict) and block.get("block_type") == "table"]
    evidence = {
        "paragraph_candidate_count": len(paragraphs),
        "paragraph_consumed_count": 0,
        "table_candidate_count": len(tables),
        "table_consumed_count": 0,
        "formula_retyped_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    for block in blocks:
        if not isinstance(block, dict) or block.get("block_type") in {"table", "image", "separator", "unknown"}:
            continue
        location = _legacy_location(block)
        matches = paragraph_by_location.get(location, []) if location is not None else []
        if not matches:
            evidence["unmatched_count"] += 1
            continue
        if len(matches) != 1:
            evidence["ambiguous_count"] += 1
            continue
        paragraph = matches[0]
        text = str(paragraph.get("text") or "")
        appendix = paragraph.get("appendix")
        if block.get("block_type") == "appendix" and isinstance(appendix, Mapping):
            block["title"] = str(appendix.get("title") or text)
            block["appendix_id"] = appendix.get("appendix_id")
            if appendix.get("classification"):
                block["classification"] = appendix["classification"]
        else:
            block["text"] = text
            block["inline_runs"] = copy.deepcopy(list(paragraph.get("inline_runs") or [{"text": text}]))
        if paragraph.get("semantic_kind") == "formula_number" and block.get("block_type") == "list_item":
            block["block_type"] = "body"
            block["role"] = "formula"
            block.setdefault("source", {})["role"] = "formula"
            block["source"]["protected"] = True
            block["source"]["protected_reason"] = "formula_number"
            block.pop("level", None)
            block.pop("list_type", None)
            block.pop("restart", None)
            evidence["formula_retyped_count"] += 1
        evidence["paragraph_consumed_count"] += 1

    for index, table in enumerate(tables):
        if index >= len(table_blocks):
            evidence["unmatched_count"] += 1
            continue
        block = table_blocks[index]
        projected_rows = list(table.get("rows") or [])
        projected_inline_rows = list(table.get("inline_rows") or [])
        legacy_rows = list(block.get("rows") or [])
        if len(projected_rows) != len(legacy_rows) or any(
            len(projected_row) != len(legacy_row)
            for projected_row, legacy_row in zip(projected_rows, legacy_rows)
        ):
            evidence["ambiguous_count"] += 1
            continue
        for row_index, (projected_row, legacy_row) in enumerate(zip(projected_rows, legacy_rows)):
            inline_row = projected_inline_rows[row_index] if row_index < len(projected_inline_rows) else []
            for cell_index, (text, cell) in enumerate(zip(projected_row, legacy_row)):
                if isinstance(cell, dict):
                    inline_runs = inline_row[cell_index] if cell_index < len(inline_row) else None
                    _replace_cell_text(cell, str(text), inline_runs)
        evidence["table_consumed_count"] += 1

    if len(table_blocks) > len(tables):
        evidence["unmatched_count"] += len(table_blocks) - len(tables)
    return SourceProjectionApplication(model=result, evidence=evidence)


__all__ = ["SourceProjectionApplication", "apply_source_projection"]
