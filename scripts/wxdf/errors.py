"""Shared error types for the persistence modules.

``CheckpointError`` used to live in ``checkpoint_store``.  It is defined here so
the request-append journal and the response identity module can raise it without
importing ``checkpoint_store``, which keeps the persistence module graph acyclic.
``checkpoint_store`` re-exports the name, so existing imports keep working.
"""

from __future__ import annotations


class CheckpointError(RuntimeError):
    """Raised when a checkpoint cannot be safely stored or resumed."""

    def __init__(self, message: str, *, code: str = "checkpoint_error") -> None:
        self.code = code
        super().__init__(message)


__all__ = ["CheckpointError"]
