"""Structure candidates for SourceFacts roles and section hierarchy."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..model import inline_text_projection
from ..validation import validate_source_facts
from .protected import protected_unit_semantics


def _candidate(node_id: str, unit: Mapping[str, Any], value: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    source_ref = unit.get("source_ref")
    return {
        "candidate_id": f"structure-{node_id}",
        "module_id": "source_structure",
        "module_version": "1.0",
        "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
        "target_node_ids": [node_id],
        "proposal": {"operation": "update_role", "property_scope": ["role", "level"] if value.get("role") == "heading" else ["role"], "value": value},
        "positive_evidence": evidence,
        "negative_evidence": [],
        "scope": {"stages": ["role", "relation"]},
        "constraints": [{"kind": "preserve_source_ref"}],
        "rationale": "SourceFacts explicitly identifies the paragraph structure",
        "confidence": None,
    }


def _docx_style_role(style_name: str) -> tuple[str | None, int | None]:
    normalized = style_name.strip().casefold()
    if normalized in {"title", "document title", "documenttitle", "文档标题", "文件标题", "主标题"}:
        return "title", None
    heading = re.match(r"^(?:heading|标题)\s*(\d+)$", normalized, re.IGNORECASE)
    if heading:
        return "heading", min(int(heading.group(1)), 6)
    if (
        normalized in {"caption", "题注", "附录标题"}
        or "列项" in style_name
        or "注-" in style_name
        or style_name.startswith("附录")
    ):
        return None, None
    return "body", None


def collect_structure_candidates(
    facts: Mapping[str, Any],
    regions: Mapping[str, Any] | None = None,
    *,
    protect_semantics: bool = False,
) -> list[dict[str, Any]]:
    """Collect explicit heading and paragraph roles from public facts."""

    validate_source_facts(facts)
    excluded_node_ids = {
        str(node_id)
        for region in (regions or {}).get("regions", [])
        if region.get("semantic_scope") == "excluded"
        for node_id in region.get("node_ids", [])
    }
    ordered_node_ids = list(facts["children"].get("root", []))
    is_markdown = facts["source"].get("source_type") == "markdown"
    title_seen = False
    semantic_content_seen = False
    result: list[dict[str, Any]] = []
    for node_id in ordered_node_ids:
        if str(node_id) in excluded_node_ids:
            continue
        unit = facts["units"].get(node_id)
        if not isinstance(unit, Mapping):
            if node_id in facts["containers"] and facts["containers"][node_id].get("kind") in {"table", "quote", "list"}:
                semantic_content_seen = True
            continue
        if unit.get("kind") != "paragraph":
            if unit.get("raw_text") or unit.get("inline_content"):
                semantic_content_seen = True
            continue
        role = str(unit.get("role") or "body")
        text = inline_text_projection(unit.get("inline_content", []))
        has_media = any(
            item.get("kind") == "image"
            for item in unit.get("inline_content", [])
            if isinstance(item, Mapping)
        )
        if not text.strip() and not has_media:
            continue
        evidence = facts["evidence"].get(node_id, {})
        protected = protected_unit_semantics(unit, evidence)
        if protect_semantics and protected is not None:
            protected_role = str(protected.get("role") or "")
            resolved_role = protected_role if protected_role in {"title", "body", "heading", "caption", "note", "numbered_note", "unknown"} else "body"
            result.append(_candidate(
                str(node_id),
                unit,
                {"role": resolved_role},
                [{"kind": "format_contract", "source": "protected_content", "reason": protected.get("reason")}],
            ))
            semantic_content_seen = True
            continue
        if not is_markdown:
            source_style = str(unit.get("style") or evidence.get("style") or "")
            style_role, style_level = _docx_style_role(source_style)
            if style_role is None:
                semantic_content_seen = True
                continue
            if style_role == "title" and not title_seen:
                result.append(_candidate(
                    str(node_id),
                    unit,
                    {"role": "title"},
                    [{"kind": "structural", "source": "docx_style", "style": source_style}],
                ))
                title_seen = True
                semantic_content_seen = True
                continue
            if style_role == "heading":
                result.append(_candidate(
                    str(node_id),
                    unit,
                    {"role": "heading", "level": style_level},
                    [{"kind": "structural", "source": "docx_style", "style": source_style}],
                ))
                semantic_content_seen = True
                continue
            role = "body"
        if role == "heading":
            level = int(unit.get("level") or evidence.get("level") or 1)
            if is_markdown and level == 1 and not title_seen and not semantic_content_seen:
                result.append(_candidate(
                    str(node_id),
                    unit,
                    {"role": "title"},
                    [{"kind": "structural", "source": "markdown_leading_h1"}],
                ))
                title_seen = True
            else:
                normalized_level = level - 1 if is_markdown else level
                result.append(_candidate(
                    str(node_id),
                    unit,
                    {"role": "heading", "level": max(1, min(normalized_level, 6))},
                    [{"kind": "structural", "source": "source_facts", "level": level}],
                ))
            semantic_content_seen = True
        elif text.strip():
            result.append(_candidate(str(node_id), unit, {"role": "body"}, [{"kind": "structural", "source": "source_facts", "text_present": True}]))
            semantic_content_seen = True
    return result


__all__ = ["collect_structure_candidates"]
