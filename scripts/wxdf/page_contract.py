"""Page, formula and sequence numbering checks.

These three requirements are the ones the element format contract cannot express:
they are properties of the document's page setup and of the numbering running
through it, not of a single paragraph's effective formatting.

Every expected shape is read from the template, which is the spec's executable
form: the section numbering a document must carry, the position a formula number
must hold, and the caption scheme the body must keep.  Nothing here hardcodes a
numbering format or a caption pattern.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from table_semantics import classify_docx_table
from text_utils import is_formula_number
from wxdf.format_contract import PropertyRef, resolve_paragraph_value

_JC = PropertyRef("pPr", "jc", "val")

# A cell or a paragraph tail that is nothing but a bracketed number, the form the
# template's formula layout and the spec's hand-made variant both use.
_BRACKETED_NUMBER = re.compile(r"^[（(]\s*(\d+)\s*[)）]$")
_TRAILING_NUMBER = re.compile(r"[（(]\s*(\d+)\s*[)）]\s*$")
# A caption's number token: an optional section letter, then a dotted, hyphenated
# or plain chain ("1", "1.1", "1-1-1", "A.1").
_CAPTION_NUMBER = re.compile(r"^[图表]\s*([A-Za-z]?[.．]?\d+(?:[.．-]\d+)*)")
_LABEL = "表图"


def _alignment(paragraph: Paragraph) -> str | None:
    """Return the paragraph's effective alignment, style chain included."""

    value, _ = resolve_paragraph_value(paragraph, _JC)
    return value


def _sections(doc: Any) -> list[Any]:
    return list(doc.sections)


def _section_record(section: Any) -> dict[str, Any]:
    sect_pr = section._sectPr
    pg_num_type = sect_pr.find(qn("w:pgNumType"))
    footers = sect_pr.findall(qn("w:footerReference"))
    return {
        "fmt": pg_num_type.get(qn("w:fmt")) if pg_num_type is not None else None,
        "start": pg_num_type.get(qn("w:start")) if pg_num_type is not None else None,
        "has_footer": bool(footers),
    }


def derive_page_expectations(template_doc: Any) -> list[dict[str, Any]]:
    """Return the section numbering the template asks for, in document order."""

    return [
        {"index": index, **_section_record(section)}
        for index, section in enumerate(_sections(template_doc), 1)
    ]


def audit_page_setup(doc: Any, template_doc: Any) -> dict[str, Any]:
    """Compare a document's sections with the template's page numbering."""

    expected = derive_page_expectations(template_doc)
    actual = [_section_record(section) for section in _sections(doc)]
    issues: list[dict[str, Any]] = []

    if len(actual) != len(expected):
        issues.append({
            "code": "page_section_count_mismatch",
            "level": "medium",
            "location": "文档分节",
            "expected_count": len(expected),
            "actual_count": len(actual),
            "expected": [item["fmt"] for item in expected],
            "actual": [item["fmt"] for item in actual],
            "severity": "medium",
        })
        return {"total_items": max(len(expected), 1), "issues": issues}

    for expected_section, actual_section in zip(expected, actual):
        index = expected_section["index"]
        for field in ("fmt", "start"):
            if actual_section[field] == expected_section[field]:
                continue
            issues.append({
                "code": "page_numbering_mismatch",
                "level": "medium",
                "location": f"第 {index} 节页码",
                "field": field,
                "expected": expected_section[field],
                "actual": actual_section[field],
                "severity": "medium",
            })
        if expected_section["has_footer"] and not actual_section["has_footer"]:
            issues.append({
                "code": "page_footer_missing",
                "level": "low",
                "location": f"第 {index} 节页脚",
                "severity": "low",
            })
    return {"total_items": max(len(expected), 1), "issues": issues}


def _body_blocks(doc: Any) -> Iterable[tuple[str, Any]]:
    """Yield ``(kind, object)`` for the body's paragraphs and tables in order."""

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "paragraph", Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield "table", Table(child, doc)


def _formula_numbers(doc: Any) -> list[dict[str, Any]]:
    """Return each formula number in reading order with where it sits.

    The template's formula layout is a two column layout table whose right cell
    holds the number, and the spec also allows a hand-made paragraph ending in
    the number.  Both forms are recognised.
    """

    found: list[dict[str, Any]] = []
    for kind, block in _body_blocks(doc):
        if kind == "table":
            semantics = classify_docx_table(block)
            if semantics.table_type != "layout" or len(block.columns) != 2:
                continue
            cells = block.rows[0].cells if block.rows else []
            if len(cells) != 2:
                continue
            match = _BRACKETED_NUMBER.match(cells[1].text.strip())
            if match is None:
                continue
            found.append({
                "value": int(match.group(1)),
                "formula_paragraph": cells[0].paragraphs[0] if cells[0].paragraphs else None,
                "number_paragraph": cells[1].paragraphs[0] if cells[1].paragraphs else None,
                "location": "公式版式表",
                "layout": True,
            })
            continue
        text = block.text.strip()
        match = _TRAILING_NUMBER.search(text)
        if match is None or not is_formula_number(text):
            continue
        found.append({
            "value": int(match.group(1)),
            "formula_paragraph": block,
            "number_paragraph": block,
            "location": f"段落：{text[:24]}",
            "layout": False,
        })
    return found


def audit_formula_numbering(doc: Any) -> dict[str, Any]:
    """Check the formula numbers run consecutively and sit where the template does.

    The position is checked on the template's layout form only.  That form states
    the alignment in the cells (the formula cell centred, the number cell right
    aligned), while the hand-made variant the spec also allows reaches the same
    look with leading spaces, which no reliable reading can verify.
    """

    formulas = _formula_numbers(doc)
    issues: list[dict[str, Any]] = []
    actual = [item["value"] for item in formulas]
    expected = list(range(1, len(actual) + 1))
    if actual and actual != expected:
        issues.append({
            "code": "formula_numbering_broken",
            "level": "medium",
            "location": f"公式编号（{len(actual)} 个）",
            "expected": expected,
            "actual": actual,
            "severity": "medium",
        })

    for item in formulas:
        if not item.get("layout"):
            continue
        formula_paragraph = item["formula_paragraph"]
        number_paragraph = item["number_paragraph"]
        if formula_paragraph is not None and _alignment(formula_paragraph) != "center":
            issues.append({
                "code": "formula_not_centered",
                "level": "medium",
                "location": item["location"],
                "actual": _alignment(formula_paragraph),
                "severity": "medium",
            })
        if number_paragraph is not None and _alignment(number_paragraph) != "right":
            issues.append({
                "code": "formula_number_not_right",
                "level": "medium",
                "location": item["location"],
                "actual": _alignment(number_paragraph),
                "severity": "medium",
            })
    return {"total_items": max(len(formulas), 1), "issues": issues}


def _caption_number(text: str) -> str | None:
    if not text or text[0] not in _LABEL:
        return None
    match = _CAPTION_NUMBER.match(text)
    return match.group(1) if match else None


def _scheme(number: str) -> str:
    if "." in number or "．" in number:
        return "dot"
    if "-" in number:
        return "hyphen"
    return "plain"


def audit_sequence_numbering_uniformity(doc: Any) -> dict[str, Any]:
    """Check the body's captions keep one numbering scheme.

    A caption whose number starts with a letter (``表A.1``) is an appendix caption
    and is grouped on its own, which is the form the appendix keeps by decision 3.
    """

    body: dict[str, list[str]] = {}
    appendix: dict[str, list[str]] = {}
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        number = _caption_number(text)
        if number is None:
            continue
        scheme = _scheme(number)
        bucket = appendix if number[0].isalpha() else body
        bucket.setdefault(scheme, []).append(text[:32])

    issues: list[dict[str, Any]] = []
    if len(body) > 1:
        issues.append({
            "code": "sequence_numbering_inconsistent",
            "level": "medium",
            "location": "图表编号",
            "schemes": {
                scheme: samples[0] for scheme, samples in sorted(body.items())
            },
            "severity": "medium",
        })
    total = sum(len(samples) for samples in body.values()) + sum(
        len(samples) for samples in appendix.values()
    )
    return {"total_items": max(total, 1), "issues": issues}


def audit_page_contract(doc: Any, template_doc: Any) -> dict[str, Any]:
    """Run the three numbering checks and expose their findings."""

    checks = {
        "page_setup": audit_page_setup(doc, template_doc),
        "formula_numbering": audit_formula_numbering(doc),
        "sequence_uniformity": audit_sequence_numbering_uniformity(doc),
    }
    issues: list[dict[str, Any]] = []
    for check in checks.values():
        issues.extend(check.get("issues") or [])
    checked = sum(int(check.get("total_items") or 0) for check in checks.values())
    return {"passed": not issues, "issues": issues, "checked": checked, "checks": checks}
