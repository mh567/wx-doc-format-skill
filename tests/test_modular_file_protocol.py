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


def _direct_resume_after_prepare(
    checkpoint_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_prepare,
):
    from wxdf import run_store
    from wxdf.kernel import ConversionKernel, ConversionRequest

    original = run_store.prepare_native_resume

    def prepare_then_change(*args, **kwargs):
        prepared = original(*args, **kwargs)
        after_prepare()
        return prepared

    monkeypatch.setattr(run_store, "prepare_native_resume", prepare_then_change)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    configuration = checkpoint["configuration"]
    return ConversionKernel().convert(
        ConversionRequest(
            input_path=Path(configuration["input"]),
            output_path=Path(configuration["output"]),
            template_path=Path(configuration["template"]) if configuration["template"] else None,
            pipeline="modular",
            list_policy=configuration["list_policy"],
            options={
                "llm_enhance": configuration["llm_enhance"],
                "protocol_mode": "resume",
                "checkpoint_path": checkpoint_path,
                "checkpoint_configuration": configuration,
                "source_ast_path": Path(configuration["source_ast"]) if configuration["source_ast"] else None,
                "normalized_ast_path": Path(configuration["ast"]) if configuration["ast"] else None,
                "report_path": Path(configuration["report"]) if configuration["report"] else None,
                "profile": "native_v2",
            },
        )
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


@pytest.mark.parametrize("checkpoint_status", ["waiting", "completed"])
def test_native_resume_rejects_tampered_stored_response_digest_before_replay(
    tmp_path: Path,
    checkpoint_status: str,
) -> None:
    mode = "force-ab" if checkpoint_status == "waiting" else "force-a"
    checkpoint_path, output, _source_ast_path = _generate(tmp_path, mode=mode)
    request = _read_requests(checkpoint_path)[0]
    response = _response(request)
    _write_responses(checkpoint_path, [response])
    first = _run("--resume", checkpoint_path)
    assert first.returncode == 0, first.stderr
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == ("waiting_external" if checkpoint_status == "waiting" else "completed")
    checkpoint["responses"][0]["candidates"][0]["proposal"]["value"]["semantic_role"] = "tampered-stored-candidate"
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    checkpoint_bytes = checkpoint_path.read_bytes()
    output_bytes = output.read_bytes() if output.exists() else None

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    assert "response_identity_mismatch" in resumed.stderr
    assert checkpoint_path.read_bytes() == checkpoint_bytes
    assert (output.read_bytes() if output.exists() else None) == output_bytes


@pytest.mark.parametrize("checkpoint_status", ["waiting", "completed"])
@pytest.mark.parametrize(
    "application_field",
    ["applied", "apply_started", "transaction_id", "transaction_content_sha256"],
)
def test_native_resume_rejects_tampered_response_application_state_before_replay(
    tmp_path: Path,
    checkpoint_status: str,
    application_field: str,
) -> None:
    mode = "force-ab" if checkpoint_status == "waiting" else "force-a"
    checkpoint_path, output, _source_ast_path = _generate(tmp_path, mode=mode)
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    first = _run("--resume", checkpoint_path)
    assert first.returncode == 0, first.stderr
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == ("waiting_external" if checkpoint_status == "waiting" else "completed")
    response = checkpoint["responses"][0]
    if application_field == "applied":
        response["applied"] = not response["applied"]
    elif application_field == "apply_started":
        response["apply_started"] = True
    elif application_field == "transaction_id":
        response["transaction_id"] = "forged-transaction"
    else:
        response["transaction_content_sha256"] = "sha256:" + "9" * 64
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    checkpoint_bytes = checkpoint_path.read_bytes()
    output_bytes = output.read_bytes() if output.exists() else None

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    assert "response_application_state_mismatch" in resumed.stderr
    assert checkpoint_path.read_bytes() == checkpoint_bytes
    assert (output.read_bytes() if output.exists() else None) == output_bytes


def test_native_resume_rejects_response_rebinding_to_another_valid_transaction(
    tmp_path: Path,
) -> None:
    from wxdf.decision_ledger import _replay_snapshot, accept_decisions
    from wxdf.transaction import apply_stage_batch, make_snapshot

    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    target_id = next(iter(checkpoint["source_ast"]["document"]["blocks"]))
    preloaded_decision = {
        "decision_id": "preloaded-binding-proof",
        "transaction_id": "preloaded-binding-transaction",
        "operation": "update_overlay",
        "target_node_ids": [target_id],
        "target_source_refs": [],
        "expected_revision": checkpoint["source_ast"]["revision"],
        "property_scope": ["overlay"],
        "value": {"preloaded_binding_proof": True},
        "preconditions": [{
            "kind": "source_sha256",
            "value": checkpoint["source_facts"]["source"]["content_sha256"],
        }],
        "evidence": [{"kind": "user_override"}],
        "invalidates": [],
    }
    committed = apply_stage_batch(
        make_snapshot(checkpoint["source_ast"]),
        [preloaded_decision],
        expected_revision=checkpoint["source_ast"]["revision"],
    )
    ledger = accept_decisions(
        checkpoint["decision_ledger"],
        {"transaction_id": committed.transaction_id, "decisions": [preloaded_decision]},
    )
    replayed = _replay_snapshot(
        checkpoint["source_facts"],
        ledger,
        make_snapshot(checkpoint["source_ast"]),
    )
    preloaded_digest = ledger["revisions"][0]["transaction_content_sha256"]
    checkpoint["source_ast"] = replayed.ast
    checkpoint["decision_ledger"] = ledger
    checkpoint["content_overlay"] = [
        {"node_id": node_id, **value}
        for node_id, value in sorted(replayed.overlays.items())
    ]
    checkpoint["applied_transactions"] = [{
        "transaction_id": committed.transaction_id,
        "content_sha256": preloaded_digest,
    }]
    checkpoint["transaction_content_digests"] = {
        committed.transaction_id: preloaded_digest,
    }
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    resumed = _run("--resume", checkpoint_path)
    assert resumed.returncode == 0, resumed.stderr

    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert len(completed["transaction_content_digests"]) >= 2
    response = completed["responses"][0]
    assert response["transaction_id"] != committed.transaction_id
    response["transaction_id"] = committed.transaction_id
    response["transaction_content_sha256"] = completed["transaction_content_digests"][committed.transaction_id]
    checkpoint_path.write_text(
        json.dumps(completed, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    checkpoint_bytes = checkpoint_path.read_bytes()
    output_bytes = output.read_bytes()

    rebound = _run("--resume", checkpoint_path)

    assert rebound.returncode != 0
    assert "response_transaction_binding_mismatch" in rebound.stderr
    assert checkpoint_path.read_bytes() == checkpoint_bytes
    assert output.read_bytes() == output_bytes


def test_live_baseline_and_native_chain_share_candidate_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from wxdf import builtin_modules

    candidate_groups = {
        "source_region_candidates": [{"candidate_id": "region"}],
        "document_structure_candidates": [{"candidate_id": "structure"}],
        "protected_content_candidates": [{"candidate_id": "protected"}],
        "list_semantics_candidates": [{"candidate_id": "list"}],
        "appendix_semantics_candidates": [{"candidate_id": "appendix"}],
        "table_semantics_candidates": [{"candidate_id": "table"}],
        "caption_semantics_candidates": [{"candidate_id": "caption"}],
    }
    collected: list[dict[str, list[dict[str, str]]]] = []

    def collect(**groups):
        collected.append(groups)
        return [{"candidate_id": "shared-order"}]

    monkeypatch.setattr(builtin_modules, "collect_native_candidates", collect)
    monkeypatch.setattr(
        builtin_modules,
        "resolve_source_regions",
        lambda source_path, source_facts: {
            "regions": [],
            "candidates": candidate_groups["source_region_candidates"],
        },
    )
    monkeypatch.setattr(builtin_modules, "analyze_lists", lambda source_facts: {})
    monkeypatch.setattr(
        builtin_modules,
        "materialize_list_semantics",
        lambda source_ast, source_facts, result: (
            {},
            candidate_groups["list_semantics_candidates"],
            {},
        ),
    )
    monkeypatch.setattr(builtin_modules, "collect_source_appendix_semantics", lambda source_facts: {})
    monkeypatch.setattr(
        builtin_modules,
        "prepare_native_appendix_semantics",
        lambda source_facts, source_ast, semantics, list_result: {},
    )
    monkeypatch.setattr(
        builtin_modules,
        "collect_structure_candidates",
        lambda source_facts, regions, protect_semantics: candidate_groups["document_structure_candidates"],
    )
    monkeypatch.setattr(
        builtin_modules,
        "collect_protected_candidates",
        lambda source_facts: candidate_groups["protected_content_candidates"],
    )
    monkeypatch.setattr(
        builtin_modules,
        "collect_native_appendix_candidates",
        lambda source_facts, source_ast, semantics, list_result: candidate_groups["appendix_semantics_candidates"],
    )
    monkeypatch.setattr(
        builtin_modules,
        "collect_table_candidates",
        lambda source_ast: candidate_groups["table_semantics_candidates"],
    )
    monkeypatch.setattr(
        builtin_modules,
        "collect_caption_candidates",
        lambda source_ast: candidate_groups["caption_semantics_candidates"],
    )

    rebuilt = builtin_modules.build_native_candidate_baseline("source.docx", {}, {})
    context = builtin_modules.ModuleExecutionContext(
        request=SimpleNamespace(options={"llm_enhance": "off"}),
        artifacts={**candidate_groups, "source_ast": {}},
    )
    runtime = builtin_modules._native_candidate_set(context)

    assert rebuilt == [{"candidate_id": "shared-order"}]
    assert runtime == [{"candidate_id": "shared-order"}]
    assert collected == [candidate_groups, candidate_groups]


def test_native_resume_rejects_unsupported_checkpoint_stage(tmp_path: Path) -> None:
    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["resume_stage"] = "render"
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    before = checkpoint_path.read_bytes()

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    assert "unsupported_resume_stage" in resumed.stderr
    assert checkpoint_path.read_bytes() == before
    assert not output.exists()


def test_native_resume_rejects_live_candidate_drift_before_recording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import builtin_modules

    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    before = checkpoint_path.read_bytes()

    original_builder = builtin_modules.build_native_candidate_baseline

    def drifted_candidates(*args, **kwargs):
        return [
            *original_builder(*args, **kwargs),
            {"candidate_id": "simulated-live-candidate-drift"},
        ]

    monkeypatch.setattr(builtin_modules, "build_native_candidate_baseline", drifted_candidates)
    result = _direct_resume_after_prepare(checkpoint_path, monkeypatch, lambda: None)

    assert result.exit_code != 0
    assert any(item.get("code") == "candidate_baseline_mismatch" for item in result.diagnostics)
    assert checkpoint_path.read_bytes() == before
    assert json.loads(before)["responses"] == []
    assert not output.exists()


@pytest.mark.parametrize(
    "component",
    [
        "source_ast",
        "decision_ledger",
        "transaction_conflict",
        "content_overlay",
        "presentation_override",
        "transactions",
        "candidate_baseline",
    ],
)
def test_native_resume_rejects_inconsistent_relation_snapshot(
    tmp_path: Path,
    component: str,
) -> None:
    from wxdf.decision_ledger import accept_decisions

    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    target_id = next(iter(checkpoint["source_ast"]["document"]["blocks"]))
    if component == "source_ast":
        checkpoint["source_ast"]["document"]["blocks"][target_id]["semantic_role"] = "untracked-change"
    elif component == "decision_ledger":
        checkpoint["decision_ledger"] = accept_decisions(
            checkpoint["decision_ledger"],
            [{
                "decision_id": "unapplied-ledger-decision",
                "operation": "update_overlay",
                "target_node_ids": [target_id],
                "target_source_refs": [],
                "expected_revision": checkpoint["source_ast"]["revision"],
                "property_scope": ["overlay"],
                "value": {"ledger_value": "missing-from-snapshot"},
                "preconditions": [{
                    "kind": "source_sha256",
                    "value": checkpoint["source_facts"]["source"]["content_sha256"],
                }],
                "evidence": [{"kind": "test"}],
                "invalidates": [],
            }],
        )
    elif component == "transaction_conflict":
        checkpoint["decision_ledger"] = accept_decisions(
            checkpoint["decision_ledger"],
            [{
                "decision_id": "malformed-overlay-decision",
                "operation": "update_overlay",
                "target_node_ids": [target_id],
                "target_source_refs": [],
                "expected_revision": checkpoint["source_ast"]["revision"],
                "property_scope": ["overlay"],
                "value": "invalid-overlay-value",
                "preconditions": [{
                    "kind": "source_sha256",
                    "value": checkpoint["source_facts"]["source"]["content_sha256"],
                }],
                "evidence": [{"kind": "test"}],
                "invalidates": [],
            }],
        )
    elif component == "content_overlay":
        checkpoint["content_overlay"] = [{"node_id": target_id, "untracked": "overlay"}]
    elif component == "presentation_override":
        checkpoint["presentation_override"] = [{"node_id": target_id, "untracked": "presentation"}]
    elif component == "transactions":
        checkpoint["applied_transactions"] = [{
            "transaction_id": "untracked-transaction",
            "content_sha256": "sha256:" + "7" * 64,
        }]
        checkpoint["transaction_content_digests"] = {
            "untracked-transaction": "sha256:" + "7" * 64,
        }
    else:
        checkpoint["candidate_baseline_id"] = "sha256:" + "8" * 64
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    before = checkpoint_path.read_bytes()

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    expected_code = "candidate_baseline_mismatch" if component == "candidate_baseline" else "resume_snapshot_mismatch"
    assert expected_code in resumed.stderr
    assert checkpoint_path.read_bytes() == before
    assert not output.exists()


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
    from wxdf.decision_ledger import _replay_snapshot, accept_decisions
    from wxdf.transaction import apply_stage_batch, make_snapshot

    checkpoint_path, output, _source_ast_path = _generate(tmp_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    target_id = next(iter(checkpoint["source_ast"]["document"]["blocks"]))
    source_digest = checkpoint["source_facts"]["source"]["content_sha256"]
    decisions = [
        {
            "decision_id": "preloaded-overlay",
            "transaction_id": "preloaded-transaction",
            "operation": "update_overlay",
            "target_node_ids": [target_id],
            "target_source_refs": [],
            "expected_revision": checkpoint["source_ast"]["revision"],
            "property_scope": ["overlay"],
            "value": {"restored_overlay": "kept"},
            "preconditions": [{"kind": "source_sha256", "value": source_digest}],
            "evidence": [{"kind": "user_override"}],
            "invalidates": [],
        },
        {
            "decision_id": "preloaded-presentation",
            "transaction_id": "preloaded-transaction",
            "operation": "set_presentation_override",
            "target_node_ids": [target_id],
            "target_source_refs": [],
            "expected_revision": checkpoint["source_ast"]["revision"],
            "property_scope": ["presentation"],
            "value": {"restored_presentation": "kept"},
            "preconditions": [{"kind": "source_sha256", "value": source_digest}],
            "evidence": [{"kind": "user_override"}],
            "invalidates": [],
        },
    ]
    committed = apply_stage_batch(
        make_snapshot(checkpoint["source_ast"]),
        decisions,
        expected_revision=checkpoint["source_ast"]["revision"],
    )
    ledger = accept_decisions(
        checkpoint["decision_ledger"],
        {"transaction_id": committed.transaction_id, "decisions": decisions},
    )
    replayed = _replay_snapshot(
        checkpoint["source_facts"],
        ledger,
        make_snapshot(checkpoint["source_ast"]),
    )
    transaction_digest = ledger["revisions"][0]["transaction_content_sha256"]
    checkpoint["source_ast"] = replayed.ast
    checkpoint["content_overlay"] = [
        {"node_id": node_id, **value}
        for node_id, value in sorted(replayed.overlays.items())
    ]
    checkpoint["presentation_override"] = [
        {"node_id": node_id, **value}
        for node_id, value in sorted(replayed.presentation_overrides.items())
    ]
    checkpoint["decision_ledger"] = ledger
    checkpoint["applied_transactions"] = [{
        "transaction_id": committed.transaction_id,
        "content_sha256": transaction_digest,
    }]
    checkpoint["transaction_content_digests"] = {
        committed.transaction_id: transaction_digest,
    }
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode == 0, resumed.stderr
    assert output.is_file()
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert all(revision["status"] == "active" for revision in completed["decision_ledger"]["revisions"][:2])
    assert completed["normalized_ast"]["source_ast"] == completed["source_ast"]
    assert completed["normalized_ast"]["overlay"][target_id]["restored_overlay"] == "kept"
    assert completed["normalized_ast"]["presentation_override"][target_id]["restored_presentation"] == "kept"
    assert completed["transaction_content_digests"][committed.transaction_id] == transaction_digest


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


def test_native_resume_rejects_resource_disappearance_between_parse_and_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import builtin_modules, parsers

    source = tmp_path / "vanishing-resource.md"
    resource = tmp_path / "vanishing-pixel.png"
    resource.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg=="
    ))
    source.write_text("# Vanishing resource\n\n![pixel](vanishing-pixel.png)\n", encoding="utf-8")
    request_dir = tmp_path / "vanishing-resource-requests"
    output = tmp_path / "vanishing-resource-output.docx"
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
    original_parse = parsers.parse

    def parse_then_remove(*args, **kwargs):
        facts = original_parse(*args, **kwargs)
        resource.unlink()
        monkeypatch.setattr(parsers, "parse", original_parse)
        monkeypatch.setattr(builtin_modules, "parse", original_parse)
        return facts

    monkeypatch.setattr(parsers, "parse", parse_then_remove)
    monkeypatch.setattr(builtin_modules, "parse", parse_then_remove)
    result = _direct_resume_after_prepare(checkpoint_path, monkeypatch, lambda: None)

    assert result.exit_code != 0
    assert checkpoint_path.read_bytes() == before
    assert json.loads(before)["responses"] == []
    assert not output.exists()


def test_native_resume_renders_verified_resource_bytes_after_source_path_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "bound-resource.md"
    resource = tmp_path / "bound-pixel.png"
    original_resource = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg=="
    )
    resource.write_bytes(original_resource)
    source.write_text("# Bound resource\n\n![pixel](bound-pixel.png)\n", encoding="utf-8")
    request_dir = tmp_path / "bound-resource-requests"
    output = tmp_path / "bound-resource-output.docx"
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

    result = _direct_resume_after_prepare(
        checkpoint_path,
        monkeypatch,
        lambda: resource.write_bytes(b"changed-after-validation"),
    )

    assert result.exit_code == 0, result.diagnostics
    assert output.is_file()
    with ZipFile(output) as archive:
        media = [archive.read(name) for name in archive.namelist() if name.startswith("word/media/")]
    assert original_resource in media


def test_native_resume_renders_verified_template_bytes_after_template_path_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "template-source.md"
    source.write_bytes(SOURCE.read_bytes())
    template = tmp_path / "bound-template.docx"
    template.write_bytes(TEMPLATE.read_bytes())
    request_dir = tmp_path / "bound-template-requests"
    output = tmp_path / "bound-template-output.docx"
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
    request = _read_requests(checkpoint_path)[0]
    _write_responses(checkpoint_path, [_response(request)])

    result = _direct_resume_after_prepare(
        checkpoint_path,
        monkeypatch,
        lambda: template.write_bytes(b"changed-after-validation"),
    )

    assert result.exit_code == 0, result.diagnostics
    assert output.is_file()
    assert result.report["saved_output_audit"]["passed"] is True


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
