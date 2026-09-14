from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.decision_ledger import (
    LedgerError,
    accept_decisions,
    project_ledger,
    replay,
    validate_revisions,
)
from wxdf.validation import validate_ast


def _ref() -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": 0, "end": 1, "unit": "unicode_code_point"},
    }


def _ast() -> dict:
    return {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p0": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "内容", "marks": []}],
                    "source_ref": _ref(),
                }
            },
            "containers": {"root": {"kind": "document"}},
            "children": {"root": ["p0"]},
            "associations": [],
            "resources": {},
        },
    }


def _revision(revision_id: str = "r1", *, role: str = "note", digest: str = "a" * 64, supersedes: list[str] | None = None) -> dict:
    ref = _ref()
    ref["content_sha256"] = "sha256:" + digest
    return {
        "revision_id": revision_id,
        "parent_revision": 1,
        "transaction_id": f"tx-{revision_id}",
        "target_source_refs": [ref],
        "target_node_ids": ["p0"],
        "property_scope": ["role"],
        "decision": {"operation": "update_role", "value": {"role": role}},
        "preconditions": [{"kind": "source_sha256", "value": "sha256:" + digest}],
        "evidence": [{"kind": "user_override"}],
        "origin": {"kind": "review"},
        "resolver_version": "1.0",
        "status": "active",
        "supersedes": supersedes or [],
    }


def _ledger(revisions: list[dict] | None = None) -> dict:
    return {"schema_version": "2.0", "revision": 1, "revisions": revisions or []}


def test_validate_and_replay_active_revision_through_source_ast() -> None:
    ast = _ast()
    ledger = _ledger([_revision()])

    validate_revisions({"source": {"content_sha256": "sha256:" + "a" * 64}}, ledger)
    replayed = replay({"source": {"content_sha256": "sha256:" + "a" * 64}}, ledger, ast)

    validate_ast(replayed)
    assert replayed["document"]["blocks"]["p0"]["role"] == "note"
    assert replayed["revision"] == 2


def test_source_change_projects_active_revision_to_invalidated_without_mutating_baseline() -> None:
    ledger = _ledger([_revision()])
    baseline = copy.deepcopy(ledger)

    projected = project_ledger({"source": {"content_sha256": "sha256:" + "b" * 64}}, ledger)

    assert ledger == baseline
    assert projected["revisions"][0]["status"] == "invalidated"
    assert projected["diagnostics"][0]["code"] == "revision_precondition_failed"
    with pytest.raises(LedgerError, match="invalidated"):
        replay({"source": {"content_sha256": "sha256:" + "b" * 64}}, ledger, _ast())


def test_supersedes_and_conflicting_active_revisions_are_explicit() -> None:
    ledger = _ledger([_revision("r1")])
    replacement = _revision("r2", role="heading", supersedes=["r1"])
    accepted = accept_decisions(ledger, [{**replacement, "status": "active"}])

    projected = project_ledger({"source": {"content_sha256": "sha256:" + "a" * 64}}, accepted)
    assert projected["active_revisions"][0]["revision_id"] == "r2"
    assert projected["revisions"][0]["status"] == "superseded"

    conflict = _ledger([_revision("r1"), _revision("r2", role="heading")])
    conflict_view = project_ledger({"source": {"content_sha256": "sha256:" + "a" * 64}}, conflict)
    assert {item["status"] for item in conflict_view["revisions"]} == {"conflicted"}
    with pytest.raises(LedgerError, match="conflicted"):
        replay({"source": {"content_sha256": "sha256:" + "a" * 64}}, conflict, _ast())


def test_invalid_revision_operation_is_rejected_without_polluting_ledger() -> None:
    ledger = _ledger()
    invalid = _revision()
    invalid["decision"] = {"operation": "execute_code", "value": {}}

    with pytest.raises(LedgerError, match="unsupported"):
        accept_decisions(ledger, [invalid])
    assert ledger == _ledger()


def test_update_node_content_revision_requires_a_non_empty_target() -> None:
    revision = _revision()
    revision["target_node_ids"] = []
    revision["property_scope"] = ["inline_content"]
    revision["decision"] = {
        "operation": "update_node_content",
        "value": {"inline_content": [{"kind": "text", "text": "新内容", "marks": []}]},
    }

    with pytest.raises(LedgerError, match="target_node_ids"):
        validate_revisions(
            {"source": {"content_sha256": "sha256:" + "a" * 64}},
            _ledger([revision]),
        )
