from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import main


def _load_build_module():
    path = ROOT / "packaging" / "nuitka" / "build_distribution.py"
    spec = importlib.util.spec_from_file_location("wx_nuitka_build", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_build_module()


def _load_smoke_module():
    path = ROOT / "packaging" / "nuitka" / "smoke_distribution.py"
    spec = importlib.util.spec_from_file_location("wx_nuitka_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


smoke_distribution = _load_smoke_module()


def _write_posix_runtime(path: Path) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    path.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then printf "%s\\n" "{version}"; fi\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_load_version_from_source_tree_and_release_runtime(tmp_path):
    source = tmp_path / "source"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    (source / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    assert main._load_version(module_file=scripts / "main.py", release=False) == "1.2.3"

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    executable = runtime / "wx-doc-format"
    executable.touch()
    (runtime / "VERSION").write_text("4.5.6\n", encoding="utf-8")
    assert main._load_version(
        module_file=scripts / "main.py",
        executable=executable,
        release=True,
    ) == "4.5.6"


def test_offline_backend_allows_command_and_blocks_cloud_auto_detection(monkeypatch):
    monkeypatch.setenv("WX_DOC_FORMAT_OFFLINE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    assert main._resolve_llm_call(SimpleNamespace(llm_command=None)) is None
    assert callable(main._resolve_llm_call(SimpleNamespace(llm_command="printf ok")))


def test_platform_identifiers_cover_release_matrix():
    assert build.platform_id("Windows", "AMD64") == "windows-x86_64"
    assert build.platform_id("Darwin", "arm64") == "macos-arm64"
    assert build.platform_id("Linux", "aarch64") == "kylin-v10-arm64"


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Darwin", "x86_64"), ("Linux", "x86_64")],
)
def test_removed_release_targets_are_rejected(system, machine):
    with pytest.raises(ValueError, match="Unsupported release target"):
        build.platform_id(system, machine)


def test_nuitka_command_writes_private_compilation_report(tmp_path):
    command = build.nuitka_command(tmp_path)
    assert f"--report={tmp_path / 'compilation-report.xml'}" in command


def test_runtime_layout_restores_python_docx_parts_directory(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "docx" / "templates").mkdir(parents=True)

    build.ensure_runtime_layout(runtime)

    assert (runtime / "docx" / "parts").is_dir()


def test_unicode_path_smoke_copies_package_before_execution(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    (package / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    observed = []

    def record_smoke(copied_package):
        observed.append(copied_package)
        assert "中文安装路径" in str(copied_package)
        assert (copied_package / "VERSION").read_text(encoding="utf-8") == "1.2.3\n"

    monkeypatch.setattr(smoke_distribution, "smoke", record_smoke)
    smoke_distribution.smoke_from_unicode_path(package)

    assert len(observed) == 1


def test_skip_compile_package_assembly_writes_runtime_and_manifest(tmp_path):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    binary_name = build.executable_name()
    binary = dist / binary_name
    binary.write_bytes(b"compiled-placeholder")

    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())

    assert (package / "runtime" / binary_name).read_bytes() == b"compiled-placeholder"
    assert (package / "runtime" / "VERSION").read_text(encoding="utf-8").strip()
    assert (package / "SKILL.md").is_file()
    assert (package / "assets" / "wx_template.docx").is_file()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["offline"] is True
    assert manifest["platform"] == build.platform_id()
    manifest_paths = {item["path"] for item in manifest["files"]}
    assert f"runtime/{binary_name}" in manifest_paths
    assert "runtime/VERSION" in manifest_paths
    sums = (package / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "manifest.json" in sums
    assert f"runtime/{binary_name}" in sums


@pytest.mark.parametrize("suffix", [".py", ".pyc", ".pyo"])
def test_package_assembly_rejects_python_sources(tmp_path, suffix):
    dist = tmp_path / "unsafe.dist"
    dist.mkdir()
    (dist / build.executable_name()).write_bytes(b"compiled-placeholder")
    (dist / f"private_logic{suffix}").write_bytes(b"private")
    with pytest.raises(ValueError, match="Python source files are forbidden"):
        build.assemble_package(dist, tmp_path / "output", build.platform_id())


def test_package_assembly_rejects_newlines_in_paths(tmp_path):
    dist = tmp_path / "unsafe.dist"
    dist.mkdir()
    (dist / build.executable_name()).write_bytes(b"compiled-placeholder")
    (dist / "unsafe\nname.dat").write_bytes(b"private")
    with pytest.raises(ValueError, match="must not contain newlines"):
        build.assemble_package(dist, tmp_path / "output", build.platform_id())


def test_skip_compile_cli_uses_existing_standalone_directory(tmp_path, monkeypatch):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    (dist / build.executable_name()).write_bytes(b"compiled-placeholder")

    def unexpected_compile(_build_dir):
        raise AssertionError("Nuitka compilation must be skipped")

    monkeypatch.setattr(build, "compile_standalone", unexpected_compile)
    package = build.main([
        "--skip-compile",
        "--dist-dir", str(dist),
        "--output-dir", str(tmp_path / "output"),
    ])
    assert (package / "runtime" / build.executable_name()).is_file()


def test_release_entrypoint_sets_offline_environment_before_import():
    source = (ROOT / "packaging" / "nuitka" / "release_entrypoint.py").read_text(encoding="utf-8")
    offline_pos = source.index('os.environ["WX_DOC_FORMAT_OFFLINE"]')
    import_pos = source.index("from main import main")
    assert offline_pos < import_pos


def test_release_entrypoint_converts_unexpected_exception_to_stable_error(monkeypatch, capsys):
    path = ROOT / "packaging" / "nuitka" / "release_entrypoint.py"
    spec = importlib.util.spec_from_file_location("wx_release_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(main=lambda: (_ for _ in ()).throw(RuntimeError("private"))))
    with pytest.raises(SystemExit) as exc_info:
        module.run()
    assert exc_info.value.code == 70
    assert capsys.readouterr().err.strip() == "WXDF-E-UNEXPECTED"


def test_version_flag_exits_before_input_validation(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["wx-doc-format", "--version"])
    main.main()
    assert capsys.readouterr().out.strip() == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_release_rejects_ast_exports(monkeypatch):
    monkeypatch.setenv("WX_DOC_FORMAT_RELEASE", "1")
    monkeypatch.setattr(sys, "argv", ["wx-doc-format", "--ast", "private.json"])
    with pytest.raises(SystemExit) as exc_info:
        main.main()
    assert exc_info.value.code == 2


def test_release_resume_cannot_restore_ast_export_paths(monkeypatch):
    monkeypatch.setenv("WX_DOC_FORMAT_RELEASE", "1")
    monkeypatch.setattr(sys, "argv", ["wx-doc-format", "--resume", "run.json"])
    monkeypatch.setattr(main, "read_run_info", lambda _path: {
        "args": {
            "input": "source.docx",
            "output": "output.docx",
            "ast": "private.json",
        }
    })
    with pytest.raises(SystemExit) as exc_info:
        main.main()
    assert exc_info.value.code == 2


def test_release_report_is_recursively_sanitized_without_mutating_full_report():
    full_ast = {"schema_version": "1.0", "document": {"blocks": [{"id": "b1"}]}}
    report = {
        "skill_version": "1.0.0",
        "risk_warnings": [{"type": "sample"}],
        "review_packet": {"targets": ["b1"]},
        "unknown_pattern_packet": {"prompt": "private"},
        "audit": {
            "passed": True,
            "candidate_evidence": ["private"],
            "confidence": 0.99,
            "before_score": {"high": 1},
            "traceback": "private stack",
            "source_ast": full_ast,
        },
        "embedded_model": full_ast,
    }
    sanitized = main.report_for_output(report, release=True)
    serialized = json.dumps(sanitized)
    for private_key in (
        "review_packet", "unknown_pattern_packet", "candidate_evidence",
        "confidence", "before_score", "traceback", "source_ast", "blocks",
    ):
        assert private_key not in serialized
    assert sanitized["risk_warnings"] == report["risk_warnings"]
    assert "review_packet" in report
    assert "source_ast" in report["audit"]


def test_release_json_and_markdown_writes_receive_sanitized_report(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    output = tmp_path / "output.docx"
    json_report = tmp_path / "report.json"
    markdown_report = tmp_path / "report.md"
    source.write_text("# 测试\n\n正文。\n", encoding="utf-8")
    captured = {}
    original_writer = main.write_markdown_report

    def capture_writer(report, path):
        captured.update(report)
        original_writer(report, path)

    monkeypatch.setattr(main, "write_markdown_report", capture_writer)
    monkeypatch.setenv("WX_DOC_FORMAT_RELEASE", "1")
    monkeypatch.setattr(sys, "argv", [
        "wx-doc-format",
        "--input", str(source),
        "--output", str(output),
        "--report", str(json_report),
        "--report-md", str(markdown_report),
    ])
    main.main()

    written = json.loads(json_report.read_text(encoding="utf-8"))
    assert "review_packet" not in written
    assert "unknown_pattern_packet" not in written
    assert "review_packet" not in captured
    assert markdown_report.is_file()
    assert output.is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer test")
def test_posix_install_replaces_old_tree_and_retains_backup(tmp_path):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    binary = dist / build.executable_name()
    _write_posix_runtime(binary)
    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())

    destination = tmp_path / "installed" / "wx-doc-format"
    (destination / "scripts").mkdir(parents=True)
    (destination / "scripts" / "main.py").write_text("old private source", encoding="utf-8")
    subprocess.run(["/bin/sh", str(package / "scripts" / "install.sh"), str(destination)], check=True)

    assert not (destination / "scripts" / "main.py").exists()
    assert (destination / "runtime" / build.executable_name()).is_file()
    backups = list(destination.parent.glob("wx-doc-format.backup.*"))
    assert len(backups) == 1
    assert (backups[0] / "scripts" / "main.py").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer test")
@pytest.mark.parametrize("destination_kind", ["empty", "root", "home"])
def test_posix_installer_rejects_unsafe_destinations(tmp_path, destination_kind):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    binary = dist / build.executable_name()
    _write_posix_runtime(binary)
    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    destination = {"empty": "", "root": "/", "home": str(fake_home)}[destination_kind]
    environment = os.environ.copy()
    environment["HOME"] = str(fake_home)
    result = subprocess.run(
        ["/bin/sh", str(package / "scripts" / "install.sh"), destination],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer test")
def test_posix_installer_rejects_files_missing_from_checksum_list(tmp_path):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    binary = dist / build.executable_name()
    _write_posix_runtime(binary)
    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())
    (package / "unlisted-extra.txt").write_text("unexpected", encoding="utf-8")
    destination = tmp_path / "installed" / "wx-doc-format"
    result = subprocess.run(
        ["/bin/sh", str(package / "scripts" / "install.sh"), str(destination)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "does not match SHA256SUMS.txt" in result.stderr
    assert not destination.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer test")
def test_posix_installer_rejects_unlisted_newline_filename(tmp_path):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    binary = dist / build.executable_name()
    _write_posix_runtime(binary)
    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())
    (package / "unsafe\nextra.txt").write_text("unexpected", encoding="utf-8")
    destination = tmp_path / "installed" / "wx-doc-format"
    result = subprocess.run(
        ["/bin/sh", str(package / "scripts" / "install.sh"), str(destination)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "unsafe filename" in result.stderr
    assert not destination.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer test")
def test_posix_installer_rejects_staged_runtime_version_mismatch(tmp_path):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    binary = dist / build.executable_name()
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "wrong-version"\n', encoding="utf-8")
    binary.chmod(0o755)
    package = build.assemble_package(dist, tmp_path / "output", build.platform_id())
    destination = tmp_path / "installed" / "wx-doc-format"
    result = subprocess.run(
        ["/bin/sh", str(package / "scripts" / "install.sh"), str(destination)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "does not match package VERSION" in result.stderr
    assert not destination.exists()


def test_kylin_manifest_is_marked_as_candidate(tmp_path):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    (dist / build.executable_name("linux")).write_bytes(b"compiled-placeholder")
    package = build.assemble_package(dist, tmp_path / "output", "kylin-v10-arm64")
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["compatibility_status"] == "candidate"


def test_archive_name_keeps_full_version_and_private_build_files_stay_out(tmp_path):
    dist = tmp_path / "existing.dist"
    dist.mkdir()
    (dist / "wx-doc-format.exe").write_bytes(b"compiled-placeholder")
    package = build.assemble_package(dist, tmp_path / "output", "windows-x86_64")
    archive = build.create_archive(package, "windows-x86_64")
    assert archive.name == f"{package.name}.zip"
    assert not (package / "compilation-report.xml").exists()
    assert not (package / "smoke_distribution.py").exists()


def test_expected_tag_version_must_match_version_file():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert build.validate_expected_version(f"v{version}") == version
    with pytest.raises(ValueError, match="does not match"):
        build.validate_expected_version("v999.0.0")


def test_public_skill_description_does_not_expose_private_implementation_names():
    public_text = "\n".join(
        (ROOT / "packaging" / "nuitka" / "public" / name).read_text(encoding="utf-8")
        for name in ("SKILL.md", "README.md")
    )
    for private_name in ("docx_pipeline", "model_normalization", "review_loop", "python-docx", "lxml"):
        assert private_name not in public_text
