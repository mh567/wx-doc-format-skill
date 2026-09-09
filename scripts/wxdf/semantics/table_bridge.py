"""Apply native table semantics to the transitional legacy model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TableSemanticsApplication:
    model: dict[str, Any]
    evidence: dict[str, int]


def apply_table_semantics(
    model: Mapping[str, Any],
    semantics: list[Mapping[str, Any]],
) -> TableSemanticsApplication:
    """Apply table type, header roles and layout contracts by source position."""

    result = copy.deepcopy(dict(model))
    table_blocks = [
        block
        for block in (result.get("document") or {}).get("blocks") or []
        if isinstance(block, dict) and block.get("block_type") == "table"
    ]
    by_position: dict[int, list[dict[str, Any]]] = {}
    for block in table_blocks:
        position = (block.get("source") or {}).get("source_position")
        if isinstance(position, int):
            by_position.setdefault(position, []).append(block)
    evidence = {
        "candidate_count": len(semantics),
        "consumed_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    for index, semantic in enumerate(semantics):
        position = semantic.get("source_position")
        matches = by_position.get(position, []) if isinstance(position, int) else []
        if not matches and index < len(table_blocks):
            matches = [table_blocks[index]]
        if not matches:
            evidence["unmatched_count"] += 1
            continue
        if len(matches) != 1:
            evidence["ambiguous_count"] += 1
            continue
        block = matches[0]
        table_type = str(semantic.get("table_type") or "unknown")
        header_rows = max(0, int(semantic.get("header_rows") or 0))
        block["table_type"] = table_type
        block["header_rows"] = header_rows
        block["native_table_decision"] = copy.deepcopy(dict(semantic))
        block.setdefault("source", {})["table_semantics"] = {
            key: copy.deepcopy(semantic.get(key))
            for key in ("table_type", "header_rows", "caption_eligible", "confidence", "evidence")
        }
        layout = copy.deepcopy(dict(semantic.get("layout_contract") or {}))
        if layout:
            block["layout"] = layout
        rows = list(block.get("rows") or [])
        for row_index, row in enumerate(rows):
            role = "header" if table_type == "data" and row_index < header_rows else "code" if table_type == "code_sample" else "body"
            for cell in row:
                if isinstance(cell, dict):
                    cell["cell_role"] = role
        if table_type == "layout":
            block.pop("autofit", None)
            block.pop("row_height_rule", None)
        else:
            block["autofit"] = True
            block["row_height_rule"] = "atLeast"
        evidence["consumed_count"] += 1
    return TableSemanticsApplication(model=result, evidence=evidence)


__all__ = ["TableSemanticsApplication", "apply_table_semantics"]
