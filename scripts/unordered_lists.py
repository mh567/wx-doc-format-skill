"""Unordered-list evidence discovery and WX hierarchy normalization."""

from __future__ import annotations

from collections import Counter
import re
import unicodedata
from typing import Any


UNORDERED_LIST_TYPES = frozenset({"dash", "bullet_dot"})

_KNOWN_MARKERS = frozenset({
    "-", "–", "—", "－", "•", "·", "●", "○",
    "■", "□", "▪", "▫", "◆", "◇", "▶", "▷",
    "►", "▻", "※", "★", "☆", "✓", "✔",
})
_EXCLUDED_LEADING = frozenset({
    "#", "*", "_", "~", "`", "@", "$", "%", "^", "&", "+", "=",
    "|", "\\", "/", ":", ";", ",", ".", "?", "!", "，", "。", "：",
    "；", "？", "！", "、", "“", "”", "‘", "’", "《", "》",
    "【", "】", "(", ")", "[", "]", "{", "}", "<", ">",
})
_ORDERED_PREFIX = re.compile(
    r"^(?:[A-Za-z]\)|\d+[.\uff0e]{1,2}(?![.\uff0e\d])|\d+\)|（\d+）|\(\d+\))"
)


def _twips(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value.twips)
    except (AttributeError, TypeError, ValueError):
        return None


def paragraph_layout_evidence(paragraph: Any) -> dict[str, Any]:
    """Return stable paragraph indentation and page-boundary evidence."""
    direct = paragraph.paragraph_format
    style_format = getattr(getattr(paragraph, "style", None), "paragraph_format", None)

    def resolved(name: str) -> int | None:
        direct_value = _twips(getattr(direct, name, None))
        if direct_value is not None:
            return direct_value
        return _twips(getattr(style_format, name, None)) if style_format is not None else None

    result = {
        "left_twips": resolved("left_indent") or 0,
        "right_twips": resolved("right_indent") or 0,
        "first_line_twips": resolved("first_line_indent") or 0,
    }
    try:
        from docx.oxml.ns import qn

        p_pr = paragraph._p.find(qn("w:pPr"))
        page_break_before = p_pr.find(qn("w:pageBreakBefore")) if p_pr is not None else None
        if page_break_before is not None:
            value = (page_break_before.get(qn("w:val")) or "true").casefold()
            result["page_break_before"] = value not in {"0", "false", "off", "no"}
        result["contains_page_break"] = any(
            element.get(qn("w:type")) == "page"
            for element in paragraph._p.iter(qn("w:br"))
        )
    except Exception:
        pass
    return {key: value for key, value in result.items() if value is not None}


def leading_unordered_marker_candidate(text: str) -> dict[str, Any] | None:
    """Describe a plausible leading unordered marker without enumerating it.

    The detector is deliberately broad because it only creates an LLM review
    candidate.  Deterministic conversion still requires a known marker,
    native Word numbering, or a named list style.
    """
    stripped = str(text or "").lstrip()
    if len(stripped) < 2 or _ORDERED_PREFIX.match(stripped):
        return None

    first = stripped[0]
    if first.isalnum() or "\u4e00" <= first <= "\u9fff" or first in _EXCLUDED_LEADING:
        return None
    category = unicodedata.category(first)
    if not category.startswith(("P", "S")) and category != "Co":
        return None

    marker_length = 1
    while marker_length < min(3, len(stripped)) and stripped[marker_length] == first:
        marker_length += 1
    marker = stripped[:marker_length]
    remainder = stripped[marker_length:]
    has_separator = bool(remainder[:1].isspace())
    content = remainder.lstrip()
    if not content or not any(char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in content):
        return None
    if first in {"-", "–", "—", "－"}:
        if marker_length >= 3 or (content[:1].isdigit() and not has_separator):
            return None
    if (
        first not in _KNOWN_MARKERS
        and not has_separator
        and category != "So"
    ):
        return None

    return {
        "marker": marker,
        "unicode_name": unicodedata.name(first, "UNKNOWN"),
        "unicode_category": category,
        "content_text": content,
        "known_marker": first in _KNOWN_MARKERS,
        "has_separator": has_separator,
    }


def annotate_unordered_candidates(model: dict, parse_report: dict) -> None:
    """Attach bounded unknown-marker evidence for the list_detect capability."""
    blocks = model.get("document", {}).get("blocks", [])
    section_candidates: list[tuple[dict, dict]] = []

    def flush() -> None:
        if not section_candidates:
            return
        counts = Counter(item[1]["marker"] for item in section_candidates)
        for block, candidate in section_candidates:
            evidence = dict(candidate)
            evidence["section_marker_count"] = counts[candidate["marker"]]
            evidence["layout"] = dict(block.get("source", {}).get("layout", {}))
            block.setdefault("source", {})["unordered_candidate"] = evidence
            parse_report.setdefault("ambiguous_unordered_paragraphs", []).append({
                "block_id": block.get("id"),
                "text": block.get("text", ""),
                **evidence,
            })
        section_candidates.clear()

    for block in blocks:
        if block.get("block_type") == "heading":
            flush()
            continue
        if block.get("block_type") != "body":
            continue
        candidate = leading_unordered_marker_candidate(str(block.get("text") or ""))
        if candidate is not None:
            section_candidates.append((block, candidate))
    flush()


def strip_source_unordered_marker(block: dict) -> str:
    """Remove an LLM-reviewed source marker using recorded parse evidence."""
    text = str(block.get("text") or "")
    candidate = block.get("source", {}).get("unordered_candidate", {})
    marker = str(candidate.get("marker") or "")
    if marker and text.lstrip().startswith(marker):
        return text.lstrip()[len(marker):].lstrip()
    return text


def _layout_level_map(run: list[dict]) -> dict[int, int]:
    left_values = []
    for block in run:
        value = block.get("source", {}).get("layout", {}).get("left_twips")
        if isinstance(value, int):
            left_values.append(value)
    if not left_values:
        return {}

    clusters: list[int] = []
    for value in sorted(set(left_values)):
        if not clusters or value - clusters[-1] >= 120:
            clusters.append(value)
    return {value: min(index, 8) for index, value in enumerate(clusters)}


def normalize_unordered_hierarchy(model: dict, repairs: list[dict]) -> None:
    """Normalize unordered levels per section while leaving ordered levels intact."""
    blocks = model.get("document", {}).get("blocks", [])
    run: list[dict] = []

    def flush() -> None:
        if not run:
            return
        unordered = [b for b in run if b.get("list_type") in UNORDERED_LIST_TYPES]
        if not unordered:
            run.clear()
            return

        layout_levels = _layout_level_map(run)
        proposed: dict[int, int] = {}
        for block in unordered:
            source = block.get("source", {})
            numbering = source.get("numbering", {})
            left = source.get("layout", {}).get("left_twips")
            if numbering.get("status") in {"detected", "ambiguous"}:
                level = int(numbering.get("ilvl") or 0)
            elif isinstance(left, int) and left in layout_levels:
                level = layout_levels[left]
            else:
                level = int(block.get("level") or 0)
            proposed[id(block)] = max(0, level)

        if len(unordered) == len(run):
            baseline = min(proposed.values(), default=0)
        else:
            baseline = 0

        previous_level: int | None = None
        active_unordered_signatures: set[tuple[int, str]] = set()
        for block in run:
            if block.get("list_type") not in UNORDERED_LIST_TYPES:
                previous_level = int(block.get("level") or 0)
                continue
            old_level = int(block.get("level") or 0)
            level = max(0, proposed[id(block)] - baseline)
            if previous_level is None and level > 0:
                level = 0
            elif previous_level is not None and level > previous_level + 1:
                level = previous_level + 1
            block["level"] = level
            target_type = "dash" if level == 0 else "bullet_dot"
            block["list_type"] = target_type
            signature = (level, target_type)
            block["restart"] = signature not in active_unordered_signatures
            active_unordered_signatures.add(signature)
            clean_text = strip_source_unordered_marker(block)
            if clean_text != block.get("text"):
                block["text"] = clean_text
                repairs.append({
                    "block_id": block.get("id"),
                    "type": "unordered_source_marker_removed",
                })
            if old_level != level:
                repairs.append({
                    "block_id": block.get("id"),
                    "type": "unordered_level_normalized",
                    "from": old_level,
                    "to": level,
                })
            previous_level = level
        run.clear()

    for block in blocks:
        if block.get("block_type") == "heading":
            flush()
        elif block.get("block_type") == "list_item":
            run.append(block)
        else:
            flush()
    flush()
