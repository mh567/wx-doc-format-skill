"""Apply native structure decisions to the transitional legacy Source Model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


_STRUCTURE_ROLES = {"title", "heading", "body"}


@dataclass(frozen=True)
class StructureApplication:
    """A copied legacy model plus observable decision-consumption evidence."""

    model: dict[str, Any]
    evidence: dict[str, int]


def _location_key(source: Mapping[str, Any]) -> tuple[str, int] | None:
    position = source.get("source_position")
    if isinstance(position, int):
        return "source_position", position
    line_start = source.get("line_start")
    if isinstance(line_start, int):
        return "line_start", line_start
    return None


def _current_role(block: Mapping[str, Any]) -> str:
    role = block.get("role")
    if isinstance(role, str) and role:
        return role
    block_type = str(block.get("block_type") or "")
    if block_type == "heading":
        return "heading"
    if block_type == "body":
        return "body"
    return block_type or "unknown"


def _apply_value(block: dict[str, Any], value: Mapping[str, Any]) -> bool:
    role = str(value.get("role") or "")
    before = copy.deepcopy(block)
    if role == "title":
        block.update({
            "block_type": "heading",
            "role": "title",
            "level": 0,
            "numbering": {"mode": "none"},
        })
    elif role == "heading":
        block.update({
            "block_type": "heading",
            "role": "heading",
            "level": max(1, min(int(value.get("level") or 1), 6)),
            "numbering": {"mode": "auto"},
        })
    elif role == "body":
        block["block_type"] = "body"
        block.pop("role", None)
        block.pop("level", None)
        block.pop("numbering", None)
    return block != before


def apply_structure_decisions(
    model: Mapping[str, Any],
    facts: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> StructureApplication:
    """Apply unambiguous native title, heading and body decisions by source location."""

    result = copy.deepcopy(dict(model))
    blocks = list((result.get("document") or {}).get("blocks") or [])
    blocks_by_location: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        key = _location_key(block.get("source") or {})
        if key is not None:
            blocks_by_location.setdefault(key, []).append(block)

    evidence = {
        "candidate_count": len(candidates),
        "applied_count": 0,
        "unchanged_count": 0,
        "skipped_special_role_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    units = facts.get("units") or {}
    fact_evidence = facts.get("evidence") or {}
    for candidate in candidates:
        target_ids = list(candidate.get("target_node_ids") or [])
        value = ((candidate.get("proposal") or {}).get("value") or {})
        if len(target_ids) != 1 or value.get("role") not in _STRUCTURE_ROLES:
            evidence["unmatched_count"] += 1
            continue
        node_id = target_ids[0]
        unit = units.get(node_id) or {}
        location = dict(fact_evidence.get(node_id) or {})
        source_ref = unit.get("source_ref") or {}
        if "source_position" not in location and "line_start" not in location:
            location.update(source_ref)
        key = _location_key(location)
        if key is None or key not in blocks_by_location:
            evidence["unmatched_count"] += 1
            continue
        matches = blocks_by_location[key]
        if len(matches) != 1:
            evidence["ambiguous_count"] += 1
            continue
        block = matches[0]
        if _current_role(block) not in _STRUCTURE_ROLES:
            evidence["skipped_special_role_count"] += 1
            continue
        if _apply_value(block, value):
            evidence["applied_count"] += 1
        else:
            evidence["unchanged_count"] += 1
    return StructureApplication(model=result, evidence=evidence)


__all__ = ["StructureApplication", "apply_structure_decisions"]
