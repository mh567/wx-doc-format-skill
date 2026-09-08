"""Appendix scope and relative heading semantics for AST 2.0."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..model import inline_text_projection
from ..validation import validate_ast


_TITLE_RE = re.compile(r"^附\s*录(?:\s+([A-Z]))?(?:[：:]\s*)?(.*)$", re.IGNORECASE)
_VISIBLE_RE = re.compile(r"^([A-Z])(?:[.．]\d+){1,3}\s*(.*)$", re.IGNORECASE)
_MAIN_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)+\s+")


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


__all__ = ["annotate_appendix_ranges", "collect_appendix_candidates"]
