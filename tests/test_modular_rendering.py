from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_normalization import _ast, _profile
from wxdf.auditing import check_model, check_saved_output
from wxdf.normalization import normalize
from wxdf.rendering import RenderError, render
import wxdf.template as template_module
from wxdf.validation import validate_ast


def test_ast_render_save_readback_and_independent_audit(tmp_path: Path) -> None:
    normalized = normalize(_ast(), _profile(), strategy="wx-default-v2")
    output = tmp_path / "candidate.docx"

    result = render(normalized, output)
    model_findings = check_model(normalized)
    findings = check_saved_output(output, normalized, result["render_map"])

    assert model_findings["passed"] is True
    assert findings["passed"] is True
    assert result["render_map"]["i1"]["paragraph"] == 1
    assert output.exists()


def test_render_rejects_missing_required_image_resource(tmp_path: Path) -> None:
    ast = _ast()
    ast["document"]["blocks"]["p3"]["inline_content"] = [{"kind": "image", "resource_id": "missing", "alt_text": "图"}]
    ast["document"]["resources"]["missing"] = {
        "kind": "image",
        "content_sha256": "sha256:" + "a" * 64,
        "source_ref": ast["document"]["blocks"]["p3"]["source_ref"],
    }
    normalized = normalize(ast, _profile(), strategy="legacy")

    with pytest.raises(RenderError, match="resource"):
        render(normalized, tmp_path / "missing-resource.docx")


def test_render_and_audit_table_children_independently(tmp_path: Path) -> None:
    ast = _ast()
    ast["document"]["blocks"]["t1"] = {
        "block_type": "table",
        "table_type": "data",
        "source_ref": ast["document"]["blocks"]["p1"]["source_ref"],
    }
    ast["document"]["blocks"]["p4"] = {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": "单元格", "marks": []}],
        "source_ref": ast["document"]["blocks"]["p1"]["source_ref"],
    }
    ast["document"]["containers"].update({
        "r1": {"kind": "table_row"},
        "c1": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1},
    })
    ast["document"]["children"].update({"r1": ["c1"], "c1": ["p4"]})
    ast["document"]["children"]["root"].append("t1")
    ast["document"]["children"]["t1"] = ["r1"]
    validate_ast(ast)

    bundle = template_module.load_template_bundle(SCRIPTS.parent / "assets" / "wx_template.docx")
    normalized = normalize(ast, bundle, strategy="wx-default-v2")
    output = tmp_path / "table.docx"
    result = render(normalized, output, template_bundle=bundle)

    findings = check_saved_output(output, normalized, result["render_map"])

    assert findings["passed"] is True
    assert findings["table_count"] == 1
    assert result["render_map"]["t1"]["kind"] == "table"
    saved_table = Document(output).tables[0]
    assert saved_table.style.name == "Table Grid"
    assert [paragraph.style.name for paragraph in saved_table.cell(0, 0).paragraphs] == ["表正文"]


def test_render_keeps_layout_table_paragraphs_out_of_table_body_contract(tmp_path: Path) -> None:
    ast = _ast()
    ast["document"]["blocks"]["t1"] = {
        "block_type": "table",
        "table_type": "layout",
        "layout": {
            "source_width": {"value": "4200", "type": "dxa"},
            "source_margins": {
                "top": {"value": "80", "type": "dxa"},
                "left": {"value": "100", "type": "dxa"},
                "bottom": {"value": "80", "type": "dxa"},
                "right": {"value": "100", "type": "dxa"},
            },
            "row_heights": [{"value": "360", "rule": "atLeast"}],
            "borders": {
                name: {"val": "none", "sz": None, "space": None, "color": None}
                for name in ("top", "left", "bottom", "right", "insideH", "insideV")
            },
            "autofit": False,
        },
        "source_ref": ast["document"]["blocks"]["p1"]["source_ref"],
    }
    ast["document"]["blocks"]["p4"] = {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": "布局内容", "marks": []}],
        "source_ref": ast["document"]["blocks"]["p1"]["source_ref"],
    }
    ast["document"]["containers"].update({
        "r1": {"kind": "table_row"},
        "c1": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1},
    })
    ast["document"]["children"].update({"r1": ["c1"], "c1": ["p4"]})
    ast["document"]["children"]["root"].append("t1")
    ast["document"]["children"]["t1"] = ["r1"]
    validate_ast(ast)

    bundle = template_module.load_template_bundle(SCRIPTS.parent / "assets" / "wx_template.docx")
    normalized = normalize(ast, bundle, strategy="wx-default-v2")
    output = tmp_path / "layout-table.docx"

    result = render(
        normalized,
        output,
        template_bundle=bundle,
        table_row_height_cm=2,
        table_row_height_rule="exact",
    )

    saved_table = Document(output).tables[0]
    paragraph = saved_table.cell(0, 0).paragraphs[0]
    findings = check_saved_output(output, normalized, result["render_map"])
    tbl_pr = saved_table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    width = tbl_pr.find(qn("w:tblW"))
    margins = tbl_pr.find(qn("w:tblCellMar"))
    height = saved_table.rows[0]._tr.find("w:trPr/w:trHeight", saved_table.rows[0]._tr.nsmap)
    assert paragraph.text == "布局内容"
    assert paragraph.style.name == "Normal"
    assert saved_table.style is None or saved_table.style.name != "Table Grid"
    assert tbl_pr.find(qn("w:tblStyle")) is None
    assert saved_table.autofit is False
    assert width is not None and width.get(qn("w:w")) == "4200" and width.get(qn("w:type")) == "dxa"
    assert margins is not None
    assert margins.find(qn("w:left")).get(qn("w:w")) == "100"
    assert height is not None
    assert height.get(qn("w:val")) == "360"
    assert height.get(qn("w:hRule")) == "atLeast"
    assert borders is not None
    assert all(borders.find(qn(f"w:{name}")).get(qn("w:val")) == "none" for name in ("top", "left", "bottom", "right", "insideH", "insideV"))
    assert findings["unexpected_styles"] == []


def test_template_bundle_preserves_toc_and_applies_title_style(tmp_path: Path) -> None:
    ast = _ast()
    ast["document"]["blocks"]["title"] = {
        "block_type": "paragraph",
        "role": "title",
        "inline_content": [{"kind": "text", "text": "模块化测试文档", "marks": []}],
        "source_ref": ast["document"]["blocks"]["p1"]["source_ref"],
    }
    ast["document"]["children"]["root"].insert(0, "title")
    bundle = template_module.load_template_bundle(SCRIPTS.parent / "assets" / "wx_template.docx")
    normalized = normalize(ast, bundle, strategy="wx-default-v2")
    output = tmp_path / "template-bundle.docx"

    result = render(normalized, output, template_bundle=bundle)

    document = Document(output)
    title = next(paragraph for paragraph in document.paragraphs if paragraph.text == "模块化测试文档")
    findings = check_saved_output(output, normalized, result["render_map"])
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        settings_xml = archive.read("word/settings.xml").decode("utf-8")
    assert title.style.name == "文档标题"
    assert document_xml.count("TOC \\o") == 1
    assert "updateFields" in settings_xml
    assert normalized["presentation"]["template_contract"]["toc"]["mode"] == "template_fragment"
    assert findings["unexpected_styles"] == []
    assert findings["passed"] is True


def test_independent_audit_detects_unexpected_saved_paragraph_style(tmp_path: Path) -> None:
    ast = _ast()
    ast["document"]["blocks"]["title"] = {
        "block_type": "paragraph",
        "role": "title",
        "inline_content": [{"kind": "text", "text": "模块化测试文档", "marks": []}],
        "source_ref": ast["document"]["blocks"]["p1"]["source_ref"],
    }
    ast["document"]["children"]["root"].insert(0, "title")
    bundle = template_module.load_template_bundle(SCRIPTS.parent / "assets" / "wx_template.docx")
    normalized = normalize(ast, bundle, strategy="wx-default-v2")
    output = tmp_path / "unexpected-style.docx"
    result = render(normalized, output, template_bundle=bundle)
    document = Document(output)
    unexpected = document.styles.add_style("T06 Unexpected", WD_STYLE_TYPE.PARAGRAPH)
    title = next(paragraph for paragraph in document.paragraphs if paragraph.text == "模块化测试文档")
    title.style = unexpected
    document.save(output)

    findings = check_saved_output(output, normalized, result["render_map"])

    assert findings["unexpected_styles"] == ["T06 Unexpected"]
    assert findings["passed"] is False
    assert any(issue["code"] == "unexpected_styles" for issue in findings["issues"])


def test_independent_audit_detects_saved_paragraph_reordering(tmp_path: Path) -> None:
    normalized = normalize(_ast(), _profile(), strategy="wx-default-v2")
    output = tmp_path / "tampered.docx"
    result = render(normalized, output)
    document = Document(output)
    body = document._element.body
    paragraphs = list(document.paragraphs)
    body.remove(paragraphs[1]._p)
    body.insert(0, paragraphs[1]._p)
    document.save(output)

    findings = check_saved_output(output, normalized, result["render_map"])

    assert findings["passed"] is False
    assert any(issue["code"] == "paragraph_order_changed" for issue in findings["issues"])
