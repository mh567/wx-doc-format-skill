"""Cross-process publication locks keyed by target directories."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Mapping

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None

LOCK_NAME = ".wxdf-publication.lock"
_local = threading.local()


def _targets(values: Iterable[Path | str] | Mapping[Path, bytes] | Path | str) -> list[Path]:
    if isinstance(values, Mapping):
        return [Path(value) for value in values]
    if isinstance(values, (str, Path)):
        return [Path(values)]
    return [Path(value) for value in values]


def publication_lock_paths(values: Iterable[Path | str] | Mapping[Path, bytes] | Path | str) -> list[Path]:
    """Return one lock path per target parent directory, in stable order."""

    parents = {Path(value).resolve().parent for value in _targets(values)}
    usable = {
        parent
        for parent in parents
        if not parent.exists() or parent.is_dir()
    }
    return [parent / LOCK_NAME for parent in sorted(usable, key=str)]


def held_lock_paths() -> list[str]:
    """Return the publication lock paths currently held by this thread."""

    return sorted(getattr(_local, "held", {}))


def _open_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        handle = os.fdopen(descriptor, "a+b")
    except Exception:
        os.close(descriptor)
        raise
    try:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        elif _msvcrt is not None:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
        else:
            raise RuntimeError("no supported publication lock backend")
    except Exception:
        handle.close()
        raise
    return handle


def _release_lock(handle) -> None:
    try:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        elif _msvcrt is not None:
            handle.seek(0)
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


@contextmanager
def publication_lock(values: Iterable[Path | str] | Mapping[Path, bytes] | Path | str):
    """Hold every target directory lock, reentrant within one thread.

    The lock file stays in the target directory as a stable coordination point.
    """

    held = getattr(_local, "held", None)
    if held is None:
        held = {}
        _local.held = held
    tokens: list[tuple[str, bool]] = []
    try:
        for lock_path in publication_lock_paths(values):
            key = str(lock_path)
            if key in held:
                held[key][1] += 1
                tokens.append((key, False))
            else:
                held[key] = [_open_lock(lock_path), 1]
                tokens.append((key, True))
        yield
    finally:
        for key, fresh in reversed(tokens):
            state = held.get(key)
            if state is None:
                continue
            handle, depth = state
            if depth > 1:
                held[key] = [handle, depth - 1]
                continue
            if fresh:
                _release_lock(handle)
            del held[key]
