from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.migration import MigrationError, legacy_view, migrate_legacy_model
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


def test_migration_splits_legacy_list_items_into_containers_and_paragraphs() -> None:
    legacy = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "b1", "block_type": "list_item", "text": "父项", "level": 0, "list_type": "lower_letter_paren", "restart": True, "source": {"source_position": 0}},
                {"id": "b2", "block_type": "list_item", "text": "子项", "level": 1, "list_type": "decimal_paren", "restart": True, "source": {"source_position": 1}},
                {"id": "b3", "block_type": "body", "text": "正文", "source": {"source_position": 2}},
            ]
        },
    }

    migrated = migrate_legacy_model(legacy)

    validate_ast(migrated.ast)
    assert migrated.ast["revision"] == 1
    assert any(container.get("kind") == "list_item" for container in migrated.ast["document"]["containers"].values())
    assert any(block.get("inline_content") for block in migrated.ast["document"]["blocks"].values())
    assert migrated.diagnostics == []


def test_migration_keeps_missing_parent_as_unresolved_without_inventing_parent() -> None:
    legacy = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "b1", "block_type": "list_item", "text": "孤立深层项", "level": 2, "list_type": "decimal_paren", "restart": True, "source": {"source_position": 0}},
            ]
        },
    }

    migrated = migrate_legacy_model(legacy)

    assert migrated.diagnostics[0]["code"] == "unresolved_list_parent"
    assert not any(block.get("text") == "虚构父项" for block in migrated.ast["document"]["blocks"].values())
    validate_ast(migrated.ast)


def test_migration_preserves_title_table_headers_and_generated_caption_relation() -> None:
    legacy = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "title", "block_type": "heading", "role": "title", "level": 0, "text": "测试报告"},
                {
                    "id": "caption-1",
                    "block_type": "caption",
                    "caption_type": "table",
                    "text": "",
                    "_auto_generated": True,
                    "association": {"object_id": "table-1"},
                },
                {
                    "id": "table-1",
                    "block_type": "table",
                    "table_type": "data",
                    "header_rows": 1,
                    "rows": [
                        [{"text": "名称", "cell_role": "header"}, {"text": "值", "cell_role": "header"}],
                        [{"text": "A"}, {"text": "1"}],
                    ],
                },
            ]
        },
    }

    migrated = migrate_legacy_model(legacy).ast

    validate_ast(migrated)
    blocks = migrated["document"]["blocks"]
    containers = migrated["document"]["containers"]
    assert blocks["title"]["role"] == "title"
    assert "level" not in blocks["title"]
    assert blocks["table-1"]["header_rows"] == 1
    assert containers["table-1-row-0-cell-0"]["cell_role"] == "header"
    assert containers["table-1-row-1-cell-0"]["cell_role"] == "body"
    assert blocks["caption-1"]["target_id"] == "table-1"
    assert blocks["caption-1"]["generated_by"] == "legacy_normalization_adapter"
    assert migrated["document"]["associations"] == [
        {"kind": "caption_target", "source_id": "caption-1", "target_id": "table-1"}
    ]


@pytest.mark.parametrize(
    ("legacy_value", "expected"),
    [("资料性", "informative"), ("规范性", "normative")],
)
def test_migration_maps_legacy_chinese_appendix_classification_to_ast_enum(
    legacy_value: str,
    expected: str,
) -> None:
    legacy = {
        "schema_version": "1.0",
        "document": {
            "blocks": [{
                "id": "appendix-a",
                "block_type": "appendix",
                "appendix_id": "A",
                "title": "附录示例",
                "classification": legacy_value,
                "source": {"source_position": 12},
            }]
        },
    }

    migrated = migrate_legacy_model(legacy)

    appendix = migrated.ast["document"]["containers"]["appendix-a"]
    assert appendix["source_classification"] == expected
    assert appendix["source_classification_label"] == legacy_value
    assert migrated.diagnostics == []
    validate_ast(migrated.ast)


def test_migration_downgrades_unmapped_appendix_classification_with_diagnostic() -> None:
    legacy = {
        "schema_version": "1.0",
        "document": {
            "blocks": [{
                "id": "appendix-a",
                "block_type": "appendix",
                "title": "附录示例",
                "classification": "内部参考",
            }]
        },
    }

    migrated = migrate_legacy_model(legacy)

    appendix = migrated.ast["document"]["containers"]["appendix-a"]
    assert appendix["source_classification"] == "unknown"
    assert appendix["source_classification_label"] == "内部参考"
    assert migrated.diagnostics == [{
        "code": "legacy_appendix_classification_unmapped",
        "severity": "warning",
        "message": "legacy appendix classification is outside AST 2.0 enum",
        "node_id": "appendix-a",
        "value": "内部参考",
    }]
    validate_ast(migrated.ast)


def test_migration_nests_explicit_appendix_context_under_appendix_container() -> None:
    legacy = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {
                    "id": "appendix-a",
                    "block_type": "appendix",
                    "appendix_id": "A",
                    "title": "附录示例",
                    "context": {"scope": "appendix", "appendix_id": "A"},
                },
                {
                    "id": "appendix-heading-1",
                    "block_type": "heading",
                    "role": "appendix_heading",
                    "level": 1,
                    "text": "附录一级标题",
                    "appendix_id": "A",
                    "context": {"scope": "appendix", "appendix_id": "A"},
                },
                {
                    "id": "appendix-body",
                    "block_type": "body",
                    "text": "附录正文",
                    "appendix_id": "A",
                    "context": {"scope": "appendix", "appendix_id": "A"},
                },
            ]
        },
    }

    migrated = migrate_legacy_model(legacy).ast

    assert migrated["document"]["children"]["root"] == ["appendix-a"]
    assert migrated["document"]["children"]["appendix-a"] == [
        "appendix-a-title",
        "appendix-heading-1",
        "appendix-body",
    ]
    validate_ast(migrated)


def test_legacy_view_is_read_only_and_rejects_unrepresentable_inline_nodes() -> None:
    ast = _ast()
    view = legacy_view(ast)

    with pytest.raises(TypeError):
        view["blocks"]["p0"]["text"] = "修改"

    ast["document"]["blocks"]["p0"]["inline_content"] = [{"kind": "opaque_inline", "diagnostic": "unsupported field", "source_ref": _ref("p0")}]
    with pytest.raises(MigrationError, match="opaque_inline"):
        legacy_view(ast)


def test_legacy_view_preserves_href_in_inline_runs() -> None:
    ast = _ast(1)
    ast["document"]["blocks"]["p0"]["inline_content"] = [
        {"kind": "link", "target": "https://example.org", "children": [{"kind": "text", "text": "说明", "marks": ["strong"]}]}
    ]

    view = legacy_view(ast)

    assert view["blocks"]["p0"]["text"] == "说明"
    assert view["blocks"]["p0"]["inline_runs"][0]["href"] == "https://example.org"


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
