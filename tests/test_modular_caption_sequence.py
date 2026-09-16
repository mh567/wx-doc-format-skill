"""The caption sequence identifier and field form come from the template.

Ruling of 2026-09-16 (docs/execution-v8): the output wrote a hardcoded English
identifier in a simple field, so Word's cross-reference dialog listed no item for
the 图 and 表 reference types.  The template's own captions are the contract: a
complex field whose identifier is the caption label (``SEQ 表`` / ``SEQ 图``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.rendering import _write_caption
from wxdf.run_result import ConversionRequest
from wxdf.template import load_template_bundle


TEMPLATE = ROOT / "assets" / "wx_template.docx"
BASELINE = ROOT / "input" / "test_baseline.md"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS = {"w": W[1:-1]}


def _caption_contract(contract: dict) -> dict:
    return dict(contract["captions"])


def _caption_paragraphs(path: Path) -> list:
    """Return the caption-styled paragraphs of a saved DOCX."""

    with ZipFile(path) as archive:
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        names = {
            style.get(f"{W}styleId"): (style.find("w:name", NS).get(f"{W}val") if style.find("w:name", NS) is not None else "")
            for style in styles.findall("w:style", NS)
        }
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    return [
        paragraph
        for paragraph in root.iter(f"{W}p")
        if (names.get((paragraph.find("w:pPr/w:pStyle", NS).get(f"{W}val") if paragraph.find("w:pPr/w:pStyle", NS) is not None else "")) or "").casefold() == "caption"
    ]


def _caption_identifiers(path: Path) -> list[str]:
    identifiers = []
    for paragraph in _caption_paragraphs(path):
        for node in paragraph.iter(f"{W}instrText"):
            text = node.text or ""
            if "SEQ" in text and len(text.split()) > 1:
                identifiers.append(text.split()[1])
    return identifiers


def _source_with_captions(tmp_path: Path) -> Path:
    """Build a DOCX holding a table caption, a figure caption and a formula number."""

    from docx.shared import Cm
    from PIL import Image

    image = tmp_path / "figure.png"
    Image.new("RGB", (60, 40), (30, 90, 160)).save(image)
    document = Document()
    document.add_paragraph("表1 字段说明")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "说明"
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(str(image), width=Cm(3))
    document.add_paragraph("图1 流程示意")
    document.add_paragraph("如公式(1)所示。")
    source = tmp_path / "captions.docx"
    document.save(str(source))
    return source


def _convert(tmp_path: Path, source: Path) -> tuple[Path, object]:
    output = tmp_path / f"{source.stem}-out.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return output, ConversionKernel().convert(request)


# ---------------------------------------------------------------------------
# Contract level
# ---------------------------------------------------------------------------

def test_the_template_supplies_the_caption_identifiers() -> None:
    bundle = load_template_bundle(TEMPLATE)

    assert _caption_contract(bundle.presentation_contract) == {
        "table_sequence": "表",
        "figure_sequence": "图",
        "source": "template",
    }


def test_a_template_without_caption_fields_falls_back_to_the_wx_labels(tmp_path: Path) -> None:
    bare = tmp_path / "bare.docx"
    Document().save(str(bare))

    bundle = load_template_bundle(bare)

    captions = _caption_contract(bundle.presentation_contract)
    assert captions["table_sequence"] == "表"
    assert captions["figure_sequence"] == "图"
    assert captions["source"] == "fallback"


def _caption_field_template(path: Path, *, label: str, identifier: str, styled: bool, next_table: bool) -> Path:
    """Build a template whose only caption field is the one under test."""

    document = Document()
    if not styled:
        style = document.styles["Caption"]
        style._element.getparent().remove(style._element)
    paragraph = document.add_paragraph()
    paragraph.add_run(label)
    paragraph._p.append(
        parse_xml(f'<w:r xmlns:w="{W[1:-1]}"><w:instrText xml:space="preserve"> SEQ {identifier} \\* ARABIC </w:instrText></w:r>')
    )
    if styled:
        paragraph.style = document.styles["Caption"]
    if next_table:
        document.add_table(rows=1, cols=1)
    else:
        document.add_paragraph("正文")
    document.save(str(path))
    return path


def test_a_caption_label_decides_its_kind_before_the_next_block(tmp_path: Path) -> None:
    """A 图 caption followed by a table must not name the table sequence.

    The label the caption leads with is its own statement of what it labels, so it
    outranks the neighbouring block.  Reading the neighbour first would put the
    figure's identifier into ``table_sequence``.
    """

    template = _caption_field_template(
        tmp_path / "figure-then-table.docx", label="图", identifier="图解", styled=True, next_table=True
    )

    captions = _caption_contract(load_template_bundle(template).presentation_contract)

    assert captions["figure_sequence"] == "图解"
    assert captions["table_sequence"] == "表"


def test_a_template_without_a_caption_style_reads_no_field(tmp_path: Path) -> None:
    """An unresolved caption style must not turn unstyled paragraphs into captions."""

    template = _caption_field_template(
        tmp_path / "unstyled.docx", label="图", identifier="自定义", styled=False, next_table=True
    )

    captions = _caption_contract(load_template_bundle(template).presentation_contract)

    assert (captions["table_sequence"], captions["figure_sequence"]) == ("表", "图")


# ---------------------------------------------------------------------------
# Write level
# ---------------------------------------------------------------------------

def _run_shapes(paragraph) -> list[tuple[str, str]]:
    """Return ``(element, value)`` for each run of a paragraph, in order."""

    shapes: list[tuple[str, str]] = []
    for run in paragraph._p:
        if run.tag != qn("w:r"):
            continue
        char = run.find(qn("w:fldChar"))
        if char is not None:
            shapes.append(("fldChar", char.get(qn("w:fldCharType")) or ""))
            continue
        instruction = run.find(qn("w:instrText"))
        if instruction is not None:
            shapes.append(("instrText", instruction.text or ""))
            continue
        text = run.find(qn("w:t"))
        shapes.append(("text", text.text if text is not None else ""))
    return shapes


def test_a_caption_is_written_as_the_template_complex_field() -> None:
    template = Document(str(TEMPLATE))
    paragraph = template.add_paragraph()
    block = {
        "role": "caption",
        "caption_type": "table",
        "inline_content": [{"kind": "text", "text": "表1 字段说明", "marks": []}],
    }

    text = _write_caption(paragraph, block, {})

    assert _run_shapes(paragraph) == [
        ("text", "表"),
        ("fldChar", "begin"),
        ("instrText", " SEQ 表 \\* ARABIC "),
        ("fldChar", "separate"),
        ("text", "1"),
        ("fldChar", "end"),
        ("text", " 字段说明"),
    ]
    instruction = next(run for run in paragraph._p.iter(qn("w:instrText")))
    assert instruction.get(qn("xml:space")) == "preserve"
    assert text == "表1 字段说明"


def test_the_label_and_the_number_are_adjacent_like_the_template() -> None:
    template = Document(str(TEMPLATE))
    paragraph = template.add_paragraph()
    block = {
        "role": "caption",
        "caption_type": "figure",
        "inline_content": [{"kind": "text", "text": "图 1 流程示意", "marks": []}],
    }

    text = _write_caption(paragraph, block, {})

    assert text == "图1 流程示意"
    assert not any(element.tag == qn("w:fldSimple") for element in paragraph._p.iter())


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_the_converted_captions_use_the_template_identifiers(tmp_path: Path) -> None:
    output, result = _convert(tmp_path, _source_with_captions(tmp_path))

    assert result.status == "completed", result.diagnostics
    assert result.report["saved_output_audit"]["passed"] is True
    assert result.report["saved_output_audit"]["unexpected_styles"] == []
    assert sorted(_caption_identifiers(output)) == ["图", "表"]


def test_the_converted_caption_paragraph_carries_no_simple_field(tmp_path: Path) -> None:
    output, result = _convert(tmp_path, _source_with_captions(tmp_path))

    assert result.status == "completed", result.diagnostics
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "SEQ Table" not in document_xml
    assert "SEQ Figure" not in document_xml


def test_the_tracked_baseline_still_converts_cleanly(tmp_path: Path) -> None:
    _output, result = _convert(tmp_path, BASELINE)

    assert result.status == "completed", result.diagnostics
    assert result.report["model_audit"]["passed"] is True
    assert result.report["saved_output_audit"]["passed"] is True
    assert (result.report.get("risk_warnings") or []) == []


def test_the_markdown_baseline_still_converts_cleanly(tmp_path: Path) -> None:
    _output, result = _convert(tmp_path, ROOT / "input" / "benchmark_report.md")

    assert result.status == "completed", result.diagnostics
    assert result.report["model_audit"]["passed"] is True
    assert result.report["saved_output_audit"]["passed"] is True
    assert (result.report.get("risk_warnings") or []) == []


def test_a_formula_number_field_survives_the_caption_change(tmp_path: Path) -> None:
    """A retained formula field keeps its identifier, so cross references resolve.

    The field is written as the complex form the template uses, which is what a
    real document carries.
    """

    source = tmp_path / "formula.docx"
    document = Document()
    paragraph = document.add_paragraph("如公式")
    for element in (
        '<w:r xmlns:w="{ns}"><w:fldChar w:fldCharType="begin"/></w:r>',
        '<w:r xmlns:w="{ns}"><w:instrText xml:space="preserve"> SEQ 公式 \\* ARABIC </w:instrText></w:r>',
        '<w:r xmlns:w="{ns}"><w:fldChar w:fldCharType="separate"/></w:r>',
        '<w:r xmlns:w="{ns}"><w:t>1</w:t></w:r>',
        '<w:r xmlns:w="{ns}"><w:fldChar w:fldCharType="end"/></w:r>',
    ):
        paragraph._p.append(parse_xml(element.format(ns=W[1:-1])))
    paragraph.add_run("所示。")
    document.save(str(source))

    output, result = _convert(tmp_path, source)

    assert result.status == "completed", result.diagnostics
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert document_xml.count("SEQ 公式") == 1
    assert "SEQ Table" not in document_xml
