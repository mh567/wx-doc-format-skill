"""Apply native appendix ranges to the transitional legacy model."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AppendixSemanticsApplication:
    model: dict[str, Any]
    evidence: dict[str, int]


def _location(block: Mapping[str, Any]) -> tuple[str, int] | None:
    source = block.get("source") if isinstance(block.get("source"), Mapping) else {}
    for key in ("source_position", "line_start"):
        value = source.get(key)
        if isinstance(value, int) and value >= 0:
            return key, value
    return None


def _semantic_location(value: Any) -> tuple[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    kind = value.get("kind")
    location_value = value.get("value")
    if kind in {"source_position", "line_start"} and isinstance(location_value, int) and location_value >= 0:
        return str(kind), location_value
    return None


def apply_appendix_semantics(
    model: Mapping[str, Any],
    semantics: Mapping[str, Any],
) -> AppendixSemanticsApplication:
    result = copy.deepcopy(dict(model))
    blocks = list((result.get("document") or {}).get("blocks") or [])
    by_location: dict[tuple[str, int], list[tuple[int, dict[str, Any]]]] = {}
    for index, block in enumerate(blocks):
        if isinstance(block, dict):
            location = _location(block)
            if location is not None:
                by_location.setdefault(location, []).append((index, block))
    ranges = list(semantics.get("ranges") or [])
    evidence = {
        "range_candidate_count": len(ranges),
        "range_consumed_count": 0,
        "heading_candidate_count": sum(len(item.get("heading_decisions") or []) for item in ranges),
        "heading_consumed_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    document_ranges: list[dict[str, Any]] = []
    for appendix in ranges:
        title_matches = by_location.get(_semantic_location(appendix.get("title_location")), [])
        if len(title_matches) != 1:
            evidence["ambiguous_count" if len(title_matches) > 1 else "unmatched_count"] += 1
            continue
        start_index, title = title_matches[0]
        end_matches = by_location.get(_semantic_location(appendix.get("end_location")), [])
        end_index = end_matches[-1][0] if end_matches else len(blocks) - 1
        appendix_id = str(appendix.get("appendix_id") or "A")
        title["block_type"] = "appendix"
        title["appendix_id"] = appendix_id
        title["title"] = str(appendix.get("title") or "")
        title["title_lines"] = list(appendix.get("title_lines") or [""])
        title["soft_break_count"] = int(appendix.get("soft_break_count") or 0)
        title["numbering"] = {"mode": "auto"}
        title["layout"] = {**dict(title.get("layout") or {}), "page_break_before": True}
        title["native_appendix_decision"] = copy.deepcopy(dict(appendix))
        if appendix.get("classification"):
            title["classification"] = appendix["classification"]
        else:
            title.pop("classification", None)
        for key in ("text", "role", "level", "list_type", "restart"):
            title.pop(key, None)
        evidence["range_consumed_count"] += 1

        scoped = blocks[start_index:end_index + 1]
        for block in scoped:
            block["appendix_id"] = appendix_id
            block["context"] = {**dict(block.get("context") or {}), "scope": "appendix", "appendix_id": appendix_id}
        for decision in appendix.get("heading_decisions") or []:
            matches = by_location.get(_semantic_location(decision.get("location")), [])
            if len(matches) != 1:
                evidence["ambiguous_count" if len(matches) > 1 else "unmatched_count"] += 1
                continue
            _, heading = matches[0]
            heading["block_type"] = "heading"
            heading["role"] = "appendix_heading"
            heading["level"] = int(decision.get("relative_level") or 1)
            heading["text"] = str(decision.get("text") or heading.get("text") or "")
            heading["numbering"] = {"mode": "auto"}
            heading["appendix_heading_detection"] = {
                "source": decision.get("level_source"),
                "source_heading_level": decision.get("source_heading_level"),
            }
            heading.pop("list_type", None)
            heading.pop("restart", None)
            evidence["heading_consumed_count"] += 1
        document_ranges.append({
            "appendix_id": appendix_id,
            "classification": appendix.get("classification"),
            "title": appendix.get("title", ""),
            "start_block_id": title.get("id"),
            "end_block_id": blocks[end_index].get("id") if blocks else title.get("id"),
            "start_block_index": start_index,
            "end_block_index": end_index,
            "start_source_position": (_location(title) or (None, None))[1],
            "end_source_position": (_location(blocks[end_index]) or (None, None))[1] if blocks else None,
            "block_count": len(scoped),
            "page_break_before": True,
        })
    result["document"]["appendices"] = document_ranges
    result["document"]["has_appendix"] = bool(document_ranges)
    return AppendixSemanticsApplication(model=result, evidence=evidence)


__all__ = ["AppendixSemanticsApplication", "apply_appendix_semantics"]
