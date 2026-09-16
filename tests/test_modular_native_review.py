from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf import auditing, transaction
from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.registry import ModuleRegistry


TEMPLATE = ROOT / "assets" / "wx_template.docx"


def _request(tmp_path: Path, source: Path, adapter) -> ConversionRequest:
    return ConversionRequest(
        input_path=source,
        output_path=tmp_path / "formal.docx",
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={
            "profile": "native_v2",
            "llm_enhance": "force-c",
            "enhancement_call": adapter,
            "enhancement_budget": 1,
            "source_ast_path": tmp_path / "accepted-source.json",
        },
    )


def _review_patch(payload, *, role: str = "note") -> dict:
    packet = payload.get("review_packet") or payload.get("context", {}).get("review_packet")
    if packet:
        target = packet["targets"][0]
        target_id = target["block_id"]
        source_ref = copy.deepcopy(target["source_ref"])
        revision = packet["baseline_revision"]
    else:
        ast = payload["items"][0]["source_ast"]
        target_id, target = next(
            (node_id, node)
            for node_id, node in ast["document"]["blocks"].items()
            if node.get("block_type") == "paragraph"
        )
        source_ref = copy.deepcopy(target["source_ref"])
        revision = ast["revision"]
    candidate_id = f"review-{target_id}"
    return {
        "patch_schema_version": "2.0",
        "patch_id": "native-review-patch",
        "base_revision": revision,
        "phase": "relation",
        "candidates": [
            {
                "candidate_id": candidate_id,
                "module_id": "document_review",
                "module_version": "2.0",
                "target_source_refs": [source_ref],
                "target_node_ids": [target_id],
                "proposal": {
                    "operation": "update_role",
                    "property_scope": ["role"],
                    "value": {"role": role},
                },
                "positive_evidence": [{"kind": "post_audit_review"}],
                "negative_evidence": [],
                "scope": {"stages": ["role", "normalize", "render", "audit"]},
                "constraints": [],
                "rationale": "repair the audited role finding",
                "confidence": 0.95,
            }
        ],
        "decisions": [],
    }


def _reviewable_model_audit(real_check, *, always_fail: bool = False, raise_on_note: bool = False):
    def check(normalized: dict) -> dict:
        result = real_check(normalized)
        target_id, target = next(
            (node_id, node)
            for node_id, node in normalized["source_ast"]["document"]["blocks"].items()
            if node.get("block_type") == "paragraph"
        )
        if raise_on_note and target.get("role") == "note":
            raise RuntimeError("candidate audit exploded")
        if always_fail or target.get("role") != "note":
            result["passed"] = False
            result["issues"].append(
                {
                    "code": "reviewable_role",
                    "node_id": target_id,
                    "severity": "high",
                    "repairable": True,
                    "allowed_operations": ["update_role"],
                    "property_scope": ["role"],
                    "allowed_values": {"role": ["note"]},
                }
            )
        return result

    return check


def _external_response(request: dict, *, candidates: list[dict] | None = None) -> dict:
    return {
        "request_id": request["request_id"],
        "response_id": f"response-{request['request_id']}",
        "candidate_set_version": "2.0",
        "base_revision": request["model_revision"],
        "view_sha256": request["view_sha256"],
        "candidates": copy.deepcopy(candidates or []),
    }


def _write_external_responses(path: Path, responses: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in responses),
        encoding="utf-8",
    )


def test_native_review_uses_independent_artifacts_and_publishes_accepted_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    packets: list[dict] = []
    real_check = auditing.check_model
    monkeypatch.setattr(auditing, "check_model", _reviewable_model_audit(real_check))

    def adapter(payload):
        packets.append(copy.deepcopy(dict(payload["context"]["review_packet"])))
        return _review_patch(payload)

    result = ConversionKernel().convert(_request(tmp_path, source, adapter))

    assert result.status == "completed"
    assert packets and packets[0]["repairable_count"] == 1
    review = result.report["native_review"]
    assert review["status"] == "accepted"
    assert review["acceptance"]["accepted"] is True
    assert review["acceptance"]["reason"] == "target_audit_improved"
    assert review["baseline_artifact"] != review["candidate_artifact"]
    assert review["acceptance"]["details"]["unresolved_target_findings"] == []
    accepted_source = __import__("json").loads((tmp_path / "accepted-source.json").read_text(encoding="utf-8"))
    assert any(node.get("role") == "note" for node in accepted_source["document"]["blocks"].values())
    assert zipfile.is_zipfile(tmp_path / "formal.docx")


def test_native_review_reprocesses_candidate_through_registered_child_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    real_check = auditing.check_model
    monkeypatch.setattr(auditing, "check_model", _reviewable_model_audit(real_check))
    executed_plans: list[list[str]] = []
    real_execute = ModuleRegistry.execute

    def record_plan(self, plan, context):
        executed_plans.append([module.module_id for module in plan.modules])
        return real_execute(self, plan, context)

    monkeypatch.setattr(ModuleRegistry, "execute", record_plan)
    result = ConversionKernel().convert(
        _request(tmp_path, source, lambda payload: _review_patch(payload))
    )

    assert result.status == "completed", result.diagnostics
    assert len(executed_plans) == 3
    assert executed_plans[1] == [
        "native-review-candidate-set",
        "native-resolver",
    ]
    assert executed_plans[2] == [
        "native-transaction",
        "native-normalizer",
        "native-renderer",
        "native-auditor",
    ]
    assert result.report["native_review"]["reprocess_plan"] == [
        *executed_plans[1],
        *executed_plans[2],
    ]


def test_native_review_repairs_real_heading_audit_without_audit_monkeypatch(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n### 跳级标题\n\n正文。\n", encoding="utf-8")

    def adapter(payload):
        patch = _review_patch(payload, role="heading")
        target = payload["context"]["review_packet"]["targets"][0]
        patch["candidates"][0]["proposal"] = {
            "operation": "update_role",
            "property_scope": ["role", "level"],
            "value": {
                "role": "heading",
                "level": target["evidence"]["expected_level"],
            },
        }
        return patch

    result = ConversionKernel().convert(_request(tmp_path, source, adapter))

    assert result.status == "completed", result.diagnostics
    assert result.report["native_review"]["status"] == "accepted"
    assert result.report["review_packet"]["repairable_count"] == 1
    assert result.report["native_review"]["acceptance"]["reason"] == "target_audit_improved"
    assert result.report["model_audit"]["issues"] == []
    assert zipfile.is_zipfile(tmp_path / "formal.docx")


@pytest.mark.parametrize("invalid_value", ["missing_role", "missing_level", "extra_field"])
def test_native_heading_review_rejects_incomplete_value_before_candidate_artifacts(
    tmp_path: Path,
    invalid_value: str,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Heading\n\n### Jump\n\nBody.\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"old-formal")

    def adapter(payload):
        patch = _review_patch(payload, role="heading")
        target = payload["context"]["review_packet"]["targets"][0]
        value = {"role": "heading", "level": target["evidence"]["expected_level"]}
        if invalid_value == "extra_field":
            value["semantic_role"] = "appendix_title"
        else:
            value.pop(invalid_value.removeprefix("missing_"))
        patch["candidates"][0]["proposal"] = {
            "operation": "update_role",
            "property_scope": ["role", "level"],
            "value": value,
        }
        return patch

    result = ConversionKernel().convert(_request(tmp_path, source, adapter))

    review = result.report["native_review"]
    assert result.status == "failed"
    assert review["status"] == "rolled_back"
    assert review["acceptance"]["reason"] == "review_scope_violation"
    assert not Path(review["candidate_artifact"]).exists()
    assert formal.read_bytes() == b"old-formal"


def test_native_publication_failure_preserves_formal_and_all_existing_auxiliary_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n### 跳级标题\n\n正文。\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    source_ast_path = tmp_path / "accepted-source.json"
    report_path = tmp_path / "report.json"
    formal.write_bytes(b"old-formal")
    source_ast_path.write_bytes(b"old-source-ast")
    report_path.write_bytes(b"old-report")
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_bytes(b"file-prevents-directory-creation")
    normalized_path = blocked_parent / "normalized.json"

    def adapter(payload):
        patch = _review_patch(payload, role="heading")
        target = payload["context"]["review_packet"]["targets"][0]
        patch["candidates"][0]["proposal"] = {
            "operation": "update_role",
            "property_scope": ["role", "level"],
            "value": {
                "role": "heading",
                "level": target["evidence"]["expected_level"],
            },
        }
        return patch

    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=formal,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={
            "profile": "native_v2",
            "llm_enhance": "force-c",
            "enhancement_call": adapter,
            "enhancement_budget": 1,
            "source_ast_path": source_ast_path,
            "normalized_ast_path": normalized_path,
            "report_path": report_path,
        },
    ))

    assert result.status == "failed"
    assert any(item.get("code") == "PUBLICATION_FAILED" for item in result.diagnostics)
    assert formal.read_bytes() == b"old-formal"
    assert source_ast_path.read_bytes() == b"old-source-ast"
    assert report_path.read_bytes() == b"old-report"
    assert not normalized_path.exists()


def test_native_entry_recovers_interrupted_formal_generation_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import publication

    formal = tmp_path / "formal.docx"
    report = tmp_path / "report.json"
    formal.write_bytes(b"old-formal")
    report.write_bytes(b"old-report")
    real_replace = publication.os.replace
    interrupted = False

    def interrupt_after_formal_install(source: Path | str, target: Path | str) -> None:
        nonlocal interrupted
        real_replace(source, target)
        if (
            not interrupted
            and Path(target) == formal
            and ".staging-" in Path(source).name
        ):
            interrupted = True
            raise SystemExit("injected native formal interruption")

    monkeypatch.setattr(publication.os, "replace", interrupt_after_formal_install)
    with pytest.raises(SystemExit, match="native formal interruption"):
        publication.publish_files_atomically({formal: b"new-formal", report: b"new-report"})
    monkeypatch.setattr(publication.os, "replace", real_replace)

    result = ConversionKernel().convert(ConversionRequest(
        input_path=tmp_path / "missing.md",
        output_path=formal,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={
            "profile": "native_v2",
            "llm_enhance": "off",
            "report_path": report,
        },
    ))

    assert result.status == "failed"
    assert formal.read_bytes() == b"old-formal"
    assert report.read_bytes() == b"old-report"
    assert not list(tmp_path.glob(".wxdf-publication-*.journal"))


def test_rejected_native_review_preserves_existing_formal_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"existing-formal-output")
    real_check = auditing.check_model
    monkeypatch.setattr(
        auditing,
        "check_model",
        _reviewable_model_audit(real_check, always_fail=True),
    )

    result = ConversionKernel().convert(
        _request(tmp_path, source, lambda payload: _review_patch(payload))
    )

    assert result.status == "failed"
    assert result.report["native_review"]["status"] == "rolled_back"
    assert result.report["native_review"]["acceptance"]["accepted"] is False
    assert result.report["native_review"]["acceptance"]["reason"] in {
        "target_findings_not_resolved",
        "audit_score_did_not_improve",
    }
    assert formal.read_bytes() == b"existing-formal-output"


def test_candidate_audit_exception_rolls_back_and_preserves_formal_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"existing-formal-output")
    real_check = auditing.check_model
    monkeypatch.setattr(
        auditing,
        "check_model",
        _reviewable_model_audit(real_check, raise_on_note=True),
    )

    result = ConversionKernel().convert(
        _request(tmp_path, source, lambda payload: _review_patch(payload))
    )

    assert result.status == "failed"
    assert result.report["native_review"]["status"] == "rolled_back"
    assert result.report["native_review"]["acceptance"]["reason"] == "audit_exception"
    assert result.report["native_review"]["audit_exception"]["type"] == "RuntimeError"
    assert formal.read_bytes() == b"existing-formal-output"


def test_candidate_transaction_exception_returns_explicit_rollback_with_baseline_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"existing-formal-output")
    real_check = auditing.check_model
    monkeypatch.setattr(auditing, "check_model", _reviewable_model_audit(real_check))

    def reject_candidate(*_args, **_kwargs):
        raise transaction.TransactionConflict("candidate transaction rejected")

    monkeypatch.setattr(transaction, "stage_candidate_revision", reject_candidate)

    result = ConversionKernel().convert(
        _request(tmp_path, source, lambda payload: _review_patch(payload))
    )

    review = result.report["native_review"]
    assert result.status == "failed"
    assert review["status"] == "rolled_back"
    assert review["acceptance"]["reason"] == "candidate_transaction_failed"
    assert review["audit_exception"]["type"] == "TransactionConflict"
    assert result.report["model_audit"]["issues"]
    assert formal.read_bytes() == b"existing-formal-output"


def test_native_review_rejects_candidate_target_outside_review_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"existing-formal-output")
    real_check = auditing.check_model
    monkeypatch.setattr(auditing, "check_model", _reviewable_model_audit(real_check))

    def adapter(payload):
        patch = _review_patch(payload)
        ast = payload["items"][0]["source_ast"]
        authorized_id = payload["context"]["review_packet"]["targets"][0]["block_id"]
        other_id, other_node = next(
            (node_id, node)
            for node_id, node in ast["document"]["blocks"].items()
            if node_id != authorized_id and node.get("block_type") == "paragraph"
        )
        patch["candidates"][0]["target_node_ids"] = [other_id]
        patch["candidates"][0]["target_source_refs"] = [
            copy.deepcopy(other_node["source_ref"])
        ]
        return patch

    result = ConversionKernel().convert(_request(tmp_path, source, adapter))

    review = result.report["native_review"]
    assert result.status == "failed"
    assert review["status"] == "rolled_back"
    assert review["acceptance"]["reason"] == "review_scope_violation"
    assert review["acceptance"]["details"]["review_scope_violation"]["kind"] == "target"
    assert formal.read_bytes() == b"existing-formal-output"


def test_all_file_mode_waits_for_relation_then_review_and_publishes_accepted_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"existing-formal-output")
    checkpoint_path = tmp_path / "requests" / "run.json"
    requests_path = checkpoint_path.with_name("llm_requests.jsonl")
    responses_path = checkpoint_path.with_name("llm_responses.jsonl")
    configuration = {
        "input": str(source),
        "output": str(formal),
        "template": str(TEMPLATE),
        "llm_enhance": "all",
        "pipeline": "modular",
        "list_policy": "wx-default-v2",
    }
    real_check = auditing.check_model
    monkeypatch.setattr(auditing, "check_model", _reviewable_model_audit(real_check))

    def request(protocol_mode: str) -> ConversionRequest:
        options = {
            "profile": "native_v2",
            "llm_enhance": "all",
            "protocol_mode": protocol_mode,
            "checkpoint_path": checkpoint_path,
            "checkpoint_configuration": configuration,
        }
        if protocol_mode == "generate_requests":
            options.update(
                {
                    "requests_path": requests_path,
                    "responses_path": responses_path,
                    "enhancement_request_sink": [],
                }
            )
        return ConversionRequest(
            input_path=source,
            output_path=formal,
            template_path=TEMPLATE,
            pipeline="modular",
            list_policy="wx-default-v2",
            options=options,
        )

    relation_wait = ConversionKernel().convert(request("generate_requests"))
    assert relation_wait.status == "waiting_external"
    relation_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    relation_requests = relation_checkpoint["requests"]
    assert [item["capability"] for item in relation_requests] == [
        "toc_region_review",
        "list_detect",
        "caption_gen",
    ]
    candidate_baseline_id = relation_checkpoint["candidate_baseline_id"]
    relation_responses = [_external_response(item) for item in relation_requests]
    _write_external_responses(responses_path, relation_responses)

    review_wait = ConversionKernel().convert(request("resume"))

    assert review_wait.status == "waiting_external", review_wait.diagnostics
    assert formal.read_bytes() == b"existing-formal-output"
    review_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert review_checkpoint["candidate_baseline_id"] == candidate_baseline_id
    assert [item["request_id"] for item in review_checkpoint["responses"]] == [
        item["request_id"] for item in relation_responses
    ]
    assert all(item["applied"] is False for item in review_checkpoint["responses"])
    assert all(item["response_sha256"].startswith("sha256:") for item in review_checkpoint["responses"])
    assert [item["capability"] for item in review_checkpoint["requests"]] == [
        "toc_region_review",
        "list_detect",
        "caption_gen",
        "document_review",
    ]
    review_request = review_checkpoint["requests"][-1]
    audited_revision = review_request["review_packet"]["baseline_revision"]
    assert review_request["model_revision"] == audited_revision
    assert review_request["items"][0]["revision"] == audited_revision
    assert review_request["items"][0]["source_ast"]["revision"] == audited_revision
    patch = _review_patch(review_request)
    review_response = _external_response(
        review_request,
        candidates=patch["candidates"],
    )
    _write_external_responses(responses_path, [*relation_responses, review_response])

    accepted = ConversionKernel().convert(request("resume"))

    assert accepted.status == "completed", (
        accepted.diagnostics,
        accepted.report.get("native_review"),
    )
    assert accepted.report["native_review"]["status"] == "accepted"
    assert accepted.report["native_review"]["acceptance"]["reason"] == "target_audit_improved"
    assert zipfile.is_zipfile(formal)
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert completed["status"] == "completed"
    assert completed["source_ast"]["revision"] == accepted.report["native_transaction"]["committed_revision"]
    relation_transaction = accepted.report["decision_set"]["transaction_id"]
    review_transaction = accepted.report["native_review"]["decision_set"]["transaction_id"]
    assert {item["transaction_id"] for item in completed["decision_ledger"]["revisions"]} == {
        relation_transaction,
        review_transaction,
    }
    assert {
        item["transaction_id"]
        for item in completed["responses"]
        if item["request_id"] != review_request["request_id"]
    } == {relation_transaction}
    assert next(
        item["transaction_id"]
        for item in completed["responses"]
        if item["request_id"] == review_request["request_id"]
    ) == review_transaction
    ledger_digests: dict[str, str] = {}
    for revision in completed["decision_ledger"]["revisions"]:
        transaction_id = revision["transaction_id"]
        digest = revision["transaction_content_sha256"]
        assert ledger_digests.setdefault(transaction_id, digest) == digest
    assert ledger_digests == completed["transaction_content_digests"]
    assert {
        item["transaction_id"]: item["content_sha256"]
        for item in completed["applied_transactions"]
    } == ledger_digests
    assert {
        binding["transaction_id"]: binding["transaction_content_sha256"]
        for binding in completed["response_transaction_bindings"].values()
    } == ledger_digests

    tampered = copy.deepcopy(completed)
    for revision in tampered["decision_ledger"]["revisions"]:
        if revision["transaction_id"] == review_transaction:
            revision["transaction_content_sha256"] = "sha256:" + "0" * 64
    checkpoint_path.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    checkpoint_bytes = checkpoint_path.read_bytes()
    formal_bytes = formal.read_bytes()

    rejected_replay = ConversionKernel().convert(request("resume"))

    assert rejected_replay.status == "failed"
    assert any(
        item.get("code") == "transaction_state_mismatch"
        for item in rejected_replay.diagnostics
    )
    assert checkpoint_path.read_bytes() == checkpoint_bytes
    assert formal.read_bytes() == formal_bytes


def test_rejected_file_review_persists_terminal_rollback_and_replays_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"existing-formal-output")
    checkpoint_path = tmp_path / "requests" / "run.json"
    requests_path = checkpoint_path.with_name("llm_requests.jsonl")
    responses_path = checkpoint_path.with_name("llm_responses.jsonl")
    configuration = {
        "input": str(source),
        "output": str(formal),
        "template": str(TEMPLATE),
        "llm_enhance": "all",
        "pipeline": "modular",
        "list_policy": "wx-default-v2",
    }
    real_check = auditing.check_model
    monkeypatch.setattr(
        auditing,
        "check_model",
        _reviewable_model_audit(real_check, always_fail=True),
    )

    def run(protocol_mode: str):
        options = {
            "profile": "native_v2",
            "llm_enhance": "all",
            "protocol_mode": protocol_mode,
            "checkpoint_path": checkpoint_path,
            "checkpoint_configuration": configuration,
        }
        if protocol_mode == "generate_requests":
            options.update({
                "requests_path": requests_path,
                "responses_path": responses_path,
                "enhancement_request_sink": [],
            })
        return ConversionKernel().convert(ConversionRequest(
            input_path=source,
            output_path=formal,
            template_path=TEMPLATE,
            pipeline="modular",
            list_policy="wx-default-v2",
            options=options,
        ))

    assert run("generate_requests").status == "waiting_external"
    relation_requests = json.loads(checkpoint_path.read_text(encoding="utf-8"))["requests"]
    relation_responses = [_external_response(item) for item in relation_requests]
    _write_external_responses(responses_path, relation_responses)
    assert run("resume").status == "waiting_external"
    review_request = json.loads(checkpoint_path.read_text(encoding="utf-8"))["requests"][-1]
    review_response = _external_response(
        review_request,
        candidates=_review_patch(review_request)["candidates"],
    )
    _write_external_responses(responses_path, [*relation_responses, review_response])

    rejected = run("resume")

    assert rejected.status == "failed"
    assert rejected.report["native_review"]["status"] == "rolled_back"
    terminal = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert terminal["status"] == "failed", rejected.diagnostics
    assert terminal["review_outcome"]["status"] == "rolled_back"
    assert terminal["review_outcome"]["acceptance"]["accepted"] is False
    relation_ids = {item["request_id"] for item in relation_requests}
    assert {
        item["request_id"]
        for item in terminal["responses"]
        if item["applied"] is True
    } == relation_ids
    assert next(
        item for item in terminal["responses"]
        if item["request_id"] == review_request["request_id"]
    )["applied"] is False
    assert set(terminal["response_transaction_bindings"]) == relation_ids
    assert formal.read_bytes() == b"existing-formal-output"
    terminal_bytes = checkpoint_path.read_bytes()

    replayed = run("resume")

    assert replayed.status == "failed", replayed.diagnostics
    assert "review_outcome" in replayed.report, (replayed.report, replayed.diagnostics)
    assert replayed.report["review_outcome"]["status"] == "rolled_back"
    assert checkpoint_path.read_bytes() == terminal_bytes
    assert formal.read_bytes() == b"existing-formal-output"
