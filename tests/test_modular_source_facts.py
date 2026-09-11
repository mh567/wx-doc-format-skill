from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from modular_support import build_docx_fixture
from wxdf.parsers import parse, summarize_source_facts
from wxdf.semantics.source_ast import build_source_ast
from wxdf.source_store import SourceStore
from wxdf.validation import validate_source_facts


def test_markdown_parser_emits_stable_public_facts_and_inline_resources(tmp_path: Path) -> None:
    asset = tmp_path / "pixel.bin"
    asset.write_bytes(b"pixel-data")
    source = tmp_path / "source.md"
    source.write_text(
        "# 标题\n\n1. 父项\n   - 子项\n\n混合 [链接](https://example.org) ![图](pixel.bin)\n",
        encoding="utf-8",
    )

    first = parse(source)
    second = parse(source)

    validate_source_facts(first)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert first["source"]["source_type"] == "markdown"
    assert first["containers"]["root"]["kind"] == "document"
    assert first["resources"]
    assert any(item.get("kind") == "link" for unit in first["units"].values() for item in unit.get("inline_content", []))
    assert any(item.get("kind") == "image" for unit in first["units"].values() for item in unit.get("inline_content", []))
    assert any(evidence.get("marker_kind") == "ordered" for evidence in first["evidence"].values())
    assert any(evidence.get("marker_kind") == "unordered" for evidence in first["evidence"].values())


def test_docx_parser_emits_numbering_indentation_and_table_scope_facts(tmp_path: Path) -> None:
    source = build_docx_fixture(tmp_path)
    document = Document(source)
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "表格单元格"
    document.save(source)

    facts = parse(source)

    validate_source_facts(facts)
    assert facts["source"]["source_type"] == "docx"
    assert any(container.get("kind") == "table" for container in facts["containers"].values())
    list_evidence = [item for item in facts["evidence"].values() if item.get("marker_kind")]
    assert list_evidence
    assert all("indent_value" in item and "indent_unit" in item for item in list_evidence)
    assert all("numbering_definition_ref" in item for item in list_evidence)


def test_docx_layout_paragraph_format_is_captured_and_propagated_by_whitelist(
    tmp_path: Path,
) -> None:
    source = tmp_path / "layout-format.docx"
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).paragraphs[0]._p.append(OxmlElement("m:oMath"))
    paragraph = table.cell(0, 1).paragraphs[0]
    paragraph.text = "(1)"
    paragraph.style = "Quote"
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph.paragraph_format.space_before = Pt(7)
    paragraph.paragraph_format.space_after = Pt(13)
    paragraph.paragraph_format.line_spacing = 1.5
    paragraph.paragraph_format.left_indent = Inches(0.2)
    paragraph.paragraph_format.right_indent = Inches(0.1)
    paragraph.paragraph_format.first_line_indent = Inches(0.15)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.keep_together = True
    paragraph.paragraph_format.page_break_before = True
    paragraph.paragraph_format.widow_control = False
    borders = OxmlElement("w:tblBorders")
    for name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{name}")
        edge.set(qn("w:val"), "none")
        borders.append(edge)
    table._tbl.tblPr.append(borders)
    document.save(source)

    facts = parse(source)
    paragraph_id, unit = next(
        (unit_id, unit)
        for unit_id, unit in facts["units"].items()
        if any(item.get("text") == "(1)" for item in unit.get("inline_content", []))
    )
    expected = {
        "alignment": "right",
        "spacing": {"before": "140", "after": "260", "line": "360", "lineRule": "auto"},
        "indentation": {"left": "288", "right": "144", "firstLine": "216"},
        "pagination": {
            "keepNext": None,
            "keepLines": None,
            "pageBreakBefore": None,
            "widowControl": "0",
        },
    }

    assert facts["containers"][next(
        node_id
        for node_id, container in facts["containers"].items()
        if container.get("kind") == "table"
    )]["table_semantics"]["table_type"] == "layout"
    assert unit["source_paragraph_format"] == expected
    assert build_source_ast(facts)["document"]["blocks"][paragraph_id]["source_paragraph_format"] == expected


def test_source_store_opens_source_once_and_resolves_declared_parts(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文\n", encoding="utf-8")
    store = SourceStore()

    handle = store.open_source(source)
    same_handle = store.open_source(source)
    payload = store.resolve({"source_id": handle.source_id, "part": source.name})

    assert same_handle == handle
    assert store.open_count == 1
    assert payload == "正文\n"
    store.close()
    assert store.is_open is False


def test_same_consumer_accepts_an_independent_parser_adapter(tmp_path: Path) -> None:
    source = parse(tmp_path / "unused.md", {"text": "# 外部适配器\n"})

    class IndependentParser:
        def parse(self):
            return source

    facts = IndependentParser().parse()
    summary = summarize_source_facts(facts)

    assert summary["source_type"] == "markdown"
    assert summary["unit_count"] == len(facts["units"])
    assert "private_tokenizer" not in summary
