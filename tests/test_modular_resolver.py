from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.resolver import resolve


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
