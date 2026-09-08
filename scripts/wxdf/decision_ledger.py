"""Validated decision revisions, invalidation, supersession and replay."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence

from .contracts import PATCH_OPERATIONS
from .transaction import apply_stage_batch, make_snapshot
from .validation import validate_ast


class LedgerError(ValueError):
    """Structured failure while validating or replaying a decision ledger."""

    def __init__(self, message: str, *, code: str = "ledger_error") -> None:
        self.code = code
        super().__init__(message)


_STATUSES = {"active", "superseded", "invalidated", "conflicted"}
_REQUIRED = (
    "revision_id",
    "parent_revision",
    "transaction_id",
    "target_source_refs",
    "target_node_ids",
    "property_scope",
    "decision",
    "preconditions",
    "evidence",
    "origin",
    "resolver_version",
    "status",
    "supersedes",
)


def _json_safe(value: Any) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise LedgerError("revision contains a non-serializable value", code="non_serializable_revision") from exc


def _source_digest(facts: Mapping[str, Any]) -> str | None:
    source = facts.get("source") if isinstance(facts, Mapping) else None
    if isinstance(source, Mapping):
        value = source.get("content_sha256")
        if isinstance(value, str):
            return value
    return None


def validate_revisions(facts: Mapping[str, Any], ledger: Mapping[str, Any]) -> None:
    """Validate the ledger shape and the safe semantic operation surface."""

    if not isinstance(ledger, Mapping) or ledger.get("schema_version") != "2.0":
        raise LedgerError("ledger schema_version must be 2.0", code="unsupported_version")
    if not isinstance(ledger.get("revision"), int) or ledger["revision"] < 0:
        raise LedgerError("ledger revision must be a non-negative integer", code="invalid_revision")
    revisions = ledger.get("revisions")
    if not isinstance(revisions, list):
        raise LedgerError("ledger revisions must be a list", code="invalid_revisions")
    seen: set[str] = set()
    ids = {str(item.get("revision_id")) for item in revisions if isinstance(item, Mapping) and item.get("revision_id")}
    for index, revision in enumerate(revisions):
        if not isinstance(revision, Mapping):
            raise LedgerError(f"revision {index} is not an object", code="invalid_revision")
        missing = [field for field in _REQUIRED if field not in revision]
        if missing:
            raise LedgerError(f"revision {index} is missing {missing[0]}", code="missing_revision_field")
        revision_id = revision["revision_id"]
        if not isinstance(revision_id, str) or not revision_id:
            raise LedgerError("revision_id must be a non-empty string", code="invalid_revision_id")
        if revision_id in seen:
            raise LedgerError(f"duplicate revision_id {revision_id}", code="duplicate_revision_id")
        seen.add(revision_id)
        if revision["parent_revision"] is not None and (not isinstance(revision["parent_revision"], int) or revision["parent_revision"] < 0):
            raise LedgerError(f"invalid parent_revision for {revision_id}", code="invalid_parent_revision")
        if not isinstance(revision["transaction_id"], str) or not revision["transaction_id"]:
            raise LedgerError(f"invalid transaction_id for {revision_id}", code="invalid_transaction_id")
        for field in ("target_source_refs", "target_node_ids", "property_scope", "preconditions", "evidence", "supersedes"):
            if not isinstance(revision[field], list):
                raise LedgerError(f"{field} must be a list for {revision_id}", code="invalid_revision_field")
        if any(not isinstance(value, str) or not value for value in revision["target_node_ids"]):
            raise LedgerError(f"target_node_ids contains an invalid value for {revision_id}", code="invalid_target")
        if revision["status"] not in _STATUSES:
            raise LedgerError(f"unsupported revision status {revision['status']}", code="invalid_status")
        operation = (revision["decision"] or {}).get("operation") if isinstance(revision["decision"], Mapping) else None
        if operation not in PATCH_OPERATIONS:
            raise LedgerError(f"unsupported revision operation {operation!r}", code="unsupported_operation")
        for superseded_id in revision["supersedes"]:
            if superseded_id not in ids:
                raise LedgerError(f"revision {revision_id} supersedes unknown {superseded_id}", code="unknown_superseded_revision")
        _json_safe(revision)


def _precondition_ok(facts: Mapping[str, Any], revision: Mapping[str, Any]) -> tuple[bool, str]:
    digest = _source_digest(facts)
    for ref in revision.get("target_source_refs", []):
        if not isinstance(ref, Mapping):
            return False, "target source reference is not an object"
        expected = ref.get("content_sha256")
        if digest is not None and expected and expected != digest:
            return False, "target source digest changed"
    for condition in revision.get("preconditions", []):
        if not isinstance(condition, Mapping):
            return False, "precondition is not an object"
        kind = condition.get("kind")
        if kind in {"source_sha256", "source_content_sha256"} and digest is not None:
            if condition.get("value") != digest:
                return False, "source digest precondition failed"
    return True, ""


def project_ledger(facts: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Return a derived ledger view for the current source facts."""

    validate_revisions(facts, ledger)
    projected = copy.deepcopy(dict(ledger))
    diagnostics: list[dict[str, Any]] = []
    by_id = {item["revision_id"]: item for item in projected["revisions"]}
    for revision in projected["revisions"]:
        if revision["status"] != "active":
            continue
        valid, reason = _precondition_ok(facts, revision)
        if not valid:
            revision["status"] = "invalidated"
            diagnostics.append({"code": "revision_precondition_failed", "revision_id": revision["revision_id"], "message": reason})

    for revision in projected["revisions"]:
        if revision["status"] != "active":
            continue
        for superseded_id in revision.get("supersedes", []):
            superseded = by_id[superseded_id]
            if superseded["status"] == "active":
                superseded["status"] = "superseded"
                diagnostics.append({"code": "revision_superseded", "revision_id": superseded_id, "by": revision["revision_id"]})

    groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[dict[str, Any]]] = {}
    for revision in projected["revisions"]:
        if revision["status"] != "active":
            continue
        decision = revision["decision"]
        key = (tuple(sorted(revision["target_node_ids"])), tuple(sorted(revision["property_scope"])))
        groups.setdefault(key, []).append(revision)
    for revisions in groups.values():
        proposals = {json.dumps(item["decision"], ensure_ascii=False, sort_keys=True) for item in revisions}
        if len(proposals) <= 1:
            continue
        revision_ids = sorted(item["revision_id"] for item in revisions)
        for revision in revisions:
            revision["status"] = "conflicted"
        diagnostics.append({"code": "revision_conflict", "revision_ids": revision_ids, "message": "active revisions disagree for one target property"})

    projected["active_revisions"] = [item for item in projected["revisions"] if item["status"] == "active"]
    projected["diagnostics"] = diagnostics
    return projected


def _revision_decision(revision: Mapping[str, Any], expected_revision: int) -> dict[str, Any]:
    decision = dict(revision["decision"])
    decision.update(
        {
            "decision_id": f"ledger-{revision['revision_id']}",
            "target_node_ids": list(revision["target_node_ids"]),
            "expected_revision": expected_revision,
            "property_scope": list(revision["property_scope"]),
            "preconditions": list(revision["preconditions"]),
            "evidence": list(revision["evidence"]),
            "transaction_id": revision["transaction_id"],
        }
    )
    return decision


def replay(facts: Mapping[str, Any], ledger: Mapping[str, Any], plan: Any) -> dict[str, Any]:
    """Rebuild a Source AST by applying only currently effective revisions."""

    view = project_ledger(facts, ledger)
    if view["diagnostics"]:
        invalid = [item for item in view["diagnostics"] if item["code"] in {"revision_precondition_failed", "revision_conflict"}]
        if invalid:
            raise LedgerError("cannot replay invalidated or conflicted revisions", code="replay_blocked")
    if callable(plan):
        ast = plan(facts)
    elif isinstance(plan, Mapping) and "source_ast" in plan:
        ast = copy.deepcopy(plan["source_ast"])
    else:
        ast = copy.deepcopy(plan)
    validate_ast(ast)
    active = sorted(view["active_revisions"], key=lambda item: str(item["revision_id"]))
    if not active:
        return ast
    decisions = [_revision_decision(revision, ast["revision"]) for revision in active]
    result = apply_stage_batch(make_snapshot(ast), decisions, expected_revision=ast["revision"])
    return result.snapshot.ast


def _decision_as_revision(decision: Mapping[str, Any], parent_revision: int, index: int) -> dict[str, Any]:
    if "revision_id" in decision and all(field in decision for field in _REQUIRED):
        revision = copy.deepcopy(dict(decision))
        revision.setdefault("parent_revision", parent_revision)
        return revision
    digest = hashlib.sha256(json.dumps(decision, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return {
        "revision_id": f"r-{digest}-{index}",
        "parent_revision": parent_revision,
        "transaction_id": str(decision.get("transaction_id") or f"tx-{digest}"),
        "target_source_refs": list(decision.get("target_source_refs") or []),
        "target_node_ids": list(decision.get("target_node_ids") or []),
        "property_scope": list(decision.get("property_scope") or []),
        "decision": {key: value for key, value in decision.items() if key in {"operation", "value", "invalidates"}},
        "preconditions": list(decision.get("preconditions") or []),
        "evidence": list(decision.get("evidence") or []),
        "origin": {"kind": "decision_set"},
        "resolver_version": "1.0",
        "status": "active",
        "supersedes": list(decision.get("supersedes") or []),
    }


def accept_decisions(ledger: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]] | Mapping[str, Any]) -> dict[str, Any]:
    """Append a complete accepted DecisionSet to a copied ledger."""

    validate_revisions({}, ledger)
    if isinstance(decisions, Mapping):
        decisions = decisions.get("decisions", [])
    candidate = copy.deepcopy(dict(ledger))
    parent_revision = int(candidate["revision"])
    for index, decision in enumerate(decisions):
        revision = _decision_as_revision(decision, parent_revision, index)
        if revision["revision_id"] in {item["revision_id"] for item in candidate["revisions"]}:
            raise LedgerError(f"duplicate revision_id {revision['revision_id']}", code="duplicate_revision_id")
        candidate["revisions"].append(revision)
    candidate["revision"] = parent_revision + 1 if decisions else parent_revision
    validate_revisions({}, candidate)
    return candidate
