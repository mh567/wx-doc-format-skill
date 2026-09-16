"""execution-v6 residual round R6: protected-list precision and matrix gaps.

This file owns the residual-round decisions for:

  * P1 protected-list precision -- the low-octet IPv4 gate is kept (deferral)
    and the boundary is pinned;
  * P2 protected-list precision -- a CJK equation that carries an explicit math
    symbol is a formula, and a CJK sentence with only an equals sign is not;
  * P3 protected-list precision -- a style that is clearly a list style
    (``列项标题``) is no longer swallowed by the ``标题`` token, while a chapter
    heading style is still protected;
  * P4 protected-list precision -- a manually typed dotted marker is a manual
    heading in the text path; the DOCX visible-marker path keeps its
    3+-segment list rule, so the two paths differ by design;
  * M1 matrix -- a list inside a header is a different surface and is still not
    read by the source parser, while a text box and a content control are body
    content in a wrapper and are converted (the source-parser wrapper policy was
    completed in round R7); the skipped numbered content is named by an
    informational diagnostic instead of disappearing silently;
  * M2 matrix -- levels 3 through 8 clamp to the second level and emit the
    clamp diagnostic;
  * M3 matrix -- synthetic WPS / LibreOffice numbering shapes behave gracefully;
  * M4 matrix -- same indent different declared level keeps the declarations.

Fixture naming follows the T01 convention ``r6_<item>_<scenario>``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.protected import _is_ipv4, protected_text_reason


TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------
# Synthetic SourceFacts helpers
# ---------------------------------------------------------------------------

def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "word/document.xml",
        "content_sha256": "sha256:" + "a" * 64,
        "node_path": ["body", index, "w:p"],
        "parser_version": "1.1.0",
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
        "source": {"source_id": "source", "source_type": "docx", "content_sha256": "sha256:" + "a" * 64},
        "parser": {"id": "test", "version": "1.1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": units,
        "containers": containers,
        "children": children,
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def _numbered_evidence(num_id: int, ordinal: int, *, depth: int = 0) -> dict:
    return {
        "marker_kind": "ordered",
        "marker_text": f"{ordinal}.",
        "ordinal": ordinal,
        "explicit_depth": depth,
        "numbering_definition_ref": {"num_id": num_id, "abstract_num_id": num_id},
        "num_fmt": "decimal",
        "lvl_text": "%1.",
        "source_ilvl": depth,
        "p_style_ref": None,
    }


def _item_ids(result: dict) -> set[str]:
    return {str(item["item_id"]) for item in result["items"]}


# ---------------------------------------------------------------------------
# DOCX builders
# ---------------------------------------------------------------------------

def _add_abstract(document: Document, abstract_id: int, levels: list[int]) -> None:
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    for ilvl in levels:
        level = OxmlElement("w:lvl")
        level.set(qn("w:ilvl"), str(ilvl))
        for tag, value in (("w:start", "1"), ("w:numFmt", "decimal"), ("w:lvlText", f"%{ilvl + 1}.")):
            child = OxmlElement(tag)
            child.set(qn("w:val"), value)
            level.append(child)
        abstract.append(level)
    root.insert(0, abstract)


def _add_num(
    document: Document,
    num_id: int,
    abstract_id: int,
    *,
    start_override: int | None = None,
) -> None:
    root = document.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), str(abstract_id))
    num.append(reference)
    if start_override is not None:
        lvl_override = OxmlElement("w:lvlOverride")
        lvl_override.set(qn("w:ilvl"), "0")
        override = OxmlElement("w:startOverride")
        override.set(qn("w:val"), str(start_override))
        lvl_override.append(override)
        num.append(lvl_override)
    root.append(num)


def _number(
    document: Document,
    text: str,
    num_id: int,
    *,
    ilvl: int = 0,
    left_twips: int | None = None,
    omit_ilvl: bool = False,
):
    paragraph = document.add_paragraph(text)
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    if not omit_ilvl:
        level = OxmlElement("w:ilvl")
        level.set(qn("w:val"), str(ilvl))
        num_pr.append(level)
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), str(num_id))
    num_pr.append(identifier)
    p_pr.append(num_pr)
    if left_twips is not None:
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(left_twips))
        p_pr.append(ind)
    return paragraph


def _unit_text(unit: dict) -> str:
    return "".join(
        str(item.get("text") or "")
        for item in unit.get("inline_content", [])
        if item.get("kind") == "text"
    )


def _text_to_evidence(facts: dict) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for unit_id, unit in facts["units"].items():
        if unit.get("kind") != "paragraph":
            continue
        mapping[_unit_text(unit)] = facts["evidence"].get(unit_id, {})
    return mapping


def _body_texts(facts: dict) -> set[str]:
    return {text for text in _text_to_evidence(facts) if text}


def _convert(tmp_path: Path, source: Path, tag: str):
    output = tmp_path / f"{tag}.out.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return ConversionKernel().convert(request)


# ===========================================================================
# P1: the low-octet IPv4 gate is kept (deliberate deferral)
# ===========================================================================

def test_r6_p1_low_octet_quad_is_deferred_to_a_manual_heading() -> None:
    """A bare low-octet dotted quad stays a section number, not an address.

    ``1.2.3.4`` is shape-identical to a level-4 section number, so the octet
    gate deliberately defers it to the manual-heading rule.  The deferred block
    is still protected, so no list promotion or content loss follows from the
    deferral.
    """

    assert protected_text_reason("1.2.3.4", {}) == "manual_heading_marker"
    assert protected_text_reason("4.2.1.3", {}) == "manual_heading_marker"
    # The two-segment section-number shape is never an address.
    assert protected_text_reason("1.2 需求分析", {}) == "manual_heading_marker"


def test_r6_p1_real_addresses_are_caught_and_invalid_shapes_are_rejected() -> None:
    """The gate still classifies a real address and rejects every invalid shape."""

    assert _is_ipv4("192.168.1.1")
    assert _is_ipv4("10.0.0.1")
    assert _is_ipv4("192.168.1.1:8080")
    assert _is_ipv4("0.0.0.0") is False  # no octet above 9, so deferred as well
    assert not _is_ipv4("999.1.1.1")
    assert not _is_ipv4("256.0.0.1")
    assert not _is_ipv4("1.2.3")
    assert not _is_ipv4("1.2.3.4.5")
    assert protected_text_reason("192.168.1.1 主节点地址", {}) == "ip_address"


def test_r6_p1_deferred_quad_in_the_visible_marker_path_is_a_dotted_list_marker(tmp_path: Path) -> None:
    """The deferral's one visible consequence, recorded here.

    A low-octet quad typed as a paragraph-leading marker is read like any other
    three-segment or deeper dotted marker, so it materializes as a list item.  The
    gate chooses this over reclassifying the shape as an address; the block's text
    is preserved either way.
    """

    source = tmp_path / "low-octet-marker.docx"
    document = Document()
    for text in ("1.2.3.4 地址规划", "1.2.3.5 另一地址"):
        document.add_paragraph(text)
    document.save(str(source))

    result = analyze_lists(parse(source))

    assert len(_item_ids(result)) == 2, result["items"]


# ===========================================================================
# P2: a CJK equation that carries a math symbol is a formula
# ===========================================================================

def test_r6_p2_cjk_equation_with_a_math_symbol_is_a_formula() -> None:
    """An explicit math symbol is a formula signal independent of CJK text."""

    assert protected_text_reason("面积 = 长 × 宽", {}) == "formula"
    assert protected_text_reason("周长 = 2 × (长 + 宽)", {}) == "formula"
    assert protected_text_reason("y = kx + b", {}) == "formula"


def test_r6_p2_cjk_prose_with_only_an_equals_sign_is_not_a_formula() -> None:
    """The CJK guard governs the text-only equation heuristic."""

    assert protected_text_reason("总收入 = 一千万元", {}) is None
    assert protected_text_reason("总数量等于计划值", {}) is None


# ===========================================================================
# P3: a clearly list-style name is not swallowed by the 标题 token
# ===========================================================================

def test_r6_p3_list_item_title_style_is_not_a_protected_heading() -> None:
    """``列项标题`` names a list entry, so the list token wins over 标题."""

    units = {
        "i1": _paragraph("i1", 0, "内容一", style="列项标题"),
        "i2": _paragraph("i2", 1, "内容二", style="列项标题"),
    }
    children = {"root": ["i1", "i2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {"i1": _numbered_evidence(1, 1), "i2": _numbered_evidence(1, 2)}
    facts = _facts(containers, children, units, evidence)

    result = analyze_lists(facts)

    assert _item_ids(result) == {"i1", "i2"}, result["items"]


def test_r6_p3_chapter_heading_style_stays_protected() -> None:
    """A style that is a heading (``章标题``) is still never a list item."""

    units = {
        "h1": _paragraph("h1", 0, "6.3 概述", style="章标题"),
        "h2": _paragraph("h2", 1, "6.4 数据流设计", style="章标题"),
    }
    children = {"root": ["h1", "h2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {"h1": _numbered_evidence(1, 1), "h2": _numbered_evidence(1, 2)}
    facts = _facts(containers, children, units, evidence)

    result = analyze_lists(facts)

    assert _item_ids(result) == set(), result["items"]


# ===========================================================================
# P4: a manually typed dotted marker is a manual heading in every path
# ===========================================================================

def _decimal_chain_evidence(marker: str, ordinal: int = 1) -> dict:
    return {
        "marker_kind": "ordered",
        "marker_text": marker,
        "source_list_type": "decimal_chain",
        "ordinal": ordinal,
        "explicit_depth": 0,
        "num_fmt": None,
        "p_style_ref": None,
        "visible_marker": {
            "marker_text": marker,
            "source_list_type": "decimal_chain",
            "ordinal": ordinal,
        },
    }


def test_r6_p4_dotted_marker_is_a_manual_heading_in_the_text_path() -> None:
    """Pure text (no visible marker) resolves a dotted marker as a heading."""

    assert protected_text_reason("1.2 需求分析", {}) == "manual_heading_marker"
    assert protected_text_reason("1.2.3 需求分析", {}) == "manual_heading_marker"
    assert protected_text_reason("1.2.3.4 需求分析", {}) == "manual_heading_marker"


def test_r6_p4_two_segment_dotted_marker_is_a_manual_heading_in_the_visible_marker_path() -> None:
    """A two-segment dotted marker is a section number, in both paths.

    This is the documented trade-off: a genuinely nested list item whose text is
    ``1.2 需求分析`` loses its numbering, because the two-segment dotted shape is
    the canonical section number and README constraint 6 plus T07 make "a heading
    never becomes a list item" a hard invariant.  The block keeps its source text,
    so only the list container is forgone.
    """

    assert protected_text_reason("1.2 需求分析", _decimal_chain_evidence("1.2")) == "manual_heading_marker"


def test_r6_p4_three_segment_marker_follows_the_dotted_list_rule() -> None:
    """A three-segment or deeper dotted marker is a dotted list marker.

    The segment count is deliberately part of the decision: in the DOCX
    visible-marker path a three-segment marker is a list item whose marker is
    stripped (see
    ``tests/test_modular_lists.py::test_docx_visible_list_markers_are_removed_when_list_semantics_materialize``),
    while the text path protects it as a manual heading.
    """

    assert protected_text_reason("1.2.3 需求分析", _decimal_chain_evidence("1.2.3")) is None
    assert protected_text_reason("4.2.1 复合编号需求", _decimal_chain_evidence("4.2.1")) is None
    assert protected_text_reason("1.2.3.4 需求分析", _decimal_chain_evidence("1.2.3.4")) is None
    assert protected_text_reason("1.2.3 需求分析", {}) == "manual_heading_marker"


def _build_single_level_list_docx(path: Path) -> None:
    document = Document()
    for text in ("1. 甲", "2. 乙"):
        document.add_paragraph(text)
    document.save(str(path))


def test_r6_p4_single_level_markers_still_form_a_list(tmp_path: Path) -> None:
    """The dotted-marker rule does not touch single-segment markers."""

    source = tmp_path / "single-level.docx"
    _build_single_level_list_docx(source)

    result = analyze_lists(parse(source))

    assert len(_item_ids(result)) == 2, result["items"]


def test_r6_p4_two_segment_dotted_marker_stays_a_manual_heading(tmp_path: Path) -> None:
    """The two-segment trade-off is unchanged: the marker is a manual heading."""

    source = tmp_path / "two-segment.docx"
    document = Document()
    for text in ("1.2 需求分析", "1.3 概要设计"):
        document.add_paragraph(text)
    document.save(str(source))

    result = analyze_lists(parse(source))

    assert _item_ids(result) == set(), result["items"]
    reasons = {
        str(item["node_id"]): str(item.get("message"))
        for item in result["downgrades"]
        if item["code"] == "protected_list_like_text"
    }
    assert set(reasons.values()) == {"manual_heading_marker"}, reasons


def test_r6_p4_three_segment_marker_is_a_list_marker_end_to_end(tmp_path: Path) -> None:
    """End to end, a three-segment dotted marker materializes as a list item."""

    source = tmp_path / "three-segment.docx"
    document = Document()
    for text in ("4.2.1 复合编号需求", "4.2.2 另一需求"):
        document.add_paragraph(text)
    document.save(str(source))

    result = analyze_lists(parse(source))

    assert len(_item_ids(result)) == 2, result["items"]


# ===========================================================================
# M1: containers outside the body are not read by the source parser
# ===========================================================================

def _build_container_docx(path: Path) -> None:
    """Body list plus a numbered list in a header, a textbox and an SDT.

    A header is a separate document part.  A textbox lives in ``w:txbxContent``
    nested inside a run and a content control is a body child of kind ``w:sdt``;
    those two shapes are body content in a wrapper, so the source parser converts
    them in document order (round R7).  Only the header stays skipped.
    """

    document = Document()
    document.add_paragraph("1. 正文项一")
    document.add_paragraph("2. 正文项二")

    header = document.sections[0].header
    header.is_linked_to_previous = False
    header.paragraphs[0].text = "1. 页眉项"

    textbox_host = document.add_paragraph()
    run = textbox_host.add_run()
    drawing = OxmlElement("w:drawing")
    run._r.append(drawing)
    txbx = OxmlElement("w:txbxContent")
    drawing.append(txbx)
    inner = OxmlElement("w:p")
    inner_run = OxmlElement("w:r")
    inner_text = OxmlElement("w:t")
    inner_text.text = "1. 文本框项"
    inner_run.append(inner_text)
    inner.append(inner_run)
    txbx.append(inner)

    sdt = OxmlElement("w:sdt")
    sdt_content = OxmlElement("w:sdtContent")
    sdt_paragraph = OxmlElement("w:p")
    sdt_run = OxmlElement("w:r")
    sdt_text = OxmlElement("w:t")
    sdt_text.text = "1. 内容控件项"
    sdt_run.append(sdt_text)
    sdt_paragraph.append(sdt_run)
    sdt_content.append(sdt_paragraph)
    sdt.append(sdt_content)
    document.element.body.append(sdt)

    document.save(str(path))


def test_r6_m1_header_is_skipped_but_wrapped_body_content_is_read(tmp_path: Path) -> None:
    """A header is skipped; a text box and a content control are converted.

    The source parser keeps header, footer, footnote, endnote and comment parts
    out of the converted block set, so a list there never reaches the pipeline.
    A text box (``w:txbxContent``) and a content control (``w:sdt``) are body
    content in a wrapper, so they are converted in document order (round R7).
    The body list is still recognized, and the header content does not leak in.
    """

    source = tmp_path / "containers.docx"
    _build_container_docx(source)

    facts = parse(source)
    body_texts = _body_texts(facts)

    assert {"1. 正文项一", "2. 正文项二"} <= body_texts, body_texts
    # A header is a different surface, so it stays out of the body.
    assert "1. 页眉项" not in body_texts, body_texts
    # A text box and a content control are body content in a wrapper.
    assert "1. 文本框项" in body_texts, body_texts
    assert "1. 内容控件项" in body_texts, body_texts
    # Every unit still comes from the body part; a header would carry a different
    # part name.
    assert {unit["source_ref"].get("part") for unit in facts["units"].values()} == {"word/document.xml"}
    assert "unread_body_content_skipped" not in [item["code"] for item in facts["diagnostics"]]


# ===========================================================================
# M2: levels 3 through 8 clamp to the second level
# ===========================================================================

def _nested_markdown(depth: int) -> str:
    lines = ["# 文档", "", "## 深层", ""]
    for level in range(depth):
        lines.append("   " * level + f"1. 层级{level + 1}")
    return "\n".join(lines) + "\n"


def test_r6_m2_levels_three_to_eight_clamp_to_the_second_level(tmp_path: Path) -> None:
    """Each depth from 3 to 8 degrades to the two-level projection.

    A list nested three or more levels deep must clamp to the second level
    (``render_level`` 1) and emit one ``list_render_level_clamped`` record per
    deeper semantic level, while the document still completes.
    """

    for depth in range(3, 9):
        source = tmp_path / f"depth-{depth}.md"
        source.write_text(_nested_markdown(depth), encoding="utf-8")
        result = _convert(tmp_path, source, f"depth-{depth}")

        assert result.status == "completed", (depth, result.diagnostics)
        assert not result.report.get("risk_warnings"), (depth, result.report.get("risk_warnings"))
        audit = result.report["saved_output_audit"]
        assert audit["passed"] is True, (depth, audit)
        clamps = audit.get("list_render_clamped") or []
        assert {int(clamp["semantic_level"]) for clamp in clamps} == set(range(2, depth)), (depth, clamps)
        assert {int(clamp["render_level"]) for clamp in clamps} == {1}, (depth, clamps)
        assert result.report["list_semantics"]["item_count"] == depth, (depth, result.report["list_semantics"])


def test_r6_m2_two_level_list_emits_no_clamp(tmp_path: Path) -> None:
    """The clamp starts at semantic level 2, not before."""

    source = tmp_path / "depth-2.md"
    source.write_text(_nested_markdown(2), encoding="utf-8")
    result = _convert(tmp_path, source, "depth-2")

    assert result.status == "completed", result.diagnostics
    assert "list_render_clamped" not in result.report["saved_output_audit"]


# ===========================================================================
# M3: synthetic WPS / LibreOffice numbering shapes are graceful
# ===========================================================================

def test_r6_m3_missing_abstract_num_is_graceful(tmp_path: Path) -> None:
    """A ``w:num`` whose ``abstractNumId`` points at a missing abstract."""

    document = Document()
    _add_abstract(document, 400, [0])
    _add_num(document, 401, 400)
    # Repoint numId 401 at an abstract that does not exist.
    root = document.part.numbering_part.element
    for num in root.findall(qn("w:num")):
        if num.get(qn("w:numId")) == "401":
            num.find(qn("w:abstractNumId")).set(qn("w:val"), "999")
    _number(document, "缺失定义项", 401)
    source = tmp_path / "missing-abstract.docx"
    document.save(str(source))

    facts = parse(source)
    assert "缺失定义项" in _body_texts(facts)

    result = analyze_lists(facts)
    assert result["status"] == "completed", result["diagnostics"]
    assert _item_ids(result) == set(), result["items"]
    codes = {item["code"] for item in result["diagnostics"]} | {item["code"] for item in result["downgrades"]}
    assert codes & {"invalid_numbering_definition", "missing_numbering_definition"}, codes

    # End to end the document still converts and the content survives.
    converted = _convert(tmp_path, source, "missing-abstract")
    assert converted.status in {"completed", "completed_with_warnings"}, converted.diagnostics
    assert converted.report["saved_output_audit"]["passed"] is True


def test_r6_m3_style_numpr_with_mismatched_ilvl_is_graceful(tmp_path: Path) -> None:
    """A paragraph style whose numPr carries an out-of-range ``w:ilvl``."""

    from docx.enum.style import WD_STYLE_TYPE

    document = Document()
    _add_abstract(document, 600, [0, 1, 2])
    _add_num(document, 600, 600)
    style = document.styles.add_style("列项样式X", WD_STYLE_TYPE.PARAGRAPH)
    style_p_pr = style.element.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "5")
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), "600")
    num_pr.append(level)
    num_pr.append(identifier)
    style_p_pr.append(num_pr)
    document.add_paragraph("样式编号项一", style=style)
    document.add_paragraph("样式编号项二", style=style)
    source = tmp_path / "style-ilvl.docx"
    document.save(str(source))

    facts = parse(source)
    assert {"样式编号项一", "样式编号项二"} <= _body_texts(facts)

    result = analyze_lists(facts)
    assert result["status"] == "completed", result["diagnostics"]
    # The mismatched style level never becomes a runaway deep list item.
    # The block is downgraded to body text rather than becoming a shallow
    # list item, so it produces no list item at all.
    assert [item["item_id"] for item in result["items"]] == [], result["items"]
    assert any(
        entry["code"] in {"invalid_numbering_definition", "missing_numbering_definition"}
        for entry in result["diagnostics"]
    ), result["diagnostics"]


def test_r6_m3_run_split_across_num_ids_is_graceful(tmp_path: Path) -> None:
    """A list run split across several ``numId`` values with continuing starts."""

    document = Document()
    for abstract_id in (700, 701, 702):
        _add_abstract(document, abstract_id, [0])
    _add_num(document, 700, 700)
    _add_num(document, 701, 701, start_override=3)
    _add_num(document, 702, 702, start_override=5)
    _number(document, "甲一", 700)
    _number(document, "甲二", 700)
    _number(document, "乙一", 701)
    _number(document, "乙二", 701)
    _number(document, "丙一", 702)
    source = tmp_path / "split-numids.docx"
    document.save(str(source))

    facts = parse(source)
    assert {"甲一", "甲二", "乙一", "乙二", "丙一"} <= _body_texts(facts)

    result = analyze_lists(facts)
    assert result["status"] == "completed", result["diagnostics"]
    assert len(result["items"]) == 5, result["items"]
    # Chained instances that carry a start override are nested by the current
    # heuristic, so the level may cascade; the shape stays bounded and graceful
    # rather than producing an out-of-range level or a crash.
    assert all(0 <= int(item["semantic_level"]) <= 2 for item in result["items"]), result["items"]


def test_r6_m3_omitted_ilvl_defaults_to_level_zero(tmp_path: Path) -> None:
    """A ``w:numPr`` without ``w:ilvl`` is read at level 0."""

    document = Document()
    _add_abstract(document, 500, [0])
    _add_num(document, 500, 500)
    _number(document, "无ilvl一", 500, omit_ilvl=True)
    _number(document, "无ilvl二", 500, omit_ilvl=True)
    source = tmp_path / "no-ilvl.docx"
    document.save(str(source))

    facts = parse(source)
    evidence = _text_to_evidence(facts)
    assert evidence["无ilvl一"]["source_ilvl"] == 0
    assert evidence["无ilvl一"]["effective_ilvl"] == 0

    result = analyze_lists(facts)
    assert result["status"] == "completed", result["diagnostics"]
    assert len(_item_ids(result)) == 2, result["items"]
    assert {int(item["semantic_level"]) for item in result["items"]} == {0}, result["items"]


# ===========================================================================
# M4: indent and declared level combinations
# ===========================================================================

def test_r6_m4_same_indent_different_level_keeps_the_declared_levels(tmp_path: Path) -> None:
    """Same indent with increasing declared levels keeps the declarations.

    The R1 indentation rule only deepens a block whose indent cluster is strictly
    deeper than an earlier sibling's, so a run at one indent cannot be re-levelled
    by indentation: the declared effective levels stand and the deeper items
    parent to the shallower ones.
    """

    document = Document()
    _add_abstract(document, 300, [0, 1, 2])
    _add_num(document, 300, 300)
    _number(document, "甲", 300, ilvl=0, left_twips=1440)
    _number(document, "乙", 300, ilvl=1, left_twips=1440)
    _number(document, "丙", 300, ilvl=2, left_twips=1440)
    source = tmp_path / "same-indent-diff-level.docx"
    document.save(str(source))

    facts = parse(source)
    evidence = _text_to_evidence(facts)
    assert [evidence[text]["effective_ilvl"] for text in ("甲", "乙", "丙")] == [0, 1, 2]

    result = analyze_lists(facts)
    by_text = {_unit_text(facts["units"][item["item_id"]]): item for item in result["items"]}
    assert by_text["乙"]["parent_item_id"] == by_text["甲"]["item_id"], by_text
    assert by_text["丙"]["parent_item_id"] == by_text["乙"]["item_id"], by_text


def test_r6_m4_different_indent_same_level_resolves_deeper(tmp_path: Path) -> None:
    """Different indent with the same declared level resolves one level deeper.

    This is the R1 rule re-pinned for the matrix item; the dedicated R1 coverage
    lives in ``tests/test_v6_residuals.py``.
    """

    document = Document()
    _add_abstract(document, 301, [0])
    _add_num(document, 301, 301)
    _number(document, "父层", 301, ilvl=0, left_twips=720)
    _number(document, "子层", 301, ilvl=0, left_twips=1440)
    _number(document, "回到父层", 301, ilvl=0, left_twips=720)
    source = tmp_path / "different-indent-same-level.docx"
    document.save(str(source))

    facts = parse(source)
    evidence = _text_to_evidence(facts)
    assert evidence["父层"]["effective_ilvl"] == 0
    assert evidence["子层"]["effective_ilvl"] == 1
    assert evidence["回到父层"]["effective_ilvl"] == 0

    result = analyze_lists(facts)
    by_text = {_unit_text(facts["units"][item["item_id"]]): item for item in result["items"]}
    assert by_text["子层"]["semantic_level"] == 1
    assert by_text["子层"]["parent_item_id"] == by_text["父层"]["item_id"], by_text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))


def test_r6_m1_numbered_header_is_reported_instead_of_dropped_silently(tmp_path: Path) -> None:
    """Content the adapter does not read is named, not silently dropped.

    The adapter converts the document body, so a numbered list inside a header,
    footer, footnote, endnote, text box or content control never reaches the
    output.  A numbered header must leave an informational record naming the
    skipped part, while an ordinary header that carries no numbering must not be
    reported.
    """

    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def add_numbering(document) -> int:
        root = document.part.numbering_part.element
        abstract = OxmlElement("w:abstractNum")
        abstract.set(qn("w:abstractNumId"), "77")
        level = OxmlElement("w:lvl")
        level.set(qn("w:ilvl"), "0")
        for tag, value in (("w:numFmt", "decimal"), ("w:lvlText", "%1.")):
            element = OxmlElement(tag)
            element.set(qn("w:val"), value)
            level.append(element)
        abstract.append(level)
        root.insert(0, abstract)
        num = OxmlElement("w:num")
        num.set(qn("w:numId"), "77")
        reference = OxmlElement("w:abstractNumId")
        reference.set(qn("w:val"), "77")
        num.append(reference)
        root.append(num)
        return 77

    def number(paragraph, num_id: int) -> None:
        properties = paragraph._p.get_or_add_pPr()
        num_pr = OxmlElement("w:numPr")
        ilvl = OxmlElement("w:ilvl")
        ilvl.set(qn("w:val"), "0")
        num_pr.append(ilvl)
        num_id_element = OxmlElement("w:numId")
        num_id_element.set(qn("w:val"), str(num_id))
        num_pr.append(num_id_element)
        properties.append(num_pr)

    numbered = Document()
    num_id = add_numbering(numbered)
    header_paragraph = numbered.sections[0].header.paragraphs[0]
    header_paragraph.text = "页眉列项"
    number(header_paragraph, num_id)
    number(numbered.add_paragraph("正文甲"), num_id)
    numbered_source = tmp_path / "numbered-header.docx"
    numbered.save(numbered_source)

    facts = parse(numbered_source)
    codes = [item["code"] for item in facts["diagnostics"]]
    assert "unread_part_numbering_skipped" in codes, codes
    skipped = next(item for item in facts["diagnostics"] if item["code"] == "unread_part_numbering_skipped")
    assert skipped["severity"] == "info"
    assert "header" in str(skipped["message"]), skipped
    # The record names the skipped part, and the body content is still converted.
    assert {unit["source_ref"]["part"] for unit in facts["units"].values()} == {"word/document.xml"}
    assert any("正文甲" in "".join(item.get("text", "") for item in unit.get("inline_content", [])) for unit in facts["units"].values())

    plain = Document()
    add_numbering(plain)
    plain.sections[0].header.paragraphs[0].text = "页眉普通文字"
    number(plain.add_paragraph("正文乙"), 77)
    plain_source = tmp_path / "plain-header.docx"
    plain.save(plain_source)

    plain_facts = parse(plain_source)
    assert [item["code"] for item in plain_facts["diagnostics"]] == []
