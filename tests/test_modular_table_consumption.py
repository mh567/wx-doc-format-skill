from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.parsers import parse
from wxdf.semantics.tables import collect_source_table_semantics


def _make_formula_layout(document: Document):
    table = document.add_table(rows=1, cols=2)
    table.autofit = False
    table.cell(0, 0).paragraphs[0]._p.append(OxmlElement("m:oMath"))
    table.cell(0, 1).text = "(1)"
    borders = OxmlElement("w:tblBorders")
    for name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{name}")
        edge.set(qn("w:val"), "none")
        borders.append(edge)
    table._tbl.tblPr.append(borders)
    return table


def test_docx_source_facts_fix_data_and_layout_table_semantics(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    data = document.add_table(rows=2, cols=2)
    data.cell(0, 0).text = "名称"
    data.cell(0, 1).text = "说明"
    data.cell(1, 0).text = "A"
    data.cell(1, 1).text = "示例"
    _make_formula_layout(document)
    document.save(source)

    facts = parse(source)
    semantics = collect_source_table_semantics(facts)

    assert [item["table_type"] for item in semantics] == ["data", "layout"]
    assert [item["header_rows"] for item in semantics] == [1, 0]
    assert semantics[0]["caption_eligible"] is True
    assert semantics[1]["caption_eligible"] is False
    assert semantics[1]["layout_contract"]["autofit"] is False
    assert semantics[1]["layout_contract"]["borders"]["top"]["val"] == "none"
