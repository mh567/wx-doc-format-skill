from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_ast import _ast
from wxdf import auditing, review, transaction
from wxdf.review import (
    MODE_MANIFEST,
    accept_candidate,
    build_review_packet,
    evaluate_candidate,
    mode_manifest,
)
from wxdf.transaction import make_snapshot


def _bundle(
    *,
    level: int = 1,
    presentation: str = "presentation-1",
    text: str = "段落0",
    paragraph_count: int = 1,
) -> dict:
    ast = _ast(paragraph_count)
    ast["document"]["blocks"]["p0"]["inline_content"][0]["text"] = text
    ast["document"]["blocks"]["p0"]["role"] = "heading"
    ast["document"]["blocks"]["p0"]["level"] = level
    return {
        "source_ast": ast,
        "normalized_ast": {"semantic_digest": f"semantic-{level}-{text}", "presentation_digest": presentation, "source_ast": ast},
        "artifacts": {"docx": "candidate.docx", "report": "report.json", "manifest": "manifest.json"},
    }


def _findings(before: list[dict], after: list[dict]) -> dict:
    return {"baseline": before, "candidate": after}


def test_semantic_change_with_target_improvement_is_accepted_even_when_presentation_is_same() -> None:
    baseline = _bundle(level=1)
    candidate = _bundle(level=2)
    finding = {"audit_id": "heading:p0", "severity": "high", "repairable": True, "block_id": "p0", "category": "semantic", "evidence": {"type": "heading_hierarchy"}}

    decision = evaluate_candidate(baseline, candidate, _findings([finding], []), decisions=[{"target_node_ids": ["p0"]}])

    assert decision.status == "accepted"
    assert decision.semantic_changed is True
    assert decision.presentation_changed is False
    assert decision.reason == "target_audit_improved"


def test_identical_semantic_and_presentation_outputs_are_no_effect() -> None:
    baseline = _bundle()
    candidate = copy.deepcopy(baseline)

    decision = evaluate_candidate(baseline, candidate, _findings([], []))

    assert decision.status == "no_effect"
    assert decision.accepted is False
    assert decision.reason == "no_effect"


def test_semantic_change_without_target_improvement_is_rejected() -> None:
    baseline = _bundle(level=1)
    candidate = _bundle(level=2)
    finding = {"audit_id": "heading:p0", "severity": "high", "repairable": True, "block_id": "p0", "category": "semantic", "evidence": {"type": "heading_hierarchy"}}

    decision = evaluate_candidate(baseline, candidate, _findings([finding], [finding]))

    assert decision.status == "rejected"
    assert decision.reason == "audit_score_did_not_improve"


def test_content_change_and_protection_regression_are_rejected() -> None:
    baseline = _bundle(level=1)
    candidate = _bundle(level=2, text="未经授权的内容")
    before = [{"audit_id": "heading:p0", "severity": "high", "repairable": True, "block_id": "p0", "category": "semantic", "evidence": {"type": "heading_hierarchy"}}]
    after = [{"audit_id": "invariant:document", "severity": "critical", "repairable": False, "category": "deterministic_format", "evidence": {"type": "output_structure"}}]

    decision = evaluate_candidate(baseline, candidate, _findings(before, after), decisions=[{"target_node_ids": ["p0"]}])

    assert decision.status == "rejected"
    assert decision.reason == "content_changed"


@pytest.mark.parametrize(
    "surface",
    ["children", "containers", "associations", "resources", "overlay"],
)
def test_content_preservation_digest_rejects_non_block_content_mutations(surface: str) -> None:
    baseline = _bundle(level=1, paragraph_count=2)
    candidate = _bundle(level=2, paragraph_count=2)
    document = candidate["source_ast"]["document"]
    if surface == "children":
        document["children"] = list(reversed(document["children"]))
    elif surface == "containers":
        document["containers"]["root"]["kind"] = "mutated-container"
    elif surface == "associations":
        document.setdefault("associations", []).append(
            {"kind": "caption_for", "source_id": "p0", "target_id": "p1"}
        )
    elif surface == "resources":
        document.setdefault("resources", {})["r0"] = {
            "kind": "image",
            "sha256": "sha256:" + "1" * 64,
        }
    else:
        candidate["normalized_ast"]["overlay"] = {"p0": {"marker": "changed"}}

    decision = evaluate_candidate(
        baseline,
        candidate,
        _findings(
            [{"audit_id": "heading:p0", "severity": "high", "repairable": True, "block_id": "p0"}],
            [],
        ),
        decisions=[{"target_node_ids": ["p0"]}],
    )

    assert decision.status == "rejected"
    assert decision.reason == "content_changed"


def test_mode_manifest_matches_current_preflight_surface() -> None:
    manifest = mode_manifest()

    assert manifest == MODE_MANIFEST
    assert manifest["modular_pure"]["status"] == "enabled"
    assert manifest["modular_online"]["status"] == "enabled"
    assert manifest["modular_online"]["contract"] == "T03"
    assert manifest["modular_file"] == {
        "status": "enabled",
        "entrypoint": "CheckpointStore",
        "contract": "T04",
    }
    assert manifest["modular_review"] == {
        "status": "enabled",
        "entrypoint": "ReviewEvaluator",
        "contract": "T05",
    }
    assert all(item["status"] in {"enabled", "closed"} for item in manifest.values())


def test_accept_candidate_persists_accepted_revision_without_changing_overlay_contract() -> None:
    baseline = _bundle(level=1)
    candidate = _bundle(level=2)
    candidate["decision_ledger"] = {"schema_version": "2.0", "revision": 0, "revisions": []}
    finding = {"audit_id": "heading:p0", "severity": "high", "repairable": True, "block_id": "p0", "category": "semantic", "evidence": {"type": "heading_hierarchy"}}
    decisions = [{"operation": "update_role", "target_node_ids": ["p0"], "property_scope": ["role", "level"], "value": {"role": "heading", "level": 2}}]

    decision, accepted = accept_candidate(baseline, candidate, _findings([finding], []), decisions=decisions)

    assert decision.accepted is True
    assert accepted["decision_ledger"]["revision"] == 1
    assert len(accepted["decision_ledger"]["revisions"]) == 1
    assert accepted["acceptance"]["reason"] == "target_audit_improved"


def test_new_high_risk_finding_rejects_candidate_even_when_total_score_improves() -> None:
    baseline = _bundle(level=1)
    candidate = _bundle(level=2)
    target = {
        "audit_id": "heading:p0",
        "severity": "high",
        "repairable": True,
        "block_id": "p0",
        "category": "semantic",
        "evidence": {"type": "heading_hierarchy"},
    }
    second = {**target, "audit_id": "heading:p1", "block_id": "p1"}
    introduced = {
        "audit_id": "saved:new-risk",
        "severity": "high",
        "repairable": False,
        "block_id": "p0",
        "category": "saved_output",
        "evidence": {"type": "new_high_risk"},
    }

    decision = evaluate_candidate(
        baseline,
        candidate,
        _findings([target, second], [introduced]),
        decisions=[{"target_node_ids": ["p0"]}],
    )

    assert decision.accepted is False
    assert decision.reason == "new_high_risk_findings"
    assert decision.details["new_high_risk_findings"] == ["saved:new-risk"]


def test_audit_exception_rejects_candidate_and_returns_unchanged_baseline() -> None:
    baseline = _bundle(level=1)
    candidate = _bundle(level=2)
    candidate["audit_exception"] = {"type": "RuntimeError", "message": "audit failed"}

    decision, selected = accept_candidate(
        baseline,
        candidate,
        _findings([], []),
        decisions=[{"target_node_ids": ["p0"]}],
    )

    assert decision.accepted is False
    assert decision.reason == "audit_exception"
    assert selected == baseline
    assert selected is not baseline


def test_review_packet_contains_only_stable_repairable_targets() -> None:
    bundle = _bundle(level=1)
    findings = [
        {
            "audit_id": "model:heading:p0",
            "severity": "high",
            "repairable": True,
            "block_id": "p0",
            "category": "model",
            "evidence": {"code": "heading_level_jump", "expected_level": 2},
        },
        {
            "audit_id": "saved:paragraph-order",
            "severity": "critical",
            "repairable": False,
            "category": "saved_output",
            "evidence": {"code": "paragraph_order_changed"},
        },
    ]

    assert callable(getattr(review, "build_review_packet", None))
    packet = review.build_review_packet(bundle, findings)

    assert packet["schema_version"] == "2.0"
    assert packet["baseline_revision"] == 1
    assert packet["repairable_count"] == 1
    assert packet["targets"][0]["block_id"] == "p0"
    assert packet["targets"][0]["source_ref"]["source_id"] == "p0"
    assert packet["targets"][0]["allowed_operations"] == ["update_role"]
    assert packet["targets"][0]["property_scope"] == ["role", "level"]
    assert packet["targets"][0]["allowed_values"] == {
        "role": ["heading"],
        "level": [2],
    }
    assert packet["targets"][0]["required_value_fields"] == ["role", "level"]


@pytest.mark.parametrize(
    "decision",
    [
        {
            "operation": "update_role",
            "target_node_ids": ["p1"],
            "property_scope": ["role"],
            "value": {"role": "note"},
        },
        {
            "operation": "update_association",
            "target_node_ids": ["p0"],
            "property_scope": ["association"],
            "value": {"association": {"kind": "caption_target", "source_id": "p0", "target_id": "p1"}},
        },
        {
            "operation": "update_role",
            "target_node_ids": ["p0"],
            "property_scope": ["role", "semantic_role"],
            "value": {"role": "heading", "semantic_role": "appendix_title"},
        },
    ],
    ids=["target", "operation", "property_scope"],
)
def test_review_packet_rejects_candidate_decisions_outside_explicit_authority(decision: dict) -> None:
    baseline = _bundle(level=1, paragraph_count=2)
    finding = {
        "audit_id": "model:heading_level_jump:p0",
        "severity": "high",
        "repairable": True,
        "block_id": "p0",
        "category": "model",
        "evidence": {"code": "heading_level_jump", "expected_level": 2},
    }
    packet = build_review_packet(baseline, [finding])

    violation = review.validate_review_decisions(packet, [decision])

    assert violation is not None
    assert violation["code"] == "review_scope_violation"


@pytest.mark.parametrize(
    "value",
    [
        {"role": "note", "level": 2},
        {"role": "heading", "level": 1},
        {"role": "heading"},
        {"level": 2},
        {},
        {"role": "heading", "level": 2, "semantic_role": "appendix_title"},
    ],
    ids=["role", "level", "missing_level", "missing_role", "empty", "extra_field"],
)
def test_heading_review_authority_rejects_values_outside_finding(value: dict) -> None:
    baseline = _bundle(level=1)
    finding = {
        "audit_id": "model:heading_level_jump:p0",
        "severity": "high",
        "repairable": True,
        "block_id": "p0",
        "category": "model",
        "evidence": {"code": "heading_level_jump", "expected_level": 2},
    }
    packet = build_review_packet(baseline, [finding])

    violation = review.validate_review_decisions(packet, [{
        "operation": "update_role",
        "target_node_ids": ["p0"],
        "property_scope": ["role", "level"],
        "value": value,
    }])

    assert violation is not None
    assert violation["kind"] == "value"


def test_candidate_rejects_out_of_scope_ast_semantic_mutation_after_authorized_decision() -> None:
    baseline = _bundle(level=1, paragraph_count=2)
    candidate = copy.deepcopy(baseline)
    candidate["source_ast"]["document"]["blocks"]["p0"]["level"] = 2
    candidate["source_ast"]["document"]["blocks"]["p1"]["role"] = "note"
    candidate["normalized_ast"]["semantic_digest"] = "semantic-authorized-plus-hidden"
    finding = {
        "audit_id": "model:heading_level_jump:p0",
        "severity": "high",
        "repairable": True,
        "block_id": "p0",
        "category": "model",
        "evidence": {"code": "heading_level_jump", "expected_level": 2},
    }
    packet = build_review_packet(baseline, [finding])
    decisions = [{
        "operation": "update_role",
        "target_node_ids": ["p0"],
        "property_scope": ["role", "level"],
        "value": {"role": "heading", "level": 2},
    }]

    decision = evaluate_candidate(
        baseline,
        candidate,
        _findings([finding], []),
        decisions=decisions,
        review_packet=packet,
    )

    assert decision.status == "rejected"
    assert decision.reason == "review_scope_violation"
    assert decision.details["review_scope_violation"]["code"] == "out_of_scope_semantic_change"


def test_valid_move_node_allows_passive_sibling_index_shifts() -> None:
    baseline = _bundle(level=1, paragraph_count=3)
    candidate = copy.deepcopy(baseline)
    candidate["source_ast"]["document"]["children"]["root"] = ["p1", "p2", "p0"]
    candidate["normalized_ast"]["semantic_digest"] = "semantic-moved-p0"
    finding = {
        "audit_id": "model:parent:p0",
        "severity": "high",
        "repairable": True,
        "block_id": "p0",
    }

    decision = evaluate_candidate(
        baseline,
        candidate,
        _findings([finding], []),
        decisions=[{
            "operation": "move_node",
            "target_node_ids": ["p0"],
            "property_scope": ["parent"],
            "value": {"new_parent_id": "root", "index": 2},
        }],
    )

    assert decision.status == "accepted"
    assert decision.reason == "target_audit_improved"


def test_audit_findings_preserve_risk_and_repairability_across_audit_sources() -> None:
    assert callable(getattr(auditing, "collect_audit_findings", None))
    findings = auditing.collect_audit_findings(
        {
            "model": {
                "passed": False,
                "issues": [
                    {
                        "code": "heading_hierarchy",
                        "node_id": "p0",
                        "severity": "high",
                        "repairable": True,
                    }
                ],
            },
            "saved_output": {
                "passed": False,
                "issues": [{"code": "paragraph_order_changed"}],
            },
        }
    )

    assert findings[0]["audit_id"] == "model:heading_hierarchy:p0"
    assert findings[0]["repairable"] is True
    assert findings[1]["severity"] == "critical"
    assert findings[1]["repairable"] is False


def test_production_heading_level_jump_is_repairable_and_unknown_code_is_not() -> None:
    bundle = _bundle(level=1, paragraph_count=2)
    ast = bundle["source_ast"]
    ast["document"]["blocks"]["p1"]["role"] = "heading"
    ast["document"]["blocks"]["p1"]["level"] = 3
    bundle["normalized_ast"].update({
        "source_ast": ast,
        "source_revision": ast["revision"],
        "presentation": {"nodes": {}},
        "generated_nodes": [],
        "diagnostics": [],
    })

    model_audit = auditing.check_model(bundle["normalized_ast"])
    findings = auditing.collect_audit_findings({
        "model": model_audit,
        "saved_output": {
            "passed": False,
            "issues": [{"code": "unknown_saved_issue", "node_id": "p0"}],
        },
    })

    heading = next(item for item in findings if item["evidence"]["code"] == "heading_level_jump")
    unknown = next(item for item in findings if item["evidence"]["code"] == "unknown_saved_issue")
    assert heading["block_id"] == "p1"
    assert heading["repairable"] is True
    assert heading["evidence"]["expected_level"] == 2
    assert unknown["repairable"] is False


def test_candidate_revision_keeps_baseline_snapshot_independent_for_rollback() -> None:
    snapshot = make_snapshot(_ast(1))
    baseline = copy.deepcopy(snapshot.ast)
    assert callable(getattr(transaction, "stage_candidate_revision", None))
    revision = transaction.stage_candidate_revision(
        snapshot,
        [
            {
                "decision_id": "review-p0",
                "operation": "update_role",
                "target_node_ids": ["p0"],
                "expected_revision": 1,
                "property_scope": ["role"],
                "value": {"role": "note"},
            }
        ],
        expected_revision=1,
    )

    assert revision.baseline.ast == baseline
    assert revision.candidate.snapshot.ast["revision"] == 2
    assert revision.candidate.snapshot.ast["document"]["blocks"]["p0"]["role"] == "note"
    revision.candidate.snapshot.ast["document"]["blocks"]["p0"]["role"] = "body"
    assert revision.rollback().ast == baseline
