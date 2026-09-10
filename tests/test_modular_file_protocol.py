from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PYTHON = SCRIPTS / ".venv" / "bin" / "python3"
SOURCE = ROOT / "input" / "benchmark_report.md"
TEMPLATE = ROOT / "assets" / "wx_template.docx"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.run_store import CheckpointError, record_response, request_digest
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


def _generate(tmp_path: Path, *, mode: str = "force-a") -> tuple[Path, Path, Path]:
    request_dir = tmp_path / "requests"
    output = tmp_path / "file-output.docx"
    source_ast = tmp_path / "file-source-ast.json"
    completed = _run(
        "--input", SOURCE,
        "--output", output,
        "--template", TEMPLATE,
        "--pipeline", "modular",
        "--modular-profile", "native-v2",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", mode,
        "--source-ast", source_ast,
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


@pytest.mark.parametrize("drift", ["source", "template", "configuration", "contracts", "modules"])
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
        else:
            checkpoint["modules"][0]["version"] = "9.0"
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

    repeated = _run("--resume", checkpoint_path)
    assert repeated.returncode == 0, repeated.stderr
    assert checkpoint_path.read_bytes() == baseline_bytes

    changed = copy.deepcopy(response)
    changed["candidates"][0]["proposal"]["value"]["semantic_role"] = "changed-marker"
    _write_responses(checkpoint_path, [changed])
    conflicted = _run("--resume", checkpoint_path)
    assert conflicted.returncode != 0
    assert checkpoint_path.read_bytes() == baseline_bytes


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
        "--report", online_report,
    )
    assert online.returncode == 0, online.stderr

    assert json.loads(file_source_ast.read_text(encoding="utf-8")) == json.loads(online_source_ast.read_text(encoding="utf-8"))
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    online_state = json.loads(online_report.read_text(encoding="utf-8"))
    assert completed["normalized_ast"] == json.loads(file_source_ast.read_text(encoding="utf-8"))
    assert completed["semantic_digest"] == online_state["semantic_digest"]
    assert completed["presentation_digest"] == online_state["presentation_digest"]
    assert file_output.is_file() and online_output.is_file()
