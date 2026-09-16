from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.model import build_indexes
from wxdf.transaction import (
    TransactionConflict,
    apply_stage_batch,
    make_snapshot,
)
from wxdf.validation import validate_ast


def _ref(source_id: str, start: int = 0, end: int = 1) -> dict:
    return {
        "source_id": source_id,
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": start, "end": end, "unit": "unicode_code_point"},
    }


def _paragraph(node_id: str, text: str, source_id: str | None = None) -> dict:
    source_id = source_id or node_id
    return {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
        "source_ref": _ref(source_id),
    }


def _ast(paragraph_count: int = 3) -> dict:
    blocks = {f"p{index}": _paragraph(f"p{index}", f"段落{index}") for index in range(paragraph_count)}
    return {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": blocks,
            "containers": {"root": {"kind": "document"}},
            "children": {"root": list(blocks)},
            "associations": [],
            "resources": {},
        },
    }


def test_indexes_are_derived_from_children_and_bound_to_revision() -> None:
    ast = _ast()

    indexes = build_indexes(ast)

    assert indexes.revision == 1
    assert indexes.parent_by_id["p0"] == "root"
    assert indexes.owner_item_by_id["p0"] is None
    assert indexes.section_by_id["p0"] is None
    assert indexes.semantic_depth["p0"] == 1


@pytest.mark.parametrize("paragraph_count", [100, 1000])
def test_transaction_applies_batch_once_and_rebuilds_index_once(paragraph_count: int) -> None:
    snapshot = make_snapshot(_ast(paragraph_count))
    decisions = [
        {
            "decision_id": f"d{index}",
            "operation": "update_role",
            "target_node_ids": [f"p{index}"],
            "expected_revision": 1,
            "property_scope": ["role"],
            "value": {"role": "note"},
        }
        for index in range(paragraph_count)
    ]

    result = apply_stage_batch(snapshot, decisions, expected_revision=1)

    assert result.status == "committed"
    assert result.snapshot.ast["revision"] == 2
    assert result.metrics["full_model_copy_count"] == 1
    assert result.metrics["full_graph_validation_count"] == 1
    assert result.metrics["index_rebuild_count"] == 1
    assert all(block["role"] == "note" for block in result.snapshot.ast["document"]["blocks"].values())


def test_transaction_failure_leaves_baseline_unchanged_and_version_conflict_is_explicit() -> None:
    ast = _ast()
    snapshot = make_snapshot(ast)
    baseline = copy.deepcopy(snapshot.ast)
    invalid = [{
        "decision_id": "bad",
        "operation": "update_role",
        "target_node_ids": ["missing"],
        "expected_revision": 1,
        "property_scope": ["role"],
        "value": {"role": "note"},
    }]

    with pytest.raises(TransactionConflict):
        apply_stage_batch(snapshot, invalid, expected_revision=1)
    assert snapshot.ast == baseline

    with pytest.raises(TransactionConflict, match="revision"):
        apply_stage_batch(snapshot, [], expected_revision=2)
