from copy import deepcopy
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from review_loop import (
    accepted_candidate,
    audit_score,
    build_review_packet,
    collect_audit_findings,
    content_fingerprint,
    unknown_pattern_packet,
    validate_review_patch,
)
from llm_enhancer import enhance_document_model, normalize_mode, should_enhance
from llm_file_protocol import (
    ProtocolError,
    build_llm_request,
    collect_phase_requests,
    replay_phase_responses,
    verify_response,
    verify_source_snapshot,
)


def _model():
    return {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "b1", "block_type": "heading", "level": 1, "text": "范围", "source": {}},
                {
                    "id": "b2",
                    "block_type": "body",
                    "text": "第一项",
                    "source": {"numbering": {"status": "detected", "ilvl": 0, "list_type": "lower_letter_paren"}},
                },
                {"id": "t1", "block_type": "table", "table_type": "unknown", "rows": [], "source": {}},
            ]
        },
    }


def _report():
    return {
        "skill_version": "0.12.8",
        "table_semantics_audit": {
            "passed": False,
            "issues": [{"type": "unknown_table_semantics", "block_id": "t1"}],
        },
        "list_preservation_audit": {
            "passed": False,
            "source_list_body_residue": ["b2"],
        },
        "audit": {"table_format_contract": {"passed": True}},
        "template_finalizer": {"style_audit": {"unexpected_styles": []}},
    }


def test_review_packet_only_contains_repairable_stable_block_targets():
    packet = build_review_packet(_report(), _model())
    assert packet["repairable_count"] == 2
    assert {item["block_id"] for item in packet["targets"]} == {"b2", "t1"}
    assert packet["baseline_score"]["high"] == 2


def test_review_patch_rejects_unlisted_blocks_and_low_confidence():
    patch = {
        "schema_version": "1.0",
        "phase": "C",
        "decisions": [
            {"block_id": "b1", "operation": "retype", "to": {"block_type": "body"}, "confidence": 0.99},
            {"block_id": "t1", "operation": "set_table_type", "to": {"table_type": "data"}, "confidence": 0.80},
        ],
    }
    errors = validate_review_patch(patch, _model(), _report())
    assert len(errors) == 3


def test_content_fingerprint_ignores_roles_but_preserves_text_and_rows():
    before = _model()
    after = deepcopy(before)
    after["document"]["blocks"][1]["block_type"] = "list_item"
    assert content_fingerprint(before) == content_fingerprint(after)
    after["document"]["blocks"][1]["text"] = "改写"
    assert content_fingerprint(before) != content_fingerprint(after)


def test_candidate_requires_strict_audit_improvement_and_content_preservation():
    baseline_model = _model()
    candidate_model = deepcopy(baseline_model)
    candidate_model["document"]["blocks"][1]["block_type"] = "list_item"
    candidate_model["document"]["blocks"][2]["table_type"] = "data"
    candidate_report = deepcopy(_report())
    candidate_report["table_semantics_audit"] = {"passed": True, "issues": []}
    candidate_report["list_preservation_audit"] = {"passed": True, "source_list_body_residue": []}
    accepted, reason, details = accepted_candidate(
        _report(), candidate_report, baseline_model, candidate_model,
    )
    assert accepted is True
    assert reason == "audit_score_improved"
    assert details["after_score"]["high"] == 0


def test_candidate_rejects_unrelated_semantic_changes_when_decisions_are_known():
    baseline_model = _model()
    candidate_model = deepcopy(baseline_model)
    candidate_model["document"]["blocks"][2]["table_type"] = "data"
    candidate_model["document"]["blocks"][0]["level"] = 2
    candidate_report = deepcopy(_report())
    candidate_report["table_semantics_audit"] = {"passed": True, "issues": []}
    accepted, reason, details = accepted_candidate(
        _report(), candidate_report, baseline_model, candidate_model,
        decisions=[{"block_id": "t1"}],
    )
    assert accepted is False
    assert reason == "unrelated_semantic_fields_changed"
    assert details["unrelated_semantic_changes"] == ["b1"]


def test_candidate_requires_target_finding_to_disappear():
    baseline_model = _model()
    candidate_model = deepcopy(baseline_model)
    accepted, reason, _ = accepted_candidate(
        _report(), _report(), baseline_model, candidate_model,
        decisions=[{"block_id": "t1"}],
    )
    assert accepted is False
    assert reason == "target_findings_not_resolved"


def test_nonrepairable_invariants_dominate_score():
    report = _report()
    report["output_structure_audit"] = {"passed": False, "issues": [{"type": "missing_toc"}]}
    findings = collect_audit_findings(report, _model())
    score = audit_score(findings)
    assert score.critical == 1
    assert score.invariant_failures == 1


def test_document_review_capability_applies_only_audited_high_confidence_change():
    model = _model()
    report = _report()
    raw = """{
      "schema_version": "1.0",
      "phase": "C",
      "decisions": [{
        "block_id": "t1",
        "operation": "set_table_type",
        "from": {"table_type": "unknown"},
        "to": {"table_type": "data"},
        "confidence": 0.92,
        "reason": "stable_relational_shape"
      }]
    }"""
    result = enhance_document_model(
        model, report, phase="document_review", llm_call=lambda prompt: raw,
    )
    assert result["document"]["blocks"][2]["table_type"] == "data"
    assert report["llm_enhancer"]["applied"][-1]["block_id"] == "t1"


def test_document_review_rejects_invalid_operation_payloads():
    model = {
        "document": {
            "blocks": [{"id": "h1", "block_type": "heading", "level": 3, "text": "范围"}],
        }
    }
    report = {
        "document_model_issues": [{"block": 1, "type": "heading_missing_level"}],
    }
    raw = """{
      "schema_version": "1.0",
      "phase": "C",
      "decisions": [{
        "block_id": "h1",
        "operation": "adjust_level",
        "from": {"level": 3},
        "to": {"level": "one"},
        "confidence": 0.99
      }]
    }"""
    result = enhance_document_model(
        model, report, phase="document_review", llm_call=lambda prompt: raw,
    )
    assert result["document"]["blocks"][0]["level"] == 3
    assert report["llm_enhancer"]["errors"]


def test_missing_caption_cannot_be_hidden_by_reclassifying_the_table():
    model = {
        "document": {
            "blocks": [{"id": "t1", "block_type": "table", "table_type": "data", "rows": []}],
        }
    }
    report = {
        "table_semantics_audit": {
            "issues": [{"type": "data_table_missing_caption", "block_id": "t1"}],
        }
    }
    packet = build_review_packet(report, model)
    assert packet["repairable_count"] == 0


def test_all_mode_includes_post_audit_review_only_when_targets_exist():
    assert normalize_mode("all") == "sabc"
    report = _report()
    report["review_packet"] = build_review_packet(report, _model())
    assert should_enhance(report, "C", normalize_mode("all")) is True
    report["review_packet"] = {"targets": []}
    assert should_enhance(report, "C", "auto") is False


def test_legacy_abc_modes_keep_their_original_ab_scope():
    assert should_enhance({}, "A", "abc") is True
    assert should_enhance({}, "B", "abc") is True
    assert should_enhance({}, "C", "abc") is False
    assert should_enhance({}, "C", "force-abc") is False
    assert should_enhance({}, "C", normalize_mode("all")) is True


def test_file_protocol_replays_document_review_with_integrity_hash():
    model = _model()
    report = _report()
    collected = collect_phase_requests(model, report, phase="document_review")
    assert collected[0]["phase"] == "C"
    assert collected[0]["capability"] == "document_review"
    request = build_llm_request(run_id="run-1", **collected[0])
    patch = {
        "schema_version": "1.0",
        "phase": "C",
        "decisions": [{
            "block_id": "t1",
            "operation": "set_table_type",
            "from": {"table_type": "unknown"},
            "to": {"table_type": "data"},
            "confidence": 0.91,
            "reason": "relational_table",
        }],
    }
    response = {
        "protocol_version": request["protocol_version"],
        "request_id": request["request_id"],
        "input_hash": request["input_hash"],
        "raw_response": __import__("json").dumps(patch),
    }
    replay_phase_responses(
        model, report, phase="document_review",
        requests=[request], responses=[response],
    )
    assert model["document"]["blocks"][2]["table_type"] == "data"


def test_file_protocol_requires_hash_and_recomputes_request_integrity():
    request = build_llm_request("run-1", "A-0000", "A", "list_detect", "prompt")
    response = {
        "protocol_version": request["protocol_version"],
        "request_id": request["request_id"],
        "raw_response": "{}",
    }
    try:
        verify_response(request, response)
    except ProtocolError as exc:
        assert "Missing input_hash" in str(exc)
    else:
        raise AssertionError("response without input_hash was accepted")

    response["input_hash"] = request["input_hash"]
    request["prompt"] = "tampered"
    try:
        verify_response(request, response)
    except ProtocolError as exc:
        assert "request input_hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered request was accepted")


def test_resume_source_snapshot_must_match(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(b"first")
    from llm_file_protocol import compute_source_sha256
    expected = compute_source_sha256(source)
    verify_source_snapshot(source, expected)
    source.write_bytes(b"second")
    try:
        verify_source_snapshot(source, expected)
    except ProtocolError as exc:
        assert "source file changed" in str(exc)
    else:
        raise AssertionError("changed source was accepted")


def test_unknown_pattern_packet_includes_repairable_remaining_findings():
    packet = unknown_pattern_packet(_report(), _model(), input_hash="sha256:test")
    assert {item.get("block_id") for item in packet["unresolved_findings"]} >= {"b2", "t1"}
