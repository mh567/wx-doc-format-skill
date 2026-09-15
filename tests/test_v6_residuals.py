"""Follow-up residual tests for the closed execution package ``docs/execution-v6``.

Covers the four residuals the follow-up round closes:

* R1 -- ``w:ind`` / adjacency genuinely contributes to ``effective_ilvl``, so an
  indented sub-list without a distinct numbering instance resolves one level
  deeper than its neighbour while a same-indent block stays a sibling and a flat
  list is unaffected.
* R2 -- the numbering instance identity has one owner (the top-level
  ``num_id`` / ``abstract_num_id``) and ``numbering_definition_ref`` is a derived
  compatibility view that cannot drift.
* R3 -- every low-confidence candidate decision is indexable in the ambiguity
  record, and a clean document still reports ``completed``.
* R4 -- a clamped list is visible in the report without blocking publication.

Fixture naming follows the T01 convention ``v6_<residual>_<scenario>``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


# ---------------------------------------------------------------------------
# DOCX fixture builders
# ---------------------------------------------------------------------------

def _add_single_level_abstract(
    document: Document,
    abstract_id: int,
    *,
    num_fmt: str = "decimal",
    lvl_text: str = "%1.",
    left_twips: int = 720,
) -> None:
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    for tag, value in (("w:start", "1"), ("w:numFmt", num_fmt), ("w:lvlText", lvl_text)):
        child = OxmlElement(tag)
        child.set(qn("w:val"), value)
        level.append(child)
    if left_twips is not None:
        p_pr = OxmlElement("w:pPr")
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(left_twips))
        p_pr.append(ind)
        level.append(p_pr)
    abstract.append(level)
    root.insert(0, abstract)


def _add_num(document: Document, num_id: int, abstract_id: int) -> None:
    root = document.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), str(abstract_id))
    num.append(reference)
    root.append(num)


def _number(
    document: Document,
    text: str,
    num_id: int,
    *,
    ilvl: int = 0,
    left_twips: int | None = None,
):
    paragraph = document.add_paragraph(text)
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl_element = OxmlElement("w:ilvl")
    ilvl_element.set(qn("w:val"), str(ilvl))
    num_pr.append(ilvl_element)
    num_id_element = OxmlElement("w:numId")
    num_id_element.set(qn("w:val"), str(num_id))
    num_pr.append(num_id_element)
    p_pr.append(num_pr)
    if left_twips is not None:
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(left_twips))
        p_pr.append(ind)
    return paragraph


def _text_to_evidence(facts: dict) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for unit_id, unit in facts["units"].items():
        if unit.get("kind") != "paragraph":
            continue
        text = "".join(
            str(item.get("text") or "")
            for item in unit.get("inline_content", [])
            if item.get("kind") == "text"
        )
        mapping[text] = facts["evidence"].get(unit_id, {})
    return mapping


def _synthetic_facts(evidence: dict[str, dict], texts: dict[str, str]) -> dict:
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
        for index, (unit_id, text) in enumerate(texts.items())
    }
    return {
        "facts_schema_version": "1.0",
        "source": {
            "source_id": "source",
            "source_type": "docx",
            "content_sha256": "sha256:" + "a" * 64,
        },
        "parser": {
            "id": "test",
            "version": "1.1.0",
            "dialect": "fixture",
            "config_sha256": "sha256:" + "b" * 64,
        },
        "units": units,
        "containers": {"root": {"kind": "document"}},
        "children": {"root": list(texts)},
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


# ---------------------------------------------------------------------------
# R1 -- w:ind contributes to effective_ilvl
# ---------------------------------------------------------------------------

def test_v6_r1_indented_sublist_without_new_instance_resolves_deeper(tmp_path: Path) -> None:
    """An indented sub-list that closes resolves one deeper.

    The run returns to the shallower cluster afterwards, which is what a nested
    sub-list closing looks like.  Both blocks declare ilvl 0 on the same instance,
    so only the indentation separates them.
    """

    document = Document()
    _add_single_level_abstract(document, 200, left_twips=720)
    _add_num(document, 200, 200)
    _number(document, "父项", 200, ilvl=0, left_twips=720)
    _number(document, "子项", 200, ilvl=0, left_twips=1440)
    _number(document, "回到父层", 200, ilvl=0, left_twips=720)
    source = tmp_path / "indented.docx"
    document.save(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)

    assert evidence["父项"]["source_ilvl"] == 0
    assert evidence["子项"]["source_ilvl"] == 0
    assert evidence["父项"]["num_id"] == evidence["子项"]["num_id"] == 200
    assert evidence["父项"]["effective_ilvl"] == 0
    assert evidence["子项"]["effective_ilvl"] == 1
    assert evidence["回到父层"]["effective_ilvl"] == 0
    # The rule that produced the level is recorded in the evidence.
    cluster = evidence["子项"]["indent_cluster_level"]
    assert cluster["rule"] == "declared_plus_indent_cluster"
    assert cluster["indent_offset"] == 1
    assert cluster["declared_effective_ilvl"] == 0

    result = analyze_lists(facts)
    items = result["items"]
    parent = next(item for item in items if item["effective_ilvl"] == 0)
    child = next(item for item in items if item["effective_ilvl"] == 1)
    assert child["semantic_level"] == 1
    assert child["parent_item_id"] == parent["item_id"]


def test_v6_r1_flat_list_with_a_shallower_first_item_is_not_nested(tmp_path: Path) -> None:
    """A flat run whose leading item carries less indent stays flat.

    Without a later return to the shallower cluster the deeper indent is one
    flat list whose first item happens to carry a smaller indent, so the
    declared level stands.  Recording this keeps the rule from mis-nesting an
    ordinary document.
    """

    document = Document()
    _add_single_level_abstract(document, 201, left_twips=720)
    _add_num(document, 201, 201)
    _number(document, "一", 201, ilvl=0, left_twips=0)
    _number(document, "二", 201, ilvl=0, left_twips=720)
    _number(document, "三", 201, ilvl=0, left_twips=720)
    source = tmp_path / "flat.docx"
    document.save(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)

    assert {entry["effective_ilvl"] for entry in evidence.values()} == {0}
    result = analyze_lists(facts)
    assert [item["semantic_level"] for item in result["items"]] == [0, 0, 0]
    assert all(item["parent_item_id"] is None for item in result["items"])


def test_v6_r1_same_indent_block_stays_a_sibling(tmp_path: Path) -> None:
    """Two blocks at the same indent stay siblings even at ilvl 0."""

    document = Document()
    _add_single_level_abstract(document, 201, left_twips=720)
    _add_num(document, 201, 201)
    _number(document, "甲项", 201, ilvl=0, left_twips=1440)
    _number(document, "乙项", 201, ilvl=0, left_twips=1440)
    source = tmp_path / "sibling.docx"
    document.save(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)

    assert evidence["甲项"]["effective_ilvl"] == 0
    assert evidence["乙项"]["effective_ilvl"] == 0
    result = analyze_lists(facts)
    assert {item["semantic_level"] for item in result["items"]} == {0}


def test_v6_r1_flat_uniform_list_is_unaffected(tmp_path: Path) -> None:
    """A flat list with uniform indents keeps every item at level 0."""

    document = Document()
    _add_single_level_abstract(document, 202, left_twips=720)
    _add_num(document, 202, 202)
    for text in ("一", "二", "三"):
        _number(document, text, 202, ilvl=0, left_twips=720)
    source = tmp_path / "flat.docx"
    document.save(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)

    assert [evidence[text]["effective_ilvl"] for text in ("一", "二", "三")] == [0, 0, 0]
    result = analyze_lists(facts)
    assert len(result["items"]) == 3
    assert {item["semantic_level"] for item in result["items"]} == {0}


# ---------------------------------------------------------------------------
# R2 -- one owner for the numbering instance identity
# ---------------------------------------------------------------------------

def test_v6_r2_numbering_definition_ref_is_derived_from_the_owner(tmp_path: Path) -> None:
    """Both evidence spellings carry the owner's value, so they cannot drift."""

    document = Document()
    _add_single_level_abstract(document, 210, left_twips=720)
    _add_num(document, 220, 210)
    _number(document, "第一项", 220, ilvl=0, left_twips=720)
    source = tmp_path / "owner.docx"
    document.save(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)["第一项"]

    assert evidence["num_id"] == 220
    assert evidence["abstract_num_id"] == 210
    assert evidence["numbering_definition_ref"] == {"num_id": 220, "abstract_num_id": 210}


def test_v6_r2_numbering_ids_prefers_the_owner_field() -> None:
    from wxdf.semantics.lists import _numbering_ids

    assert _numbering_ids({"num_id": 7, "abstract_num_id": 3}) == (7, 3)
    assert _numbering_ids({"numbering_definition_ref": {"num_id": 7, "abstract_num_id": 3}}) == (7, 3)
    # When the two spellings disagree the owner wins, so a stale compatibility
    # copy cannot override the single source of truth.
    assert _numbering_ids({"num_id": 7, "numbering_definition_ref": {"num_id": 9}}) == (7, None)


def test_v6_r2_numbering_instance_identity_is_single_sourced(tmp_path: Path) -> None:
    """A real conversion's list semantics reads one value for both spellings."""

    document = Document()
    _add_single_level_abstract(document, 211, left_twips=720)
    _add_num(document, 221, 211)
    _number(document, "甲", 221, ilvl=0, left_twips=720)
    _number(document, "乙", 221, ilvl=0, left_twips=720)
    source = tmp_path / "single-source.docx"
    document.save(source)

    facts = parse(source)
    result = analyze_lists(facts)
    assert {item["num_id"] for item in result["items"]} == {221}
    assert {item["abstract_num_id"] for item in result["items"]} == {211}
    assert {item["sequence"] for item in result["items"]} == {"docx-sequence-221-211"}


# ---------------------------------------------------------------------------
# R3 -- the ambiguity record indexes low-confidence decisions
# ---------------------------------------------------------------------------

def test_v6_r3_low_confidence_decision_is_indexed_and_non_blocking() -> None:
    evidence = {
        "low": {
            "numbering_status": "ambiguous",
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "numbering_evidence": ["isolated_generic_numbering"],
            "explicit_depth": 0,
            "num_fmt": "decimal",
            "lvl_text": "%1.",
        },
    }
    facts = _synthetic_facts(evidence, {"low": "疑似残留编号的正文。"})

    result = analyze_lists(facts)

    records = [record for record in result["ambiguity_records"] if record["node_id"] == "low"]
    assert records, result["ambiguity_records"]
    record = records[0]
    assert record["kind"] == "low_confidence_candidate"
    assert record["record"] == "low_confidence_list_marker"
    assert record["resolution"] == "OUTSIDE_LIST"
    # The decision is also published as a downgrade and stays non-blocking.
    assert any(item["code"] == "low_confidence_list_marker" for item in result["downgrades"])
    assert result["state_unresolved"] == []
    assert result["status"] == "completed"
    assert "low" not in {item["item_id"] for item in result["items"]}


def test_v6_r3_resolver_conflict_is_indexed_and_blocks_completion() -> None:
    evidence = {
        "i1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
        },
        "suspect": {
            "marker_kind": "ordered",
            "marker_text": "2.",
            "ordinal": 2,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": None, "abstract_num_id": None},
            "numbering_evidence": ["unresolved_numbering_definition"],
        },
    }
    facts = _synthetic_facts(evidence, {"i1": "正常列表项", "suspect": "疑似列表项"})

    result = analyze_lists(facts)

    conflicts = [record for record in result["ambiguity_records"] if record["kind"] == "resolver_conflict"]
    assert conflicts, result["ambiguity_records"]
    assert conflicts[0]["node_id"] == "suspect"
    assert conflicts[0]["record"] == "ambiguous_list_state"
    assert result["state_unresolved"]
    assert result["status"] == "unresolved"


# ---------------------------------------------------------------------------
# R4 -- a clamped list is report-visible and non-blocking
# ---------------------------------------------------------------------------

def _convert_markdown(tmp_path: Path, text: str, name: str):
    source = tmp_path / f"{name}.md"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / f"{name}.docx"
    normalized_path = tmp_path / f"{name}.json"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"normalized_ast_path": normalized_path, "llm_enhance": "off"},
    )
    return ConversionKernel().convert(request)


def test_v6_r4_clamped_list_is_report_visible_and_non_blocking(tmp_path: Path) -> None:
    result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 三级列表\n\n1. 一级\n   1. 二级\n      1. 三级\n   2. 二级二\n2. 一级二\n",
        "three-level",
    )

    assert result.status == "completed", result.diagnostics
    audit = result.report["saved_output_audit"]
    assert audit["passed"] is True
    clamps = audit.get("list_render_clamped")
    assert clamps, audit
    assert clamps[0]["code"] == "list_render_level_clamped"
    assert clamps[0]["semantic_level"] == 2
    assert clamps[0]["render_level"] == 1


def test_v6_r4_two_level_list_has_no_clamp_record(tmp_path: Path) -> None:
    result = _convert_markdown(
        tmp_path,
        "# 文档\n\n1. 一级一\n   1. 二级一\n2. 一级二\n",
        "two-level",
    )

    assert result.status == "completed"
    assert "list_render_clamped" not in result.report["saved_output_audit"]
