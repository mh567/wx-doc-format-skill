from __future__ import annotations

import json
import subprocess
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest
from docx import Document

from modular_support import _append_numbering_definition, _set_numbering


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "scripts" / ".venv" / "bin" / "python3"
TEMPLATE = ROOT / "assets" / "wx_template.docx"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
NESTED_MARKDOWN = "1. 一级有序\n   - 二级无序\n     1. 三级有序\n2. 第二个一级\n"


def _build_l01_docx(tmp_path: Path) -> Path:
    document = Document()
    _append_numbering_definition(
        document,
        abstract_id=903,
        num_id=903,
        levels=[("decimal", "%1."), ("bullet", "%1.%2"), ("decimal", "%1.%2.%3")],
    )
    for text, level in (("一级有序", 0), ("二级无序", 1), ("三级有序", 2), ("第二个一级", 0)):
        paragraph = document.add_paragraph(text)
        _set_numbering(paragraph, num_id=903, level=level)
    output = tmp_path / "l01.docx"
    document.save(output)
    return output


def _run_cli(
    source: Path,
    tmp_path: Path,
    name: str,
    *,
    pipeline: str | None = "modular",
    modular_profile: str | None = "native-v2",
    list_policy: str | None = "wx-default-v2",
    write_ast: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path, Path]:
    output = tmp_path / f"{name}.output.docx"
    report = tmp_path / f"{name}.report.json"
    source_ast = tmp_path / f"{name}.source.json"
    normalized_ast = tmp_path / f"{name}.normalized.json"
    command = [
        str(PYTHON),
        str(ROOT / "scripts" / "main.py"),
        "--input", str(source),
        "--output", str(output),
        "--template", str(TEMPLATE),
    ]
    if pipeline is not None:
        command.extend(["--pipeline", pipeline])
    if modular_profile is not None:
        command.extend(["--modular-profile", modular_profile])
    if list_policy is not None:
        command.extend(["--list-policy", list_policy])
    command.extend(["--llm-enhance", "off", "--report", str(report)])
    if write_ast:
        command.extend(["--source-ast", str(source_ast), "--ast", str(normalized_ast)])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return completed, report, output, source_ast, normalized_ast


def _run_native(source: Path, tmp_path: Path, name: str) -> tuple[dict, dict, dict, Path]:
    completed, report, output, source_ast, normalized_ast = _run_cli(source, tmp_path, name)
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    return (
        json.loads(report.read_text(encoding="utf-8")),
        json.loads(source_ast.read_text(encoding="utf-8")),
        json.loads(normalized_ast.read_text(encoding="utf-8")),
        output,
    )


def _paragraph_text(node: dict) -> str:
    return "".join(
        item.get("text", "")
        for item in node.get("inline_content", [])
        if item.get("kind") == "text"
    )


def _assert_nested_source_ast(source_ast: dict) -> None:
    document = source_ast["document"]
    items = {
        node_id: node
        for node_id, node in document["containers"].items()
        if node.get("kind") == "list_item"
    }
    blocks = document["blocks"]
    item_by_text = {
        _paragraph_text(blocks[child_id]): item_id
        for item_id in items
        for child_id in document["children"].get(item_id, [])
        if child_id in blocks
    }
    first = item_by_text["一级有序"]
    second = item_by_text["二级无序"]
    third = item_by_text["三级有序"]
    fourth = item_by_text["第二个一级"]
    parent_by_child = {
        child_id: parent_id
        for parent_id, children in document["children"].items()
        for child_id in children
    }
    first_list = parent_by_child[first]
    second_list = parent_by_child[second]
    third_list = parent_by_child[third]
    assert document["containers"][first_list]["kind"] == "list"
    assert document["containers"][second_list]["kind"] == "list"
    assert document["containers"][third_list]["kind"] == "list"
    assert [
        document["containers"][list_id]["list_kind"]
        for list_id in (first_list, second_list, third_list)
    ] == ["ordered", "unordered", "ordered"]
    assert document["children"][first_list] == [first, fourth]
    assert document["children"][second_list] == [second]
    assert document["children"][third_list] == [third]
    assert parent_by_child[second_list] == first
    assert parent_by_child[third_list] == second


def _assert_render_plan(report: dict, source_ast: dict) -> None:
    document = source_ast["document"]
    item_text = {}
    for item_id, item in document["containers"].items():
        if item.get("kind") != "list_item":
            continue
        block_id = next(
            (child_id for child_id in document["children"].get(item_id, []) if child_id in document["blocks"]),
            None,
        )
        if block_id is not None:
            item_text[item_id] = _paragraph_text(document["blocks"][block_id])
    operations = [
        operation
        for operation in report["model_audit"]["render_plan"]["operations"]
        if operation["kind"] == "list_item"
    ]
    assert [item_text[operation["node_id"]] for operation in operations] == [
        "一级有序",
        "二级无序",
        "三级有序",
        "第二个一级",
    ]
    presentations = [operation["presentation"] for operation in operations]
    assert [item["marker_kind"] for item in presentations] == [
        "ordered",
        "unordered",
        "ordered",
        "ordered",
    ]
    assert [item["target_level"] for item in presentations] == [0, 1, 0, 0]
    assert [item["target_ordinal"] for item in presentations] == [1, None, 2, 3]
    assert [item["restart"] for item in presentations] == [True, True, False, False]


def _assert_saved_numbering(output: Path) -> None:
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        document = ElementTree.fromstring(archive.read("word/document.xml"))
    numbers = {item.get(f"{{{W_NS}}}numId") for item in numbering.findall("w:num", NS)}
    abstract_num_by_id = {
        item.get(f"{{{W_NS}}}abstractNumId"): item
        for item in numbering.findall("w:abstractNum", NS)
    }
    abstract_id_by_num = {
        item.get(f"{{{W_NS}}}numId"): item.find("w:abstractNumId", NS).get(f"{{{W_NS}}}val")
        for item in numbering.findall("w:num", NS)
        if item.find("w:abstractNumId", NS) is not None
    }
    style_num_pr = {}
    for style in styles.findall("w:style", NS):
        style_id = style.get(f"{{{W_NS}}}styleId")
        num_pr = style.find("w:pPr/w:numPr", NS)
        if style_id is not None and num_pr is not None:
            style_num_pr[style_id] = num_pr
    assert numbers
    paragraph_levels = {
        "一级有序": "0",
        "二级无序": "0",
        "三级有序": "0",
        "第二个一级": "0",
    }
    paragraph_formats = {
        "一级有序": "lowerLetter",
        "二级无序": "bullet",
        "三级有序": "lowerLetter",
        "第二个一级": "lowerLetter",
    }
    seen_texts = []
    for paragraph in document.findall(".//w:p", NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
        if text not in paragraph_levels:
            continue
        assert text not in seen_texts
        seen_texts.append(text)
        num_pr = paragraph.find("w:pPr/w:numPr", NS)
        if num_pr is None:
            style = paragraph.find("w:pPr/w:pStyle", NS)
            num_pr = style_num_pr.get(style.get(f"{{{W_NS}}}val")) if style is not None else None
        assert num_pr is not None
        num_id = num_pr.find("w:numId", NS)
        ilvl = num_pr.find("w:ilvl", NS)
        assert num_id is not None
        assert ilvl is not None
        num_id_value = num_id.get(f"{{{W_NS}}}val")
        assert num_id_value in numbers
        assert ilvl.get(f"{{{W_NS}}}val") == paragraph_levels[text]
        abstract_id = abstract_id_by_num[num_id_value]
        abstract_num = abstract_num_by_id[abstract_id]
        level = abstract_num.find(f"w:lvl[@w:ilvl='{paragraph_levels[text]}']", NS)
        assert level is not None
        num_fmt = level.find("w:numFmt", NS)
        assert num_fmt is not None
        assert num_fmt.get(f"{{{W_NS}}}val") == paragraph_formats[text]
    assert seen_texts == list(paragraph_levels)


def _run_legacy_default(source: Path, tmp_path: Path) -> tuple[dict, Path]:
    completed, report_path, output, _, _ = _run_cli(
        source,
        tmp_path,
        "legacy",
        pipeline=None,
        modular_profile=None,
        list_policy=None,
        write_ast=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    return json.loads(report_path.read_text(encoding="utf-8")), output


@pytest.mark.parametrize("source_kind", ["markdown", "docx"])
def test_l01_nested_ordered_unordered_ordered_runs_full_native_acceptance(tmp_path: Path, source_kind: str) -> None:
    if source_kind == "markdown":
        source = tmp_path / "l01.md"
        source.write_text(NESTED_MARKDOWN, encoding="utf-8")
    else:
        source = _build_l01_docx(tmp_path)

    source_before = source.read_bytes()
    report, source_ast, normalized_ast, output = _run_native(source, tmp_path, source_kind)

    assert source.read_bytes() == source_before
    assert report["status"] == "completed"
    assert report["list_policy"] == "wx-default-v2"
    assert report["module_chain"] == [
        "SourceParser",
        "SourceFacts",
        "SemanticModuleCandidateSet",
        "SemanticResolverDecisionSet",
        "TransactionManagerSourceAST",
        "NormalizationPolicy",
        "RenderPlan",
        "Renderer",
        "AuditModule",
        "Publisher",
    ]
    assert report["execution_plan"]["profile"] == "wx_full_rules-native-v2"
    assert [
        module["module_id"] for module in report["execution_plan"]["modules"]
    ] == [
        "native-source-parser",
        "native-candidate-set",
        "native-resolver",
        "native-transaction",
        "native-normalizer",
        "native-renderer",
        "native-auditor",
        "native-publisher",
    ]
    assert report["native_transaction"]["status"] == "committed"
    assert report["model_audit"]["passed"] is True
    assert report["saved_output_audit"]["passed"] is True
    assert report["source_facts_summary"]["unit_count"] == 4
    assert set(report["source_facts_summary"]["marker_kinds"]) == {"ordered", "unordered"}
    assert report["source_facts_immutable"] is True
    assert report["source_facts_digest"] == report["source_facts_digest_after"]
    assert report["list_semantics"]["item_count"] == 4
    assert report["list_semantics_consumption"]["item_consumed_count"] == 4
    _assert_nested_source_ast(source_ast)
    _assert_render_plan(report, source_ast)
    assert normalized_ast["source_ast"]["revision"] == source_ast["revision"]
    assert normalized_ast["source_ast"]["document"] == source_ast["document"]
    _assert_saved_numbering(output)


def test_legacy_remains_the_default_cli_pipeline(tmp_path: Path) -> None:
    source = tmp_path / "legacy.md"
    source.write_text(NESTED_MARKDOWN, encoding="utf-8")

    report, output = _run_legacy_default(source, tmp_path)

    assert "module_chain" not in report
    assert report["source_document_model_summary"]["schema_version"] == "1.0"
    assert report["markdown_source_audit"]["adapter"] == "markdown-it-py"
    assert report["markdown_source_audit"]["list_items"] == 4
    with ZipFile(output) as archive:
        assert archive.testzip() is None
