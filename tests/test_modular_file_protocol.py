from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PYTHON = SCRIPTS / ".venv" / "bin" / "python3"
SOURCE = ROOT / "input" / "benchmark_report.md"
TEMPLATE = ROOT / "assets" / "wx_template.docx"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.decision_ledger import source_binding
from wxdf.enhancement import build_native_request, serialize_native_request
from wxdf.run_store import CheckpointError, file_digest, record_response, request_digest
from wxdf.validation import validate_checkpoint


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("WX_DOC_LLM_ENHANCE", None)
    environment["PYTHONPATH"] = str(SCRIPTS)
    return environment


def _run(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), str(SCRIPTS / "main.py"), *(str(value) for value in arguments)],
        cwd=ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
    )


def _docx_content_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(archive.read(name))
    return "sha256:" + digest.hexdigest()


def _docx_structure_summary(path: Path) -> dict:
    word_namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with ZipFile(path) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
    table_cells = []
    for cell in document.iter(word_namespace + "tc"):
        paragraphs = list(cell.findall(word_namespace + "p"))
        table_cells.append({
            "paragraph_count": len(paragraphs),
            "empty_paragraph_count": sum(
                not any((node.text or "") for node in paragraph.iter(word_namespace + "t"))
                for paragraph in paragraphs
            ),
            "styles": [
                next(
                    (
                        style.get(word_namespace + "val")
                        for style in paragraph.iter(word_namespace + "pStyle")
                    ),
                    None,
                )
                for paragraph in paragraphs
            ],
        })
    instructions = [node.text or "" for node in document.iter(word_namespace + "instrText")]
    return {
        "table_count": sum(1 for _ in document.iter(word_namespace + "tbl")),
        "table_cells": table_cells,
        "numbering_count": sum(1 for _ in document.iter(word_namespace + "numPr")),
        "caption_sequences": [value for value in instructions if "SEQ " in value],
    }


def _generate(tmp_path: Path, *, mode: str = "force-a") -> tuple[Path, Path, Path]:
    request_dir = tmp_path / "requests"
    output = tmp_path / "file-output.docx"
    source_ast = tmp_path / "file-source-ast.json"
    report = tmp_path / "file-report.json"
    completed = _run(
        "--input", SOURCE,
        "--output", output,
        "--template", TEMPLATE,
        "--pipeline", "modular",
        "--modular-profile", "native-v2",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", mode,
        "--source-ast", source_ast,
        "--report", report,
        "--generate-requests", request_dir,
    )
    assert completed.returncode == 0, completed.stderr
    return request_dir / "run.json", output, source_ast


def _read_requests(checkpoint_path: Path) -> list[dict]:
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    request_path = Path(checkpoint["files"]["requests_path"])
    return [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _response(request: dict, *, marker: str | None = "file-protocol-marker", response_id: str | None = None) -> dict:
    ast = request["items"][0]["source_ast"]
    target_id = next(iter(ast["document"]["blocks"]))
    source_ref = copy.deepcopy(ast["document"]["blocks"][target_id]["source_ref"])
    candidates = []
    if marker is not None:
        candidates.append(
            {
                "candidate_id": f"file-{request['capability']}",
                "module_id": f"enhancement-{request['capability']}",
                "module_version": "2.0",
                "target_source_refs": [source_ref],
                "target_node_ids": [target_id],
                "proposal": {
                    "operation": "update_node_semantics",
                    "property_scope": ["semantics"],
                    "value": {"semantic_role": marker},
                },
                "positive_evidence": [{"kind": "llm", "source": "file-protocol-test"}],
                "negative_evidence": [],
                "scope": {"stages": ["role"]},
                "constraints": [],
                "rationale": "file protocol response",
                "confidence": 0.9,
            }
        )
    response = {
        "request_id": request["request_id"],
        "response_id": response_id or f"response-{request['request_id']}",
        "candidate_set_version": "2.0",
        "base_revision": ast["revision"],
        "view_sha256": request["view_sha256"],
        "candidates": candidates,
    }
    return response


def _write_responses(checkpoint_path: Path, responses: list[dict]) -> None:
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    response_path = Path(checkpoint["files"]["responses_path"])
    response_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in responses),
        encoding="utf-8",
    )


def _replace_requests(checkpoint_path: Path, requests: list[dict]) -> None:
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    request_path = Path(checkpoint["files"]["requests_path"])
    request_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in requests),
        encoding="utf-8",
    )
    checkpoint["requests"] = copy.deepcopy(requests)
    fingerprints = checkpoint["resource_summary"].get("fingerprints", {})
    if "requests" in fingerprints:
        fingerprints["requests"] = file_digest(request_path)
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_native_generate_writes_complete_waiting_checkpoint_and_requests(tmp_path: Path) -> None:
    checkpoint_path, output, _source_ast = _generate(tmp_path)

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    requests = _read_requests(checkpoint_path)

    assert validate_checkpoint(checkpoint) is None
    assert checkpoint["checkpoint_schema_version"] == "2.0"
    assert checkpoint["status"] == "waiting_external"
    assert checkpoint["resume_stage"] == "relation"
    assert checkpoint["profile"] == "wx_full_rules-native-v2"
    assert checkpoint["list_policy"] == "wx-default-v2"
    assert checkpoint["configuration"]["output"] == str(output)
    assert checkpoint["decision_ledger"]["source_binding"]
    assert checkpoint["normalized_ast"] is None
    assert checkpoint["modules"] and all(item["version"] for item in checkpoint["modules"])
    assert set(checkpoint["resource_summary"]["fingerprints"]) >= {
        "source", "template", "configuration", "contracts", "modules"
    }
    assert requests == checkpoint["requests"]
    assert requests and requests[0]["request_sha256"].startswith("sha256:")
    assert requests[0]["items"][0]["source_ast"] == checkpoint["source_ast"]
    assert not output.exists()


def test_response_and_request_identity_conflicts_are_stable() -> None:
    from test_modular_resume import _checkpoint

    checkpoint = _checkpoint()
    checkpoint["requests"] = []
    for request_id, capability in (("request-1", "list_detect"), ("request-2", "caption_gen")):
        request = {"request_id": request_id, "capability": capability, "items": []}
        request["request_sha256"] = request_digest(request)
        checkpoint["requests"].append(request)
    first = {
        "request_id": "request-1",
        "response_id": "shared-response",
        "candidate_set_version": "2.0",
        "base_revision": 1,
        "candidates": [],
    }
    recorded, outcome = record_response(checkpoint, first)
    assert outcome == "recorded"

    with pytest.raises(CheckpointError) as reused:
        record_response(recorded, {**first, "request_id": "request-2"})
    assert reused.value.code == "response_identity_conflict"

    changed_request = copy.deepcopy(recorded)
    changed_request["requests"][0]["request_sha256"] = "sha256:" + "3" * 64
    with pytest.raises(CheckpointError) as changed:
        record_response(changed_request, first)
    assert changed.value.code == "request_identity_conflict"


@pytest.mark.parametrize("drift", ["source", "template", "configuration", "contracts", "modules", "parser"])
def test_native_resume_rejects_identity_drift_before_recording(tmp_path: Path, drift: str) -> None:
    source = tmp_path / "source.md"
    source.write_bytes(SOURCE.read_bytes())
    template = tmp_path / "template.docx"
    template.write_bytes(TEMPLATE.read_bytes())
    request_dir = tmp_path / "requests"
    output = tmp_path / "output.docx"
    generated = _run(
        "--input", source,
        "--output", output,
        "--template", template,
        "--pipeline", "modular",
        "--modular-profile", "native-v2",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", "force-a",
        "--generate-requests", request_dir,
    )
    assert generated.returncode == 0, generated.stderr
    checkpoint_path = request_dir / "run.json"
    requests = _read_requests(checkpoint_path)
    _write_responses(checkpoint_path, [_response(requests[0])])
    before = checkpoint_path.read_bytes()

    extra_args: tuple[str, ...] = ()
    if drift == "source":
        source.write_text(source.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    elif drift == "template":
        template.write_bytes(template.read_bytes() + b"changed")
    else:
        checkpoint = json.loads(before)
        if drift == "configuration":
            checkpoint["configuration"]["llm_enhance"] = "force-b"
        elif drift == "contracts":
            checkpoint["contracts"]["patch"] = "9.0"
        elif drift == "modules":
            checkpoint["modules"][0]["version"] = "9.0"
        else:
            checkpoint["resource_summary"]["fingerprints"]["parser"] = "sha256:" + "9" * 64
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        before = checkpoint_path.read_bytes()

    resumed = _run("--resume", checkpoint_path, *extra_args)

    assert resumed.returncode != 0
    assert not output.exists()
    assert checkpoint_path.read_bytes() == before
    checkpoint = json.loads(before)
    assert checkpoint["responses"] == []


def test_native_resume_is_idempotent_and_changed_content_conflicts(tmp_path: Path) -> None:
    checkpoint_path, output, source_ast_path = _generate(tmp_path)
    request = _read_requests(checkpoint_path)[0]
    response = _response(request)
    _write_responses(checkpoint_path, [response])

    first = _run("--resume", checkpoint_path)
    assert first.returncode == 0, first.stderr
    assert output.is_file()
    assert source_ast_path.is_file()
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert completed["status"] in {"completed", "completed_with_warnings"}
    assert completed["responses"][0]["applied"] is True
    assert len(completed["applied_transactions"]) == 1
    baseline_bytes = checkpoint_path.read_bytes()
    baseline_output = output.read_bytes()

    repeated = _run("--resume", checkpoint_path)
    assert repeated.returncode == 0, repeated.stderr
    assert checkpoint_path.read_bytes() == baseline_bytes

    changed = copy.deepcopy(response)
    changed["candidates"][0]["proposal"]["value"]["semantic_role"] = "changed-marker"
    _write_responses(checkpoint_path, [changed])
    conflicted = _run("--resume", checkpoint_path)
    assert conflicted.returncode != 0
    assert checkpoint_path.read_bytes() == baseline_bytes
    assert output.read_bytes() == baseline_output


@pytest.mark.parametrize("invalid_kind", ["stale_view", "invalid_patch", "stale_target"])
def test_native_resume_blocks_invalid_responses_before_transaction_or_publish(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    request = _read_requests(checkpoint_path)[0]
    response = _response(request)
    if invalid_kind == "stale_view":
        response["view_sha256"] = "sha256:" + "0" * 64
    elif invalid_kind == "invalid_patch":
        response = {
            "request_id": request["request_id"],
            "response_id": "invalid-patch-response",
            "patch_schema_version": "1.0",
        }
    else:
        response["candidates"][0]["target_node_ids"] = ["removed-node"]
    _write_responses(checkpoint_path, [response])

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    assert not output.exists()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "waiting_external"
    assert checkpoint["applied_transactions"] == []
    assert checkpoint["responses"]
    assert all(item["applied"] is False for item in checkpoint["responses"])


def test_native_resume_restores_saved_ast_ledger_overlays_and_transactions(tmp_path: Path) -> None:
    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    target_id = next(iter(checkpoint["source_ast"]["document"]["blocks"]))
    checkpoint["source_ast"]["document"]["blocks"][target_id]["semantic_role"] = "checkpoint-restored"
    restored_request = serialize_native_request(
        build_native_request(
            checkpoint["requests"][0]["capability"],
            checkpoint["source_ast"],
            budget=checkpoint["requests"][0]["budget"],
            timeout_seconds=checkpoint["requests"][0]["timeout_seconds"],
            request_id=checkpoint["requests"][0]["request_id"],
        )
    )
    checkpoint["content_overlay"] = [{"node_id": target_id, "restored_overlay": "kept"}]
    checkpoint["presentation_override"] = [{"node_id": target_id, "restored_presentation": "kept"}]
    checkpoint["applied_transactions"] = [
        {"transaction_id": "preloaded-transaction", "content_sha256": "sha256:" + "7" * 64}
    ]
    checkpoint["transaction_content_digests"] = {
        "preloaded-transaction": "sha256:" + "7" * 64
    }
    checkpoint["decision_ledger"] = {
        "schema_version": "2.0",
        "revision": 1,
        "revisions": [
            {
                "revision_id": "preloaded-revision",
                "parent_revision": 0,
                "transaction_id": "preloaded-ledger-transaction",
                "target_source_refs": [],
                "target_node_ids": [target_id],
                "property_scope": ["semantics"],
                "decision": {
                    "operation": "update_node_semantics",
                    "value": {"semantic_role": "checkpoint-restored"},
                },
                "preconditions": [
                    {
                        "kind": "source_sha256",
                        "value": checkpoint["source_facts"]["source"]["content_sha256"],
                    }
                ],
                "evidence": [{"kind": "user_override"}],
                "origin": {"kind": "review"},
                "resolver_version": "1.0",
                "status": "active",
                "supersedes": [],
            }
        ],
        "source_binding": source_binding(checkpoint["source_facts"]),
    }
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _replace_requests(checkpoint_path, [restored_request])
    _write_responses(checkpoint_path, [_response(restored_request)])

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode == 0, resumed.stderr
    assert output.is_file()
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert completed["source_ast"]["document"]["blocks"][target_id]["semantic_role"] == "checkpoint-restored"
    assert any(
        revision["revision_id"] == "preloaded-revision"
        for revision in completed["decision_ledger"]["revisions"]
    )
    assert completed["normalized_ast"]["source_ast"] == completed["source_ast"]
    assert completed["normalized_ast"]["overlay"][target_id]["restored_overlay"] == "kept"
    assert completed["normalized_ast"]["presentation_override"][target_id]["restored_presentation"] == "kept"
    assert completed["transaction_content_digests"]["preloaded-transaction"] == "sha256:" + "7" * 64


@pytest.mark.parametrize(
    "configuration_field",
    ["input", "output", "template", "modular_profile", "list_policy", "llm_enhance"],
)
def test_native_resume_rejects_explicit_cli_configuration_drift_before_recording(
    tmp_path: Path,
    configuration_field: str,
) -> None:
    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    before = checkpoint_path.read_bytes()
    alternate_output = tmp_path / "alternate-output.docx"
    alternate_input = tmp_path / "alternate-source.md"
    alternate_input.write_bytes(SOURCE.read_bytes())
    alternate_template = tmp_path / "alternate-template.docx"
    alternate_template.write_bytes(TEMPLATE.read_bytes())
    arguments = {
        "input": ("--input", alternate_input),
        "output": ("--output", alternate_output),
        "template": ("--template", alternate_template),
        "modular_profile": ("--modular-profile", "legacy-full-v1"),
        "list_policy": ("--list-policy", "legacy"),
        "llm_enhance": ("--llm-enhance", "force-b"),
    }[configuration_field]

    resumed = _run("--resume", checkpoint_path, *arguments)

    assert resumed.returncode != 0
    assert checkpoint_path.read_bytes() == before
    assert not output.exists()
    assert not alternate_output.exists()


def test_native_resume_rejects_tampered_request_file_before_recording(tmp_path: Path) -> None:
    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    before = checkpoint_path.read_bytes()
    checkpoint = json.loads(before)
    request_path = Path(checkpoint["files"]["requests_path"])
    on_disk = json.loads(request_path.read_text(encoding="utf-8"))
    on_disk["phase"] = "tampered-phase"
    request_path.write_text(
        json.dumps(on_disk, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    assert "fingerprint_mismatch" in resumed.stderr
    assert checkpoint_path.read_bytes() == before
    assert json.loads(before)["responses"] == []
    assert not output.exists()


def test_native_resume_rejects_external_resource_drift_before_recording(tmp_path: Path) -> None:
    source = tmp_path / "resource-source.md"
    resource = tmp_path / "pixel.png"
    resource.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg=="
    ))
    source.write_text("# Resource\n\n![pixel](pixel.png)\n", encoding="utf-8")
    request_dir = tmp_path / "resource-requests"
    output = tmp_path / "resource-output.docx"
    generated = _run(
        "--input", source,
        "--output", output,
        "--template", TEMPLATE,
        "--pipeline", "modular",
        "--modular-profile", "native-v2",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", "force-a",
        "--generate-requests", request_dir,
    )
    assert generated.returncode == 0, generated.stderr
    checkpoint_path = request_dir / "run.json"
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    before = checkpoint_path.read_bytes()
    resource.write_bytes(resource.read_bytes() + b"changed")

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    assert "fingerprint_mismatch" in resumed.stderr
    assert checkpoint_path.read_bytes() == before
    assert json.loads(before)["responses"] == []
    assert not output.exists()


def test_multi_stage_resume_matches_single_process_native_execution(tmp_path: Path) -> None:
    checkpoint_path, file_output, file_source_ast = _generate(tmp_path, mode="force-ab")
    requests = _read_requests(checkpoint_path)
    assert [item["capability"] for item in requests] == ["list_detect", "caption_gen"]
    responses = [
        _response(requests[0], marker="multi-stage-marker"),
        _response(requests[1], marker=None),
    ]
    _write_responses(checkpoint_path, responses[:1])

    paused = _run("--resume", checkpoint_path)
    assert paused.returncode == 0, paused.stderr
    waiting = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert waiting["status"] == "waiting_external"
    assert [item["request_id"] for item in waiting["responses"]] == [requests[0]["request_id"]]
    assert waiting["responses"][0]["applied"] is False
    assert waiting["applied_transactions"] == []
    assert not file_output.exists()

    _write_responses(checkpoint_path, responses)
    resumed = _run("--resume", checkpoint_path)
    assert resumed.returncode == 0, resumed.stderr

    response_map = tmp_path / "online-responses.json"
    response_map.write_text(
        json.dumps({item["capability"]: response for item, response in zip(requests, responses)}, ensure_ascii=False),
        encoding="utf-8",
    )
    adapter = tmp_path / "online-adapter.py"
    adapter.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "responses = json.load(open(sys.argv[1], encoding='utf-8'))\n"
        "print(json.dumps(responses[payload['capability']], ensure_ascii=False))\n",
        encoding="utf-8",
    )
    online_output = tmp_path / "online-output.docx"
    online_source_ast = tmp_path / "online-source-ast.json"
    online_normalized_ast = tmp_path / "online-normalized-ast.json"
    online_report = tmp_path / "online-report.json"
    command = " ".join(shlex.quote(value) for value in (str(PYTHON), str(adapter), str(response_map)))
    online = _run(
        "--input", SOURCE,
        "--output", online_output,
        "--template", TEMPLATE,
        "--pipeline", "modular",
        "--modular-profile", "native-v2",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", "force-ab",
        "--llm-command", command,
        "--source-ast", online_source_ast,
        "--ast", online_normalized_ast,
        "--report", online_report,
    )
    assert online.returncode == 0, online.stderr

    assert json.loads(file_source_ast.read_text(encoding="utf-8")) == json.loads(online_source_ast.read_text(encoding="utf-8"))
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    online_state = json.loads(online_report.read_text(encoding="utf-8"))
    file_state = json.loads(Path(completed["configuration"]["report"]).read_text(encoding="utf-8"))
    assert completed["normalized_ast"] == json.loads(online_normalized_ast.read_text(encoding="utf-8"))
    assert completed["semantic_digest"] == online_state["semantic_digest"]
    assert completed["presentation_digest"] == online_state["presentation_digest"]
    assert file_state["saved_output_audit"] == online_state["saved_output_audit"]
    assert file_state["saved_output_audit"]["passed"] is True
    assert _docx_structure_summary(file_output) == _docx_structure_summary(online_output)
    assert _docx_content_digest(file_output) == _docx_content_digest(online_output)
    assert file_output.is_file() and online_output.is_file()
