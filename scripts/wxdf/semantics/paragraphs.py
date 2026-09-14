"""Paragraph-level semantic normalization for source documents."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from ..validation import validate_ast, validate_source_facts


def _strip_leading_whitespace(inline_content: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove presentation whitespace before the first visible inline token."""

    result: list[dict[str, Any]] = []
    leading = True
    for item in inline_content:
        copied = copy.deepcopy(dict(item))
        if not leading:
            result.append(copied)
            continue

        kind = copied.get("kind")
        if kind == "tab":
            continue
        if kind != "text":
            leading = False
            result.append(copied)
            continue

        text = str(copied.get("text") or "")
        retained = text.lstrip(" \t")
        if retained:
            copied["text"] = retained
            result.append(copied)
            leading = False
        elif text:
            continue
        else:
            result.append(copied)
    return result


def canonicalize_docx_paragraph_inline_content(
    inline_content: list[Mapping[str, Any]],
    evidence: Mapping[str, Any],
    *,
    eligible: bool,
    role: str | None = None,
    style: str | None = None,
) -> list[dict[str, Any]]:
    """Consume root-paragraph presentation whitespace recorded by the parser.

    SourceFacts remains the lossless source contract.  This function creates
    the semantic inline projection used by Source AST consumers only when the
    parser has recorded leading whitespace and the caller has established that
    the paragraph is an eligible root-level DOCX paragraph.
    """

    copied = copy.deepcopy(list(inline_content))
    normalized_style = str(style or "").casefold().replace(" ", "")
    protected_style = (
        normalized_style.startswith(("heading", "toc", "title", "代码", "code"))
        or "题注" in normalized_style
        or "caption" in normalized_style
        or "附录标题" in normalized_style
    )
    if (
        not eligible
        or role not in {None, "body"}
        or protected_style
        or not evidence.get("leading_whitespace")
    ):
        return copied
    return _strip_leading_whitespace(copied)


def collect_paragraph_candidates(
    facts: Mapping[str, Any],
    source_ast: Mapping[str, Any],
    *,
    caption_semantics_result: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Publish paragraph-only content repairs for the relation transaction.

    The parser owns raw whitespace evidence.  This module decides whether the
    evidence is presentation noise and emits a bounded inline-content write.
    List markers are deliberately left to ``list_semantics`` so one paragraph
    has one content owner in the candidate set.

    ``caption_semantics_result`` is the published caption result owned by
    ``collect_source_caption_semantics``.  When the caller supplies it, this
    module reads the caption ownership instead of recomputing caption and table
    semantics.
    """

    validate_source_facts(facts)
    validate_ast(source_ast)
    if str(facts.get("source", {}).get("source_type") or "") != "docx":
        return []

    if caption_semantics_result is None:
        from .captions import collect_source_caption_semantics
        from .tables import collect_source_table_semantics

        caption_semantics_result = collect_source_caption_semantics(
            facts,
            collect_source_table_semantics(facts),
        )

    caption_ids = {
        str(item.get("node_id"))
        for item in caption_semantics_result.get("captions", [])
        if item.get("status") == "associated"
    }

    result: list[dict[str, Any]] = []
    for node_id in facts["children"].get("root", []):
        unit = facts["units"].get(node_id)
        block = source_ast["document"]["blocks"].get(node_id)
        if not isinstance(unit, Mapping) or unit.get("kind") != "paragraph":
            continue
        if not isinstance(block, Mapping) or block.get("block_type") != "paragraph":
            continue
        evidence = facts.get("evidence", {}).get(node_id, {})
        if (
            not evidence.get("leading_whitespace")
            or evidence.get("visible_marker")
            or str(node_id) in caption_ids
        ):
            continue
        role = str(unit.get("role") or "body")
        cleaned = canonicalize_docx_paragraph_inline_content(
            unit.get("inline_content", []),
            evidence,
            eligible=True,
            role=role,
            style=unit.get("style"),
        )
        if cleaned == block.get("inline_content", []):
            continue
        source_ref = unit.get("source_ref")
        result.append({
            "candidate_id": f"paragraph-content-{node_id}",
            "module_id": "paragraph_semantics",
            "module_version": "1.0",
            "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
            "target_node_ids": [node_id],
            "proposal": {
                "operation": "update_node_content",
                "property_scope": ["inline_content"],
                "value": {"inline_content": copy.deepcopy(cleaned)},
            },
            "positive_evidence": [{"kind": "format_contract", "source": "docx_leading_whitespace"}],
            "negative_evidence": [],
            "scope": {"stages": ["relation"]},
            "constraints": [{"kind": "consume_parser_presentation_evidence"}],
            "rationale": "remove leading DOCX presentation whitespace from an eligible body paragraph",
            "confidence": None,
        })
    return result


__all__ = ["canonicalize_docx_paragraph_inline_content", "collect_paragraph_candidates"]
