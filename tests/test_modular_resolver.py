from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.resolver import resolve
from wxdf.parsers import parse
from wxdf.semantics.captions import collect_caption_candidates
from wxdf.semantics.protected import collect_protected_candidates
from wxdf.semantics.regions import classify_regions, collect_scope_candidates
from wxdf.semantics.source_ast import build_source_ast
from wxdf.semantics.tables import collect_table_candidates


def _candidate(
    candidate_id: str,
    module_id: str,
    role: str,
    *,
    evidence_kind: str = "heuristic",
    confidence: float = 0.99,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "module_id": module_id,
        "module_version": "1.0",
        "target_source_refs": [{"source_id": "source", "part": "document.md", "content_sha256": "sha256:" + "a" * 64, "span": {"start": 0, "end": 1, "unit": "unicode_code_point"}}],
        "target_node_ids": ["p0"],
        "proposal": {"operation": "update_role", "property_scope": ["role"], "value": {"role": role}},
        "positive_evidence": [{"kind": evidence_kind}],
        "negative_evidence": [],
        "scope": {"stages": ["role"]},
        "constraints": [],
        "rationale": f"candidate {candidate_id}",
        "confidence": confidence,
    }


def _overlay_candidate(candidate_id: str, module_id: str, value: dict) -> dict:
    return {
        "candidate_id": candidate_id,
        "module_id": module_id,
        "module_version": "1.0",
        "target_source_refs": [{"source_id": "source", "part": "document.md", "content_sha256": "sha256:" + "a" * 64, "span": {"start": 0, "end": 1, "unit": "unicode_code_point"}}],
        "target_node_ids": ["p0"],
        "proposal": {
            "operation": "update_overlay",
            "property_scope": sorted(value),
            "value": value,
        },
        "positive_evidence": [{"kind": "format_contract"}],
        "negative_evidence": [],
        "scope": {"stages": ["relation"]},
        "constraints": [],
        "rationale": f"overlay candidate {candidate_id}",
        "confidence": 1.0,
    }


def test_resolver_is_independent_of_module_input_order_and_does_not_compare_raw_confidence() -> None:
    heading = _candidate("heading", "structure", "heading", evidence_kind="structural", confidence=0.60)
    note = _candidate("note", "notes", "note", evidence_kind="heuristic", confidence=0.99)

    first = resolve([heading, note], {"revision": 1})
    second = resolve([note, heading], {"revision": 1})

    assert first == second
    assert first["decisions"][0]["value"]["role"] == "heading"
    assert first["unresolved"] == []


def test_equal_strength_conflict_is_unresolved_with_explanatory_candidates() -> None:
    result = resolve(
        [_candidate("h1", "one", "heading", evidence_kind="structural"), _candidate("n1", "two", "note", evidence_kind="structural")],
        {"revision": 1},
    )

    assert result["decisions"] == []
    assert result["unresolved"][0]["code"] == "candidate_conflict"
    assert result["unresolved"][0]["candidate_ids"] == ["h1", "n1"]


def test_partial_overlay_write_set_conflict_is_resolved_per_property() -> None:
    result = resolve(
        [
            _overlay_candidate("content", "protected_content", {"protected": True, "reason": "date_or_version"}),
            _overlay_candidate("region", "source_regions", {"protected": False, "region_kind": "front_matter"}),
        ],
        {"revision": 1},
    )

    assert result["unresolved"] == [
        {
            "code": "candidate_conflict",
            "candidate_ids": ["content", "region"],
            "target_node_ids": ["p0"],
            "property_scope": ["protected"],
        }
    ]


def test_region_membership_and_content_protection_are_independent_claims(tmp_path: Path) -> None:
    source = tmp_path / "front-matter.md"
    source.write_text("V1.0\n\n# 正文标题\n", encoding="utf-8")
    facts = parse(source)
    regions = classify_regions(facts, front_matter_positions={1})
    first_node_id = facts["children"]["root"][0]

    result = resolve(
        [*collect_scope_candidates(facts, regions), *collect_protected_candidates(facts)],
        {"revision": 1},
    )
    target_decisions = [
        decision
        for decision in result["decisions"]
        if decision["target_node_ids"] == [first_node_id]
        and decision["operation"] == "update_overlay"
    ]

    assert result["unresolved"] == []
    assert {tuple(decision["property_scope"]) for decision in target_decisions} == {
        ("region_kind",),
        ("semantic_scope",),
        ("protected",),
        ("reason",),
    }


def test_protected_target_cannot_be_changed_by_a_candidate() -> None:
    result = resolve([_candidate("h1", "structure", "heading")], {"revision": 1, "protected_node_ids": ["p0"]})

    assert result["decisions"] == []
    assert result["unresolved"][0]["code"] == "protected_target"


def test_active_ledger_revision_is_prioritized_over_heuristics() -> None:
    ledger_view = {
        "revision": 4,
        "active_revisions": [
            {
                "revision_id": "r1",
                "transaction_id": "tx1",
                "target_node_ids": ["p0"],
                "property_scope": ["role"],
                "decision": {"operation": "update_role", "value": {"role": "note"}},
                "evidence": [{"kind": "user_override"}],
            }
        ],
    }

    result = resolve([_candidate("h1", "structure", "heading")], {"revision": 4}, ledger_view)

    assert result["decisions"][0]["revision_id"] == "r1"
    assert result["decisions"][0]["value"]["role"] == "note"
    assert result["unresolved"] == []


def test_table_semantics_and_caption_eligibility_keep_disjoint_write_sets(tmp_path: Path) -> None:
    source = tmp_path / "table.md"
    source.write_text("| 名称 | 说明 |\n| --- | --- |\n| SOC | 安全运营中心 |\n", encoding="utf-8")
    ast = build_source_ast(parse(source))
    table_id = next(
        node_id
        for node_id, block in ast["document"]["blocks"].items()
        if block.get("block_type") == "table"
    )

    result = resolve(
        [*collect_table_candidates(ast), *collect_caption_candidates(ast)],
        {"revision": ast["revision"]},
    )
    target_decisions = [
        decision
        for decision in result["decisions"]
        if decision["target_node_ids"] == [table_id]
        and decision["operation"] == "update_overlay"
    ]

    assert result["unresolved"] == []
    assert {tuple(decision["property_scope"]) for decision in target_decisions} == {
        ("table_type",),
        ("caption_eligible",),
        ("layout_contract",),
        ("caption_type",),
        ("eligible",),
    }
    resolved_overlay = {
        key: value
        for decision in target_decisions
        for key, value in decision["value"].items()
    }
    assert resolved_overlay["table_type"] == "data"
    assert resolved_overlay["caption_eligible"] is True
    assert resolved_overlay["caption_type"] == "table"
    assert resolved_overlay["eligible"] is True
