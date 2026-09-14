"""Response content-address identities and the pure response recording logic.

The module owns the response identity rule (which fields make up a request or a
response digest) and the pure, dict-level ``record_response`` transition.  It
depends only on ``digest`` and ``errors``, so ``checkpoint_store`` and
``request_append`` can reach it without importing ``response_store``.  The
public ``response_store`` re-exports these names and wraps ``record_response``
with the store-level validation, so existing imports keep working.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .digest import digest
from .errors import CheckpointError

# Response fields that carry the transaction proof instead of response content.
_APPLICATION_FIELDS = frozenset(
    {"response_sha256", "applied", "apply_started", "transaction_id", "transaction_content_sha256"}
)


def content_digest(value: Any) -> str:
    return digest(value)


def request_digest(request: Mapping[str, Any]) -> str:
    if not isinstance(request, Mapping):
        raise CheckpointError("request must be an object", code="invalid_request")
    return content_digest({key: value for key, value in request.items() if key != "request_sha256"})


def response_digest(response: Mapping[str, Any]) -> str:
    if not isinstance(response, Mapping):
        raise CheckpointError("response must be an object", code="invalid_response")
    return content_digest({key: value for key, value in response.items() if key not in _APPLICATION_FIELDS})


def _response_id(response: Mapping[str, Any]) -> str:
    value = response.get("request_id")
    if not isinstance(value, str) or not value:
        raise CheckpointError("response request_id is required", code="invalid_response")
    return value


def record_response(checkpoint: Mapping[str, Any], response: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Record one response on a copied checkpoint without validating it.

    The store-level entrypoint in ``response_store`` validates the checkpoint
    before calling this pure transition.
    """

    if not isinstance(response, Mapping):
        raise CheckpointError("response must be an object", code="invalid_response")
    request_id = _response_id(response)
    requests = checkpoint.get("requests", [])
    matching = [item for item in requests if isinstance(item, Mapping) and item.get("request_id") == request_id]
    request_ids = {item.get("request_id") for item in requests if isinstance(item, Mapping)}
    if request_ids and not matching:
        raise CheckpointError(f"response references unknown request {request_id}", code="stale_response")
    if len(matching) > 1:
        raise CheckpointError(f"request identity is repeated for {request_id}", code="request_identity_conflict")
    if matching and matching[0].get("request_sha256") is not None and matching[0]["request_sha256"] != request_digest(matching[0]):
        raise CheckpointError(f"request content changed for {request_id}", code="request_identity_conflict")
    incoming = response_digest(response)
    result = copy.deepcopy(dict(checkpoint))
    responses = result.setdefault("responses", [])
    for existing in responses:
        if isinstance(existing, Mapping) and existing.get("response_id") is not None and existing.get("response_id") == response.get("response_id") and existing.get("request_id") != request_id:
            raise CheckpointError(f"response identity is reused for {request_id}", code="response_identity_conflict")
        if not isinstance(existing, Mapping) or existing.get("request_id") != request_id:
            continue
        if (existing.get("response_sha256") or response_digest(existing)) != incoming:
            raise CheckpointError(f"response conflict for {request_id}", code="response_conflict")
        return result, "already_recorded"
    record = copy.deepcopy(dict(response))
    record["response_sha256"] = incoming
    record["applied"] = False
    responses.append(record)
    return result, "recorded"


__all__ = [
    "_APPLICATION_FIELDS",
    "content_digest",
    "record_response",
    "request_digest",
    "response_digest",
]
