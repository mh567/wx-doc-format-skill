"""Structure candidates for SourceFacts roles and section hierarchy."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from ..model import consume_leading_text, inline_text_projection
from ..validation import validate_source_facts
from note_semantics import heading_style_level
from text_utils import strip_heading_marker_prefix

from .protected import protected_unit_semantics


_GRADED_HEADING_ROLES = frozenset({"heading", "title"})
# A heading the source itself declares, through a heading style or a Markdown
# marker, outweighs the text protections named here: the paragraph is styled as a
# section title, so its text alone does not demote it.  ``manual_heading_marker``
# is the ruling of 2026-09-16, ``api_label`` the ruling of 2026-09-17 (an
# interface document styles ``接口说明`` and ``请求参数`` as ``Heading 3``, so the
# exact-text label rule must not take the role away).  Both reasons still ride on
# the role candidate as evidence and still govern everywhere else: the label text
# keeps an unstyled paragraph out of a list, and an unstyled label gets no
# heading role because the style, not the text, is what a heading is graded on.
# Every other protection reason names content the document keeps as it stands (a
# date, a legal clause, a formula), so it keeps its precedence and its protected
# role, which L11 pins.
_PROTECTION_REASONS_A_HEADING_OUTWEIGHS = frozenset({"manual_heading_marker", "api_label"})


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
    level = heading_style_level(style_name)
    if level is not None:
        return "heading", min(level, 6)
    if (
        normalized in {"caption", "题注", "附录标题"}
        or "列项" in style_name
        or "注-" in style_name
        or style_name.startswith("附录")
    ):
        return None, None
    return "body", None


def _marker_content_candidate(
    node_id: str,
    unit: Mapping[str, Any],
    text: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the marker-free content write for one graded heading, and its record.

    A graded heading never carries a manual marker in the delivered document, so
    the text the template renders is the source text without it (AGENTS.md
    decision 10).  The vocabulary is ``text_utils.strip_heading_marker_prefix``
    and the marker leaves the inline content by consuming the same number of
    visible characters, so the marks inside the heading survive.

    Two shapes keep the source text and return a record instead of a write: a
    heading whose whole text is the marker, which would otherwise become empty,
    and a marker the walk cannot consume because an object sits inside it.  Both
    are reported rather than applied in part.
    """

    stripped = strip_heading_marker_prefix(text)
    if stripped == text:
        return None, None
    if not stripped:
        return None, _marker_record(node_id, "heading_marker_only_text", text)
    content = consume_leading_text(list(unit.get("inline_content") or []), len(text) - len(stripped))
    if inline_text_projection(content) != stripped:
        return None, _marker_record(node_id, "heading_marker_not_consumed", text)
    source_ref = unit.get("source_ref")
    return {
        "candidate_id": f"heading-marker-{node_id}",
        "module_id": "source_structure",
        "module_version": "1.0",
        "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
        "target_node_ids": [node_id],
        "proposal": {
            "operation": "update_node_content",
            "property_scope": ["inline_content"],
            "value": {"inline_content": content},
        },
        "positive_evidence": [{
            "kind": "format_contract",
            "source": "heading_marker_stripped",
            "marker_length": len(text) - len(stripped),
        }],
        "negative_evidence": [],
        "scope": {"stages": ["role", "relation"]},
        "constraints": [{"kind": "preserve_inline_marks"}],
        "rationale": "a graded heading never carries a manual marker",
        "confidence": None,
    }, None


def _marker_record(node_id: str, code: str, text: str) -> dict[str, Any]:
    """Return the record for a heading marker that stays in the source text."""

    return {
        "kind": "diagnostic",
        "source": code,
        "code": code,
        "severity": "info",
        "node_id": node_id,
        "message": f"{code}: the manual marker stays in the heading text",
        "source_text": text,
    }


def _graded_candidates(
    node_id: str,
    unit: Mapping[str, Any],
    text: str,
    value: Mapping[str, Any],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the role candidate of a graded heading and its marker cleanup.

    The cleanup is published next to the role it belongs to, so one module owns
    both facts about a heading: which role it has and what text it renders.
    """

    content, record = _marker_content_candidate(node_id, unit, text)
    items = [*evidence, record] if record is not None else list(evidence)
    candidates = [_candidate(node_id, unit, dict(value), items)]
    if content is not None:
        candidates.append(content)
    return candidates


def _generated_title_candidate(facts: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the candidate that gives a Markdown source its document title.

    A Markdown source without a leading H1 still needs a title: the template
    renders one above the table of contents, and the author's file name is the
    only evidence left.  The node records that it was generated, so no reader
    mistakes it for authored content.  A source that does not name its file
    yields no candidate, so nothing invents a title out of nothing.
    """

    name = str(facts["source"].get("name") or "")
    title = Path(name).stem
    if not title:
        return None
    return {
        "candidate_id": "structure-generated-title",
        "module_id": "source_structure",
        "module_version": "1.0",
        "target_source_refs": [],
        "target_node_ids": ["generated-title"],
        "proposal": {
            "operation": "create_node",
            "property_scope": ["children"],
            "value": {
                "node_id": "generated-title",
                "parent_id": "root",
                "index": 0,
                "node": {
                    "block_type": "paragraph",
                    "role": "title",
                    "inline_content": [{"kind": "text", "text": title, "marks": []}],
                    "generated_by": "structure_semantics.filename_title",
                    "generation_basis": [name],
                },
            },
        },
        "positive_evidence": [{"kind": "format_contract", "source": "markdown_filename_title"}],
        "negative_evidence": [],
        "scope": {"stages": ["role"]},
        "constraints": [],
        "rationale": "a Markdown source without a leading H1 needs the document title the template renders",
        "confidence": None,
    }


def declares_document_title(facts: Mapping[str, Any]) -> bool:
    """Return whether the source declares a non-empty document title of its own.

    Only a paragraph the delivered body renders can declare the title: the
    structure module grades root paragraphs and leaves the source's own table of
    contents to the template, so a title style inside a table cell or inside the
    TOC control declares nothing here.  A blank title-styled paragraph declares
    nothing either and still takes the generated line.  The style vocabulary
    stays owned by ``_docx_style_role``, so the reader and the structure module
    cannot disagree about which style is the document title.
    """

    evidence = facts.get("evidence") or {}
    for node_id in (facts.get("children") or {}).get("root", []):
        unit = (facts.get("units") or {}).get(node_id)
        if not isinstance(unit, Mapping):
            continue
        if (evidence.get(node_id) or {}).get("source_toc") is True:
            continue
        role, _level = _docx_style_role(str(unit.get("style") or ""))
        if role == "title" and inline_text_projection(list(unit.get("inline_content") or [])).strip():
            return True
    return False


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
        source_style = ""
        style_role: str | None = None
        style_level: int | None = None
        if not is_markdown:
            source_style = str(unit.get("style") or evidence.get("style") or "")
            style_role, style_level = _docx_style_role(source_style)
            outline_level = evidence.get("outline_level")
            if (
                style_role == "body"
                and isinstance(outline_level, int)
                and not isinstance(outline_level, bool)
            ):
                # A heading the source declares through ``w:outlineLvl`` alone
                # earns the same role as one it declares through a heading style.
                # The reader records the level, the review already reads the same
                # property, and this grades the paragraph so a plain-styled title
                # does not lose its section role.
                style_role = "heading"
                style_level = max(1, min(outline_level + 1, 6))
        # A heading the source itself declares, through a heading style or a
        # Markdown marker, outweighs the text protections a heading can carry
        # (rulings of 2026-09-16 and 2026-09-17).  The reason still rides on the
        # role candidate as evidence.  A protection that names a role of its own
        # keeps its precedence, so a numbered note stays a note and a code block
        # keeps its role.
        outweighed_evidence: list[dict[str, Any]] = []
        if protect_semantics and protected is not None:
            explicit_heading = style_role in _GRADED_HEADING_ROLES or (is_markdown and role == "heading")
            if explicit_heading and protected.get("reason") in _PROTECTION_REASONS_A_HEADING_OUTWEIGHS:
                outweighed_evidence = [{
                    "kind": "structural",
                    "source": str(protected.get("reason")),
                    "reason": protected.get("reason"),
                }]
            else:
                protected_role = str(protected.get("role") or "")
                resolved_role = protected_role if protected_role in {"title", "body", "heading", "caption", "note", "numbered_note", "unknown"} else "body"
                result.append(_candidate(
                    str(node_id),
                    unit,
                    {"role": resolved_role},
                    [{"kind": "format_contract", "source": "protected_content", "reason": protected.get("reason")}],
                ))
                if protected_role in {"note", "numbered_note"}:
                    canonical = str(protected["text"])
                    prefix_length = text.find(canonical) if canonical else -1
                    if prefix_length > 0:
                        content = consume_leading_text(list(unit.get("inline_content") or []), prefix_length)
                        if inline_text_projection(content).strip() == canonical:
                            candidate = copy.deepcopy(result[-1])
                            candidate["candidate_id"] = f"note-content-{node_id}"
                            candidate["proposal"] = {
                                "operation": "update_node_content",
                                "property_scope": ["inline_content"],
                                "value": {"inline_content": content},
                            }
                            candidate["rationale"] = "consume the literal note marker after resolving its note role"
                            result.append(candidate)
                semantic_content_seen = True
                continue
        if not is_markdown:
            if style_role is None:
                semantic_content_seen = True
                continue
            if style_role == "title" and not title_seen:
                result.extend(_graded_candidates(
                    str(node_id),
                    unit,
                    text,
                    {"role": "title"},
                    [
                        {"kind": "structural", "source": "docx_style", "style": source_style},
                        *outweighed_evidence,
                    ],
                ))
                title_seen = True
                semantic_content_seen = True
                continue
            if style_role == "heading":
                result.extend(_graded_candidates(
                    str(node_id),
                    unit,
                    text,
                    {"role": "heading", "level": style_level},
                    [
                        {"kind": "structural", "source": "docx_style", "style": source_style},
                        *outweighed_evidence,
                    ],
                ))
                semantic_content_seen = True
                continue
            role = "body"
        if role == "heading":
            level = int(unit.get("level") or evidence.get("level") or 1)
            if is_markdown and level == 1 and not title_seen and not semantic_content_seen:
                result.extend(_graded_candidates(
                    str(node_id),
                    unit,
                    text,
                    {"role": "title"},
                    [{"kind": "structural", "source": "markdown_leading_h1"}, *outweighed_evidence],
                ))
                title_seen = True
            else:
                normalized_level = level - 1 if is_markdown else level
                result.extend(_graded_candidates(
                    str(node_id),
                    unit,
                    text,
                    {"role": "heading", "level": max(1, min(normalized_level, 6))},
                    [{"kind": "structural", "source": "source_facts", "level": level}, *outweighed_evidence],
                ))
            semantic_content_seen = True
        elif text.strip():
            result.append(_candidate(str(node_id), unit, {"role": "body"}, [{"kind": "structural", "source": "source_facts", "text_present": True}]))
            semantic_content_seen = True
    if is_markdown and not title_seen:
        # A Markdown source without a leading H1 still needs the document title
        # the template renders, so the file name supplies it.
        generated = _generated_title_candidate(facts)
        if generated is not None:
            result.insert(0, generated)
    return result


def generated_title(candidates: list[Mapping[str, Any]]) -> str | None:
    """Return the title a structure candidate set generated from a file name."""

    for candidate in candidates:
        if str(candidate.get("candidate_id") or "") != "structure-generated-title":
            continue
        value = candidate.get("proposal", {}).get("value", {})
        inline = value.get("node", {}).get("inline_content") or []
        if inline:
            return str(inline[0].get("text") or "") or None
    return None


__all__ = ["collect_structure_candidates", "generated_title"]
