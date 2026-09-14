"""T07 focused contracts: the request-append journal is its own module and the
``response_store`` seam is sealed.

Before T07 the write-ahead request-append journal (cross-process lock plus crash
recovery) lived in ``checkpoint_store``, which also re-exported ``response_store``
symbols from the bottom of the file.  That reverse re-export produced an import
cycle between the two modules.  After T07 the journal is an independent module and
``response_store`` depends only on ``checkpoint_store`` public API.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

WXDF = SCRIPTS / "wxdf"

RESPONSE_RE_EXPORTS = (
    "apply_response",
    "complete_pending_response",
    "content_digest",
    "record_response",
    "request_digest",
    "response_digest",
)


def _source(name: str) -> str:
    return (WXDF / name).read_text(encoding="utf-8")


def _module_level_imports(path: Path, module: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.extend(alias.name for alias in node.names)
    return names


def test_journal_is_an_independent_module() -> None:
    assert importlib.util.find_spec("wxdf.request_append") is not None
    journal = importlib.import_module("wxdf.request_append")
    assert journal.append_native_waiting_requests.__module__ == "wxdf.request_append"
    assert "append_native_waiting_requests" in journal.__all__


def test_journal_owns_the_lock_and_recovery_seam() -> None:
    journal = importlib.import_module("wxdf.request_append")
    for helper in ("_exclusive_file_lock", "_recover_native_request_append", "_request_append_journal_path"):
        assert hasattr(journal, helper), helper


def test_checkpoint_store_no_longer_re_exports_response_store_symbols() -> None:
    checkpoint_store = importlib.import_module("wxdf.checkpoint_store")
    for name in RESPONSE_RE_EXPORTS:
        assert not hasattr(checkpoint_store, name), name
        assert name not in getattr(checkpoint_store, "__all__", ()), name


def test_checkpoint_store_has_no_module_level_response_store_import() -> None:
    assert _module_level_imports(WXDF / "checkpoint_store.py", "response_store") == []


def test_response_store_uses_checkpoint_store_public_api_only() -> None:
    source = _source("response_store.py")
    assert "_validate_for_store" not in source
    assert "_validate_embedded_fingerprints" not in source
    imported = _module_level_imports(WXDF / "response_store.py", "checkpoint_store")
    assert imported, "response_store must depend on checkpoint_store"
    for name in imported:
        assert not name.startswith("_"), name


def test_checkpoint_store_keeps_its_deep_persistence_interface() -> None:
    checkpoint_store = importlib.import_module("wxdf.checkpoint_store")
    for name in (
        "CheckpointError",
        "save_checkpoint",
        "load_checkpoint",
        "prepare_native_resume",
        "complete_native_checkpoint",
        "write_native_waiting_checkpoint",
        "append_native_waiting_requests",
    ):
        assert hasattr(checkpoint_store, name), name


def test_journal_file_protocol_is_preserved() -> None:
    source = _source("request_append.py")
    assert ".request-append.journal" in source
    assert ".request-append.lock" in source
    assert '"schema_version": "1.0"' in source
