"""Caption associations, placement and validated content overlays."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from ..validation import validate_ast


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reading_order(ast: Mapping[str, Any]) -> list[str]:
    document = ast["document"]
    result: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            result.append(child_id)
            walk(child_id)

    walk(document["root_id"])
    return result


def _target_digest(ast: Mapping[str, Any], target_id: str) -> str:
    block = ast["document"]["blocks"].get(target_id)
    if block is None:
        raise ValueError(f"unknown caption target {target_id}")
    return _digest({"node_id": target_id, "block": block})


def _association_digest(ast: Mapping[str, Any], target_id: str) -> str:
    associations = [
        association
        for association in ast["document"].get("associations", [])
        if association.get("kind") == "caption_target" and association.get("target_id") == target_id
    ]
    return _digest(sorted(associations, key=lambda item: (str(item.get("source_id")), str(item.get("target_id")))))


def build_caption_overlay(ast: Mapping[str, Any], target_id: str, text: str, *, capability_version: str = "caption_gen@1.0") -> dict[str, Any]:
    """Build a validated overlay without changing the Source AST."""

    validate_ast(ast)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("caption text must be non-empty")
    target = ast["document"]["blocks"].get(target_id)
    if not target or target.get("block_type") not in {"table", "image"}:
        raise ValueError("caption target must be a table or image")
    return {
        "schema_version": "1.0",
        "kind": "caption_text",
        "target_node_id": target_id,
        "text": text,
        "target_content_sha256": _target_digest(ast, target_id),
        "association_sha256": _association_digest(ast, target_id),
        "association_revision": ast["revision"],
        "capability_version": capability_version,
        "validated": True,
    }


def invalidate_overlay(overlay: Mapping[str, Any], ast: Mapping[str, Any]) -> bool:
    """Return whether target content or association revision invalidates overlay."""

    target_id = overlay.get("target_node_id")
    try:
        digest_changed = overlay.get("target_content_sha256") != _target_digest(ast, str(target_id))
    except ValueError:
        return True
    association_changed = overlay.get("association_sha256") not in {None, _association_digest(ast, str(target_id))}
    return bool(digest_changed or association_changed)


def audit_caption_placement(ast: Mapping[str, Any]) -> dict[str, Any]:
    validate_ast(ast)
    document = ast["document"]
    order = _reading_order(ast)
    indexes = {node_id: index for index, node_id in enumerate(order)}
    associations: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for caption_id, caption in document["blocks"].items():
        if caption.get("role") != "caption":
            continue
        target_id = str(caption.get("target_id") or "")
        target = document["blocks"].get(target_id)
        if target is None:
            issues.append({"code": "caption_target_missing", "caption_id": caption_id})
            continue
        expected = "before" if target.get("block_type") == "table" else "after"
        actual = "before" if indexes.get(caption_id, -1) < indexes.get(target_id, -1) else "after"
        if actual != expected:
            issues.append({"code": "caption_placement_violation", "caption_id": caption_id, "target_id": target_id, "expected": expected, "actual": actual})
        associations.append({"caption_id": caption_id, "target_id": target_id, "caption_type": caption.get("caption_type"), "placement": actual})
    return {"passed": not issues, "issues": issues, "associations": associations}


def collect_caption_candidates(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_ast(ast)
    result: list[dict[str, Any]] = []
    for node_id, block in ast["document"]["blocks"].items():
        if block.get("block_type") == "table" and block.get("table_type") == "data":
            result.append({"candidate_id": f"caption-target-{node_id}", "module_id": "caption_semantics", "module_version": "1.0", "target_source_refs": [copy.deepcopy(dict(block["source_ref"]))] if isinstance(block.get("source_ref"), Mapping) else [], "target_node_ids": [node_id], "proposal": {"operation": "create_overlay", "property_scope": ["overlay"], "value": {"caption_type": "table", "eligible": True}}, "positive_evidence": [{"kind": "format_contract", "source": "data_table_caption_eligibility"}], "negative_evidence": [], "scope": {"stage": "relation"}, "constraints": [{"kind": "caption_above_table"}], "rationale": "data table is caption eligible", "confidence": None})
        elif block.get("block_type") == "image":
            result.append({"candidate_id": f"caption-target-{node_id}", "module_id": "caption_semantics", "module_version": "1.0", "target_source_refs": [copy.deepcopy(dict(block["source_ref"]))] if isinstance(block.get("source_ref"), Mapping) else [], "target_node_ids": [node_id], "proposal": {"operation": "create_overlay", "property_scope": ["overlay"], "value": {"caption_type": "figure", "eligible": True}}, "positive_evidence": [{"kind": "format_contract", "source": "image_caption_eligibility"}], "negative_evidence": [], "scope": {"stage": "relation"}, "constraints": [{"kind": "caption_below_figure"}], "rationale": "image is figure caption eligible", "confidence": None})
    return result


__all__ = ["audit_caption_placement", "build_caption_overlay", "collect_caption_candidates", "invalidate_overlay"]
