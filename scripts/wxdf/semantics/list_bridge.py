"""Apply native list semantics to the transitional legacy model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ListSemanticsApplication:
    model: dict[str, Any]
    evidence: dict[str, int]


def _location(source: Mapping[str, Any]) -> tuple[str, int] | None:
    for key in ("source_position", "line_start"):
        value = source.get(key)
        if isinstance(value, int) and value >= 0:
            return key, value
    return None


def _first_unit(node_id: str, facts: Mapping[str, Any]) -> str | None:
    pending = [node_id]
    visited: set[str] = set()
    while pending:
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current in (facts.get("units") or {}):
            return current
        pending[0:0] = [str(child) for child in (facts.get("children") or {}).get(current, [])]
    return None


def _fact_location(node_id: str, facts: Mapping[str, Any]) -> tuple[str, int] | None:
    target_id = node_id if node_id in (facts.get("units") or {}) else _first_unit(node_id, facts)
    if target_id is None:
        return None
    unit = (facts.get("units") or {}).get(target_id) or {}
    evidence = (facts.get("evidence") or {}).get(target_id) or {}
    source_ref = unit.get("source_ref") if isinstance(unit, Mapping) else {}
    return _location(evidence) or _location(source_ref if isinstance(source_ref, Mapping) else {})


def _depth(item_id: str, by_id: Mapping[str, Mapping[str, Any]]) -> int:
    depth = 0
    current = by_id.get(item_id) or {}
    visited = {item_id}
    while current.get("parent_item_id"):
        parent_id = str(current["parent_item_id"])
        if parent_id in visited:
            break
        visited.add(parent_id)
        depth += 1
        current = by_id.get(parent_id) or {}
    return depth


def apply_list_semantics(
    model: Mapping[str, Any],
    facts: Mapping[str, Any],
    semantics: Mapping[str, Any],
) -> ListSemanticsApplication:
    """Apply list items and continuations with the WX list policy."""

    result = copy.deepcopy(dict(model))
    blocks = list((result.get("document") or {}).get("blocks") or [])
    blocks_by_location: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for block in blocks:
        if isinstance(block, dict):
            key = _location(block.get("source") or {})
            if key is not None:
                blocks_by_location.setdefault(key, []).append(block)

    relations = list(semantics.get("items") or [])
    relation_by_id = {str(item.get("item_id")): item for item in relations}
    block_relations: dict[int, Mapping[str, Any]] = {}
    evidence = {
        "item_candidate_count": len(relations),
        "item_consumed_count": 0,
        "continuation_candidate_count": len(semantics.get("continuations") or []),
        "continuation_consumed_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    for relation in relations:
        key = _fact_location(str(relation.get("item_id") or ""), facts)
        matches = blocks_by_location.get(key, []) if key is not None else []
        if not matches:
            evidence["unmatched_count"] += 1
            continue
        if len(matches) != 1:
            evidence["ambiguous_count"] += 1
            continue
        block_relations[id(matches[0])] = relation

    chapter_needs_restart = True
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("block_type") == "heading":
            chapter_needs_restart = True
            continue
        relation = block_relations.get(id(block))
        if relation is None:
            continue
        family = str(relation.get("list_kind") or "ordered")
        source_depth = _depth(str(relation.get("item_id") or ""), relation_by_id)
        level = 0 if family == "ordered" else min(source_depth, 1)
        list_type = "lower_letter_paren" if family == "ordered" else "dash" if level == 0 else "bullet_dot"
        restart = chapter_needs_restart if family == "ordered" else bool(relation.get("source_ordinal") in {None, 1})
        block["block_type"] = "list_item"
        block.pop("role", None)
        block.pop("numbering", None)
        block["level"] = level
        block["list_type"] = list_type
        block["restart"] = restart
        block["native_list_decision"] = {
            "item_id": relation.get("item_id"),
            "list_id": relation.get("list_id"),
            "list_kind": family,
            "level": level,
            "list_type": list_type,
            "restart": restart,
            "source_depth": source_depth,
        }
        if family == "ordered":
            chapter_needs_restart = False
        evidence["item_consumed_count"] += 1

    for continuation in semantics.get("continuations") or []:
        key = _fact_location(str(continuation.get("node_id") or ""), facts)
        matches = blocks_by_location.get(key, []) if key is not None else []
        if not matches:
            evidence["unmatched_count"] += 1
            continue
        if len(matches) != 1:
            evidence["ambiguous_count"] += 1
            continue
        matches[0].setdefault("source", {})["list_continuation"] = {
            "owner_item_id": continuation.get("owner_item_id"),
            "list_id": continuation.get("list_id"),
        }
        evidence["continuation_consumed_count"] += 1
    return ListSemanticsApplication(model=result, evidence=evidence)


__all__ = ["ListSemanticsApplication", "apply_list_semantics"]
