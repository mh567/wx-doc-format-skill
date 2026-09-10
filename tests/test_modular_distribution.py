from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.contracts import (
    AST_SCHEMA_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    PATCH_SCHEMA_VERSION,
    SOURCE_FACTS_SCHEMA_VERSION,
)
from wxdf.review import MODE_MANIFEST
from wxdf.kernel import ConversionRequest


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_module("wx_modular_distribution_build", ROOT / "packaging" / "nuitka" / "build_distribution.py")
smoke = _load_module("wx_modular_distribution_smoke", ROOT / "packaging" / "nuitka" / "smoke_distribution.py")


def _write_runtime(path: Path) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{version}"\n', encoding="utf-8")
    path.chmod(0o755)


def test_nuitka_command_explicitly_includes_the_complete_wxdf_surface(tmp_path: Path) -> None:
    command = build.nuitka_command(tmp_path)
    assert "--include-package=wxdf" in command
    assert "--include-package=wxdf.semantics" in command
    for module_name in build.wxdf_module_names():
        if module_name not in {"wxdf", "wxdf.semantics"}:
            assert f"--include-module={module_name}" in command


def test_modular_manifest_matches_contracts_and_open_modes() -> None:
    manifest = build.modular_module_manifest()
    assert manifest["source_modules"] == list(build.wxdf_module_names())
    assert manifest["runtime_modules"] == manifest["source_modules"]
    assert manifest["module_set_status"] == "matched"
    assert manifest["contract_versions"] == {
        "source_facts": SOURCE_FACTS_SCHEMA_VERSION,
        "ast": AST_SCHEMA_VERSION,
        "patch": PATCH_SCHEMA_VERSION,
        "checkpoint": CHECKPOINT_SCHEMA_VERSION,
        "capability_input": "2.0",
        "normalized": "1.0",
    }
    assert manifest["open_modes"] == MODE_MANIFEST
    assert manifest["open_modes"]["modular_online"] == {
        "status": "enabled",
        "entrypoint": "EnhancementExecutor",
        "contract": "T03",
    }
    assert manifest["open_modes"]["modular_file"] == {
        "status": "enabled",
        "entrypoint": "CheckpointStore",
        "contract": "T04",
    }
    assert manifest["open_modes"]["modular_review"] == {
        "status": "enabled",
        "entrypoint": "ReviewEvaluator",
        "contract": "T05",
    }


def test_release_smoke_accepts_explicit_native_online_adapter_mode() -> None:
    modular = build.modular_module_manifest()
    modular["open_modes"] = json.loads(json.dumps(MODE_MANIFEST))

    smoke.validate_modular_manifest({"modular": modular})


def test_assembled_release_manifest_records_modular_runtime_surface(tmp_path: Path) -> None:
    dist = tmp_path / "runtime.dist"
    dist.mkdir()
    _write_runtime(dist / build.executable_name())

    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    smoke.validate_modular_manifest(manifest)
    assert manifest["modular"]["runtime_evidence"] == "explicit_nuitka_inclusion"
    assert (package / "runtime" / "VERSION").is_file()


def test_legacy_remains_the_default_and_modular_requires_explicit_selection() -> None:
    request_defaults = ConversionRequest(
        input_path=ROOT / "input" / "test_baseline.md",
        output_path=ROOT / "wx_output" / "modular-v3" / "t16-default.docx",
    )
    assert request_defaults.pipeline == "legacy"
    assert request_defaults.list_policy == "legacy"
    modular = ConversionRequest(
        input_path=request_defaults.input_path,
        output_path=request_defaults.output_path,
        pipeline="modular",
        list_policy="legacy",
    )
    assert modular.pipeline == "modular"
    cli_source = (ROOT / "scripts" / "main.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--pipeline", choices=["legacy", "modular"], default="legacy")' in cli_source
