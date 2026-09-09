"""Appendix scope and relative heading semantics for AST 2.0."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..model import inline_text_projection
from ..validation import validate_ast, validate_source_facts
from appendix_semantics import (
    appendix_heading_level,
    appendix_role_from_style,
    parse_appendix_marker,
    parse_appendix_title,
)


_TITLE_RE = re.compile(r"^附\s*录(?:\s+([A-Z]))?(?:[：:]\s*)?(.*)$", re.IGNORECASE)
_VISIBLE_RE = re.compile(r"^([A-Z])(?:[.．]\d+){1,3}\s*(.*)$", re.IGNORECASE)
_MAIN_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)+\s+")
_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)


def _text(block: Mapping[str, Any]) -> str:
    if "text" in block:
        return str(block.get("text") or "")
    return inline_text_projection(block.get("inline_content", []))


def collect_appendix_candidates(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_ast(ast)
    document = ast["document"]
    appendix_containers = {node_id: node for node_id, node in document["containers"].items() if node.get("kind") == "appendix"}
    result: list[dict[str, Any]] = []
    for container_id, container in appendix_containers.items():
        title_id = container.get("title_ref")
        title = document["blocks"].get(title_id, {})
        text = _text(title)
        marker = _TITLE_RE.match(text.strip())
        result.append({"candidate_id": f"appendix-{container_id}", "container_id": container_id, "title_id": title_id, "appendix_id": (marker.group(1).upper() if marker and marker.group(1) else None), "classification": container.get("source_classification", "unknown"), "confidence": 1.0, "accepted": bool(title_id in document["blocks"]), "evidence": ["appendix_container", "title_ref"], "negative_evidence": []})
    for block_id, block in document["blocks"].items():
        text = _text(block).strip()
        marker = _TITLE_RE.match(text)
        if not marker or any(item.get("title_id") == block_id for item in result):
            continue
        negative = ["nested_main_numbered_heading"] if _MAIN_NUMBER_RE.match(text) and (block.get("level") or 0) >= 2 else []
        result.append({"candidate_id": f"appendix-title-{block_id}", "container_id": None, "title_id": block_id, "appendix_id": marker.group(1).upper() if marker.group(1) else None, "classification": "unknown", "confidence": 0.7 if not negative else 0.2, "accepted": not negative, "evidence": ["explicit_appendix_title"], "negative_evidence": negative})
    return result


def _reading_order(ast: Mapping[str, Any]) -> list[str]:
    document = ast["document"]
    result: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            result.append(child_id)
            walk(child_id)

    walk(document["root_id"])
    return result


def annotate_appendix_ranges(ast: Mapping[str, Any]) -> dict[str, Any]:
    """Return a derived AST view with appendix ranges and relative levels."""

    validate_ast(ast)
    derived = copy.deepcopy(dict(ast))
    document = derived["document"]
    candidates = [item for item in collect_appendix_candidates(ast) if item.get("accepted")]
    order = _reading_order(derived)
    ranges: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates, 1):
        container_id = candidate.get("container_id")
        if container_id:
            node_ids = [node_id for node_id in order if node_id == container_id or node_id in _descendants(derived, container_id)]
        else:
            start = order.index(str(candidate["title_id"])) if candidate["title_id"] in order else 0
            node_ids = order[start:]
        appendix_id = str(candidate.get("appendix_id") or chr(64 + ordinal))
        heading_ids = [node_id for node_id in node_ids if node_id != candidate.get("title_id") and node_id in document["blocks"] and document["blocks"][node_id].get("role") == "heading"]
        source_levels = [int(document["blocks"][node_id].get("level") or 1) for node_id in heading_ids]
        base = min(source_levels) if source_levels else 1
        heading_levels = {node_id: max(1, min(3, int(document["blocks"][node_id].get("level") or base) - base + 1)) for node_id in heading_ids}
        for node_id, level in heading_levels.items():
            document["blocks"][node_id].setdefault("derived_appendix", {})["appendix_id"] = appendix_id
            document["blocks"][node_id]["derived_appendix"]["relative_level"] = level
        start_index = min((order.index(node_id) for node_id in node_ids if node_id in order), default=0)
        end_index = max((order.index(node_id) for node_id in node_ids if node_id in order), default=start_index)
        ranges.append({"appendix_id": appendix_id, "container_id": container_id, "title_id": candidate.get("title_id"), "node_ids": node_ids, "heading_levels": heading_levels, "source_number_preserved": True, "page_break_before": True, "start_order": start_index, "end_order": end_index, "negative_evidence": list(candidate.get("negative_evidence", []))})
    return {"schema_version": "1.0", "source_revision": ast["revision"], "source_ast": derived, "candidates": candidates, "ranges": ranges, "diagnostics": []}


def _descendants(ast: Mapping[str, Any], node_id: str) -> set[str]:
    children = ast["document"]["children"]
    result: set[str] = set()
    stack = list(children.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def _source_location(facts: Mapping[str, Any], node_id: str) -> dict[str, Any] | None:
    evidence = facts.get("evidence", {}).get(node_id, {})
    for key in ("source_position", "line_start"):
        value = evidence.get(key)
        if isinstance(value, int) and value >= 0:
            return {"kind": key, "value": value}
    return None


def _source_heading_level(unit: Mapping[str, Any]) -> int | None:
    style = str(unit.get("style") or "")
    appendix_level = appendix_heading_level(style)
    if appendix_level is not None:
        return appendix_level
    match = _HEADING_STYLE_RE.match(style.strip())
    if match is not None:
        return int(match.group(1))
    level = unit.get("level")
    return int(level) if isinstance(level, int) and level > 0 else None


def _visible_appendix_heading(text: str, appendix_id: str) -> tuple[int, str] | None:
    match = _VISIBLE_RE.match(text.strip())
    if match is None or match.group(1).upper() != appendix_id.upper():
        return None
    prefix = text.strip().split(maxsplit=1)[0].replace("．", ".")
    level = prefix.count(".")
    return max(1, min(3, level)), match.group(2).strip()


def collect_source_appendix_semantics(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve appendix starts, ranges and relative headings from SourceFacts."""

    validate_source_facts(facts)
    entries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for order, node_id in enumerate(facts["children"].get("root", [])):
        unit = facts["units"].get(node_id)
        container = facts["containers"].get(node_id, {})
        if unit is not None and unit.get("kind") == "paragraph":
            text = inline_text_projection(unit.get("inline_content", [])).strip()
            has_image = any(item.get("kind") == "image" for item in unit.get("inline_content", []))
            if text or has_image:
                entries.append({
                    "node_id": node_id,
                    "order": order,
                    "location": _source_location(facts, node_id),
                    "kind": "image" if has_image and not text else "paragraph",
                    "text": text,
                    "style": unit.get("style"),
                    "heading_level": _source_heading_level(unit),
                })
            direct_marker = parse_appendix_marker(text)
            numbered_match = _MAIN_NUMBER_RE.match(text)
            nested_marker = parse_appendix_marker(text[numbered_match.end():]) if numbered_match else None
            style_role = appendix_role_from_style(unit.get("style"))
            if direct_marker is None and nested_marker is None and style_role != "appendix_title":
                continue
            source_evidence = facts.get("evidence", {}).get(node_id, {})
            nested_numbering = (
                source_evidence.get("num_fmt") == "decimal"
                and str(source_evidence.get("lvl_text") or "").count("%") >= 2
                and (_source_heading_level(unit) or 0) >= 2
            )
            negative = ["nested_main_numbered_heading"] if nested_marker is not None or nested_numbering else []
            marker = direct_marker or nested_marker
            accepted = not negative
            candidates.append({
                "candidate_id": f"appendix-source-{node_id}",
                "node_id": node_id,
                "location": _source_location(facts, node_id),
                "order": order,
                "appendix_id": (marker or {}).get("appendix_id"),
                "accepted": accepted,
                "confidence": 1.0 if style_role == "appendix_title" or direct_marker is not None else 0.2,
                "evidence": ["dedicated_appendix_semantics"] if style_role == "appendix_title" else ["explicit_appendix_title"],
                "negative_evidence": negative,
                "raw_text": text,
            })
        elif container.get("kind") == "table":
            entries.append({
                "node_id": node_id,
                "order": order,
                "location": _source_location(facts, node_id),
                "kind": "table",
                "text": "",
                "style": None,
                "heading_level": None,
            })

    accepted = [item for item in candidates if item["accepted"]]
    ranges: list[dict[str, Any]] = []
    for index, candidate in enumerate(accepted):
        end_order = accepted[index + 1]["order"] - 1 if index + 1 < len(accepted) else max((entry["order"] for entry in entries), default=candidate["order"])
        scoped = [entry for entry in entries if candidate["order"] <= entry["order"] <= end_order]
        title_data = parse_appendix_title(candidate["raw_text"], index + 1)
        if candidate.get("appendix_id"):
            title_data["appendix_id"] = candidate["appendix_id"]
        appendix_id = str(title_data["appendix_id"])
        heading_entries = [
            entry for entry in scoped
            if entry["node_id"] != candidate["node_id"] and entry.get("heading_level") is not None
        ]
        generic_levels = [int(entry["heading_level"]) for entry in heading_entries]
        base_level = min(generic_levels) if generic_levels else None
        heading_decisions: list[dict[str, Any]] = []
        for entry in heading_entries:
            visible = _visible_appendix_heading(str(entry["text"]), appendix_id)
            if visible is not None:
                relative_level, heading_text = visible
                level_source = "visible_appendix_number"
            elif base_level is not None:
                relative_level = max(1, min(3, int(entry["heading_level"]) - base_level + 1))
                heading_text = str(entry["text"])
                level_source = "relative_heading_hierarchy"
            else:
                continue
            heading_decisions.append({
                "node_id": entry["node_id"],
                "location": copy.deepcopy(entry["location"]),
                "relative_level": relative_level,
                "text": heading_text,
                "level_source": level_source,
                "source_heading_level": entry["heading_level"],
            })
        end_entry = scoped[-1] if scoped else next(entry for entry in entries if entry["node_id"] == candidate["node_id"])
        ranges.append({
            "appendix_id": appendix_id,
            "classification": title_data.get("classification"),
            "title": title_data.get("title", ""),
            "title_lines": list(title_data.get("title_lines") or []),
            "soft_break_count": int(title_data.get("soft_break_count") or 0),
            "title_location": copy.deepcopy(candidate["location"]),
            "start_location": copy.deepcopy(candidate["location"]),
            "end_location": copy.deepcopy(end_entry["location"]),
            "page_break_before": True,
            "confidence": candidate["confidence"],
            "evidence": list(candidate["evidence"]),
            "negative_evidence": list(candidate["negative_evidence"]),
            "heading_decisions": heading_decisions,
        })
    for candidate in candidates:
        candidate.pop("order", None)
        candidate.pop("raw_text", None)
    return {"candidates": candidates, "ranges": ranges, "issues": []}


__all__ = ["annotate_appendix_ranges", "collect_appendix_candidates", "collect_source_appendix_semantics"]
