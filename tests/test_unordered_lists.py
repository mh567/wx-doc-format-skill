from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.shared import Twips
from docx.table import Table
from docx.text.paragraph import Paragraph


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from docx_pipeline import infer_docx_role, parse_docx_to_model
from llm_enhancer import _build_phase_a_prompt, enhance_document_model, validate_patch
from model_normalization import normalize_document_model_simple
from unordered_lists import (
    annotate_unordered_candidates,
    leading_unordered_marker_candidate,
)


def _model(blocks: list[dict]) -> dict:
    return {"schema_version": "1.0", "document": {"blocks": blocks}}


def test_unknown_unicode_symbols_become_bounded_candidates() -> None:
    for text in ("■ 指纹认证", "◆ 人脸认证", "★ 策略配置", "✓ 已完成", "✦自定义项"):
        candidate = leading_unordered_marker_candidate(text)
        assert candidate is not None
        assert candidate["content_text"]

    for text in ("-1 表示未配置", "---", "【备注】说明", "1. 有序步骤"):
        assert leading_unordered_marker_candidate(text) is None


def test_candidate_annotation_records_repetition_and_layout() -> None:
    model = _model([
        {
            "id": "b1",
            "block_type": "body",
            "text": "■ 指纹认证",
            "source": {"layout": {"left_twips": 0}},
        },
        {
            "id": "b2",
            "block_type": "body",
            "text": "■ 人脸认证",
            "source": {"layout": {"left_twips": 0}},
        },
    ])
    report: dict = {}

    annotate_unordered_candidates(model, report)

    assert len(report["ambiguous_unordered_paragraphs"]) == 2
    candidate = model["document"]["blocks"][0]["source"]["unordered_candidate"]
    assert candidate["marker"] == "■"
    assert candidate["section_marker_count"] == 2
    assert candidate["layout"]["left_twips"] == 0


def test_unknown_marker_llm_decision_must_remain_unordered() -> None:
    model = _model([{
        "id": "b1",
        "block_type": "body",
        "text": "■ 指纹认证",
        "source": {
            "unordered_candidate": {
                "marker": "■",
                "content_text": "指纹认证",
            },
        },
    }])
    patch = {
        "schema_version": "1.0",
        "phase": "A",
        "decisions": [{
            "block_id": "b1",
            "operation": "retype",
            "from": {"block_type": "body"},
            "to": {"block_type": "list_item", "level": 0, "list_type": "lower_letter_paren"},
            "confidence": 0.9,
        }],
    }

    errors = validate_patch(patch, model, frozenset({"retype"}))

    assert any("unordered list_type" in error["message"] for error in errors)


def test_enhanced_unknown_marker_is_retyped_stripped_and_canonicalized() -> None:
    model = _model([{
        "id": "b1",
        "block_type": "body",
        "text": "◆ 指纹认证",
        "source": {
            "layout": {"left_twips": 0},
            "unordered_candidate": {
                "marker": "◆",
                "content_text": "指纹认证",
            },
        },
    }])
    response = """{
      "schema_version": "1.0",
      "phase": "A",
      "decisions": [{
        "block_id": "b1",
        "operation": "retype",
        "from": {"block_type": "body"},
        "to": {"block_type": "list_item", "level": 1, "list_type": "bullet_dot"},
        "confidence": 0.90
      }]
    }"""
    report = {"parse_report": {"ambiguous_unordered_paragraphs": 1}}

    enhanced = enhance_document_model(
        model,
        report,
        phase="list_detect",
        llm_call=lambda _: response,
    )
    normalized = normalize_document_model_simple(enhanced, report)
    block = normalized["document"]["blocks"][0]

    assert block["text"] == "指纹认证"
    # The two-level model keeps the level the LLM chose: the normalization layer
    # no longer rebases every unordered item to level 0 (that legacy list patch
    # was removed in T10), so the retyped level 1 stays and its marker is the
    # level-1 unordered type.
    assert block["level"] == 1
    assert block["list_type"] == "bullet_dot"


def test_list_detect_prompt_exposes_unknown_marker_evidence() -> None:
    model = _model([{
        "id": "b1",
        "block_type": "body",
        "text": "◆ 指纹认证",
        "source": {
            "unordered_candidate": {
                "marker": "◆",
                "unicode_name": "BLACK DIAMOND",
                "section_marker_count": 3,
                "layout": {"left_twips": 0},
            },
        },
    }])

    prompt = _build_phase_a_prompt(model)

    assert "BLACK DIAMOND" in prompt
    assert "repeated=3" in prompt
    assert "未知符号候选" in prompt


def test_docx_parse_collects_layout_and_unknown_marker_candidate() -> None:
    doc = Document()
    paragraph = doc.add_paragraph("■ 指纹认证")
    paragraph.paragraph_format.left_indent = Twips(720)

    model = parse_docx_to_model(
        Path("sample.docx"),
        doc,
        True,
        0,
        skill_version=lambda: "test",
        new_report=lambda: {},
        iter_blocks=lambda d: (
            Paragraph(child, d) if child.tag == qn("w:p") else Table(child, d)
            for child in d.element.body.iterchildren()
            if child.tag in (qn("w:p"), qn("w:tbl"))
        ),
        paragraph_class=Paragraph,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=lambda _: False,
        caption_pattern=None,
    )
    block = model["document"]["blocks"][0]

    assert block["block_type"] == "body"
    assert block["source"]["layout"]["left_twips"] == 720
    assert block["source"]["unordered_candidate"]["marker"] == "■"


def test_named_unordered_style_is_preserved_as_unordered_evidence() -> None:
    doc = Document()
    style = doc.styles.add_style("2.2二级列项-无编号", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("指纹认证", style=style)

    model = parse_docx_to_model(
        Path("sample.docx"),
        doc,
        True,
        0,
        skill_version=lambda: "test",
        new_report=lambda: {},
        iter_blocks=lambda d: (
            Paragraph(child, d) if child.tag == qn("w:p") else Table(child, d)
            for child in d.element.body.iterchildren()
            if child.tag in (qn("w:p"), qn("w:tbl"))
        ),
        paragraph_class=Paragraph,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=lambda _: False,
        caption_pattern=None,
    )
    block = model["document"]["blocks"][0]

    assert block["block_type"] == "list_item"
    assert block["level"] == 1
    assert block["list_type"] == "bullet_dot"

    normalized = normalize_document_model_simple(model, {})
    normalized_block = normalized["document"]["blocks"][0]
    # A named second-level unordered style stays at the level its style declares;
    # the T10 removal dropped the legacy ``resolve_list_hierarchy`` rebase that
    # forced every unordered item to the primary level.
    assert normalized_block["level"] == 1
    assert normalized_block["list_type"] == "bullet_dot"
