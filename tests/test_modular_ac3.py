"""AC-3 architecture contracts for role ownership and release boundaries."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))


def test_native_registry_assigns_semantic_and_lifecycle_roles_to_deep_modules() -> None:
    from wxdf.registry import build_native_v2_registry

    registry = build_native_v2_registry()
    plan = registry.build({"pipeline": "modular", "list_policy": "wx-default-v2"})
    semantic_ids = {
        "source-facts-parser",
        "source-region",
        "document-structure",
        "protected-content",
        "list-semantics",
        "table-semantics",
        "caption-semantics",
        "appendix-semantics",
    }
    lifecycle_ids = {
        "native-source-parser",
        "native-candidate-set",
        "native-resolver",
        "native-transaction",
        "native-normalizer",
        "native-renderer",
        "native-auditor",
        "native-reviewer",
        "native-publisher",
    }
    assert {
        module_id
        for module_id in semantic_ids
        if registry.implementation_module(module_id) == "wxdf.semantic_modules"
    } == semantic_ids
    assert {
        module_id
        for module_id in lifecycle_ids
        if registry.implementation_module(module_id) == "wxdf.native_stages"
    } == lifecycle_ids
    assert [module.module_id for module in plan.modules]


def test_checkpoint_and_response_roles_have_separate_implementations() -> None:
    checkpoint_store = importlib.import_module("wxdf.checkpoint_store")
    response_store = importlib.import_module("wxdf.response_store")
    errors = importlib.import_module("wxdf.errors")

    # FU-3 moved CheckpointError to the low-level errors module; the store
    # re-exports it so callers keep one shared error type.
    assert checkpoint_store.CheckpointError is errors.CheckpointError
    assert errors.CheckpointError.__module__ == "wxdf.errors"
    assert checkpoint_store.save_checkpoint.__module__ == "wxdf.checkpoint_store"
    assert checkpoint_store.load_checkpoint.__module__ == "wxdf.checkpoint_store"
    assert response_store.content_digest.__module__ == "wxdf.response_store"
    assert response_store.record_response.__module__ == "wxdf.response_store"


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["--version"],
    ],
)
def test_stable_cli_entry_points_remain_available(argv: list[str], capsys) -> None:
    import main

    if argv == ["--help"]:
        with pytest.raises(SystemExit) as raised:
            main.main(argv)
        assert raised.value.code == 0
    else:
        assert main.main(argv) is None
    assert capsys.readouterr().out
