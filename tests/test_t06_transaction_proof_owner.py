"""T06 focused contracts: one owner for the transaction proof and apply semantics.

Before T06 the transaction proof (that a response, its transaction, the ledger
and the checkpoint describe the same commit) was assembled in four modules, and
``decision_ledger`` re-derived the apply semantics that ``transaction`` performs
in its ``_apply_*`` helpers.  After T06 ``decision_ledger`` owns the proof
construction and verification, and ``transaction.revision_applied`` is the
single pure predicate that reports whether a revision already matches a commit.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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


def _revision_decision() -> dict:
    return {
        "decision_id": "ledger-r1",
        "transaction_id": "tx-r1",
        "operation": "update_role",
        "value": {"role": "note"},
        "target_source_refs": [_ref()],
        "target_node_ids": ["p0"],
        "property_scope": ["role"],
        "expected_revision": 1,
    }


def _revision() -> dict:
    return {
        "revision_id": "r1",
        "decision": {"operation": "update_role", "value": {"role": "note"}},
        "target_node_ids": ["p0"],
        "property_scope": ["role"],
    }


def test_transaction_owns_the_pure_apply_predicate() -> None:
    from wxdf import decision_ledger, transaction

    assert callable(transaction.revision_applied)
    # The ledger must call the transaction predicate instead of re-deriving it.
    assert decision_ledger.revision_applied is transaction.revision_applied
    assert "_revision_applied" not in vars(decision_ledger)


def test_revision_applied_is_a_pure_predicate_over_apply_results() -> None:
    from wxdf.transaction import apply_stage_batch, make_snapshot, revision_applied

    snapshot = make_snapshot(_ast())
    revision = _revision()
    assert revision_applied(snapshot.ast, revision, snapshot) is False

    before = copy.deepcopy(snapshot.ast)
    result = apply_stage_batch(snapshot, [_revision_decision()], expected_revision=1)

    assert revision_applied(result.snapshot.ast, revision, result.snapshot) is True
    assert revision_applied(before, revision, None) is False
    # The predicate and the commit leave the caller snapshot untouched.
    assert snapshot.ast == before


def test_decision_ledger_owns_the_transaction_proof() -> None:
    from wxdf import decision_ledger

    assert isinstance(decision_ledger.APPLICATION_FIELDS, frozenset)
    assert callable(decision_ledger.response_transaction_binding)
    assert callable(decision_ledger.stored_transaction_digests)
    assert callable(decision_ledger.snapshot_transaction_state)
    assert callable(decision_ledger.verify_response_transaction_proof)


def test_checkpoint_and_response_stores_point_at_the_ledger_owner() -> None:
    from wxdf import checkpoint_store, decision_ledger, response_store

    assert not hasattr(checkpoint_store, "_response_transaction_binding")
    assert not hasattr(checkpoint_store, "_stored_transaction_digests")
    assert not hasattr(checkpoint_store, "_snapshot_transaction_state")
    assert not hasattr(response_store, "_APPLICATION_FIELDS")
    assert response_store.APPLICATION_FIELDS is decision_ledger.APPLICATION_FIELDS


def test_response_binding_and_snapshot_state_round_trip_through_the_owner() -> None:
    from wxdf import decision_ledger

    response = {
        "request_id": "role-0000",
        "response_sha256": "sha256:" + "b" * 64,
        "transaction_id": "tx-1",
        "transaction_content_sha256": "sha256:" + "c" * 64,
    }
    binding = decision_ledger.response_transaction_binding(
        response,
        "tx-1",
        "sha256:" + "c" * 64,
    )
    assert binding["request_id"] == "role-0000"
    assert binding["binding_sha256"] == decision_ledger.digest({
        "request_id": "role-0000",
        "response_sha256": "sha256:" + "b" * 64,
        "transaction_id": "tx-1",
        "transaction_content_sha256": "sha256:" + "c" * 64,
    })

    applied, digests = decision_ledger.snapshot_transaction_state({"tx-1": "sha256:" + "c" * 64})
    assert applied == [{"transaction_id": "tx-1", "content_sha256": "sha256:" + "c" * 64}]
    assert digests == {"tx-1": "sha256:" + "c" * 64}

    checkpoint = {"applied_transactions": applied, "transaction_content_digests": digests}
    stored, stored_applied = decision_ledger.stored_transaction_digests(checkpoint)
    assert stored == digests
    assert stored_applied == {"tx-1": "sha256:" + "c" * 64}
