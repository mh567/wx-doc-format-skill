from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


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
    (root / "VERSION").write_text(f"{PACKAGE_VERSION}\n", encoding="ascii")
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
    executable.write_text(f'#!/bin/sh\nprintf "%s\\n" "{PACKAGE_VERSION}"\n', encoding="ascii")
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
        build._write_integrity_files(package, platform_id, PACKAGE_VERSION, manifest["modular"])
    return build.create_archive(package, platform_id)


def test_build_identity_pins_clean_git_source_and_rejects_drift(tmp_path: Path) -> None:
    root = _clean_source(tmp_path)
    identity = build.capture_source_identity(root)
    assert identity["version"] == PACKAGE_VERSION
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
    (artifacts / f"wx-doc-format-skill-{PACKAGE_VERSION}-macos-arm64.tar.gz").write_bytes(b"not an archive")
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
    root = f"wx-doc-format-skill-{PACKAGE_VERSION}-macos-arm64"
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
    assert result["file_protocol"]["skill_document_sha256"] == hashlib.sha256((ROOT / "SKILL.md").read_bytes()).hexdigest()
    assert len(result["file_protocol"]["waiting_stages"]) >= 2
    assert "document_review" in result["file_protocol"]["capabilities"]
    assert result["review"]["heading_level"] == 1


def _skill_json_example(name: str, bindings: dict) -> dict:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    examples = re.findall(rf"<!-- wxdf:{re.escape(name)} -->\s*```json\s*\n(.*?)\n```", text, re.DOTALL)
    assert len(examples) == 1, f"SKILL must publish one executable {name} JSON example"

    def bind(value):
        if isinstance(value, dict):
            return {key: bind(child) for key, child in value.items()}
        if isinstance(value, list):
            return [bind(child) for child in value]
        return bindings[value] if isinstance(value, str) and value.startswith("$") else value

    return bind(json.loads(examples[0]))


def test_public_skill_json_examples_complete_real_native_file_recovery(tmp_path: Path) -> None:
    from docx import Document

    source = tmp_path / "source.md"
    source.write_text("# Protocol acceptance\n\n### Jump\n\nProtocol target.\n", encoding="utf-8")
    output, report, report_md = (tmp_path / name for name in ("output.docx", "report.json", "report.md"))
    request_dir = tmp_path / "requests"
    checkpoint_path = request_dir / "run.json"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("WX_DOC_")}
    environment["PYTHONPATH"] = str(ROOT / "scripts")

    def run(*arguments):
        result = subprocess.run(
            [sys.executable, str(ROOT / "packaging/nuitka/release_entrypoint.py"), *map(str, arguments)],
            env=environment, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr

    run("--input", source, "--output", output, "--template", ROOT / "assets/wx_template.docx",
        "--report", report, "--report-md", report_md, "--llm-enhance", "all", "--generate-requests", request_dir)
    responses, capabilities = {}, set()
    for _attempt in range(6):
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint["status"] == "completed":
            break
        assert checkpoint["status"] == "waiting_external"
        for line in (request_dir / "llm_requests.jsonl").read_text(encoding="utf-8").splitlines():
            request = json.loads(line)
            if request["request_id"] in responses:
                continue
            capabilities.add(request["capability"])
            bindings = {
                "$request.request_id": request["request_id"], "$request.capability": request["capability"],
                "$request.model_revision": request["model_revision"], "$request.view_sha256": request["view_sha256"],
                "$response_id": "doc-response-" + request["request_id"],
                "$candidate_id": "doc-candidate-" + request["request_id"],
            }
            response = _skill_json_example("candidate-set-response", bindings)
            if request["capability"] in {"list_detect", "document_review"}:
                if request["capability"] == "document_review":
                    target = request["review_packet"]["targets"][0]
                    target_id, source_ref, level = target["block_id"], target["source_ref"], target["evidence"]["expected_level"]
                else:
                    target_id, target = next((key, block) for key, block in request["items"][0]["source_ast"]["document"]["blocks"].items() if block.get("role") == "body")
                    source_ref, level = target["source_ref"], 2
                bindings.update({"$target.node_id": target_id, "$target.source_ref": source_ref, "$target.expected_level": level})
                response["candidates"] = [_skill_json_example("role-candidate", bindings)]
            responses[request["request_id"]] = response
        (request_dir / "llm_responses.jsonl").write_text(
            "".join(json.dumps(value) + "\n" for value in responses.values()), encoding="utf-8",
        )
        run("--resume", checkpoint_path)
    else:
        pytest.fail("Documented file protocol did not complete")
    assert {"list_detect", "document_review"} <= capabilities
    assert len({value["response_id"] for value in responses.values()}) == len(responses)
    paragraphs = {paragraph.text: paragraph.style.name for paragraph in Document(output).paragraphs}
    assert paragraphs["Protocol target."] == "Heading 2"
    assert paragraphs["Jump"] == "Heading 1"
    artifacts = (checkpoint_path, output, report, report_md)
    before = [path.read_bytes() for path in artifacts]
    run("--resume", checkpoint_path)
    assert [path.read_bytes() for path in artifacts] == before
    summary = json.loads(report.read_text(encoding="utf-8"))["public_summary"]
    assert summary["status"] == "completed"
    assert summary["unexpected_styles_count"] == 0
    assert summary["manual_review_required"] is False
    assert all(summary["audits"][key] == "passed" for key in ("model_audit", "saved_output_audit", "appendix_preservation_audit"))


@pytest.mark.parametrize(("original", "replacement"), [
    ('"candidate_set_version": "2.0"', '"candidate_set_version": "1.0"'),
    ('"request_id": "$request.request_id",', ''),
    ('"view_sha256": "$request.view_sha256"', '"view_sha256": "$request.request_id"'),
    ('"base_revision": "$request.model_revision"', '"base_revision": "1"'),
    ('"operation": "update_role"', '"operation": "unsupported_operation"'),
])
def test_runtime_acceptance_rejects_a_stale_packaged_skill_response_example(
    tmp_path: Path, monkeypatch, original: str, replacement: str,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        (ROOT / "SKILL.md").read_text(encoding="utf-8").replace(original, replacement),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="command: expected exit 0"):
        smoke.run_runtime_contracts(
            [sys.executable, str(ROOT / "packaging/nuitka/release_entrypoint.py")],
            ROOT / "assets/wx_template.docx", tmp_path / "evidence", skill_path=skill,
        )


def test_native_file_protocol_rejects_retired_raw_response_envelopes(tmp_path: Path, monkeypatch) -> None:
    from wxdf.enhancement import EnhancementExecutor

    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    evidence = tmp_path / "evidence"
    smoke.run_runtime_contracts(
        [sys.executable, str(ROOT / "packaging/nuitka/release_entrypoint.py")],
        ROOT / "assets/wx_template.docx", evidence,
    )
    requests = [json.loads(line) for line in (evidence / "requests/llm_requests.jsonl").read_text().splitlines()]
    response_lines = (evidence / "requests/llm_responses.jsonl").read_text().splitlines()
    responses = {value["request_id"]: value for value in map(json.loads, response_lines)}
    for capability in ("list_detect", "document_review"):
        request = next(value for value in requests if value["capability"] == capability)
        response = responses[request["request_id"]]
        executor = EnhancementExecutor()
        assert executor.execute(request, call=lambda _payload: response).status == "completed"
        legacy = {
            "protocol_version": "1.0", "request_id": request["request_id"],
            "input_hash": request["view_sha256"], "raw_response": json.dumps(response),
        }
        result = executor.execute(request, call=lambda _payload: legacy)
        assert result.status == "invalid_patch"
        assert result.diagnostics[0]["code"] == "INVALID_PATCH"
        assert "response must be Patch 2.0 or CandidateSet 2.0" in result.diagnostics[0]["message"]


@pytest.mark.parametrize("report_kind", ["json", "markdown"])
def test_runtime_acceptance_rejects_terminal_report_overwrite(tmp_path: Path, monkeypatch, report_kind: str) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "scripts"))
    # Simulate the regression at the runtime process boundary while retaining the real CLI flow.
    forwarder = """import json, subprocess, sys
from pathlib import Path
entrypoint, report_kind, *arguments = sys.argv[1:]
checkpoint = None
if '--resume' in arguments:
    checkpoint = json.loads(Path(arguments[arguments.index('--resume') + 1]).read_text())
result = subprocess.run([sys.executable, entrypoint, *arguments])
if checkpoint is not None and checkpoint.get('status') == 'completed':
    report = Path(checkpoint['configuration']['report'])
    if report_kind == 'markdown':
        report = report.with_suffix('.md')
    report.write_text('overwritten terminal report')
sys.exit(result.returncode)
"""
    with pytest.raises(RuntimeError, match="Terminal resume changed.*report"):
        smoke.run_runtime_contracts(
            [sys.executable, "-c", forwarder, str(ROOT / "packaging/nuitka/release_entrypoint.py"), report_kind],
            ROOT / "assets/wx_template.docx", tmp_path / "evidence",
        )


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


@pytest.mark.parametrize(("relative", "kind", "prepend"), [
    ("runtime/./wx-doc-format", "directory", False),
    ("runtime/./wx-doc-format", "file", False),
    ("runtime//wx-doc-format", "directory", False),
    ("./runtime", "directory", False),
    ("runtime/../runtime", "directory", False),
    (".", "directory", False),
    ("/absolute/runtime", "directory", False),
    ("runtime", "directory", False),
    ("runtime/wx-doc-format", "directory", False),
    ("runtime/wx-doc-format/child", "directory", False),
    ("runtime/wx-doc-format/child", "directory", True),
])
def test_public_assembly_rejects_ambiguous_tar_paths_before_creating_output(
    tmp_path: Path, relative: str, kind: str, prepend: bool,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_archive_fixture(artifacts, "macos-arm64")
    root = f"wx-doc-format-skill-{PACKAGE_VERSION}-macos-arm64"
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None) for member in archive.getmembers()]
        payload = archive.extractfile(f"{root}/runtime/wx-doc-format").read()
    added = tarfile.TarInfo(relative if relative.startswith("/") else f"{root}/{relative}")
    added.type = tarfile.DIRTYPE if kind == "directory" else tarfile.REGTYPE
    added.mode = 0o755
    added.size = 0 if kind == "directory" else len(payload)
    extra = [(added, None if kind == "directory" else payload)]
    with tarfile.open(path, "w:gz") as archive:
        for member, data in (extra + entries if prepend else entries + extra):
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    output = tmp_path / "public"
    with pytest.raises(ValueError, match="ARTIFACT_INVALID") as rejected:
        public.assemble_public_skill(artifacts, output, ("macos-arm64",), "26.0")
    assert str(rejected.value).startswith(f"ARTIFACT_INVALID: {path.name}:")
    assert not output.exists()


@pytest.mark.parametrize("relative", ["scripts/install.sh", "scripts/run.sh", "runtime/wx-doc-format"])
def test_public_assembly_rejects_non_executable_required_archive_files(tmp_path: Path, relative: str) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_archive_fixture(artifacts, "macos-arm64")
    root = f"wx-doc-format-skill-{PACKAGE_VERSION}-macos-arm64"
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None) for member in archive.getmembers()]
    with tarfile.open(path, "w:gz") as archive:
        for member, data in entries:
            if member.name == f"{root}/{relative}":
                member.mode = 0o644
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    output = tmp_path / "public"
    with pytest.raises(ValueError, match="ARTIFACT_INVALID.*executable"):
        public.assemble_public_skill(artifacts, output, ("macos-arm64",), "26.0")
    assert not output.exists()


@pytest.mark.parametrize("target", ["macos-arm64", "kylin-v10-arm64"])
@pytest.mark.parametrize(("directories", "prepend"), [
    (("runtime/WX-DOC-FORMAT",), False),
    (("RUNTIME/wx-doc-format/child",), False),
    (("runtime/WX-DOC-FORMAT/child",), True),
    (("runtime/CaseFolder", "runtime/casefolder"), False),
    (("runtime/Caf\u00e9", "runtime/Cafe\u0301"), False),
])
def test_public_assembly_applies_target_filesystem_collision_keys(
    tmp_path: Path, target: str, directories: tuple[str, ...], prepend: bool,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_archive_fixture(artifacts, target)
    root = f"wx-doc-format-skill-{PACKAGE_VERSION}-{target}"
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None) for member in archive.getmembers()]
    extra = []
    for relative in directories:
        member = tarfile.TarInfo(f"{root}/{relative}")
        member.type = tarfile.DIRTYPE
        member.mode = 0o755
        extra.append((member, None))
    with tarfile.open(path, "w:gz") as archive:
        for member, data in (extra + entries if prepend else entries + extra):
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    output = tmp_path / "public"
    if target == "macos-arm64":
        with pytest.raises(ValueError, match="ARTIFACT_INVALID"):
            public.assemble_public_skill(artifacts, output, (target,), "26.0")
        assert not output.exists()
    else:
        assert public.assemble_public_skill(artifacts, output, (target,)) == output
        assert (output / "artifacts" / "manifest.json").is_file()


@pytest.mark.parametrize("target", ["macos-arm64", "kylin-v10-arm64"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(("extra_specs", "aliased"), [
    pytest.param((("runtime/CaseFolder/a.dat", False), ("runtime/casefolder/b.dat", False)), True, id="implicit-case"),
    pytest.param((("runtime/Caf\u00e9/a.dat", False), ("runtime/Cafe\u0301/b.dat", False)), True, id="implicit-unicode"),
    pytest.param((("runtime/CaseFolder/first/a.dat", False), ("runtime/casefolder/second/b.dat", False)), True, id="deep-ancestor-case"),
    pytest.param((("runtime/CaseFolder", True), ("runtime/casefolder/a.dat", False)), True, id="mixed-case"),
    pytest.param((("runtime/Caf\u00e9", True), ("runtime/Cafe\u0301/a.dat", False)), True, id="mixed-unicode"),
    pytest.param((("runtime/CaseFolder/a.dat", False), ("runtime/CaseFolder/b.dat", False)), False, id="identical-implicit"),
    pytest.param((("runtime/Caf\u00e9", True), ("runtime/Caf\u00e9/a.dat", False)), False, id="identical-mixed"),
])
def test_public_assembly_validates_implicit_ancestor_spellings_independent_of_order(
    tmp_path: Path, target: str, reverse: bool, extra_specs: tuple[tuple[str, bool], ...], aliased: bool,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_archive_fixture(artifacts, target)
    root = f"wx-doc-format-skill-{PACKAGE_VERSION}-{target}"
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None) for member in archive.getmembers()]
        manifest = json.load(archive.extractfile(f"{root}/manifest.json"))
    specs = tuple(reversed(extra_specs)) if reverse else extra_specs
    for relative, directory in specs:
        member = tarfile.TarInfo(f"{root}/{relative}")
        member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
        member.mode = 0o755 if directory else 0o644
        data = None if directory else b"declared archive payload\n"
        if data is not None:
            member.size = len(data)
            manifest["files"].append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        entries.append((member, data))
    manifest["files"].sort(key=lambda item: item["path"])
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payloads = {member.name: data for member, data in entries if data is not None and member.name != f"{root}/SHA256SUMS.txt"}
    payloads[f"{root}/manifest.json"] = manifest_data
    sums_data = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name[len(root) + 1:]}\n"
        for name, data in sorted(payloads.items())
    ).encode("utf-8")
    with tarfile.open(path, "w:gz") as archive:
        for member, data in entries:
            if member.name == f"{root}/manifest.json":
                data = manifest_data
            elif member.name == f"{root}/SHA256SUMS.txt":
                data = sums_data
            if data is not None:
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    output = tmp_path / "public"
    if target == "macos-arm64" and aliased:
        with pytest.raises(ValueError, match="ARTIFACT_INVALID.*alias"):
            public.assemble_public_skill(artifacts, output, (target,), "26.0")
        assert not output.exists()
    else:
        assert public.assemble_public_skill(artifacts, output, (target,), "26.0") == output
        assert (output / "artifacts" / "manifest.json").is_file()


@pytest.mark.parametrize("target", ["macos-arm64", "kylin-v10-arm64"])
@pytest.mark.parametrize(("relative", "mode"), [
    ("scripts/install.sh", 0o111),
    ("scripts/run.sh", 0o111),
    ("runtime/wx-doc-format", 0o111),
    ("VERSION", 0o200),
    ("manifest.json", 0o000),
    ("SHA256SUMS.txt", 0o200),
    ("runtime", 0o600),
    ("assets", 0o100),
    ("", 0o400),
])
def test_public_assembly_rejects_owner_unreadable_or_unsearchable_members(
    tmp_path: Path, target: str, relative: str, mode: int,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_archive_fixture(artifacts, target)
    root = f"wx-doc-format-skill-{PACKAGE_VERSION}-{target}"
    member_name = f"{root}/{relative}" if relative else root
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None) for member in archive.getmembers()]
    with tarfile.open(path, "w:gz") as archive:
        for member, data in entries:
            if member.name == member_name:
                member.mode = mode
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    output = tmp_path / "public"
    with pytest.raises(ValueError, match="ARTIFACT_INVALID.*owner"):
        public.assemble_public_skill(artifacts, output, (target,), "26.0")
    assert not output.exists()


@pytest.mark.parametrize("target", ["macos-arm64", "kylin-v10-arm64"])
def test_public_assembly_accepts_minimal_owner_read_and_search_permissions(tmp_path: Path, target: str) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_archive_fixture(artifacts, target)
    root = f"wx-doc-format-skill-{PACKAGE_VERSION}-{target}"
    entrypoints = {f"{root}/{relative}" for relative in ("scripts/install.sh", "scripts/run.sh", "runtime/wx-doc-format")}
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None) for member in archive.getmembers()]
    with tarfile.open(path, "w:gz") as archive:
        for member, data in entries:
            member.mode = 0o500 if member.isdir() or member.name in entrypoints else 0o400
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    output = tmp_path / "public"
    assert public.assemble_public_skill(artifacts, output, (target,), "26.0") == output
    assert (output / "artifacts" / "manifest.json").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="Current POSIX build entrypoints")
@pytest.mark.parametrize("target", ["macos-arm64", "linux-arm64"])
@pytest.mark.parametrize("compile_status", [0, 37])
def test_build_shell_preserves_compiler_logs_and_exit_status(
    tmp_path: Path, target: str, compile_status: int,
) -> None:
    project = _clean_source(tmp_path)
    entrypoint = project / "packaging" / "nuitka" / f"build-local-{target}.sh"
    entrypoint.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "packaging" / "nuitka" / entrypoint.name, entrypoint)
    (project / ".gitignore").write_text("build/\ndist/\n", encoding="ascii")
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(project), "-c", "user.name=T09 fixture", "-c", "user.email=t09@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "build shell fixture",
    ], check=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    fake_python = tools / "build-python"
    fake_python.write_text(f"#!{sys.executable}\n" + '''import json
import os
from pathlib import Path
import sys
arguments = sys.argv[1:]
if any(value.endswith("build_distribution.py") for value in arguments):
    print("compiler stdout", flush=True)
    print("compiler stderr", file=sys.stderr, flush=True)
    status = int(os.environ["T09_COMPILE_STATUS"])
    if status == 0:
        output = Path(arguments[arguments.index("--output-dir") + 1])
        target = arguments[arguments.index("--platform-id") + 1]
        (output / ("wx-doc-format-skill-" + os.environ["T09_PACKAGE_VERSION"] + "-" + target)).mkdir(parents=True)
        (output / ("wx-doc-format-skill-" + os.environ["T09_PACKAGE_VERSION"] + "-" + target + ".tar.gz")).touch()
    sys.exit(status)
if any(value.endswith("smoke_distribution.py") for value in arguments):
    evidence = Path(arguments[arguments.index("--evidence-dir") + 1])
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "acceptance.json").write_text(json.dumps({"fixture": True}))
    Path(os.environ["T09_SMOKE_MARKER"]).touch()
elif arguments[:2] == ["-m", "pytest"]:
    print("fixture full pytest passed")
elif arguments[:3] == ["-m", "pip", "freeze"]:
    print("fixture-dependency==1")
''', encoding="utf-8")
    fake_python.chmod(0o755)
    (tools / "uname").write_text('#!/bin/sh\nif [ "$1" = "-s" ]; then printf "Darwin\\n"; else printf "arm64\\n"; fi\n', encoding="ascii")
    (tools / "uname").chmod(0o755)
    (tools / "yum").write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    (tools / "yum").chmod(0o755)
    (tools / "mktemp").write_text('#!/bin/sh\nexec /usr/bin/mktemp -d "$TMPDIR/build-smoke.XXXXXX"\n', encoding="ascii")
    (tools / "mktemp").chmod(0o755)
    (tools / "docker").write_text(f"#!{sys.executable}\n" + '''import os
import shlex
import subprocess
import sys
arguments = sys.argv[1:]
if arguments == ["info"]:
    sys.exit(0)
assert arguments[0] == "run"
environment = os.environ.copy()
mounts = {}
for index, argument in enumerate(arguments[:-1]):
    if argument == "-v":
        host, container = arguments[index + 1].rsplit(":", 1)
        mounts[container] = host
    elif argument == "-e":
        key, value = arguments[index + 1].split("=", 1)
        environment[key] = value
body = arguments[-1].replace("/opt/python/cp313-cp313/bin/python", environment["T09_FAKE_PYTHON"])
body = body.replace("/opt/_internal", environment["T09_ABSENT_INTERNAL"])
for variable, container in (("BUILD_ROOT", "/build"), ("OUTPUT_DIR", "/artifacts")):
    body = body.replace(variable + "=" + container, variable + "=" + shlex.quote(mounts[container]))
sys.exit(subprocess.run(["/bin/bash", "-c", body], cwd=mounts["/workspace"], env=environment).returncode)
''', encoding="utf-8")
    (tools / "docker").chmod(0o755)
    build_root = project / "build" / "nuitka" / f"local-{target}"
    if target == "macos-arm64":
        venv_bin = build_root / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").symlink_to(fake_python)
    marker = tmp_path / "smoke-called"
    environment = {
        **os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "WXDF_BOOTSTRAP_PYTHON": str(fake_python), "T09_FAKE_PYTHON": str(fake_python),
        "T09_COMPILE_STATUS": str(compile_status), "T09_SMOKE_MARKER": str(marker),
        "T09_PACKAGE_VERSION": PACKAGE_VERSION,
        "T09_ABSENT_INTERNAL": str(tmp_path / "absent-internal"),
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(["/bin/sh", str(entrypoint)], env=environment, text=True, capture_output=True)
    assert result.returncode == compile_status, result.stderr
    assert "fixture full pytest passed" in (build_root / "tests.log").read_text()
    compile_log = build_root / "compile.log"
    assert compile_log.is_file(), result.stdout + result.stderr
    assert compile_log.read_text().splitlines() == ["compiler stdout", "compiler stderr"]
    assert marker.exists() is (compile_status == 0)
