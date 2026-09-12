"""AC-3 architecture contracts for role ownership and release boundaries."""

from __future__ import annotations

import importlib
import importlib.util
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
    compatibility = importlib.import_module("wxdf.run_store")

    assert checkpoint_store.CheckpointError.__module__ == "wxdf.checkpoint_store"
    assert checkpoint_store.save_checkpoint.__module__ == "wxdf.checkpoint_store"
    assert checkpoint_store.load_checkpoint.__module__ == "wxdf.checkpoint_store"
    assert response_store.content_digest.__module__ == "wxdf.response_store"
    assert response_store.record_response.__module__ == "wxdf.response_store"
    assert compatibility.CheckpointError is checkpoint_store.CheckpointError
    assert compatibility.content_digest is response_store.content_digest


def test_release_module_surface_contains_role_modules_and_excludes_retired_modules() -> None:
    path = ROOT / "packaging" / "nuitka" / "build_distribution.py"
    spec = importlib.util.spec_from_file_location("wx_nuitka_build_ac3", path)
    assert spec is not None and spec.loader is not None
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    release_modules = set(build.release_module_names())

    assert {
        "wxdf.semantic_modules",
        "wxdf.native_stages",
        "wxdf.checkpoint_store",
        "wxdf.response_store",
    } <= release_modules
    assert not release_modules.intersection(build.RETIRED_RELEASE_MODULES)
    assert not any(name.startswith("wxdf.legacy_") for name in release_modules)


def test_release_manifest_rejects_legacy_runtime_modules() -> None:
    build_path = ROOT / "packaging" / "nuitka" / "build_distribution.py"
    build_spec = importlib.util.spec_from_file_location("wx_nuitka_build_ac3_guard", build_path)
    assert build_spec is not None and build_spec.loader is not None
    build = importlib.util.module_from_spec(build_spec)
    build_spec.loader.exec_module(build)

    smoke_path = ROOT / "packaging" / "nuitka" / "smoke_distribution.py"
    smoke_spec = importlib.util.spec_from_file_location("wx_nuitka_smoke_ac3_guard", smoke_path)
    assert smoke_spec is not None and smoke_spec.loader is not None
    smoke = importlib.util.module_from_spec(smoke_spec)
    smoke_spec.loader.exec_module(smoke)

    manifest = {
        "modular": build.modular_module_manifest(
            runtime_modules=[*build.release_module_names(), "wxdf.legacy_pipeline"]
        )
    }
    with pytest.raises(RuntimeError, match="Retired release module is forbidden"):
        smoke.validate_modular_manifest(manifest)


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
