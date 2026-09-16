"""Regression tests for the defects the real-input corpus exposed.

Each test pins one defect found by converting the corpus in
``/Users/harris/Documents/zero项目/input`` and the fix that made the conversion
match the source:

  * a ``w:b`` / ``w:i`` switch that OOXML explicitly turns off must not become a
    mark, because editors write the off form on runs whose style would otherwise
    supply it (11k+ runs across the corpus);
  * a numbering level that declares a visible format is a list level, so circled
    and ideographic sequences are not rejected as damaged definitions;
  * ``w:numId`` 0 detaches a paragraph from numbering, so it is not a damaged
    definition either;
  * the excluded-region pruning drops the associations and captions that point
    into the dropped subtree, so the normalized view stays a valid AST 2.0;
  * a created node joins the scope of the container it lands in, so a caption
    generated for a table inside an appendix is appendix-owned;
  * the render map reports the text a retained inline object shows, so the saved
    output audit compares like with like.
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

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
from wxdf.transaction import _apply_create_node
from wxdf.normalization import normalize
from wxdf.validation import validate_ast

from test_modular_normalization import _profile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CAPTION_STYLE_ID = "12"


def _empty_template_document() -> Document:
    """Return the template document with its body content removed."""

    document = Document(str(TEMPLATE))
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    return document


def _source_ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "word/document.xml",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


# ---------------------------------------------------------------------------
# D2: run switch values
# ---------------------------------------------------------------------------

def test_run_switch_value_decides_bold_and_italic(tmp_path: Path) -> None:
    """``w:val="0"`` turns a mark off instead of adding it."""

    source = tmp_path / "switches.docx"
    document = _empty_template_document()
    paragraph = document.add_paragraph()
    explicit_on = paragraph.add_run("显式加粗")
    explicit_on.bold = True
    explicit_off = paragraph.add_run("显式取消加粗")
    explicit_off.bold = False
    italic_off = paragraph.add_run("显式取消倾斜")
    italic_off.italic = False
    document.save(str(source))

    facts = parse(source)
    inline = next(
        unit["inline_content"]
        for unit in facts["units"].values()
        if unit.get("kind") == "paragraph"
        and any(item.get("text") == "显式取消加粗" for item in unit.get("inline_content") or [])
    )
    marks = {
        item.get("text"): item.get("marks")
        for item in inline
        if item.get("kind") == "text" and item.get("text")
    }

    assert marks["显式加粗"] == ["strong"]
    assert marks["显式取消加粗"] == []
    assert marks["显式取消倾斜"] == []


# ---------------------------------------------------------------------------
# D3: numbering formats
# ---------------------------------------------------------------------------

def _add_abstract(document: Document, abstract_id: int, ilvl: int, num_fmt: str, lvl_text: str) -> None:
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), str(ilvl))
    for tag, value in (("w:start", "1"), ("w:numFmt", num_fmt), ("w:lvlText", lvl_text)):
        element = OxmlElement(tag)
        element.set(qn("w:val"), value)
        level.append(element)
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


def _apply_numpr(paragraph, num_id: int, ilvl: int) -> None:
    properties = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), str(ilvl))
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), str(num_id))
    num_pr.append(level)
    num_pr.append(identifier)
    properties.append(num_pr)


def test_circled_numbering_format_is_a_list_level(tmp_path: Path) -> None:
    """``decimalEnclosedCircle`` declares a marker, so its level is a list."""

    source = tmp_path / "circled.docx"
    document = Document()
    _add_abstract(document, 26, 0, "decimalEnclosedCircle", "%1")
    _add_num(document, 22, 26)
    for text in ("文件数据处理层：包含可疑文件处理", "分析结果处理"):
        paragraph = document.add_paragraph(text)
        _apply_numpr(paragraph, 22, 0)
    document.save(str(source))

    facts = parse(source)
    entries = [
        item for item in facts["evidence"].values()
        if item.get("num_fmt") == "decimalEnclosedCircle"
    ]

    assert entries, "the circled level must reach the numbering evidence"
    for entry in entries:
        assert entry["numbering_status"] == "detected"
        assert "valid_numbering_definition" in entry["numbering_evidence"]
        assert "invalid_numbering_definition" not in entry["numbering_evidence"]

    result = analyze_lists(facts)
    codes = {item["code"] for item in result["diagnostics"]}

    assert result["items"], "circled numbering must still materialize list items"
    assert not codes & {"missing_numbering_definition", "invalid_numbering_definition"}


# ---------------------------------------------------------------------------
# D4: numId 0
# ---------------------------------------------------------------------------

def test_numbering_suppressed_paragraph_is_not_a_damaged_definition(tmp_path: Path) -> None:
    """``w:numId`` 0 reports suppression and yields no downgrade diagnostic."""

    source = tmp_path / "suppressed.docx"
    document = Document()
    paragraph = document.add_paragraph("总体编制要求：")
    paragraph.style = document.styles["Normal"]
    _apply_numpr(paragraph, 0, 0)
    document.save(str(source))

    facts = parse(source)
    evidence = next(
        item for item in facts["evidence"].values() if item.get("numbering_status") is not None
    )

    assert evidence["numbering_status"] == "ignored"
    assert "numbering_suppressed" in evidence["numbering_evidence"]
    assert "invalid_numbering_definition" not in evidence["numbering_evidence"]

    result = analyze_lists(facts)
    codes = {item["code"] for item in result["diagnostics"]}

    assert not codes & {"missing_numbering_definition", "invalid_numbering_definition"}


# ---------------------------------------------------------------------------
# D5: a list deeper than the two-level model
# ---------------------------------------------------------------------------

def test_deep_orphan_list_is_reported_without_blocking_delivery(tmp_path: Path) -> None:
    """A source level the two-level model cannot express is clamped and reported.

    The defect this pins: the corpus document declared ``w:ilvl=2`` bullets with
    no level-1 parent, every audit passed, and the run still failed with no
    artifact at all.
    """

    source = tmp_path / "deep.docx"
    document = Document()
    _add_abstract(document, 40, 2, "bullet", "\uf0d8")
    _add_num(document, 25, 40)
    document.add_paragraph("工单信息由多个页面维护。")
    for text in ("新建工单信息", "保存附件文件信息"):
        paragraph = document.add_paragraph(text)
        _apply_numpr(paragraph, 25, 2)
    document.save(str(source))

    output = tmp_path / "deep.out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))
    report = result.as_report()
    codes = {item["code"] for item in report["diagnostics"]}

    assert report["status"] in {"completed", "completed_with_warnings"}, codes
    assert output.is_file(), "the clamped document must still be delivered"
    # The clamp stays visible: the source depth is reported, not erased.
    assert "list_constraint_violation" in codes, codes
    assert "orphan_list_depth_flattened" in codes, codes


# ---------------------------------------------------------------------------
# D1: excluded-region pruning
# ---------------------------------------------------------------------------

def _region_ast() -> dict:
    """Return an AST whose excluded region owns a captioned table."""

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p1": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "正文", "marks": []}],
                    "source_ref": _source_ref(1),
                },
                "t1": {"block_type": "table", "table_type": "data", "source_ref": _source_ref(2)},
                "p2": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "目录单元格", "marks": []}],
                    "source_ref": _source_ref(3),
                },
                "cap1": {
                    "block_type": "paragraph",
                    "role": "caption",
                    "caption_type": "table",
                    "target_id": "t1",
                    "inline_content": [{"kind": "text", "text": "表 目录表", "marks": []}],
                    "source_ref": _source_ref(4),
                },
            },
            "containers": {
                "root": {"kind": "document"},
                "toc": {"kind": "section"},
                "row1": {"kind": "table_row"},
                "cell1": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1},
            },
            "children": {
                "root": ["toc", "cap1", "p1"],
                "toc": ["t1"],
                "t1": ["row1"],
                "row1": ["cell1"],
                "cell1": ["p2"],
            },
            "associations": [{"kind": "caption_target", "source_id": "cap1", "target_id": "t1"}],
            "resources": {},
        },
    }
    validate_ast(ast)
    return ast


def test_excluded_region_prunes_dependent_captions_and_associations() -> None:
    """Dropping a region drops what only referenced it."""

    ast = _region_ast()
    overlay = {"toc": {"semantic_scope": "excluded"}}

    normalized = normalize(ast, _profile(), strategy="wx-default-v2", overlay=overlay)
    document = normalized["source_ast"]["document"]

    validate_ast(normalized["source_ast"])
    assert "toc" not in document["containers"]
    assert "t1" not in document["blocks"]
    assert "cap1" not in document["blocks"]
    assert document["associations"] == []
    # The committed Source AST keeps the region it was handed.
    assert ast["document"]["blocks"]["cap1"]["target_id"] == "t1"


# ---------------------------------------------------------------------------
# D1b: created nodes join the scope of their container
# ---------------------------------------------------------------------------

def test_created_node_inherits_container_scope_metadata() -> None:
    """A caption generated inside an appendix becomes appendix-owned."""

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "title": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "附录", "marks": []}],
                    "source_ref": _source_ref(1),
                },
                "t1": {"block_type": "table", "table_type": "data", "source_ref": _source_ref(2)},
            },
            "containers": {
                "root": {"kind": "document"},
                "apx": {
                    "kind": "appendix",
                    "source_classification": "unknown",
                    "title_ref": "title",
                    "appendix_id": "A",
                    "semantic_scope_id": "semantic:appendix:A",
                    "source_number_preserved": True,
                },
            },
            "children": {"root": ["apx"], "apx": ["title", "t1"]},
            "associations": [],
            "resources": {},
        },
    }
    validate_ast(ast)

    _apply_create_node(ast, {
        "value": {
            "node_id": "cap1",
            "parent_id": "apx",
            "index": 1,
            "node": {
                "block_type": "paragraph",
                "role": "caption",
                "caption_type": "table",
                "target_id": "t1",
                "inline_content": [],
                "generated_by": "test",
                "generation_basis": ["t1"],
            },
        }
    })

    created = ast["document"]["blocks"]["cap1"]
    assert created["appendix_id"] == "A"
    assert created["semantic_scope_id"] == "semantic:appendix:A"
    # ``source_number_preserved`` describes the node's own content, not the
    # container's scope.  Inheriting it made a generated caption take the
    # "keep the authored number" branch and render as an empty paragraph.
    assert "source_number_preserved" not in created
    assert ast["document"]["children"]["apx"].index("cap1") == 1


def test_created_node_keeps_its_own_scope_declaration() -> None:
    """An explicit scope on the created node wins over the container's.

    A precedence guard rather than a defect regression: the created node keeps
    its own declaration instead of being overwritten by the container's.
    """

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p1": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "正文", "marks": []}],
                    "source_ref": _source_ref(1),
                },
            },
            "containers": {"root": {"kind": "document"}, "sec": {"kind": "section"}},
            "children": {"root": ["sec"], "sec": ["p1"]},
            "associations": [],
            "resources": {},
        },
    }
    validate_ast(ast)

    _apply_create_node(ast, {
        "value": {
            "node_id": "p2",
            "parent_id": "sec",
            "index": 1,
            "node": {
                "block_type": "paragraph",
                "role": "body",
                "inline_content": [{"kind": "text", "text": "新增", "marks": []}],
                "generated_by": "test",
                "generation_basis": ["p1"],
                "appendix_id": "B",
            },
        }
    })

    assert ast["document"]["blocks"]["p2"]["appendix_id"] == "B"


# ---------------------------------------------------------------------------
# D6: a caption generated inside an appendix
# ---------------------------------------------------------------------------

def test_generated_caption_inside_an_appendix_keeps_its_label(tmp_path: Path) -> None:
    """A generated appendix caption must not render as an empty paragraph."""

    source = tmp_path / "appendix.docx"
    document = _empty_template_document()
    document.add_paragraph("演示文档")
    document.add_paragraph("附录")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "标识"
    table.cell(1, 1).text = "资源标识"
    document.save(str(source))

    output = tmp_path / "appendix.out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))
    report = result.as_report()

    assert report["appendix_preservation_audit"]["passed"] is True, (
        report["appendix_preservation_audit"]["issues"]
    )

    with ZipFile(output) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    captions = []
    for paragraph in root.iter(f"{{{WORD_NS}}}p"):
        style = paragraph.find(f"{{{WORD_NS}}}pPr/{{{WORD_NS}}}pStyle")
        if style is None or style.get(f"{{{WORD_NS}}}val") != CAPTION_STYLE_ID:
            continue
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
        instruction = "".join(
            node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}instrText")
        )
        captions.append((text, instruction))

    assert captions, "the appendix data table must carry a caption"
    for text, instruction in captions:
        assert text.strip() or "SEQ" in instruction, (text, instruction)


# ---------------------------------------------------------------------------
# D5: render map text for retained inline objects
# ---------------------------------------------------------------------------

def test_retained_field_text_matches_the_saved_output(tmp_path: Path) -> None:
    """A copied-back sequence field must not look like changed text."""

    source = tmp_path / "field.docx"
    document = _empty_template_document()
    paragraph = document.add_paragraph()
    paragraph.add_run("图 ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "SEQ Figure \\* ARABIC")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.set(qn("xml:space"), "preserve")
    text.text = "1"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)
    paragraph.add_run(" 演示图")
    document.save(str(source))

    output = tmp_path / "field.out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))
    report = result.as_report()

    assert report["saved_output_audit"]["passed"] is True, report["saved_output_audit"]["issues"]
    assert "paragraph_order_changed" not in {
        issue["code"] for issue in report["saved_output_audit"]["issues"]
    }
