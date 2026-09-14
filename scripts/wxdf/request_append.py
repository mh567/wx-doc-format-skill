"""Write-ahead request-append journal for native file checkpoints.

Later-stage native requests are appended to the request file while the waiting
checkpoint stays readable.  The append is guarded by a cross-process lock and a
write-ahead journal so a crash between the request-file replace and the
checkpoint save is replayable.  The journal schema is version ``1.0``.

The module depends only on ``digest``, ``errors`` and ``response_identity``.
It reaches the checkpoint commit entrypoint through an injected callback
(``register_checkpoint_saver``) instead of importing ``checkpoint_store``, so
the persistence module graph stays acyclic.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None

from .digest import canonical_json
from .errors import CheckpointError
from .response_identity import request_digest


__all__ = ["append_native_waiting_requests"]

# The checkpoint commit entrypoint is injected by ``checkpoint_store`` so this
# module never imports it back.
_checkpoint_saver: Callable[..., Any] | None = None


def register_checkpoint_saver(save_checkpoint: Callable[..., Any]) -> None:
    """Let ``checkpoint_store`` inject its atomic checkpoint commit entrypoint."""

    global _checkpoint_saver
    _checkpoint_saver = save_checkpoint


def _request_append_journal_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.with_name(checkpoint_path.name + ".request-append.journal")


def _bytes_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _replace_bytes(path: Path, payload: bytes, suffix: str) -> None:
    temporary = path.with_name(path.name + suffix)
    temporary.write_bytes(payload)
    os.replace(temporary, path)


@contextmanager
def _exclusive_file_lock(lock_path: Path):
    """Hold one cross-process file lock on supported platforms."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if _fcntl is not None:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
            return
        if _msvcrt is not None:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_UNLCK, 1)
            return
        raise CheckpointError(
            "no supported request append lock backend",
            code="lock_backend_unavailable",
        )


def _recover_native_request_append(checkpoint_path: Path) -> None:
    journal_path = _request_append_journal_path(checkpoint_path)
    if not journal_path.is_file():
        return
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if journal.get("schema_version") != "1.0":
            raise ValueError("unsupported journal version")
        request_path = Path(journal["requests_path"])
        checkpoint_request_path = Path(checkpoint["files"]["requests_path"])
        if request_path.resolve() != checkpoint_request_path.resolve():
            raise ValueError("journal request path does not match checkpoint")
        previous = base64.b64decode(journal["previous_base64"], validate=True)
        replacement = base64.b64decode(journal["replacement_base64"], validate=True)
        previous_digest = _bytes_digest(previous)
        replacement_digest = _bytes_digest(replacement)
        if (
            previous_digest != journal.get("previous_sha256")
            or replacement_digest != journal.get("replacement_sha256")
        ):
            raise ValueError("journal content digest changed")
        checkpoint_digest = (
            checkpoint.get("resource_summary", {})
            .get("fingerprints", {})
            .get("requests")
        )
        current = request_path.read_bytes()
        if checkpoint_digest == replacement_digest:
            if current != replacement:
                raise ValueError("committed request append does not match its file")
        elif checkpoint_digest == previous_digest:
            if current == replacement:
                _replace_bytes(request_path, previous, ".append-recovery.tmp")
            elif current != previous:
                raise ValueError("request file changed during append recovery")
        else:
            raise ValueError("checkpoint request digest does not match append journal")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CheckpointError(
            f"cannot recover native request append: {exc}",
            code="request_append_recovery_failed",
        ) from exc
    request_path.with_name(request_path.name + ".append.tmp").unlink(missing_ok=True)
    journal_path.unlink(missing_ok=True)


def append_native_waiting_requests(
    checkpoint_path: Path | str,
    checkpoint: Mapping[str, Any],
    requests: list[dict[str, Any]],
    *,
    crash_at: str | None = None,
) -> dict[str, Any]:
    """Serialize cooperative append writers through checkpoint commit."""

    checkpoint_path = _path(checkpoint_path)
    try:
        request_path = Path(checkpoint["files"]["requests_path"])
    except (KeyError, TypeError) as exc:
        raise CheckpointError(
            "native checkpoint has no request path",
            code="invalid_checkpoint",
        ) from exc
    checkpoint_lock_path = checkpoint_path.with_name(
        checkpoint_path.name + ".request-append.lock"
    )
    request_lock_path = request_path.with_name(request_path.name + ".append.lock")
    with _exclusive_file_lock(checkpoint_lock_path):
        with _exclusive_file_lock(request_lock_path):
            return _append_native_waiting_requests_locked(
                checkpoint_path,
                checkpoint,
                requests,
                crash_at=crash_at,
            )


def _append_native_waiting_requests_locked(
    checkpoint_path: Path | str,
    checkpoint: Mapping[str, Any],
    requests: list[dict[str, Any]],
    *,
    crash_at: str | None = None,
) -> dict[str, Any]:
    """Append later-stage requests while preserving the waiting checkpoint."""

    if _checkpoint_saver is None:
        raise CheckpointError(
            "request append requires a registered checkpoint store",
            code="checkpoint_store_not_registered",
        )
    checkpoint_path = _path(checkpoint_path)
    updated = copy.deepcopy(dict(checkpoint))
    if updated.get("status") != "waiting_external":
        raise CheckpointError(
            "native requests can only be appended to a waiting checkpoint",
            code="invalid_checkpoint_state",
        )
    request_path = Path(updated["files"]["requests_path"])
    previous = request_path.read_bytes()
    expected = "".join(canonical_json(item) + "\n" for item in updated.get("requests", [])).encode("utf-8")
    if previous != expected:
        raise CheckpointError(
            "native request file changed before later-stage append",
            code="request_identity_conflict",
        )
    existing_by_id = {
        str(item.get("request_id")): item
        for item in updated.get("requests", [])
        if isinstance(item, Mapping) and item.get("request_id")
    }
    for raw_request in requests:
        request = copy.deepcopy(dict(raw_request))
        request_id = str(request.get("request_id") or "")
        if not request_id:
            raise CheckpointError("native request has no request ID", code="invalid_request")
        existing = existing_by_id.get(request_id)
        if existing is not None:
            if existing != request:
                raise CheckpointError(
                    f"request identity changed for {request_id}",
                    code="request_identity_conflict",
                )
            continue
        if request.get("request_sha256") != request_digest(request):
            raise CheckpointError(
                f"request content changed for {request_id}",
                code="request_identity_conflict",
            )
        updated["requests"].append(request)
        existing_by_id[request_id] = request
    if updated["requests"] == checkpoint.get("requests"):
        return updated

    payload = "".join(canonical_json(item) + "\n" for item in updated["requests"]).encode("utf-8")
    temporary = request_path.with_name(request_path.name + ".append.tmp")
    journal_path = _request_append_journal_path(checkpoint_path)
    temporary.write_bytes(payload)
    journal = {
        "schema_version": "1.0",
        "requests_path": str(request_path),
        "previous_sha256": _bytes_digest(previous),
        "replacement_sha256": _bytes_digest(payload),
        "previous_base64": base64.b64encode(previous).decode("ascii"),
        "replacement_base64": base64.b64encode(payload).decode("ascii"),
    }
    _replace_bytes(
        journal_path,
        (canonical_json(journal) + "\n").encode("utf-8"),
        ".tmp",
    )
    try:
        if request_path.read_bytes() != previous:
            journal_path.unlink(missing_ok=True)
            raise CheckpointError(
                "native request file changed immediately before later-stage append",
                code="request_identity_conflict",
            )
        os.replace(temporary, request_path)
        if crash_at == "after_request_replace":
            raise SystemExit("injected request append crash")
        updated["resource_summary"]["fingerprints"]["requests"] = _bytes_digest(payload)
        updated["budget"]["limit"] = len(updated["requests"])
        updated["round"] = int(updated.get("round") or 0) + 1
        _checkpoint_saver(updated, checkpoint_path)
    except Exception:
        if request_path.read_bytes() == payload:
            _replace_bytes(request_path, previous, ".rollback.tmp")
        journal_path.unlink(missing_ok=True)
        raise
    journal_path.unlink(missing_ok=True)
    return updated


def _path(value: Path | str) -> Path:
    return value if isinstance(value, Path) else Path(value)
