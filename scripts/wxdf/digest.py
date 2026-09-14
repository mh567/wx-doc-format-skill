"""Single owner of the canonical-JSON-then-sha256 identity rule.

Checkpoints compare stored digests, so the canonical JSON serialisation and the
``sha256:<hex>`` string produced here are a stable contract. Every module that
needs a content identity imports one of these helpers instead of copying the
rule.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text that defines a serialised identity."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_hex(value: Any) -> str:
    """Return the bare sha256 hex digest of the canonical JSON of ``value``."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest(value: Any) -> str:
    """Return the shared content identity for ``value`` as ``sha256:<hex>``."""

    return "sha256:" + digest_hex(value)


__all__ = ["canonical_json", "digest", "digest_hex"]
