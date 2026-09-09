"""Deterministic cross-module candidate resolution."""

from __future__ import annotations

import hashlib
import json
import copy
from collections import defaultdict
from typing import Any, Mapping, Sequence


_EVIDENCE_STRENGTH = {
    "explicit_user_override": 5,
    "user_override": 5,
    "format_contract": 4,
    "contract": 4,
    "structural": 3,
    "structure": 3,
    "contextual": 2,
    "heuristic": 1,
    "llm": 0,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_strength(candidate: Mapping[str, Any]) -> int:
    evidence = list(candidate.get("positive_evidence") or [])
    strengths = []
    for item in evidence:
        if isinstance(item, Mapping):
            kind = str(item.get("kind") or item.get("category") or "heuristic")
            strengths.append(_EVIDENCE_STRENGTH.get(kind, 1))
    return max(strengths, default=1)


def _group_key(candidate: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    proposal = candidate.get("proposal") or {}
    target_ids = tuple(sorted(str(value) for value in candidate.get("target_node_ids") or []))
    scopes = tuple(sorted(str(value) for value in proposal.get("property_scope") or candidate.get("property_scope") or []))
    return target_ids, scopes


def _active_ledger_revisions(ledger_view: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not ledger_view:
        return []
    revisions = ledger_view.get("active_revisions")
    if revisions is None:
        revisions = [item for item in ledger_view.get("revisions", []) if item.get("status") == "active"]
    return [item for item in revisions if isinstance(item, Mapping)]


def _decision_from_candidate(candidate: Mapping[str, Any], expected_revision: int) -> dict[str, Any]:
    proposal = dict(candidate.get("proposal") or {})
    operation = proposal.get("operation")
    if operation is None:
        operation = "update_role" if "role" in proposal or "role" in proposal.get("value", {}) else "update_overlay"
    value = proposal.get("value")
    if value is None:
        value = {key: proposal[key] for key in ("role", "level", "caption_type", "target_id") if key in proposal}
    value = dict(value) if isinstance(value, Mapping) else value
    target_node_ids = list(candidate.get("target_node_ids") or [])
    if operation in {"create_overlay", "update_overlay"}:
        operation = "update_overlay"
    elif operation == "adjust_list_level":
        operation = "set_presentation_override"
        if isinstance(value, dict):
            if "level" in value and "target_level" not in value:
                value["target_level"] = value["level"]
            value.setdefault("source_semantics", "preserved")
    elif operation == "retype_list_item":
        list_item_id = value.get("list_item_id") if isinstance(value, Mapping) else None
        list_parent_id = value.get("list_parent_id") if isinstance(value, Mapping) else None
        if isinstance(list_item_id, str) and isinstance(list_parent_id, str):
            operation = "update_node_semantics"
            target_node_ids = [list_item_id]
            value = {
                "semantic_role": "list_item",
                "source_operation": "retype_list_item",
                "source_semantics": "list_item",
                "adapter_id": value.get("adapter_id", "legacy-list-retype-v1"),
                "paragraph_id": value.get("paragraph_id"),
                "list_parent_id": list_parent_id,
            }
    property_scope = list(proposal.get("property_scope") or candidate.get("property_scope") or [])
    if operation == "set_presentation_override" and not property_scope:
        property_scope = ["presentation"]
    if not property_scope:
        property_scope = ["role"] if operation == "update_role" else ["overlay"]
    preconditions = list(proposal.get("preconditions") or candidate.get("preconditions") or [])
    preconditions.extend(
        {"kind": "candidate_constraint", "value": copy.deepcopy(dict(constraint))}
        for constraint in candidate.get("constraints") or []
        if isinstance(constraint, Mapping)
    )
    return {
        "decision_id": "decision-" + hashlib.sha256(_canonical(candidate).encode("utf-8")).hexdigest()[:16],
        "candidate_id": str(candidate.get("candidate_id")),
        "candidate_ids": [str(candidate.get("candidate_id"))],
        "operation": operation,
        "target_node_ids": target_node_ids,
        "target_source_refs": copy_source_refs(candidate.get("target_source_refs")),
        "expected_revision": expected_revision,
        "property_scope": property_scope,
        "value": value,
        "preconditions": preconditions,
        "evidence": list(candidate.get("positive_evidence") or []),
        "invalidates": list(proposal.get("invalidates") or []),
    }


def copy_source_refs(value: Any) -> list[dict[str, Any]]:
    """Copy source references without importing the broader contract model."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _decision_from_revision(revision: Mapping[str, Any], expected_revision: int) -> dict[str, Any]:
    decision = dict(revision.get("decision") or {})
    decision.update(
        {
            "decision_id": f"ledger-{revision.get('revision_id')}",
            "revision_id": revision.get("revision_id"),
            "candidate_id": None,
            "candidate_ids": [],
            "target_node_ids": list(revision.get("target_node_ids") or []),
            "target_source_refs": copy_source_refs(revision.get("target_source_refs")),
            "expected_revision": expected_revision,
            "property_scope": list(revision.get("property_scope") or decision.get("property_scope") or []),
            "preconditions": list(revision.get("preconditions") or decision.get("preconditions") or []),
            "evidence": list(revision.get("evidence") or []),
            "invalidates": list(decision.get("invalidates") or []),
            "transaction_id": revision.get("transaction_id"),
        }
    )
    return decision


def resolve(
    candidate_set: Sequence[Mapping[str, Any]],
    constraints: Mapping[str, Any] | None = None,
    ledger_view: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve candidates by evidence class and explicit ledger precedence."""

    constraints = constraints or {}
    expected_revision = int(constraints.get("revision", (ledger_view or {}).get("revision", 1)))
    protected = {str(value) for value in constraints.get("protected_node_ids", [])}
    grouped: dict[tuple[tuple[str, ...], tuple[str, ...]], list[Mapping[str, Any]]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    for candidate in sorted(candidate_set, key=lambda item: (str(item.get("module_id")), str(item.get("module_version")), str(item.get("candidate_id")))):
        target_ids = {str(value) for value in candidate.get("target_node_ids") or []}
        if target_ids & protected:
            unresolved.append(
                {
                    "code": "protected_target",
                    "candidate_ids": [str(candidate.get("candidate_id"))],
                    "target_node_ids": sorted(target_ids & protected),
                }
            )
            continue
        grouped[_group_key(candidate)].append(candidate)

    decisions: list[dict[str, Any]] = []
    for group_key in sorted(grouped):
        candidates = grouped[group_key]
        ledger_matches = [revision for revision in _active_ledger_revisions(ledger_view) if tuple(sorted(str(value) for value in revision.get("target_node_ids") or [])) == group_key[0] and tuple(sorted(str(value) for value in revision.get("property_scope") or [])) == group_key[1]]
        if ledger_matches:
            if len({_canonical(item.get("decision") or {}) for item in ledger_matches}) > 1:
                unresolved.append({"code": "ledger_conflict", "candidate_ids": [], "revision_ids": sorted(str(item.get("revision_id")) for item in ledger_matches)})
                continue
            decision = _decision_from_revision(sorted(ledger_matches, key=lambda item: str(item.get("revision_id")))[0], expected_revision)
            decision["candidate_ids"] = sorted(str(item.get("candidate_id")) for item in candidates)
            decision["candidate_id"] = decision["candidate_ids"][0] if decision["candidate_ids"] else None
            decisions.append(decision)
            continue

        by_proposal: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            by_proposal[_canonical(candidate.get("proposal") or {})].append(candidate)
        ranked = sorted(
            ((max(_candidate_strength(item) for item in items), proposal_key, items) for proposal_key, items in by_proposal.items()),
            key=lambda item: (-item[0], item[1]),
        )
        best_strength = ranked[0][0]
        best = [item for item in ranked if item[0] == best_strength]
        if len(best) > 1:
            candidate_ids = sorted(str(item.get("candidate_id")) for _, _, items in best for item in items)
            unresolved.append({"code": "candidate_conflict", "candidate_ids": candidate_ids, "target_node_ids": list(group_key[0])})
            continue
        selected = sorted(best[0][2], key=lambda item: (str(item.get("module_id")), str(item.get("candidate_id"))))
        decision = _decision_from_candidate(selected[0], expected_revision)
        decision["candidate_ids"] = sorted(str(item.get("candidate_id")) for item in selected)
        decisions.append(decision)

    decisions.sort(key=lambda item: (tuple(item.get("target_node_ids", [])), tuple(item.get("property_scope", [])), str(item.get("decision_id"))))
    unresolved.sort(key=lambda item: (item.get("code", ""), tuple(item.get("candidate_ids", [])), tuple(item.get("revision_ids", []))))
    transaction_payload = {"revision": expected_revision, "decisions": decisions, "unresolved": unresolved}
    transaction_id = "decision-set-" + hashlib.sha256(_canonical(transaction_payload).encode("utf-8")).hexdigest()[:20]
    for decision in decisions:
        if not decision.get("transaction_id"):
            decision["transaction_id"] = transaction_id
    return {
        "decision_set_version": "2.0",
        "transaction_id": transaction_id,
        "expected_revision": expected_revision,
        "decisions": decisions,
        "unresolved": unresolved,
        "diagnostics": [],
    }
