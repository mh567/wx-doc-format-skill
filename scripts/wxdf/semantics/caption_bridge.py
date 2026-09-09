"""Apply native caption decisions to the transitional legacy model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CaptionSemanticsApplication:
    model: dict[str, Any]
    evidence: dict[str, int]


def _location(block: Mapping[str, Any]) -> tuple[str, int] | None:
    source = block.get("source") if isinstance(block.get("source"), Mapping) else {}
    for key in ("source_position", "line_start"):
        value = source.get(key)
        if isinstance(value, int) and value >= 0:
            return key, value
    return None


def _semantic_location(item: Mapping[str, Any], key: str) -> tuple[str, int] | None:
    location = item.get(key)
    if not isinstance(location, Mapping):
        return None
    kind = location.get("kind")
    value = location.get("value")
    if kind in {"source_position", "line_start"} and isinstance(value, int) and value >= 0:
        return str(kind), value
    return None


def apply_caption_semantics(
    model: Mapping[str, Any],
    semantics: Mapping[str, Any],
) -> CaptionSemanticsApplication:
    result = copy.deepcopy(dict(model))
    blocks = list((result.get("document") or {}).get("blocks") or [])
    by_location: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for block in blocks:
        if isinstance(block, dict):
            location = _location(block)
            if location is not None:
                by_location.setdefault(location, []).append(block)
    candidates = list(semantics.get("captions") or [])
    evidence = {
        "candidate_count": len(candidates),
        "consumed_count": 0,
        "associated_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    for candidate in candidates:
        matches = [block for block in by_location.get(_semantic_location(candidate, "location"), []) if block.get("block_type") == "caption"]
        if not matches:
            evidence["unmatched_count"] += 1
            continue
        if len(matches) != 1:
            evidence["ambiguous_count"] += 1
            continue
        caption = matches[0]
        caption["caption_type"] = str(candidate.get("caption_type") or "unknown")
        caption["text"] = str(candidate.get("text") or "")
        numbering = caption.setdefault("numbering", {"mode": "auto"})
        numbering["label"] = candidate.get("label")
        numbering["raw_number"] = candidate.get("raw_number")
        decision = copy.deepcopy(dict(candidate))
        target_matches = by_location.get(_semantic_location(candidate, "target_location"), [])
        compatible = [
            block for block in target_matches
            if (candidate.get("caption_type") == "table" and block.get("block_type") == "table")
            or (candidate.get("caption_type") == "figure" and (block.get("block_type") == "image" or block.get("source", {}).get("had_mixed_graphic")))
        ]
        if candidate.get("status") == "associated" and len(compatible) == 1:
            decision["target_block_id"] = compatible[0].get("id")
            evidence["associated_count"] += 1
        elif candidate.get("status") == "associated" and len(compatible) > 1:
            evidence["ambiguous_count"] += 1
        elif candidate.get("status") == "associated":
            evidence["unmatched_count"] += 1
        caption["native_caption_decision"] = decision
        evidence["consumed_count"] += 1
    return CaptionSemanticsApplication(model=result, evidence=evidence)


__all__ = ["CaptionSemanticsApplication", "apply_caption_semantics"]
