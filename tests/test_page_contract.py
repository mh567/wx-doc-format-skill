"""Page, formula and caption numbering tests.

These three requirements are properties of the page setup and of the numbering
running through the document, so they live in their own contract rather than in
the element format contract.  Every expected shape is read from the template, so
these tests pin:

  * the template's section numbering is derived, not hardcoded;
  * a document carrying the template's sections passes and a single-section one
    does not;
  * a broken formula sequence and a misplaced formula are reported;
  * caption numbering must keep one scheme in the body, while an appendix caption
    is grouped on its own;
  * the findings reach the report as advisory and never block publication.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from modular_support import run_native_markdown_conversion
from wxdf.page_contract import (
    audit_formula_numbering,
    audit_page_contract,
    audit_page_setup,
    audit_sequence_numbering_uniformity,
    derive_page_expectations,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
SPEC_DOCUMENT = Path("/Users/harris/Documents/zero项目/input/技术文件格式及书写要求.docx")


def _codes(detail: dict) -> list[str]:
    return [str(issue.get("code")) for issue in detail["issues"]]


def test_the_template_section_numbering_is_derived_not_hardcoded() -> None:
    expectations = derive_page_expectations(Document(TEMPLATE))

    assert [item["fmt"] for item in expectations] == ["upperRoman", "decimal"]
    assert [item["start"] for item in expectations] == ["1", "1"]
    assert all(item["has_footer"] for item in expectations)
    assert derive_page_expectations(Document(TEMPLATE)) == expectations


def test_a_document_carrying_the_template_sections_passes() -> None:
    doc = Document(TEMPLATE)

    assert audit_page_setup(doc, Document(TEMPLATE))["issues"] == []


def test_a_single_section_document_reports_the_section_count() -> None:
    doc = Document()

    detail = audit_page_setup(doc, Document(TEMPLATE))

    codes = _codes(detail)
    assert "page_section_count_mismatch" in codes
    issue = detail["issues"][0]
    assert issue["expected_count"] == 2
    assert issue["actual_count"] == 1


def test_the_real_spec_document_satisfies_the_page_contract() -> None:
    if not SPEC_DOCUMENT.is_file():
        pytest.skip("the specification document is not available")

    doc = Document(SPEC_DOCUMENT)

    assert audit_page_setup(doc, Document(TEMPLATE))["issues"] == []
    assert audit_sequence_numbering_uniformity(doc)["issues"] == []


def test_a_broken_formula_sequence_is_reported() -> None:
    doc = Document(TEMPLATE)
    table = next(
        item
        for item in doc.tables
        if len(item.columns) == 2 and item.rows[0].cells[1].text.strip() == "(2)"
    )
    paragraph = table.rows[0].cells[1].paragraphs[0]
    for run in list(paragraph.runs):
        run._r.getparent().remove(run._r)
    paragraph.add_run("(4)")

    detail = audit_formula_numbering(doc)

    broken = [issue for issue in detail["issues"] if issue["code"] == "formula_numbering_broken"]
    assert len(broken) == 1
    assert broken[0]["actual"] == [1, 4, 3]


def test_a_misplaced_formula_is_reported() -> None:
    doc = Document(TEMPLATE)
    table = next(
        item
        for item in doc.tables
        if len(item.columns) == 2 and item.rows[0].cells[1].text.strip() == "(1)"
    )
    table.rows[0].cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
    table.rows[0].cells[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT

    codes = _codes(audit_formula_numbering(doc))

    assert "formula_not_centered" in codes
    assert "formula_number_not_right" in codes


def test_mixing_caption_schemes_in_the_body_is_reported() -> None:
    doc = Document(TEMPLATE)
    doc.add_paragraph("表1.1 说明", style="caption")

    detail = audit_sequence_numbering_uniformity(doc)

    inconsistent = [issue for issue in detail["issues"] if issue["code"] == "sequence_numbering_inconsistent"]
    assert len(inconsistent) == 1
    assert set(inconsistent[0]["schemes"]) == {"dot", "plain"}


def test_an_appendix_caption_is_grouped_on_its_own() -> None:
    """The template's own captions mix 表1 with the appendix's 表A.1 and must pass."""

    detail = audit_sequence_numbering_uniformity(Document(TEMPLATE))

    assert detail["issues"] == []


def test_the_page_contract_runs_all_three_checks() -> None:
    detail = audit_page_contract(Document(TEMPLATE), Document(TEMPLATE))

    assert set(detail["checks"]) == {"page_setup", "formula_numbering", "sequence_uniformity"}
    assert detail["checked"] > 0


def test_a_current_conversion_reproduces_the_template_sections(tmp_path: Path) -> None:
    """The delivered artifact must carry the template's section numbering.

    The template fragment supplies the front matter's section break, so a
    conversion reproduces both sections.  This guards that behaviour and shows
    the page findings ride on the report's advisory key rather than blocking.
    """

    result = run_native_markdown_conversion(tmp_path)
    assert result.returncode == 0, result.stderr

    output = Document(result.output)
    detail = audit_page_setup(output, Document(TEMPLATE))
    assert detail["issues"] == []

    report = json.loads(result.report.read_text(encoding="utf-8"))
    saved_output = report["saved_output_audit"]
    assert saved_output["passed"] is True
    assert saved_output["issues"] == []
    assert all(
        item.get("severity") in {"medium", "low"}
        for item in (saved_output.get("format_issues") or [])
    )
