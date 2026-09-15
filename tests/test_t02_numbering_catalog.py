"""T02 native DOCX numbering catalogue and list-semantics evidence model.

The tests exercise the pure-function surface added by T02:

* ``wxdf.parsers.build_numbering_catalog`` reads the full OOXML level set
  (``numFmt / lvlText / pStyle / start / w:ind / w:suff / lvlRestart``) plus
  ``lvlOverride`` and ``startOverride``.
* ``wxdf.parsers.describe_docx_paragraph`` implements the MS
  NumberingProperties rule: a ``numPr`` on the paragraph STYLE ignores its
  ``ilvl`` and resolves the effective level from ``w:lvl/w:pStyle``.
* Missing or damaged numbering definitions are downgraded to body text with an
  explicit diagnostic instead of being silently promoted to a list item.
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

from wxdf.parsers import build_numbering_catalog, describe_docx_paragraph, parse
from wxdf.semantics.lists import analyze_lists


def _level(
    ilvl: int,
    *,
    num_fmt: str | None = None,
    lvl_text: str | None = None,
    start: int | None = None,
    p_style: str | None = None,
    left_twips: int | None = None,
    suff: str | None = None,
    lvl_restart: int | None = None,
) -> OxmlElement:
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), str(ilvl))
    # CT_Lvl child order: start, numFmt, lvlRestart, pStyle, suff, lvlText, pPr.
    for tag, value in (
        ("w:start", start),
        ("w:numFmt", num_fmt),
        ("w:lvlRestart", lvl_restart),
        ("w:pStyle", p_style),
        ("w:suff", suff),
        ("w:lvlText", lvl_text),
    ):
        if value is None:
            continue
        element = OxmlElement(tag)
        element.set(qn("w:val"), str(value))
        level.append(element)
    if left_twips is not None:
        # OOXML nests level indentation under the level's paragraph properties.
        p_pr = OxmlElement("w:pPr")
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(left_twips))
        p_pr.append(ind)
        level.append(p_pr)
    return level


def _add_abstract(document: Document, abstract_id: int, levels: list[OxmlElement]) -> None:
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    for level in levels:
        abstract.append(level)
    root.insert(0, abstract)


def _add_num(
    document: Document,
    num_id: int,
    abstract_id: int | None,
    *,
    overrides: list[tuple[int, dict[str, object]]] | None = None,
) -> None:
    root = document.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    if abstract_id is not None:
        reference = OxmlElement("w:abstractNumId")
        reference.set(qn("w:val"), str(abstract_id))
        num.append(reference)
    for ilvl, values in overrides or []:
        override = OxmlElement("w:lvlOverride")
        override.set(qn("w:ilvl"), str(ilvl))
        start_override = values.get("startOverride")
        if start_override is not None:
            element = OxmlElement("w:startOverride")
            element.set(qn("w:val"), str(start_override))
            override.append(element)
        level_values = values.get("level")
        if isinstance(level_values, dict):
            override.append(_level(ilvl, **level_values))
        num.append(override)
    root.append(num)


def _apply_numpr(paragraph, num_id: int, ilvl: int | None) -> None:
    properties = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl_element = OxmlElement("w:ilvl")
    ilvl_element.set(qn("w:val"), str(ilvl if ilvl is not None else 0))
    num_pr.append(ilvl_element)
    num_id_element = OxmlElement("w:numId")
    num_id_element.set(qn("w:val"), str(num_id))
    num_pr.append(num_id_element)
    properties.append(num_pr)


# ---------------------------------------------------------------------------
# Catalogue coverage
# ---------------------------------------------------------------------------

def test_t02_catalogue_reads_the_full_ooxml_level_set(tmp_path: Path) -> None:
    document = Document()
    _add_abstract(document, 10, [
        _level(
            0,
            start=1,
            num_fmt="decimal",
            lvl_text="%1.",
            p_style="ListNumber",
            left_twips=720,
            suff="space",
            lvl_restart=2,
        ),
    ])
    _add_num(document, 20, 10)

    catalog = build_numbering_catalog(document)

    assert catalog["num_to_abstract"][20] == 10
    level = catalog["abstract_levels"][10][0]
    assert level["num_fmt"] == "decimal"
    assert level["lvl_text"] == "%1."
    assert level["p_style"] == "ListNumber"
    assert level["start"] == 1
    assert level["suff"] == "space"
    assert level["lvl_restart"] == 2
    assert level["ind"]["left_twips"] == 720
    assert catalog["abstract_pstyle"][10] == {"listnumber": 0}


def test_t02_catalogue_applies_level_and_start_overrides(tmp_path: Path) -> None:
    document = Document()
    _add_abstract(document, 11, [
        _level(0, start=1, num_fmt="decimal", lvl_text="%1."),
    ])
    _add_num(document, 21, 11, overrides=[
        (0, {"startOverride": 5, "level": {"num_fmt": "lowerLetter", "lvl_text": "%1)"}}),
    ])

    catalog = build_numbering_catalog(document)

    override = catalog["num_level_overrides"][21][0]
    assert override["start"] == 5
    assert override["num_fmt"] == "lowerLetter"
    assert override["lvl_text"] == "%1)"
    assert catalog["abstract_levels"][11][0]["num_fmt"] == "decimal"


def test_t02_catalogue_survives_a_document_without_numbering_part() -> None:
    class _DocumentWithoutNumbering:
        @property
        def part(self):  # pragma: no cover - attribute access raises first
            raise KeyError("this part has no numbering part")

    catalog = build_numbering_catalog(_DocumentWithoutNumbering())

    assert catalog == {
        "num_to_abstract": {},
        "abstract_levels": {},
        "abstract_pstyle": {},
        "num_level_overrides": {},
    }


# ---------------------------------------------------------------------------
# MS NumberingProperties: style numPr ilvl is ignored
# ---------------------------------------------------------------------------

def test_t02_paragraph_style_numpr_ilvl_is_ignored(tmp_path: Path) -> None:
    document = Document()
    style = document.styles.add_style("T02ListNumber", WD_STYLE_TYPE.PARAGRAPH)
    style_properties = style.element.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl_element = OxmlElement("w:ilvl")
    ilvl_element.set(qn("w:val"), "3")
    num_pr.append(ilvl_element)
    num_id_element = OxmlElement("w:numId")
    num_id_element.set(qn("w:val"), "30")
    num_pr.append(num_id_element)
    style_properties.append(num_pr)
    _add_abstract(document, 13, [
        _level(0, start=1, num_fmt="decimal", lvl_text="%1."),
        _level(1, start=1, num_fmt="lowerLetter", lvl_text="%2)", p_style=style.style_id),
    ])
    _add_num(document, 30, 13)
    paragraph = document.add_paragraph("样式编号列项", style="T02ListNumber")

    descriptor = describe_docx_paragraph(document, paragraph, build_numbering_catalog(document))

    assert descriptor is not None
    assert descriptor["numbering_source"] == "style"
    assert descriptor["source_ilvl"] == 3
    assert descriptor["effective_ilvl"] == 1
    assert descriptor["num_fmt"] == "lowerLetter"
    assert descriptor["status"] == "detected"


# ---------------------------------------------------------------------------
# Evidence model reaches SourceFacts
# ---------------------------------------------------------------------------

def test_t02_source_facts_carry_the_catalogue_evidence(tmp_path: Path) -> None:
    source = tmp_path / "catalogue.docx"
    document = Document()
    _add_abstract(document, 14, [
        _level(0, start=1, num_fmt="decimal", lvl_text="%1.", left_twips=720, suff="tab"),
    ])
    _add_num(document, 40, 14)
    for text in ("第一项", "第二项"):
        paragraph = document.add_paragraph(text)
        _apply_numpr(paragraph, 40, 0)
    document.save(source)

    facts = parse(source)
    evidence = [item for item in facts["evidence"].values() if item.get("numbering_status") == "detected"]

    assert evidence, "expected the numbered paragraphs to be detected as a list"
    for item in evidence:
        assert item["num_id"] == 40
        assert item["abstract_num_id"] == 14
        assert item["source_ilvl"] == 0
        assert item["effective_ilvl"] == 0
        assert item["num_fmt"] == "decimal"
        assert item["lvl_text"] == "%1."
        assert item["sequence"] == "docx-sequence-40-14"
        assert item["restart_scope"] == "abstract:14"
        assert item["start_value"] == 1
        assert item["w_ind"] == 720
        assert item["suffix"] == "tab"


def test_t02_start_override_reaches_the_evidence_model(tmp_path: Path) -> None:
    source = tmp_path / "start-override.docx"
    document = Document()
    _add_abstract(document, 15, [_level(0, start=1, num_fmt="decimal", lvl_text="%1.")])
    _add_num(document, 50, 15, overrides=[(0, {"startOverride": 7})])
    paragraph = document.add_paragraph("第七项")
    _apply_numpr(paragraph, 50, 0)
    document.save(source)

    facts = parse(source)
    evidence = next(item for item in facts["evidence"].values() if item.get("numbering_status"))

    assert evidence["start_override"] == 7
    assert evidence["start_value"] == 7


# ---------------------------------------------------------------------------
# Missing / damaged numbering definitions
# ---------------------------------------------------------------------------

def test_t02_missing_abstract_definition_is_downgraded_to_body(tmp_path: Path) -> None:
    source = tmp_path / "missing-definition.docx"
    document = Document()
    _add_num(document, 60, None)
    paragraph = document.add_paragraph("没有编号定义的段落")
    _apply_numpr(paragraph, 60, 0)
    document.save(source)

    facts = parse(source)
    evidence = next(item for item in facts["evidence"].values() if item.get("numbering_status") is not None)

    assert evidence["numbering_status"] == "ignored"
    assert "invalid_numbering_definition" in evidence["numbering_evidence"]
    assert "marker_kind" not in evidence

    result = analyze_lists(facts)
    codes = {item["code"] for item in result["diagnostics"]}

    assert codes & {"missing_numbering_definition", "invalid_numbering_definition"}
    assert result["downgrades"], "a damaged definition must be recorded as a downgrade"
    assert not result["items"]


def test_t02_descriptor_reports_damaged_definitions(tmp_path: Path) -> None:
    document = Document()
    _add_num(document, 61, 999)
    paragraph = document.add_paragraph("编号定义缺失")
    _apply_numpr(paragraph, 61, 0)

    descriptor = describe_docx_paragraph(document, paragraph, build_numbering_catalog(document))

    assert descriptor is not None
    assert descriptor["status"] == "ignored"
    assert "invalid_numbering_definition" in descriptor["evidence"]
    assert descriptor["confidence"] == 0.0


@pytest.mark.parametrize("num_fmt", ["decimal", "lowerLetter", "upperRoman", "bullet"])
def test_t02_catalogue_accepts_supported_number_formats(tmp_path: Path, num_fmt: str) -> None:
    document = Document()
    _add_abstract(document, 16, [_level(0, start=1, num_fmt=num_fmt, lvl_text="%1.")])
    _add_num(document, 70, 16)
    paragraph = document.add_paragraph("列项")
    _apply_numpr(paragraph, 70, 0)

    descriptor = describe_docx_paragraph(document, paragraph, build_numbering_catalog(document))

    assert descriptor is not None
    assert descriptor["num_fmt"] == num_fmt
    assert descriptor["status"] == "detected"
    assert descriptor["source_list_type"] == ("bullet_dot" if num_fmt == "bullet" else descriptor["source_list_type"])


# ---------------------------------------------------------------------------
# Sequence identity must not split a list that carries no numbering instance
# ---------------------------------------------------------------------------

def test_t02_exported_sequence_id_matches_the_list_semantics_spelling() -> None:
    from wxdf.semantics.lists import json_sequence_id

    reference = {"num_id": 40, "abstract_num_id": 14}

    assert _numbering_sequence_id_for_test(40, 14) == json_sequence_id(reference, "unit")
    assert _numbering_sequence_id_for_test(40, None) == json_sequence_id({"num_id": 40}, "unit")


def _numbering_sequence_id_for_test(num_id: int, abstract_id: int | None) -> str:
    from wxdf.parsers import _numbering_sequence_id

    return _numbering_sequence_id(num_id, abstract_id)


def test_t02_consecutive_visible_markers_stay_in_one_list() -> None:
    units = {
        unit_id: {
            "kind": "paragraph",
            "source_ref": {
                "source_id": "source",
                "part": "word/document.xml",
                "content_sha256": "sha256:" + "a" * 64,
                "node_path": ["body", index, "w:p"],
                "parser_version": "1.1.0",
            },
            "inline_content": [{"kind": "text", "text": text, "marks": []}],
        }
        for index, (unit_id, text) in enumerate(
            (("a", "甲"), ("b", "乙"), ("c", "丙"))
        )
    }
    children = {"root": ["a", "b", "c"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        unit_id: {
            "marker_kind": "ordered",
            "marker_text": marker,
            "ordinal": ordinal,
            "explicit_depth": 0,
            "numbering_definition_ref": None,
            "num_fmt": "decimal",
            "numbering_status": "detected",
            "source_ilvl": None,
            "effective_ilvl": 0,
            "p_style_ref": None,
        }
        for unit_id, marker, ordinal in (("a", "a)", 1), ("b", "b)", 2), ("c", "c)", 3))
    }
    facts = {
        "facts_schema_version": "1.0",
        "source": {"source_id": "source", "source_type": "docx", "content_sha256": "sha256:" + "a" * 64},
        "parser": {"id": "test", "version": "1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": units,
        "containers": containers,
        "children": children,
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }

    result = analyze_lists(facts)

    assert len(result["lists"]) == 1, "manually typed markers without a numbering instance must not split"
    assert len(result["items"]) == 3
