from __future__ import annotations

import base64
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from docx import Document

from modular_support import _append_numbering_definition, _set_numbering
from test_issue1_acceptance import _paragraph_text, _run_native
from test_issue2_acceptance import _assert_native_completed, _item_by_text, _parent_by_child, _write_markdown
from test_modular_normalization import _ast, _profile
from wxdf.auditing import check_saved_output
from wxdf.normalization import normalize
from wxdf.validation import validate_ast


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

    table = document.add_table(rows=3, cols=3)
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged.paragraphs[0].text = "合并单元格"
    merged_item = merged.add_paragraph("合并列表一")
    _set_numbering(merged_item, num_id=905, level=0)
    merged_item_two = merged.add_paragraph("合并列表二")
    _set_numbering(merged_item_two, num_id=905, level=0)

    vertical = table.cell(0, 2).merge(table.cell(1, 2))
    vertical.text = "纵向合并"

    nested_host = table.cell(1, 0)
    nested_host.paragraphs[0].text = "外层单元格"
    nested = nested_host.add_table(rows=1, cols=1)
    nested_cell = nested.cell(0, 0)
    nested_cell.paragraphs[0].text = "嵌套表格正文"
    nested_item = nested_cell.add_paragraph("嵌套列表")
    _set_numbering(nested_item, num_id=905, level=0)

    table.cell(1, 1).text = "另一单元格"
    table.cell(2, 0).text = "底部一"
    table.cell(2, 1).text = "底部二"
    table.cell(2, 2).text = "底部三"
    outside_two = document.add_paragraph("外部二")
    _set_numbering(outside_two, num_id=905, level=0)

    output = tmp_path / "l09.docx"
    document.save(output)
    return output


def _assert_numbered_texts(output: Path, texts: set[str]) -> None:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    with ZipFile(output) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
    style_num_pr = {
        style.get(f"{{{namespace}}}styleId"): style.find("w:pPr/w:numPr", ns)
        for style in styles.findall("w:style", ns)
    }
    seen: set[str] = set()
    for paragraph in document.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        if text not in texts:
            continue
        seen.add(text)
        direct = paragraph.find("w:pPr/w:numPr", ns)
        style = paragraph.find("w:pPr/w:pStyle", ns)
        inherited = style_num_pr.get(style.get(f"{{{namespace}}}val")) if style is not None else None
        assert direct is not None or inherited is not None
    assert seen == texts


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


def test_l07_block_image_first_child_gets_generated_anchor() -> None:
    ast = _ast()
    source_ref = ast["document"]["blocks"]["p3"]["source_ref"]
    ast["document"]["resources"]["image-1"] = {
        "kind": "image",
        "source_ref": source_ref,
        "content_sha256": source_ref["content_sha256"],
    }
    ast["document"]["blocks"]["image-1"] = {
        "block_type": "image",
        "resource_id": "image-1",
        "alt_text": "块图片",
        "source_ref": source_ref,
    }
    ast["document"]["blocks"].pop("p3")
    ast["document"]["children"]["i3"] = ["image-1"]
    validate_ast(ast)

    normalized = normalize(ast, _profile(), strategy="wx-default-v2")

    presentation = normalized["presentation"]["nodes"]["i3"]
    assert presentation["generated_anchor"] is True
    assert presentation["marker_anchor"] == "generated-anchor-i3"
    assert [node["node_id"] for node in normalized["generated_nodes"]] == ["generated-anchor-i3"]


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
    assert len(first_row_cells) == 2
    assert [document["containers"][cell_id]["col_span"] for cell_id in first_row_cells] == [2, 1]
    assert sum(_paragraph_text(block) == "合并单元格" for block in document["blocks"].values()) == 1
    vertical_cells = [
        node for node in document["containers"].values()
        if node.get("kind") == "table_cell" and node.get("row_span") == 2
    ]
    assert len(vertical_cells) == 1
    assert sum(_paragraph_text(block) == "纵向合并" for block in document["blocks"].values()) == 1

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
    assert rendered_outer.cell(0, 2).text == "纵向合并"
    assert rendered_outer.cell(1, 2).text == "纵向合并"


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
    parents = _parent_by_child(source_ast)
    quote_list = parents[items["引用一"]]
    quote_id = parents[quote_list]
    assert document["containers"][quote_id]["kind"] == "quote"
    assert items["引用一"] in document["children"][quote_list]
    outer_list = next(
        parent_id
        for parent_id, children in document["children"].items()
        if items["外部一"] in children
    )
    assert document["containers"][outer_list]["kind"] == "list"
    operations = [
        operation
        for operation in report["model_audit"]["render_plan"]["operations"]
        if operation["kind"] == "list_item"
    ]
    assert {operation["node_id"] for operation in operations} == set(presentations)
    _assert_numbered_texts(output, {"引用一", "引用二", "引用三", "外部一", "外部二"})
    assert report["list_semantics"]["diagnostics"] == []


def test_saved_output_audit_rejects_plain_styled_text_as_numbered(tmp_path: Path) -> None:
    output = tmp_path / "plain.docx"
    document = Document()
    document.add_paragraph("条目", style="Heading 1")
    document.save(output)

    findings = check_saved_output(
        output,
        {"expected_texts": ["条目"]},
        {"item": {"kind": "paragraph", "text": "条目", "numbered": True}},
    )

    assert findings["passed"] is False
    assert any(issue["code"] == "list_numbering_missing" for issue in findings["issues"])
