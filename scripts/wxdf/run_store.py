"""Compatibility facade for modular checkpoint and response storage."""

from .checkpoint_store import (
    CheckpointError,
    append_native_waiting_requests,
    candidate_baseline_digest,
    checkpoint_target_map,
    complete_native_checkpoint,
    file_digest,
    load_checkpoint,
    overlay_entries,
    prepare_native_resume,
    save_checkpoint,
    transition_status,
    validate_status_transition,
    write_native_waiting_checkpoint,
)
from .checkpoint_store import _replace_bytes, os
from .response_store import (
    apply_response,
    complete_pending_response,
    content_digest,
    record_response,
    request_digest,
    response_digest,
)

__all__ = [
    "CheckpointError",
    "append_native_waiting_requests",
    "apply_response",
    "candidate_baseline_digest",
    "checkpoint_target_map",
    "complete_native_checkpoint",
    "complete_pending_response",
    "content_digest",
    "file_digest",
    "load_checkpoint",
    "overlay_entries",
    "prepare_native_resume",
    "record_response",
    "request_digest",
    "response_digest",
    "save_checkpoint",
    "transition_status",
    "validate_status_transition",
    "write_native_waiting_checkpoint",
]
