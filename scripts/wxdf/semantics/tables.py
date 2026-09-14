"""Table semantic ownership for AST 2.0."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from ..contracts import TABLE_TYPES, overlay_property_scope
from ..validation import validate_ast, validate_source_facts


def table_caption_eligible(table_type: str | None) -> bool:
    return str(table_type or "unknown") == "data"


def capture_docx_table_layout(table: Any) -> dict[str, Any]:
    """Capture source layout facts needed to preserve layout tables."""

    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    result: dict[str, Any] = {}
    width = tbl_pr.find(qn("w:tblW")) if tbl_pr is not None else None
    if width is not None:
        result["source_width"] = {
            "value": width.get(qn("w:w")),
            "type": width.get(qn("w:type")),
        }
    margins = tbl_pr.find(qn("w:tblCellMar")) if tbl_pr is not None else None
    if margins is not None:
        result["source_margins"] = {
            name: {
                "value": child.get(qn("w:w")),
                "type": child.get(qn("w:type")),
            }
            for name in ("top", "left", "bottom", "right")
            for child in [margins.find(qn(f"w:{name}"))]
            if child is not None
        }
    row_heights = []
    for row in table.rows:
        tr_pr = row._tr.find(qn("w:trPr"))
        height = tr_pr.find(qn("w:trHeight")) if tr_pr is not None else None
        row_heights.append({
            "value": height.get(qn("w:val")) if height is not None else None,
            "rule": height.get(qn("w:hRule")) if height is not None else None,
        })
    if any(item["value"] is not None or item["rule"] is not None for item in row_heights):
        result["row_heights"] = row_heights
    borders = tbl_pr.find(qn("w:tblBorders")) if tbl_pr is not None else None
    if borders is not None:
        result["borders"] = {
            name: {
                "val": edge.get(qn("w:val")),
                "sz": edge.get(qn("w:sz")),
                "space": edge.get(qn("w:space")),
                "color": edge.get(qn("w:color")),
            }
            for name in ("top", "left", "bottom", "right", "insideH", "insideV")
            for edge in [borders.find(qn(f"w:{name}"))]
            if edge is not None
        }
    if table.autofit is not None:
        result["autofit"] = bool(table.autofit)
    return result


def collect_source_table_semantics(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read table roles and layout contracts from SourceFacts in document order."""

    validate_source_facts(facts)
    result: list[dict[str, Any]] = []

    def walk(node_id: str) -> None:
        for child_id in facts["children"].get(node_id, []):
            container = facts["containers"].get(child_id, {})
            if container.get("kind") == "table":
                source = dict(container.get("table_semantics") or {})
                table_type = str(source.get("table_type") or "data")
                row_ids = facts["children"].get(child_id, [])
                inferred_headers = 1 if row_ids and all(
                    facts["containers"].get(cell_id, {}).get("cell_role") == "header"
                    for cell_id in facts["children"].get(row_ids[0], [])
                ) else 0
                header_rows = int(source.get("header_rows", inferred_headers) or 0)
                result.append({
                    "node_id": child_id,
                    "source_position": facts["evidence"].get(child_id, {}).get("source_position"),
                    "table_type": table_type,
                    "header_rows": header_rows,
                    "caption_eligible": bool(source.get("caption_eligible", table_caption_eligible(table_type))),
                    "confidence": source.get("confidence"),
                    "evidence": list(source.get("evidence") or []),
                    "layout_contract": copy.deepcopy(dict(container.get("layout") or {})),
                })
            if child_id in facts["containers"]:
                walk(child_id)

    walk("root")
    return result


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
            "proposal": {
                "operation": "update_overlay",
                "property_scope": overlay_property_scope(value),
                "value": value,
            },
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


__all__ = [
    "audit_table_semantics",
    "capture_docx_table_layout",
    "collect_source_table_semantics",
    "collect_table_candidates",
    "table_caption_eligible",
]
