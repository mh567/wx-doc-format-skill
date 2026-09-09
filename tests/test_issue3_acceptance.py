from __future__ import annotations

import base64
from pathlib import Path
from zipfile import ZipFile

from docx import Document

from modular_support import _append_numbering_definition, _set_numbering
from test_issue1_acceptance import _paragraph_text, _run_native
from test_issue2_acceptance import _assert_native_completed, _item_by_text, _parent_by_child, _write_markdown


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg=="
)


def _first_non_generated_child(ast: dict, item_id: str) -> str:
    document = ast["document"]
    generated = {
        node["node_id"]
        for node in ast.get("generated_nodes", [])
        if node.get("node_id")
    }
    return next(child_id for child_id in document["children"].get(item_id, []) if child_id not in generated)


def _build_l09_docx(tmp_path: Path) -> Path:
    document = Document()
    _append_numbering_definition(
        document,
        abstract_id=905,
        num_id=905,
        levels=[("decimal", "%1."), ("decimal", "%1.%2")],
    )
    outside = document.add_paragraph("外部一")
    _set_numbering(outside, num_id=905, level=0)

    table = document.add_table(rows=2, cols=2)
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged.paragraphs[0].text = "合并单元格"
    merged_item = merged.add_paragraph("合并列表一")
    _set_numbering(merged_item, num_id=905, level=0)
    merged_item_two = merged.add_paragraph("合并列表二")
    _set_numbering(merged_item_two, num_id=905, level=0)

    nested_host = table.cell(1, 0)
    nested_host.paragraphs[0].text = "外层单元格"
    nested = nested_host.add_table(rows=1, cols=1)
    nested_cell = nested.cell(0, 0)
    nested_cell.paragraphs[0].text = "嵌套表格正文"
    nested_item = nested_cell.add_paragraph("嵌套列表")
    _set_numbering(nested_item, num_id=905, level=0)

    table.cell(1, 1).text = "另一单元格"
    outside_two = document.add_paragraph("外部二")
    _set_numbering(outside_two, num_id=905, level=0)

    output = tmp_path / "l09.docx"
    document.save(output)
    return output


def test_l07_first_table_and_sublist_get_one_evidenced_anchor(tmp_path: Path) -> None:
    source = _write_markdown(
        tmp_path,
        "l07",
        [
            "1.",
            "   | A | B |",
            "   |---|---|",
            "   | x | y |",
            "2.",
            "   1. 子列表",
            "",
        ],
    )

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l07")

    _assert_native_completed(report, output)
    document = source_ast["document"]
    items = _item_by_text(source_ast)
    table_id = next(block_id for block_id, block in document["blocks"].items() if block.get("block_type") == "table")
    first_item = next(
        parent_id
        for parent_id, children in document["children"].items()
        if table_id in children and document["containers"].get(parent_id, {}).get("kind") == "list_item"
    )
    table_child = next(
        child_id
        for child_id in document["children"][first_item]
        if child_id not in normalized["presentation"]["nodes"][first_item]["marker_anchor"]
        and not child_id.startswith("generated-anchor-")
    )
    assert document["blocks"][table_child]["block_type"] == "table"
    nested_item = items["子列表"]
    nested_list = next(parent for parent, children in document["children"].items() if nested_item in children)
    parent_item = next(parent for parent, children in document["children"].items() if nested_list in children)
    assert document["containers"][nested_list]["kind"] == "list"
    assert document["containers"][parent_item]["kind"] == "list_item"

    affected = [first_item, parent_item]
    presentations = normalized["presentation"]["nodes"]
    assert all(presentations[item]["generated_anchor"] for item in affected)
    anchors = [presentations[item]["marker_anchor"] for item in affected]
    assert len(set(anchors)) == len(anchors)
    assert all(anchor.startswith("generated-anchor-") for anchor in anchors)
    assert len(normalized["generated_nodes"]) == len(affected)
    assert report["list_semantics_consumption"]["anchor_consumed_count"] == len(affected)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert b">A<" in archive.read("word/document.xml")


def test_l08_empty_item_generates_anchor_while_inline_image_keeps_paragraph(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(PNG_1X1)
    source = _write_markdown(tmp_path, "l08", ["1.", "2. ![内嵌图](pixel.png)", ""])

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l08")

    _assert_native_completed(report, output)
    items = _item_by_text(source_ast)
    empty_item = next(
        item_id
        for item_id, children in source_ast["document"]["children"].items()
        if source_ast["document"]["containers"].get(item_id, {}).get("kind") == "list_item"
        and all(child.startswith("generated-anchor-") for child in children)
    )
    image_item = next(
        item_id
        for item_id, children in source_ast["document"]["children"].items()
        if source_ast["document"]["containers"].get(item_id, {}).get("kind") == "list_item"
        and any(
            block.get("inline_content")
            and block["inline_content"][0].get("kind") == "image"
            for child in children
            if (block := source_ast["document"]["blocks"].get(child)) is not None
        )
    )
    assert normalized["presentation"]["nodes"][empty_item]["generated_anchor"] is True
    assert normalized["presentation"]["nodes"][image_item]["generated_anchor"] is False
    assert normalized["presentation"]["nodes"][image_item]["marker_anchor"] in source_ast["document"]["blocks"]
    assert len(normalized["generated_nodes"]) == 1
    assert len(Document(output).inline_shapes) == 1
    assert report["list_semantics_consumption"]["anchor_consumed_count"] == 1


def test_l09_merged_and_nested_table_cells_are_unique_and_scope_local(tmp_path: Path) -> None:
    source = _build_l09_docx(tmp_path)

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l09")

    _assert_native_completed(report, output)
    document = source_ast["document"]
    tables = [node_id for node_id, node in document["blocks"].items() if node.get("block_type") == "table"]
    assert len(tables) == 2
    outer = next(table_id for table_id in tables if any(document["containers"].get(child, {}).get("kind") == "table_row" for child in document["children"].get(table_id, [])))
    first_row = document["children"][outer][0]
    first_row_cells = document["children"][first_row]
    assert len(first_row_cells) == 1
    assert document["containers"][first_row_cells[0]]["col_span"] == 2
    assert sum(_paragraph_text(block) == "合并单元格" for block in document["blocks"].values()) == 1

    item_ids = {
        item_id: next(
            _paragraph_text(document["blocks"][child])
            for child in document["children"].get(item_id, [])
            if child in document["blocks"]
        )
        for item_id, node in document["containers"].items()
        if node.get("kind") == "list_item"
    }
    presentations = normalized["presentation"]["nodes"]
    assert presentations[next(item for item, text in item_ids.items() if text == "合并列表一")]["scope_id"] == presentations[next(item for item, text in item_ids.items() if text == "合并列表二")]["scope_id"]
    merged_scope = presentations[next(item for item, text in item_ids.items() if text == "合并列表一")]["scope_id"]
    nested_scope = presentations[next(item for item, text in item_ids.items() if text == "嵌套列表")]["scope_id"]
    assert merged_scope.startswith("scope:") and merged_scope != "scope:document"
    assert nested_scope.startswith("scope:") and nested_scope not in {merged_scope, "scope:document"}
    assert report["table_semantics"]["source_count"] == 2
    rendered_outer = Document(output).tables[0]
    assert [paragraph.text for paragraph in rendered_outer.cell(0, 0).paragraphs if paragraph.text] == [
        "合并单元格",
        "合并列表一",
        "合并列表二",
    ]
    rendered_nested = rendered_outer.cell(1, 0).tables[0]
    assert [paragraph.text for paragraph in rendered_nested.cell(0, 0).paragraphs if paragraph.text] == [
        "嵌套表格正文",
        "嵌套列表",
    ]


def test_l10_quote_and_list_scopes_do_not_consume_each_other(tmp_path: Path) -> None:
    source = _write_markdown(
        tmp_path,
        "l10",
        [
            "> 1. 引用一",
            "> 2. 引用二",
            ">",
            "> # 引用标题",
            "> 3. 引用三",
            "",
            "1. 外部一",
            "   > # 局部标题",
            "   > 引用内一",
            "2. 外部二",
            "",
        ],
    )

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l10")

    _assert_native_completed(report, output)
    document = source_ast["document"]
    items = {
        next(
            _paragraph_text(document["blocks"][child])
            for child in document["children"].get(item_id, [])
            if child in document["blocks"]
        ): item_id
        for item_id, node in document["containers"].items()
        if node.get("kind") == "list_item"
    }
    presentations = normalized["presentation"]["nodes"]
    assert [presentations[items[text]]["target_ordinal"] for text in ("引用一", "引用二", "引用三")] == [1, 2, 1]
    assert [presentations[items[text]]["target_ordinal"] for text in ("外部一", "外部二")] == [1, 2]
    assert presentations[items["引用一"]]["scope_id"] != presentations[items["外部一"]]["scope_id"]
    assert report["list_semantics"]["diagnostics"] == []
