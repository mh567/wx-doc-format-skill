"""Input-document review tests.

The reviewer audits a source DOCX against the template's requirements before
conversion, and it does so without assuming the document uses the template's
styles: a paragraph is classified into a role from evidence (outline level,
numbering, note/caption/appendix semantics, style names, text) and its effective
formatting is then compared with that role's expectation.  These tests pin:

  * the style check reports a style the template does not define and does not
    report the template's own styles (including headings and TOC styles);
  * the format check reports a deviation the author introduced directly, allows
    the emphasis the spec permits, and stays independent of the style check;
  * a document written with Word's stock styles or a custom style name is still
    classified and still reports its format problems;
  * the outline check reports a declared level jump;
  * the caption check reports a data table without a caption;
  * the TOC check reports a missing TOC field;
  * the scoring engine turns the per-dimension findings into a 100-point score
    and a grade, weighting the dimensions it declares;
  * both report writers emit their files.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from review_report import write_review_report, write_review_report_html
from wxdf.input_reviewer import review_input_document
from wxdf.review_scoring import build_scoring_result, score_to_grade


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


def _build_document(path: Path) -> None:
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    document.add_paragraph("一级小节", style="Heading 3")
    document.add_paragraph("正文内容")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "表头"
    document.styles.add_style("自定义样式", WD_STYLE_TYPE.PARAGRAPH)
    document.add_paragraph("使用了自定义样式", style="自定义样式")
    document.save(path)


def _issue_codes(result: dict) -> list[str]:
    return [str(issue.get("code")) for issue in result["issues"]]


def test_review_reports_custom_style_heading_jump_and_missing_toc(tmp_path: Path) -> None:
    source = tmp_path / "review-source.docx"
    _build_document(source)

    result = review_input_document(source, TEMPLATE)

    codes = _issue_codes(result)
    assert "missing_template_style" in codes
    assert "heading_level_jump" in codes
    assert "table_missing_caption" in codes
    assert "toc_missing" in codes
    assert result["score"] < 100
    assert 0 <= result["score"] <= 100
    assert result["grade"] in {"优", "良", "中", "及格", "差"}


def test_review_does_not_flag_template_styles(tmp_path: Path) -> None:
    source = tmp_path / "clean-source.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    document.add_paragraph("小节", style="Heading 2")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    style_issues = [issue for issue in result["issues"] if issue.get("code") == "missing_template_style"]
    assert style_issues == []


def test_review_locates_the_heading_jump_between_the_right_levels(tmp_path: Path) -> None:
    source = tmp_path / "jump.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    document.add_paragraph("跳级小节", style="Heading 4")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    jumps = [issue for issue in result["issues"] if issue.get("code") == "heading_level_jump"]
    assert len(jumps) == 1
    assert jumps[0]["from_level"] == 1
    assert jumps[0]["to_level"] == 4
    assert jumps[0]["missing_level"] == 2


def test_scoring_weights_dimensions_and_maps_grades() -> None:
    clean = {
        dimension: {"total_items": 10, "issues": []}
        for dimension in (
            "format_conformance",
            "heading_hierarchy",
            "list_structure",
            "table_format",
            "toc_structure",
            "appendix_structure",
            "caption",
            "note_structure",
        )
    }
    perfect = build_scoring_result(clean)
    assert perfect["score"] == 100.0
    assert perfect["grade"] == "优"
    assert perfect["passed"] is True

    penalized = dict(clean)
    penalized["toc_structure"] = {"total_items": 1, "issues": [{"level": "critical"}]}
    scored = build_scoring_result(penalized)
    assert scored["score"] < perfect["score"]
    assert score_to_grade(0.0) == "差"


def test_severity_orders_the_issue_list(tmp_path: Path) -> None:
    source = tmp_path / "ordered.docx"
    _build_document(source)

    result = review_input_document(source, TEMPLATE)

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    levels = [order[str(issue.get("level"))] for issue in result["issues"]]
    assert levels == sorted(levels)


def test_report_writers_emit_markdown_and_html(tmp_path: Path) -> None:
    source = tmp_path / "report-source.docx"
    _build_document(source)
    result = review_input_document(source, TEMPLATE)

    markdown_path = tmp_path / "review.md"
    html_path = tmp_path / "review.html"
    write_review_report(result, markdown_path)
    write_review_report_html(result, html_path)

    assert markdown_path.read_text(encoding="utf-8").startswith("# 文档格式审查报告")
    html = html_path.read_text(encoding="utf-8")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert f"{result['score']}" in html


def test_review_reports_a_manual_heading_number(tmp_path: Path) -> None:
    source = tmp_path / "manual-number.docx"
    document = Document()
    document.add_paragraph("第一章 概述", style="Heading 1")
    document.add_paragraph("正文内容")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    manual = [issue for issue in result["issues"] if issue.get("code") == "heading_manual_number"]
    assert len(manual) == 1
    assert "第一章 概述" in manual[0]["text"]


def test_review_reports_a_list_that_does_not_restart_after_a_heading(tmp_path: Path) -> None:
    source = tmp_path / "list-restart.docx"
    document = Document()
    document.add_paragraph("第一章", style="Heading 1")
    document.add_paragraph("a) 第一项")
    document.add_paragraph("b) 第二项")
    document.add_paragraph("第二章", style="Heading 1")
    document.add_paragraph("c) 应重启的项")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    restart = [issue for issue in result["issues"] if issue.get("code") == "list_numbering_not_restarted"]
    assert len(restart) == 1
    assert restart[0]["actual_ordinal"] == 3


def test_review_reports_a_note_without_a_note_style(tmp_path: Path) -> None:
    source = tmp_path / "note.docx"
    document = Document()
    document.add_paragraph("正文内容")
    document.add_paragraph("注：这是一条注", style="Normal")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    notes = [issue for issue in result["issues"] if issue.get("code") == "note_style_missing"]
    assert len(notes) == 1
    assert notes[0]["actual_style"] == "Normal"


def test_review_does_not_flag_appendix_when_none_is_declared(tmp_path: Path) -> None:
    source = tmp_path / "no-appendix.docx"
    document = Document()
    document.add_paragraph("第一章", style="Heading 1")
    document.add_paragraph("正文内容")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    appendix = [issue for issue in result["issues"] if "appendix" in str(issue.get("code"))]
    assert appendix == []


def test_review_accepts_a_properly_marked_appendix(tmp_path: Path) -> None:
    source = tmp_path / "appendix.docx"
    document = Document()
    document.add_paragraph("第一章", style="Heading 1")
    document.add_paragraph("正文内容")
    document.add_paragraph("附录A 术语定义", style="Heading 1")
    document.add_paragraph("术语内容")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    appendix = [issue for issue in result["issues"] if "appendix" in str(issue.get("code"))]
    assert appendix == []


def _add_toc_field(document: Document, *, title: str, max_level: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document.add_paragraph(title)
    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = f'TOC \\o "1-{max_level}" \\h \\z'
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instruction, end):
        run._r.append(node)


def test_review_reports_a_toc_field_whose_depth_exceeds_the_template(tmp_path: Path) -> None:
    source = tmp_path / "toc-depth.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    _add_toc_field(document, title="目  次", max_level=6)
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    deep = [issue for issue in result["issues"] if issue.get("code") == "toc_level_too_deep"]
    assert len(deep) == 1
    assert deep[0]["actual_level"] == 6
    assert deep[0]["expected_level"] == 4


def test_review_reports_a_toc_title_off_the_template_style(tmp_path: Path) -> None:
    source = tmp_path / "toc-title.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    _add_toc_field(document, title="目  次", max_level=4)
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    title = [issue for issue in result["issues"] if issue.get("code") == "toc_title_style"]
    assert len(title) == 1
    assert title[0]["expected_style"] == "目录标题"


def _template_based_document() -> Document:
    """Return a blank document whose styles and defaults come from the template."""

    from docx.oxml.ns import qn

    document = Document(TEMPLATE)
    for child in list(document.element.body):
        if child.tag != qn("w:sectPr"):
            document.element.body.remove(child)
    return document


def _format_issues(result: dict) -> list[dict]:
    return [
        issue
        for issue in result["issues"]
        if str(issue.get("code", "")).startswith("format_")
    ]


def test_review_accepts_a_paragraph_that_only_uses_its_style(tmp_path: Path) -> None:
    source = tmp_path / "styled.docx"
    document = _template_based_document()
    document.add_paragraph("这是一段完全依赖样式的正文。", style="Normal")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    assert _format_issues(result) == []


def test_review_reports_a_direct_line_spacing_override(tmp_path: Path) -> None:
    source = tmp_path / "line-spacing.docx"
    document = _template_based_document()
    paragraph = document.add_paragraph("正文段落", style="Normal")
    paragraph.paragraph_format.line_spacing = 1.0
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    mismatches = [issue for issue in _format_issues(result) if issue["code"] == "format_value_mismatch"]
    assert mismatches
    assert "spacing.line" in mismatches[0]["properties"]
    assert mismatches[0]["level"] in {"critical", "high", "medium"}


def test_review_reports_a_direct_font_size_override_on_a_run(tmp_path: Path) -> None:
    from docx.shared import Pt

    source = tmp_path / "run-size.docx"
    document = _template_based_document()
    paragraph = document.add_paragraph("正文段落", style="Normal")
    for run in paragraph.runs:
        run.font.size = Pt(10.5)
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    overrides = [issue for issue in _format_issues(result) if issue["code"] == "format_run_direct_override"]
    assert overrides


def test_review_allows_bold_emphasis_on_a_run(tmp_path: Path) -> None:
    source = tmp_path / "emphasis.docx"
    document = _template_based_document()
    paragraph = document.add_paragraph("正文段落", style="Normal")
    for run in paragraph.runs:
        run.bold = True
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    overrides = [issue for issue in _format_issues(result) if issue["code"] == "format_run_direct_override"]
    assert overrides == []


def test_review_reports_format_problems_in_a_word_default_document(tmp_path: Path) -> None:
    """A document no layer needs the template for must still be judged.

    Word's stock template formats body text as 11pt Calibri with no first line
    indent and single spacing, so every one of those is a spec deviation.  The
    review must report them without the document using any template style.
    """

    source = tmp_path / "word-default.docx"
    document = Document()
    document.add_paragraph("文档标题示例", style="Heading 1")
    document.add_paragraph("这是一段正文文字，用于检查格式。")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    mismatches = [issue for issue in _format_issues(result) if issue["code"] == "format_value_mismatch"]
    assert mismatches
    assert {"body", "heading_1"} <= {issue.get("role") for issue in mismatches}
    properties = {name for issue in mismatches for name in issue.get("properties", [])}
    assert {"sz", "spacing.line", "ind.firstLineChars"} <= properties


def test_review_reports_a_heading_under_a_custom_style(tmp_path: Path) -> None:
    """A heading is identified by its outline level, not by a known style name."""

    source = tmp_path / "custom-heading.docx"
    document = Document()
    styled = document.add_paragraph("自定义样式标题")
    styled.style = document.styles.add_style("我的小标题", WD_STYLE_TYPE.PARAGRAPH)
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), "1")
    styled._p.get_or_add_pPr().append(outline)
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    mismatches = [issue for issue in _format_issues(result) if issue["code"] == "format_value_mismatch"]
    assert any(issue.get("role") == "heading_2" for issue in mismatches)


def test_the_style_check_and_the_format_check_are_independent(tmp_path: Path) -> None:
    """An unknown style name is reported by one check, the formatting by the other.

    A style the template does not define is a style-name problem; a paragraph
    whose effective formatting still matches the spec has no format problem.  The
    two findings must not stand in for one another.
    """

    source = tmp_path / "renamed-style.docx"
    document = _template_based_document()
    custom = document.styles.add_style("我的段落样式", WD_STYLE_TYPE.PARAGRAPH)
    custom.base_style = document.styles["Normal"]
    paragraph = document.add_paragraph("格式正确但样式名不在模板里。")
    paragraph.style = custom
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    codes = [issue["code"] for issue in result["issues"]]
    assert "missing_template_style" in codes
    assert [issue for issue in _format_issues(result) if issue["code"] == "format_value_mismatch"] == []


def test_review_reports_page_formula_and_caption_numbering(tmp_path: Path) -> None:
    """The numbering dimension covers the page setup, formulas and captions."""

    source = tmp_path / "numbering.docx"
    document = Document(TEMPLATE)
    table = next(
        item
        for item in document.tables
        if len(item.columns) == 2 and item.rows[0].cells[1].text.strip() == "(2)"
    )
    paragraph = table.rows[0].cells[1].paragraphs[0]
    for run in list(paragraph.runs):
        run._r.getparent().remove(run._r)
    paragraph.add_run("(4)")
    document.add_paragraph("表1.1 说明", style="caption")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    codes = {issue["code"] for issue in result["issues"]}
    assert "formula_numbering_broken" in codes
    assert "sequence_numbering_inconsistent" in codes
    assert result["dimension_scores"]["numbering_page"] < 100


def test_review_reports_a_document_that_is_not_sectioned_like_the_template(tmp_path: Path) -> None:
    source = tmp_path / "single-section.docx"
    document = Document()
    document.add_paragraph("一段正文。")
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    codes = {issue["code"] for issue in result["issues"]}
    assert "page_section_count_mismatch" in codes


def test_review_reports_a_figure_note_below_its_caption(tmp_path: Path) -> None:
    """The spec puts a figure's note above the figure title, not below it."""

    for name, paragraphs, expected in (
        ("below", ("图1 说明", "注1：这是图注"), True),
        ("above", ("注1：这是图注", "图1 说明"), False),
    ):
        source = tmp_path / f"{name}.docx"
        document = Document()
        document.add_paragraph("正文")
        for text in paragraphs:
            document.add_paragraph(text)
        document.save(source)

        result = review_input_document(source, TEMPLATE)

        found = [issue for issue in result["issues"] if issue["code"] == "figure_note_below_caption"]
        assert bool(found) is expected, name


def test_review_refuses_an_input_it_cannot_audit(tmp_path: Path) -> None:
    """An unusable input is a caller error with a stable code, not a crash.

    The review only reads DOCX, so a Markdown path and a file that is not a
    document at all both have to stop with a diagnostic instead of an unexpected
    exit.
    """

    from main import main

    markdown = tmp_path / "input.md"
    markdown.write_text("# 标题\n", encoding="utf-8")
    with pytest.raises(SystemExit) as unsupported:
        main(["--review", "--input", str(markdown), "--template", str(TEMPLATE)])
    assert unsupported.value.code == 2

    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(SystemExit) as failed:
        main(["--review", "--input", str(broken), "--template", str(TEMPLATE)])
    assert failed.value.code == 1


def test_a_template_requirement_is_not_softened_by_any_later_step(tmp_path: Path) -> None:
    """A deviation is reported at the requirement's own severity.

    The table format contract is a template requirement, so its findings are
    reported like any other rather than downgraded on the grounds that some later
    step would rewrite the document.  The review judges the document in front of
    it, independently of any pipeline.
    """

    source = tmp_path / "tables.docx"
    document = _template_based_document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "表头"
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    contract = [issue for issue in result["issues"] if issue["code"] == "table_layout"]
    assert contract
    assert contract[0]["level"] == "medium"
    # No finding and no risk note may lean on what some later step would do.
    for issue in result["issues"]:
        assert "转换器" not in str(issue.get("suggestion") or "")
    for warning in result["risk_warnings"]:
        assert "转换器" not in warning


def test_review_reports_an_authored_body_alignment(tmp_path: Path) -> None:
    """The template's justification is a requirement the review enforces.

    The template's Normal style asks for justified body text, so a paragraph the
    author aligned another way is a deviation.  The check belongs to the review
    and not to the artifact audit: the renderer only writes a direct alignment
    for the paragraphs whose presentation owns one (an image paragraph or a
    formula number), so an ordinary body paragraph comes out justified whatever
    the author chose.
    """

    from docx.enum.text import WD_ALIGN_PARAGRAPH

    source = tmp_path / "aligned.docx"
    document = _template_based_document()
    paragraph = document.add_paragraph("这段正文被作者设为左对齐。", style="Normal")
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    document.save(source)

    result = review_input_document(source, TEMPLATE)

    mismatches = [issue for issue in _format_issues(result) if issue["code"] == "format_value_mismatch"]
    body = [issue for issue in mismatches if issue.get("role") == "body"]
    assert body
    assert "jc" in body[0]["properties"]
