from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / "nuitka" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load("wx_t09_build", "build_distribution.py")
public = _load("wx_t09_public", "assemble_public_skill.py")
smoke = _load("wx_t09_smoke", "smoke_distribution.py")


def _clean_source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    (root / "VERSION").write_text("0.13.0\n", encoding="ascii")
    (root / "source.txt").write_text("frozen source\n", encoding="ascii")
    for args in (
        ["init", "-q"],
        ["add", "VERSION", "source.txt"],
        ["-c", "user.name=T09 fixture", "-c", "user.email=t09@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
    ):
        subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"},
        )
    return root


def fixture_source_identity(tmp_path: Path) -> dict:
    root = tmp_path / "source"
    if not root.exists():
        _clean_source(tmp_path)
    return build.capture_source_identity(root)


def write_runtime_fixture(runtime: Path, *, identity: dict | None = None) -> dict:
    runtime.mkdir(parents=True, exist_ok=True)
    identity = identity or fixture_source_identity(runtime.parent)
    executable = runtime / "wx-doc-format"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "0.13.0"\n', encoding="ascii")
    executable.chmod(0o755)
    report = runtime.parent / "private-compilation-report.xml"
    modules = "".join(f'<module name="{name}" filename="/private/source/{name}.py"/>' for name in build.release_module_names())
    report.write_text(
        '<nuitka-compilation-report completion="yes" mode="standalone">'
        + modules + '</nuitka-compilation-report>', encoding="utf-8",
    )
    build._write_runtime_module_manifest(runtime, report, source_identity=identity)
    return identity


def write_archive_fixture(
    artifacts: Path, platform_id: str, *, identity: dict | None = None, marker: str | None = None,
) -> Path:
    runtime = artifacts / f"{platform_id}.dist"
    write_runtime_fixture(runtime, identity=identity)
    package = build.assemble_package(runtime, artifacts, platform_id)
    if marker is not None:
        (package / "scripts" / "run.sh").write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{marker}'\n", encoding="ascii",
        )
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        build._write_integrity_files(package, platform_id, "0.13.0", manifest["modular"])
    return build.create_archive(package, platform_id)


def test_build_identity_pins_clean_git_source_and_rejects_drift(tmp_path: Path) -> None:
    root = _clean_source(tmp_path)
    identity = build.capture_source_identity(root)
    assert identity["version"] == "0.13.0"
    assert identity["commit"] == subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
    ).strip()
    assert len(identity["tree"]) == 40
    assert identity["toolchain"]["python"] == sys.version.split()[0]
    assert identity["toolchain"]["nuitka"]

    (root / "source.txt").write_text("changed source\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="SOURCE_DIRTY"):
        build.capture_source_identity(root)


def test_compile_rejects_source_changes_during_the_compiler_process(tmp_path: Path, monkeypatch) -> None:
    root = _clean_source(tmp_path)
    monkeypatch.setattr(build, "PROJECT_ROOT", root)
    real_run = subprocess.run

    def compiler_process(command, **options):
        if "nuitka" in command:
            (root / "source.txt").write_text("changed during compile\n", encoding="ascii")
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **options)

    monkeypatch.setattr(subprocess, "run", compiler_process)
    with pytest.raises(RuntimeError, match="SOURCE_DIRTY"):
        build.compile_standalone(tmp_path / "compile")


def test_package_keeps_source_identity_and_excludes_private_compilation_xml(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.dist"
    identity = write_runtime_fixture(runtime)
    package = build.assemble_package(runtime, tmp_path / "packages", "macos-arm64")
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_identity"] == identity
    assert manifest["modular"]["compilation_report"]["sha256"]
    assert (runtime / build.RUNTIME_COMPILATION_REPORT_NAME).is_file()
    assert not (package / "runtime" / build.RUNTIME_COMPILATION_REPORT_NAME).exists()
    assert not any("/private/source/" in path.read_text(encoding="utf-8", errors="ignore") for path in (package / "runtime").glob("*.json"))


def test_public_assembly_rejects_a_filename_only_artifact_before_output(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "wx-doc-format-skill-0.13.0-macos-arm64.tar.gz").write_bytes(b"not an archive")
    with pytest.raises(ValueError, match="ARTIFACT_INVALID"):
        public.assemble_public_skill(artifacts, tmp_path / "public", ("macos-arm64",), "26.0")
    assert not (tmp_path / "public").exists()


def test_public_assembly_rejects_platforms_built_from_different_commits(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    write_archive_fixture(artifacts, "macos-arm64")
    identity = fixture_source_identity(artifacts)
    identity["commit"] = "1" * 40
    write_archive_fixture(artifacts, "kylin-v10-arm64", identity=identity)
    with pytest.raises(ValueError, match="SOURCE_IDENTITY_MISMATCH"):
        public.assemble_public_skill(artifacts, tmp_path / "public", ("macos-arm64", "kylin-v10-arm64"), "26.0")
    assert not (tmp_path / "public").exists()


@pytest.mark.parametrize("relative", ["runtime/wx-doc-format", "unlisted.txt", "../escape", "runtime/wxdf-compilation-report.xml"])
def test_public_assembly_rejects_tampered_or_unsafe_archive_payload(tmp_path: Path, relative: str) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_archive_fixture(artifacts, "macos-arm64")
    root = "wx-doc-format-skill-0.13.0-macos-arm64"
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None) for member in archive.getmembers()]
    with tarfile.open(path, "w:gz") as archive:
        for member, data in entries:
            if member.name != f"{root}/{relative}":
                archive.addfile(member, io.BytesIO(data) if data is not None else None)
        member = tarfile.TarInfo(f"{root}/{relative}")
        member.size = len(b"tampered")
        archive.addfile(member, io.BytesIO(b"tampered"))

    with pytest.raises(ValueError, match="ARTIFACT_INVALID"):
        public.assemble_public_skill(artifacts, tmp_path / "public", ("macos-arm64",), "26.0")
    assert not (tmp_path / "public").exists()


def test_runtime_acceptance_runs_real_markdown_and_docx_contracts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    evidence = tmp_path / "evidence"
    result = smoke.run_runtime_contracts(
        [sys.executable, str(ROOT / "packaging" / "nuitka" / "release_entrypoint.py")],
        ROOT / "assets" / "wx_template.docx", evidence,
    )
    assert result["markdown"]["status"] == "passed"
    assert result["docx"]["status"] == "passed"
    assert (evidence / "runtime-acceptance.json").is_file()


def test_runtime_acceptance_checks_a_committed_command_enhancement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    result = smoke.run_runtime_contracts(
        [sys.executable, str(ROOT / "packaging" / "nuitka" / "release_entrypoint.py")],
        ROOT / "assets" / "wx_template.docx", tmp_path / "evidence",
    )
    assert result["command_enhancement"]["heading_level"] == 2


def test_runtime_acceptance_resumes_file_stages_and_repairs_a_real_audit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    result = smoke.run_runtime_contracts(
        [sys.executable, str(ROOT / "packaging" / "nuitka" / "release_entrypoint.py")],
        ROOT / "assets" / "wx_template.docx", tmp_path / "evidence",
    )
    assert result["file_protocol"]["idempotent"] is True
    assert len(result["file_protocol"]["waiting_stages"]) >= 2
    assert "document_review" in result["file_protocol"]["capabilities"]
    assert result["review"]["heading_level"] == 1


def test_runtime_acceptance_checks_rejected_review_and_release_export_guards(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    result = smoke.run_runtime_contracts(
        [sys.executable, str(ROOT / "packaging" / "nuitka" / "release_entrypoint.py")],
        ROOT / "assets" / "wx_template.docx", tmp_path / "evidence",
    )
    assert result["release_guards"]["status"] == "passed"
    assert result["rejected_review"]["formal_preserved"] is True


def test_distribution_smoke_cannot_count_a_script_as_a_compiled_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.dist"
    write_runtime_fixture(runtime)
    package = build.assemble_package(runtime, tmp_path / "packages", "macos-arm64")
    with pytest.raises(RuntimeError, match="COMPILED_RUNTIME_REQUIRED"):
        smoke.smoke(package, evidence_dir=tmp_path / "evidence")


@pytest.mark.skipif(sys.platform == "win32", reason="Current POSIX release contract")
def test_installation_acceptance_uses_real_offline_and_http_launchers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    runtime = tmp_path / "runtime.dist"
    write_runtime_fixture(runtime)
    (runtime / "wx-doc-format").write_text(
        "#!/bin/sh\nexec " + shlex.join([sys.executable, str(ROOT / "packaging" / "nuitka" / "release_entrypoint.py")]) + ' "$@"\n',
        encoding="utf-8",
    )
    target = build.platform_id()
    package = build.assemble_package(runtime, tmp_path / "artifacts", target)
    build.create_archive(package, target)
    public_skill = public.assemble_public_skill(package.parent, tmp_path / "public", (target,), "26.0")
    result = smoke.run_installation_contracts(package, tmp_path / "evidence", public_skill=public_skill)
    assert result["offline"]["status"] == "passed"
    assert result["online_http"]["download_count"] == 1
    assert result["ascii_guards"]["status"] == "passed"


def test_build_cli_rejects_an_existing_runtime_from_another_source_commit(tmp_path: Path, monkeypatch) -> None:
    identity = fixture_source_identity(tmp_path)
    runtime = tmp_path / "runtime.dist"
    write_runtime_fixture(runtime, identity={**identity, "commit": "1" * 40})
    monkeypatch.setattr(build, "capture_source_identity", lambda _root: identity)
    with pytest.raises(RuntimeError, match="SOURCE_IDENTITY_MISMATCH"):
        build.main(["--skip-compile", "--dist-dir", str(runtime), "--output-dir", str(tmp_path / "output")])
    assert not (tmp_path / "output").exists()


def test_public_cli_rejects_artifacts_from_a_different_frozen_checkout(tmp_path: Path, monkeypatch) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    write_archive_fixture(artifacts, "macos-arm64")
    identity = {**fixture_source_identity(artifacts), "commit": "1" * 40}
    monkeypatch.setattr(public, "capture_source_identity", lambda _root: identity, raising=False)
    with pytest.raises(ValueError, match="SOURCE_IDENTITY_MISMATCH"):
        public.main(["--artifacts-dir", str(artifacts), "--output-dir", str(tmp_path / "public"), "--macos-min-version", "26.0"])
    assert not (tmp_path / "public").exists()
