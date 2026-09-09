from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_normalization import _profile
from test_modular_ast import _ast
from wxdf.normalization import normalize
from wxdf.review import accept_candidate, evaluate_candidate
from wxdf.transaction import apply_stage_batch, make_snapshot


def _parent_ast() -> dict:
    ast = _ast(0)
    source_ref = {
        "source_id": "parent-fixture",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": 0, "end": 1, "unit": "unicode_code_point"},
    }
    ast["document"]["blocks"].update(
        {
            "p-parent": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "父项", "marks": []}], "source_ref": source_ref},
            "p-child": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "第二父项", "marks": []}], "source_ref": source_ref},
            "p-nested": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "子项", "marks": []}], "source_ref": source_ref},
        }
    )
    ast["document"]["containers"].update(
        {
            "outer-list": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "outer"},
            "parent-item": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "outer"},
            "second-item": {"kind": "list_item", "source_marker": "2.", "source_ordinal": 2, "source_sequence_id": "outer"},
            "nested-list": {"kind": "list", "list_kind": "unordered", "source_start": 1, "source_sequence_id": "nested"},
            "nested-item": {"kind": "list_item", "source_marker": "-", "source_ordinal": 1, "source_sequence_id": "nested"},
        }
    )
    ast["document"]["children"] = {
        "root": ["outer-list"],
        "outer-list": ["parent-item", "second-item"],
        "parent-item": ["p-parent", "nested-list"],
        "second-item": ["p-child"],
        "nested-list": ["nested-item"],
        "nested-item": ["p-nested"],
    }
    return ast


def _parent_bundles() -> tuple[dict, dict, dict]:
    baseline_ast = _parent_ast()
    baseline_normalized = normalize(baseline_ast, _profile(), strategy="wx-default-v2")
    committed = apply_stage_batch(
        make_snapshot(baseline_ast),
        [{"operation": "move_node", "target_node_ids": ["nested-list"], "expected_revision": 1, "value": {"new_parent_id": "second-item"}}],
        expected_revision=1,
    )
    candidate_ast = committed.snapshot.ast
    candidate_normalized = normalize(candidate_ast, _profile(), strategy="wx-default-v2")
    return {
        "source_ast": baseline_ast,
        "normalized_ast": baseline_normalized,
    }, {
        "source_ast": candidate_ast,
        "normalized_ast": candidate_normalized,
    }, {
        "baseline": baseline_normalized,
        "candidate": candidate_normalized,
    }


def _findings(before: list[dict], after: list[dict]) -> dict:
    return {"baseline": before, "candidate": after}


def _parent_finding() -> dict:
    return {
        "audit_id": "list-parent:nested-list",
        "severity": "high",
        "repairable": True,
        "block_id": "nested-list",
        "category": "semantic",
        "evidence": {"type": "list_parent_relationship"},
    }


def test_l15_parent_repair_is_accepted_when_presentation_is_unchanged() -> None:
    baseline, candidate, normalized = _parent_bundles()
    finding = _parent_finding()

    assert baseline["normalized_ast"]["presentation_digest"] == candidate["normalized_ast"]["presentation_digest"]
    assert baseline["normalized_ast"]["semantic_digest"] != candidate["normalized_ast"]["semantic_digest"]
    assert baseline["source_ast"]["document"]["children"]["parent-item"] == ["p-parent", "nested-list"]
    assert "nested-list" not in baseline["source_ast"]["document"]["children"]["second-item"]
    assert candidate["source_ast"]["document"]["children"]["second-item"] == ["p-child", "nested-list"]
    assert "nested-list" not in candidate["source_ast"]["document"]["children"]["parent-item"]

    decision = evaluate_candidate(
        baseline,
        candidate,
        _findings([finding], []),
        decisions=[{"target_node_ids": ["nested-list"], "operation": "move_node"}],
    )

    assert decision.status == "accepted"
    assert decision.reason == "target_audit_improved"
    assert decision.accepted is True
    assert decision.semantic_changed is True
    assert decision.presentation_changed is False

    accepted_decision, accepted = accept_candidate(
        baseline,
        candidate,
        _findings([finding], []),
        decisions=[{"target_node_ids": ["nested-list"], "operation": "move_node"}],
    )
    assert accepted_decision.accepted is True
    assert accepted["source_ast"]["document"]["children"]["second-item"] == ["p-child", "nested-list"]
    assert "nested-list" not in accepted["source_ast"]["document"]["children"]["parent-item"]
    assert accepted["normalized_ast"] == normalized["candidate"]


def test_l15_only_identical_semantic_and_presentation_results_are_no_effect() -> None:
    baseline, _, _ = _parent_bundles()
    candidate = copy.deepcopy(baseline)

    decision = evaluate_candidate(baseline, candidate, _findings([], []))

    assert decision.status == "no_effect"
    assert decision.reason == "no_effect"
    assert decision.accepted is False
