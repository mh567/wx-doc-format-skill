from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from wxdf.review import mode_manifest
from docx import Document


def _retired_module_literal(name: object) -> bool:
    value = str(name)
    return value == "wxdf.migration" or any(
        "legacy" in component.lower() for component in value.split(".")
    )


def _retired_mode_literal(name: object) -> bool:
    return "legacy" in str(name).lower()


def runtime_executable(package_dir: Path) -> Path:
    name = "wx-doc-format.exe" if platform.system().lower() == "windows" else "wx-doc-format"
    return package_dir / "runtime" / name


def report_keys(value) -> set[str]:
    keys = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(report_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(report_keys(child))
    return keys


def validate_modular_manifest(manifest: dict) -> None:
    modular = manifest.get("modular")
    if not isinstance(modular, dict):
        raise RuntimeError("Release manifest does not expose the modular surface")
    source_modules = modular.get("source_modules")
    runtime_modules = modular.get("runtime_modules")
    if not isinstance(source_modules, list) or not source_modules:
        raise RuntimeError("Modular manifest has no source module list")
    if not isinstance(runtime_modules, list):
        raise RuntimeError("Modular manifest has no runtime module list")
    for module_name in [*source_modules, *runtime_modules]:
        if _retired_module_literal(module_name):
            raise RuntimeError(f"Retired release module is forbidden: {module_name}")
    if source_modules != runtime_modules:
        raise RuntimeError("Compiled runtime wxdf modules do not match source modules")
    if modular.get("module_set_status") != "matched":
        raise RuntimeError("Modular module set is not marked as matched")
    contract_versions = modular.get("contract_versions")
    required_contracts = {"source_facts", "ast", "patch", "checkpoint", "capability_input", "normalized"}
    if not isinstance(contract_versions, dict) or set(contract_versions) != required_contracts:
        raise RuntimeError("Modular contract version manifest is incomplete")
    modes = modular.get("open_modes")
    if not isinstance(modes, dict):
        raise RuntimeError("Modular mode manifest is missing")
    for mode_name in modes:
        if _retired_mode_literal(mode_name):
            raise RuntimeError(f"Retired release mode is forbidden: {mode_name}")
    authoritative_modes = mode_manifest()
    if set(modes) != set(authoritative_modes):
        unexpected = sorted(set(modes) - set(authoritative_modes))
        missing = sorted(set(authoritative_modes) - set(modes))
        raise RuntimeError(
            "Native mode manifest mismatch against authoritative native modes; "
            f"unexpected={unexpected}, missing={missing}"
        )
    for mode_name, authoritative_mode in authoritative_modes.items():
        mode = modes[mode_name]
        if (
            not isinstance(mode, dict)
            or mode.get("status") != authoritative_mode.get("status")
        ):
            raise RuntimeError(
                f"{mode_name} status must match the authoritative native mode"
            )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _execute(command: list[str], evidence_dir: Path, label: str, *, expected: int = 0, environment: dict | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, env=environment)
    _write_json(evidence_dir / f"{label}.process.json", {
        "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    })
    if result.returncode != expected:
        raise RuntimeError(f"{label}: expected exit {expected}, got {result.returncode}: {result.stderr[-1000:]}")
    if "Traceback (most recent call last)" in result.stderr:
        raise RuntimeError(f"{label}: release process exposed a traceback")
    return result


def _validate_public_report(path: Path, *, status: str = "completed") -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    keys = report_keys(report)
    forbidden = {key for key in keys if key in {"review_packet", "unknown_pattern_packet", "blocks", "ast"}
                 or key.startswith("ast_") or key.endswith("_ast")
                 or any(part in key for part in ("candidate", "confidence", "decision", "evidence", "frame", "score", "stack", "traceback"))}
    if forbidden:
        raise RuntimeError(f"Release report exposes sensitive fields: {sorted(forbidden)}")
    summary = report.get("public_summary") or {}
    if summary.get("status") != status:
        raise RuntimeError(f"Release report has unexpected status: {summary}")
    if status == "completed":
        if summary.get("unexpected_styles_count") != 0 or summary.get("manual_review_required") is not False:
            raise RuntimeError(f"Release report did not pass public audits: {summary}")
        if any(summary.get("audits", {}).get(name) != "passed" for name in ("model_audit", "saved_output_audit", "appendix_preservation_audit")) or "failed" in summary.get("audits", {}).values():
            raise RuntimeError(f"Release report contains a failed audit: {summary}")
    if status == "completed" and "unexpected_styles" not in keys:
        raise RuntimeError("Release report does not expose unexpected_styles")
    return summary


def _validate_docx(path: Path, *, text: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("DOCX ZIP checksum validation failed")
    document = Document(path)
    if text not in [paragraph.text for paragraph in document.paragraphs]:
        raise RuntimeError(f"DOCX did not preserve text: {text}")
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}


def _documented_json(skill_path: Path, name: str, bindings: dict) -> dict:
    """Read a labelled JSON example, substituting known values without evaluation."""
    text = skill_path.read_text(encoding="utf-8")
    examples = re.findall(rf"<!-- wxdf:{re.escape(name)} -->\s*```json\s*\n(.*?)\n```", text, re.DOTALL)
    if len(examples) != 1:
        raise RuntimeError(f"SKILL_EXAMPLE_INVALID: expected one {name} JSON example")

    def bind(value):
        if isinstance(value, dict):
            return {key: bind(child) for key, child in value.items()}
        if isinstance(value, list):
            return [bind(child) for child in value]
        if isinstance(value, str) and value.startswith("$"):
            if value not in bindings:
                raise RuntimeError(f"SKILL_EXAMPLE_INVALID: unknown binding {value}")
            return bindings[value]
        return value

    result = bind(json.loads(examples[0]))
    if not isinstance(result, dict):
        raise RuntimeError(f"SKILL_EXAMPLE_INVALID: {name} must be an object")
    return result


def native_response(request: dict, *, enhance_role: bool = True, reject_review: bool = False, skill_path: Path | None = None) -> dict:
    """Exercise the packaged response examples with deterministic local evidence."""
    skill_path = skill_path or PROJECT_ROOT / "SKILL.md"
    bindings = {
        "$request.request_id": request["request_id"], "$request.capability": request["capability"],
        "$request.model_revision": request["model_revision"], "$request.view_sha256": request["view_sha256"],
        "$response_id": "response-" + request["request_id"], "$candidate_id": "acceptance-" + request["request_id"],
    }
    response = _documented_json(skill_path, "candidate-set-response", bindings)
    if (
        response.get("request_id") != request["request_id"]
        or type(response.get("base_revision")) is not int
        or response["base_revision"] != request["model_revision"]
        or response.get("view_sha256") != request["view_sha256"]
        or response.get("response_id") != bindings["$response_id"]
        or not isinstance(response.get("candidates"), list)
    ):
        raise RuntimeError("SKILL_EXAMPLE_INVALID: response identity or candidates differ from the request")
    if request["capability"] == "list_detect" and enhance_role:
        blocks = request["items"][0]["source_ast"]["document"]["blocks"]
        target_id, target = next((key, value) for key, value in blocks.items() if value.get("role") == "body")
        bindings.update({
            "$target.node_id": target_id, "$target.source_ref": target["source_ref"], "$target.expected_level": 2,
        })
        response["candidates"].append(_documented_json(skill_path, "role-candidate", bindings))
    if request["capability"] == "document_review":
        packet = request.get("review_packet") or request.get("context", {}).get("review_packet")
        target = packet["targets"][0]
        bindings.update({
            "$target.node_id": target["block_id"], "$target.source_ref": target["source_ref"],
            "$target.expected_level": 6 if reject_review else target["evidence"]["expected_level"],
        })
        response["candidates"].append(_documented_json(skill_path, "role-candidate", bindings))
    return response


def _assert_heading(path: Path, text: str, level: int) -> None:
    paragraph = next(item for item in Document(path).paragraphs if item.text == text)
    if paragraph.style.name.lower() != f"heading {level}":
        raise RuntimeError(f"Enhancement was not committed to DOCX: {paragraph.style.name}")


def run_runtime_contracts(command: list[str], template: Path, evidence_dir: Path, *, skill_path: Path | None = None) -> dict:
    """Exercise release behavior through a runnable CLI, keeping private evidence."""
    skill_path = (skill_path or PROJECT_ROOT / "SKILL.md").resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    markdown = evidence_dir / "source.md"
    markdown.write_text("# Acceptance\n\n## Scope\n\nPreserved body.\n\n1. First item\n2. Second item\n\n| Name | Value |\n| --- | --- |\n| A | B |\n", encoding="utf-8")
    source_docx = evidence_dir / "source.docx"
    document = Document()
    document.add_heading("Acceptance", level=1)
    document.add_heading("Scope", level=2)
    document.add_paragraph("Preserved body.")
    document.add_paragraph("First item", style="List Number")
    table = document.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, (("Name", "Value"), ("A", "B"))):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document.save(source_docx)
    results = {}
    for label, source in (("markdown", markdown), ("docx", source_docx)):
        output = evidence_dir / f"{label}.docx"
        report = evidence_dir / f"{label}.report.json"
        report_md = evidence_dir / f"{label}.report.md"
        _execute([*command, "--input", str(source), "--output", str(output), "--template", str(template),
                  "--report", str(report), "--report-md", str(report_md)], evidence_dir, label)
        summary = _validate_public_report(report)
        artifact = _validate_docx(output, text="Preserved body.")
        if not report_md.is_file() or any(value in report_md.read_text(encoding="utf-8") for value in ("Traceback", "review_packet", "source_ast", "native_module_evidence")):
            raise RuntimeError("Release Markdown report is missing or exposes a traceback")
        results[label] = {"status": "passed", "artifact": artifact, "public_summary": summary}
    source = evidence_dir / "command-source.md"
    source.write_text("# Command acceptance\n\n## Scope\n\nCommand target.\n", encoding="utf-8")
    output = evidence_dir / "command.docx"
    report = evidence_dir / "command.report.json"
    adapter = shlex.join([sys.executable, str(Path(__file__).resolve()), "--respond", "--skill-document", str(skill_path)])
    _execute([*command, "--input", str(source), "--output", str(output), "--template", str(template),
              "--report", str(report), "--llm-enhance", "force-a", "--llm-command", adapter], evidence_dir, "command")
    _validate_public_report(report)
    _assert_heading(output, "Command target.", 2)
    results["command_enhancement"] = {"status": "passed", "heading_level": 2, "artifact": _validate_docx(output, text="Command target.")}
    source = evidence_dir / "review-source.md"
    source.write_text("# Review acceptance\n\n### Jump\n\nReview body.\n", encoding="utf-8")
    output = evidence_dir / "file.docx"
    previous_formal = b"previous formal output"
    output.write_bytes(previous_formal)
    report = evidence_dir / "file.report.json"
    report_md = evidence_dir / "file.report.md"
    request_dir = evidence_dir / "requests"
    _execute([*command, "--input", str(source), "--output", str(output), "--template", str(template),
              "--report", str(report), "--report-md", str(report_md),
              "--llm-enhance", "all", "--generate-requests", str(request_dir)], evidence_dir, "file-generate")
    checkpoint_path = request_dir / "run.json"
    responses = {}
    stages = []
    capabilities = []
    for attempt in range(6):
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("checkpoint_schema_version") != "2.0" or checkpoint.get("configuration", {}).get("pipeline") != "modular" or checkpoint.get("profile") != "wx-default-v2" or checkpoint.get("list_policy") != "wx-default-v2":
            raise RuntimeError("File protocol checkpoint has a non-native identity")
        if checkpoint["status"] == "completed":
            break
        if checkpoint["status"] != "waiting_external" or output.read_bytes() != previous_formal:
            raise RuntimeError("File protocol published before all stages were accepted")
        _validate_public_report(report, status="waiting_external")
        stages.append(checkpoint["resume_stage"])
        for request in checkpoint["requests"]:
            if request["request_id"] not in responses:
                capabilities.append(request["capability"])
                responses[request["request_id"]] = native_response(request, enhance_role=False, skill_path=skill_path)
        Path(checkpoint["files"]["responses_path"]).write_text(
            "".join(json.dumps(response, ensure_ascii=False) + "\n" for response in responses.values()), encoding="utf-8",
        )
        _execute([*command, "--resume", str(checkpoint_path)], evidence_dir, f"file-resume-{attempt}")
    else:
        raise RuntimeError("File protocol did not terminate within its bounded stage count")
    _validate_public_report(report)
    _assert_heading(output, "Jump", 1)
    if len(stages) < 2 or "document_review" not in capabilities:
        raise RuntimeError("File protocol did not exercise post-audit review")
    replay_artifacts = (checkpoint_path, output, report, report_md)
    before_replay = tuple(path.read_bytes() for path in replay_artifacts)
    _execute([*command, "--resume", str(checkpoint_path)], evidence_dir, "file-idempotent-resume")
    if before_replay != tuple(path.read_bytes() for path in replay_artifacts):
        raise RuntimeError("Terminal resume changed the checkpoint, formal DOCX, or report")
    _validate_public_report(report)
    if any(value in report_md.read_text(encoding="utf-8") for value in ("Traceback", "review_packet", "source_ast", "native_module_evidence")):
        raise RuntimeError("Release Markdown report exposes private details after terminal resume")
    results["file_protocol"] = {
        "status": "passed", "idempotent": True, "waiting_stages": stages, "capabilities": capabilities,
        "skill_document_sha256": hashlib.sha256(skill_path.read_bytes()).hexdigest(),
    }
    results["review"] = {"status": "passed", "heading_level": 1, "artifact": _validate_docx(output, text="Jump")}
    rejected = evidence_dir / "rejected.docx"
    rejected.write_bytes(previous_formal)
    rejected_report = evidence_dir / "rejected.report.json"
    rejection_adapter = shlex.join([sys.executable, str(Path(__file__).resolve()), "--respond", "--reject-review", "--skill-document", str(skill_path)])
    _execute([*command, "--input", str(source), "--output", str(rejected), "--template", str(template),
              "--report", str(rejected_report), "--llm-enhance", "force-c", "--llm-command", rejection_adapter], evidence_dir, "rejected-review", expected=1)
    _validate_public_report(rejected_report, status="failed")
    if rejected.read_bytes() != previous_formal:
        raise RuntimeError("Rejected review replaced the prior formal output")
    results["rejected_review"] = {"status": "passed", "formal_preserved": True}
    for option in ("--ast", "--source-ast"):
        forbidden = evidence_dir / (option.removeprefix("--") + ".forbidden.json")
        _execute([*command, "--input", str(markdown), "--output", str(evidence_dir / "forbidden.docx"),
                  "--template", str(template), option, str(forbidden)], evidence_dir, "guard-" + option.removeprefix("--"), expected=2)
        if forbidden.exists():
            raise RuntimeError("Release runtime exported a private AST")
    retired = _execute([*command, "--pipeline", "legacy", "--version"], evidence_dir, "guard-legacy", expected=2)
    if "--pipeline" not in retired.stderr:
        raise RuntimeError("Release runtime did not reject the retired pipeline flag")
    legacy_checkpoint = evidence_dir / "legacy-run.json"
    _write_json(legacy_checkpoint, {"protocol_version": "1.0", "args": {}})
    legacy = _execute([*command, "--resume", str(legacy_checkpoint)], evidence_dir, "guard-legacy-checkpoint", expected=2)
    if "LEGACY_CHECKPOINT_UNSUPPORTED" not in legacy.stderr:
        raise RuntimeError("Release runtime did not reject the retired checkpoint")
    results["release_guards"] = {"status": "passed", "ast_exports_rejected": True, "legacy_rejected": True}
    _write_json(evidence_dir / "runtime-acceptance.json", results)
    return results


def inspect_binary_metadata(package_dir: Path) -> dict:
    executable = runtime_executable(package_dir)
    description = subprocess.check_output(["file", "-b", str(executable)], text=True).strip()
    expected = "Mach-O" if platform.system() == "Darwin" else "ELF"
    if expected not in description:
        raise RuntimeError(f"COMPILED_RUNTIME_REQUIRED: {description}")
    if platform.system() != "Darwin":
        if "aarch64" not in description.lower() and "arm64" not in description.lower():
            raise RuntimeError(f"RUNTIME_ARCHITECTURE_MISMATCH: {description}")
        return {"format": "ELF", "architecture": "aarch64", "description": description}
    images = []
    for path in sorted((package_dir / "runtime").rglob("*")):
        if not path.is_file():
            continue
        kind = subprocess.check_output(["file", "-b", str(path)], text=True)
        if "Mach-O" not in kind:
            continue
        architecture = subprocess.check_output(["lipo", "-archs", str(path)], text=True).strip()
        if "arm64" not in architecture.split():
            raise RuntimeError(f"RUNTIME_ARCHITECTURE_MISMATCH: {path.name}: {architecture}")
        commands = subprocess.check_output(["otool", "-arch", "arm64", "-l", str(path)], text=True)
        versions = re.findall(r"\bminos\s+(\d+(?:\.\d+)*)", commands)
        versions += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\d+(?:\.\d+)*)", commands)
        if not versions:
            raise RuntimeError(f"MACOS_MINOS_MISSING: {path.name}")
        minimum = max(versions, key=lambda value: tuple(map(int, value.split("."))))
        images.append({"path": path.relative_to(package_dir).as_posix(), "architecture": architecture, "minos": minimum})
    maximum = max((item["minos"] for item in images), key=lambda value: tuple(map(int, value.split("."))))
    return {"format": "Mach-O", "architecture": "arm64", "macos_min_version": maximum, "images": images}


def run_installation_contracts(package_dir: Path, evidence_dir: Path, *, public_skill: Path | None = None) -> dict:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("WX_DOC_FORMAT_")}
    version = (package_dir / "VERSION").read_text(encoding="utf-8").strip()
    source = evidence_dir / "installation-source.md"
    source.write_text("# Installation acceptance\n\nPreserved body.\n", encoding="utf-8")

    def installed_conversion(launcher: Path, label: str, env: dict) -> dict:
        checked = _execute(["/bin/sh", str(launcher), "--version"], evidence_dir, label + "-version", environment=env)
        if not checked.stdout.splitlines() or checked.stdout.splitlines()[-1].strip() != version:
            raise RuntimeError(f"{label}: installed runtime version mismatch")
        output = evidence_dir / f"{label}.docx"
        report = evidence_dir / f"{label}.report.json"
        _execute(["/bin/sh", str(launcher), "--input", str(source), "--output", str(output),
                  "--template", str(package_dir / "assets" / "wx_template.docx"), "--report", str(report)],
                 evidence_dir, label + "-conversion", environment=env)
        _validate_public_report(report)
        return {"status": "passed", "artifact": _validate_docx(output, text="Preserved body.")}

    offline = evidence_dir / "offline-skills" / "wx-doc-format"
    if offline.exists():
        raise RuntimeError("INSTALLATION_TARGET_NOT_EMPTY: use fresh acceptance evidence directories")
    _execute(["/bin/sh", str(package_dir / "scripts" / "install.sh"), str(offline)], evidence_dir, "offline-install", environment=environment)
    results = {"offline": installed_conversion(offline / "scripts" / "run.sh", "offline", environment)}
    nonascii = evidence_dir / "nonascii-路径"
    _execute(["/bin/sh", str(package_dir / "scripts" / "install.sh"), str(nonascii)], evidence_dir, "ascii-installer", expected=2, environment=environment)
    shutil.copytree(package_dir / "scripts", nonascii / "scripts")
    _execute(["/bin/sh", str(nonascii / "scripts" / "run.sh"), "--version"], evidence_dir, "ascii-launcher", expected=2, environment=environment)
    results["ascii_guards"] = {"status": "passed", "installer": True, "launcher": True}
    if public_skill is not None:
        archive_name = package_dir.name + ".tar.gz"
        downloads = []

        class ArtifactHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(package_dir.parent), **kwargs)

            def do_GET(self):
                if self.path != "/" + archive_name:
                    self.send_error(404)
                    return
                downloads.append(self.path)
                super().do_GET()

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ArtifactHandler)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        try:
            online_environment = {
                **environment,
                "WX_DOC_FORMAT_RELEASE_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "WX_DOC_FORMAT_CACHE_DIR": str(evidence_dir / "online-cache"),
            }
            results["online_http"] = installed_conversion(public_skill / "scripts" / "run.sh", "online", online_environment)
            if len(downloads) != 1:
                raise RuntimeError(f"Online acceptance expected one empty-cache download, observed {len(downloads)}")
            results["online_http"]["download_count"] = len(downloads)
            online_environment["WX_DOC_FORMAT_CACHE_DIR"] = str(evidence_dir / "cache-路径")
            _execute(["/bin/sh", str(public_skill / "scripts" / "run.sh"), "--version"], evidence_dir, "ascii-cache", expected=2, environment=online_environment)
            results["ascii_guards"]["cache"] = True
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
    _write_json(evidence_dir / "installation-acceptance.json", results)
    return results


def smoke(package_dir: Path, *, evidence_dir: Path | None = None, public_skill: Path | None = None) -> dict:
    executable = runtime_executable(package_dir)
    binary = inspect_binary_metadata(package_dir)
    version = (package_dir / "VERSION").read_text(encoding="utf-8").strip()
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    validate_modular_manifest(manifest)
    version_result = subprocess.run([str(executable), "--version"], text=True, capture_output=True, check=True)
    if version_result.stdout.strip() != version:
        raise RuntimeError("Runtime version does not match package VERSION")
    destination = evidence_dir or Path(tempfile.mkdtemp(prefix="wx-doc-format-acceptance-"))
    destination.mkdir(parents=True, exist_ok=True)
    result = {
        "status": "passed", "version": version, "source_identity": manifest["source_identity"],
        "binary": binary,
        "contracts": run_runtime_contracts([str(executable)], package_dir / "assets" / "wx_template.docx", destination / "runtime", skill_path=package_dir / "SKILL.md"),
        "installation": run_installation_contracts(package_dir, destination / "installation", public_skill=public_skill),
    }
    _write_json(destination / "acceptance.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test an assembled offline package.")
    parser.add_argument("package_dir", type=Path, nargs="?")
    parser.add_argument("--respond", action="store_true", help="Run the local acceptance response adapter")
    parser.add_argument("--reject-review", action="store_true", help="Exercise review rollback with a rejected response")
    parser.add_argument("--skill-document", type=Path, help="Use response examples from this packaged SKILL.md")
    parser.add_argument("--evidence-dir", type=Path, help="Keep private acceptance logs and artifacts in this directory")
    parser.add_argument("--public-skill", type=Path, help="Exercise the thin Skill against a local HTTP archive server")
    args = parser.parse_args()
    if args.respond:
        print(json.dumps(native_response(json.load(sys.stdin), reject_review=args.reject_review, skill_path=args.skill_document), ensure_ascii=False))
        return
    if args.package_dir is None:
        parser.error("package_dir is required")
    package_dir = args.package_dir.resolve()
    result = smoke(package_dir, evidence_dir=args.evidence_dir, public_skill=args.public_skill)
    print(json.dumps({"status": result["status"], "version": result["version"], "binary": result["binary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
