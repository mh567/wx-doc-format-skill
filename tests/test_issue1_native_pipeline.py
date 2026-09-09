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
from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.legacy_capability import make_view, translate
from wxdf.registry import ModuleDescriptor, PlanError
from wxdf.resolver import resolve
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
    assert all(
        item["target_level"] == 0
        for item in normalized["presentation"]["nodes"].values()
        if item["marker_kind"] == "ordered"
    )


def test_legacy_list_patch_uses_standard_transaction_operations() -> None:
    ast = _ast(0)
    ast["document"]["containers"].update({
        "list0": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "seq0"},
        "item0": {
            "kind": "list_item",
            "legacy_id": "old-item",
            "source_marker": "a)",
            "source_ordinal": 1,
            "source_sequence_id": "seq0",
            "source_ref": {
                "source_id": "list",
                "part": "document.md",
                "content_sha256": "sha256:" + "a" * 64,
                "span": {"start": 0, "end": 2, "unit": "unicode_code_point"},
            },
        },
    })
    ast["document"]["blocks"]["li0"] = {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": "列表项", "marks": []}],
        "source_ref": ast["document"]["containers"]["item0"]["source_ref"],
    }
    ast["document"]["children"] = {"root": ["list0"], "list0": ["item0"], "item0": ["li0"]}
    bundle = make_view(ast)
    translated = translate(
        {"schema_version": "1.0", "decisions": [{"block_id": "old-item", "operation": "set_restart", "to": {"restart": True}}]},
        bundle.target_map,
        ast,
    )

    assert translated["candidates"][0]["proposal"]["operation"] == "set_presentation_override"
    decision_set = resolve(translated["candidates"], {"revision": ast["revision"]})
    committed = apply_stage_batch(make_snapshot(ast), decision_set["decisions"], expected_revision=ast["revision"])

    assert committed.status == "committed"
    assert committed.snapshot.presentation_overrides["item0"]["restart"] is True
    assert committed.snapshot.ast["document"] == ast["document"]
    assert committed.snapshot.ast["revision"] == ast["revision"] + 1


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
        ConversionKernel._native_preflight(_ast(0), "text", "wx-default-v2")


def test_explicit_legacy_profile_cannot_be_overridden_by_list_text(tmp_path: Path) -> None:
    source = tmp_path / "listed.md"
    source.write_text("1. 列表项\n", encoding="utf-8")
    output = tmp_path / "listed.docx"
    report = tmp_path / "report.json"
    command = [
        str(SCRIPTS / ".venv" / "bin" / "python3"),
        str(SCRIPTS / "main.py"),
        "--input", str(source),
        "--output", str(output),
        "--template", str(ROOT / "assets" / "wx_template.docx"),
        "--pipeline", "modular",
        "--modular-profile", "legacy-full-v1",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", "off",
        "--report", str(report),
    ]

    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    saved_report = json.loads(report.read_text(encoding="utf-8"))
    assert saved_report["migration_adapter"] == "legacy-full-v1"
    assert saved_report["execution_plan"]["profile"] == "wx_full_rules"
