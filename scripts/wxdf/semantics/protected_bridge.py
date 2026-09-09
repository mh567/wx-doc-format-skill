"""Apply native protected-content decisions to the transitional legacy model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from note_semantics import strip_source_note_marker


@dataclass(frozen=True)
class ProtectedContentApplication:
    model: dict[str, Any]
    evidence: dict[str, int]


def _location(source: Mapping[str, Any]) -> tuple[str, int] | None:
    for key in ("source_position", "line_start"):
        value = source.get(key)
        if isinstance(value, int) and value >= 0:
            return key, value
    return None


def _fact_location(facts: Mapping[str, Any], node_id: str) -> tuple[str, int] | None:
    unit = (facts.get("units") or {}).get(node_id) or (facts.get("containers") or {}).get(node_id) or {}
    evidence = (facts.get("evidence") or {}).get(node_id) or {}
    source_ref = unit.get("source_ref") if isinstance(unit, Mapping) else {}
    return _location(evidence) or _location(source_ref if isinstance(source_ref, Mapping) else {})


def _protect(block: dict[str, Any], value: Mapping[str, Any]) -> bool:
    before = copy.deepcopy(block)
    reason = str(value.get("reason") or "protected_content")
    role = str(value.get("role") or "")
    source = block.setdefault("source", {})
    source["protected"] = True
    source["protected_reason"] = reason
    if role:
        source["role"] = role
        block["role"] = role
    if reason == "api_label":
        block["block_type"] = "body"
        block.pop("role", None)
        block.pop("level", None)
        block.pop("numbering", None)
        block.pop("list_type", None)
        block.pop("restart", None)
    elif role in {"note", "numbered_note", "formula"}:
        block["block_type"] = "body"
        block.pop("level", None)
        block.pop("numbering", None)
        block.pop("list_type", None)
        block.pop("restart", None)
        if role in {"note", "numbered_note"}:
            block["text"] = strip_source_note_marker(str(block.get("text") or ""), role)
    return block != before


def apply_protected_candidates(
    model: Mapping[str, Any],
    facts: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> ProtectedContentApplication:
    """Apply unambiguous protection decisions by source location."""

    result = copy.deepcopy(dict(model))
    blocks = list((result.get("document") or {}).get("blocks") or [])
    blocks_by_location: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for block in blocks:
        if isinstance(block, dict):
            key = _location(block.get("source") or {})
            if key is not None:
                blocks_by_location.setdefault(key, []).append(block)

    evidence = {
        "candidate_count": len(candidates),
        "applied_count": 0,
        "unchanged_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    for candidate in candidates:
        target_ids = list(candidate.get("target_node_ids") or [])
        value = ((candidate.get("proposal") or {}).get("value") or {})
        if len(target_ids) != 1 or value.get("protected") is not True:
            evidence["unmatched_count"] += 1
            continue
        key = _fact_location(facts, str(target_ids[0]))
        matches = blocks_by_location.get(key, []) if key is not None else []
        if not matches:
            evidence["unmatched_count"] += 1
            continue
        if len(matches) != 1:
            evidence["ambiguous_count"] += 1
            continue
        if _protect(matches[0], value):
            evidence["applied_count"] += 1
        else:
            evidence["unchanged_count"] += 1
    return ProtectedContentApplication(model=result, evidence=evidence)


__all__ = ["ProtectedContentApplication", "apply_protected_candidates"]
