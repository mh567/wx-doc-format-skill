"""Request identity and exactly-once response application for native runs."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Mapping

_APPLICATION_FIELDS = frozenset({"response_sha256", "applied", "apply_started", "transaction_id", "transaction_content_sha256"})

def _store() -> Any:
    from . import checkpoint_store
    return checkpoint_store

def content_digest(value: Any) -> str:
    return _store()._digest(value)

def request_digest(request: Mapping[str, Any]) -> str:
    if not isinstance(request, Mapping): raise _store().CheckpointError("request must be an object", code="invalid_request")
    return content_digest({key: value for key, value in request.items() if key != "request_sha256"})

def response_digest(response: Mapping[str, Any]) -> str:
    if not isinstance(response, Mapping): raise _store().CheckpointError("response must be an object", code="invalid_response")
    return content_digest({key: value for key, value in response.items() if key not in _APPLICATION_FIELDS})

def _response_id(response: Mapping[str, Any]) -> str:
    value = response.get("request_id")
    if not isinstance(value, str) or not value: raise _store().CheckpointError("response request_id is required", code="invalid_response")
    return value

def record_response(checkpoint: Mapping[str, Any], response: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    store = _store(); store._validate_for_store(checkpoint); store._validate_embedded_fingerprints(checkpoint)
    if not isinstance(response, Mapping): raise store.CheckpointError("response must be an object", code="invalid_response")
    request_id = _response_id(response); requests = checkpoint.get("requests", [])
    matching = [item for item in requests if isinstance(item, Mapping) and item.get("request_id") == request_id]
    request_ids = {item.get("request_id") for item in requests if isinstance(item, Mapping)}
    if request_ids and not matching: raise store.CheckpointError(f"response references unknown request {request_id}", code="stale_response")
    if len(matching) > 1: raise store.CheckpointError(f"request identity is repeated for {request_id}", code="request_identity_conflict")
    if matching and matching[0].get("request_sha256") is not None and matching[0]["request_sha256"] != request_digest(matching[0]): raise store.CheckpointError(f"request content changed for {request_id}", code="request_identity_conflict")
    incoming = response_digest(response); result = copy.deepcopy(dict(checkpoint)); responses = result.setdefault("responses", [])
    for existing in responses:
        if isinstance(existing, Mapping) and existing.get("response_id") is not None and existing.get("response_id") == response.get("response_id") and existing.get("request_id") != request_id: raise store.CheckpointError(f"response identity is reused for {request_id}", code="response_identity_conflict")
        if not isinstance(existing, Mapping) or existing.get("request_id") != request_id: continue
        if (existing.get("response_sha256") or response_digest(existing)) != incoming: raise store.CheckpointError(f"response conflict for {request_id}", code="response_conflict")
        return result, "already_recorded"
    record = copy.deepcopy(dict(response)); record["response_sha256"] = incoming; record["applied"] = False; responses.append(record)
    return result, "recorded"

def apply_response(checkpoint: Mapping[str, Any], response: Mapping[str, Any], *, apply: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]], path: Path | str | None = None, crash_at: str | None = None) -> dict[str, Any]:
    store = _store(); recorded, outcome = record_response(checkpoint, response)
    if outcome == "already_recorded":
        existing = next(item for item in recorded["responses"] if item.get("request_id") == response.get("request_id"))
        if existing.get("applied"): return recorded
        if existing.get("apply_started"): raise store.CheckpointError(f"response application is pending for {response.get('request_id')}", code="response_application_pending")
    if path is not None: store.save_checkpoint(recorded, path, crash_at="before_commit" if crash_at == "before_response_commit" else None)
    if crash_at == "before_apply": raise store.CheckpointError("injected failure before apply", code="injected_failure")
    next(item for item in recorded["responses"] if item.get("request_id") == response.get("request_id"))["apply_started"] = True
    if path is not None: store.save_checkpoint(recorded, path)
    candidate = apply(copy.deepcopy(recorded), copy.deepcopy(dict(response)))
    if not isinstance(candidate, dict): raise store.CheckpointError("response applier must return a checkpoint object", code="invalid_apply_result")
    matched = [item for item in candidate.get("responses", []) if item.get("request_id") == response.get("request_id")]
    if len(matched) != 1: raise store.CheckpointError("applier removed or duplicated the response record", code="response_apply_conflict")
    matched[0]["applied"], matched[0]["apply_started"] = True, False; store._validate_for_store(candidate)
    if path is not None: store.save_checkpoint(candidate, path, crash_at="after_commit" if crash_at == "after_apply_commit" else None)
    return candidate

def complete_pending_response(checkpoint: Mapping[str, Any], request_id: str, *, transaction_id: str, completed_checkpoint: Mapping[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    store = _store(); store._validate_for_store(checkpoint); store._validate_for_store(completed_checkpoint)
    pending = [item for item in checkpoint.get("responses", []) if item.get("request_id") == request_id]; result = copy.deepcopy(dict(completed_checkpoint)); matches = [item for item in result.get("responses", []) if item.get("request_id") == request_id]
    if len(pending) != 1 or len(matches) != 1: raise store.CheckpointError(f"unknown pending response {request_id}", code="unknown_response")
    for field in ("checkpoint_schema_version", "run_id", "source_facts", "decision_ledger", "contracts", "modules", "requests", "candidate_baseline_id"):
        if content_digest(checkpoint.get(field)) != content_digest(result.get(field)): raise store.CheckpointError(f"completed checkpoint identity does not match response {request_id}", code="checkpoint_identity_mismatch")
    response = matches[0]; previous = pending[0]; digest = previous.get("response_sha256") or response_digest(previous)
    if response.get("response_sha256") != digest or {k:v for k,v in previous.items() if k not in _APPLICATION_FIELDS} != {k:v for k,v in response.items() if k not in _APPLICATION_FIELDS}: raise store.CheckpointError(f"completed response does not match pending response {request_id}", code="response_identity_mismatch")
    if not response.get("apply_started") and not response.get("applied"): raise store.CheckpointError(f"response {request_id} was not started", code="response_not_pending")
    if response.get("transaction_id") != transaction_id:
        raise store.CheckpointError(
            f"transaction {transaction_id} is not bound to response {request_id}",
            code="transaction_state_mismatch",
        )
    if transaction_id not in completed_checkpoint.get("transaction_content_digests", {}):
        raise store.CheckpointError(
            f"transaction {transaction_id} is not recorded for response {request_id}",
            code="transaction_state_missing",
        )
    if response.get("transaction_content_sha256") != completed_checkpoint["transaction_content_digests"][transaction_id]: raise store.CheckpointError(f"transaction {transaction_id} content does not match response {request_id}", code="transaction_state_mismatch")
    if not any(
        item.get("transaction_id") == transaction_id
        and item.get("content_sha256") == response.get("transaction_content_sha256")
        for item in completed_checkpoint.get("applied_transactions", [])
        if isinstance(item, Mapping)
    ):
        raise store.CheckpointError(
            f"transaction {transaction_id} is not present in applied transaction state",
            code="transaction_state_missing",
        )
    if response.get("applied"): return result
    response["applied"], response["apply_started"] = True, False; store._validate_for_store(result)
    if path is not None: store.save_checkpoint(result, path)
    return result

__all__ = ["apply_response", "complete_pending_response", "content_digest", "record_response", "request_digest", "response_digest"]
