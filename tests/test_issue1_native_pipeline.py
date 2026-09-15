from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_ast import _ast
from wxdf.semantic_modules import native_semantic_modules
from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.parsers import parse
from wxdf.registry import ModuleDescriptor, PlanError, build_native_v2_registry, native_preflight
from wxdf.resolver import resolve
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics
from wxdf.transaction import apply_stage_batch, make_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _native_request(source: Path, tmp_path: Path) -> ConversionRequest:
    return ConversionRequest(
        input_path=source,
        output_path=tmp_path / "output.docx",
        template_path=ROOT / "assets" / "wx_template.docx",
        pipeline="modular",
        list_policy="wx-default-v2",
        options={
            "profile": "native_v2",
            "source_ast_path": tmp_path / "source.json",
            "normalized_ast_path": tmp_path / "normalized.json",
            "report_path": tmp_path / "report.json",
        },
    )


def test_native_modular_path_commits_source_hierarchy_before_flattening(tmp_path: Path) -> None:
    source = tmp_path / "nested.md"
    source.write_text("# 标题\n\n1. 父项\n   1. 子项\n2. 第二项\n", encoding="utf-8")

    result = ConversionKernel().convert(_native_request(source, tmp_path))

    assert result.status == "completed", result.diagnostics
    source_ast = json.loads((tmp_path / "source.json").read_text(encoding="utf-8"))
    normalized = json.loads((tmp_path / "normalized.json").read_text(encoding="utf-8"))
    document = source_ast["document"]
    list_items = {
        node_id: node
        for node_id, node in document["containers"].items()
        if node.get("kind") == "list_item"
    }
    nested_lists = {
        node_id
        for node_id, node in document["containers"].items()
        if node.get("kind") == "list"
        and any(
            child in list_items
            for child in document["children"].get(node_id, [])
        )
    }
    assert len(nested_lists) == 2
    assert any(
        any(document["containers"].get(child, {}).get("kind") == "list" for child in document["children"].get(item_id, []))
        for item_id in list_items
    )
    assert result.report["native_transaction"]["status"] == "committed"
    assert result.report["native_transaction"]["decision_count"] > 0
    assert result.report["capability_migration"]["list_semantics"] == "consumed"
    # T08: the source hierarchy survives normalization.  The nested ordered child
    # keeps its level instead of every ordered item being pinned to level 0.
    assert sorted(
        item["target_level"]
        for item in normalized["presentation"]["nodes"].values()
        if item["marker_kind"] == "ordered"
    ) == [0, 0, 1]


def _list_facts(*, continuation: bool = False, empty_item: bool = False) -> dict:
    units = {} if empty_item else {
        "p1": {
            "kind": "paragraph",
            "source_ref": {"source_id": "source", "part": "document.md", "content_sha256": "sha256:" + "a" * 64, "span": {"start": 0, "end": 1, "unit": "unicode_code_point"}},
            "inline_content": [{"kind": "text", "text": "第一项", "marks": []}],
        },
    }
    children = {"root": ["list"], "list": ["item"], "item": [] if empty_item else ["p1"]}
    evidence = {"item": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0}}
    if continuation:
        units["p1b"] = {
            "kind": "paragraph",
            "source_ref": {"source_id": "source", "part": "document.md", "content_sha256": "sha256:" + "a" * 64, "span": {"start": 1, "end": 2, "unit": "unicode_code_point"}},
            "inline_content": [{"kind": "text", "text": "续段", "marks": []}],
        }
        children["item"].append("p1b")
        evidence["p1b"] = {"continuation_hint": True}
    return {
        "facts_schema_version": "1.0",
        "source": {"source_id": "source", "source_type": "markdown", "content_sha256": "sha256:" + "a" * 64},
        "parser": {"id": "test", "version": "1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": units,
        "containers": {
            "root": {"kind": "document"},
            "list": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "seq0"},
            "item": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "seq0"},
        },
        "children": children,
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def test_list_continuation_is_materialized_as_an_association_transaction() -> None:
    facts = _list_facts(continuation=True)
    source_ast = build_source_ast(facts)
    list_result = analyze_lists(facts)

    materialized, candidates, _ = materialize_list_semantics(source_ast, facts, list_result)
    continuation_candidates = [
        candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "update_association"
        and candidate["proposal"]["value"]["association"]["kind"] == "sequence_continuation"
    ]

    assert len(continuation_candidates) == 1
    assert continuation_candidates[0]["proposal"]["value"]["association"] == {
        "kind": "sequence_continuation",
        "source_id": "item",
        "target_id": "p1b",
    }
    decision_set = resolve(candidates, {"revision": source_ast["revision"]})
    committed = apply_stage_batch(make_snapshot(materialized), decision_set["decisions"], expected_revision=source_ast["revision"])

    assert committed.snapshot.ast["document"]["associations"] == [{
        "kind": "sequence_continuation",
        "source_id": "item",
        "target_id": "p1b",
    }]


def test_generated_list_anchor_is_created_by_the_transaction() -> None:
    facts = _list_facts(empty_item=True)
    source_ast = build_source_ast(facts)
    list_result = analyze_lists(facts)

    materialized, candidates, _ = materialize_list_semantics(source_ast, facts, list_result)
    anchor_candidates = [
        candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "create_node"
        and candidate["proposal"]["value"]["node"].get("generated_by") == "list_semantics.marker_anchor"
    ]

    assert len(anchor_candidates) == 1
    assert anchor_candidates[0]["proposal"]["value"]["parent_id"] == "item"
    decision_set = resolve(candidates, {"revision": source_ast["revision"]})
    committed = apply_stage_batch(make_snapshot(materialized), decision_set["decisions"], expected_revision=source_ast["revision"])
    anchor_id = anchor_candidates[0]["proposal"]["value"]["node_id"]

    assert committed.snapshot.ast["document"]["children"]["item"] == [anchor_id]
    assert committed.snapshot.ast["document"]["blocks"][anchor_id]["generation_basis"] == ["item", "empty_list_item"]


def test_module_descriptor_preflight_declares_contract_and_audits() -> None:
    descriptor = ModuleDescriptor(
        "lists",
        "1.0",
        ("relation",),
        ("source_facts",),
        ("list_candidates",),
        contract_version="2.0",
        supported_source_types=("markdown", "docx"),
        supported_node_kinds=("list", "list_item"),
        failure_policy="fail",
        required_audits=("saved_output_audit",),
    )

    assert descriptor.contract_version == "2.0"
    assert descriptor.required_audits == ("saved_output_audit",)


def test_native_preflight_rejects_text_source_type() -> None:
    with pytest.raises(PlanError, match="does not support source type text"):
        native_preflight(_ast(0), "text", "wx-default-v2")


def test_native_v2_preflight_builds_plan_from_registered_modules() -> None:
    registry = build_native_v2_registry()
    plan = native_preflight(_ast(0), "markdown", "wx-default-v2")

    assert [module.module_id for module in plan.modules] == [
        "source-facts-parser",
        "native-source-parser",
        "source-region",
        "document-structure",
        "protected-content",
        "list-semantics",
        "table-semantics",
        "caption-semantics",
        "appendix-semantics",
        "native-candidate-set",
        "native-resolver",
        "native-transaction",
        "native-normalizer",
        "native-renderer",
        "native-auditor",
        "native-reviewer",
        "native-publisher",
    ]
    assert [module.module_id for module in registry.build({"pipeline": "modular", "list_policy": "wx-default-v2", "source_type": "markdown", "node_kinds": ["document"], "available_audits": ["model_audit", "saved_output_audit"]}).modules] == [module.module_id for module in plan.modules]
    assert all(module.failure_policy == "fail" for module in plan.modules)
    assert plan.modules[-1].required_audits == ()


def test_native_v2_default_factory_has_a_single_full_chain_shape() -> None:
    """The default factory call returns the complete native chain once."""

    config = {
        "pipeline": "modular",
        "list_policy": "wx-default-v2",
        "source_type": "markdown",
        "available_audits": ("model_audit", "saved_output_audit"),
    }
    default_plan = [module.module_id for module in build_native_v2_registry().build(config).modules]
    injected_plan = [
        module.module_id
        for module in build_native_v2_registry(semantic_modules=native_semantic_modules()).build(config).modules
    ]

    assert default_plan == injected_plan
    assert "native-candidate-set" in default_plan
    assert "source-facts-parser" in default_plan
    assert len(default_plan) == 17


def test_native_fail_policy_blocks_unresolved_decisions_before_publish() -> None:
    from wxdf.native_stages import _plan_blocks_publication
    from wxdf.semantic_modules import ModuleExecutionContext

    plan = native_preflight(_ast(0), "markdown", "wx-default-v2")
    context = ModuleExecutionContext(request=None, services={"execution_plan": plan})

    assert _plan_blocks_publication(context, {"unresolved": [{"code": "candidate_conflict"}]}) is True
    assert _plan_blocks_publication(context, {"unresolved": []}) is False


def test_native_unresolved_decisions_do_not_create_a_published_docx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "blocked.md"
    source.write_text("正文\n", encoding="utf-8")

    def unresolved(*_args: object, **_kwargs: object) -> dict:
        return {"decisions": [], "unresolved": [{"code": "candidate_conflict", "candidate_ids": ["c1"]}]}

    monkeypatch.setattr("wxdf.resolver.resolve", unresolved)
    request = _native_request(source, tmp_path)
    result = ConversionKernel().convert(request)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.diagnostics[0]["code"] == "candidate_conflict"
    assert not request.output_path.exists()


def test_native_audit_failure_does_not_create_a_published_docx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "audit-failure.md"
    source.write_text("正文\n", encoding="utf-8")

    monkeypatch.setattr("wxdf.auditing.check_model", lambda *_args, **_kwargs: {"passed": False, "issues": [{"code": "model_audit_failed"}]})
    request = _native_request(source, tmp_path)
    result = ConversionKernel().convert(request)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.diagnostics[0]["code"] == "model_audit_failed"
    assert not request.output_path.exists()


def test_list_materialization_emits_structure_candidates_without_mutating_source_ast(tmp_path: Path) -> None:
    source = tmp_path / "nested.md"
    source.write_text("1. 父项\n2. 第二项\n", encoding="utf-8")
    facts = parse(source)
    source_list_id = next(
        container_id
        for container_id, container in facts["containers"].items()
        if container.get("kind") == "list"
    )
    item_ids = list(facts["children"][source_list_id])
    unit_ids = [facts["children"][item_id][0] for item_id in item_ids]
    for item_id, unit_id in zip(item_ids, unit_ids):
        item = facts["containers"][item_id]
        facts["evidence"][unit_id] = {
            "marker_kind": "ordered",
            "marker_text": item["source_marker"],
            "ordinal": item["source_ordinal"],
            "explicit_depth": 0,
        }
    facts["containers"] = {"root": facts["containers"]["root"]}
    facts["children"] = {"root": unit_ids}
    facts["evidence"] = {unit_id: facts["evidence"][unit_id] for unit_id in unit_ids}
    source_ast = build_source_ast(facts)
    baseline = json.loads(json.dumps(source_ast, ensure_ascii=False))

    materialized, candidates, _ = materialize_list_semantics(
        source_ast,
        facts,
        analyze_lists(facts),
    )

    assert materialized == baseline
    assert any(candidate["proposal"]["operation"] == "create_node" for candidate in candidates)
    assert any(
        candidate["proposal"]["value"]["node"].get("kind") == "list_item"
        for candidate in candidates
        if candidate["proposal"]["operation"] == "create_node"
    )
    decision_set = resolve(candidates, {"revision": source_ast["revision"]})
    committed = apply_stage_batch(
        make_snapshot(materialized),
        decision_set["decisions"],
        expected_revision=source_ast["revision"],
    )

    assert committed.status == "committed"
    committed_document = committed.snapshot.ast["document"]
    assert any(node.get("kind") == "list" for node in committed_document["containers"].values())
    assert any(node.get("kind") == "list_item" for node in committed_document["containers"].values())


def test_transaction_orders_created_list_parent_before_child() -> None:
    ast = _ast(0)
    decisions = [
        {
            "decision_id": "create-item",
            "transaction_id": "tx-parent-child",
            "operation": "create_node",
            "target_node_ids": ["item0"],
            "expected_revision": ast["revision"],
            "property_scope": ["children"],
            "value": {
                "node_id": "item0",
                "parent_id": "list0",
                "index": 0,
                "node": {
                    "kind": "list_item",
                    "source_marker": "1.",
                    "source_ordinal": 1,
                    "source_sequence_id": "seq0",
                },
            },
        },
        {
            "decision_id": "create-list",
            "transaction_id": "tx-parent-child",
            "operation": "create_node",
            "target_node_ids": ["list0"],
            "expected_revision": ast["revision"],
            "property_scope": ["children"],
            "value": {
                "node_id": "list0",
                "parent_id": "root",
                "index": 0,
                "node": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "seq0"},
            },
        },
    ]

    committed = apply_stage_batch(make_snapshot(ast), decisions, expected_revision=ast["revision"])

    assert committed.status == "committed"
    assert committed.snapshot.ast["document"]["children"]["root"] == ["list0"]
    assert committed.snapshot.ast["document"]["children"]["list0"] == ["item0"]
