from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from appendix_semantics import (
    annotate_appendix_ranges,
    audit_appendix_contract,
    audit_appendix_preservation,
    parse_appendix_title,
)
from docx_pipeline import infer_docx_role, parse_docx_to_model
from docx_render import render_docx_direct
from list_detector import analyze_docx_lists
from model_normalization import normalize_document_model_simple
from reporting import add_risk_warnings, new_report
from template_profile import load_template_profile, template_numbering_ids
from text_utils import CAPTION_PATTERN, iter_blocks, looks_like_code_sample_table
from template_finalizer import insert_table_of_contents
from word_model_renderer import render_document_model


TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx"


def _clear_body(doc: Document) -> None:
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def _appendix_document() -> Document:
    doc = Document(TEMPLATE)
    _clear_body(doc)
    doc.add_paragraph("正文内容。")
    title = doc.add_paragraph(style="附录标题")
    title.add_run().add_break(WD_BREAK.LINE)
    title.add_run("（资料性）")
    title.add_run().add_break(WD_BREAK.LINE)
    title.add_run("附录示例")
    doc.add_paragraph("一级标题", style="附录一级标题")
    doc.add_paragraph("二级标题", style="附录二级标题")
    doc.add_paragraph("三级标题", style="附录三级标题")
    doc.add_paragraph("附录正文。")
    doc.add_paragraph("表A.1 附录表", style="Caption")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "名称"
    table.cell(0, 1).text = "内容"
    return doc


def _parse(doc: Document) -> dict:
    numbering_context = analyze_docx_lists(doc, {})
    return parse_docx_to_model(
        Path("appendix.docx"), doc, True, 0,
        skill_version=lambda: "test",
        new_report=lambda: new_report("test"),
        iter_blocks=iter_blocks,
        paragraph_class=Paragraph,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=looks_like_code_sample_table,
        caption_pattern=CAPTION_PATTERN,
        numbering_context=numbering_context,
    )


def _irregular_appendix_document(*, marker_style: str = "Normal") -> Document:
    doc = Document(TEMPLATE)
    _clear_body(doc)
    doc.add_paragraph("正文一章", style="Heading 1")
    doc.add_paragraph("正文内容。")
    doc.add_paragraph("附录", style=marker_style)
    doc.add_paragraph("接口说明", style="Heading 2")
    doc.add_paragraph("字段定义", style="Heading 3")
    doc.add_paragraph("附录正文。")
    return doc


def test_appendix_styles_are_protected_before_generic_list_detection() -> None:
    doc = _appendix_document()
    descriptors = analyze_docx_lists(doc, {})

    for source_position in (1, 2, 3, 4):
        assert descriptors[source_position]["status"] == "ignored"
        assert "protected_role" in descriptors[source_position]["evidence"]

    roles = [
        infer_docx_role(doc.paragraphs[index], True, {}, descriptors.get(index))[2]
        for index in range(1, 5)
    ]
    assert roles == [
        "appendix_title",
        "appendix_heading_1",
        "appendix_heading_2",
        "appendix_heading_3",
    ]


def test_appendix_range_and_heading_roles_are_written_in_source_ast() -> None:
    model = _parse(_appendix_document())
    document = model["document"]
    appendix = document["appendices"][0]
    appendix_blocks = [
        block for block in document["blocks"]
        if block.get("appendix_id") == "A"
    ]

    assert document["has_appendix"] is True
    assert appendix["start_source_position"] == 1
    assert appendix["end_source_position"] == 7
    assert appendix["classification"] == "资料性"
    assert appendix["title"] == "附录示例"
    assert appendix_blocks[0]["block_type"] == "appendix"
    assert appendix_blocks[0]["title_lines"] == ["", "（资料性）", "附录示例"]
    assert [block.get("role") for block in appendix_blocks[1:4]] == [
        "appendix_heading", "appendix_heading", "appendix_heading",
    ]
    assert [block.get("level") for block in appendix_blocks[1:4]] == [1, 2, 3]

    normalized = normalize_document_model_simple(model, {})
    normalized_appendix = normalized["document"]["appendices"][0]
    assert normalized_appendix["appendix_id"] == "A"
    assert normalized_appendix["start_source_position"] == 1
    assert normalized_appendix["end_source_position"] == 7
    assert normalized_appendix["block_count"] >= appendix["block_count"]


def test_appendix_renderers_use_template_styles_and_soft_breaks() -> None:
    source_doc = _appendix_document()
    model = normalize_document_model_simple(_parse(source_doc), {})
    profile = load_template_profile(TEMPLATE)

    direct_doc = Document(TEMPLATE)
    _clear_body(direct_doc)
    render_docx_direct(
        source_doc,
        direct_doc,
        {},
        0.69,
        "atLeast",
        template_numbering_ids(profile),
        template_profile=profile,
        model=model,
    )
    assert [paragraph.style.name for paragraph in direct_doc.paragraphs[2:6]] == [
        "附录标题", "附录一级标题", "附录二级标题", "附录三级标题",
    ]
    assert direct_doc.paragraphs[2].text == "\n（资料性）\n附录示例"
    assert any(
        element.get(qn("w:type")) == "page"
        for element in direct_doc.paragraphs[1]._p.iter(qn("w:br"))
    )
    assert any(paragraph.text == "表A.1 附录表" for paragraph in direct_doc.paragraphs)
    insert_table_of_contents(direct_doc, profile)
    assert audit_appendix_contract(direct_doc, profile)["passed"] is True
    assert audit_appendix_preservation(direct_doc, model, profile)["passed"] is True

    model_doc = Document(TEMPLATE)
    _clear_body(model_doc)
    render_document_model(
        model,
        model_doc,
        {},
        0.69,
        "atLeast",
        template_numbering_ids(profile),
        template_profile=profile,
    )
    assert [paragraph.style.name for paragraph in model_doc.paragraphs[2:6]] == [
        "附录标题", "附录一级标题", "附录二级标题", "附录三级标题",
    ]
    assert any(paragraph.text == "表A.1 附录表" for paragraph in model_doc.paragraphs)
    insert_table_of_contents(model_doc, profile)
    assert audit_appendix_contract(model_doc, profile)["passed"] is True


def test_appendix_audit_rejects_missing_shift_enter_structure() -> None:
    doc = _appendix_document()
    title = doc.paragraphs[1]
    title.text = "（资料性）附录示例"

    audit = audit_appendix_contract(doc)

    assert audit["passed"] is False
    assert any(issue["type"] == "appendix_title_soft_breaks" for issue in audit["issues"])
    assert any(issue["type"] == "appendix_title_line_structure" for issue in audit["issues"])


def test_appendix_audit_rejects_duplicate_page_boundaries() -> None:
    doc = _appendix_document()
    page_break = doc.paragraphs[0].add_run()
    page_break.add_break(WD_BREAK.PAGE)
    page_break.add_break(WD_BREAK.PAGE)

    audit = audit_appendix_contract(doc)

    assert any(issue["type"] == "appendix_duplicate_page_break_before" for issue in audit["issues"])


def test_appendix_text_reference_does_not_open_a_range() -> None:
    doc = Document()
    paragraph = doc.add_paragraph("具体要求见附录A。")

    assert infer_docx_role(paragraph, True, {})[2] == "body"

    model = {"document": {"blocks": [
        {"id": "b1", "block_type": "body", "text": paragraph.text, "source": {"source_position": 0}},
    ]}}
    annotate_appendix_ranges(model)
    assert model["document"]["has_appendix"] is False
    assert model["document"]["appendices"] == []


def test_appendix_title_parser_preserves_classification_and_title_lines() -> None:
    parsed = parse_appendix_title("附录 B\n（规范性）\n接口约束", 1)

    assert parsed == {
        "appendix_id": "B",
        "classification": "规范性",
        "title": "接口约束",
        "title_lines": ["", "（规范性）", "接口约束"],
        "soft_break_count": 2,
    }


def test_appendix_preservation_failure_enters_risk_review() -> None:
    report = new_report("test")
    report["appendix_preservation_audit"] = {
        "passed": False,
        "issues": [{"type": "appendix_heading_preservation", "level": 1}],
    }

    add_risk_warnings(report, "atLeast")

    assert any(
        warning["type"] == "appendix_preservation"
        for warning in report["risk_warnings"]
    )


def test_standalone_normal_appendix_marker_opens_region_without_optional_signals() -> None:
    model = _parse(_irregular_appendix_document())
    document = model["document"]
    appendix = document["appendices"][0]
    blocks = [block for block in document["blocks"] if block.get("appendix_id") == "A"]

    assert document["has_appendix"] is True
    assert appendix["classification"] is None
    assert appendix["title"] == ""
    assert appendix["page_break_before"] is True
    assert blocks[0]["block_type"] == "appendix"
    assert blocks[0]["layout"]["page_break_before"] is True
    assert blocks[0]["detection"]["confidence"] >= 0.70
    assert [(block.get("role"), block.get("level")) for block in blocks[1:3]] == [
        ("appendix_heading", 1),
        ("appendix_heading", 2),
    ]
    assert blocks[1]["appendix_heading_detection"]["source_heading_level"] == 2
    assert blocks[2]["appendix_heading_detection"]["source_heading_level"] == 3


def test_heading_two_appendix_marker_uses_relative_heading_hierarchy() -> None:
    model = _parse(_irregular_appendix_document(marker_style="Heading 2"))
    appendix_blocks = [
        block for block in model["document"]["blocks"]
        if block.get("appendix_id") == "A"
    ]

    assert appendix_blocks[0]["block_type"] == "appendix"
    assert [(block.get("role"), block.get("level")) for block in appendix_blocks[1:3]] == [
        ("appendix_heading", 1),
        ("appendix_heading", 2),
    ]


def test_numbered_nested_appendix_heading_is_rejected_but_terminal_marker_is_accepted() -> None:
    doc = Document(TEMPLATE)
    _clear_body(doc)
    doc.add_paragraph("格式要求", style="Heading 1")
    doc.add_paragraph("标题格式", style="Heading 2")
    doc.add_paragraph("附录", style="Heading 3")
    doc.add_paragraph("附录格式说明。")
    doc.add_paragraph("后续要求", style="Heading 3")
    doc.add_paragraph("正文结束。")
    doc.add_paragraph("附录")
    doc.add_paragraph("接口说明", style="Heading 1")
    doc.add_paragraph("附录内容。")

    model = _parse(doc)
    candidates = model["document"]["appendix_candidates"]

    assert len(candidates) == 2
    assert candidates[0]["accepted"] is False
    assert "nested_main_numbered_heading" in candidates[0]["negative_evidence"]
    assert candidates[1]["accepted"] is True
    assert model["document"]["appendices"][0]["start_source_position"] == 6

    report: dict = {}
    annotate_appendix_ranges(model, report)
    assert report["appendix_detection"]["candidate_count"] == 2
    assert report["appendix_detection"]["candidates"][0]["accepted"] is False


def test_multiple_plain_appendices_receive_sequential_ids() -> None:
    doc = Document(TEMPLATE)
    _clear_body(doc)
    doc.add_paragraph("正文", style="Heading 1")
    doc.add_paragraph("附录")
    doc.add_paragraph("第一部分", style="Heading 1")
    doc.add_paragraph("内容 A。")
    doc.add_paragraph("附录")
    doc.add_paragraph("第二部分", style="Heading 2")
    doc.add_paragraph("内容 B。")

    model = _parse(doc)

    assert [item["appendix_id"] for item in model["document"]["appendices"]] == ["A", "B"]
    assert [
        block["level"] for block in model["document"]["blocks"]
        if block.get("role") == "appendix_heading"
    ] == [1, 1]


def test_appendix_without_internal_headings_keeps_body_content() -> None:
    doc = Document(TEMPLATE)
    _clear_body(doc)
    doc.add_paragraph("正文", style="Heading 1")
    doc.add_paragraph("附录")
    doc.add_paragraph("只有附录正文。")
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "附录表格"

    model = _parse(doc)
    appendix_blocks = [
        block for block in model["document"]["blocks"]
        if block.get("appendix_id") == "A"
    ]

    assert [block["block_type"] for block in appendix_blocks] == ["appendix", "body", "table"]


def test_visible_appendix_numbers_are_retyped_and_removed_from_heading_text() -> None:
    doc = Document(TEMPLATE)
    _clear_body(doc)
    doc.add_paragraph("正文", style="Heading 1")
    doc.add_paragraph("附录")
    doc.add_paragraph("A.1 接口说明")
    doc.add_paragraph("A.1.1 字段定义")
    doc.add_paragraph("A.1.1.1 取值约束")

    model = _parse(doc)
    headings = [
        block for block in model["document"]["blocks"]
        if block.get("role") == "appendix_heading"
    ]

    assert [block["level"] for block in headings] == [1, 2, 3]
    assert [block["text"] for block in headings] == ["接口说明", "字段定义", "取值约束"]
