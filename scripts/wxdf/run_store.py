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
    _validate_for_store(checkpoint)
    destination = _path(path)
    _write_json_atomic(checkpoint, destination, crash_at=crash_at)
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


def load_checkpoint(
    path: Path | str,
    expected_fingerprints: Mapping[str, Any] | None = None,
    *,
    expected_contracts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load, validate and optionally bind a checkpoint to current inputs."""

    source = _path(path)
    try:
        checkpoint = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read checkpoint: {source}", code="checkpoint_read_failed") from exc
    if not isinstance(checkpoint, dict):
        raise CheckpointError("checkpoint root must be an object", code="invalid_checkpoint")
    _validate_for_store(checkpoint)
    if expected_fingerprints:
        _compare_fingerprints(checkpoint, expected_fingerprints)
    if expected_contracts:
        actual = checkpoint.get("contracts", {})
        for name, value in expected_contracts.items():
            if actual.get(name) != value:
                raise CheckpointError(f"contract mismatch for {name}", code="contract_mismatch")
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
        existing_digest = existing.get("response_sha256") or _digest({key: value for key, value in existing.items() if key not in {"response_sha256", "applied"}})
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
    if path is not None:
        save_checkpoint(recorded, path, crash_at="before_commit" if crash_at == "before_response_commit" else None)
    if crash_at == "before_apply":
        raise CheckpointError("injected failure before apply", code="injected_failure")
    candidate = apply(copy.deepcopy(recorded), copy.deepcopy(dict(response)))
    if not isinstance(candidate, dict):
        raise CheckpointError("response applier must return a checkpoint object", code="invalid_apply_result")
    matched = [item for item in candidate.get("responses", []) if item.get("request_id") == response.get("request_id")]
    if len(matched) != 1:
        raise CheckpointError("applier removed or duplicated the response record", code="response_apply_conflict")
    matched[0]["applied"] = True
    _validate_for_store(candidate)
    if path is not None:
        save_checkpoint(candidate, path, crash_at="after_commit" if crash_at == "after_apply_commit" else None)
    return candidate


__all__ = [
    "CheckpointError",
    "apply_response",
    "load_checkpoint",
    "record_response",
    "save_checkpoint",
    "transition_status",
    "validate_status_transition",
]
