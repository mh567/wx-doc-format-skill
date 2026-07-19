"""Analyze Word numbering before semantic AST construction.

The source document stays unchanged.  Each top-level paragraph receives a
stable source position and, when applicable, an OOXML numbering descriptor.
High-confidence descriptors can be converted deterministically.  Ambiguous
descriptors remain available to the registered ``list_detect`` capability.
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from list_style_mapping import normalize_wx_list_type, source_list_type, wx_list_style_name
from text_utils import looks_like_list_item


_LIST_FORMATS = {
    "decimal", "decimalZero", "lowerLetter", "upperLetter",
    "lowerRoman", "upperRoman", "bullet",
}
_ORDERED_LIST_FORMATS = _LIST_FORMATS - {"bullet"}
_PROTECTED_STYLES = {"caption", "题注", "文档标题", "title"}


def _style_name(paragraph: Paragraph) -> str:
    try:
        return paragraph.style.name or ""
    except Exception:
        return ""


def _is_list_style(style_name: str) -> bool:
    normalized = style_name.casefold().replace(" ", "")
    return (
        normalized.startswith("listnumber")
        or normalized.startswith("listbullet")
        or normalized == "listparagraph"
        or "列项" in style_name
    )


def _is_protected_style(style_name: str) -> bool:
    normalized = style_name.casefold().strip()
    return (
        normalized.startswith(("heading", "toc"))
        or normalized in _PROTECTED_STYLES
        or any(token in normalized for token in ("目录", "题注", "注释", "公式", "附录标题"))
    )


def _paragraph_num_info(paragraph: Paragraph) -> tuple[int | None, int | None, str]:
    """Return effective ``(ilvl, num_id, source)`` for a paragraph."""
    try:
        p_pr = paragraph._p.pPr
        num_pr = p_pr.numPr if p_pr is not None else None
        if num_pr is not None and num_pr.numId is not None:
            ilvl = num_pr.ilvl.val if num_pr.ilvl is not None else 0
            return int(ilvl), int(num_pr.numId.val), "direct"
    except (AttributeError, TypeError, ValueError):
        pass

    try:
        style_p_pr = paragraph.style.element.pPr
        num_pr = style_p_pr.numPr if style_p_pr is not None else None
        if num_pr is not None and num_pr.numId is not None:
            ilvl = num_pr.ilvl.val if num_pr.ilvl is not None else 0
            return int(ilvl), int(num_pr.numId.val), "style"
    except (AttributeError, TypeError, ValueError):
        pass
    return None, None, "none"


def _level_values(level) -> dict[str, Any]:
    def value(tag: str) -> str | None:
        child = level.find(qn(tag))
        return child.get(qn("w:val")) if child is not None else None

    try:
        start = int(value("w:start") or 1)
    except (TypeError, ValueError):
        start = 1
    return {
        "num_fmt": value("w:numFmt"),
        "lvl_text": value("w:lvlText"),
        "p_style": value("w:pStyle"),
        "start": start,
    }


def _numbering_maps(
    doc,
) -> tuple[
    dict[int, int],
    dict[int, dict[int, dict[str, Any]]],
    dict[int, dict[int, dict[str, Any]]],
]:
    num_to_abstract: dict[int, int] = {}
    abstract_levels: dict[int, dict[int, dict[str, Any]]] = {}
    num_level_overrides: dict[int, dict[int, dict[str, Any]]] = {}
    try:
        root = doc.part.numbering_part.element
    except Exception:
        return num_to_abstract, abstract_levels, num_level_overrides

    for num in root.findall(qn("w:num")):
        try:
            num_id = int(num.get(qn("w:numId")))
            abstract = num.find(qn("w:abstractNumId"))
            if abstract is not None:
                num_to_abstract[num_id] = int(abstract.get(qn("w:val")))
            overrides: dict[int, dict[str, Any]] = {}
            for override in num.findall(qn("w:lvlOverride")):
                try:
                    ilvl = int(override.get(qn("w:ilvl"), "0"))
                except ValueError:
                    ilvl = 0
                values: dict[str, Any] = {}
                override_level = override.find(qn("w:lvl"))
                if override_level is not None:
                    level_values = _level_values(override_level)
                    if override_level.find(qn("w:start")) is None:
                        level_values.pop("start", None)
                    values.update({
                        key: value for key, value in level_values.items()
                        if value is not None
                    })
                start_override = override.find(qn("w:startOverride"))
                if start_override is not None:
                    try:
                        values["start"] = int(start_override.get(qn("w:val")))
                    except (TypeError, ValueError):
                        pass
                if values:
                    overrides[ilvl] = values
            if overrides:
                num_level_overrides[num_id] = overrides
        except (TypeError, ValueError):
            continue

    for abstract in root.findall(qn("w:abstractNum")):
        try:
            abstract_id = int(abstract.get(qn("w:abstractNumId")))
        except (TypeError, ValueError):
            continue
        levels: dict[int, dict[str, Any]] = {}
        for level in abstract.findall(qn("w:lvl")):
            try:
                ilvl = int(level.get(qn("w:ilvl"), "0"))
            except ValueError:
                ilvl = 0

            levels[ilvl] = _level_values(level)
        abstract_levels[abstract_id] = levels
    return num_to_abstract, abstract_levels, num_level_overrides


def _normalized_marker_template(value: str | None) -> str:
    """Normalize placeholder indexes while preserving visible punctuation."""
    return re.sub(r"%\d+", "%n", value or "")


def _physical_groups_continue(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> bool:
    """Return True when two Word numbering instances form one visible sequence.

    OOXML producers frequently create a new ``numId`` for an item that merely
    continues the displayed sequence.  Continuity therefore uses observable
    invariants instead of identifier equality alone.
    """
    if not previous or not current:
        return False
    prev_last = previous[-1]
    curr_first = current[0]
    if curr_first["source_position"] != prev_last["source_position"] + 1:
        return False
    if any(_is_protected_style(item.get("style", "")) for item in (*previous, *current)):
        return False
    if len({int(item.get("ilvl") or 0) for item in previous}) != 1:
        return False
    if len({int(item.get("ilvl") or 0) for item in current}) != 1:
        return False
    if int(previous[0].get("ilvl") or 0) != int(curr_first.get("ilvl") or 0):
        return False
    prev_format = previous[0].get("num_fmt")
    curr_format = curr_first.get("num_fmt")
    if prev_format not in _ORDERED_LIST_FORMATS or curr_format != prev_format:
        return False
    if _normalized_marker_template(previous[0].get("lvl_text")) != _normalized_marker_template(curr_first.get("lvl_text")):
        return False
    if previous[0].get("style", "").casefold().strip() != curr_first.get("style", "").casefold().strip():
        return False
    try:
        expected_start = int(previous[0].get("start") or 1) + len(previous)
        actual_start = int(curr_first.get("start") or 1)
    except (TypeError, ValueError):
        return False
    return actual_start == expected_start


def analyze_docx_lists(
    doc,
    report: dict,
    *,
    excluded_source_positions: set[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Return numbering descriptors keyed by stable top-level source position."""
    excluded = excluded_source_positions or set()
    num_to_abstract, abstract_levels, num_level_overrides = _numbering_maps(doc)
    candidates: list[dict[str, Any]] = []
    position = 0
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, doc)
            if position not in excluded:
                ilvl, num_id, numbering_source = _paragraph_num_info(paragraph)
                if num_id is not None:
                    abstract_id = num_to_abstract.get(num_id)
                    level = dict(abstract_levels.get(abstract_id, {}).get(ilvl or 0, {}))
                    level.update(num_level_overrides.get(num_id, {}).get(ilvl or 0, {}))
                    style_name = _style_name(paragraph)
                    candidates.append({
                        "source_position": position,
                        "text": paragraph.text.strip(),
                        "style": style_name,
                        "num_id": num_id,
                        "ilvl": ilvl or 0,
                        "abstract_num_id": abstract_id,
                        "num_fmt": level.get("num_fmt"),
                        "lvl_text": level.get("lvl_text"),
                        "p_style": level.get("p_style"),
                        "start": level.get("start", 1),
                        "numbering_source": numbering_source,
                    })
            position += 1
        elif child.tag == qn("w:tbl"):
            position += 1

    physical_group_id = 0
    previous: dict[str, Any] | None = None
    physical_groups: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        contiguous = (
            previous is not None
            and candidate["num_id"] == previous["num_id"]
            and candidate["source_position"] == previous["source_position"] + 1
        )
        if not contiguous:
            physical_group_id += 1
            physical_groups.append([])
        candidate["physical_group_id"] = f"num_group_{physical_group_id}"
        physical_groups[-1].append(candidate)
        previous = candidate

    groups: list[list[dict[str, Any]]] = []
    previous_physical_group: list[dict[str, Any]] | None = None
    for physical_group in physical_groups:
        if (
            groups
            and previous_physical_group is not None
            and _physical_groups_continue(previous_physical_group, physical_group)
        ):
            groups[-1].extend(physical_group)
        else:
            groups.append(list(physical_group))
        previous_physical_group = physical_group

    result: dict[int, dict[str, Any]] = {}
    for numeric_group_id, group in enumerate(groups, 1):
        logical_num_ids = list(dict.fromkeys(int(item["num_id"]) for item in group))
        continued_across_num_id = len(logical_num_ids) > 1
        for group_index, candidate in enumerate(group):
            style_name = candidate["style"]
            num_fmt = candidate.get("num_fmt")
            p_style = (candidate.get("p_style") or "").casefold()
            evidence: list[str] = []
            if candidate.get("abstract_num_id") is not None and num_fmt in _LIST_FORMATS:
                evidence.append("valid_numbering_definition")
            if _is_list_style(style_name):
                evidence.append("list_style")
            physical_group_size = sum(
                1 for item in group
                if item.get("physical_group_id") == candidate.get("physical_group_id")
            )
            if physical_group_size >= 2:
                evidence.append("consecutive_num_id_group")
            if len(group) >= 2:
                evidence.append("consecutive_numbering_sequence")
            if continued_across_num_id:
                evidence.append("continued_across_num_id")
            if looks_like_list_item(candidate.get("text", "")):
                evidence.append("visible_list_marker")
            if candidate.get("numbering_source") == "direct":
                evidence.append("direct_num_pr")

            protected = _is_protected_style(style_name) or p_style.startswith("heading")
            valid = candidate.get("abstract_num_id") is not None and num_fmt in _LIST_FORMATS
            high = valid and bool({
                "list_style", "consecutive_numbering_sequence", "visible_list_marker",
            } & set(evidence))
            if protected or num_fmt == "none" or not valid or not candidate.get("text"):
                status = "ignored"
                confidence = 0.0
                if protected:
                    evidence.append("protected_role")
                elif not valid:
                    evidence.append("invalid_numbering_definition")
            elif high:
                status = "detected"
                confidence = 0.98 if "list_style" in evidence and len(group) >= 2 else 0.90
            else:
                status = "ambiguous"
                confidence = 0.55
                evidence.append("isolated_generic_numbering")

            descriptor = {
                key: value for key, value in candidate.items()
                if key not in {"text"}
            }
            source_marker_type = source_list_type(num_fmt, candidate.get("lvl_text"))
            descriptor.update({
                "group_size": len(group),
                "group_index": group_index,
                "group_id": f"list_group_{numeric_group_id}",
                "logical_num_ids": logical_num_ids,
                "continued_across_num_id": continued_across_num_id,
                "restart": group_index == 0,
                "status": status,
                "confidence": confidence,
                "evidence": evidence,
                "source_list_type": source_marker_type,
                "list_type": normalize_wx_list_type(
                    source_marker_type,
                    int(candidate.get("ilvl") or 0),
                ),
            })
            result[candidate["source_position"]] = descriptor

    statuses = Counter(item["status"] for item in result.values())
    report["source_lists"] = {
        "detected": statuses.get("detected", 0),
        "ambiguous": statuses.get("ambiguous", 0),
        "ignored": statuses.get("ignored", 0),
        "group_count": len(groups),
        "physical_group_count": len(physical_groups),
        "cross_num_id_group_count": sum(1 for group in groups if len({item['num_id'] for item in group}) > 1),
        "candidates": list(result.values()),
    }
    if statuses.get("ambiguous"):
        report.setdefault("risk_warnings", []).append({
            "type": "ambiguous_source_lists",
            "count": statuses["ambiguous"],
            "message": "Word 编号候选证据不足，普通模式保留为正文。",
        })
    return result


def audit_list_preservation(
    doc,
    normalized_model: dict | None,
    source_lists: dict | None,
    template_profile: dict | None = None,
) -> dict:
    """Compare detected source lists, normalized AST lists, and rendered lists."""
    source_lists = source_lists or {}
    blocks = (normalized_model or {}).get("document", {}).get("blocks", [])
    ast_lists = [block for block in blocks if block.get("block_type") == "list_item"]
    rendered_lists = []
    for index, paragraph in enumerate(doc.paragraphs, 1):
        style_name = _style_name(paragraph)
        ilvl, num_id, _ = _paragraph_num_info(paragraph)
        if _is_protected_style(style_name):
            continue
        if _is_list_style(style_name):
            rendered_lists.append({
                "paragraph": index,
                "style": style_name,
                "num_id": num_id,
                "ilvl": ilvl,
                "text": paragraph.text[:120],
            })

    expected = int(source_lists.get("detected") or 0)
    ast_source_lists = [
        block for block in ast_lists
        if block.get("source", {}).get("numbering", {}).get("status") == "detected"
    ]
    conflicts = [
        block.get("id") for block in ast_lists
        if block.get("source", {}).get("numbering", {}).get("status") == "ignored"
    ]
    style_level_mismatches: list[dict[str, Any]] = []
    if len(rendered_lists) == len(ast_lists):
        for block, rendered in zip(ast_lists, rendered_lists):
            level = int(block.get("level") or 0)
            expected_style = wx_list_style_name(
                block.get("list_type"), level, template_profile,
            )
            if rendered.get("style") != expected_style:
                style_level_mismatches.append({
                    "block_id": block.get("id"),
                    "level": level,
                    "list_type": block.get("list_type"),
                    "expected_style": expected_style,
                    "rendered_style": rendered.get("style"),
                })
    source_body_residue = [
        block.get("id") for block in blocks
        if block.get("block_type") != "list_item"
        and block.get("source", {}).get("numbering", {}).get("status") == "detected"
    ]
    level_jumps: list[dict[str, Any]] = []
    isolated_items: list[str] = []
    current_run: list[dict[str, Any]] = []
    for block in [*blocks, {"block_type": "_end"}]:
        if block.get("block_type") == "list_item":
            if current_run:
                previous_level = int(current_run[-1].get("level") or 0)
                level = int(block.get("level") or 0)
                if level > previous_level + 1:
                    level_jumps.append({
                        "block_id": block.get("id"),
                        "previous_level": previous_level,
                        "level": level,
                    })
            current_run.append(block)
            continue
        if len(current_run) == 1:
            isolated_items.append(str(current_run[0].get("id") or ""))
        current_run = []

    origins: Counter[str] = Counter()
    for block in ast_lists:
        source = block.get("source", {})
        numbering = source.get("numbering", {})
        if block.get("_original_block_type") not in (None, "list_item"):
            origin = "llm"
        elif numbering.get("status") == "detected":
            origin = "rules"
        elif looks_like_list_item(str(source.get("raw_text") or block.get("text") or "")):
            origin = "text_marker"
        elif source.get("inferred_role") not in (None, "list"):
            origin = "normalization_fallback"
        else:
            origin = "rules"
        origins[origin] += 1
    return {
        "source_detected": expected,
        "ast_source_list_items": len(ast_source_lists),
        "ast_total_list_items": len(ast_lists),
        "rendered_list_items": len(rendered_lists),
        "origins": {
            name: origins.get(name, 0)
            for name in ("rules", "llm", "text_marker", "normalization_fallback")
        },
        "source_list_body_residue": source_body_residue,
        "style_level_mismatches": style_level_mismatches,
        "list_level_jumps": level_jumps,
        "isolated_ast_list_items": isolated_items,
        "protected_role_conflicts": conflicts,
        "passed": (
            len(ast_source_lists) == expected
            and len(rendered_lists) == len(ast_lists)
            and not source_body_residue
            and not style_level_mismatches
            and not level_jumps
            and not conflicts
        ),
    }
