from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_ast import _ast
from wxdf.review import MODE_MANIFEST, accept_candidate, evaluate_candidate, mode_manifest


def _bundle(*, level: int = 1, presentation: str = "presentation-1", text: str = "段落0") -> dict:
    ast = _ast(1)
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


def test_mode_manifest_matches_current_preflight_surface() -> None:
    manifest = mode_manifest()

    assert manifest == MODE_MANIFEST
    assert manifest["modular_pure"]["status"] == "enabled"
    assert manifest["modular_online"]["status"] == "closed"
    assert manifest["modular_file"]["status"] == "closed"
    assert manifest["modular_review"]["status"] == "closed"
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
