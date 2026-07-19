"""Discover family-neutral semantic list groups in a document model."""

from __future__ import annotations

from collections import Counter
import re
from statistics import mean
from typing import Any


AUTO_GROUP_THRESHOLD = 0.85
REVIEW_GROUP_THRESHOLD = 0.65

_COLON = (":", "：")
_OPEN_ENDINGS = _COLON + (",", "，", ";", "；")
_CLOSED_ENDINGS = ("。", ".", "!", "！", "?", "？")
_ORDERED_MARKER = re.compile(r"^\s*(?:[A-Za-z]\)|\d+[.)、]|[（(]\d+[）)])\s*")


def _source_position(block: dict[str, Any], fallback: int) -> int:
    value = block.get("source", {}).get("source_position")
    return int(value) if isinstance(value, int) else fallback


def _protected_body(block: dict[str, Any]) -> bool:
    if block.get("block_type") != "body":
        return True
    role = str(block.get("role") or block.get("source", {}).get("role") or "")
    return role in {"note", "numbered_note", "formula", "appendix_title", "title"}


def _label_prefix(text: str) -> str | None:
    stripped = str(text or "").strip()
    positions = [stripped.find(token) for token in _COLON if token in stripped]
    if not positions:
        return None
    position = min(value for value in positions if value >= 0)
    prefix = stripped[:position].strip()
    if not prefix or len(prefix) > 32 or any(token in prefix for token in ("。", ".", "；", ";")):
        return None
    return prefix


def _ratio(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _layout_cohesion(items: list[dict[str, Any]]) -> float:
    layouts = [item.get("source", {}).get("layout", {}) for item in items]
    if not layouts:
        return 0.0
    compatible = []
    first = layouts[0]
    for layout in layouts:
        compatible.append(all(
            not isinstance(first.get(field), int)
            or not isinstance(layout.get(field), int)
            or abs(first[field] - layout[field]) <= 120
            for field in ("left_twips", "right_twips", "first_line_twips")
        ))
    return _ratio(compatible)


def _terminal_cohesion(texts: list[str]) -> float:
    categories = []
    for text in texts:
        stripped = text.rstrip()
        if stripped.endswith(("；", ";")):
            categories.append("semicolon")
        elif stripped.endswith(("。", ".")):
            categories.append("period")
        elif stripped.endswith(("，", ",")):
            categories.append("comma")
        else:
            categories.append("other")
    counts = Counter(categories)
    return max(counts.values(), default=0) / len(categories) if categories else 0.0


def _length_cohesion(texts: list[str]) -> float:
    lengths = [max(1, len(text)) for text in texts]
    average = mean(lengths) if lengths else 0
    if average <= 0:
        return 0.0
    spread = max(lengths) - min(lengths)
    return max(0.0, 1.0 - spread / max(average * 2.0, 1.0))


def _family_decision(items: list[dict[str, Any]]) -> tuple[str, str, float, list[str]]:
    ordered_source = 0
    unordered_source = 0
    visible_unordered = 0
    visible_ordered = 0
    style_ordered = 0
    style_unordered = 0
    for item in items:
        source = item.get("source", {})
        numbering = source.get("numbering", {})
        source_type = numbering.get("source_list_type")
        numbering_status = numbering.get("status")
        if numbering_status != "ignored" and source_type in {"lower_letter_paren", "decimal_paren"}:
            ordered_source += 1
        elif numbering_status != "ignored" and source_type in {"dash", "bullet_dot"}:
            unordered_source += 1
        if source.get("unordered_candidate"):
            visible_unordered += 1
        if _ORDERED_MARKER.match(str(source.get("raw_text") or item.get("text") or "")):
            visible_ordered += 1
        style = str(source.get("style") or "")
        if "无编号" in style or style.casefold().startswith("list bullet"):
            style_unordered += 1
        elif "列项" in style or style.casefold().startswith("list number"):
            style_ordered += 1

    count = max(1, len(items))
    if ordered_source == count:
        return "ordered", "source_ordered", 0.98, ["source_ordered"]
    if unordered_source == count:
        return "unordered", "source_unordered", 0.98, ["source_unordered"]
    if visible_ordered / count >= 0.6:
        return "ordered", "visible_ordered_marker", 0.92, ["visible_ordered_marker"]
    if visible_unordered / count >= 0.6:
        return "unordered", "visible_unordered_marker", 0.92, ["visible_unordered_marker"]
    if style_ordered / count >= 0.6:
        return "ordered", "named_ordered_style", 0.85, ["named_ordered_style"]
    if style_unordered / count >= 0.6:
        return "unordered", "named_unordered_style", 0.85, ["named_unordered_style"]
    return "ordered", "semantic_default_ordered", 0.50, ["semantic_default_ordered"]


def _score_group(
    anchor: dict[str, Any],
    items: list[dict[str, Any]],
    following: dict[str, Any] | None,
) -> tuple[float, list[str], list[str]]:
    texts = [str(item.get("text") or "").strip() for item in items]
    anchor_text = str(anchor.get("text") or "").strip()
    label_ratio = _ratio([_label_prefix(text) is not None for text in texts])
    layout = _layout_cohesion(items)
    terminals = _terminal_cohesion(texts)
    lengths = _length_cohesion(texts)

    score = 0.20 + min(0.04, max(0, len(items) - 3) * 0.01)
    evidence = ["contiguous_body_group", f"item_count:{len(items)}"]
    conflicts: list[str] = []

    if anchor_text.endswith(_COLON):
        score += 0.22
        evidence.append("strong_intro_punctuation")
    elif anchor_text.endswith(_OPEN_ENDINGS) or (
        len(anchor_text) <= 90 and not anchor_text.endswith(_CLOSED_ENDINGS)
    ):
        score += 0.10
        evidence.append("soft_intro_relation")
    else:
        conflicts.append("weak_intro_relation")
    if len(anchor_text) <= 50:
        score += 0.04
        evidence.append("compact_anchor")

    score += 0.28 * label_ratio
    score += 0.12 * layout
    score += 0.08 * terminals
    score += 0.08 * lengths
    evidence.extend([
        f"label_ratio:{label_ratio:.2f}",
        f"layout_cohesion:{layout:.2f}",
        f"terminal_cohesion:{terminals:.2f}",
        f"length_cohesion:{lengths:.2f}",
    ])
    if label_ratio >= 0.6 and len(items) >= 3:
        score += 0.08
        evidence.append("majority_parallel_labels")

    marker_ratio = _ratio([
        bool(item.get("source", {}).get("unordered_candidate")) for item in items
    ])
    if marker_ratio >= 0.6:
        score += 0.18
        evidence.append(f"repeated_visible_marker:{marker_ratio:.2f}")

    if following is not None:
        following_has_label = _label_prefix(str(following.get("text") or "")) is not None
        if label_ratio >= 0.6 and following_has_label:
            score -= 0.12
            conflicts.append("possible_truncated_group")
        elif label_ratio >= 0.6 and not following_has_label:
            score += 0.03
            evidence.append("following_change_point")

    return max(0.0, min(score, 0.99)), evidence, conflicts


def _body_runs(blocks: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    runs: list[tuple[str, list[dict[str, Any]]]] = []
    current: list[dict[str, Any]] = []
    section_id = "document"
    for block in blocks:
        if block.get("block_type") == "heading":
            if current:
                runs.append((section_id, current))
                current = []
            section_id = str(block.get("id") or section_id)
        elif _protected_body(block):
            if current:
                runs.append((section_id, current))
                current = []
        else:
            current.append(block)
    if current:
        runs.append((section_id, current))
    return runs


def discover_semantic_list_groups(model: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = model.get("document", {}).get("blocks", [])
    proposals: list[dict[str, Any]] = []
    for section_id, run in _body_runs(blocks):
        if len(run) < 4:
            continue
        for start in range(1, len(run) - 2):
            anchor = run[start - 1]
            for end in range(start + 3, min(len(run), start + 12) + 1):
                items = run[start:end]
                following = run[end] if end < len(run) else None
                confidence, evidence, conflicts = _score_group(anchor, items, following)
                if confidence < REVIEW_GROUP_THRESHOLD:
                    continue
                family, family_source, family_confidence, family_evidence = _family_decision(items)
                proposals.append({
                    "section_id": section_id,
                    "anchor_block_ids": [str(anchor.get("id"))],
                    "item_block_ids": [str(item.get("id")) for item in items],
                    "group_confidence": round(confidence, 4),
                    "family": family,
                    "family_source": family_source,
                    "family_confidence": family_confidence,
                    "level": 0,
                    "level_confidence": round(min(0.90, 0.65 + 0.20 * _layout_cohesion(items)), 4),
                    "evidence": evidence + family_evidence,
                    "conflicts": conflicts,
                })

    selected: list[dict[str, Any]] = []
    occupied: set[str] = set()
    proposals.sort(key=lambda item: (
        item["group_confidence"], len(item["item_block_ids"])
    ), reverse=True)
    for proposal in proposals:
        item_ids = set(proposal["item_block_ids"])
        if item_ids & occupied:
            continue
        occupied.update(item_ids)
        selected.append(proposal)

    selected.sort(key=lambda item: min(
        _source_position(block, index)
        for index, block in enumerate(blocks)
        if block.get("id") in set(item["item_block_ids"])
    ))
    for index, group in enumerate(selected, 1):
        group["group_id"] = f"semantic_list_group_{index}"
        group["status"] = (
            "auto" if group["group_confidence"] >= AUTO_GROUP_THRESHOLD else "review"
        )
    return selected


def annotate_semantic_list_groups(model: dict[str, Any], parse_report: dict[str, Any]) -> None:
    groups = discover_semantic_list_groups(model)
    by_id = {
        str(block.get("id")): block
        for block in model.get("document", {}).get("blocks", [])
        if block.get("id")
    }
    for group in groups:
        for block_id in group["item_block_ids"]:
            block = by_id.get(block_id)
            if block is not None:
                block.setdefault("source", {})["list_group_candidate"] = dict(group)
    parse_report["semantic_list_groups"] = groups
    parse_report["parallel_content_candidates"] = len(groups)
    parse_report["unresolved_parallel_groups"] = sum(
        1 for group in groups if group["status"] == "review"
    )


def apply_semantic_list_groups(model: dict[str, Any], repairs: list[dict[str, Any]]) -> None:
    blocks = model.get("document", {}).get("blocks", [])
    groups: dict[str, dict[str, Any]] = {}
    for block in blocks:
        candidate = block.get("source", {}).get("list_group_candidate")
        if candidate and candidate.get("status") == "auto":
            groups[str(candidate.get("group_id"))] = candidate

    by_id = {str(block.get("id")): block for block in blocks if block.get("id")}
    for group in groups.values():
        list_type = "dash" if group.get("family") == "unordered" else "lower_letter_paren"
        for item_index, block_id in enumerate(group.get("item_block_ids", [])):
            block = by_id.get(str(block_id))
            if block is None or block.get("block_type") != "body":
                continue
            block["block_type"] = "list_item"
            block["level"] = int(group.get("level") or 0)
            block["list_type"] = list_type
            block["restart"] = item_index == 0
            repairs.append({
                "block_id": block_id,
                "type": "semantic_group_retyped_as_list_item",
                "group_id": group.get("group_id"),
                "family_source": group.get("family_source"),
            })
