"""Request identity and exactly-once response application for native runs.

The module owns the response identity rule (which fields make up a request or a
response digest) and the exactly-once application state machine.  It depends on
``checkpoint_store`` only through its public API: the error type, the atomic
save entrypoint and the store level validation helpers.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Mapping

from .checkpoint_store import (
    CheckpointError,
    save_checkpoint,
    validate_checkpoint_for_store,
    validate_embedded_fingerprints,
)
from .decision_ledger import APPLICATION_FIELDS, LedgerError, verify_response_transaction_proof
from .digest import digest
from .response_identity import (
    record_response as _record_response,
    request_digest,
    response_digest,
)


def content_digest(value: Any) -> str:
    return digest(value)


def record_response(checkpoint: Mapping[str, Any], response: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Validate the checkpoint, then apply the pure response transition."""

    validate_checkpoint_for_store(checkpoint)
    validate_embedded_fingerprints(checkpoint)
    return _record_response(checkpoint, response)


def apply_response(
    checkpoint: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    apply: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    path: Path | str | None = None,
    crash_at: str | None = None,
) -> dict[str, Any]:
    recorded, outcome = record_response(checkpoint, response)
    if outcome == "already_recorded":
        existing = next(item for item in recorded["responses"] if item.get("request_id") == response.get("request_id"))
        if existing.get("applied"):
            return recorded
        if existing.get("apply_started"):
            raise CheckpointError(f"response application is pending for {response.get('request_id')}", code="response_application_pending")
    if path is not None:
        save_checkpoint(recorded, path, crash_at="before_commit" if crash_at == "before_response_commit" else None)
    if crash_at == "before_apply":
        raise CheckpointError("injected failure before apply", code="injected_failure")
    next(item for item in recorded["responses"] if item.get("request_id") == response.get("request_id"))["apply_started"] = True
    if path is not None:
        save_checkpoint(recorded, path)
    candidate = apply(copy.deepcopy(recorded), copy.deepcopy(dict(response)))
    if not isinstance(candidate, dict):
        raise CheckpointError("response applier must return a checkpoint object", code="invalid_apply_result")
    matched = [item for item in candidate.get("responses", []) if item.get("request_id") == response.get("request_id")]
    if len(matched) != 1:
        raise CheckpointError("applier removed or duplicated the response record", code="response_apply_conflict")
    matched[0]["applied"], matched[0]["apply_started"] = True, False
    validate_checkpoint_for_store(candidate)
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
    validate_checkpoint_for_store(checkpoint)
    validate_checkpoint_for_store(completed_checkpoint)
    pending = [item for item in checkpoint.get("responses", []) if item.get("request_id") == request_id]
    result = copy.deepcopy(dict(completed_checkpoint))
    matches = [item for item in result.get("responses", []) if item.get("request_id") == request_id]
    if len(pending) != 1 or len(matches) != 1:
        raise CheckpointError(f"unknown pending response {request_id}", code="unknown_response")
    for field in ("checkpoint_schema_version", "run_id", "source_facts", "decision_ledger", "contracts", "modules", "requests", "candidate_baseline_id"):
        if content_digest(checkpoint.get(field)) != content_digest(result.get(field)):
            raise CheckpointError(f"completed checkpoint identity does not match response {request_id}", code="checkpoint_identity_mismatch")
    response = matches[0]
    previous = pending[0]
    digest = previous.get("response_sha256") or response_digest(previous)
    if response.get("response_sha256") != digest or {k: v for k, v in previous.items() if k not in APPLICATION_FIELDS} != {k: v for k, v in response.items() if k not in APPLICATION_FIELDS}:
        raise CheckpointError(f"completed response does not match pending response {request_id}", code="response_identity_mismatch")
    if not response.get("apply_started") and not response.get("applied"):
        raise CheckpointError(f"response {request_id} was not started", code="response_not_pending")
    try:
        verify_response_transaction_proof(
            completed_checkpoint,
            request_id,
            transaction_id=transaction_id,
            response=response,
        )
    except LedgerError as exc:
        raise CheckpointError(str(exc), code=exc.code) from exc
    if response.get("applied"):
        return result
    response["applied"], response["apply_started"] = True, False
    validate_checkpoint_for_store(result)
    if path is not None:
        save_checkpoint(result, path)
    return result


__all__ = ["apply_response", "complete_pending_response", "content_digest", "record_response", "request_digest", "response_digest"]
