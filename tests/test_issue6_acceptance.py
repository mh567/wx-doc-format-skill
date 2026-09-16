from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from docx import Document

from modular_support import _append_numbering_definition, _set_numbering


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.semantics.lists import analyze_lists


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
TEMPLATE = ROOT / "assets" / "wx_template.docx"


def _build_switch_docx(tmp_path: Path) -> Path:
    document = Document()
    _append_numbering_definition(
        document,
        abstract_id=1001,
        num_id=1001,
        levels=[("decimal", "%1.")],
    )
    _append_numbering_definition(
        document,
        abstract_id=1002,
        num_id=1002,
        levels=[("bullet", "•")],
    )
    _append_numbering_definition(
        document,
        abstract_id=1003,
        num_id=1003,
        levels=[("decimal", "%1.")],
    )
    for text, num_id in (
        ("有序组一", 1001),
        ("有序组二", 1001),
        ("无序组一", 1002),
        ("无序组二", 1002),
        ("有序组回归一", 1003),
        ("有序组回归二", 1003),
    ):
        paragraph = document.add_paragraph(text)
        _set_numbering(paragraph, num_id=num_id, level=0)
    output = tmp_path / "same-level-switch.docx"
    document.save(output)
    return output


def _build_missing_definition_docx(tmp_path: Path) -> Path:
    document = Document()
    for text in ("缺失定义项一", "缺失定义项二"):
        paragraph = document.add_paragraph(text)
        _set_numbering(paragraph, num_id=1777, level=0)
    output = tmp_path / "missing-numbering-definition.docx"
    document.save(output)
    return output


def _build_nested_missing_definition_docx(tmp_path: Path) -> Path:
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    first = cell.paragraphs[0]
    first.text = "嵌套缺失定义一"
    _set_numbering(first, num_id=1778, level=0)
    second = cell.add_paragraph("嵌套缺失定义二")
    _set_numbering(second, num_id=1778, level=0)
    output = tmp_path / "nested-missing-numbering-definition.docx"
    document.save(output)
    return output


def _run_native(source: Path, tmp_path: Path, name: str) -> tuple[dict, dict, Path]:
    output = tmp_path / f"{name}.output.docx"
    report_path = tmp_path / f"{name}.report.json"
    source_ast_path = tmp_path / f"{name}.source.json"
    completed = subprocess.run(
        [
            str(PYTHON),
            str(ROOT / "scripts" / "main.py"),
            "--input", str(source),
            "--output", str(output),
            "--template", str(TEMPLATE),
            "--llm-enhance", "off",
            "--report", str(report_path),
            "--source-ast", str(source_ast_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    return (
        json.loads(report_path.read_text(encoding="utf-8")),
        json.loads(source_ast_path.read_text(encoding="utf-8")),
        output,
    )


def test_native_docx_same_level_family_switch_creates_explicit_sibling_groups(tmp_path: Path) -> None:
    report, source_ast, _ = _run_native(_build_switch_docx(tmp_path), tmp_path, "switch")

    lists = [
        (node_id, node)
        for node_id, node in source_ast["document"]["containers"].items()
        if node.get("kind") == "list"
    ]
    by_id = dict(lists)
    ordered_ids = []
    current_id = next(
        node_id
        for node_id, node in lists
        if node.get("group_boundary", {}).get("kind") == "run_start"
    )
    while current_id in by_id:
        ordered_ids.append(current_id)
        next_id = next(
            (
                node_id
                for node_id, node in lists
                if node.get("group_boundary", {}).get("previous_list_id") == current_id
            ),
            None,
        )
        if next_id is None:
            break
        current_id = next_id
    lists = [(node_id, by_id[node_id]) for node_id in ordered_ids]
    assert [node["list_kind"] for _, node in lists] == ["ordered", "unordered", "ordered"]
    boundaries = [node.get("group_boundary") for _, node in lists]
    assert boundaries[1] == {
        "kind": "same_level_list_kind_switch",
        "depth": 0,
        "from": "ordered",
        "to": "unordered",
        "previous_list_id": lists[0][0],
    }
    assert boundaries[2] == {
        "kind": "same_level_list_kind_switch",
        "depth": 0,
        "from": "unordered",
        "to": "ordered",
        "previous_list_id": lists[1][0],
    }
    assert report["list_semantics"]["item_count"] == 6
    assert report["list_semantics"]["diagnostics"] == []
    assert report["list_semantics_consumption"]["item_consumed_count"] == 6


def test_native_docx_missing_numbering_definition_is_explicitly_downgraded_to_body(tmp_path: Path) -> None:
    report, source_ast, _ = _run_native(_build_missing_definition_docx(tmp_path), tmp_path, "missing")

    diagnostics = report["list_semantics"]["diagnostics"]
    assert len(diagnostics) == 2
    assert {item["code"] for item in diagnostics} == {"missing_numbering_definition"}
    assert all(item["severity"] == "info" for item in diagnostics)
    assert all(item["fallback"]["kind"] == "body_paragraph" for item in diagnostics)
    assert all(item["source_evidence"]["explicit_depth"] == 0 for item in diagnostics)
    assert all(item["source_evidence"]["ordinal"] == 1 for item in diagnostics)
    assert all(item["source_evidence"]["indent_unit"] == "twips" for item in diagnostics)
    assert {item["source_evidence"]["restart_hint"] for item in diagnostics} == {True, False}
    assert report["list_semantics"]["item_count"] == 0
    assert report["capability_migration"]["list_semantics"] == "delivered"
    assert not any(
        node.get("kind") == "list"
        for node in source_ast["document"]["containers"].values()
    )
    assert all(
        block.get("block_type") == "paragraph"
        for block in source_ast["document"]["blocks"].values()
    )


def test_nested_docx_missing_numbering_definition_recomputes_local_evidence(tmp_path: Path) -> None:
    report, _, _ = _run_native(_build_nested_missing_definition_docx(tmp_path), tmp_path, "nested-missing")

    diagnostics = report["list_semantics"]["diagnostics"]
    assert len(diagnostics) == 2
    assert {item["source_evidence"]["ordinal"] for item in diagnostics} == {1, 2}
    assert {item["source_evidence"]["restart_hint"] for item in diagnostics} == {True, False}


def test_unknown_marker_kind_is_explicitly_downgraded() -> None:
    facts = {
        "facts_schema_version": "1.0",
        "source": {
            "source_id": "unknown-marker",
            "source_type": "docx",
            "content_sha256": "sha256:" + "a" * 64,
        },
        "parser": {
            "id": "fixture",
            "version": "1.0",
            "dialect": "docx-ooxml",
            "config_sha256": "sha256:" + "b" * 64,
        },
        "units": {
            "u1": {
                "kind": "paragraph",
                "source_ref": {
                    "source_id": "unknown-marker",
                    "part": "word/document.xml",
                    "content_sha256": "sha256:" + "a" * 64,
                    "span": {"start": 0, "end": 1, "unit": "unicode_code_point"},
                },
                "inline_content": [{"kind": "text", "text": "未知标记", "marks": []}],
            },
            "u2": {
                "kind": "paragraph",
                "source_ref": {
                    "source_id": "unknown-marker",
                    "part": "word/document.xml",
                    "content_sha256": "sha256:" + "a" * 64,
                    "span": {"start": 1, "end": 2, "unit": "unicode_code_point"},
                },
                "inline_content": [{"kind": "text", "text": "深层有效项", "marks": []}],
            },
        },
        "containers": {"root": {"kind": "document"}},
        "children": {"root": ["u1", "u2"]},
        "evidence": {
            "u1": {
                "numbering_status": "detected",
                "marker_kind": "mystery",
                "marker_text": "?",
                "ordinal": 1,
                "explicit_depth": 0,
                "indent_value": 0,
                "indent_unit": "twips",
                "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
                "restart_hint": True,
                "blank_before": False,
                "continuation_hint": False,
            },
            "u2": {
                "numbering_status": "detected",
                "marker_kind": "ordered",
                "marker_text": "%1.",
                "ordinal": 1,
                "explicit_depth": 1,
                "indent_value": 240,
                "indent_unit": "twips",
                "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
                "restart_hint": True,
                "blank_before": False,
                "continuation_hint": False,
            },
        },
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }

    result = analyze_lists(facts)

    assert len(result["items"]) == 1
    assert result["items"][0]["item_id"] == "u2"
    assert result["items"][0]["parent_item_id"] is None
    assert result["status"] == "unresolved"
    assert {item["code"] for item in result["diagnostics"]} == {
        "unknown_list_marker_kind",
        "unresolved_list_parent",
    }
    unknown = next(item for item in result["diagnostics"] if item["code"] == "unknown_list_marker_kind")
    assert unknown == {
        "code": "unknown_list_marker_kind",
        "severity": "info",
        "message": "list-like paragraph u1 has an unsupported marker kind; its source content was preserved",
        "node_id": "u1",
        "source_evidence": facts["evidence"]["u1"],
        "fallback": {"kind": "body_paragraph", "preserve_source_content": True},
    }
