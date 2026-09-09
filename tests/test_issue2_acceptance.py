from __future__ import annotations

import json
import subprocess
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from docx import Document

from modular_support import _append_numbering_definition, _set_numbering
from test_issue1_acceptance import _run_cli, _run_native, _paragraph_text


def _write_markdown(tmp_path: Path, name: str, lines: list[str]) -> Path:
    source = tmp_path / f"{name}.md"
    source.write_text("\n".join(lines), encoding="utf-8")
    return source


def _item_by_text(source_ast: dict) -> dict[str, str]:
    document = source_ast["document"]
    result = {}
    for item_id, item in document["containers"].items():
        if item.get("kind") != "list_item":
            continue
        for child_id in document["children"].get(item_id, []):
            block = document["blocks"].get(child_id)
            if block is not None:
                result[_paragraph_text(block)] = item_id
                break
    return result


def _parent_by_child(source_ast: dict) -> dict[str, str]:
    return {
        child_id: parent_id
        for parent_id, child_ids in source_ast["document"]["children"].items()
        for child_id in child_ids
    }


def _render_items(report: dict, source_ast: dict) -> list[tuple[str, dict]]:
    document = source_ast["document"]
    result = []
    for operation in report["model_audit"]["render_plan"]["operations"]:
        if operation["kind"] != "list_item":
            continue
        item_id = operation["node_id"]
        block_id = next(
            child_id
            for child_id in document["children"].get(item_id, [])
            if child_id in document["blocks"]
        )
        result.append((_paragraph_text(document["blocks"][block_id]), operation["presentation"]))
    return result


def _render_operations(report: dict, source_ast: dict) -> list[tuple[str, dict]]:
    document = source_ast["document"]
    result = []
    for operation in report["model_audit"]["render_plan"]["operations"]:
        node_id = operation["node_id"]
        if node_id in document["blocks"]:
            text = _paragraph_text(document["blocks"][node_id])
        else:
            block_id = next(
                child_id
                for child_id in document["children"].get(node_id, [])
                if child_id in document["blocks"]
            )
            text = _paragraph_text(document["blocks"][block_id])
        result.append((text, operation))
    return result


def _assert_unumbered_paragraphs(output: Path, texts: set[str]) -> None:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    with ZipFile(output) as archive:
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        document = ElementTree.fromstring(archive.read("word/document.xml"))
    style_num_pr = {
        style.get(f"{{{namespace}}}styleId"): style.find("w:pPr/w:numPr", ns)
        for style in styles.findall("w:style", ns)
    }
    seen = set()
    for paragraph in document.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        if text not in texts:
            continue
        seen.add(text)
        assert paragraph.find("w:pPr/w:numPr", ns) is None
        style = paragraph.find("w:pPr/w:pStyle", ns)
        if style is not None:
            assert style_num_pr.get(style.get(f"{{{namespace}}}val")) is None
    assert seen == texts


def _assert_native_completed(report: dict, output: Path) -> None:
    assert report["status"] == "completed"
    assert report["source_facts_immutable"] is True
    assert report["model_audit"]["passed"] is True
    assert report["saved_output_audit"]["passed"] is True
    with ZipFile(output) as archive:
        assert archive.testzip() is None


def _build_l06_depth_jump_docx(tmp_path: Path) -> Path:
    document = Document()
    _append_numbering_definition(
        document,
        abstract_id=904,
        num_id=904,
        levels=[("decimal", "%1."), ("decimal", "%1.%2"), ("decimal", "%1.%2.%3")],
    )
    for text, level in (("一级", 0), ("孤立三级", 2), ("返回一级", 0)):
        paragraph = document.add_paragraph(text)
        _set_numbering(paragraph, num_id=904, level=level)
    output = tmp_path / "l06-depth-jump.docx"
    document.save(output)
    return output


def _run_native_expect_failure(source: Path, tmp_path: Path, name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed, report, _, _, _ = _run_cli(source, tmp_path, name, write_ast=False)
    assert report.is_file(), completed.stderr
    return completed, json.loads(report.read_text(encoding="utf-8"))


def test_l02_nested_item_belongs_to_the_nearest_valid_parent(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path, "l02", ["1. 一级一", "2. 一级二", "   1. 二级属于二", ""])

    report, source_ast, _, output = _run_native(source, tmp_path, "l02")

    _assert_native_completed(report, output)
    items = _item_by_text(source_ast)
    parents = _parent_by_child(source_ast)
    first = items["一级一"]
    second = items["一级二"]
    nested = items["二级属于二"]
    nested_list = parents[nested]
    assert parents[nested_list] == second
    assert parents[nested] == nested_list
    assert parents[first] != parents[nested_list]


def test_l03_continuation_paragraphs_keep_owner_and_do_not_get_markers(tmp_path: Path) -> None:
    source = _write_markdown(
        tmp_path,
        "l03",
        [
            "1. 父项",
            "",
            "   父项续段",
            "",
            "   - 子项",
            "",
            "   父项后续段",
            "",
            "2. 第二项",
            "",
        ],
    )

    report, source_ast, _, output = _run_native(source, tmp_path, "l03")

    _assert_native_completed(report, output)
    assert report["list_semantics"]["continuation_count"] == 2
    assert report["list_semantics_consumption"]["continuation_consumed_count"] == 2
    items = _item_by_text(source_ast)
    parents = _parent_by_child(source_ast)
    owner = items["父项"]
    assert "父项续段" not in items
    assert "父项后续段" not in items
    continuation_blocks = [
        block_id
        for block_id, block in source_ast["document"]["blocks"].items()
        if _paragraph_text(block) in {"父项续段", "父项后续段"}
    ]
    assert {parents[block_id] for block_id in continuation_blocks} == {owner}
    operations = _render_operations(report, source_ast)
    assert [text for text, operation in operations if operation["kind"] == "list_item"] == ["父项", "子项", "第二项"]
    assert all(
        operation["kind"] == "paragraph" and operation["role"] == "body"
        for text, operation in operations
        if text in {"父项续段", "父项后续段"}
    )
    _assert_unumbered_paragraphs(output, {"父项续段", "父项后续段"})


def test_l04_external_body_separates_list_groups_and_source_sequences(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path, "l04", ["1. 第一项", "", "正文段落", "", "1. 第二项", ""])

    report, source_ast, _, output = _run_native(source, tmp_path, "l04")

    _assert_native_completed(report, output)
    document = source_ast["document"]
    root_children = document["children"][document["root_id"]]
    root_lists = [
        node_id
        for node_id in root_children
        if document["containers"].get(node_id, {}).get("kind") == "list"
    ]
    root_blocks = [node_id for node_id in root_children if node_id in document["blocks"]]
    assert len(root_lists) == 2
    assert [document["containers"].get(node_id, {}).get("kind", "paragraph") for node_id in root_children] == ["list", "paragraph", "list"]
    assert [_paragraph_text(document["blocks"][node_id]) for node_id in root_blocks] == ["正文段落"]
    assert document["containers"][root_lists[0]]["source_sequence_id"] != document["containers"][root_lists[1]]["source_sequence_id"]
    rendered = _render_items(report, source_ast)
    assert [text for text, _ in rendered] == ["第一项", "第二项"]
    assert [presentation["target_ordinal"] for _, presentation in rendered] == [1, 2]
    assert [presentation["restart"] for _, presentation in rendered] == [True, False]


def test_l05_source_numbering_is_preserved_and_heading_restarts_target_scope(tmp_path: Path) -> None:
    source = _write_markdown(
        tmp_path,
        "l05",
        ["3. 非一", "7. 跳号", "1. 重复一", "", "# 新章节", "", "1. 新列表", ""],
    )

    report, source_ast, _, output = _run_native(source, tmp_path, "l05")

    _assert_native_completed(report, output)
    items = _item_by_text(source_ast)
    containers = source_ast["document"]["containers"]
    assert [containers[items[text]]["source_marker"] for text in ("非一", "跳号", "重复一", "新列表")] == ["3.", "7.", "1.", "1."]
    rendered = _render_items(report, source_ast)
    assert [text for text, _ in rendered] == ["非一", "跳号", "重复一", "新列表"]
    assert [presentation["target_ordinal"] for _, presentation in rendered] == [1, 2, 3, 1]
    assert [presentation["restart"] for _, presentation in rendered] == [True, False, False, True]


def test_l06_tabs_blank_lines_and_multi_level_fallback_preserve_parent_chain(tmp_path: Path) -> None:
    source = _write_markdown(
        tmp_path,
        "l06-markdown",
        ["1. 一级", "", "", "\t1. 二级", "", "", "\t\t1. 三级", "", "", "2. 返回一级", ""],
    )

    report, source_ast, _, output = _run_native(source, tmp_path, "l06-markdown")

    _assert_native_completed(report, output)
    items = _item_by_text(source_ast)
    parents = _parent_by_child(source_ast)
    assert report["list_semantics"]["diagnostics"] == []
    assert parents[parents[items["二级"]]] == items["一级"]
    assert parents[parents[items["三级"]]] == items["二级"]
    assert [presentation["target_ordinal"] for _, presentation in _render_items(report, source_ast)] == [1, 2, 3, 4]


def test_l06_docx_depth_jump_is_explicitly_unresolved_and_not_published(tmp_path: Path) -> None:
    source = _build_l06_depth_jump_docx(tmp_path)

    completed, report = _run_native_expect_failure(source, tmp_path, "l06-docx")

    assert completed.returncode != 0
    assert report["status"] == "failed"
    assert any(item["code"] == "unresolved_list_parent" for item in report["diagnostics"])
    assert not (tmp_path / "l06-docx.output.docx").exists()
