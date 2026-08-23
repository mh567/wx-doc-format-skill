from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Twips
from docx.text.paragraph import Paragraph


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from docx_pipeline import infer_docx_role, parse_docx_to_model
from docx_render import render_docx_direct
from list_detector import analyze_docx_lists
from model_normalization import normalize_document_model_simple
from note_semantics import audit_note_preservation
from reporting import add_risk_warnings, new_report
from template_profile import load_template_profile, template_numbering_ids
from text_utils import (
    CAPTION_PATTERN,
    caption_parts,
    heading_level_from_text,
    is_caption_text,
    iter_blocks,
    looks_like_code_sample_table,
    looks_like_list_item,
    strip_heading_marker,
    strip_list_marker,
)
from word_model_renderer import render_document_model


TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx"


def _add_single_level_abstract(doc: Document, abstract_id: int) -> None:
    root = doc.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi = OxmlElement("w:multiLevelType")
    multi.set(qn("w:val"), "singleLevel")
    abstract.append(multi)
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    level.append(start)
    num_fmt = OxmlElement("w:numFmt")
    num_fmt.set(qn("w:val"), "decimal")
    level.append(num_fmt)
    lvl_text = OxmlElement("w:lvlText")
    lvl_text.set(qn("w:val"), "%1.")
    level.append(lvl_text)
    abstract.append(level)
    root.insert(0, abstract)


def _add_bullet_abstract(doc: Document, abstract_id: int, marker: str = "•") -> None:
    root = doc.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    level.append(start)
    num_fmt = OxmlElement("w:numFmt")
    num_fmt.set(qn("w:val"), "bullet")
    level.append(num_fmt)
    lvl_text = OxmlElement("w:lvlText")
    lvl_text.set(qn("w:val"), marker)
    level.append(lvl_text)
    abstract.append(level)
    root.insert(0, abstract)


def _add_num(doc: Document, num_id: int, abstract_id: int, start: int) -> None:
    root = doc.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abstract_ref = OxmlElement("w:abstractNumId")
    abstract_ref.set(qn("w:val"), str(abstract_id))
    num.append(abstract_ref)
    if start != 1:
        override = OxmlElement("w:lvlOverride")
        override.set(qn("w:ilvl"), "0")
        start_override = OxmlElement("w:startOverride")
        start_override.set(qn("w:val"), str(start))
        override.append(start_override)
        num.append(override)
    root.append(num)


def _add_note_abstract(
    doc: Document,
    abstract_id: int,
    *,
    num_fmt_value: str,
    level_text_value: str,
    style_name: str,
) -> None:
    root = doc.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    level.append(start)
    num_fmt = OxmlElement("w:numFmt")
    num_fmt.set(qn("w:val"), num_fmt_value)
    level.append(num_fmt)
    level_text = OxmlElement("w:lvlText")
    level_text.set(qn("w:val"), level_text_value)
    level.append(level_text)
    p_style = OxmlElement("w:pStyle")
    p_style.set(qn("w:val"), doc.styles[style_name].style_id)
    level.append(p_style)
    abstract.append(level)
    root.insert(0, abstract)


def _bind_style_numbering(doc: Document, style_name: str, num_id: int) -> None:
    style = doc.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "0")
    num_pr.append(ilvl)
    num = OxmlElement("w:numId")
    num.set(qn("w:val"), str(num_id))
    num_pr.append(num)
    style._element.get_or_add_pPr().append(num_pr)


def _note_document() -> Document:
    doc = Document()
    _bind_style_numbering(doc, "3.1注-无编号注", 901)
    _bind_style_numbering(doc, "3.2注-有编号注", 902)
    _add_note_abstract(
        doc, 901, num_fmt_value="none", level_text_value="注：",
        style_name="3.1注-无编号注",
    )
    _add_note_abstract(
        doc, 902, num_fmt_value="decimal", level_text_value="注%1：",
        style_name="3.2注-有编号注",
    )
    _add_num(doc, 901, 901, 1)
    _add_num(doc, 902, 902, 1)
    doc.add_paragraph("无编号注的格式要求。", style="3.1注-无编号注")
    doc.add_paragraph("有编号注的格式要求。", style="3.2注-有编号注")
    doc.add_paragraph("表格正文字号的例外说明。", style="3.2注-有编号注")
    return doc


def _set_direct_num(paragraph, num_id: int) -> None:
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "0")
    num_pr.append(ilvl)
    num = OxmlElement("w:numId")
    num.set(qn("w:val"), str(num_id))
    num_pr.append(num)
    paragraph._p.get_or_add_pPr().append(num_pr)


def _append_numbered(doc: Document, text: str, num_id: int, style: str | None = None) -> None:
    paragraph = doc.add_paragraph(text, style=style)
    _set_direct_num(paragraph, num_id)


def test_hierarchical_heading_marker_is_atomic() -> None:
    for text in (
        "6.3 标准化注册时序流程",
        "6．4 指纹注册采集交互序列图",
        "6.5.1 后台入口与账号关联规则",
    ):
        assert heading_level_from_text(text) in {2, 3}
        assert looks_like_list_item(text) is False
        assert strip_list_marker(text) == text
        assert strip_heading_marker(text).startswith(("标准", "指纹", "后台"))


def test_single_level_decimal_markers_remain_lists() -> None:
    for text in ("1. 操作步骤", "2.配置管理", "3) 检查结果"):
        assert looks_like_list_item(text) is True
        assert strip_list_marker(text) in {"操作步骤", "配置管理", "检查结果"}


def test_decimal_like_body_text_is_not_promoted_to_heading() -> None:
    assert heading_level_from_text("1.2 协议用于以下场景。") is None
    assert heading_level_from_text("2026.07.19") is None
    assert heading_level_from_text("1.2%") is None


def test_ip_date_and_version_prefixes_are_not_promoted_to_heading() -> None:
    for text in (
        "192.168.1.1 地址配置为主节点",
        "10.0.0.1 IP地址",
        "1.2.3.4 地址规划",
        "2026.7.19 发布生产版本",
        "1.2.3 版本号说明如下",
    ):
        assert heading_level_from_text(text) is None

    assert heading_level_from_text("1.2.3 版本管理") == 3


def test_role_inference_prefers_hierarchical_heading_over_list_rules() -> None:
    doc = Document()
    paragraph = doc.add_paragraph("6.3 标准化注册时序流程")
    report: dict = {}

    text, style, role = infer_docx_role(paragraph, True, report)

    assert text == "6.3 标准化注册时序流程"
    assert style == "Heading 2"
    assert role == "heading"
    assert report["inferred_headings"][0]["source"] == "docx-hierarchical-text"


def test_heading_styled_toc_title_in_body_keeps_heading_role() -> None:
    doc = Document()
    paragraph = doc.add_paragraph("目次", style="Heading 2")

    text, style, role = infer_docx_role(paragraph, True, {})

    assert text == "目次"
    assert style == "Heading 2"
    assert role == "heading"


def test_plain_toc_title_without_heading_evidence_remains_body() -> None:
    doc = Document()
    paragraph = doc.add_paragraph("目次")

    _, style, role = infer_docx_role(paragraph, True, {})

    assert style is None
    assert role == "body"


def test_caption_recognition_requires_numeric_marker_or_separator() -> None:
    for text in (
        "图1-1 架构图",
        "图 5-1 架构图",
        "图：架构图",
        "表 1 接口清单",
    ):
        assert is_caption_text(text)
        assert caption_parts(text)[0] in {"figure", "table"}


def test_caption_recognition_rejects_words_starting_with_caption_labels() -> None:
    for text in (
        "图表组件、模板、主题与仪表盘，供门户与监控复用。",
        "图表组件",
        "图表1",
        "表单管理",
    ):
        assert not is_caption_text(text)


def test_infer_docx_role_keeps_graph_component_heading_and_body_text() -> None:
    document = Document()
    heading = document.add_paragraph("图表组件", style="Heading 6")
    body = document.add_paragraph("图表组件、模板、主题与仪表盘，供门户与监控复用。", style="Normal")

    assert infer_docx_role(heading, True, {}) == ("图表组件", "Heading 6", "heading")
    assert infer_docx_role(body, True, {}) == (
        "图表组件、模板、主题与仪表盘，供门户与监控复用。",
        None,
        "body",
    )


def test_invalid_numbering_cannot_be_revived_by_list_style() -> None:
    doc = Document()
    style_name = "1.1一级列项-编号"
    doc.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
    paragraph = doc.add_paragraph(
        "生物特征认证系统与IAM之间的逻辑接口如表1所示，具体接口见附录。",
        style=style_name,
    )
    _set_direct_num(paragraph, 0)
    report: dict = {}
    numbering = analyze_docx_lists(doc, report)[0]
    parse_report: dict = {}

    _, _, role = infer_docx_role(paragraph, True, parse_report, numbering)

    assert numbering["status"] == "ignored"
    assert "invalid_numbering_definition" in numbering["evidence"]
    assert role == "body"
    assert parse_report["suppressed_list_style_conflicts"]


def test_cross_num_id_instances_are_reconstructed_as_one_sequence() -> None:
    doc = Document()
    abstract_id = 100
    _add_single_level_abstract(doc, abstract_id)
    for num_id, start in ((100, 1), (101, 3), (102, 4), (103, 5)):
        _add_num(doc, num_id, abstract_id, start)
    _append_numbered(doc, "步骤1", 100)
    _append_numbered(doc, "步骤2", 100)
    _append_numbered(doc, "步骤3", 101)
    _append_numbered(doc, "步骤4", 102)
    for step in range(5, 14):
        _append_numbered(doc, f"步骤{step}", 103)
    report: dict = {}

    descriptors = analyze_docx_lists(doc, report)
    ordered = [descriptors[index] for index in sorted(descriptors)]

    assert len(ordered) == 13
    assert all(item["status"] == "detected" for item in ordered)
    assert all(item["group_size"] == 13 for item in ordered)
    assert [item["restart"] for item in ordered] == [True] + [False] * 12
    assert all(item["logical_num_ids"] == [100, 101, 102, 103] for item in ordered)
    assert report["source_lists"]["cross_num_id_group_count"] == 1


def test_cross_num_id_merge_requires_visible_sequence_continuity() -> None:
    doc = Document()
    abstract_id = 110
    _add_single_level_abstract(doc, abstract_id)
    _add_num(doc, 110, abstract_id, 1)
    _add_num(doc, 111, abstract_id, 1)
    _append_numbered(doc, "第一组步骤1", 110)
    _append_numbered(doc, "第一组步骤2", 110)
    _append_numbered(doc, "第二组步骤1", 111)
    _append_numbered(doc, "第二组步骤2", 111)
    report: dict = {}

    descriptors = analyze_docx_lists(doc, report)
    groups = [descriptors[index]["group_id"] for index in sorted(descriptors)]
    restarts = [descriptors[index]["restart"] for index in sorted(descriptors)]

    assert groups[0] == groups[1]
    assert groups[2] == groups[3]
    assert groups[0] != groups[2]
    assert restarts == [True, False, True, False]
    assert report["source_lists"]["cross_num_id_group_count"] == 0


def test_fragmented_bullet_num_ids_are_reconstructed_by_structure() -> None:
    doc = Document()
    abstract_id = 120
    _add_bullet_abstract(doc, abstract_id)
    for num_id in range(120, 125):
        _add_num(doc, num_id, abstract_id, 1)
        paragraph = doc.add_paragraph(f"并列角色{num_id}")
        paragraph.paragraph_format.left_indent = Twips(420)
        _set_direct_num(paragraph, num_id)
    report: dict = {}

    descriptors = analyze_docx_lists(doc, report)
    items = [descriptors[index] for index in sorted(descriptors)]

    assert all(item["status"] == "detected" for item in items)
    assert all(item["group_size"] == 5 for item in items)
    assert all(item["source_list_type"] == "bullet_dot" for item in items)
    assert [item["restart"] for item in items] == [True, False, False, False, False]
    assert report["source_lists"]["cross_num_id_group_count"] == 1
    assert "structural_bullet_continuity" in items[0]["evidence"]


def test_fragmented_bullets_do_not_cross_layout_boundary() -> None:
    doc = Document()
    abstract_id = 130
    _add_bullet_abstract(doc, abstract_id)
    for num_id, left in ((130, 0), (131, 720)):
        _add_num(doc, num_id, abstract_id, 1)
        paragraph = doc.add_paragraph(f"分组{num_id}")
        paragraph.paragraph_format.left_indent = Twips(left)
        _set_direct_num(paragraph, num_id)
    report: dict = {}

    descriptors = analyze_docx_lists(doc, report)

    assert descriptors[0]["group_id"] != descriptors[1]["group_id"]
    assert descriptors[0]["status"] == "ambiguous"
    assert descriptors[1]["status"] == "ambiguous"


def test_normalization_repairs_legacy_role_conflicts() -> None:
    source_model = {
        "document": {
            "blocks": [
                {
                    "id": "b1",
                    "block_type": "list_item",
                    "level": 1,
                    "list_type": "decimal_paren",
                    "text": "3 标准化注册时序流程",
                    "restart": True,
                    "source": {
                        "raw_text": "6.3 标准化注册时序流程",
                        "inferred_role": "list",
                    },
                },
                {
                    "id": "b2",
                    "block_type": "list_item",
                    "level": 0,
                    "list_type": "lower_letter_paren",
                    "text": "逻辑接口如表1所示。",
                    "restart": True,
                    "source": {
                        "raw_text": "逻辑接口如表1所示。",
                        "inferred_role": "list",
                        "numbering": {
                            "status": "ignored",
                            "evidence": ["invalid_numbering_definition"],
                        },
                    },
                },
            ]
        }
    }
    report: dict = {}

    normalized = normalize_document_model_simple(source_model, report)
    heading, body = normalized["document"]["blocks"]

    assert heading["block_type"] == "heading"
    assert heading["level"] == 2
    assert heading["text"] == "标准化注册时序流程"
    assert body["block_type"] == "body"
    assert body["text"] == "逻辑接口如表1所示。"
    repair_types = {item["type"] for item in report["model_normalization_repairs"]}
    assert "list_item_retyped_as_hierarchical_heading" in repair_types
    assert "invalid_list_style_retyped_as_body" in repair_types


def test_note_numbering_is_protected_before_generic_list_detection() -> None:
    doc = _note_document()
    report: dict = {}

    descriptors = analyze_docx_lists(doc, report)
    items = [descriptors[index] for index in sorted(descriptors)]

    assert [item["semantic_role"] for item in items] == [
        "note", "numbered_note", "numbered_note",
    ]
    assert all(item["status"] == "ignored" for item in items)
    assert all("protected_role" in item["evidence"] for item in items)
    inferred = [
        infer_docx_role(paragraph, True, {}, descriptors[index])[2]
        for index, paragraph in enumerate(doc.paragraphs)
    ]
    assert inferred == ["note", "numbered_note", "numbered_note"]


def test_note_roles_are_written_into_source_ast() -> None:
    doc = _note_document()
    numbering_context = analyze_docx_lists(doc, {})

    model = parse_docx_to_model(
        Path("notes.docx"), doc, True, 0,
        skill_version=lambda: "test",
        new_report=lambda: new_report("test"),
        iter_blocks=iter_blocks,
        paragraph_class=Paragraph,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=looks_like_code_sample_table,
        caption_pattern=CAPTION_PATTERN,
        numbering_context=numbering_context,
    )
    blocks = model["document"]["blocks"]

    assert [block["block_type"] for block in blocks] == ["body", "body", "body"]
    assert [block["role"] for block in blocks] == [
        "note", "numbered_note", "numbered_note",
    ]
    assert [block["source"]["role"] for block in blocks] == [
        "note", "numbered_note", "numbered_note",
    ]
    assert all(not block["text"].startswith("注") for block in blocks)

    normalized = normalize_document_model_simple(model, {})
    normalized_blocks = normalized["document"]["blocks"]
    assert [block["block_type"] for block in normalized_blocks] == [
        "body", "body", "body",
    ]
    assert [block["role"] for block in normalized_blocks] == [
        "note", "numbered_note", "numbered_note",
    ]


def test_note_roles_render_with_template_styles_and_pass_audit() -> None:
    source_doc = _note_document()
    numbering_context = analyze_docx_lists(source_doc, {})
    model = parse_docx_to_model(
        Path("notes.docx"), source_doc, True, 0,
        skill_version=lambda: "test",
        new_report=lambda: new_report("test"),
        iter_blocks=iter_blocks,
        paragraph_class=Paragraph,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=looks_like_code_sample_table,
        caption_pattern=CAPTION_PATTERN,
        numbering_context=numbering_context,
    )
    profile = load_template_profile(TEMPLATE)
    output_doc = Document(TEMPLATE)
    initial_count = len(output_doc.paragraphs)

    render_document_model(
        model, output_doc, {}, 0.8, "atLeast", template_numbering_ids(profile),
        template_profile=profile,
    )
    rendered = output_doc.paragraphs[initial_count:]
    audit = audit_note_preservation(output_doc, model, profile)

    assert [paragraph.style.name for paragraph in rendered] == [
        "3.1注-无编号注", "3.2注-有编号注", "3.2注-有编号注",
    ]
    assert audit["source_note_count"] == 3
    assert audit["note_count"] == 1
    assert audit["numbered_note_count"] == 2
    assert audit["passed"] is True


def test_direct_docx_renderer_consumes_body_semantic_role_from_ast() -> None:
    source_doc = _note_document()
    numbering_context = analyze_docx_lists(source_doc, {})
    model = parse_docx_to_model(
        Path("notes.docx"), source_doc, True, 0,
        skill_version=lambda: "test",
        new_report=lambda: new_report("test"),
        iter_blocks=iter_blocks,
        paragraph_class=Paragraph,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=looks_like_code_sample_table,
        caption_pattern=CAPTION_PATTERN,
        numbering_context=numbering_context,
    )
    profile = load_template_profile(TEMPLATE)
    output_doc = Document(TEMPLATE)
    initial_count = len(output_doc.paragraphs)

    render_docx_direct(
        source_doc, output_doc, {}, 0.8, "at-least", template_numbering_ids(profile),
        template_profile=profile,
        model=model,
    )
    rendered = output_doc.paragraphs[initial_count:]

    assert [paragraph.style.name for paragraph in rendered] == [
        "3.1注-无编号注", "3.2注-有编号注", "3.2注-有编号注",
    ]


def test_note_preservation_failure_enters_risk_review() -> None:
    report = new_report("test")
    report["note_preservation_audit"] = {
        "passed": False,
        "issues": [{"type": "note_rendered_style_mismatch"}],
    }

    add_risk_warnings(report, "at-least")

    assert any(
        warning["type"] == "note_preservation"
        for warning in report["risk_warnings"]
    )
