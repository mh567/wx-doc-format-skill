"""Format-contract tests.

The contract resolves each element's effective formatting from the document
(style chain, numbering level, direct properties) and compares it with the value
the template's own style resolves to.  Two consumers share it: the rendered
artifact's audit and the source document's review.

These tests pin the properties that make the sharing safe:

  * the template baseline is non-empty, stable and complete;
  * the output audit exempts the direct properties the renderer legitimately
    writes, so a real conversion raises no finding;
  * the output audit still catches a tampered paragraph, and never blocks;
  * the effective-value resolver applies the documented precedence;
  * a style name maps to the role whose contract governs it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from lxml import etree


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from modular_support import run_native_markdown_conversion
from template_profile import load_template_profile
from wxdf.format_contract import (
    CONTRACTS,
    PropertyRef,
    audit_document_format,
    check_contract_completeness,
    contract_for_style,
    derive_baseline,
    resolve_paragraph_value,
    resolve_role,
    retained_run_fingerprint,
    style_role_index,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


@pytest.fixture(scope="module")
def profile() -> dict:
    return load_template_profile(TEMPLATE)


@pytest.fixture(scope="module")
def template_doc() -> Document:
    return Document(TEMPLATE)


@pytest.fixture(scope="module")
def baseline(profile: dict, template_doc: Document) -> dict:
    return derive_baseline(profile, template_doc)


def test_the_template_satisfies_every_contract(profile: dict, template_doc: Document) -> None:
    assert check_contract_completeness(profile, template_doc) == []


def test_the_baseline_is_non_empty_complete_and_stable(
    profile: dict, template_doc: Document, baseline: dict
) -> None:
    assert set(baseline) == set(CONTRACTS)
    for role, values in baseline.items():
        assert values, f"{role} resolved no baseline"
        assert any(value is not None for value in values.values()), f"{role} resolved only unset values"
    assert derive_baseline(profile, template_doc) == baseline


def test_the_baseline_reports_the_template_values_not_hardcoded_ones(baseline: dict) -> None:
    body = baseline["body"]
    assert body["spacing.line"] == "300"
    assert body["spacing.lineRule"] == "auto"
    assert body["ind.firstLineChars"] == "200"
    assert body["rFonts.eastAsia"] == "宋体"
    assert body["rFonts.ascii"] == "Times New Roman"


def test_a_real_conversion_raises_no_format_finding(tmp_path: Path, profile: dict) -> None:
    result = run_native_markdown_conversion(tmp_path)
    assert result.returncode == 0, result.stderr
    output = Document(result.output)

    detail = audit_document_format(output, profile, output, consumer="audit")

    assert detail["checked"] > 0
    assert detail["issues"] == []
    assert detail["passed"] is True


def test_the_audit_catches_a_direct_override(tmp_path: Path, profile: dict) -> None:
    result = run_native_markdown_conversion(tmp_path)
    assert result.returncode == 0, result.stderr
    rendered = Document(result.output)
    target = rendered.add_paragraph("被篡改的正文", style="Normal")
    target.paragraph_format.line_spacing = 1.0
    for run in target.runs:
        run.font.size = Pt(10.5)
    tampered = tmp_path / "tampered-output.docx"
    rendered.save(tampered)

    detail = audit_document_format(Document(tampered), profile, Document(tampered), consumer="audit")

    codes = {issue["code"] for issue in detail["issues"]}
    assert "format_value_mismatch" in codes
    assert detail["passed"] is False


def _add_field_run(paragraph, font_name: str = "Courier New"):
    run = paragraph.add_run()
    run.font.name = font_name
    for kind in ("begin", "instrText", "separate", "text", "end"):
        child = OxmlElement("w:fldChar" if kind in {"begin", "separate", "end"} else "w:instrText" if kind == "instrText" else "w:t")
        if kind in {"begin", "separate", "end"}:
            child.set(qn("w:fldCharType"), kind)
        elif kind == "instrText":
            child.text = " REF _Ref123 \\h "
        else:
            child.text = "引用文本"
        run._r.append(child)
    return run


def _empty_template_document() -> Document:
    document = Document(TEMPLATE)
    for child in list(document.element.body):
        if child.tag != qn("w:sectPr"):
            document.element.body.remove(child)
    return document


def test_retained_field_font_override_is_skipped_only_with_matching_provenance(
    profile: dict,
) -> None:
    document = _empty_template_document()
    paragraph = document.add_paragraph(style="Normal")
    paragraph.add_run("正文")
    field_run = _add_field_run(paragraph)
    path = "paragraph:1"
    digest = retained_run_fingerprint(field_run._r)

    without_provenance = audit_document_format(document, profile, document)
    with_provenance = audit_document_format(
        document, profile, document, provenance={path: {"2": digest}}
    )
    review_with_provenance = audit_document_format(
        document, profile, document, consumer="review", provenance={path: {"2": digest}}
    )

    assert any(issue["property"] == "run[2].rFonts.ascii" for issue in without_provenance["issues"])
    assert not any(issue.get("property") == "run[2].rFonts.ascii" for issue in with_provenance["issues"])
    assert any(issue["property"] == "run[2].rFonts.ascii" for issue in review_with_provenance["issues"])


def test_retained_field_fingerprint_mismatch_and_adjacent_runs_still_audit(profile: dict) -> None:
    document = _empty_template_document()
    paragraph = document.add_paragraph(style="Normal")
    paragraph.add_run("正文")
    retained = _add_field_run(paragraph)
    adjacent = paragraph.add_run("邻接普通文本")
    adjacent.font.name = "Courier New"
    path = "paragraph:1"
    stale_digest = retained_run_fingerprint(retained._r)
    retained.font.size = Pt(13)

    detail = audit_document_format(
        document, profile, document,
        provenance={path: {"2": stale_digest}},
    )

    properties = {issue["property"] for issue in detail["issues"]}
    assert "run[2].sz" in properties
    assert "run[2].rFonts.ascii" in properties
    assert "run[3].rFonts.ascii" in properties


def test_retained_fingerprint_ignores_pretty_container_whitespace_and_keeps_leaf_text(
    tmp_path: Path,
) -> None:
    document = _empty_template_document()
    paragraph = document.add_paragraph(style="Normal")
    paragraph.add_run("正文")
    run = _add_field_run(paragraph)
    original = retained_run_fingerprint(run._r)
    generic_run = etree.fromstring(etree.tostring(run._r))
    assert retained_run_fingerprint(generic_run) == original
    etree._Element.text.__set__(run._r, "\n    ")
    pretty_source = tmp_path / "pretty-retained-run.docx"
    document.save(pretty_source)
    reopened = Document(pretty_source)
    reopened_run = reopened.paragraphs[0].runs[1]._r
    assert retained_run_fingerprint(reopened_run) == original

    etree._Element.text.__set__(run._r[-2], " ")
    assert retained_run_fingerprint(run._r) != original


def test_the_resolver_prefers_direct_then_numbering_then_style_then_defaults(template_doc: Document) -> None:
    document = Document(TEMPLATE)
    for child in list(document.element.body):
        if child.tag != qn("w:sectPr"):
            document.element.body.remove(child)
    paragraph = document.add_paragraph("正文", style="Normal")

    line = PropertyRef("pPr", "spacing", "line")
    value, source = resolve_paragraph_value(paragraph, line)
    assert (value, source) == ("300", "style:Normal")

    p_pr = paragraph._p.get_or_add_pPr()
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:line"), "240")
    spacing.set(qn("w:lineRule"), "auto")
    p_pr.append(spacing)
    value, source = resolve_paragraph_value(paragraph, line)
    assert (value, source) == ("240", "direct")


def test_the_role_index_maps_template_and_common_style_names(profile: dict) -> None:
    index = style_role_index(profile)
    # The template's own bindings.
    assert index["normal"] == "body"
    assert index["文档标题"] == "title"
    assert index["1.1一级列项-编号"] == "list_letter"
    # Common equivalents a document written with Word's stock styles uses.
    assert index["heading1"] == "heading_1"
    assert index["标题2"] == "heading_2"
    assert index["caption"] == "caption"
    assert index["bodytext"] == "body"
    assert index["toc2"] == "toc_entry_2"
    # A stock style a template binds as a fallback must not shadow the common
    # role: this template binds ``formula`` to ``Normal``, and Normal is the body
    # style.
    assert index["normal"] == "body"
    assert contract_for_style(profile, "Heading 2") is CONTRACTS["heading_2"]


def test_resolve_role_reads_evidence_not_only_style_names(profile: dict) -> None:
    """A role must be recoverable without the document using template styles."""

    document = Document()
    document.add_paragraph("第一章 概述", style="Heading 1")
    body = document.add_paragraph("正文文字。")
    document.add_paragraph("表1 说明")
    styled = document.add_paragraph("自定义样式标题")
    styled.style = document.styles.add_style("我的小标题", WD_STYLE_TYPE.PARAGRAPH)
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), "1")
    styled._p.get_or_add_pPr().append(outline)
    manual = document.add_paragraph("第二章 手工编号")
    manual.style = document.styles.add_style("我的正文样式", WD_STYLE_TYPE.PARAGRAPH)

    assert resolve_role(document.paragraphs[0], profile=profile) == {"role": "heading_1", "evidence": ("outline_level",)}
    assert resolve_role(body, profile=profile)["role"] == "body"
    assert resolve_role(document.paragraphs[2], profile=profile)["role"] == "caption"
    assert resolve_role(styled, profile=profile)["role"] == "heading_2"
    assert resolve_role(manual, profile=profile) == {"role": "heading_1", "evidence": ("heading_text",)}
    # Unnamed content with an unknown style and no other clue stays unclassified
    # rather than being guessed at.
    plain = document.add_paragraph("某某某某某某")
    plain.style = document.styles.add_style("某个无名样式", WD_STYLE_TYPE.PARAGRAPH)
    assert resolve_role(plain, profile=profile) is None


def test_resolve_role_treats_a_default_derived_style_as_body(profile: dict) -> None:
    document = Document()
    custom = document.styles.add_style("我的普通文本", WD_STYLE_TYPE.PARAGRAPH)
    custom.base_style = document.styles["Normal"]
    paragraph = document.add_paragraph("一段普通文字。")
    paragraph.style = custom

    assert resolve_role(paragraph, profile=profile)["role"] == "body"


def test_the_resolver_prefers_direct_over_style(template_doc: Document) -> None:
    document = Document(TEMPLATE)
    for child in list(document.element.body):
        if child.tag != qn("w:sectPr"):
            document.element.body.remove(child)
    paragraph = document.add_paragraph("正文", style="Normal")

    line = PropertyRef("pPr", "spacing", "line")
    value, source = resolve_paragraph_value(paragraph, line)
    assert (value, source) == ("300", "style:Normal")

    p_pr = paragraph._p.get_or_add_pPr()
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:line"), "240")
    spacing.set(qn("w:lineRule"), "auto")
    p_pr.append(spacing)
    value, source = resolve_paragraph_value(paragraph, line)
    assert (value, source) == ("240", "direct")


def test_the_resolver_reads_a_list_indent_from_its_numbering_level(template_doc: Document) -> None:
    """A list paragraph's indentation lives on the numbering level, not the style."""

    document = Document(TEMPLATE)
    for child in list(document.element.body):
        if child.tag != qn("w:sectPr"):
            document.element.body.remove(child)
    paragraph = document.add_paragraph("列项", style="1.1一级列项-编号")

    left = PropertyRef("pPr", "ind", "left")
    value, source = resolve_paragraph_value(paragraph, left)

    assert source == "numbering"
    assert value == "934"
