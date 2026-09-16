"""execution-v6 residual round R7: body wrappers, numbering overrides, LIST_END.

This file owns the residual-round decisions for:

  * A -- a content control (``w:sdt``) and a text box (``w:txbxContent``) wrap
    real body content, so the wrapped paragraphs and tables are converted in
    document order, while a header / footer / footnote / endnote / comment stays
    a different surface and is still skipped; an ``mc:AlternateContent`` shape is
    read through one branch only so its content is not duplicated;
  * B -- ``tests/modular_support.simulate_saved_numbering`` now models
    ``w:lvlOverride`` (a replacement level definition) and ``w:startOverride``
    (a per-instance start), and the per-chapter restart is proven by the visible
    markers independently of the numbering-instance identity;
  * C -- ``LIST_END`` stays a reserved state, documented where a reader looks.

Fixture naming follows the T01 convention ``r7_<item>_<scenario>``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from wxdf.kernel import ConversionKernel
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import LIST_STATE_ENUM, analyze_lists
from modular_support import TEMPLATE, simulate_saved_numbering


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
VML_NS = "urn:schemas-microsoft-com:vml"


# ---------------------------------------------------------------------------
# small SourceFacts helpers
# ---------------------------------------------------------------------------

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


def _text_occurrences(facts: dict, text: str) -> int:
    return sum(
        1
        for unit in facts["units"].values()
        if unit.get("kind") == "paragraph" and _unit_text(unit) == text
    )


def _item_ids(result: dict) -> set[str]:
    return {str(item["item_id"]) for item in result["items"]}


def _convert_docx(source: Path, output: Path):
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return ConversionKernel().convert(request)


def _convert_markdown(source: Path, output: Path):
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return ConversionKernel().convert(request)


def _text_paragraph(text: str):
    return parse_xml(
        f'<w:p xmlns:w="{WORD_NS}"><w:r><w:t>{text}</w:t></w:r></w:p>'
    )


# ===========================================================================
# A: content that is merely wrapped is body content and must be converted
# ===========================================================================

def _build_wrapped_sdt_docx(path: Path) -> None:
    """A body paragraph plus a numbered list held inside a content control."""

    document = Document()
    document.add_paragraph("正文普通段")
    sdt = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    content.append(_text_paragraph("1. 控件甲"))
    content.append(_text_paragraph("2. 控件乙"))
    sdt.append(content)
    body = document.element.body
    body.insert(0, sdt)
    document.save(str(path))


def _build_textbox_docx(path: Path) -> None:
    """A numbered list held inside a text box (``w:txbxContent``)."""

    document = Document()
    document.add_paragraph("正文普通段")
    host = document.add_paragraph()
    run = host.add_run()
    drawing = OxmlElement("w:drawing")
    run._r.append(drawing)
    txbx = OxmlElement("w:txbxContent")
    txbx.append(_text_paragraph("1. 框内甲"))
    txbx.append(_text_paragraph("2. 框内乙"))
    drawing.append(txbx)
    document.save(str(path))


def _build_alternate_content_textbox_docx(path: Path) -> None:
    """A text box whose shape appears once in ``mc:Choice`` and once in ``mc:Fallback``."""

    document = Document()
    document.add_paragraph("正文普通段")
    host = document.add_paragraph()
    run = host.add_run()
    inner = (
        '<w:p xmlns:w="{w}"><w:r><w:t>1. 备选框项</w:t></w:r></w:p>'
    ).format(w=WORD_NS)
    alternate = parse_xml(
        '<mc:AlternateContent xmlns:mc="{mc}" xmlns:w="{w}" xmlns:wps="{wps}" xmlns:v="{v}">'
        '<mc:Choice Requires="wps"><w:drawing><wps:txbx><w:txbxContent>{inner}</w:txbxContent>'
        '</wps:txbx></w:drawing></mc:Choice>'
        '<mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>{inner}</w:txbxContent>'
        '</v:textbox></v:shape></w:pict></mc:Fallback>'
        '</mc:AlternateContent>'.format(mc=MC_NS, w=WORD_NS, wps=WPS_NS, v=VML_NS, inner=inner)
    )
    run._r.append(alternate)
    document.save(str(path))


def test_r7_a_wrapped_sdt_list_is_converted(tmp_path: Path) -> None:
    """A list inside a content control is body content in a wrapper, not a skipped surface."""

    source = tmp_path / "wrapped-sdt.docx"
    _build_wrapped_sdt_docx(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)

    assert "1. 控件甲" in evidence, evidence.keys()
    assert "2. 控件乙" in evidence, evidence.keys()
    assert evidence["1. 控件甲"]["numbering_status"] == "detected", evidence["1. 控件甲"]
    assert evidence["2. 控件乙"]["numbering_status"] == "detected", evidence["2. 控件乙"]

    result = analyze_lists(facts)
    assert len(_item_ids(result)) >= 2, result["items"]
    # The skipped-wrapper diagnostic must be gone for a converted content control.
    assert "unread_body_content_skipped" not in [item["code"] for item in facts["diagnostics"]]


def _add_numbering(document: Document, abstract_id: int, num_id: int) -> None:
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    for tag, value in (("w:start", "1"), ("w:numFmt", "decimal"), ("w:lvlText", "%1.")):
        element = OxmlElement(tag)
        element.set(qn("w:val"), value)
        level.append(element)
    abstract.append(level)
    root.insert(0, abstract)
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), str(abstract_id))
    num.append(reference)
    root.append(num)


def _apply_numbering(paragraph, num_id: int):
    properties = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "0")
    num_pr.append(ilvl)
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), str(num_id))
    num_pr.append(identifier)
    properties.append(num_pr)
    return paragraph


def _number_paragraph(document: Document, text: str, num_id: int):
    return _apply_numbering(document.add_paragraph(text), num_id)


def _build_sdt_numbered_docx(path: Path) -> None:
    """Real ``w:numPr`` numbering held inside a content control."""

    document = Document()
    _add_numbering(document, 903, 903)
    document.add_paragraph("正文普通段")
    sdt = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    for text in ("控件甲", "控件乙"):
        paragraph = OxmlElement("w:p")
        p_pr = OxmlElement("w:pPr")
        num_pr = OxmlElement("w:numPr")
        ilvl = OxmlElement("w:ilvl")
        ilvl.set(qn("w:val"), "0")
        num_pr.append(ilvl)
        identifier = OxmlElement("w:numId")
        identifier.set(qn("w:val"), "903")
        num_pr.append(identifier)
        p_pr.append(num_pr)
        paragraph.append(p_pr)
        run = OxmlElement("w:r")
        text_element = OxmlElement("w:t")
        text_element.text = text
        run.append(text_element)
        paragraph.append(run)
        content.append(paragraph)
    sdt.append(content)
    document.element.body.insert(0, sdt)
    document.save(str(path))


def test_r7_a_sdt_preserves_real_numbering(tmp_path: Path) -> None:
    """The wrapped content keeps its real numbering instance when it is converted."""

    source = tmp_path / "sdt-numbered.docx"
    _build_sdt_numbered_docx(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)

    assert "控件甲" in evidence, evidence.keys()
    assert evidence["控件甲"]["numbering_status"] == "detected", evidence["控件甲"]
    assert evidence["控件甲"]["num_id"] == 903, evidence["控件甲"]

    result = analyze_lists(facts)
    assert len(_item_ids(result)) == 2, result["items"]


def test_r7_a_textbox_list_is_converted(tmp_path: Path) -> None:
    """A list inside ``w:txbxContent`` is converted in document order."""

    source = tmp_path / "textbox.docx"
    _build_textbox_docx(source)

    facts = parse(source)
    evidence = _text_to_evidence(facts)

    assert "1. 框内甲" in evidence, evidence.keys()
    assert "2. 框内乙" in evidence, evidence.keys()
    assert evidence["1. 框内甲"]["numbering_status"] == "detected", evidence["1. 框内甲"]

    result = analyze_lists(facts)
    assert len(_item_ids(result)) >= 2, result["items"]
    assert "unread_body_content_skipped" not in [item["code"] for item in facts["diagnostics"]]


def test_r7_a_alternate_content_textbox_yields_content_once(tmp_path: Path) -> None:
    """An ``mc:AlternateContent`` text box is read through one branch only."""

    source = tmp_path / "alternate-content.docx"
    _build_alternate_content_textbox_docx(source)

    facts = parse(source)

    assert _text_occurrences(facts, "1. 备选框项") == 1, [
        _unit_text(unit) for unit in facts["units"].values()
    ]
    evidence = _text_to_evidence(facts)
    assert evidence["1. 备选框项"]["numbering_status"] == "detected", evidence["1. 备选框项"]
    # The fallback branch must not have contributed a second copy of the shape.
    assert "unread_body_content_skipped" not in [item["code"] for item in facts["diagnostics"]]


def test_r7_a_numbered_header_stays_skipped(tmp_path: Path) -> None:
    """A header belongs to a different surface and is still skipped, with a record."""

    document = Document()
    _add_numbering(document, 904, 904)
    header_paragraph = document.sections[0].header.paragraphs[0]
    header_paragraph.text = "页眉列项"
    _apply_numbering(header_paragraph, 904)
    _number_paragraph(document, "正文甲", 904)
    source = tmp_path / "numbered-header.docx"
    document.save(str(source))

    facts = parse(source)
    codes = [item["code"] for item in facts["diagnostics"]]

    assert "unread_part_numbering_skipped" in codes, codes
    assert "页眉列项" not in _text_to_evidence(facts), _text_to_evidence(facts)


def test_r7_a_plain_header_leaves_no_record(tmp_path: Path) -> None:
    """An ordinary header carries no numbering and must not be reported."""

    document = Document()
    _add_numbering(document, 905, 905)
    document.sections[0].header.paragraphs[0].text = "页眉普通文字"
    _number_paragraph(document, "正文乙", 905)
    source = tmp_path / "plain-header.docx"
    document.save(str(source))

    facts = parse(source)

    assert [item["code"] for item in facts["diagnostics"]] == []


# ===========================================================================
# B: the numbering simulator learns w:lvlOverride / w:startOverride
# ===========================================================================

def _add_lower_letter_definition(document: Document, abstract_id: int, *, start: int) -> None:
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    for tag, value in (("w:start", str(start)), ("w:numFmt", "lowerLetter"), ("w:lvlText", "%1)")):
        element = OxmlElement(tag)
        element.set(qn("w:val"), value)
        level.append(element)
    abstract.append(level)
    root.insert(0, abstract)


def _append_plain_num(document: Document, num_id: int, abstract_id: int) -> None:
    root = document.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), str(abstract_id))
    num.append(reference)
    root.append(num)


def _append_start_override_num(document: Document, num_id: int, abstract_id: int, start: int) -> None:
    root = document.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), str(abstract_id))
    num.append(reference)
    override = OxmlElement("w:lvlOverride")
    override.set(qn("w:ilvl"), "0")
    start_override = OxmlElement("w:startOverride")
    start_override.set(qn("w:val"), str(start))
    override.append(start_override)
    num.append(override)
    root.append(num)


def _build_start_override_docx(path: Path) -> None:
    """Two chapters of the R5 shape, with a startOverride that makes the override observable.

    The abstract definition starts at 3, so a chapter that restarts through a
    ``w:startOverride=1`` must show ``a)`` while a chapter that does not shows
    ``c)``.  This is the real R5 output shape (one abstract, one plain instance
    and one synthesized instance carrying ``startOverride=1``) with the abstract
    start chosen so the override is actually observable.
    """

    document = Document()
    _add_lower_letter_definition(document, 906, start=3)
    _append_plain_num(document, 906, 906)
    _append_start_override_num(document, 907, 906, 1)
    for text, num_id in (("甲一", 906), ("甲二", 906), ("乙一", 907), ("乙二", 907)):
        _number_paragraph(document, text, num_id)
    document.save(str(path))


def test_r7_b_simulator_honours_start_override(tmp_path: Path) -> None:
    """A ``w:startOverride`` sets that instance's start for the level."""

    source = tmp_path / "start-override.docx"
    _build_start_override_docx(source)

    markers = {row["text"]: row["marker"] for row in simulate_saved_numbering(source) if row["text"]}

    assert markers["甲一"] == "c)", markers
    assert markers["甲二"] == "d)", markers
    assert markers["乙一"] == "a)", markers
    assert markers["乙二"] == "b)", markers


def test_r7_b_simulator_honours_level_override_definition(tmp_path: Path) -> None:
    """A ``w:lvlOverride`` level replaces the abstract level's definition."""

    document = Document()
    _add_lower_letter_definition(document, 908, start=1)
    _append_plain_num(document, 908, 908)
    root = document.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), "909")
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), "908")
    num.append(reference)
    override = OxmlElement("w:lvlOverride")
    override.set(qn("w:ilvl"), "0")
    replacement = OxmlElement("w:lvl")
    replacement.set(qn("w:ilvl"), "0")
    for tag, value in (("w:start", "1"), ("w:numFmt", "upperLetter"), ("w:lvlText", "%1)")):
        element = OxmlElement(tag)
        element.set(qn("w:val"), value)
        replacement.append(element)
    override.append(replacement)
    num.append(override)
    root.append(num)
    _number_paragraph(document, "甲", 908)
    _number_paragraph(document, "乙", 909)
    source = tmp_path / "level-override.docx"
    document.save(str(source))

    markers = {row["text"]: row["marker"] for row in simulate_saved_numbering(source) if row["text"]}

    assert markers["甲"] == "a)", markers
    assert markers["乙"] == "A)", markers


def test_r7_b_real_r5_output_restarts_per_chapter_by_markers(tmp_path: Path) -> None:
    """The per-chapter restart is proven by the visible markers alone.

    This does not assert anything about the numbering-instance identity: the
    markers of each chapter's list are compared with each other, so the restart
    holds even if a reader ignores which ``w:num`` produced it.
    """

    source = tmp_path / "chapters.md"
    source.write_text(
        "\n".join(["# 章一", "", "1. 甲一", "2. 甲二", "", "## 章二", "", "1. 乙一", "2. 乙二", ""]),
        encoding="utf-8",
    )
    output = tmp_path / "chapters.out.docx"
    result = _convert_markdown(source, output)
    assert result.status == "completed", result.diagnostics

    list_rows = {
        row["text"]: row["marker"]
        for row in simulate_saved_numbering(output)
        if row.get("num_id") is not None and row["text"] in {"甲一", "甲二", "乙一", "乙二"}
    }

    assert list_rows == {"甲一": "a)", "甲二": "b)", "乙一": "a)", "乙二": "b)"}, list_rows


# ===========================================================================
# C: LIST_END stays reserved
# ===========================================================================

def _build_boundary_docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("章标题", style="Heading 1")
    document.add_paragraph("1. 列项甲")
    document.add_paragraph("2. 列项乙")
    document.add_paragraph("下一节标题", style="Heading 2")
    document.add_paragraph("正文收尾")
    document.save(str(path))


def test_r7_c_list_end_stays_reserved(tmp_path: Path) -> None:
    """``LIST_END`` is enumerated but intentionally never emitted.

    Boundary termination is expressed by the protected block's ``OUTSIDE_LIST``
    state and by ``OUTSIDE_LIST`` itself; a ``LIST_END`` candidate for the same
    block would carry an equal evidence class and manufacture a Resolver tie.
    The state therefore stays reserved, and the boundary decision is still
    observable as the protected ``OUTSIDE_LIST`` block.
    """

    assert "LIST_END" in LIST_STATE_ENUM

    source = tmp_path / "boundary.docx"
    _build_boundary_docx(source)

    facts = parse(source)
    result = analyze_lists(facts)

    decided_states = {
        str((decision.get("value") or {}).get("state"))
        for decision in result["state_decisions"]
    }
    assert "LIST_END" not in decided_states, result["state_decisions"]
    # The heading is the boundary: it is decided OUTSIDE_LIST and is not a list item.
    assert len(_item_ids(result)) == 2, result["items"]


def _build_cell_wrapped_docx(path: Path, wrapper: str) -> None:
    """A table cell whose content is an ``w:sdt`` or an ``mc:AlternateContent``."""

    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    document = Document()
    _add_numbering(document, 80, 80)
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    for paragraph in list(cell._tc.findall(qn("w:p"))):
        cell._tc.remove(paragraph)
    paragraphs = (
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="80"/></w:numPr></w:pPr>'
        '<w:r><w:t>单元格甲</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="80"/></w:numPr></w:pPr>'
        '<w:r><w:t>单元格乙</w:t></w:r></w:p>'
    )
    if wrapper == "sdt":
        xml = f'<w:sdt {nsdecls("w")}><w:sdtContent>{paragraphs}</w:sdtContent></w:sdt>'
    else:
        markup = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        xml = (
            f'<mc:AlternateContent {nsdecls("w")} {markup}>'
            f'<mc:Choice Requires="wps">{paragraphs}</mc:Choice>'
            f'<mc:Fallback>{paragraphs}</mc:Fallback>'
            '</mc:AlternateContent>'
        )
    cell._tc.append(parse_xml(xml))
    document.add_paragraph("表后正文")
    document.save(str(path))


def test_r7_a_cell_nested_content_control_is_converted_once(tmp_path: Path) -> None:
    """A content control inside a table cell is converted, exactly once."""

    source = tmp_path / "cell-sdt.docx"
    _build_cell_wrapped_docx(source, "sdt")

    facts = parse(source)
    texts = ["".join(item.get("text", "") for item in unit.get("inline_content", [])) for unit in facts["units"].values()]

    assert texts.count("单元格甲") == 1, texts
    assert texts.count("单元格乙") == 1, texts
    assert "表后正文" in texts, texts
    assert [item["code"] for item in facts["diagnostics"]] == [], facts["diagnostics"]


def test_r7_a_cell_nested_alternate_content_is_converted_once(tmp_path: Path) -> None:
    """An ``mc:AlternateContent`` inside a table cell is read through one branch."""

    source = tmp_path / "cell-alternate.docx"
    _build_cell_wrapped_docx(source, "alternate")

    facts = parse(source)
    texts = ["".join(item.get("text", "") for item in unit.get("inline_content", [])) for unit in facts["units"].values()]

    assert texts.count("单元格甲") == 1, texts
    assert texts.count("单元格乙") == 1, texts
    assert [item["code"] for item in facts["diagnostics"]] == [], facts["diagnostics"]


def test_r7_a_alternate_content_reads_only_the_first_choice(tmp_path: Path) -> None:
    """Only the first ``mc:Choice`` is read, matching markup-compatibility rules."""

    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    document = Document()
    _add_numbering(document, 80, 80)
    markup = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    xml = (
        f'<mc:AlternateContent {nsdecls("w")} {markup}>'
        '<mc:Choice Requires="wps"><w:p><w:r><w:t>首选框</w:t></w:r></w:p></mc:Choice>'
        '<mc:Choice Requires="wpg"><w:p><w:r><w:t>次选框</w:t></w:r></w:p></mc:Choice>'
        '</mc:AlternateContent>'
    )
    body = document.element.body
    body.insert(len(body) - 1, parse_xml(xml))
    source = tmp_path / "two-choices.docx"
    document.save(str(source))

    facts = parse(source)
    texts = ["".join(item.get("text", "") for item in unit.get("inline_content", [])) for unit in facts["units"].values()]

    assert "首选框" in texts, texts
    assert "次选框" not in texts, texts
