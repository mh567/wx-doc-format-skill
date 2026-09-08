"""Apply resolved source exclusions to the transitional legacy Source Model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RegionApplication:
    model: dict[str, Any]
    evidence: dict[str, int]


def _location(source: Mapping[str, Any]) -> tuple[str, int] | None:
    position = source.get("source_position")
    if isinstance(position, int):
        return "source_position", position
    line_start = source.get("line_start")
    if isinstance(line_start, int):
        return "line_start", line_start
    return None


def apply_region_decisions(
    model: Mapping[str, Any],
    facts: Mapping[str, Any],
    regions: Mapping[str, Any],
) -> RegionApplication:
    """Remove only legacy blocks that map unambiguously to excluded source facts."""

    result = copy.deepcopy(dict(model))
    excluded_node_ids = {
        str(node_id)
        for region in regions.get("regions", [])
        if region.get("semantic_scope") == "excluded"
        for node_id in region.get("node_ids", [])
    }
    locations: dict[tuple[str, int], int] = {}
    for node_id in excluded_node_ids:
        key = _location(facts.get("evidence", {}).get(node_id, {}))
        if key is not None:
            locations[key] = locations.get(key, 0) + 1

    blocks = list((result.get("document") or {}).get("blocks") or [])
    block_counts: dict[tuple[str, int], int] = {}
    for block in blocks:
        key = _location((block.get("source") or {}) if isinstance(block, Mapping) else {})
        if key is not None:
            block_counts[key] = block_counts.get(key, 0) + 1

    removable = {
        key
        for key, count in locations.items()
        if count == 1 and block_counts.get(key) == 1
    }
    result["document"]["blocks"] = [
        block
        for block in blocks
        if _location((block.get("source") or {}) if isinstance(block, Mapping) else {}) not in removable
    ]
    ambiguous = sum(
        1
        for key, count in locations.items()
        if count > 1 or block_counts.get(key, 0) > 1
    )
    unmatched = sum(1 for key in locations if block_counts.get(key, 0) == 0)
    return RegionApplication(
        model=result,
        evidence={
            "excluded_candidate_count": len(locations),
            "removed_count": len(removable),
            "unmatched_count": unmatched,
            "ambiguous_count": ambiguous,
        },
    )


__all__ = ["RegionApplication", "apply_region_decisions"]
