"""List semantics over the public SourceFacts contract."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..validation import validate_source_facts
from .protected import protected_code_style, protected_text_reason


def _node_kind(facts: Mapping[str, Any], node_id: str) -> str:
    if node_id in facts["units"]:
        return str(facts["units"][node_id].get("kind") or "")
    return str(facts["containers"].get(node_id, {}).get("kind") or "")


def _parent_map(facts: Mapping[str, Any]) -> dict[str, str]:
    parent: dict[str, str] = {}
    for parent_id, child_ids in facts["children"].items():
        for child_id in child_ids:
            parent[child_id] = parent_id
    return parent


def _ancestors(node_id: str, parents: Mapping[str, str]) -> list[str]:
    result: list[str] = []
    current = node_id
    while current in parents:
        current = parents[current]
        result.append(current)
    result.reverse()
    return result


def _unit_text(unit: Mapping[str, Any]) -> str:
    visible: list[str] = []
    for item in unit.get("inline_content", []):
        kind = item.get("kind")
        if kind == "text":
            visible.append(str(item.get("text") or ""))
        elif kind == "break":
            visible.append("\n")
        elif kind == "tab":
            visible.append("\t")
        elif kind == "link":
            visible.append(_inline_text(item.get("children", [])))
    return "".join(visible)


def strip_visible_list_marker(
    inline_content: list[Mapping[str, Any]],
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Remove a manually typed list prefix while preserving inline marks."""

    marker = evidence.get("visible_marker", {}).get("marker_text")
    if not isinstance(marker, str) or not marker:
        return copy.deepcopy(list(inline_content))
    prefix = re.compile(r"^[ \t]*" + re.escape(marker) + r"[ \t]+")
    visible = _unit_text({"inline_content": list(inline_content)})
    match = prefix.match(visible)
    if match is None:
        return copy.deepcopy(list(inline_content))

    remaining = match.end()
    result: list[dict[str, Any]] = []
    for item in inline_content:
        copied = copy.deepcopy(dict(item))
        kind = copied.get("kind")
        if remaining <= 0 or kind not in {"text", "tab"}:
            result.append(copied)
            continue
        item_text = str(copied.get("text") or "") if kind == "text" else "\t"
        consume = min(remaining, len(item_text))
        remaining -= consume
        retained = item_text[consume:]
        if kind == "text":
            if retained:
                copied["text"] = retained
                result.append(copied)
        elif remaining <= 0 and retained:
            result.append({"kind": "text", "text": retained, "marks": []})
    return result


def _inline_text(items: list[Mapping[str, Any]]) -> str:
    return _unit_text({"inline_content": items})


def _is_protected(unit: Mapping[str, Any], evidence: Mapping[str, Any]) -> tuple[bool, str | None]:
    if unit.get("kind") != "paragraph":
        return True, "non_paragraph_unit"
    role = str(unit.get("role") or evidence.get("role") or "")
    style = str(unit.get("style") or evidence.get("style") or "")
    text = _unit_text(unit).strip()
    if role in {"heading", "note", "numbered_note", "caption", "title", "code", "code_block"}:
        return True, f"protected_role:{role}"
    if protected_code_style(style):
        return True, "opaque_code"
    if evidence.get("protected") is True:
        return True, "source_protected"
    if evidence.get("numbering_status") == "detected" and evidence.get("marker_kind") and not evidence.get("visible_marker"):
        return False, None
    normalized = style.casefold().replace(" ", "")
    if normalized.startswith(("heading", "toc")) or "目录" in style or "题注" in style or "附录标题" in style:
        return True, "protected_style"
    reason = protected_text_reason(text, evidence)
    if reason is not None:
        return True, reason
    visible_marker = evidence.get("visible_marker")
    if isinstance(visible_marker, Mapping) and evidence.get("marker_kind"):
        return False, None
    if evidence.get("semantic_role") in {"note", "numbered_note"}:
        return True, "note_evidence"
    if evidence.get("numbering_status") == "detected" and evidence.get("marker_kind"):
        return False, None
    return False, None


def _scope_id(ancestors: list[str], facts: Mapping[str, Any]) -> str:
    for node_id in reversed(ancestors):
        if _node_kind(facts, node_id) in {"table_cell", "quote"}:
            return f"scope:{node_id}"
    return "scope:document"


def evidence_lookup_restart_scope(group: Mapping[str, Any]) -> str:
    """Return the catalogue-derived restart scope for one published group.

    README's evidence-field list defines ``restart_scope`` as derived from
    ``num_id`` / ``abstract_num_id``, so the group scope must carry the parser's
    scope identifier (``abstract:<id>`` or ``num:<id>``) instead of echoing the
    sequence identity.  The value is read from the group's numbering evidence so
    a group and its items agree on one scope.  A group with no catalogue scope
    (Markdown implicit lists carry no ``numId``) falls back to its sequence
    identity.
    """

    restart_scope = group.get("restart_scope")
    if isinstance(restart_scope, str) and restart_scope:
        return restart_scope
    return str(group.get("source_sequence_id") or group.get("list_id", "scope:document"))


def _restart_scope_from_evidence(evidence: Mapping[str, Any], num_id: Any, abstract_id: Any) -> str | None:
    """Return the restart scope the catalogue assigned to one numbering instance."""

    scope = evidence.get("restart_scope")
    if isinstance(scope, str) and scope:
        return scope
    if abstract_id is not None:
        return f"abstract:{abstract_id}"
    if num_id is not None:
        return f"num:{num_id}"
    return None


def _list_family(evidence: Mapping[str, Any], container: Mapping[str, Any] | None = None) -> str | None:
    num_fmt = evidence.get("num_fmt")
    marker_kind = evidence.get("marker_kind")
    # num_fmt is the more reliable OOXML signal; when it disagrees with
    # marker_kind, num_fmt takes precedence for category selection.
    if num_fmt == "bullet":
        return "unordered"
    if num_fmt in {"decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman"}:
        return "ordered"
    if marker_kind in {"unordered", "bullet"}:
        return "unordered"
    if marker_kind in {"ordered", "decimal", "lower_letter", "upper_letter"}:
        return "ordered"
    if container and container.get("list_kind") in {"ordered", "unordered"}:
        return str(container["list_kind"])
    return None


def _source_numbering_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "numbering_status",
        "numbering_definition_ref",
        "num_fmt",
        "lvl_text",
        "marker_kind",
        "marker_text",
        "source_list_type",
        "numbering_evidence",
        "ordinal",
        "explicit_depth",
        "indent_value",
        "indent_unit",
        "restart_hint",
        "blank_before",
        "continuation_hint",
        "source_position",
    )
    return {
        field: copy.deepcopy(evidence[field])
        for field in fields
        if field in evidence
    }


def _anchor_info(item_id: str, facts: Mapping[str, Any]) -> tuple[bool, str]:
    if item_id in facts["units"]:
        unit = facts["units"][item_id]
        if unit.get("kind") == "paragraph":
            has_inline_image = any(item.get("kind") == "image" for item in unit.get("inline_content", []))
            if _unit_text(unit).strip() or has_inline_image:
                return False, "paragraph_first"
            return True, "empty_first_paragraph"
        return True, f"unit_{unit.get('kind') or 'unknown'}"
    children = facts["children"].get(item_id, [])
    if not children:
        return True, "empty_list_item"
    first = children[0]
    kind = _node_kind(facts, first)
    if kind in {"table", "image", "list", "quote"}:
        return True, f"first_child_{kind}"
    if first in facts["units"] and facts["units"][first].get("kind") == "paragraph":
        unit = facts["units"][first]
        has_inline_image = any(item.get("kind") == "image" for item in unit.get("inline_content", []))
        if not _unit_text(unit).strip() and not has_inline_image:
            return True, "empty_first_paragraph"
    return False, "paragraph_first"


def _emit_group(
    result: dict[str, Any],
    facts: Mapping[str, Any],
    *,
    list_id: str,
    list_kind: str,
    source_sequence_id: str,
    source_start: int,
    item_records: list[dict[str, Any]],
    scope_id: str,
    parent_item_id: str | None = None,
    explicit: bool,
    group_boundary: Mapping[str, Any] | None = None,
    restart_expression: Mapping[str, Any] | None = None,
    decided_states: Mapping[str, str] | None = None,
    sequence_split_index: int = 0,
) -> None:
    group = {
        "list_id": list_id,
        "list_kind": list_kind,
        "source_sequence_id": source_sequence_id,
        "source_start": source_start,
        "start_value": source_start,
        "sequence": source_sequence_id,
        "restart_scope": (restart_expression or {}).get("restart_scope", source_sequence_id),
        "scope_id": scope_id,
        "parent_item_id": parent_item_id,
        "explicit": explicit,
        # Sequence-split identity.  A split reuses the numbering instance, so
        # ``source_sequence_id`` / ``sequence`` / ``restart_scope`` alone cannot
        # tell two split groups apart.  This additive field is the zero-based
        # index of the group within its run for one ``source_sequence_id``: 0 for
        # the first group of an instance and 1, 2, ... for the groups split off
        # from it.  The frozen ``docx-sequence-{num_id}-{abstract_num_id}``
        # spelling is unchanged; this field only extends the identity.
        "sequence_split_index": sequence_split_index,
    }
    if group_boundary is not None:
        group["group_boundary"] = copy.deepcopy(dict(group_boundary))
    if restart_expression is not None:
        group["restart_expression"] = copy.deepcopy(dict(restart_expression))
        if restart_expression.get("restart_policy") is not None:
            group["restart_policy"] = restart_expression["restart_policy"]
        if restart_expression.get("merged_sequences"):
            group["merged_sequences"] = list(restart_expression["merged_sequences"])
    result["lists"].append(group)
    last_by_depth: dict[int, str] = {}
    for index, item in enumerate(item_records):
        item_id = item["item_id"]
        evidence = item["evidence"]
        if explicit:
            depth = 0
            parent_id = parent_item_id
        else:
            declared_depth = evidence.get("explicit_depth")
            depth = item.get("effective_depth")
            if not isinstance(depth, int) or depth < 0:
                depth = int(declared_depth) if isinstance(declared_depth, int) and declared_depth >= 0 else 0
            parent_id = item.get("parent_item_id")
            if parent_id is None and depth > 0:
                parent_id = last_by_depth.get(depth - 1)
        for old_depth in [key for key in last_by_depth if key >= depth]:
            last_by_depth.pop(old_depth, None)
        last_by_depth[depth] = item_id
        target_ordinal = None
        generated_anchor, anchor_reason = _anchor_info(item_id, facts)
        source_ilvl = evidence.get("source_ilvl")
        effective_ilvl_value = evidence.get("effective_ilvl")
        if not isinstance(effective_ilvl_value, int):
            effective_ilvl_value = depth
        semantic_level = depth
        render_level = depth
        relation = {
            "item_id": item_id,
            "list_id": list_id,
            "parent_item_id": parent_id,
            "scope_id": scope_id,
            "source_marker": facts["containers"].get(item_id, {}).get("source_marker") or evidence.get("marker_text"),
            "source_ordinal": facts["containers"].get(item_id, {}).get("source_ordinal") or evidence.get("ordinal"),
            "source_sequence_id": source_sequence_id,
            "list_kind": list_kind,
            "target_ordinal": target_ordinal,
            "generated_anchor_required": generated_anchor,
            "anchor_reason": anchor_reason,
            "explicit": explicit,
            "source_ilvl": copy.deepcopy(source_ilvl) if source_ilvl is not None else None,
            "effective_ilvl": effective_ilvl_value,
            "semantic_level": semantic_level,
            "render_level": render_level,
            "start_value": evidence.get("start_value"),
            "sequence": evidence.get("sequence"),
            "restart_scope": evidence.get("restart_scope"),
            "num_id": evidence.get("num_id"),
            "abstract_num_id": evidence.get("abstract_num_id"),
            "num_fmt": evidence.get("num_fmt"),
            "lvl_text": evidence.get("lvl_text"),
            "w_ind": evidence.get("w_ind"),
            "w:ind": evidence.get("w_ind"),
            "suffix": evidence.get("suffix"),
            "p_style_ref": evidence.get("p_style_ref"),
            "restart": evidence.get("restart_hint"),
            "lvl_restart": evidence.get("lvl_restart"),
            "start_override": evidence.get("start_override"),
        }
        result["items"].append(relation)
        result["candidates"].append(
            {
                "candidate_id": f"list-relation-{item_id}",
                "module_id": "list_semantics",
                "module_version": "1.0",
                "target_source_refs": [copy.deepcopy(facts["units"].get(item_id, facts["containers"].get(item_id, {})).get("source_ref"))] if facts["units"].get(item_id, facts["containers"].get(item_id, {})).get("source_ref") else [],
                "target_node_ids": [item_id],
                "proposal": {
                    "operation": "move_node",
                    "property_scope": ["children"],
                    "value": {"new_parent_id": list_id, "list_id": list_id, "parent_item_id": parent_id},
                },
                "positive_evidence": [{"kind": "structural", "source": "source_facts_list" if explicit else "bounded_list_run"}],
                "negative_evidence": [],
                "scope": {"stages": ["relation"], "scope_id": scope_id},
                "constraints": [],
                "rationale": "list ownership derived from source order and bounded depth",
            }
        )
        children = facts["children"].get(item_id, [])
        for continuation_id in children[1:]:
            if continuation_id in facts["units"] and _node_kind(facts, continuation_id) == "paragraph":
                continuation_evidence = facts["evidence"].get(continuation_id, {})
                # Continuation ownership is consumed from the arbitrated state
                # set: a block is a continuation only when its decision says so.
                # A missing decision is not a continuation; the source evidence
                # must independently support it.
                if decided_states is not None:
                    decision = decided_states.get(continuation_id)
                    if decision != "ITEM_CONTINUATION" and not (
                        decision is None and continuation_evidence.get("continuation_hint")
                    ):
                        continue
                if not continuation_evidence.get("marker_kind"):
                    entry = {"node_id": continuation_id, "owner_item_id": item_id, "list_id": list_id, "scope_id": scope_id}
                    result["continuations"].append(entry)
                    # ``owned_content`` keeps one ownership record per item in
                    # source order, mixing continuation paragraphs and
                    # non-paragraph content.  ``continuations`` / ``child_blocks``
                    # stay filtered views over it for the consumers that read
                    # one state at a time.
                    result.setdefault("owned_content", []).append({**entry, "state": "ITEM_CONTINUATION"})


LIST_STATE_ENUM = (
    "OUTSIDE_LIST",
    "LIST_ITEM",
    "ITEM_CONTINUATION",
    "ITEM_CHILD_BLOCK",
    "NESTED_LIST_ITEM",
    "LIST_END",
    "AMBIGUOUS",
)

_STATE_PROPERTY_SCOPE = "list_state"


def _state_candidate(
    node_id: str,
    state: str,
    *,
    list_id: str | None = None,
    list_kind: str | None = None,
    semantic_level: int | None = None,
    parent_item_id: str | None = None,
    positive_evidence: list[dict[str, Any]] | None = None,
    negative_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Encode one candidate state for a source block.

    A block may publish more than one candidate state; the Resolver picks the
    strongest one.  The candidate carries no bespoke ``score`` field: the
    Resolver's own evidence-class ranking is the single arbitration input
    (``resolver._candidate_strength`` over ``positive_evidence``), so a field
    cannot drift out of sync with what actually decides a block.  Protected
    structure and contract downgrades enter as positive evidence for
    ``OUTSIDE_LIST`` and as negative evidence that keeps a heading from ever
    being promoted to a list item.
    """

    return {
        "node_id": str(node_id),
        "state": state,
        "list_id": list_id,
        "list_kind": list_kind,
        "semantic_level": semantic_level,
        "parent_item_id": parent_item_id,
        "positive_evidence": list(positive_evidence or []),
        "negative_evidence": list(negative_evidence or []),
    }


def _contract_downgrades(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return downgrade diagnostics for missing or damaged numbering definitions."""

    downgrades: list[dict[str, Any]] = []
    for node_id, evidence in facts["evidence"].items():
        if evidence.get("numbering_status") != "ignored":
            continue
        numbering_evidence = set(evidence.get("numbering_evidence") or [])
        if "invalid_numbering_definition" not in numbering_evidence:
            continue
        definition_ref = evidence.get("numbering_definition_ref")
        missing_definition = not isinstance(definition_ref, Mapping) or definition_ref.get("abstract_num_id") is None
        code = "missing_numbering_definition" if missing_definition else "invalid_numbering_definition"
        downgrades.append(
            {
                "code": code,
                "severity": "info",
                "message": f"list-like paragraph {node_id} was downgraded to a body paragraph because its numbering definition is unavailable",
                "node_id": node_id,
                "numbering_status": "ignored",
                "numbering_definition_ref": copy.deepcopy(dict(definition_ref)) if isinstance(definition_ref, Mapping) else None,
                "source_evidence": _source_numbering_evidence(evidence),
                "fallback": {
                    "kind": "body_paragraph",
                    "preserve_source_content": True,
                },
            }
        )
    return downgrades


def _has_numbering_definition(evidence: Mapping[str, Any]) -> bool:
    reference = evidence.get("numbering_definition_ref")
    if isinstance(reference, Mapping) and reference.get("num_id") is not None:
        return True
    return evidence.get("num_id") is not None


def _raw_depth(evidence: Mapping[str, Any]) -> int:
    """Return the raw effective depth declared by the source evidence."""

    effective = evidence.get("effective_ilvl")
    if isinstance(effective, int) and effective >= 0:
        return effective
    declared = evidence.get("explicit_depth")
    return int(declared) if isinstance(declared, int) and declared >= 0 else 0


_LOW_CONFIDENCE_NUMBERING_EVIDENCE = "isolated_generic_numbering"
_LOW_CONFIDENCE_NUMBERING_STATUS = "ambiguous"


def _numbering_is_low_confidence(evidence: Mapping[str, Any]) -> bool:
    """Return whether the parser flagged an unconfirmed, low-confidence marker.

    The DOCX adapter marks a paragraph whose numbering definition resolves but
    whose evidence is insufficient for a confident list classification with
    ``numbering_status="ambiguous"`` and the ``isolated_generic_numbering``
    evidence class (see ``scripts/list_detector.py``).  Such a paragraph
    arrives without ``marker_kind``, so this signal is the adapter's ambiguity
    channel and must reach the ambiguity report instead of being dropped.
    """

    if evidence.get("numbering_status") == _LOW_CONFIDENCE_NUMBERING_STATUS:
        return True
    return _LOW_CONFIDENCE_NUMBERING_EVIDENCE in {
        str(item) for item in evidence.get("numbering_evidence") or []
    }


def _state_candidates_for_paragraph(
    node_id: str,
    unit: Mapping[str, Any],
    evidence: Mapping[str, Any],
    downgrade_codes: Mapping[str, str],
    low_confidence_nodes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Offer the competing states a single paragraph's raw evidence supports.

    A block only publishes one state when its evidence is unambiguous.  Where
    the raw evidence genuinely splits (a protected heading, a damaged numbering
    definition, a marker that no numbering definition confirms) two states with
    equal evidence strength compete, so the Resolver arbitrates instead of
    confirming a decision that was already taken elsewhere.
    """

    protected, reason = _is_protected(unit, evidence)
    marker_kind = evidence.get("marker_kind")
    if protected:
        role = str(unit.get("role") or evidence.get("role") or "")
        style = str(unit.get("style") or evidence.get("style") or "")
        negative: list[dict[str, Any]] = [
            {"kind": "protected_content", "reason": reason or "protected"},
        ]
        if role in {"heading", "title", "caption"} or "heading" in style.casefold() or "标题" in style:
            negative.append({"kind": "structure", "role": role or "heading", "style": style})
        level = _raw_depth(evidence)
        # A protected heading keeps a strong OUTSIDE_LIST state and offers
        # LIST_ITEM only weakly, carrying the protection as negative evidence.
        return [
            _state_candidate(
                node_id,
                "OUTSIDE_LIST",
                semantic_level=level,
                positive_evidence=[{"kind": "structural", "source": "protected_structure"}],
                negative_evidence=negative,
            ),
            _state_candidate(
                node_id,
                "LIST_ITEM",
                semantic_level=level,
                positive_evidence=[{"kind": "heuristic", "source": "protected_marker"}],
                negative_evidence=negative,
            ),
        ]
    if not marker_kind:
        if _numbering_is_low_confidence(evidence):
            # ``list_detector`` saw a numbering definition but could not confirm
            # the block as a list item (``numbering_status="ambiguous"`` with
            # ``isolated_generic_numbering``), and it keeps the paragraph as body
            # text.  The verdict is already body text, so the block stays body
            # text instead of entering a Resolver conflict.  The decision is not
            # silent: it is recorded as a ``low_confidence_list_marker``
            # downgrade (severity info) that is published in the report.
            if low_confidence_nodes is not None:
                low_confidence_nodes.append(str(node_id))
            level = _raw_depth(evidence)
            return [
                _state_candidate(
                    node_id,
                    "OUTSIDE_LIST",
                    semantic_level=level,
                    positive_evidence=[{"kind": "contextual", "source": "unconfirmed_numbering"}],
                    negative_evidence=[{"kind": "heuristic", "reason": "low_confidence_numbering"}],
                ),
            ]
        return [
            _state_candidate(
                node_id,
                "OUTSIDE_LIST",
                positive_evidence=[{"kind": "contextual", "source": "body_paragraph"}],
            )
        ]

    level = _raw_depth(evidence)
    downgrade_code = downgrade_codes.get(node_id)
    if downgrade_code is not None:
        negative = [{"kind": "contract", "reason": downgrade_code}]
        return [
            _state_candidate(
                node_id,
                "OUTSIDE_LIST",
                semantic_level=level,
                positive_evidence=[{"kind": "contract", "source": "contract_downgrade"}],
                negative_evidence=negative,
            ),
            _state_candidate(
                node_id,
                "LIST_ITEM",
                semantic_level=level,
                positive_evidence=[{"kind": "heuristic", "source": "marker_without_definition"}],
                negative_evidence=negative,
            ),
        ]

    status = evidence.get("numbering_status")
    has_definition = _has_numbering_definition(evidence)
    visible_marker = evidence.get("visible_marker")
    if status == "ignored" and not has_definition:
        negative = [{"kind": "contract", "reason": "numbering_ignored"}]
        return [
            _state_candidate(
                node_id,
                "OUTSIDE_LIST",
                semantic_level=level,
                positive_evidence=[{"kind": "contract", "source": "ignored_numbering"}],
                negative_evidence=negative,
            ),
            _state_candidate(
                node_id,
                "LIST_ITEM",
                semantic_level=level,
                positive_evidence=[{"kind": "heuristic", "source": "marker_without_definition"}],
                negative_evidence=negative,
            ),
        ]
    if has_definition or status == "detected" or isinstance(visible_marker, Mapping):
        return [
            _state_candidate(
                node_id,
                "LIST_ITEM",
                semantic_level=level,
                positive_evidence=[{"kind": "structural", "source": "source_facts_list_item"}],
            ),
            _state_candidate(
                node_id,
                "OUTSIDE_LIST",
                semantic_level=level,
                positive_evidence=[{"kind": "contextual", "source": "marker_without_confirmation"}],
            ),
        ]
    # The block carries a numbering definition reference that does not resolve to
    # a usable definition and the parser neither detected nor downgraded it, so
    # the marker's status is genuinely unconfirmed: AMBIGUOUS and OUTSIDE_LIST
    # carry the same evidence strength and the Resolver records the conflict
    # instead of silently promoting or dropping the block.
    low_confidence = (
        "numbering_definition_ref" in evidence
        and not has_definition
        and not isinstance(visible_marker, Mapping)
        and status not in {"detected", "ignored"}
    )
    if low_confidence:
        ambiguous_negative = [{"kind": "heuristic", "reason": "low_confidence_marker"}]
        return [
            _state_candidate(
                node_id,
                "AMBIGUOUS",
                semantic_level=level,
                positive_evidence=[{"kind": "heuristic", "source": "low_confidence_marker"}],
                negative_evidence=ambiguous_negative,
            ),
            _state_candidate(
                node_id,
                "OUTSIDE_LIST",
                semantic_level=level,
                positive_evidence=[{"kind": "heuristic", "source": "unconfirmed_marker"}],
            ),
        ]
    return [
        _state_candidate(
            node_id,
            "LIST_ITEM",
            semantic_level=level,
            positive_evidence=[{"kind": "structural", "source": "marker_evidence"}],
        ),
        _state_candidate(
            node_id,
            "OUTSIDE_LIST",
            semantic_level=level,
            positive_evidence=[{"kind": "contextual", "source": "marker_without_confirmation"}],
        ),
    ]


def _build_state_candidates(
    facts: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Encode the scored candidate-state set for every source block.

    Every block is assessed from its raw SourceFacts evidence alone.  The state
    set is produced before, and independently of, the list grouping that the
    arbitrated decisions later materialize, so the Resolver arbitrates genuine
    competing alternatives rather than re-confirming a decision already taken.

    Returns the candidate states together with the node ids whose numbering
    evidence was merely low confidence: those blocks stay body text and their
    decision is published as a ``low_confidence_list_marker`` downgrade.
    """

    parents = _parent_map(facts)
    downgrade_codes = {
        str(item.get("node_id")): str(item.get("code"))
        for item in _contract_downgrades(facts)
        if item.get("node_id")
    }
    candidates: list[dict[str, Any]] = []
    low_confidence_nodes: list[str] = []

    def walk(node_id: str, owner_item_id: str | None = None) -> None:
        for child_id in facts["children"].get(node_id, []):
            child_key = str(child_id)
            kind = _node_kind(facts, child_id)
            evidence = facts["evidence"].get(child_id, {})
            if kind == "list_item":
                nested = any(
                    _node_kind(facts, ancestor) == "list_item"
                    for ancestor in _ancestors(child_key, parents)
                )
                candidates.append(
                    _state_candidate(
                        child_key,
                        "NESTED_LIST_ITEM" if nested else "LIST_ITEM",
                        list_kind=_list_family(evidence, facts["containers"].get(child_id, {})),
                        semantic_level=0,
                        positive_evidence=[{"kind": "structural", "source": "explicit_list_container"}],
                    )
                )
                walk(child_key, owner_item_id=child_key)
                continue
            if child_id in facts["units"]:
                if owner_item_id is not None:
                    candidates.append(
                        _state_candidate(
                            child_key,
                            "ITEM_CONTINUATION",
                            parent_item_id=owner_item_id,
                            positive_evidence=[{"kind": "structure", "source": "explicit_item_content"}],
                        )
                    )
                else:
                    # Every unit outside a list item, paragraph or not, keeps the
                    # states its raw evidence supports.  A non-paragraph unit
                    # resolves to a protected ``OUTSIDE_LIST`` candidate here.
                    # The implicit run's tail ownership pass is the only place
                    # that suppresses the competing ``OUTSIDE_LIST`` candidate,
                    # and only for the specific block it owns, so a
                    # non-paragraph block that is not a run tail keeps its
                    # protected state instead of losing every state decision.
                    candidates.extend(
                        _state_candidates_for_paragraph(
                            child_key,
                            facts["units"][child_id],
                            evidence,
                            downgrade_codes,
                            low_confidence_nodes=low_confidence_nodes,
                        )
                    )
                walk(child_id, owner_item_id=owner_item_id)
                continue
            if owner_item_id is not None and kind != "list":
                candidates.append(
                    _state_candidate(
                        child_key,
                        "ITEM_CHILD_BLOCK",
                        parent_item_id=owner_item_id,
                        positive_evidence=[{"kind": "structure", "source": "explicit_item_child_block"}],
                    )
                )
            walk(child_id, owner_item_id=owner_item_id)

    walk("root")
    return candidates, low_confidence_nodes


def _implicit_list_runs_for_parent(
    facts: Mapping[str, Any],
    parent_id: str,
    decided_states: Mapping[str, str],
) -> list[list[str]]:
    """Return the materialization runs of decided implicit list items for one parent."""

    if _node_kind(facts, parent_id) in {"list", "list_item"}:
        return []
    runs: list[list[str]] = []
    current: list[str] = []
    for child_id in facts["children"].get(parent_id, []):
        if child_id not in facts["units"]:
            continue
        if decided_states.get(str(child_id)) in {"LIST_ITEM", "NESTED_LIST_ITEM"}:
            current.append(str(child_id))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _tail_content_owner(
    run: list[str],
    item_levels: Mapping[str, Any],
) -> str | None:
    """Return the item that owns a run's trailing content.

    A body block that follows a list run belongs to the item at the block's own
    level: for a plain body paragraph that is the run's level-0 item (README
    constraint 5 keeps body content inside the list instead of terminating it).
    The last matching item wins, so a tail paragraph after ``[item][body]`` is a
    continuation of that item and a tail after ``[parent][child][body]`` is a
    continuation of the parent, not of the deepest child.
    """

    owner: str | None = None
    for item_id in run:
        if item_levels.get(item_id) == 0:
            owner = item_id
    if owner is None and run:
        owner = run[-1]
    return owner


def _implicit_tail_content(
    facts: Mapping[str, Any],
    parent_id: str,
    run: list[str],
    item_levels: Mapping[str, Any],
    decided_states: Mapping[str, str],
) -> tuple[str | None, list[tuple[str, str]]]:
    """Return ``(owner_item_id, [(node_id, state)])`` for a run's tail blocks.

    The tail is the contiguous block of non-list children that follows an
    implicit list run inside the same parent, up to the next list item, a list
    container, a heading or other protected block, or the end of the parent's
    children.  A paragraph is an ``ITEM_CONTINUATION``; any other block (table /
    image / quote / code / note) is an ``ITEM_CHILD_BLOCK``.  The scan stops at a
    protected or low-confidence block, so a heading or an unconfirmed numbering
    marker still ends the list instead of being absorbed (README constraint 6).
    """

    children = facts["children"].get(parent_id, [])
    run_set = {str(item_id) for item_id in run}
    last_run_index = -1
    for index, child_id in enumerate(children):
        if str(child_id) in run_set:
            last_run_index = index
    if last_run_index < 0:
        return None, []
    owner = _tail_content_owner(run, item_levels)
    tails: list[tuple[str, str]] = []
    for child_id in children[last_run_index + 1:]:
        child_key = str(child_id)
        if child_id in facts["units"]:
            kind = str(facts["units"][child_id].get("kind") or "")
            if decided_states.get(child_key) in {"LIST_ITEM", "NESTED_LIST_ITEM"}:
                break
            if kind != "paragraph":
                # A code block or other non-paragraph unit is content of the item.
                tails.append((child_key, "ITEM_CHILD_BLOCK"))
                continue
            evidence = facts["evidence"].get(child_id, {})
            if evidence.get("marker_kind"):
                # A block with its own marker is a candidate list item, not the
                # content of the preceding one.
                break
            if _numbering_is_low_confidence(evidence):
                break
            protected, _reason = _is_protected(facts["units"][child_id], evidence)
            if protected:
                break
            state = "ITEM_CONTINUATION"
        else:
            kind = str(facts["containers"].get(child_id, {}).get("kind") or "")
            if kind not in {"table", "quote"}:
                break
            state = "ITEM_CHILD_BLOCK"
        tails.append((child_key, state))
    return owner, tails


def _numbering_sequence(evidence: Mapping[str, Any]) -> str | None:
    """Return a stable identity for the numbering instance a block belongs to.

    The numId together with its abstractNumId is the DOCX signal that a run of
    items belongs to one numbering instance.  A block whose definition resolves
    to no numId falls back to its ``sequence`` evidence, and a block with
    neither has no instance identity and cannot anchor or join a sub-list.
    """

    num_id = evidence.get("num_id")
    abstract_id = evidence.get("abstract_num_id")
    reference = evidence.get("numbering_definition_ref")
    if isinstance(reference, Mapping):
        if num_id is None:
            num_id = reference.get("num_id")
        if abstract_id is None:
            abstract_id = reference.get("abstract_num_id")
    if num_id is not None:
        return f"num:{num_id}:abstract:{abstract_id}"
    sequence = evidence.get("sequence")
    return f"sequence:{sequence}" if sequence else None


def _normalized_lvl_text(evidence: Mapping[str, Any]) -> str | None:
    """Normalize placeholder indexes while preserving visible punctuation."""

    value = evidence.get("lvl_text")
    if not isinstance(value, str):
        return None
    return re.sub(r"%\d+", "%n", value)


def _trailing_physical_run(group: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the group's trailing run of items sharing one numbering instance.

    A merged group can span several ``numId`` values, so the run that a
    continuation has to continue is the maximal suffix of items whose numbering
    instance equals the last item's.  The expected next ordinal is that run's
    first ordinal plus the run length, matching the legacy group reconstruction.
    """

    items = list(group.get("items") or [])
    if not items:
        return []
    last_identity = _numbering_sequence(items[-1]["evidence"])
    run: list[Mapping[str, Any]] = []
    for item in reversed(items):
        if _numbering_sequence(item["evidence"]) != last_identity:
            break
        run.append(item)
    run.reverse()
    return run


def _sequence_continues_open_group(
    group: Mapping[str, Any],
    evidence: Mapping[str, Any],
    family: str | None,
) -> bool:
    """Return whether an item continues the open group across an instance change.

    A block whose numbering instance differs from the open group is not
    automatically a sub-list.  When it does not restart (``restart_hint`` is
    explicitly false), its numbered format matches the open group and its
    ordinal continues the open group's trailing run it is the same logical
    sequence split across ``numId`` values, so it continues the open list
    instead of nesting under it (README constraint 8).  Format consistency is
    ``num_fmt`` plus the visible ``lvlText`` template with placeholder indexes
    normalized; ordinal continuity is that the block's ordinal equals the open
    group's trailing run first ordinal plus the run length.  The native rule
    therefore stands on its own instead of inheriting the legacy
    ``list_detector._physical_groups_continue`` ordinal gate.
    """

    if evidence.get("restart_hint") is not False:
        return False
    if group.get("list_kind") != family:
        return False
    if group.get("instance_num_fmt") != evidence.get("num_fmt"):
        return False
    if group.get("instance_lvl_text") != _normalized_lvl_text(evidence):
        return False
    run = _trailing_physical_run(group)
    first_ordinal = run[0]["evidence"].get("ordinal") if run else None
    current_ordinal = evidence.get("ordinal")
    if isinstance(first_ordinal, int) and isinstance(current_ordinal, int):
        if current_ordinal != first_ordinal + len(run):
            return False
    return True


def _restart_policy_for_level(level: int, evidence: Mapping[str, Any]) -> str | None:
    """Return the sub-list restart policy for a nested level.

    ``w:lvlRestart=0`` means the level never restarts, so a sub-list keeps its
    numbering across parents.  Any other value, including an absent
    ``lvlRestart``, keeps the OOXML default: the level restarts when its parent
    level is used again.  A top-level group has no restart policy.
    """

    if level <= 0:
        return None
    if evidence.get("lvl_restart") == 0:
        return "continue_across_parents"
    return "restart_with_parent"


def _resolve_sublist(
    active_groups: Mapping[int, Mapping[str, Any]],
    active_items: Mapping[int, str],
    sequence: str | None,
    family: str | None,
) -> tuple[int, str | None] | None:
    """Return the (level, parent item) a new-instance level-0 block nests at.

    A block declared at level 0 whose numbering instance differs from the open
    level-0 list is not a sibling top-level item: it opens or continues a
    sub-list under an open item, provided the category matches.  A block whose
    category differs from the open level-0 list is a same-level category switch
    instead (README constraint 9 restarts the list rather than nesting it), and
    a block that shares the open level-0 instance stays a sibling.  When an open
    sub-list already carries the same instance and category the block continues
    it at that level; otherwise the block opens a new sub-list one level under
    the deepest open item.
    """

    if sequence is None or family is None or not active_items:
        return None
    top = active_groups.get(0)
    if top is None or top.get("sequence_identity") in (None, sequence):
        return None
    if top.get("list_kind") != family:
        return None
    matching = sorted(
        level
        for level, group in active_groups.items()
        if level > 0 and group.get("sequence_identity") == sequence and group.get("list_kind") == family
    )
    if matching:
        level = matching[0]
        return level, active_groups[level].get("parent_item_id")
    deepest = max(active_items)
    return deepest + 1, active_items[deepest]


def _structural_list_level(
    container_id: str,
    facts: Mapping[str, Any],
    parents: Mapping[str, str],
) -> int:
    """Return the number of ancestor ``list`` containers of one container."""

    level = 0
    current = parents.get(container_id)
    while current is not None:
        if _node_kind(facts, current) == "list":
            level += 1
        current = parents.get(current)
    return level


def _implicit_list_runs(
    facts: Mapping[str, Any],
    decided_states: Mapping[str, str],
) -> list[list[str]]:
    """Return the materialization runs of decided implicit list-item units.

    A run is a maximal sequence of consecutive source units that the Resolver
    decided are list items within one parent.  The materializer resets its
    active-parent state at every run boundary, so the constraint validator uses
    the same segmentation and the same boundaries.
    """

    runs: list[list[str]] = []
    for parent_id in sorted(facts["children"]):
        runs.extend(_implicit_list_runs_for_parent(facts, parent_id, decided_states))
    return runs


def _validate_state_constraints(
    decisions: list[dict[str, Any]],
    facts: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate the arbitrated decisions against README constraints 3 and 4.

    Constraint 3: an item at level ``L > 0`` must have an item at level
    ``L - 1`` earlier in the same materialization run.  Constraint 4: the level
    increases stepwise, so a jump of more than one from the previous item level
    in the same run is a violation.  The Resolver cannot see source order, level
    or parent, so this pass is the explicitly owned home for those two global
    constraints.  The level state resets at every run and scope boundary so the
    validator agrees with the state the materializer actually produces: a deep
    item that starts a run with no parent is reported here exactly as the
    materializer flattens it with ``orphan_list_depth_flattened``.  Every
    violation is a ``list_constraint_violation`` warning returned to the caller,
    which appends it to ``diagnostics``; the grouping independently flattens an
    orphan level or refuses publication (``unresolved_list_parent``), so a
    violating document is never silently accepted.  The decisions are not
    rewritten here.

    Explicit ``list`` containers are validated too.  Their nesting lives in the
    container tree rather than in a declared ``ilvl``: a nested list's
    structural level is the number of ancestor ``list`` containers.  A list at
    structural level ``L > 0`` must hang from a ``list_item`` of the level
    ``L - 1`` list (constraint 3).  Because the structural level increases by
    exactly one per nesting step, constraint 4 cannot be violated by a
    well-formed container tree, so a misplaced nested list is reported once, as
    constraint 3, on each of its decided items.
    """

    decided_states: dict[str, str] = {}
    decided_levels: dict[str, Any] = {}
    for decision in decisions:
        targets = decision.get("target_node_ids") or []
        if len(targets) != 1:
            continue
        node_id = str(targets[0])
        value = decision.get("value") or {}
        decided_states[node_id] = str(value.get("state"))
        decided_levels[node_id] = value.get("semantic_level")

    facts_parents = _parent_map(facts)
    findings: list[dict[str, Any]] = []
    for run in _implicit_list_runs(facts, decided_states):
        open_levels: dict[int, str] = {}
        previous_level: int | None = None
        for node_id in run:
            level = decided_levels.get(node_id)
            if not isinstance(level, int) or level < 0:
                level = 0
            # A single step jump names the whole defect: the item skipped a
            # level, which is also why its level-(L-1) parent is missing.  Report
            # it once as constraint 4 instead of also emitting the constraint-3
            # "no parent" finding for the same node.
            jump = previous_level is not None and level > previous_level + 1
            if jump:
                findings.append(
                    {
                        "code": "list_constraint_violation",
                        "severity": "warning",
                        "constraint": 4,
                        "node_id": node_id,
                        "message": f"list item {node_id} jumps from level {previous_level} to level {level}",
                        "previous_level": previous_level,
                        "declared_level": level,
                    }
                )
            if level > 0 and (level - 1) not in open_levels:
                if not jump:
                    findings.append(
                        {
                            "code": "list_constraint_violation",
                            "severity": "warning",
                            "constraint": 3,
                            "node_id": node_id,
                            "message": f"list item {node_id} at level {level} has no parent at level {level - 1}",
                            "declared_level": level,
                        }
                    )
                # A deep item with no parent sits on the local list root, matching
                # the flattening the grouping applies, so following siblings still
                # see a level-0 anchor.
                level = 0
            for stale_level in [key for key in open_levels if key >= level]:
                open_levels.pop(stale_level, None)
            open_levels[level] = node_id
            previous_level = level

    for container_id, container in facts["containers"].items():
        if container.get("kind") != "list":
            continue
        level = _structural_list_level(str(container_id), facts, facts_parents)
        if level == 0:
            continue
        if _node_kind(facts, str(facts_parents.get(str(container_id)))) == "list_item":
            continue
        for item_id in facts["children"].get(container_id, []):
            if facts["containers"].get(item_id, {}).get("kind") != "list_item":
                continue
            if decided_states.get(str(item_id)) not in {"LIST_ITEM", "NESTED_LIST_ITEM"}:
                continue
            findings.append(
                {
                    "code": "list_constraint_violation",
                    "severity": "warning",
                    "constraint": 3,
                    "node_id": str(item_id),
                    "message": f"list item {item_id} at level {level} has no parent list item at level {level - 1}",
                    "declared_level": level,
                }
            )
    return findings


def _validate_state_enum(state_candidates: list[dict[str, Any]]) -> None:
    """Reject any emitted state that is not a member of ``LIST_STATE_ENUM``."""

    invalid = sorted({str(candidate.get("state")) for candidate in state_candidates if candidate.get("state") not in LIST_STATE_ENUM})
    if invalid:
        raise ValueError(f"list state candidate emitted an unknown state: {invalid[0]!r}")


def _arbitrate_state_candidates(
    state_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reuse the shared Resolver to arbitrate the candidate-state set.

    The evidence class each candidate carries is the ordering input: the
    Resolver ranks a block's competing states by ``resolver._candidate_strength``
    over their ``positive_evidence`` and picks the strongest.  Two states that
    carry the same class for the same block are a genuine tie and the Resolver
    records an ``unresolved`` conflict instead of guessing, which is the only
    path that reaches the ambiguity report.

    Each state candidate is projected onto the existing ``CandidateSet``
    contract so the Resolver applies its global rules: one ownership per block,
    evidence-strength precedence, and an ``unresolved`` record whenever two
    equally strong states compete for the same block.
    """

    from ..resolver import resolve

    resolver_candidates: list[dict[str, Any]] = []
    for index, candidate in enumerate(state_candidates):
        resolver_candidates.append(
            {
                "candidate_id": f"list-state-{candidate['node_id']}-{candidate['state']}-{index}",
                "module_id": "list_semantics",
                "module_version": "1.0",
                "target_source_refs": [],
                "target_node_ids": [candidate["node_id"]],
                "proposal": {
                    "operation": "update_role",
                    "property_scope": [_STATE_PROPERTY_SCOPE],
                    "value": {
                        "state": candidate["state"],
                        "list_id": candidate["list_id"],
                        "semantic_level": candidate["semantic_level"],
                        "parent_item_id": candidate["parent_item_id"],
                    },
                },
                "positive_evidence": list(candidate["positive_evidence"]) or [{"kind": "heuristic"}],
                "negative_evidence": list(candidate["negative_evidence"]),
                "scope": {"stages": ["relation"]},
                "constraints": [],
                "rationale": "list state candidate",
            }
        )
    decision_set = resolve(resolver_candidates, {"revision": 1}, None)
    return list(decision_set.get("decisions", [])), list(decision_set.get("unresolved", []))


def analyze_lists(facts: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Generate list groups and relation candidates from SourceFacts."""

    validate_source_facts(facts)
    parents = _parent_map(facts)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "completed",
        "lists": [],
        "items": [],
        "continuations": [],
        "child_blocks": [],
        "owned_content": [],
        "candidates": [],
        "diagnostics": [],
        "downgrades": [],
        "context": copy.deepcopy(dict(context or {})),
    }

    for diagnostic in _contract_downgrades(facts):
        result["diagnostics"].append(diagnostic)
        result["downgrades"].append(copy.deepcopy(diagnostic))

    state_candidates, low_confidence_nodes = _build_state_candidates(facts)
    _validate_state_enum(state_candidates)
    result["state_candidates"] = state_candidates
    # A block whose numbering evidence was merely low confidence stays body text.
    # ``list_detector`` already resolved it to a body paragraph, so this is not a
    # Resolver conflict and must not leave ``list_semantics.status`` unresolved or
    # turn a clean document into ``completed_with_warnings``.  It is recorded as
    # an info downgrade so the decision is visible in the published report.
    for node_id in low_confidence_nodes:
        evidence = facts["evidence"].get(node_id, {})
        result["downgrades"].append(
            {
                "code": "low_confidence_list_marker",
                "severity": "info",
                "message": f"list-like paragraph {node_id} stayed a body paragraph because its numbering evidence is low confidence",
                "node_id": node_id,
                "numbering_status": evidence.get("numbering_status"),
                "source_evidence": _source_numbering_evidence(evidence),
                "fallback": {"kind": "body_paragraph", "preserve_source_content": True},
            }
        )

    # Arbitrate the block states first so the grouping materializes the verdict.
    # A second arbitration pass below adds the tail-content states of implicit
    # runs; it never changes a list-item decision because tail content is never a
    # list item.
    state_decisions, state_unresolved = _arbitrate_state_candidates(state_candidates)
    decided_states = {
        str(decision["target_node_ids"][0]): str((decision.get("value") or {}).get("state"))
        for decision in state_decisions
        if decision.get("target_node_ids")
    }
    decided_levels = {
        str(decision["target_node_ids"][0]): (decision.get("value") or {}).get("semantic_level")
        for decision in state_decisions
        if decision.get("target_node_ids")
    }

    def ordered_list_ids() -> list[str]:
        ordered: list[str] = []

        def walk(node_id: str) -> None:
            for child_id in facts["children"].get(node_id, []):
                kind = facts["containers"].get(child_id, {}).get("kind")
                if kind == "list":
                    ordered.append(child_id)
                if child_id in facts["containers"]:
                    walk(child_id)

        walk("root")
        return ordered

    for container_id in ordered_list_ids():
        container = facts["containers"][container_id]
        if container.get("kind") != "list":
            continue
        direct_items = [
            child
            for child in facts["children"].get(container_id, [])
            if facts["containers"].get(child, {}).get("kind") == "list_item"
            and decided_states.get(str(child)) in {"LIST_ITEM", "NESTED_LIST_ITEM"}
        ]
        ancestor_ids = _ancestors(container_id, parents)
        parent_item_id = next((node_id for node_id in reversed(ancestor_ids) if facts["containers"].get(node_id, {}).get("kind") == "list_item"), None)
        item_records = [{"item_id": item_id, "evidence": facts["evidence"].get(item_id, {})} for item_id in direct_items]
        list_kind = _list_family({}, container)
        if list_kind is None:
            diagnostic = {
                "code": "unknown_list_kind",
                "severity": "info",
                "message": f"explicit list container {container_id} has no supported list kind; its source content was preserved",
                "node_id": container_id,
                "fallback": {"kind": "source_container", "preserve_source_content": True},
            }
            result["diagnostics"].append(diagnostic)
            result["downgrades"].append(copy.deepcopy(diagnostic))
            continue
        _emit_group(
            result,
            facts,
            list_id=container_id,
            list_kind=list_kind,
            source_sequence_id=str(container.get("source_sequence_id") or container_id),
            source_start=max(1, int(container.get("source_start") or 1)),
            item_records=item_records,
            scope_id=_scope_id(ancestor_ids, facts),
            parent_item_id=parent_item_id,
            explicit=True,
            decided_states=decided_states,
        )

    for parent_id in sorted(facts["children"]):
        if _node_kind(facts, parent_id) in {"list", "list_item"}:
            continue
        for unit_id in facts["children"].get(parent_id, []):
            if unit_id not in facts["units"]:
                continue
            unit = facts["units"][unit_id]
            evidence = facts["evidence"].get(unit_id, {})
            protected, reason = _is_protected(unit, evidence)
            if evidence.get("marker_kind") and protected:
                result["diagnostics"].append(
                    {"code": "protected_list_like_text", "severity": "info", "message": reason or "protected content", "node_id": unit_id}
                )
        # Run membership comes from the arbitrated state decision, so the legacy
        # grouping materializes the Resolver verdict instead of deciding list
        # membership a second time.  The constraint validator consumes the same
        # runs, so the two passes agree on the run and scope boundaries.
        for run in _implicit_list_runs_for_parent(facts, parent_id, decided_states):
            group_specs: list[dict[str, Any]] = []
            active_items: dict[int, str] = {}
            active_groups: dict[int, dict[str, Any]] = {}
            seen_item_in_run = False
            flattened_source_depths: set[int] = set()
            pending_orphans: list[tuple[str, int]] = []
            # The last open group per (numbering instance, depth), used to
            # continue a sub-list across parents when ``w:lvlRestart=0`` tells us
            # the level never restarts.
            continuation_groups: dict[tuple[Any, int], dict[str, Any]] = {}
            # How many groups this run created per (sequence, kind, parent,
            # depth) slot, used to give split groups a distinguishable
            # ``sequence_split_index``.
            sequence_split_counts: dict[Any, int] = {}
            for unit_id in run:
                evidence = facts["evidence"].get(unit_id, {})
                source_depth = evidence.get("explicit_depth") if isinstance(evidence.get("explicit_depth"), int) else 0
                decided_level = decided_levels.get(unit_id)
                if isinstance(decided_level, int) and decided_level >= 0:
                    declared_depth = decided_level
                else:
                    declared_depth = _raw_depth(evidence)
                depth = declared_depth
                family = _list_family(evidence)
                parent_item_id = active_items.get(depth - 1) if depth > 0 else None
                # A block declared at level 0 whose numbering instance differs
                # from the open level-0 list opens or continues a sub-list under
                # an open item instead of starting a sibling top-level list.
                # A block that continues the open sequence across instances is
                # one logical list and must not be turned into a sub-list
                # (README constraint 8).
                if depth == 0:
                    open_group = active_groups.get(0)
                    if open_group is None or not _sequence_continues_open_group(
                        open_group, evidence, family
                    ):
                        sublist = _resolve_sublist(
                            active_groups, active_items, _numbering_sequence(evidence), family
                        )
                        if sublist is not None:
                            depth, parent_item_id = sublist
                if depth > 0 and parent_item_id is None:
                    if source_depth in flattened_source_depths:
                        depth = 0
                        parent_item_id = None
                    elif seen_item_in_run:
                        result["diagnostics"].append({
                            "code": "unresolved_list_parent",
                            "severity": "warning",
                            "message": f"list item {unit_id} has no evidenced parent at depth {depth}",
                            "node_id": unit_id,
                        })
                    else:
                        result["diagnostics"].append({
                            "code": "orphan_list_depth_flattened",
                            "severity": "info",
                            "message": f"list item {unit_id} starts at depth {depth} without an evidenced parent and was flattened to the local list root",
                            "node_id": unit_id,
                            "source_depth": depth,
                            "fallback": {"kind": "local_list_root", "preserve_source_content": True},
                        })
                        pending_orphans.append((unit_id, depth))
                        depth = 0
                        parent_item_id = None
                        flattened_source_depths.add(source_depth)
                # A shallow item that appears after a run-initial deep item is
                # that item's late parent: report it instead of leaving the
                # child silently attached to the nearest item.
                if declared_depth <= depth:
                    for pending_id, pending_level in list(pending_orphans):
                        if depth < pending_level:
                            result["diagnostics"].append({
                                "code": "late_list_parent",
                                "severity": "warning",
                                "message": f"list item {pending_id} at depth {pending_level} is followed by a shallower item at depth {depth}",
                                "node_id": pending_id,
                                "declared_level": pending_level,
                                "parent_appears_after": True,
                            })
                            pending_orphans.remove((pending_id, pending_level))
                seen_item_in_run = True
                if family is None:
                    for old_depth in [key for key in active_items if key >= depth]:
                        active_items.pop(old_depth, None)
                        active_groups.pop(old_depth, None)
                    diagnostic = {
                        "code": "unknown_list_marker_kind",
                        "severity": "info",
                        "message": f"list-like paragraph {unit_id} has an unsupported marker kind; its source content was preserved",
                        "node_id": unit_id,
                        "source_evidence": _source_numbering_evidence(evidence),
                        "fallback": {"kind": "body_paragraph", "preserve_source_content": True},
                    }
                    result["diagnostics"].append(diagnostic)
                    result["downgrades"].append(copy.deepcopy(diagnostic))
                    continue
                effective_num_id = evidence.get("num_id") or (evidence.get("numbering_definition_ref") or {}).get("num_id")
                effective_abstract_id = evidence.get("abstract_num_id") or (evidence.get("numbering_definition_ref") or {}).get("abstract_num_id")
                effective_sequence = evidence.get("sequence") or json_sequence_id(evidence.get("numbering_definition_ref"), unit_id)
                sequence_identity = _numbering_sequence(evidence)
                current = active_groups.get(depth)
                # A block that reuses the open numbering instance but restarts is
                # a different logical list: the definition is reused, so the
                # sequence splits instead of extending the open list (README
                # constraint 8).
                split_sequence = (
                    current is not None
                    and current["list_kind"] == family
                    and current.get("parent_item_id") == parent_item_id
                    and current.get("sequence_identity") == sequence_identity
                    and bool(current["items"])
                    and bool(evidence.get("restart_hint"))
                )
                if split_sequence:
                    current = None
                restart_policy = _restart_policy_for_level(depth, evidence)
                sequence_start = max(1, int(evidence.get("start_value") or evidence.get("ordinal") or 1))
                if restart_policy == "continue_across_parents":
                    previous_group = continuation_groups.get((sequence_identity, depth))
                    if previous_group is not None:
                        sequence_start = int(previous_group.get("start_value", 1)) + len(previous_group["items"])
                # Group identity stays ``list_kind`` plus parent.  A numbering
                # instance change is a continuation only when the continuation
                # evidence holds; a merge is recorded only then, and any other
                # instance change splits into a new sequence (README constraint
                # 8).  The depth>0 path applies the same continuation evidence as
                # the depth==0 path, so a restarted sibling sub-list splits
                # instead of being merged unconditionally.
                group_key_matches = (
                    current is not None
                    and current["list_kind"] == family
                    and current.get("parent_item_id") == parent_item_id
                )
                instance_changed = (
                    current is not None
                    and current.get("sequence_identity") != sequence_identity
                )
                continues_open_group = (
                    not split_sequence
                    and group_key_matches
                    and instance_changed
                    and _sequence_continues_open_group(current, evidence, family)
                )
                if current is None or not group_key_matches or (instance_changed and not continues_open_group):
                    if split_sequence:
                        group_boundary = {
                            "kind": "sequence_split",
                            "depth": depth,
                            "sequence": sequence_identity,
                            "previous_list_id": active_groups.get(depth, {}).get("list_id"),
                        }
                    elif current is None:
                        group_boundary = {"kind": "run_start", "depth": depth}
                    elif current["list_kind"] != family:
                        group_boundary = {
                            "kind": "same_level_list_kind_switch",
                            "depth": depth,
                            "from": current["list_kind"],
                            "to": family,
                            "previous_list_id": current["list_id"],
                        }
                    elif current.get("parent_item_id") != parent_item_id:
                        group_boundary = {
                            "kind": "parent_item_switch",
                            "depth": depth,
                            "previous_list_id": current["list_id"],
                            "previous_parent_item_id": current.get("parent_item_id"),
                            "parent_item_id": parent_item_id,
                        }
                    else:
                        # A different numbering instance that does not continue
                        # the open sequence is a restarted sibling list.  Split it
                        # into a new sequence with the same boundary kind the
                        # depth-0 reuse split emits.
                        group_boundary = {
                            "kind": "sequence_split",
                            "depth": depth,
                            "sequence": sequence_identity,
                            "previous_list_id": current["list_id"],
                        }
                    split_key = (effective_sequence, family, parent_item_id, depth)
                    split_index = sequence_split_counts.get(split_key, 0)
                    sequence_split_counts[split_key] = split_index + 1
                    current = {
                        "list_id": f"implicit-list-{unit_id}-{len(group_specs)}",
                        "list_kind": family,
                        "source_sequence_id": effective_sequence,
                        "source_start": sequence_start,
                        "start_value": sequence_start,
                        "scope_id": _scope_id([*_ancestors(parent_id, parents), parent_id], facts),
                        "parent_item_id": parent_item_id,
                        "items": [],
                        "group_boundary": group_boundary,
                        "effective_num_id": effective_num_id,
                        "effective_abstract_id": effective_abstract_id,
                        "sequence_identity": sequence_identity,
                        "instance_num_fmt": evidence.get("num_fmt"),
                        "instance_lvl_text": _normalized_lvl_text(evidence),
                        "restart_policy": restart_policy,
                        "restart_scope": _restart_scope_from_evidence(evidence, effective_num_id, effective_abstract_id),
                        "sequence_split_index": split_index,
                    }
                    group_specs.append(current)
                    active_groups[depth] = current
                    if restart_policy is not None:
                        continuation_groups[(sequence_identity, depth)] = current
                elif instance_changed:
                    # The block continues the open group across a numbering
                    # instance change: record the merged instance while the group
                    # keeps its first instance as the canonical identity, so the
                    # split list is published as one sequence (README constraint 8).
                    merged = current.setdefault("merged_sequences", [])
                    if effective_sequence is not None and effective_sequence not in merged:
                        merged.append(effective_sequence)
                current["items"].append({
                    "item_id": unit_id,
                    "evidence": evidence,
                    "parent_item_id": parent_item_id,
                    "effective_depth": depth,
                })
                active_items[depth] = unit_id
                for old_depth in [key for key in active_items if key > depth]:
                    active_items.pop(old_depth, None)
                    active_groups.pop(old_depth, None)
            for group in group_specs:
                first_evidence = group["items"][0]["evidence"] if group["items"] else {}
                restart_expression = {
                    "start_value": group.get("start_value", group["source_start"]),
                    "sequence": group["source_sequence_id"],
                    "restart_scope": evidence_lookup_restart_scope(group),
                    "num_id": group.get("effective_num_id"),
                    "abstract_num_id": group.get("effective_abstract_id"),
                    "lvl_restart": first_evidence.get("lvl_restart"),
                    "start_override": first_evidence.get("start_override"),
                    "restart_policy": group.get("restart_policy"),
                }
                if group.get("merged_sequences"):
                    restart_expression["merged_sequences"] = list(group["merged_sequences"])
                restart_expression["sequence_split_index"] = group.get("sequence_split_index", 0)
                _emit_group(
                    result,
                    facts,
                    list_id=group["list_id"],
                    list_kind=group["list_kind"],
                    source_sequence_id=group["source_sequence_id"],
                    source_start=group["source_start"],
                    item_records=group["items"],
                    scope_id=group["scope_id"],
                    parent_item_id=group["parent_item_id"],
                    explicit=False,
                    group_boundary=group.get("group_boundary"),
                    restart_expression=restart_expression,
                    decided_states=decided_states,
                    sequence_split_index=group.get("sequence_split_index", 0),
                )

    # Tail content of implicit runs.  A body paragraph, table, image, quote,
    # code or note block that follows an implicit list run is content of a list
    # item instead of an independent body block (README constraint 5).  The
    # ownership is a candidate state offered to the same Resolver, so the block
    # carries an explicit ITEM_CONTINUATION / ITEM_CHILD_BLOCK decision and the
    # materializer reads one ownership seam for both implicit and explicit items.
    item_by_id = {str(item["item_id"]): item for item in result["items"]}
    item_levels = {item_id: item.get("semantic_level") for item_id, item in item_by_id.items()}
    tail_candidates: list[dict[str, Any]] = []
    for parent_id in sorted(facts["children"]):
        if _node_kind(facts, parent_id) in {"list", "list_item"}:
            continue
        for run in _implicit_list_runs_for_parent(facts, parent_id, decided_states):
            owner, tails = _implicit_tail_content(facts, parent_id, run, item_levels, decided_states)
            if owner is None or owner not in item_by_id:
                continue
            owner_relation = item_by_id[owner]
            for node_id, state in tails:
                tail_candidates.append(
                    _state_candidate(
                        node_id,
                        state,
                        list_id=owner_relation["list_id"],
                        parent_item_id=owner,
                        positive_evidence=[{"kind": "structure", "source": "implicit_list_tail_content"}],
                    )
                )
                entry = {
                    "node_id": node_id,
                    "owner_item_id": owner,
                    "list_id": owner_relation["list_id"],
                    "scope_id": owner_relation["scope_id"],
                }
                result.setdefault("owned_content", []).append({**entry, "state": state})
                if state == "ITEM_CONTINUATION":
                    result["continuations"].append(entry)
                else:
                    result["child_blocks"].append(entry)

    if tail_candidates:
        # A non-paragraph unit that is the tail of an implicit run is owned by
        # the item, so its own protected ``OUTSIDE_LIST`` candidate would tie
        # with the ownership state at equal evidence strength.  Suppress the
        # competing candidate only for those tail blocks.  A non-paragraph block
        # that is not a run tail keeps the protected ``OUTSIDE_LIST`` state it
        # was offered, so the removal stays scoped to the tie it fixes.
        tail_node_ids = {str(candidate["node_id"]) for candidate in tail_candidates}
        non_paragraph_tails = {
            node_id
            for node_id in tail_node_ids
            if str(facts["units"].get(node_id, {}).get("kind") or "") != "paragraph"
        }
        state_candidates = [
            candidate
            for candidate in state_candidates
            if not (
                str(candidate["node_id"]) in non_paragraph_tails
                and candidate["state"] == "OUTSIDE_LIST"
            )
        ]
        state_candidates = [*state_candidates, *tail_candidates]
        _validate_state_enum(state_candidates)
        state_decisions, state_unresolved = _arbitrate_state_candidates(state_candidates)
        decided_states = {
            str(decision["target_node_ids"][0]): str((decision.get("value") or {}).get("state"))
            for decision in state_decisions
            if decision.get("target_node_ids")
        }

    constraint_findings = _validate_state_constraints(state_decisions, facts)
    result["state_candidates"] = state_candidates
    result["state_decisions"] = state_decisions
    result["state_unresolved"] = state_unresolved
    result["state_constraint_findings"] = constraint_findings
    # The constraint findings are warnings on ``diagnostics`` so they set
    # ``list_semantics.status`` to ``unresolved`` and reach the publisher's
    # fatal/blocking set, instead of staying an unobservable internal list.
    result["diagnostics"].extend(copy.deepcopy(constraint_findings))

    relation_by_id = {item["item_id"]: item for item in result["items"]}
    counters: dict[str, int] = {}
    visited: set[str] = set()

    def assign_in_reading_order(node_id: str) -> None:
        if node_id in relation_by_id and node_id not in visited:
            relation = relation_by_id[node_id]
            visited.add(node_id)
            if relation["list_kind"] == "ordered":
                scope_id = relation["scope_id"]
                counters[scope_id] = counters.get(scope_id, 0) + 1
                relation["target_ordinal"] = counters[scope_id]
        for child_id in facts["children"].get(node_id, []):
            assign_in_reading_order(child_id)

    assign_in_reading_order("root")
    for entry in state_unresolved:
        target_ids = entry.get("target_node_ids") or []
        # The ambiguity is an informational diagnostic: it sets the list
        # semantic status to ``unresolved`` and is published in
        # ``list_semantics.diagnostics``, but it does not by itself block a
        # document that the parser could not confirm as a list.  The block is
        # preserved as a body paragraph instead of being silently guessed.
        result["diagnostics"].append({
            "code": "ambiguous_list_state",
            "severity": "info",
            "message": "list state candidates conflict; the ambiguity report retains both",
            "node_id": target_ids[0] if target_ids else None,
            "candidate_ids": list(entry.get("candidate_ids") or []),
            "conflict_code": entry.get("code"),
        })
    result["diagnostics"].sort(key=lambda item: (item.get("code", ""), item.get("node_id", "")))
    if result["diagnostics"]:
        result["status"] = "unresolved"
    return result


def json_sequence_id(numbering_definition_ref: Any, fallback: str) -> str:
    if isinstance(numbering_definition_ref, Mapping):
        values = [str(numbering_definition_ref.get(key)) for key in ("num_id", "abstract_num_id") if numbering_definition_ref.get(key) is not None]
        if values:
            return "docx-sequence-" + "-".join(values)
    return f"source-sequence-{fallback}"


def analyze(facts: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return analyze_lists(facts, context=context)


def build_list_candidates(facts: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return analyze_lists(facts, context=context)


def recognize_list_semantics(
    facts: Mapping[str, Any],
    source_ast: Mapping[str, Any],
) -> dict[str, Any]:
    """Own the whole list concept: facts result, AST candidates and item map.

    ``result`` is the canonical list semantics over SourceFacts and
    ``candidates`` are the AST proposals that materialize it.  ``item_map``
    records the native list container that owns each source list item, which is
    the published ``list_item_map`` capability consumed by appendix semantics.
    """

    from .source_ast import materialize_list_semantics

    result = analyze_lists(facts)
    _materialized, candidates, _native_item_map = materialize_list_semantics(
        source_ast,
        facts,
        result,
    )
    item_map = {
        str(item["item_id"]): str(item["list_id"])
        for item in result.get("items", [])
        if item.get("item_id") and item.get("list_id")
    }
    return {"result": result, "candidates": candidates, "item_map": item_map}
