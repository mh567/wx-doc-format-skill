from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


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
from wxdf.kernel import ConversionRequest
from wxdf.review import mode_manifest


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_module("wx_modular_distribution_build", ROOT / "packaging" / "nuitka" / "build_distribution.py")
smoke = _load_module("wx_modular_distribution_smoke", ROOT / "packaging" / "nuitka" / "smoke_distribution.py")


def _write_runtime(path: Path, *, with_manifest: bool = True) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{version}"\n', encoding="utf-8")
    path.chmod(0o755)
    if with_manifest:
        _write_module_manifest(path.parent)


def _write_module_manifest(dist: Path, **overrides) -> None:
    manifest = build.modular_module_manifest(runtime_evidence="compilation_report")
    manifest.update(overrides)
    (dist / "wxdf-module-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_nuitka_command_explicitly_includes_the_complete_wxdf_surface(tmp_path: Path) -> None:
    command = build.nuitka_command(tmp_path)
    assert not any(argument.startswith("--include-package=") for argument in command)
    for module_name in build.release_module_names():
        assert f"--include-module={module_name}" in command
    assert "--nofollow-import-to=wxdf.migration" in command
    assert "--nofollow-import-to=wxdf.legacy_*" in command
    for module_name in (
        "docx_pipeline",
        "md_pipeline",
        "model_normalization",
        "template_finalizer",
        "docx_render",
        "word_model_renderer",
    ):
        assert f"--nofollow-import-to={module_name}" in command


def test_modular_manifest_matches_contracts_and_open_modes() -> None:
    manifest = build.modular_module_manifest()
    assert manifest["source_modules"] == list(build.release_module_names())
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
    assert set(manifest["open_modes"]) == {
        "modular_pure",
        "modular_online",
        "modular_file",
        "modular_review",
    }
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


def test_distribution_and_review_publish_the_same_native_modes() -> None:
    assert build.MODULAR_OPEN_MODES == mode_manifest()


def test_release_smoke_accepts_explicit_native_online_adapter_mode() -> None:
    modular = build.modular_module_manifest()

    smoke.validate_modular_manifest({"modular": modular})


def test_assembled_release_manifest_records_modular_runtime_surface(tmp_path: Path) -> None:
    dist = tmp_path / "runtime.dist"
    dist.mkdir()
    _write_runtime(dist / build.executable_name())

    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    smoke.validate_modular_manifest(manifest)
    assert manifest["modular"]["runtime_evidence"] == "compilation_report"
    assert (package / "runtime" / "VERSION").is_file()


def test_public_package_requires_compilation_report_runtime_manifest(tmp_path: Path) -> None:
    dist = tmp_path / "runtime.dist"
    dist.mkdir()
    _write_runtime(dist / build.executable_name(), with_manifest=False)

    with pytest.raises(RuntimeError, match="Runtime module manifest is missing"):
        build.assemble_package(dist, tmp_path / "output", build.platform_id())

    assert not (tmp_path / "output").exists()


def test_public_package_rejects_invalid_runtime_manifest_json(tmp_path: Path) -> None:
    dist = tmp_path / "runtime.dist"
    dist.mkdir()
    _write_runtime(dist / build.executable_name())
    (dist / "wxdf-module-manifest.json").write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Runtime module manifest is invalid"):
        build.assemble_package(dist, tmp_path / "output", build.platform_id())

    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"runtime_evidence": "explicit_nuitka_inclusion"}, "runtime_evidence"),
        ({"module_set_status": "mismatch"}, "module_set_status"),
        ({"runtime_modules": ["wxdf"]}, "source_modules and runtime_modules"),
        ({"forbidden_runtime_modules": ["wxdf.migration"]}, "forbidden_runtime_modules"),
        ({"missing_runtime_modules": ["wxdf.kernel"]}, "missing_runtime_modules"),
        ({"unexpected_runtime_modules": ["wxdf.extra"]}, "unexpected_runtime_modules"),
    ],
)
def test_public_package_rejects_unverified_runtime_manifest(
    tmp_path: Path,
    overrides: dict,
    message: str,
) -> None:
    dist = tmp_path / "runtime.dist"
    dist.mkdir()
    _write_runtime(dist / build.executable_name())
    _write_module_manifest(dist, **overrides)

    with pytest.raises(RuntimeError, match=message):
        build.assemble_package(dist, tmp_path / "output", build.platform_id())

    assert not (tmp_path / "output").exists()


def test_programmatic_request_defaults_to_native_identity() -> None:
    request_defaults = ConversionRequest(
        input_path=ROOT / "input" / "test_baseline.md",
        output_path=ROOT / "wx_output" / "modular-v3" / "t16-default.docx",
    )
    assert request_defaults.pipeline == "modular"
    assert request_defaults.list_policy == "wx-default-v2"
    cli_source = (ROOT / "scripts" / "main.py").read_text(encoding="utf-8")
    assert 'default="modular"' in cli_source


def test_release_module_set_excludes_development_and_retired_bridges() -> None:
    release_modules = set(build.release_module_names())

    assert "wxdf.migration" not in release_modules
    assert "wxdf.semantics.compat" not in release_modules
    assert not any(name.startswith("wxdf.legacy_") for name in release_modules)
    assert not any(name.endswith("_bridge") for name in release_modules)
    assert {
        "wxdf.semantics.equivalence",
        "wxdf.semantics.parser_equivalence",
        "wxdf.semantics.source_projection",
    }.isdisjoint(release_modules)
    assert "wxdf.semantics.source_ast" in release_modules


def test_compilation_report_surfaces_forbidden_wxdf_modules(tmp_path: Path) -> None:
    report = tmp_path / "compilation-report.xml"
    report.write_text(
        '<nuitka-compilation-report>'
        '<module name="wxdf.kernel"/><module name="wxdf.migration"/>'
        '<module name="wxdf.legacy_removed"/>'
        '<module_usage name="wxdf.legacy_reference_only"/>'
        '</nuitka-compilation-report>',
        encoding="utf-8",
    )

    assert build._report_wxdf_modules(report) == (
        "wxdf.kernel",
        "wxdf.legacy_removed",
        "wxdf.migration",
    )
    manifest = build.modular_module_manifest(
        runtime_modules=build._report_wxdf_modules(report),
        runtime_evidence="compilation_report",
    )
    assert manifest["module_set_status"] == "mismatch"
    assert manifest["forbidden_runtime_modules"] == [
        "wxdf.legacy_removed",
        "wxdf.migration",
    ]


def test_compilation_report_must_exist(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="compilation report is missing"):
        build._report_wxdf_modules(tmp_path / "missing.xml")


def test_compilation_report_must_contain_a_wxdf_module(tmp_path: Path) -> None:
    report = tmp_path / "compilation-report.xml"
    report.write_text(
        '<nuitka-compilation-report><module name="main"/></nuitka-compilation-report>',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="contains no wxdf modules"):
        build._report_wxdf_modules(report)


def test_compilation_report_rejects_truncated_xml(tmp_path: Path) -> None:
    report = tmp_path / "compilation-report.xml"
    report.write_text('<nuitka-compilation-report><module name="wxdf.kernel">', encoding="utf-8")

    with pytest.raises(RuntimeError, match="compilation report is invalid"):
        build._report_wxdf_modules(report)


def test_runtime_manifest_writer_uses_only_compilation_report_evidence(tmp_path: Path) -> None:
    dist = tmp_path / "runtime.dist"
    dist.mkdir()
    report = tmp_path / "compilation-report.xml"
    module_elements = "".join(
        f'<module name="{module_name}"/>'
        for module_name in build.release_module_names()
    )
    report.write_text(
        f"<nuitka-compilation-report>{module_elements}</nuitka-compilation-report>",
        encoding="utf-8",
    )

    build._write_runtime_module_manifest(dist, report)

    manifest = json.loads(
        (dist / "wxdf-module-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["runtime_evidence"] == "compilation_report"
    assert manifest["runtime_modules"] == list(build.release_module_names())
    assert manifest["module_set_status"] == "matched"


def test_runtime_manifest_writer_leaves_no_fallback_for_missing_report(tmp_path: Path) -> None:
    dist = tmp_path / "runtime.dist"
    dist.mkdir()

    with pytest.raises(RuntimeError, match="compilation report is missing"):
        build._write_runtime_module_manifest(dist, tmp_path / "missing.xml")

    assert not (dist / "wxdf-module-manifest.json").exists()


@pytest.mark.parametrize(
    "retired_literal",
    ["wxdf.legacy_pipeline", "wxdf.migration", "legacy_online", "legacy-custom"],
)
def test_release_smoke_rejects_any_retired_module_or_mode(retired_literal: str) -> None:
    modular = build.modular_module_manifest()
    if retired_literal.startswith("wxdf."):
        modular["source_modules"].append(retired_literal)
        modular["runtime_modules"].append(retired_literal)
    else:
        modular["open_modes"][retired_literal] = {
            "status": "enabled",
            "entrypoint": retired_literal,
            "contract": "retired",
        }

    with pytest.raises(RuntimeError, match=retired_literal.replace(".", r"\.")):
        smoke.validate_modular_manifest({"modular": modular})
