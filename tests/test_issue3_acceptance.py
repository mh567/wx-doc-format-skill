from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from modular_support import _append_numbering_definition, _set_numbering
from test_issue1_acceptance import _paragraph_text, _run_native
from test_issue2_acceptance import _assert_native_completed, _item_by_text, _parent_by_child, _write_markdown
from wxdf.auditing import check_saved_output
from wxdf.parsers import parse
from wxdf.rendering import render
from wxdf.transaction import _source_ref_matches
from template_profile import load_template_profile


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg=="
)


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
    nested = nested_host.add_table(rows=1, cols=1)
    nested_cell = nested.cell(0, 0)
    nested_cell.paragraphs[0].text = "嵌套表格正文"
    nested_item = nested_cell.add_paragraph("嵌套列表")
    _set_numbering(nested_item, num_id=905, level=0)
    nested_host.add_paragraph("外层单元格")

    table.cell(1, 1).text = "另一单元格"
    table.cell(2, 0).text = "底部一"
    table.cell(2, 1).text = "底部二"
    table.cell(2, 2).text = "底部三"
    outside_two = document.add_paragraph("外部二")
    _set_numbering(outside_two, num_id=905, level=0)

    output = tmp_path / "l09.docx"
    document.save(output)
    return output


def _build_l08_docx(tmp_path: Path) -> Path:
    document = Document()
    _append_numbering_definition(document, abstract_id=906, num_id=906, levels=[("decimal", "%1.")])
    empty = document.add_paragraph()
    _set_numbering(empty, num_id=906, level=0)
    image_only = document.add_paragraph()
    _set_numbering(image_only, num_id=906, level=0)
    image_only.add_run().add_picture(io.BytesIO(PNG_1X1))
    text_image = document.add_paragraph("文字图")
    _set_numbering(text_image, num_id=906, level=0)
    text_image.add_run().add_picture(io.BytesIO(PNG_1X1))
    output = tmp_path / "l08.docx"
    document.save(output)
    return output


def _rewrite_docx_part(source: Path, target: Path, part_name: str, transform) -> None:
    with ZipFile(source) as original, ZipFile(target, "w") as rewritten:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == part_name:
                data = transform(data)
            rewritten.writestr(info, data)


def _add_docx_part(source: Path, target: Path, part_name: str, data: bytes) -> None:
    with ZipFile(source) as original, ZipFile(target, "w") as rewritten:
        for info in original.infolist():
            rewritten.writestr(info, original.read(info.filename))
        rewritten.writestr(part_name, data)


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


def _assert_numbered_image_paragraph(output: Path, expected_count: int = 1) -> None:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    with ZipFile(output) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
    numbered_style_ids = {
        style.get(f"{{{namespace}}}styleId")
        for style in styles.findall("w:style", ns)
        if style.find("w:pPr/w:numPr/w:numId", ns) is not None
        and style.find("w:pPr/w:numPr/w:numId", ns).get(f"{{{namespace}}}val") not in {None, "0"}
    }
    image_paragraphs = [paragraph for paragraph in document.findall(".//w:p", ns) if paragraph.find(".//w:drawing", ns) is not None]
    assert len(image_paragraphs) == expected_count
    for paragraph in image_paragraphs:
        style = paragraph.find("w:pPr/w:pStyle", ns)
        style_id = style.get(f"{{{namespace}}}val") if style is not None else None
        direct = paragraph.find("w:pPr/w:numPr/w:numId", ns)
        assert style_id in numbered_style_ids or (
            direct is not None and direct.get(f"{{{namespace}}}val") not in {None, "0"}
        )


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
    # T02 materializes a generated caption node directly above the table, so
    # select the table by block type rather than by child position.
    table_child = next(
        child_id
        for child_id in document["children"][first_item]
        if document["blocks"].get(child_id, {}).get("block_type") == "table"
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


def test_l07_image_content_runs_real_parser_chain(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(PNG_1X1)
    source = _write_markdown(tmp_path, "l07-image", ["1.", "   ![块图片](pixel.png)", "2. 后续", ""])

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l07-image")

    _assert_native_completed(report, output)
    image_items = [
        item_id
        for item_id, children in source_ast["document"]["children"].items()
        if source_ast["document"]["containers"].get(item_id, {}).get("kind") == "list_item"
        and any(
            source_ast["document"]["blocks"].get(child, {}).get("inline_content", [{}])[0].get("kind") == "image"
            for child in children
            if child in source_ast["document"]["blocks"]
        )
    ]
    assert len(image_items) == 1
    assert normalized["presentation"]["nodes"][image_items[0]]["generated_anchor"] is False
    assert len(source_ast["document"]["resources"]) == 1
    assert len(Document(output).inline_shapes) == 1
    assert report["saved_output_audit"]["passed"] is True


def test_markdown_source_refs_carry_parser_version(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path, "markdown-parser-version", ["1. Markdown", ""])
    facts = parse(source)

    assert facts["parser"]["version"] == "1.1.0"
    assert all(
        node.get("source_ref", {}).get("parser_version") == "1.1.0"
        for collection in (facts["units"], facts["containers"], facts["resources"])
        for node in collection.values()
        if node.get("source_ref")
    )


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
    _assert_numbered_image_paragraph(output)
    assert report["list_semantics_consumption"]["anchor_consumed_count"] == 1


def test_l08_docx_image_only_and_inline_image_lists_are_parsed(tmp_path: Path) -> None:
    source = _build_l08_docx(tmp_path)

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l08-docx")

    _assert_native_completed(report, output)
    document = source_ast["document"]
    list_items = [node_id for node_id, node in document["containers"].items() if node.get("kind") == "list_item"]
    assert len(list_items) == 3
    presented_items = [item_id for item_id in list_items if item_id in normalized["presentation"]["nodes"]]
    assert sum(normalized["presentation"]["nodes"][item_id]["generated_anchor"] for item_id in presented_items) == 1
    assert len(document["resources"]) == 1
    assert len(Document(output).inline_shapes) == 2
    _assert_numbered_image_paragraph(output, expected_count=2)
    assert report["list_semantics_consumption"]["anchor_consumed_count"] == 1


def test_docx_parser_reuses_numbering_maps_for_nested_content(monkeypatch, tmp_path: Path) -> None:
    source = _build_l09_docx(tmp_path)
    from wxdf.parsers import build_numbering_catalog

    real_catalog = build_numbering_catalog
    calls = 0

    def counted_catalog(document):
        nonlocal calls
        calls += 1
        return real_catalog(document)

    monkeypatch.setattr("wxdf.parsers.build_numbering_catalog", counted_catalog)
    facts = parse(source)

    assert facts["parser"]["version"] == "1.1.0"
    assert calls == 1


def test_saved_output_audit_rejects_missing_numbering_definition(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(PNG_1X1)
    source = _write_markdown(tmp_path, "numbering-definition", ["1. ![内嵌图](pixel.png)", ""])
    report, _, normalized, output = _run_native(source, tmp_path, "numbering-definition")
    corrupted = tmp_path / "numbering-definition-corrupt.docx"
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}

    def invalidate_numbering(data: bytes) -> bytes:
        root = ElementTree.fromstring(data)
        for number in root.findall("w:num", ns):
            number.set(f"{{{namespace}}}numId", "999999")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    _rewrite_docx_part(output, corrupted, "word/numbering.xml", invalidate_numbering)
    resource_id = next(iter(normalized["source_ast"]["document"]["resources"]))
    rerendered = render(
        normalized,
        tmp_path / "numbering-definition-rerendered.docx",
        template_path=Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx",
        resources={resource_id: image.read_bytes()},
    )
    findings = check_saved_output(corrupted, normalized, rerendered["render_map"])
    assert findings["passed"] is False
    assert any(issue["code"] == "list_numbering_definition_invalid" for issue in findings["issues"])

    wrong_format = tmp_path / "numbering-definition-format-corrupt.docx"

    def invalidate_format(data: bytes) -> bytes:
        root = ElementTree.fromstring(data)
        for num_fmt in root.findall(".//w:numFmt", ns):
            num_fmt.set(f"{{{namespace}}}val", "bullet")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    _rewrite_docx_part(output, wrong_format, "word/numbering.xml", invalidate_format)
    format_findings = check_saved_output(wrong_format, normalized, rerendered["render_map"])
    assert format_findings["passed"] is False
    assert any(issue["code"] == "list_numbering_mapping_mismatch" for issue in format_findings["issues"])

    remapped_abstract = tmp_path / "numbering-definition-abstract-corrupt.docx"

    def remap_abstract(data: bytes) -> bytes:
        root = ElementTree.fromstring(data)
        source_abstract = next(
            abstract
            for abstract in root.findall("w:abstractNum", ns)
            if abstract.get(f"{{{namespace}}}abstractNumId") == "4"
        )
        duplicate = ElementTree.fromstring(ElementTree.tostring(source_abstract))
        duplicate.set(f"{{{namespace}}}abstractNumId", "999")
        root.append(duplicate)
        for number in root.findall("w:num", ns):
            if number.get(f"{{{namespace}}}numId") == "3":
                number.find("w:abstractNumId", ns).set(f"{{{namespace}}}val", "999")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    _rewrite_docx_part(output, remapped_abstract, "word/numbering.xml", remap_abstract)
    abstract_findings = check_saved_output(remapped_abstract, normalized, rerendered["render_map"])
    assert abstract_findings["passed"] is False
    assert any(issue["code"] == "list_numbering_mapping_mismatch" for issue in abstract_findings["issues"])


def test_saved_output_audit_rejects_tampered_image_part(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(PNG_1X1)
    source = _write_markdown(tmp_path, "image-audit", ["1. ![内嵌图](pixel.png)", ""])
    _, _, normalized, output = _run_native(source, tmp_path, "image-audit")
    resource_id = next(iter(normalized["source_ast"]["document"]["resources"]))
    rerendered = render(
        normalized,
        tmp_path / "image-audit-rerendered.docx",
        template_path=Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx",
        resources={resource_id: image.read_bytes()},
    )
    with ZipFile(output) as archive:
        document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
        relationships_xml = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
        drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
        rel_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        image_relationships = {
            relationship.get("Id"): relationship.get("Target")
            for relationship in relationships_xml
            if relationship.get("Type", "").endswith("/image")
        }
        relationship_id = document_xml.find(f".//{{{drawing_namespace}}}blip").get(f"{{{rel_namespace}}}embed")
        media_part = "word/" + image_relationships[relationship_id].lstrip("/")
    tampered = tmp_path / "image-audit-tampered.docx"
    _rewrite_docx_part(output, tampered, media_part, lambda data: b"tampered-image")

    findings = check_saved_output(tampered, normalized, rerendered["render_map"])
    assert findings["passed"] is False
    assert any(issue["code"] == "image_resource_mismatch" for issue in findings["issues"])


def test_saved_output_audit_rejects_retargeted_image_relationship(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(PNG_1X1)
    source = _write_markdown(tmp_path, "image-target-audit", ["1. ![内嵌图](pixel.png)", ""])
    _, _, normalized, output = _run_native(source, tmp_path, "image-target-audit")
    resource_id = next(iter(normalized["source_ast"]["document"]["resources"]))
    rerendered = render(
        normalized,
        tmp_path / "image-target-audit-rerendered.docx",
        template_path=Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx",
        resources={resource_id: image.read_bytes()},
    )
    with ZipFile(output) as archive:
        document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
        relationships_xml = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
        drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
        relationship_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        image_relationships = {
            relationship.get("Id"): relationship.get("Target")
            for relationship in relationships_xml
            if relationship.get("Type", "").endswith("/image")
        }
        relationship_id = document_xml.find(f".//{{{drawing_namespace}}}blip").get(f"{{{relationship_namespace}}}embed")
        original_target = image_relationships[relationship_id]
        original_part = "word/" + original_target.lstrip("/")
        alternate_part = next(
            name for name in archive.namelist()
            if name.startswith("word/media/") and name != original_part
        )
        original_blob = archive.read(original_part)

    same_content = tmp_path / "image-target-audit-same-content.docx"
    _rewrite_docx_part(output, same_content, alternate_part, lambda data: original_blob)

    def retarget(data: bytes) -> bytes:
        root = ElementTree.fromstring(data)
        for relationship in root:
            if relationship.get("Id") == relationship_id:
                relationship.set("Target", alternate_part.removeprefix("word/"))
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    retargeted = tmp_path / "image-target-audit-retargeted.docx"
    _rewrite_docx_part(same_content, retargeted, "word/_rels/document.xml.rels", retarget)

    findings = check_saved_output(retargeted, normalized, rerendered["render_map"])
    assert findings["passed"] is False
    assert any(issue["code"] == "image_relationship_target_mismatch" for issue in findings["issues"])


def test_saved_output_audit_rejects_unreferenced_media_part(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path, "media-set-audit", ["正文", ""])
    _, _, normalized, output = _run_native(source, tmp_path, "media-set-audit")
    rerendered = render(
        normalized,
        tmp_path / "media-set-audit-rerendered.docx",
        template_path=Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx",
    )
    tampered = tmp_path / "media-set-audit-tampered.docx"
    _add_docx_part(output, tampered, "word/media/unreferenced.png", PNG_1X1)

    findings = check_saved_output(tampered, normalized, rerendered["render_map"])
    assert findings["passed"] is False
    assert any(issue["code"] == "image_media_parts_changed" for issue in findings["issues"])


def test_saved_output_audit_rejects_unordered_numbering_mapping(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path, "unordered-numbering", ["- 一级无序", "  - 二级无序", ""])
    _, _, normalized, output = _run_native(source, tmp_path, "unordered-numbering")
    numbering_ids = normalized["presentation"]["profile"]["numbering_ids"]
    rerendered = render(
        normalized,
        tmp_path / "unordered-numbering-rerendered.docx",
        template_path=Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx",
    )
    corrupted = tmp_path / "unordered-numbering-corrupt.docx"
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}

    def remap_unordered(data: bytes) -> bytes:
        root = ElementTree.fromstring(data)
        for number in root.findall("w:num", ns):
            if number.get(f"{{{namespace}}}numId") == str(numbering_ids["list_dash"]):
                number.find("w:abstractNumId", ns).set(
                    f"{{{namespace}}}val",
                    str(numbering_ids["list_bullet_abstract"]),
                )
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    _rewrite_docx_part(output, corrupted, "word/numbering.xml", remap_unordered)
    findings = check_saved_output(corrupted, normalized, rerendered["render_map"])
    assert findings["passed"] is False
    assert any(issue["code"] == "list_numbering_mapping_mismatch" for issue in findings["issues"])


def test_template_numbering_ids_include_unordered_mappings() -> None:
    profile = load_template_profile(Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx")
    numbering_ids = profile["numbering_ids"]

    assert all(
        key in numbering_ids
        for key in ("list_dash", "list_dash_abstract", "list_bullet", "list_bullet_abstract")
    )


def test_l09_merged_and_nested_table_cells_are_unique_and_scope_local(tmp_path: Path) -> None:
    source = _build_l09_docx(tmp_path)

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l09")

    # The two outer paragraphs carry an isolated general numbering definition,
    # which the parser could not confirm as a list item, so they stay body text.
    # A low-confidence marker is not a Resolver conflict, so the document
    # completes cleanly instead of warning.
    _assert_native_completed(report, output)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
    document = source_ast["document"]
    assert all(
        node.get("source_ref", {}).get("parser_version") == "1.1.0"
        for collection in (document["blocks"], document["containers"])
        for node in collection.values()
        if node.get("source_ref")
    )
    tables = [node_id for node_id, node in document["blocks"].items() if node.get("block_type") == "table"]
    assert len(tables) == 2
    outer = next(table_id for table_id in tables if any(document["containers"].get(child, {}).get("kind") == "table_row" for child in document["children"].get(table_id, [])))
    root_labels = []
    for child_id in document["children"]["root"]:
        if child_id in document["blocks"]:
            block = document["blocks"][child_id]
            # T02 materializes a generated caption node directly above its
            # table.  This case asserts the outer block and table sequence.
            if block.get("role") == "caption":
                continue
            if block.get("block_type") == "table":
                root_labels.append(("table", None))
            else:
                root_labels.append(("block", _paragraph_text(block)))
    assert root_labels == [("block", "外部一"), ("table", None), ("block", "外部二")]
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
    nested_table = next(table_id for table_id in tables if table_id != outer)
    nested_cell = _parent_by_child(source_ast)[nested_table]
    nested_labels = [
        "table" if child == nested_table else _paragraph_text(document["blocks"][child])
        if child in document["blocks"]
        else document["containers"].get(child, {}).get("kind")
        for child in document["children"][nested_cell]
    ]
    assert [label for label in nested_labels if label] == ["table", "外层单元格"]
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
    cell_content = []
    for child in rendered_outer.cell(1, 0)._tc:
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name == "p":
            cell_content.append((local_name, "".join(node.text or "" for node in child.iter() if node.tag.rsplit("}", 1)[-1] == "t")))
        elif local_name == "tbl":
            cell_content.append((local_name, ""))
    assert next(index for index, item in enumerate(cell_content) if item[0] == "tbl") < next(
        index for index, item in enumerate(cell_content) if item == ("p", "外层单元格")
    )
    assert rendered_outer.cell(0, 2).text == "纵向合并"
    assert rendered_outer.cell(1, 2).text == "纵向合并"
    with ZipFile(output) as archive:
        document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    assert any(node.get(f"{{{namespace}}}val") == "2" for node in document_xml.findall(".//w:gridSpan", ns))
    assert any(node.get(f"{{{namespace}}}val") == "restart" for node in document_xml.findall(".//w:vMerge", ns))
    assert any(node.get(f"{{{namespace}}}val") in {None, "continue"} for node in document_xml.findall(".//w:vMerge", ns))
    for text in ("合并列表一", "合并列表二", "嵌套列表"):
        item_id = next(item for item, item_text in item_ids.items() if item_text == text)
        assert normalized["presentation"]["nodes"][item_id]["generated_anchor"] is False
    rerendered = render(normalized, tmp_path / "l09-rerendered.docx", template_path=Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx")
    table_render_map = [item for item in rerendered["render_map"].values() if item.get("kind") == "table"]
    assert sorted(item["table"] for item in table_render_map) == [1, 2]


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
    assert outer_list in document["children"]["root"]
    local_quote = next(
        child_id
        for child_id in document["children"][items["外部一"]]
        if document["containers"].get(child_id, {}).get("kind") == "quote"
    )
    assert local_quote in document["children"][items["外部一"]]
    operations = [
        operation
        for operation in report["model_audit"]["render_plan"]["operations"]
        if operation["kind"] == "list_item"
    ]
    assert {operation["node_id"] for operation in operations} == set(presentations)
    _assert_numbered_texts(output, {"引用一", "引用二", "引用三", "外部一", "外部二"})
    saved_texts = [paragraph.text for paragraph in Document(output).paragraphs]
    positions = [saved_texts.index(text) for text in ("外部一", "局部标题", "引用内一", "外部二")]
    assert positions == sorted(positions)
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


def test_saved_output_audit_checks_each_numbered_text(tmp_path: Path) -> None:
    output = tmp_path / "partial-numbering.docx"
    document = Document()
    document.add_paragraph("第一", style="List Number")
    document.add_paragraph("第二")
    document.save(output)

    findings = check_saved_output(
        output,
        {"expected_texts": ["第一", "第二"]},
        {
            "first": {"kind": "paragraph", "text": "第一", "style": "List Number", "numbered": True},
            "second": {"kind": "paragraph", "text": "第二", "style": "List Number", "numbered": True},
        },
    )

    assert findings["passed"] is False
    assert any(issue["code"] == "list_numbering_missing" for issue in findings["issues"])


def test_transaction_source_refs_invalidate_path_or_parser_changes() -> None:
    reference = {
        "source_id": "source",
        "part": "word/document.xml",
        "content_sha256": "sha256:" + "a" * 64,
        "node_path": ["body", 0, "w:p"],
        "parser_version": "1.1.0",
    }
    assert _source_ref_matches(reference, dict(reference))
    changed_path = dict(reference, node_path=["body", 1, "w:p"])
    changed_parser = dict(reference, parser_version="1.2.0")
    assert not _source_ref_matches(reference, changed_path)
    assert not _source_ref_matches(reference, changed_parser)
