from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.contracts import (
    AST_SCHEMA_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    PATCH_SCHEMA_VERSION,
    SOURCE_FACTS_SCHEMA_VERSION,
    ContractError,
)
from wxdf.validation import (
    validate_ast,
    validate_checkpoint,
    validate_patch,
    validate_source_facts,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "docs" / "contracts" / "modular-v3"


def _source_ref(source_id: str, start: int = 0, end: int = 4) -> dict:
    return {
        "source_id": source_id,
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": start, "end": end, "unit": "unicode_code_point"},
    }


def _text(value: str) -> dict:
    return {"kind": "text", "text": value, "marks": []}


def _valid_source_facts() -> dict:
    return {
        "facts_schema_version": SOURCE_FACTS_SCHEMA_VERSION,
        "source": {
            "source_id": "source-1",
            "source_type": "markdown",
            "content_sha256": "sha256:" + "b" * 64,
            "encoding": "utf-8",
        },
        "parser": {
            "id": "markdown-parser",
            "version": "1.0.0",
            "dialect": "commonmark",
            "config_sha256": "sha256:" + "c" * 64,
        },
        "units": {
            "u1": {
                "kind": "paragraph",
                "source_ref": _source_ref("u1"),
                "inline_content": [_text("父项")],
            }
        },
        "containers": {"root": {"kind": "document"}},
        "children": {"root": ["u1"]},
        "evidence": {
            "u1": {
                "marker_kind": "ordered",
                "marker_text": "1.",
                "ordinal": 1,
                "explicit_depth": 0,
                "indent_value": 0,
                "indent_unit": "unicode_code_point",
                "numbering_definition_ref": None,
                "restart_hint": True,
                "blank_before": False,
                "continuation_hint": False,
            }
        },
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def _valid_ast() -> dict:
    return {
        "schema_version": AST_SCHEMA_VERSION,
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p1": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [_text("父项一")],
                    "source_ref": _source_ref("p1"),
                },
                "p2": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [_text("子项")],
                    "source_ref": _source_ref("p2", 5, 7),
                },
                "t1": {
                    "block_type": "table",
                    "table_type": "data",
                    "source_ref": _source_ref("t1", 8, 12),
                },
                "p3": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [_text("单元格内容")],
                    "source_ref": _source_ref("p3", 13, 18),
                },
                "p4": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [_text("父项一的续段")],
                    "source_ref": _source_ref("p4", 19, 25),
                },
                "p5": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [_text("父项二")],
                    "source_ref": _source_ref("p5", 26, 29),
                },
            },
            "containers": {
                "root": {"kind": "document"},
                "l1": {
                    "kind": "list",
                    "list_kind": "ordered",
                    "source_start": 1,
                    "source_sequence_id": "s1",
                },
                "i1": {
                    "kind": "list_item",
                    "source_marker": "1.",
                    "source_ordinal": 1,
                    "source_sequence_id": "s1",
                    "source_ref": _source_ref("i1", 0, 4),
                },
                "l2": {
                    "kind": "list",
                    "list_kind": "ordered",
                    "source_start": 1,
                    "source_sequence_id": "s2",
                },
                "i2": {
                    "kind": "list_item",
                    "source_marker": "1.",
                    "source_ordinal": 1,
                    "source_sequence_id": "s2",
                    "source_ref": _source_ref("i2", 5, 7),
                },
                "r1": {"kind": "table_row"},
                "c1": {
                    "kind": "table_cell",
                    "row": 0,
                    "col": 0,
                    "row_span": 1,
                    "col_span": 1,
                },
                "i3": {
                    "kind": "list_item",
                    "source_marker": "2.",
                    "source_ordinal": 2,
                    "source_sequence_id": "s1",
                    "source_ref": _source_ref("i3", 26, 29),
                },
            },
            "children": {
                "root": ["l1"],
                "l1": ["i1", "i3"],
                "i1": ["p1", "l2", "t1", "p4"],
                "l2": ["i2"],
                "i2": ["p2"],
                "t1": ["r1"],
                "r1": ["c1"],
                "c1": ["p3"],
                "i3": ["p5"],
            },
            "associations": [],
            "resources": {},
        },
    }


def _valid_patch() -> dict:
    return {
        "patch_schema_version": PATCH_SCHEMA_VERSION,
        "patch_id": "patch-1",
        "base_revision": 1,
        "phase": "role",
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "module_id": "document-structure",
                "module_version": "1.0.0",
                "target_source_refs": [_source_ref("p1")],
                "target_node_ids": ["p1"],
                "proposal": {"role": "heading", "level": 2},
                "positive_evidence": [{"kind": "style", "value": "Heading 2"}],
                "negative_evidence": [],
                "scope": {"stages": ["role", "relation"]},
                "constraints": [{"kind": "requires_source_ref"}],
                "rationale": "标题样式与相邻结构一致",
                "confidence": 0.95,
            }
        ],
        "decisions": [
            {
                "decision_id": "decision-1",
                "candidate_id": "candidate-1",
                "operation": "update_role",
                "target_node_ids": ["p1"],
                "expected_revision": 1,
                "property_scope": ["role", "level"],
                "value": {"role": "heading", "level": 2},
                "preconditions": [{"kind": "node_exists", "node_id": "p1"}],
                "evidence": [{"kind": "style", "value": "Heading 2"}],
                "invalidates": ["relation", "normalize", "render", "audit"],
            }
        ],
    }


def _valid_checkpoint() -> dict:
    return {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": "run-1",
        "status": "waiting_external",
        "resume_stage": "role",
        "source_facts": _valid_source_facts(),
        "decision_ledger": {"schema_version": "1.0", "revision": 1, "revisions": []},
        "source_ast": _valid_ast(),
        "normalized_ast": None,
        "content_overlay": [],
        "presentation_override": [],
        "target_map": {"revision": 1, "entries": {"p1": {"node_id": "p1", "source_refs": [_source_ref("p1")]}}, "view_sha256": "sha256:" + "d" * 64},
        "resource_summary": {"resources": [], "source_sha256": "sha256:" + "b" * 64},
        "contracts": {"source_facts": "1.0", "ast": "2.0", "patch": "2.0", "checkpoint": "2.0"},
        "modules": [{"id": "document-structure", "version": "1.0.0"}],
        "requests": [],
        "responses": [],
        "applied_transactions": [],
        "transaction_content_digests": {},
        "budget": {"limit": 3, "consumed": 1, "waiting_seconds": 12.5},
        "round": 0,
        "candidate_baseline_id": "baseline-1",
    }


def _assert_error(call, *, code: str, path: str | None = None) -> None:
    with pytest.raises(ContractError) as caught:
        call()
    assert caught.value.code == code
    if path is not None:
        assert caught.value.path == path


def test_valid_contract_fixtures_pass_all_validators() -> None:
    assert validate_source_facts(_valid_source_facts()) is None
    assert validate_ast(_valid_ast()) is None
    assert validate_patch(_valid_patch()) is None
    assert validate_checkpoint(_valid_checkpoint()) is None


def test_checkpoint_accepts_a_complete_normalized_snapshot_and_legacy_ast_shape() -> None:
    from wxdf.normalization import normalize

    normalized = normalize(
        _valid_ast(),
        {
            "resolved_styles": {
                "list_letter": "List Number",
                "list_decimal": "List Number 2",
                "list_dash": "List Bullet",
                "list_bullet": "List Bullet 2",
            },
            "numbering_ids": {},
        },
        strategy="wx-default-v2",
    )
    checkpoint = _valid_checkpoint()
    checkpoint["normalized_ast"] = normalized

    assert validate_checkpoint(checkpoint) is None

    checkpoint["normalized_ast"] = _valid_ast()
    assert validate_checkpoint(checkpoint) is None

    schema = json.loads((CONTRACT_DIR / "checkpoint.schema.json").read_text(encoding="utf-8"))
    normalized_contract = schema["properties"]["normalized_ast"]
    assert any(
        candidate.get("properties", {}).get("schema_version", {}).get("const") == "1.0"
        and "semantic_digest" in candidate.get("required", [])
        and "presentation_digest" in candidate.get("required", [])
        for candidate in normalized_contract["anyOf"]
    )


def test_source_facts_require_explicit_offset_units_and_source_refs() -> None:
    facts = _valid_source_facts()
    facts["units"]["u1"]["source_ref"]["span"]["unit"] = "twips"
    _assert_error(lambda: validate_source_facts(facts), code="invalid_offset_unit", path="units.u1.source_ref.span.unit")

    facts = _valid_source_facts()
    del facts["units"]["u1"]["source_ref"]
    _assert_error(lambda: validate_source_facts(facts), code="missing_source_ref", path="units.u1.source_ref")


def test_ast_rejects_dangling_refs_double_parents_and_cycles() -> None:
    ast = _valid_ast()
    ast["document"]["children"]["i3"].append("p1")
    _assert_error(lambda: validate_ast(ast), code="multiple_parents", path="document.children.i3[1]")

    ast = _valid_ast()
    ast["document"]["children"]["i3"] = ["missing"]
    _assert_error(lambda: validate_ast(ast), code="unknown_child", path="document.children.i3[0]")

    ast = _valid_ast()
    ast["document"]["children"]["i3"] = ["l1"]
    _assert_error(lambda: validate_ast(ast), code="cycle", path="document.children")


def test_ast_enforces_role_fields_inline_links_resources_and_table_spans() -> None:
    ast = _valid_ast()
    ast["document"]["blocks"]["p1"]["role"] = "heading"
    _assert_error(lambda: validate_ast(ast), code="missing_heading_level", path="document.blocks.p1.level")

    ast = _valid_ast()
    ast["document"]["blocks"]["p1"]["inline_content"] = [{"kind": "link", "target": "https://example.org", "children": [{"kind": "link", "target": "https://nested", "children": [_text("x")]}]}]
    _assert_error(lambda: validate_ast(ast), code="nested_link", path="document.blocks.p1.inline_content[0].children[0]")

    ast = _valid_ast()
    ast["document"]["blocks"]["p1"]["inline_content"] = [{"kind": "image", "resource_id": "missing", "alt_text": "图"}]
    _assert_error(lambda: validate_ast(ast), code="missing_resource", path="document.blocks.p1.inline_content[0].resource_id")

    ast = _valid_ast()
    ast["document"]["containers"]["c1"]["col_span"] = 2
    ast["document"]["containers"]["c1"]["row_span"] = 2
    ast["document"]["containers"]["r1"].setdefault("extra", True)
    _assert_error(lambda: validate_ast(ast), code="table_span_overlap", path="document.containers.c1")


def test_patch_requires_versioned_candidates_and_restricted_decisions() -> None:
    patch = _valid_patch()
    patch["decisions"][0]["operation"] = "write_xml"
    _assert_error(lambda: validate_patch(patch), code="unsupported_operation", path="decisions[0].operation")

    patch = _valid_patch()
    patch["candidates"][0]["target_node_ids"] = []
    _assert_error(lambda: validate_patch(patch), code="empty_target", path="candidates[0].target_node_ids")

    patch = _valid_patch()
    patch["decisions"][0]["property_scope"] = ["document.xml"]
    _assert_error(lambda: validate_patch(patch), code="forbidden_property_scope", path="decisions[0].property_scope[0]")


def test_checkpoint_is_complete_and_json_serializable() -> None:
    checkpoint = _valid_checkpoint()
    assert json.loads(json.dumps(checkpoint, ensure_ascii=False)) == checkpoint

    checkpoint = _valid_checkpoint()
    del checkpoint["source_ast"]
    _assert_error(lambda: validate_checkpoint(checkpoint), code="missing_checkpoint_field", path="source_ast")

    checkpoint = _valid_checkpoint()
    checkpoint["status"] = "completed"
    checkpoint["resume_stage"] = "role"
    _assert_error(lambda: validate_checkpoint(checkpoint), code="invalid_resume_state", path="resume_stage")


def test_contract_json_schemas_pin_versions_and_role_requirements() -> None:
    expected = {
        "source-facts.schema.json": "1.0",
        "ast.schema.json": "2.0",
        "patch.schema.json": "2.0",
        "checkpoint.schema.json": "2.0",
    }
    for filename, version in expected.items():
        schema = json.loads((CONTRACT_DIR / filename).read_text(encoding="utf-8"))
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert version in schema["$id"]
        assert "required" in schema
