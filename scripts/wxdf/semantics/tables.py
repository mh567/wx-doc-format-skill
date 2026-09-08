"""Table semantic ownership for AST 2.0."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from ..contracts import TABLE_TYPES
from ..validation import validate_ast


def table_caption_eligible(table_type: str | None) -> bool:
    return str(table_type or "unknown") == "data"


def _layout_contract(block: Mapping[str, Any]) -> dict[str, Any]:
    layout = block.get("layout") if isinstance(block.get("layout"), Mapping) else {}
    return {
        key: copy.deepcopy(layout[key])
        for key in ("source_width", "source_margins", "row_heights", "borders", "autofit")
        if key in layout
    }


def collect_table_candidates(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose table classification and layout protection as candidates."""

    validate_ast(ast)
    result: list[dict[str, Any]] = []
    for node_id, block in ast["document"]["blocks"].items():
        if block.get("block_type") != "table":
            continue
        table_type = str(block.get("table_type") or "unknown")
        if table_type not in TABLE_TYPES:
            table_type = "unknown"
        source_ref = block.get("source_ref")
        value = {
            "table_type": table_type,
            "caption_eligible": table_caption_eligible(table_type),
            "layout_contract": _layout_contract(block),
        }
        result.append({
            "candidate_id": f"table-semantics-{node_id}",
            "module_id": "table_semantics",
            "module_version": "1.0",
            "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
            "target_node_ids": [node_id],
            "proposal": {"operation": "update_overlay", "property_scope": ["overlay"], "value": value},
            "positive_evidence": [{"kind": "structural", "source": "table_semantics", "table_type": table_type}],
            "negative_evidence": [],
            "scope": {"stage": "relation"},
            "constraints": [{"kind": "preserve_layout_contract", "value": value["layout_contract"]}],
            "rationale": "table semantic type and layout facts are fixed at source analysis",
            "confidence": None,
        })
    return result


def _reading_order(ast: Mapping[str, Any]) -> list[str]:
    document = ast["document"]
    order: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            order.append(child_id)
            walk(child_id)

    walk(document["root_id"])
    return order


def audit_table_semantics(ast: Mapping[str, Any]) -> dict[str, Any]:
    """Check table roles, layout protection and table-caption placement."""

    validate_ast(ast)
    document = ast["document"]
    order = _reading_order(ast)
    index = {node_id: position for position, node_id in enumerate(order)}
    issues: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for node_id, block in document["blocks"].items():
        if block.get("block_type") != "table":
            continue
        table_type = block.get("table_type", "unknown")
        captions = [
            caption_id
            for caption_id, caption in document["blocks"].items()
            if caption.get("role") == "caption" and caption.get("target_id") == node_id
        ]
        if table_type == "data" and captions and any(index.get(caption_id, -1) > index.get(node_id, -1) for caption_id in captions):
            issues.append({"code": "table_caption_below_table", "node_id": node_id})
        if table_type == "layout" and not _layout_contract(block):
            issues.append({"code": "layout_contract_missing", "node_id": node_id})
        tables.append({"node_id": node_id, "table_type": table_type, "caption_eligible": table_caption_eligible(table_type), "caption_ids": captions, "layout_contract": _layout_contract(block)})
    return {"passed": not issues, "issues": issues, "tables": tables}


__all__ = ["audit_table_semantics", "collect_table_candidates", "table_caption_eligible"]
