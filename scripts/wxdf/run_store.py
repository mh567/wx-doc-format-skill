"""File-backed checkpoint storage for modular runs.

The store serialises only JSON data.  Response records are committed before
the injected stage callback runs, so a process failure leaves a replayable
pending response instead of silently losing external work.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import CHECKPOINT_STATUSES, STAGES
from .validation import validate_checkpoint


class CheckpointError(RuntimeError):
    """Raised when a checkpoint cannot be safely stored or resumed."""

    def __init__(self, message: str, *, code: str = "checkpoint_error") -> None:
        self.code = code
        super().__init__(message)


_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"created", "running", "failed", "cancelled"}),
    "running": frozenset({"running", "waiting_external", "completed", "completed_with_warnings", "failed", "cancelled"}),
    "waiting_external": frozenset({"waiting_external", "running", "completed", "completed_with_warnings", "failed", "cancelled"}),
    "completed": frozenset({"completed"}),
    "completed_with_warnings": frozenset({"completed_with_warnings"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _path(value: Path | str) -> Path:
    return value if isinstance(value, Path) else Path(value)


def _validate_for_store(checkpoint: Mapping[str, Any]) -> None:
    try:
        validate_checkpoint(checkpoint)
    except Exception as exc:
        raise CheckpointError(f"checkpoint validation failed: {exc}", code="invalid_checkpoint") from exc


def _write_json_atomic(checkpoint: Mapping[str, Any], path: Path, *, crash_at: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    payload = _canonical(checkpoint) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    if crash_at == "before_commit":
        raise CheckpointError("injected failure before commit", code="injected_failure")
    os.replace(temporary, path)
    if crash_at == "after_commit":
        raise CheckpointError("injected failure after commit", code="injected_failure")


def save_checkpoint(
    checkpoint: Mapping[str, Any],
    path: Path | str | None = None,
    *,
    run_path: Path | str | None = None,
    crash_at: str | None = None,
) -> Path:
    """Validate and atomically persist one complete checkpoint."""

    if path is None:
        path = run_path
    if path is None:
        candidate = checkpoint.get("checkpoint_path") if isinstance(checkpoint, Mapping) else None
        path = candidate
    if path is None:
        raise CheckpointError("checkpoint path is required", code="missing_checkpoint_path")
    if not isinstance(checkpoint, Mapping):
        raise CheckpointError("checkpoint must be a mapping", code="invalid_checkpoint")
    payload = copy.deepcopy(dict(checkpoint))
    payload.setdefault("applied_transactions", [])
    payload.setdefault("transaction_content_digests", {})
    ledger = payload.get("decision_ledger")
    source_facts = payload.get("source_facts")
    if (
        isinstance(ledger, Mapping)
        and ledger.get("schema_version") == "2.0"
        and isinstance(source_facts, Mapping)
    ):
        from .decision_ledger import source_binding

        ledger["source_binding"] = source_binding(source_facts)
    _validate_for_store(payload)
    destination = _path(path)
    _write_json_atomic(payload, destination, crash_at=crash_at)
    return destination


def _compare_fingerprints(checkpoint: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    resource_summary = checkpoint.get("resource_summary")
    stored = resource_summary.get("fingerprints", {}) if isinstance(resource_summary, Mapping) else {}
    if not isinstance(stored, Mapping):
        raise CheckpointError("checkpoint fingerprints are invalid", code="invalid_fingerprints")
    for name, value in expected.items():
        if name in {"source_sha256", "source"}:
            actual = resource_summary.get("source_sha256") if isinstance(resource_summary, Mapping) else None
            actual = stored.get("source", actual)
        else:
            actual = stored.get(name)
        if actual != value:
            raise CheckpointError(f"fingerprint mismatch for {name}", code="fingerprint_mismatch")


def _entries_to_overlay(entries: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        node_id = entry.get("node_id") or entry.get("target_node_id") or entry.get("list_item_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        value = copy.deepcopy(dict(entry))
        value.pop("node_id", None)
        result[node_id] = value
    return result


def _overlay_to_entries(overlays: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node_id in sorted(overlays):
        entry = copy.deepcopy(dict(overlays[node_id]))
        entry["node_id"] = node_id
        result.append(entry)
    return result


def _snapshot_transaction_state(transactions: Mapping[str, str]) -> tuple[list[dict[str, str]], dict[str, str]]:
    digests = {
        str(transaction_id): value if str(value).startswith("sha256:") else f"sha256:{value}"
        for transaction_id, value in transactions.items()
    }
    applied = [
        {"transaction_id": transaction_id, "content_sha256": digest}
        for transaction_id, digest in sorted(digests.items())
    ]
    return applied, digests


def _checkpoint_transactions(checkpoint: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["transaction_id"]): str(item["content_sha256"])[len("sha256:"):]
        for item in checkpoint.get("applied_transactions", [])
        if isinstance(item, Mapping) and item.get("transaction_id") and item.get("content_sha256")
    }


def load_checkpoint(
    path: Path | str,
    expected_fingerprints: Mapping[str, Any] | None = None,
    *,
    expected_contracts: Mapping[str, str] | None = None,
    current_source_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load, validate and optionally bind a checkpoint to current inputs."""

    source = _path(path)
    try:
        checkpoint = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read checkpoint: {source}", code="checkpoint_read_failed") from exc
    if not isinstance(checkpoint, dict):
        raise CheckpointError("checkpoint root must be an object", code="invalid_checkpoint")
    checkpoint.setdefault("applied_transactions", [])
    checkpoint.setdefault("transaction_content_digests", {})
    _validate_for_store(checkpoint)
    if expected_fingerprints:
        _compare_fingerprints(checkpoint, expected_fingerprints)
    if expected_contracts:
        actual = checkpoint.get("contracts", {})
        for name, value in expected_contracts.items():
            if actual.get(name) != value:
                raise CheckpointError(f"contract mismatch for {name}", code="contract_mismatch")
    if current_source_facts is not None:
        from .decision_ledger import LedgerError, _replay_snapshot, project_ledger, source_binding
        from .kernel import ConversionKernel
        from .transaction import make_snapshot

        try:
            current_digest = current_source_facts.get("source", {}).get("content_sha256")
            stored_digest = checkpoint["source_facts"].get("source", {}).get("content_sha256")
            source_matches = current_digest == stored_digest and _digest(current_source_facts) == _digest(checkpoint["source_facts"])
            if not source_matches:
                unbound_ledger = copy.deepcopy(checkpoint["decision_ledger"])
                unbound_ledger.pop("source_binding", None)
                projection = project_ledger(current_source_facts, unbound_ledger, check_source_binding=False)
                checkpoint["decision_ledger_projection"] = projection
                checkpoint["decision_ledger"] = copy.deepcopy(projection)
                checkpoint["content_overlay"] = []
                checkpoint["presentation_override"] = []
                checkpoint["source_ast"] = ConversionKernel._facts_to_ast(current_source_facts)
                checkpoint["normalized_ast"] = None
                checkpoint["target_map"] = {
                    "revision": checkpoint["source_ast"]["revision"],
                    "entries": {},
                    "view_sha256": _digest({}),
                }
                checkpoint["applied_transactions"] = []
                checkpoint["transaction_content_digests"] = {}
                checkpoint["source_facts"] = copy.deepcopy(dict(current_source_facts))
                checkpoint["decision_ledger"]["source_binding"] = source_binding(current_source_facts)
                resource_summary = checkpoint.get("resource_summary")
                if isinstance(resource_summary, Mapping):
                    resource_summary = copy.deepcopy(dict(resource_summary))
                    resource_summary["source_sha256"] = current_digest
                    fingerprints = resource_summary.get("fingerprints")
                    if isinstance(fingerprints, Mapping):
                        fingerprints = copy.deepcopy(dict(fingerprints))
                        fingerprints["source"] = current_digest
                        resource_summary["fingerprints"] = fingerprints
                    checkpoint["resource_summary"] = resource_summary
                save_checkpoint(checkpoint, source)
                raise CheckpointError(
                    "current source facts do not match the checkpoint source",
                    code="source_changed",
                )
            checkpoint["decision_ledger"].setdefault("source_binding", source_binding(current_source_facts))
            projection = project_ledger(current_source_facts, checkpoint["decision_ledger"])
            checkpoint["decision_ledger_projection"] = projection
            checkpoint["decision_ledger"] = copy.deepcopy(projection)
            source_ast = ConversionKernel._facts_to_ast(current_source_facts)
            blocking = {
                "revision_precondition_failed",
                "revision_conflict",
            }
            if any(item.get("code") in blocking for item in projection.get("diagnostics", [])):
                raise CheckpointError("decision ledger replay is blocked", code="replay_blocked")
            else:
                snapshot = make_snapshot(source_ast)
                snapshot.transactions = _checkpoint_transactions(checkpoint)
                snapshot.overlays = _entries_to_overlay(checkpoint.get("content_overlay"))
                snapshot.presentation_overrides = _entries_to_overlay(checkpoint.get("presentation_override"))
                recovered = _replay_snapshot(current_source_facts, projection, snapshot)
                checkpoint["source_ast"] = recovered.ast
                checkpoint["content_overlay"] = _overlay_to_entries(recovered.overlays)
                checkpoint["presentation_override"] = _overlay_to_entries(recovered.presentation_overrides)
                applied_transactions, transaction_digests = _snapshot_transaction_state(recovered.transactions)
                checkpoint["applied_transactions"] = applied_transactions
                checkpoint["transaction_content_digests"] = transaction_digests
            checkpoint["source_facts"] = copy.deepcopy(dict(current_source_facts))
            resource_summary = checkpoint.get("resource_summary")
            if isinstance(resource_summary, Mapping):
                resource_summary = copy.deepcopy(dict(resource_summary))
                resource_summary["source_sha256"] = current_digest
                fingerprints = resource_summary.get("fingerprints")
                if isinstance(fingerprints, Mapping):
                    fingerprints = copy.deepcopy(dict(fingerprints))
                    fingerprints["source"] = current_digest
                    resource_summary["fingerprints"] = fingerprints
                checkpoint["resource_summary"] = resource_summary
            checkpoint["normalized_ast"] = None
            checkpoint["target_map"] = {
                "revision": checkpoint["source_ast"]["revision"],
                "entries": {},
                "view_sha256": _digest({}),
            }
            _validate_for_store(checkpoint)
        except LedgerError as exc:
            raise CheckpointError(f"decision ledger projection failed: {exc}", code=exc.code) from exc
        except CheckpointError:
            raise
        except Exception as exc:
            raise CheckpointError(f"source AST recovery failed: {exc}", code="source_ast_rebuild_failed") from exc
    return checkpoint


def validate_status_transition(current: str, target: str) -> None:
    if current not in CHECKPOINT_STATUSES or target not in CHECKPOINT_STATUSES:
        raise CheckpointError(f"unknown checkpoint status {current!r} or {target!r}", code="unknown_status")
    if target not in _TRANSITIONS[current]:
        raise CheckpointError(f"invalid status transition {current!r} -> {target!r}", code="invalid_status_transition")


def transition_status(checkpoint: Mapping[str, Any], status: str, *, resume_stage: str | None = None) -> dict[str, Any]:
    """Return a validated status transition without mutating the input."""

    _validate_for_store(checkpoint)
    current = str(checkpoint["status"])
    validate_status_transition(current, status)
    if status == "waiting_external" and resume_stage not in STAGES:
        raise CheckpointError("waiting_external requires a valid resume stage", code="invalid_resume_stage")
    if status != "waiting_external" and resume_stage is not None:
        raise CheckpointError("resume stage is only valid for waiting_external", code="invalid_resume_stage")
    result = copy.deepcopy(dict(checkpoint))
    result["status"] = status
    result["resume_stage"] = resume_stage if status == "waiting_external" else None
    _validate_for_store(result)
    return result


def _response_id(response: Mapping[str, Any]) -> str:
    request_id = response.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise CheckpointError("response request_id is required", code="invalid_response")
    return request_id


def record_response(checkpoint: Mapping[str, Any], response: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Record one response exactly once and return ``(checkpoint, outcome)``."""

    _validate_for_store(checkpoint)
    if not isinstance(response, Mapping):
        raise CheckpointError("response must be an object", code="invalid_response")
    request_id = _response_id(response)
    requests = checkpoint.get("requests", [])
    request_ids = {item.get("request_id") for item in requests if isinstance(item, Mapping)}
    if request_ids and request_id not in request_ids:
        raise CheckpointError(f"response references unknown request {request_id}", code="stale_response")
    response_digest = _digest(dict(response))
    result = copy.deepcopy(dict(checkpoint))
    responses = result.setdefault("responses", [])
    for existing in responses:
        if not isinstance(existing, Mapping) or existing.get("request_id") != request_id:
            continue
        existing_digest = existing.get("response_sha256") or _digest({key: value for key, value in existing.items() if key not in {"response_sha256", "applied", "apply_started"}})
        if existing_digest != response_digest:
            raise CheckpointError(f"response conflict for {request_id}", code="response_conflict")
        return result, "already_recorded"
    record = copy.deepcopy(dict(response))
    record["response_sha256"] = response_digest
    record["applied"] = False
    responses.append(record)
    return result, "recorded"


def apply_response(
    checkpoint: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    apply: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    path: Path | str | None = None,
    crash_at: str | None = None,
) -> dict[str, Any]:
    """Persist a response, then apply it once, then persist the applied mark."""

    recorded, outcome = record_response(checkpoint, response)
    if outcome == "already_recorded":
        existing = next(item for item in recorded["responses"] if item.get("request_id") == response.get("request_id"))
        if existing.get("applied"):
            return recorded
        if existing.get("apply_started"):
            raise CheckpointError(
                f"response application is pending for {response.get('request_id')}",
                code="response_application_pending",
            )
    if path is not None:
        save_checkpoint(recorded, path, crash_at="before_commit" if crash_at == "before_response_commit" else None)
    if crash_at == "before_apply":
        raise CheckpointError("injected failure before apply", code="injected_failure")
    for item in recorded["responses"]:
        if item.get("request_id") == response.get("request_id"):
            item["apply_started"] = True
            break
    if path is not None:
        save_checkpoint(recorded, path)
    candidate = apply(copy.deepcopy(recorded), copy.deepcopy(dict(response)))
    if not isinstance(candidate, dict):
        raise CheckpointError("response applier must return a checkpoint object", code="invalid_apply_result")
    matched = [item for item in candidate.get("responses", []) if item.get("request_id") == response.get("request_id")]
    if len(matched) != 1:
        raise CheckpointError("applier removed or duplicated the response record", code="response_apply_conflict")
    matched[0]["applied"] = True
    matched[0]["apply_started"] = False
    _validate_for_store(candidate)
    if path is not None:
        save_checkpoint(candidate, path, crash_at="after_commit" if crash_at == "after_apply_commit" else None)
    return candidate


def complete_pending_response(
    checkpoint: Mapping[str, Any],
    request_id: str,
    *,
    transaction_id: str,
    completed_checkpoint: Mapping[str, Any],
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Commit a caller-provided completed snapshot without reapplying a response."""

    _validate_for_store(checkpoint)
    _validate_for_store(completed_checkpoint)
    pending_matches = [item for item in checkpoint.get("responses", []) if item.get("request_id") == request_id]
    result = copy.deepcopy(dict(completed_checkpoint))
    matches = [item for item in result.get("responses", []) if item.get("request_id") == request_id]
    if len(pending_matches) != 1:
        raise CheckpointError(f"unknown pending response {request_id}", code="unknown_response")
    if len(matches) != 1:
        raise CheckpointError(f"unknown pending response {request_id}", code="unknown_response")
    for field in ("checkpoint_schema_version", "run_id", "source_facts", "decision_ledger", "contracts", "modules", "requests", "candidate_baseline_id"):
        if _digest(checkpoint.get(field)) != _digest(result.get(field)):
            raise CheckpointError(
                f"completed checkpoint identity does not match response {request_id}",
                code="checkpoint_identity_mismatch",
            )
    pending_response = pending_matches[0]
    response = matches[0]
    pending_response_digest = pending_response.get("response_sha256") or _digest(
        {key: value for key, value in pending_response.items() if key not in {"response_sha256", "applied", "apply_started"}}
    )
    if response.get("response_sha256") != pending_response_digest:
        raise CheckpointError(
            f"completed response does not match pending response {request_id}",
            code="response_identity_mismatch",
        )
    pending_payload = {key: value for key, value in pending_response.items() if key not in {"response_sha256", "applied", "apply_started"}}
    completed_payload = {key: value for key, value in response.items() if key not in {"response_sha256", "applied", "apply_started"}}
    if pending_payload != completed_payload:
        raise CheckpointError(
            f"completed response payload does not match pending response {request_id}",
            code="response_identity_mismatch",
        )
    if not response.get("apply_started") and not response.get("applied"):
        raise CheckpointError(f"response {request_id} was not started", code="response_not_pending")
    if response.get("transaction_id") != transaction_id:
        raise CheckpointError(
            f"transaction {transaction_id} is not bound to response {request_id}",
            code="transaction_state_mismatch",
        )
    if transaction_id not in completed_checkpoint.get("transaction_content_digests", {}):
        raise CheckpointError(
            f"transaction {transaction_id} is not recorded for response {request_id}",
            code="transaction_state_missing",
        )
    if response.get("transaction_content_sha256") != completed_checkpoint["transaction_content_digests"][transaction_id]:
        raise CheckpointError(
            f"transaction {transaction_id} content does not match response {request_id}",
            code="transaction_state_mismatch",
        )
    if not any(
        item.get("transaction_id") == transaction_id
        and item.get("content_sha256") == response.get("transaction_content_sha256")
        for item in completed_checkpoint.get("applied_transactions", [])
        if isinstance(item, Mapping)
    ):
        raise CheckpointError(
            f"transaction {transaction_id} is not present in applied transaction state",
            code="transaction_state_missing",
        )
    if response.get("applied"):
        return result
    response["applied"] = True
    response["apply_started"] = False
    _validate_for_store(result)
    if path is not None:
        save_checkpoint(result, path)
    return result


__all__ = [
    "CheckpointError",
    "apply_response",
    "complete_pending_response",
    "load_checkpoint",
    "record_response",
    "save_checkpoint",
    "transition_status",
    "validate_status_transition",
]
