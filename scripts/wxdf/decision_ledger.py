"""Validated decision revisions, invalidation, supersession and replay."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence

from .contracts import AST_BLOCK_TYPES, AST_CONTAINER_KINDS, AST_ROLES, LIST_KINDS, PATCH_OPERATIONS, TABLE_TYPES
from .digest import digest
from .response_identity import _APPLICATION_FIELDS as APPLICATION_FIELDS
from .transaction import (
    StageSnapshot,
    apply_stage_batch,
    make_snapshot,
    revision_applied,
    transaction_content_digest,
)
from .validation import _validate_inline_content, validate_ast


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


def _source_ref_identities(facts: Mapping[str, Any]) -> set[str]:
    identities: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            source_ref = value.get("source_ref")
            if isinstance(source_ref, Mapping):
                identities.add(json.dumps(source_ref, ensure_ascii=False, sort_keys=True))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(facts)
    return identities


def source_binding(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable source contract a ledger is allowed to replay against."""

    parser = facts.get("parser") if isinstance(facts, Mapping) else None
    return {
        "source_facts_sha256": digest(facts),
        "source_contract": {
            "facts_schema_version": facts.get("facts_schema_version") if isinstance(facts, Mapping) else None,
            "parser": copy.deepcopy(dict(parser)) if isinstance(parser, Mapping) else None,
        },
    }


def _validate_source_binding(facts: Mapping[str, Any], ledger: Mapping[str, Any]) -> None:
    binding = ledger.get("source_binding")
    if binding is None:
        return
    if not isinstance(binding, Mapping) or dict(binding) != source_binding(facts):
        raise LedgerError("decision ledger source binding does not match current SourceFacts", code="source_binding_mismatch")


def _invalid_create_node(revision_id: str, message: str) -> LedgerError:
    return LedgerError(f"create_node revision {revision_id} {message}", code="invalid_decision_value")


def _validate_created_node(revision_id: str, node: Mapping[str, Any]) -> None:
    block_type = node.get("block_type")
    kind = node.get("kind")
    if block_type is not None:
        if not isinstance(block_type, str) or block_type not in AST_BLOCK_TYPES or kind is not None:
            raise _invalid_create_node(revision_id, "has an invalid node block_type")
    elif not isinstance(kind, str) or kind not in AST_CONTAINER_KINDS:
        raise _invalid_create_node(revision_id, "has an invalid node kind")
    if kind == "list" and (
        node.get("list_kind") not in LIST_KINDS
        or not isinstance(node.get("source_start"), int)
        or isinstance(node.get("source_start"), bool)
        or node["source_start"] < 1
        or not isinstance(node.get("source_sequence_id"), str)
        or not node["source_sequence_id"].strip()
    ):
        raise _invalid_create_node(revision_id, "has incomplete list fields")
    if kind == "list_item" and (
        not isinstance(node.get("source_marker"), str)
        or not node["source_marker"].strip()
        or not isinstance(node.get("source_ordinal"), int)
        or isinstance(node.get("source_ordinal"), bool)
        or node["source_ordinal"] < 1
        or not isinstance(node.get("source_sequence_id"), str)
        or not node["source_sequence_id"].strip()
    ):
        raise _invalid_create_node(revision_id, "has incomplete list_item fields")
    if kind == "appendix" and (
        node.get("source_classification") not in {"normative", "informative", "unknown"}
        or not isinstance(node.get("title_ref"), str)
        or not node["title_ref"].strip()
    ):
        raise _invalid_create_node(revision_id, "has incomplete appendix fields")
    if kind == "table_cell" and (
        any(not isinstance(node.get(field), int) or isinstance(node.get(field), bool) or node[field] < 0 for field in ("row", "col"))
        or any(not isinstance(node.get(field), int) or isinstance(node.get(field), bool) or node[field] < 1 for field in ("row_span", "col_span"))
    ):
        raise _invalid_create_node(revision_id, "has incomplete table_cell fields")
    if block_type == "paragraph":
        if node.get("role") not in AST_ROLES or not isinstance(node.get("inline_content"), list):
            raise _invalid_create_node(revision_id, "has incomplete paragraph fields")
        valid_inline_kinds = {"text", "break", "tab", "image", "link", "field", "bookmark", "opaque_inline"}
        if any(not isinstance(item, Mapping) or item.get("kind") not in valid_inline_kinds for item in node["inline_content"]):
            raise _invalid_create_node(revision_id, "has invalid paragraph inline content")
        if node.get("role") == "heading" and node.get("level") not in {1, 2, 3, 4, 5, 6}:
            raise _invalid_create_node(revision_id, "has an invalid heading level")
    if block_type == "image" and (
        not isinstance(node.get("resource_id"), str)
        or not node["resource_id"].strip()
        or not isinstance(node.get("alt_text"), str)
        or not node["alt_text"].strip()
    ):
        raise _invalid_create_node(revision_id, "has incomplete image fields")
    if block_type == "table" and node.get("table_type") not in TABLE_TYPES:
        raise _invalid_create_node(revision_id, "has an invalid table_type")
    if block_type == "opaque" and (not isinstance(node.get("diagnostic_ids"), list) or not node.get("diagnostic_ids")):
        raise _invalid_create_node(revision_id, "has incomplete opaque fields")


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
        transaction_digest = revision.get("transaction_content_sha256")
        if transaction_digest is not None and (
            not isinstance(transaction_digest, str)
            or not transaction_digest.startswith("sha256:")
            or len(transaction_digest) != len("sha256:") + 64
            or any(character not in "0123456789abcdef" for character in transaction_digest[len("sha256:"):])
        ):
            raise LedgerError(f"invalid transaction_content_sha256 for {revision_id}", code="invalid_transaction_digest")
        operation = (revision["decision"] or {}).get("operation") if isinstance(revision["decision"], Mapping) else None
        if operation not in PATCH_OPERATIONS:
            raise LedgerError(f"unsupported revision operation {operation!r}", code="unsupported_operation")
        if not revision["target_node_ids"]:
            raise LedgerError(f"revision {revision_id} requires target_node_ids", code="invalid_target")
        if operation == "create_node":
            value = (revision["decision"] or {}).get("value")
            if not isinstance(value, Mapping):
                raise LedgerError(f"create_node revision {revision_id} requires a mapping value", code="invalid_decision_value")
            for field in ("node_id", "parent_id"):
                if not isinstance(value.get(field), str) or not value[field].strip():
                    raise LedgerError(f"create_node revision {revision_id} requires value.{field}", code="invalid_decision_value")
            if revision["target_node_ids"] != [value["node_id"]]:
                raise LedgerError(f"create_node revision {revision_id} target_node_ids must name value.node_id", code="invalid_decision_value")
            if not isinstance(value.get("node"), Mapping):
                raise LedgerError(f"create_node revision {revision_id} requires value.node to be a mapping", code="invalid_decision_value")
            _validate_created_node(revision_id, value["node"])
        elif operation == "update_node_content":
            value = (revision["decision"] or {}).get("value")
            if revision["property_scope"] != ["inline_content"] or not isinstance(value, Mapping):
                raise LedgerError(f"update_node_content revision {revision_id} has an invalid property scope or value", code="invalid_decision_value")
            inline_content = value.get("inline_content")
            if not isinstance(inline_content, list):
                raise LedgerError(f"update_node_content revision {revision_id} requires value.inline_content", code="invalid_decision_value")
            try:
                _validate_inline_content(inline_content, f"revision[{revision_id}].value.inline_content", "Patch 2.0", facts.get("resources", {}))
            except Exception as exc:
                raise LedgerError(f"update_node_content revision {revision_id} has invalid inline content", code="invalid_decision_value") from exc
        for superseded_id in revision["supersedes"]:
            if superseded_id not in ids:
                raise LedgerError(f"revision {revision_id} supersedes unknown {superseded_id}", code="unknown_superseded_revision")
        _json_safe(revision)


def _precondition_ok(facts: Mapping[str, Any], revision: Mapping[str, Any]) -> tuple[bool, str]:
    digest = _source_digest(facts)
    if digest is None:
        return False, "current source digest is unavailable"
    has_source_digest = False
    source_ref_identities = _source_ref_identities(facts)
    for ref in revision.get("target_source_refs", []):
        if not isinstance(ref, Mapping):
            return False, "target source reference is not an object"
        expected = ref.get("content_sha256")
        if expected:
            has_source_digest = True
            ref_identity = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            if expected != digest and ref_identity not in source_ref_identities:
                return False, "target source digest changed"
    for condition in revision.get("preconditions", []):
        if not isinstance(condition, Mapping):
            return False, "precondition is not an object"
        kind = condition.get("kind")
        if kind in {"source_sha256", "source_content_sha256"}:
            has_source_digest = True
            if condition.get("value") != digest:
                return False, "source digest precondition failed"
    if not has_source_digest:
        return False, "revision has no source digest precondition"
    return True, ""


def project_ledger(
    facts: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    existing_node_ids: set[str] | None = None,
    check_source_binding: bool = True,
) -> dict[str, Any]:
    """Return a derived ledger view for the current source facts."""

    validate_revisions(facts, ledger)
    if check_source_binding:
        _validate_source_binding(facts, ledger)
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

    if existing_node_ids is not None:
        for revision in projected["revisions"]:
            if revision["status"] != "active" or revision["decision"].get("operation") != "create_node":
                continue
            node_id = str((revision["decision"].get("value") or {}).get("node_id"))
            if node_id in existing_node_ids:
                revision["status"] = "conflicted"
                diagnostics.append({
                    "code": "revision_conflict",
                    "revision_ids": [revision["revision_id"]],
                    "message": f"create_node target {node_id} already exists in the source AST",
                })
        pending_creates = {
            str((revision["decision"].get("value") or {}).get("node_id")): revision
            for revision in projected["revisions"]
            if revision["status"] == "active" and revision["decision"].get("operation") == "create_node"
        }
        available_node_ids = set(existing_node_ids)
        while pending_creates:
            ready = [
                (node_id, revision)
                for node_id, revision in pending_creates.items()
                if str((revision["decision"].get("value") or {}).get("parent_id")) in available_node_ids
            ]
            if not ready:
                for node_id, revision in pending_creates.items():
                    revision["status"] = "conflicted"
                    parent_id = str((revision["decision"].get("value") or {}).get("parent_id"))
                    diagnostics.append({
                        "code": "revision_conflict",
                        "revision_ids": [revision["revision_id"]],
                        "message": f"create_node parent {parent_id} cannot be reached from the source AST",
                    })
                break
            for node_id, _ in ready:
                available_node_ids.add(node_id)
                pending_creates.pop(node_id)

    created_by_id = {
        str((revision["decision"].get("value") or {}).get("node_id")): revision
        for revision in projected["revisions"]
        if revision["status"] == "active" and revision["decision"].get("operation") == "create_node"
    }
    for revision in projected["revisions"]:
        if revision["status"] != "active" or revision["decision"].get("operation") in {"create_node", "move_node"}:
            continue
        overlap = set(revision["target_node_ids"]) & set(created_by_id)
        for node_id in sorted(overlap):
            created = created_by_id[node_id]
            created["status"] = "conflicted"
            revision["status"] = "conflicted"
            diagnostics.append({
                "code": "revision_conflict",
                "revision_ids": sorted([created["revision_id"], revision["revision_id"]]),
                "message": f"revision targets node {node_id} created by another active revision",
            })

    groups: dict[tuple[tuple[tuple[str, str], ...], tuple[str, ...]], list[dict[str, Any]]] = {}
    for revision in projected["revisions"]:
        if revision["status"] != "active":
            continue
        decision = revision["decision"]
        operation = decision.get("operation")
        if operation == "create_node":
            node_id = str((decision.get("value") or {}).get("node_id"))
            key = (("create_node", node_id),), ()
        else:
            target_ids = tuple(("node", node_id) for node_id in sorted(revision["target_node_ids"]))
            key = (target_ids, tuple(sorted(revision["property_scope"])))
        groups.setdefault(key, []).append(revision)
    for revisions in groups.values():
        if len(revisions) <= 1:
            continue
        proposals = {json.dumps(item["decision"], ensure_ascii=False, sort_keys=True) for item in revisions}
        creates_same_node = all(item["decision"].get("operation") == "create_node" for item in revisions)
        if creates_same_node and len(proposals) == 1:
            ordered = sorted(revisions, key=lambda item: str(item["revision_id"]))
            for duplicate in ordered[1:]:
                duplicate["status"] = "superseded"
                diagnostics.append({"code": "revision_deduplicated", "revision_id": duplicate["revision_id"], "by": ordered[0]["revision_id"]})
            continue
        if len(proposals) <= 1 and not creates_same_node:
            continue
        revision_ids = sorted(item["revision_id"] for item in revisions)
        for revision in revisions:
            revision["status"] = "conflicted"
        diagnostics.append({"code": "revision_conflict", "revision_ids": revision_ids, "message": "active revisions overlap one target property or created node"})

    active_non_creates = [
        revision
        for revision in projected["revisions"]
        if revision["status"] == "active" and revision["decision"].get("operation") != "create_node"
    ]
    reported_overlaps: set[tuple[str, str]] = set()
    for index, left in enumerate(active_non_creates):
        for right in active_non_creates[index + 1 :]:
            if not set(left["target_node_ids"]) & set(right["target_node_ids"]):
                continue
            if not set(left["property_scope"]) & set(right["property_scope"]):
                continue
            pair = tuple(sorted((str(left["revision_id"]), str(right["revision_id"]))))
            if pair in reported_overlaps:
                continue
            reported_overlaps.add(pair)
            if left["decision"] == right["decision"] and left["property_scope"] == right["property_scope"]:
                continue
            left["status"] = "conflicted"
            right["status"] = "conflicted"
            diagnostics.append({
                "code": "revision_conflict",
                "revision_ids": list(pair),
                "message": "active revisions overlap one target property or created node",
            })

    projected["active_revisions"] = [item for item in projected["revisions"] if item["status"] == "active"]
    projected["diagnostics"] = diagnostics
    return projected


def _revision_decision(revision: Mapping[str, Any], expected_revision: int) -> dict[str, Any]:
    decision = dict(revision["decision"])
    decision.update(
        {
            "decision_id": f"ledger-{revision['revision_id']}",
            "target_source_refs": copy.deepcopy(list(revision["target_source_refs"])),
            "target_node_ids": list(revision["target_node_ids"]),
            "expected_revision": expected_revision,
            "property_scope": list(revision["property_scope"]),
            "preconditions": list(revision["preconditions"]),
            "evidence": list(revision["evidence"]),
            "transaction_id": revision["transaction_id"],
            "status": revision["status"],
            "supersedes": list(revision.get("supersedes") or []),
        }
    )
    return decision


def revision_decision(revision: Mapping[str, Any], expected_revision: int) -> dict[str, Any]:
    """Expose one validated ledger revision as a standard stage decision."""

    return _revision_decision(revision, expected_revision)


def _replay_snapshot(facts: Mapping[str, Any], ledger: Mapping[str, Any], plan: Any) -> StageSnapshot:
    """Rebuild a transaction snapshot by applying currently effective revisions."""

    if callable(plan):
        ast = plan(facts)
        snapshot = make_snapshot(ast)
    elif isinstance(plan, StageSnapshot):
        snapshot = copy.deepcopy(plan)
    elif isinstance(plan, Mapping) and "source_ast" in plan:
        snapshot = make_snapshot(copy.deepcopy(plan["source_ast"]))
    else:
        snapshot = make_snapshot(copy.deepcopy(plan))
    validate_ast(snapshot.ast)
    initial_view = project_ledger(facts, ledger)
    if initial_view["diagnostics"]:
        invalid = [item for item in initial_view["diagnostics"] if item["code"] in {"revision_precondition_failed", "revision_conflict"}]
        if invalid:
            raise LedgerError("cannot replay invalidated or conflicted revisions", code="replay_blocked")
    active_before_replay = list(initial_view["active_revisions"])
    applied_create_ids = {
        str((revision["decision"].get("value") or {}).get("node_id"))
        for revision in active_before_replay
        if revision["decision"].get("operation") == "create_node" and revision_applied(snapshot.ast, revision, snapshot)
    }
    existing_node_ids = set(snapshot.ast["document"]["blocks"]) | set(snapshot.ast["document"]["containers"])
    view = project_ledger(facts, ledger, existing_node_ids=existing_node_ids - applied_create_ids)
    if view["diagnostics"]:
        invalid = [item for item in view["diagnostics"] if item["code"] in {"revision_precondition_failed", "revision_conflict"}]
        if invalid:
            raise LedgerError("cannot replay invalidated or conflicted revisions", code="replay_blocked")
    active_revisions = list(view["active_revisions"])
    by_transaction: dict[str, list[Mapping[str, Any]]] = {}
    for revision in active_revisions:
        by_transaction.setdefault(str(revision["transaction_id"]), []).append(revision)
    for transaction_id, revisions in by_transaction.items():
        if transaction_id in snapshot.transactions and not all(
            revision_applied(snapshot.ast, revision, snapshot) for revision in revisions
        ):
            snapshot.transactions.pop(transaction_id, None)
    active = sorted(
        (revision for revision in active_revisions if not revision_applied(snapshot.ast, revision, snapshot)),
        key=lambda item: str(item["revision_id"]),
    )
    if not active:
        return snapshot
    decisions = [_revision_decision(revision, snapshot.ast["revision"]) for revision in active]
    ast_operations = {
        "create_node",
        "update_node_semantics",
        "update_node_content",
        "update_role",
        "move_node",
        "update_association",
        "delete_node",
    }
    result = apply_stage_batch(snapshot, decisions, expected_revision=snapshot.ast["revision"])
    if not any(decision.get("operation") in ast_operations for decision in decisions):
        result.snapshot.ast["revision"] = snapshot.ast["revision"]
    return result.snapshot


def replay(facts: Mapping[str, Any], ledger: Mapping[str, Any], plan: Any) -> dict[str, Any]:
    """Rebuild a Source AST by applying only currently effective revisions."""

    return _replay_snapshot(facts, ledger, plan).ast


def ledger_replay_report(context: Any) -> dict[str, Any] | None:
    """Complete the ledger replay record with the resolved decision count."""

    if context is None:
        return None
    replay = context.artifacts.get("ledger_replay")
    if replay is None:
        return None
    record = dict(replay)
    decision_set = context.artifacts.get("decision_set")
    if decision_set is not None:
        record["decision_count"] = len(decision_set.get("decisions") or [])
    return record


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


def _revision_content(revision: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "target_source_refs": revision.get("target_source_refs") or [],
            "target_node_ids": revision.get("target_node_ids") or [],
            "property_scope": revision.get("property_scope") or [],
            "decision": revision.get("decision") or {},
            "preconditions": revision.get("preconditions") or [],
            "evidence": revision.get("evidence") or [],
            "origin": revision.get("origin") or {},
            "resolver_version": revision.get("resolver_version"),
            "supersedes": revision.get("supersedes") or [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _transaction_digest(revisions: Sequence[Mapping[str, Any]]) -> str:
    return transaction_content_digest(revisions)


def _legacy_transaction_digest(revisions: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [_revision_content(revision) for revision in revisions],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def response_transaction_binding(
    response: Mapping[str, Any],
    transaction_id: str,
    transaction_content_sha256: str,
) -> dict[str, str]:
    """Bind one response to the transaction proof it committed."""

    binding = {
        "request_id": str(response["request_id"]),
        "response_sha256": str(response["response_sha256"]),
        "transaction_id": str(transaction_id),
        "transaction_content_sha256": str(transaction_content_sha256),
    }
    return {**binding, "binding_sha256": digest(binding)}


def stored_transaction_digests(checkpoint: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return the transaction proof maps one checkpoint stores."""

    transaction_digests = {
        str(transaction_id): str(value)
        for transaction_id, value in checkpoint.get("transaction_content_digests", {}).items()
    }
    applied_digests = {
        str(item.get("transaction_id")): str(item.get("content_sha256"))
        for item in checkpoint.get("applied_transactions", [])
        if isinstance(item, Mapping)
    }
    return transaction_digests, applied_digests


def snapshot_transaction_state(transactions: Mapping[str, str]) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Serialize snapshot transaction digests into checkpoint proof maps."""

    digests = {
        str(transaction_id): value if str(value).startswith("sha256:") else f"sha256:{value}"
        for transaction_id, value in transactions.items()
    }
    applied = [
        {"transaction_id": transaction_id, "content_sha256": transaction_digest}
        for transaction_id, transaction_digest in sorted(digests.items())
    ]
    return applied, digests


def verify_response_transaction_proof(
    checkpoint: Mapping[str, Any],
    request_id: str,
    *,
    transaction_id: str,
    response: Mapping[str, Any],
) -> None:
    """Require one applied response and its checkpoint to share one commit proof."""

    if response.get("transaction_id") != transaction_id:
        raise LedgerError(
            f"transaction {transaction_id} is not bound to response {request_id}",
            code="transaction_state_mismatch",
        )
    transaction_digests, applied_digests = stored_transaction_digests(checkpoint)
    if transaction_id not in transaction_digests:
        raise LedgerError(
            f"transaction {transaction_id} is not recorded for response {request_id}",
            code="transaction_state_missing",
        )
    if response.get("transaction_content_sha256") != transaction_digests[transaction_id]:
        raise LedgerError(
            f"transaction {transaction_id} content does not match response {request_id}",
            code="transaction_state_mismatch",
        )
    if applied_digests.get(transaction_id) != response.get("transaction_content_sha256"):
        raise LedgerError(
            f"transaction {transaction_id} is not present in applied transaction state",
            code="transaction_state_missing",
        )


def verify_rolled_back_response_proof(checkpoint: Mapping[str, Any]) -> None:
    """Require a rolled-back checkpoint to carry one consistent response proof.

    This is the proof owner's verification of the rolled-back branch: every
    applied response must match an applied transaction, every rejected response
    must carry no application metadata, and the binding map must describe exactly
    the applied responses.
    """

    bindings = checkpoint.get("response_transaction_bindings") or {}
    _, applied_digests = stored_transaction_digests(checkpoint)
    bound_ids: set[str] = set()
    for response in checkpoint.get("responses", []):
        request_id = str(response.get("request_id") or "")
        if response.get("applied") is True:
            transaction_id = str(response.get("transaction_id") or "")
            digest = response.get("transaction_content_sha256")
            expected_binding = response_transaction_binding(
                response,
                transaction_id,
                str(digest or ""),
            )
            if (
                applied_digests.get(transaction_id) != digest
                or bindings.get(request_id) != expected_binding
            ):
                raise LedgerError(
                    f"rolled-back response proof changed for {request_id}",
                    code="response_transaction_binding_mismatch",
                )
            bound_ids.add(request_id)
        elif (
            response.get("applied") is not False
            or response.get("apply_started", False) is not False
            or response.get("transaction_id") is not None
            or response.get("transaction_content_sha256") is not None
            or request_id in bindings
        ):
            raise LedgerError(
                f"rejected response application state changed for {request_id}",
                code="response_application_state_mismatch",
            )
    if set(bindings) != bound_ids:
        raise LedgerError(
            "rolled-back response bindings do not match applied responses",
            code="response_transaction_binding_mismatch",
        )


def ledger_transaction_digests(
    ledger: Mapping[str, Any],
    *,
    version: str = "2.0",
    verify_stored: bool = True,
) -> dict[str, str]:
    """Recompute transaction proofs from immutable ledger revision content."""

    groups: dict[str, list[Mapping[str, Any]]] = {}
    for revision in ledger.get("revisions", []):
        if not isinstance(revision, Mapping):
            raise LedgerError("ledger revision is not an object", code="invalid_revision")
        transaction_id = str(revision.get("transaction_id") or "")
        if not transaction_id:
            raise LedgerError("ledger revision has no transaction ID", code="invalid_transaction_id")
        groups.setdefault(transaction_id, []).append(revision)
    if version == "2.0":
        digest_function = _transaction_digest
    elif version == "1.0":
        digest_function = _legacy_transaction_digest
    else:
        raise LedgerError(
            f"unsupported transaction digest version {version}",
            code="unsupported_transaction_digest_version",
        )
    result = {
        transaction_id: digest_function(revisions)
        for transaction_id, revisions in groups.items()
    }
    if verify_stored:
        for transaction_id, revisions in groups.items():
            expected = result[transaction_id]
            if any(revision.get("transaction_content_sha256") != expected for revision in revisions):
                raise LedgerError(
                    f"ledger transaction digest changed for {transaction_id}",
                    code="transaction_state_mismatch",
                )
    return result


def accept_decisions(ledger: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]] | Mapping[str, Any]) -> dict[str, Any]:
    """Append a complete accepted DecisionSet to a copied ledger."""

    validate_revisions({}, ledger)
    batch_transaction_id: str | None = None
    if isinstance(decisions, Mapping):
        raw_transaction_id = decisions.get("transaction_id")
        batch_transaction_id = str(raw_transaction_id) if raw_transaction_id else None
        decisions = decisions.get("decisions", [])
    candidate = copy.deepcopy(dict(ledger))
    parent_revision = int(candidate["revision"])
    appended = False
    existing_by_transaction: dict[str, list[dict[str, Any]]] = {}
    for existing in candidate["revisions"]:
        existing_by_transaction.setdefault(str(existing["transaction_id"]), []).append(existing)
    incoming_by_transaction: dict[str, str] = {}
    incoming_revisions: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions):
        if batch_transaction_id and isinstance(decision, Mapping) and not decision.get("transaction_id"):
            decision = {**decision, "transaction_id": batch_transaction_id}
        incoming_revisions.append(_decision_as_revision(decision, parent_revision, index))
    batch_digest = _transaction_digest(incoming_revisions) if batch_transaction_id else None
    for revision in incoming_revisions:
        revision["transaction_content_sha256"] = batch_digest or _transaction_digest([revision])
        existing_same_id = next(
            (item for item in candidate["revisions"] if item.get("revision_id") == revision["revision_id"]),
            None,
        )
        if existing_same_id is not None:
            if _revision_content(existing_same_id) == _revision_content(revision) and existing_same_id.get("status") == revision.get("status"):
                continue
            raise LedgerError(f"duplicate revision_id {revision['revision_id']}", code="duplicate_revision_id")
        transaction_id = str(revision["transaction_id"])
        content = _revision_content(revision)
        if not batch_transaction_id:
            previous_incoming = incoming_by_transaction.get(transaction_id)
            if previous_incoming is not None and previous_incoming != content:
                raise LedgerError(f"transaction {transaction_id} has different content", code="transaction_conflict")
            incoming_by_transaction[transaction_id] = content
        transaction_matches = existing_by_transaction.get(transaction_id, [])
        if batch_transaction_id:
            existing_digests = {item.get("transaction_content_sha256") for item in transaction_matches if item.get("transaction_content_sha256")}
            if existing_digests and existing_digests != {batch_digest}:
                raise LedgerError(f"transaction {transaction_id} has different content", code="transaction_conflict")
        elif transaction_matches and any(_revision_content(item) != content for item in transaction_matches):
            raise LedgerError(f"transaction {transaction_id} has different content", code="transaction_conflict")
        duplicate = any(
            item.get("transaction_id") == transaction_id
            and _revision_content(item) == _revision_content(revision)
            for item in candidate["revisions"]
        )
        if duplicate:
            continue
        if not any(
            isinstance(ref, Mapping) and ref.get("content_sha256")
            for ref in revision.get("target_source_refs", [])
        ) and not any(
            isinstance(condition, Mapping)
            and condition.get("kind") in {"source_sha256", "source_content_sha256"}
            and condition.get("value")
            for condition in revision.get("preconditions", [])
        ):
            revision["status"] = "invalidated"
            revision["invalidation_reason"] = "missing_source_digest"
        candidate["revisions"].append(revision)
        appended = True
    candidate["revision"] = parent_revision + 1 if appended else parent_revision
    validate_revisions({}, candidate)
    return candidate
