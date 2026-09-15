"""T07 heading / body anti-ambiguity end-to-end tests.

This file owns the T07 anti-ambiguity contract:

  * a heading, including one whose chapter number is typed by hand or whose
    paragraph style is a chapter-heading style, must never become a list item;
  * when the evidence genuinely splits (a heading structure against a confirmed
    numbering instance) the block is reported as ambiguous instead of being
    silently promoted, and normal mode degrades with a diagnostic;
  * the protected list covers easily confused content: IP addresses, version
    numbers, dates, formulas, legal clauses and API paths.

Every test is an end-to-end test: it builds a real DOCX or a SourceFacts
fixture, runs the native parser and the list semantics, and asserts the
resulting list model or the protection decision.  The red-light run is recorded
in ``docs/execution-v6/evidence/T07.md``.

Fixture naming follows the T01 convention ``t07_<scenario>_<variant>``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.protected import protected_text_reason


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


# ---------------------------------------------------------------------------
# SourceFacts fixtures
# ---------------------------------------------------------------------------

def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


def _paragraph(unit_id: str, index: int, text: str, *, style: str | None = None) -> dict:
    unit = {
        "kind": "paragraph",
        "source_ref": _ref(index),
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
    }
    if style is not None:
        unit["style"] = style
    return unit


def _facts(containers: dict, children: dict, units: dict, evidence: dict) -> dict:
    return {
        "facts_schema_version": "1.0",
        "source": {"source_id": "source", "source_type": "markdown", "content_sha256": "sha256:" + "a" * 64},
        "parser": {"id": "test", "version": "1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": units,
        "containers": containers,
        "children": children,
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def _codes(result: dict) -> set[str]:
    return {str(item.get("code")) for item in result["diagnostics"]}


def _downgrade_codes(result: dict) -> set[str]:
    return {str(item.get("code")) for item in result["downgrades"]}


def _item_ids(result: dict) -> set[str]:
    return {str(item["item_id"]) for item in result["items"]}


# ---------------------------------------------------------------------------
# Work item 1: a confirmed-numbering heading reports ambiguity, not a list item
# ---------------------------------------------------------------------------

def test_t07_detected_numbering_heading_reports_ambiguity_not_list_item() -> None:
    """A heading that also carries a confirmed numbering instance stays body text.

    The paragraph text ``6.3 概述`` is a manually typed chapter number, so the
    block reads as a heading, while its evidence carries a resolved numbering
    definition (``numbering_status: "detected"``) that reads as a list item.
    README constraint 6 makes this decidable: a heading ends the preceding list,
    so the heading is protected and never promoted.  Before T07 the confirmed
    numbering short-circuited protection and the heading became a list item
    silently; now the protection wins, the decision is recorded as an info
    downgrade, and no Resolver tie is manufactured, so an ordinary document
    still completes.
    """

    units = {
        "h1": _paragraph("h1", 0, "6.3 概述"),
        "b1": _paragraph("b1", 1, "终端用户负责发起认证请求"),
    }
    children = {"root": ["h1", "b1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "h1": {
            "marker_kind": "ordered",
            "marker_text": "6.3",
            "ordinal": 6,
            "explicit_depth": 0,
            "numbering_status": "detected",
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "b1": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None, "num_fmt": None, "p_style_ref": None},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert "h1" not in _item_ids(result), (
        f"a chapter-number heading must never become a list item; got {sorted(_item_ids(result))}"
    )
    assert "protected_list_like_text" in _downgrade_codes(result), result["downgrades"]
    assert "ambiguous_list_state" not in _codes(result), result["diagnostics"]
    assert result["state_unresolved"] == [], result["state_unresolved"]
    assert result["status"] == "completed", result["status"]


def test_t07_heading_without_confirmation_stays_a_plain_protected_block() -> None:
    """A manual chapter number with no confirmed numbering is protected quietly.

    This is the already-correct half of the same shape: with no resolved
    numbering instance the manual-heading signal wins outright, so the block is
    OUTSIDE_LIST and stays out of the list.  It guards against turning every
    heading into a noisy ambiguity.
    """

    units = {"h1": _paragraph("h1", 0, "6.3 概述")}
    children = {"root": ["h1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "h1": {
            "marker_kind": "ordered",
            "marker_text": "6.3",
            "ordinal": 6,
            "explicit_depth": 0,
            "numbering_definition_ref": None,
            "num_fmt": None,
            "p_style_ref": None,
        }
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert "h1" not in _item_ids(result), result["items"]


# ---------------------------------------------------------------------------
# Work item 3 + 1: easily confused content stays body text
# ---------------------------------------------------------------------------

def _marker_evidence(index: int) -> dict:
    """Marker evidence with no numbering definition, like a DOCX visible marker.

    The DOCX adapter emits this shape for a paragraph with a manually visible
    marker and no ``w:numPr`` (see ``parsers._docx_visible_list_marker``): the
    marker is seen but no numbering instance confirms it.  The shape is
    deliberately free of ``numbering_definition_ref`` so the block is not routed
    through the separate low-confidence tie in ``lists``.
    """

    return {
        "marker_kind": "ordered",
        "marker_text": "1.",
        "ordinal": index,
        "explicit_depth": 0,
        "num_fmt": None,
        "p_style_ref": None,
    }


def test_t07_confusable_content_is_protected_from_list_promotion() -> None:
    """IP / legal / API-path / formula text must not become list items.

    These are the easily confused shapes a marker scanner flags as list items.
    They carry a marker but no confirmed numbering, so they must stay body text
    and each must be recorded with its protection reason.  Before T07 only the
    IP shape was (mis)protected as a manual heading; the legal-clause, API-path
    and formula shapes had no protection rule and became list items.
    """

    cases = {
        "ip": "192.168.1.1 主节点地址",
        "legal": "第一条 本系统适用于生产环境。",
        "api": "/api/v1/orders/{id}",
        "formula": "y = kx + b",
    }
    units = {key: _paragraph(key, index, text) for index, (key, text) in enumerate(cases.items())}
    children = {"root": list(cases)}
    containers = {"root": {"kind": "document"}}
    evidence = {key: _marker_evidence(index + 1) for index, key in enumerate(cases)}

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert _item_ids(result) == set(), (
        f"confusable content must stay body text; got items {sorted(_item_ids(result))}"
    )
    protected = {
        str(item["node_id"]): str(item.get("message"))
        for item in result["downgrades"]
        if item["code"] == "protected_list_like_text"
    }
    assert protected.get("ip") == "ip_address", protected
    assert protected.get("legal") == "legal_clause", protected
    assert protected.get("api") == "api_path", protected
    assert protected.get("formula") == "formula", protected


def test_t07_confusable_content_reasons_are_single_owner_decisions() -> None:
    """The protection reasons are owned by the protected policy module."""

    cases = {
        "192.168.1.1": "ip_address",
        "192.168.1.1:8080": "ip_address",
        "第一条 本系统适用于生产环境。": "legal_clause",
        "第3条 适用范围": "legal_clause",
        "/api/v1/orders/{id}": "api_path",
        "https://example.com/health": "api_path",
        "y = kx + b": "formula",
        "版本 2.0": "date_or_version",
        "2026-09-08": "date_or_version",
    }

    for text, expected in cases.items():
        assert protected_text_reason(text, {}) == expected, (text, expected)


# ---------------------------------------------------------------------------
# End-to-end: a real DOCX chapter-heading style with numbering
# ---------------------------------------------------------------------------

def _level(ilvl: int, *, start: int = 1, num_fmt: str = "decimal", lvl_text: str = "%1."):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    element = OxmlElement("w:lvl")
    element.set(qn("w:ilvl"), str(ilvl))
    for tag, value in (("w:start", start), ("w:numFmt", num_fmt), ("w:lvlText", lvl_text)):
        child = OxmlElement(tag)
        child.set(qn("w:val"), str(value))
        element.append(child)
    return element


def _add_numbering(document) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), "1000")
    abstract.append(_level(0))
    root.insert(0, abstract)
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), "1000")
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), "1000")
    num.append(reference)
    root.append(num)


def _set_numbering(paragraph) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), "1000")
    num_pr.append(level)
    num_pr.append(identifier)
    properties.append(num_pr)


def _build_chapter_heading_docx(path: Path) -> None:
    """A custom chapter-heading style whose paragraphs also carry numbering.

    The style name is not in the legacy protected set, so the parser reports the
    numbering as ``detected`` while the paragraphs still read as headings.  Two
    consecutive numbered headings are needed for the legacy detector to confirm
    the numbering instance (a lone numbered paragraph is only ``ambiguous``).
    The two signals genuinely conflict.
    """

    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    document = Document()
    _add_numbering(document)
    document.styles.add_style("章标题", WD_STYLE_TYPE.PARAGRAPH)
    for text in ("6.3 标准化注册时序流程", "6.4 数据流设计"):
        heading = document.add_paragraph(text, style="章标题")
        _set_numbering(heading)
    document.add_paragraph("终端用户负责发起认证请求")
    document.save(str(path))


def test_t07_chapter_heading_style_end_to_end(tmp_path: Path) -> None:
    """A real DOCX chapter-heading style with numbering is not a list item.

    Before T07 the ``detected`` numbering short-circuited protection, so the
    heading was emitted as a list item with no record.  After T07 it stays out
    of the list, the decision is recorded as an info downgrade, and the document
    still completes because the case is decidable under README constraint 6.
    """

    source = tmp_path / "chapter.docx"
    _build_chapter_heading_docx(source)

    result = analyze_lists(parse(source))

    assert _item_ids(result) == set(), (
        f"a chapter-heading paragraph must not be a list item; got {sorted(_item_ids(result))}"
    )
    assert _downgrade_codes(result) == {"protected_list_like_text"}, result["downgrades"]
    assert result["status"] == "completed", result["status"]


def test_t07_chapter_heading_style_conversion_report(tmp_path: Path) -> None:
    """The conversion report does not count the chapter heading as a list item."""

    import json

    source = tmp_path / "chapter-report.docx"
    _build_chapter_heading_docx(source)
    output = tmp_path / "chapter-report.out.docx"
    normalized_path = tmp_path / "chapter-report.normalized.json"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"normalized_ast_path": normalized_path, "llm_enhance": "off"},
    )
    result = ConversionKernel().convert(request)
    report = result.as_report()

    assert report["list_semantics"]["item_count"] == 0, report["list_semantics"]
    assert report["source_ast_hierarchy"]["item_count"] == 0, report["source_ast_hierarchy"]


# ---------------------------------------------------------------------------
# End-to-end: consecutive body paragraphs that look like markers
# ---------------------------------------------------------------------------

def _build_ip_body_run_docx(path: Path) -> None:
    from docx import Document

    document = Document()
    for text in ("192.168.1.1 主节点", "192.168.1.2 备节点", "192.168.1.3 观测节点"):
        document.add_paragraph(text)
    document.save(str(path))


def test_t07_ip_address_body_run_is_not_a_list(tmp_path: Path) -> None:
    """A run of IP-address body paragraphs is not a list.

    The DOCX marker scanner reads ``192.168.1.1`` as a decimal-chain list
    marker, so a run of network addresses was promoted to a list.  An IPv4
    address is protected content and must stay body text.
    """

    source = tmp_path / "ips.docx"
    _build_ip_body_run_docx(source)

    result = analyze_lists(parse(source))

    assert _item_ids(result) == set(), (
        f"IP-address body paragraphs must not become a list; got {sorted(_item_ids(result))}"
    )
    reasons = {
        str(item["node_id"]): str(item.get("message"))
        for item in result["downgrades"]
        if item["code"] == "protected_list_like_text"
    }
    assert len(reasons) == 3, result["downgrades"]
    assert set(reasons.values()) == {"ip_address"}, reasons


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
