from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from test_t09_compiled_acceptance import fixture_source_identity


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import main


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


public_assembler = _load_module(
    "wx_t07_public_assembler",
    ROOT / "packaging" / "nuitka" / "assemble_public_skill.py",
)
distribution = _load_module(
    "wx_t07_distribution",
    ROOT / "packaging" / "nuitka" / "build_distribution.py",
)

PLATFORMS = ("macos-arm64", "kylin-v10-arm64")
MISSING = object()


def _write_compilation_manifest(runtime: Path) -> None:
    report = runtime / distribution.RUNTIME_COMPILATION_REPORT_NAME
    elements = "".join(
        f'<module name="{module_name}"/>'
        for module_name in distribution.release_module_names()
    )
    report.write_text(
        '<nuitka-compilation-report completion="yes" mode="standalone">'
        f"{elements}</nuitka-compilation-report>",
        encoding="utf-8",
    )
    distribution._write_runtime_module_manifest(runtime, report, source_identity=fixture_source_identity(runtime.parent))


def _write_launcher_artifact(artifacts_dir: Path, platform_id: str) -> Path:
    from test_t09_compiled_acceptance import write_archive_fixture

    return write_archive_fixture(artifacts_dir, platform_id, marker="WXDF-T07-LAUNCHER-OK")


def _assemble(tmp_path: Path, *, platforms: tuple[str, ...] = PLATFORMS) -> Path:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True)
    for platform_id in platforms:
        _write_launcher_artifact(artifacts_dir, platform_id)
    return public_assembler.assemble_public_skill(
        artifacts_dir,
        tmp_path / "public-skill",
        platforms,
        "26.0" if "macos-arm64" in platforms else None,
    )


def _run_launcher(public_skill: Path, tmp_path: Path, *, platform_id: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["WX_DOC_FORMAT_ARCHIVE_DIR"] = str(tmp_path / "artifacts")
    environment["WX_DOC_FORMAT_CACHE_DIR"] = str(tmp_path / f"cache-{platform_id}")
    system, machine = {"macos-arm64": ("Darwin", "arm64"), "kylin-v10-arm64": ("Linux", "aarch64")}[platform_id]
    shim_dir = tmp_path / f"uname-shim-{platform_id}"
    shim_dir.mkdir(exist_ok=True)
    uname = shim_dir / "uname"
    uname.write_text(
        f'#!/bin/sh\nif [ "$1" = "-s" ]; then printf "{system}\\n"; else printf "{machine}\\n"; fi\n',
        encoding="utf-8",
    )
    uname.chmod(0o755)
    if platform_id == "macos-arm64":
        version = shim_dir / "sw_vers"
        version.write_text('#!/bin/sh\nprintf "26.0\\n"\n', encoding="utf-8")
        version.chmod(0o755)
    environment["PATH"] = f"{shim_dir}{os.pathsep}{environment['PATH']}"
    return subprocess.run(
        ["/bin/sh", str(public_skill / "scripts" / "run.sh"), "--version"],
        env=environment,
        text=True,
        capture_output=True,
    )


def test_root_skill_is_the_exact_source_for_snapshots_and_generated_packages(tmp_path: Path) -> None:
    expected = (ROOT / "SKILL.md").read_bytes()
    assert (ROOT / "packaging" / "nuitka" / "public" / "SKILL.md").read_bytes() == expected
    assert (ROOT / "packaging" / "nuitka" / "public-repo" / "SKILL.md").read_bytes() == expected

    public_skill = _assemble(tmp_path / "online")
    assert (public_skill / "SKILL.md").read_bytes() == expected

    runtime = tmp_path / "runtime.dist"
    runtime.mkdir()
    executable = runtime / distribution.executable_name()
    executable.write_bytes(b"compiled-placeholder")
    _write_compilation_manifest(runtime)
    offline = distribution.assemble_package(runtime, tmp_path / "offline", "macos-arm64")
    assert (offline / "SKILL.md").read_bytes() == expected


def test_public_manifest_platform_matrix_is_status_first_and_self_consistent(tmp_path: Path) -> None:
    public_skill = _assemble(tmp_path)
    manifest = json.loads((public_skill / "artifacts" / "manifest.json").read_text(encoding="utf-8"))

    assert set(manifest["platforms"]) == set(PLATFORMS)
    assert manifest["platforms"]["macos-arm64"]["status"] == "accepted"
    assert manifest["platforms"]["kylin-v10-arm64"]["status"] == "candidate"
    assert manifest["available_platforms"] == ["kylin-v10-arm64", "macos-arm64"]
    assert manifest["unavailable_platforms"] == []
    for platform_id in PLATFORMS:
        entry = manifest["platforms"][platform_id]
        assert set(entry) == {"status", "name", "sha256", "size"}
        artifact = tmp_path / "artifacts" / entry["name"]
        assert entry["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert entry["size"] == artifact.stat().st_size
        assert f"{entry['sha256']}  {entry['name']}" in (
            public_skill / "artifacts" / "SHA256SUMS.txt"
        ).read_text(encoding="utf-8")


def test_public_manifest_marks_missing_artifacts_and_macos_without_minos_unavailable(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_launcher_artifact(artifacts_dir, "macos-arm64")
    public_skill = public_assembler.assemble_public_skill(
        artifacts_dir,
        tmp_path / "public-skill",
    )
    manifest = json.loads((public_skill / "artifacts" / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["platforms"]["macos-arm64"]["status"] == "unavailable"
    assert manifest["platforms"]["kylin-v10-arm64"] == {"status": "unavailable"}
    assert manifest["available_platforms"] == []
    assert manifest["unavailable_platforms"] == ["kylin-v10-arm64", "macos-arm64"]
    assert (public_skill / "artifacts" / "AVAILABLE_PLATFORMS.txt").read_text(encoding="utf-8") == ""


def test_public_assembler_rejects_windows_and_omits_its_launcher(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="windows-x86_64"):
        public_assembler.assemble_public_skill(
            tmp_path,
            tmp_path / "public-skill",
            ("windows-x86_64",),
        )

    public_skill = _assemble(tmp_path / "supported")
    assert not (public_skill / "scripts" / "run.ps1").exists()
    assert "windows-x86_64" not in json.dumps(
        json.loads((public_skill / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX public launcher contract")
def test_public_launcher_accepts_macos_and_warns_before_running_kylin_candidate(tmp_path: Path) -> None:
    public_skill = _assemble(tmp_path)

    accepted = _run_launcher(public_skill, tmp_path, platform_id="macos-arm64")
    assert accepted.returncode == 0, accepted.stderr
    assert "WXDF-T07-LAUNCHER-OK" in accepted.stdout
    assert "WXDF-W-RUNTIME-CANDIDATE" not in accepted.stderr

    candidate = _run_launcher(public_skill, tmp_path, platform_id="kylin-v10-arm64")
    assert candidate.returncode == 0, candidate.stderr
    assert "WXDF-W-RUNTIME-CANDIDATE" in candidate.stderr
    assert "WXDF-T07-LAUNCHER-OK" in candidate.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX public launcher contract")
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"status": "unavailable"}, "WXDF-E-RUNTIME-UNAVAILABLE"),
        ({"status": "unexpected"}, "WXDF-E-MANIFEST-INVALID"),
        (None, "WXDF-E-RUNTIME-UNAVAILABLE"),
    ],
)
def test_public_launcher_stops_on_unusable_manifest_before_cache_or_download(
    tmp_path: Path,
    mutation: dict | None,
    expected_code: str,
) -> None:
    public_skill = _assemble(tmp_path)
    manifest_path = public_skill / "artifacts" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation is None:
        manifest["platforms"].pop("kylin-v10-arm64")
    else:
        manifest["platforms"]["kylin-v10-arm64"] = mutation
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    completed = _run_launcher(public_skill, tmp_path, platform_id="kylin-v10-arm64")

    assert completed.returncode == 3
    assert expected_code in completed.stderr
    assert not (tmp_path / "cache-kylin-v10-arm64").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX public launcher contract")
def test_public_launcher_stops_on_corrupt_manifest_before_cache_or_download(tmp_path: Path) -> None:
    public_skill = _assemble(tmp_path)
    (public_skill / "artifacts" / "manifest.json").write_text("{", encoding="utf-8")

    completed = _run_launcher(public_skill, tmp_path, platform_id="kylin-v10-arm64")

    assert completed.returncode == 3
    assert "WXDF-E-MANIFEST-INVALID" in completed.stderr
    assert not (tmp_path / "cache-kylin-v10-arm64").exists()


@pytest.mark.parametrize(
    (
        "status",
        "audit_passed",
        "diagnostics",
        "manual_review",
        "unexpected_styles_count",
        "expected_diagnostic_codes",
    ),
    [
        ("completed", True, [], False, 0, []),
        (
            "failed",
            False,
            [{"code": "WXDF-E-FAILED", "message": "/private/input.docx"}],
            True,
            1,
            ["WXDF-E-FAILED"],
        ),
        (
            "waiting_external",
            None,
            [{"code": "WXDF-I-WAITING"}],
            True,
            None,
            ["WXDF-E-AUDIT-EVIDENCE-MISSING", "WXDF-I-WAITING"],
        ),
    ],
)
def test_release_report_adds_a_stable_path_free_public_summary(
    status: str,
    audit_passed: bool | None,
    diagnostics: list[dict],
    manual_review: bool,
    unexpected_styles_count: int | None,
    expected_diagnostic_codes: list[str],
) -> None:
    report = {
        "status": status,
        "pipeline": "modular",
        "profile": "wx-default-v2",
        "list_policy": "wx-default-v2",
        "model_audit": {"passed": audit_passed} if audit_passed is not None else {},
        "saved_output_audit": {
            "passed": audit_passed,
            "unexpected_styles": ["PrivateStyle"] if audit_passed is False else [],
        } if audit_passed is not None else {},
        "appendix_preservation_audit": {"passed": audit_passed} if audit_passed is not None else {},
        "diagnostics": diagnostics,
        "review_packet": {"path": "/private/input.docx", "blocks": [{"id": "p0"}]},
    }

    public = main.report_for_output(report, release=True)
    summary = public["public_summary"]

    assert list(summary) == [
        "schema_version",
        "status",
        "pipeline",
        "profile",
        "list_policy",
        "audits",
        "unexpected_styles_count",
        "manual_review_required",
        "diagnostic_codes",
    ]
    assert summary["schema_version"] == "1.0"
    assert summary["status"] == status
    assert summary["pipeline"] == "modular"
    assert summary["profile"] == "wx-default-v2"
    assert summary["list_policy"] == "wx-default-v2"
    assert summary["unexpected_styles_count"] == unexpected_styles_count
    assert summary["manual_review_required"] is manual_review
    assert summary["diagnostic_codes"] == expected_diagnostic_codes
    assert "/private" not in json.dumps(summary)
    assert "review_packet" not in public


@pytest.mark.parametrize(
    "unexpected_styles",
    [
        pytest.param(MISSING, id="missing"),
        pytest.param(None, id="null"),
        "invalid",
        False,
        -1,
    ],
)
def test_release_summary_fails_closed_when_unexpected_styles_evidence_is_missing_or_invalid(
    unexpected_styles: object,
) -> None:
    report = {
        "status": "completed",
        "pipeline": "modular",
        "profile": "wx-default-v2",
        "list_policy": "wx-default-v2",
        "audit": {"passed": True},
        "model_audit": {"passed": True},
        "saved_output_audit": {"passed": True},
        "appendix_preservation_audit": {"passed": True},
        "manual_review_required": False,
    }
    if unexpected_styles is not MISSING:
        report["saved_output_audit"]["unexpected_styles"] = unexpected_styles

    summary = main.report_for_output(report, release=True)["public_summary"]

    assert summary["audits"] == {
        "audit": "passed",
        "model_audit": "passed",
        "saved_output_audit": "passed",
        "appendix_preservation_audit": "passed",
    }
    assert summary["unexpected_styles_count"] is None
    assert summary["manual_review_required"] is True
    assert "WXDF-E-AUDIT-EVIDENCE-MISSING" in summary["diagnostic_codes"]


def test_public_manifests_declare_the_summary_contract(tmp_path: Path) -> None:
    public_skill = _assemble(tmp_path / "online")
    online = json.loads((public_skill / "artifacts" / "manifest.json").read_text(encoding="utf-8"))

    runtime = tmp_path / "runtime.dist"
    runtime.mkdir()
    (runtime / distribution.executable_name()).write_bytes(b"compiled-placeholder")
    _write_compilation_manifest(runtime)
    offline_skill = distribution.assemble_package(runtime, tmp_path / "offline", "macos-arm64")
    offline = json.loads((offline_skill / "manifest.json").read_text(encoding="utf-8"))

    expected = {
        "schema_version": "1.0",
        "field": "public_summary",
        "required_fields": [
            "status",
            "pipeline",
            "profile",
            "list_policy",
            "audits",
            "unexpected_styles_count",
            "manual_review_required",
            "diagnostic_codes",
        ],
    }
    assert online["public_report_summary"] == expected
    assert offline["public_report_summary"] == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX offline launcher contract")
def test_offline_launcher_warns_for_candidate_and_stops_on_unavailable_before_runtime(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime.dist"
    runtime.mkdir()
    executable = runtime / distribution.executable_name("linux")
    executable.write_text("#!/bin/sh\nprintf '%s\\n' 'WXDF-T07-OFFLINE-OK'\n", encoding="utf-8")
    executable.chmod(0o755)
    _write_compilation_manifest(runtime)
    package = distribution.assemble_package(runtime, tmp_path / "offline", "kylin-v10-arm64")

    environment = os.environ.copy()
    shim_dir = tmp_path / "offline-uname-shim"
    shim_dir.mkdir()
    uname = shim_dir / "uname"
    uname.write_text(
        '#!/bin/sh\nif [ "$1" = "-s" ]; then printf "Linux\\n"; else printf "aarch64\\n"; fi\n',
        encoding="utf-8",
    )
    uname.chmod(0o755)
    environment["PATH"] = f"{shim_dir}{os.pathsep}{environment['PATH']}"

    candidate = subprocess.run(
        ["/bin/sh", str(package / "scripts" / "run.sh"), "--version"],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert candidate.returncode == 0, candidate.stderr
    assert "WXDF-W-RUNTIME-CANDIDATE" in candidate.stderr
    assert "WXDF-T07-OFFLINE-OK" in candidate.stdout

    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platforms"]["kylin-v10-arm64"] = {"status": "unavailable"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    unavailable = subprocess.run(
        ["/bin/sh", str(package / "scripts" / "run.sh"), "--version"],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert unavailable.returncode == 3
    assert "WXDF-E-RUNTIME-UNAVAILABLE" in unavailable.stderr
    assert "WXDF-T07-OFFLINE-OK" not in unavailable.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX manifest validation contract")
@pytest.mark.parametrize("launcher_kind", ["online", "offline"])
@pytest.mark.parametrize("damage", ["trailing", "truncated", "corrupt_tail", "nul_trailing"])
def test_public_launchers_reject_invalid_json_before_cache_download_or_runtime(
    tmp_path: Path,
    launcher_kind: str,
    damage: str,
) -> None:
    environment = os.environ.copy()
    shim_dir = tmp_path / "invalid-json-uname-shim"
    shim_dir.mkdir()
    uname = shim_dir / "uname"
    uname.write_text(
        '#!/bin/sh\nif [ "$1" = "-s" ]; then printf "Linux\\n"; else printf "aarch64\\n"; fi\n',
        encoding="utf-8",
    )
    uname.chmod(0o755)
    environment["PATH"] = f"{shim_dir}{os.pathsep}{environment['PATH']}"

    if launcher_kind == "online":
        package = _assemble(tmp_path / "online")
        manifest_path = package / "artifacts" / "manifest.json"
        environment["WX_DOC_FORMAT_ARCHIVE_DIR"] = str(tmp_path / "online" / "artifacts")
        environment["WX_DOC_FORMAT_CACHE_DIR"] = str(tmp_path / "invalid-json-cache")
    else:
        runtime = tmp_path / "runtime.dist"
        runtime.mkdir()
        executable = runtime / distribution.executable_name("linux")
        executable.write_text("#!/bin/sh\nprintf '%s\\n' 'WXDF-T07-INVALID-RAN'\n", encoding="utf-8")
        executable.chmod(0o755)
        _write_compilation_manifest(runtime)
        package = distribution.assemble_package(
            runtime,
            tmp_path / "offline",
            "kylin-v10-arm64",
        )
        manifest_path = package / "manifest.json"

    manifest_text = manifest_path.read_text(encoding="utf-8").rstrip()
    damaged = {
        "trailing": manifest_text + "\ntrailing",
        "truncated": manifest_text[:-1],
        "corrupt_tail": manifest_text[:-1] + ",broken}",
    }
    if damage == "nul_trailing":
        manifest_path.write_bytes(manifest_text.encode("utf-8") + b"\x00garbage")
    else:
        manifest_path.write_text(damaged[damage], encoding="utf-8")

    completed = subprocess.run(
        ["/bin/sh", str(package / "scripts" / "run.sh"), "--version"],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 3
    assert "WXDF-E-MANIFEST-INVALID" in completed.stderr
    assert "WXDF-T07-LAUNCHER-OK" not in completed.stdout
    assert "WXDF-T07-INVALID-RAN" not in completed.stdout
    assert not (tmp_path / "invalid-json-cache").exists()


def test_public_launchers_use_native_v2_defaults_without_python_or_jq() -> None:
    online = (ROOT / "packaging" / "nuitka" / "public-repo" / "scripts" / "run.sh").read_text(
        encoding="utf-8"
    )
    offline = (
        ROOT / "packaging" / "nuitka" / "public" / "scripts" / "run.sh"
    ).read_text(encoding="utf-8")
    assert "--pipeline legacy" not in online + offline
    for launcher in (online, offline):
        assert "python" not in launcher.lower()
        assert "jq" not in launcher
