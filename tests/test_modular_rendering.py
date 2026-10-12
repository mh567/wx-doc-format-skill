from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.parts.image import ImagePart
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.shape import CT_Inline
from docx.shared import Emu
from lxml import etree


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_normalization import _ast, _profile
from wxdf.auditing import check_model, check_saved_output
from wxdf.normalization import normalize
from wxdf.parsers import parse
from wxdf.rendering import RenderError, _remove_empty_body_paragraphs, render
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


def test_render_preserves_emf_bytes_relationship_content_type_extent_and_audit(tmp_path: Path) -> None:
    ast = _ast()
    emf = b"\x01\x00\x00\x00native-emf-bytes"
    source_ref = ast["document"]["blocks"]["p3"]["source_ref"]
    ast["document"]["blocks"]["p3"]["inline_content"] = [
        {
            "kind": "image",
            "resource_id": "emf-1",
            "alt_text": "EMF",
            "display_size": {"width_emu": 914400, "height_emu": 457200},
            "source_embedding": "drawingml",
        },
        {
            "kind": "image",
            "resource_id": "emf-1",
            "alt_text": "EMF",
            "display_size": {"width_emu": 457200, "height_emu": 228600},
            "source_embedding": "drawingml",
        },
    ]
    ast["document"]["resources"]["emf-1"] = {
        "kind": "image",
        "media_type": "image/x-emf",
        "content_sha256": "sha256:" + hashlib.sha256(emf).hexdigest(),
        "source_ref": source_ref,
    }
    normalized = normalize(ast, _profile(), strategy="legacy")
    output = tmp_path / "emf.docx"

    result = render(normalized, output, resources={"emf-1": emf})
    findings = check_saved_output(output, normalized, result["render_map"])

    with ZipFile(output) as archive:
        names = set(archive.namelist())
        document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
        rels_xml = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
        content_types_xml = archive.read("[Content_Types].xml")
        image_names = [name for name in names if name.endswith(".emf")]
        image_name = image_names[0]
        image_bytes = archive.read(image_name)

    extents = document_xml.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent")
    image_relationships = [relationship for relationship in rels_xml if relationship.get("Target", "").endswith(".emf")]
    image_embed_ids = [
        blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        for blip in document_xml.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip")
    ]
    assert image_bytes == emf
    assert len(image_names) == 1
    assert b"ContentType=\"image/x-emf\"" in content_types_xml
    assert len(image_relationships) == 1
    assert image_relationships[0].get("Type", "").endswith("/image")
    assert len(image_embed_ids) == 2 and len(set(image_embed_ids)) == 1
    assert [
        (extent.get("cx"), extent.get("cy"))
        for extent in extents[-2:]
    ] == [("914400", "457200"), ("457200", "228600")]
    assert findings["passed"] is True


def test_render_preserves_wmf_bytes_relationship_content_type_extent_and_reuse(tmp_path: Path) -> None:
    ast = _ast()
    wmf = b"\xd7\xcd\xc6\x9a native-wmf-bytes"
    source_ref = ast["document"]["blocks"]["p3"]["source_ref"]
    image_token = {
        "kind": "image",
        "resource_id": "wmf-1",
        "alt_text": "WMF",
        "display_size": {"width_emu": 914400, "height_emu": 457200},
        "source_embedding": "vml",
    }
    ast["document"]["blocks"]["p3"]["inline_content"] = [dict(image_token), dict(image_token)]
    ast["document"]["resources"]["wmf-1"] = {
        "kind": "image",
        "media_type": "image/x-wmf",
        "content_sha256": "sha256:" + hashlib.sha256(wmf).hexdigest(),
        "source_ref": source_ref,
    }
    normalized = normalize(ast, _profile(), strategy="legacy")
    output = tmp_path / "wmf.docx"

    result = render(normalized, output, resources={"wmf-1": wmf})
    findings = check_saved_output(output, normalized, result["render_map"])

    with ZipFile(output) as archive:
        names = set(archive.namelist())
        document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
        rels_xml = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
        content_types_xml = archive.read("[Content_Types].xml")
        image_names = [name for name in names if name.endswith(".wmf")]
        image_bytes = archive.read(image_names[0])

    image_relationships = [relationship for relationship in rels_xml if relationship.get("Target", "").endswith(".wmf")]
    image_embed_ids = [
        blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        for blip in document_xml.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip")
    ]
    extents = document_xml.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent")
    assert len(image_names) == 1
    assert image_bytes == wmf
    assert b"ContentType=\"image/x-wmf\"" in content_types_xml
    assert len(image_relationships) == 1
    assert image_relationships[0].get("Type", "").endswith("/image")
    assert len(image_embed_ids) == 2 and len(set(image_embed_ids)) == 1
    assert [(extent.get("cx"), extent.get("cy")) for extent in extents[-2:]] == [
        ("914400", "457200"),
        ("914400", "457200"),
    ]
    assert findings["passed"] is True


@pytest.mark.parametrize("media_type", ["image/x-emf", "image/x-wmf"])
def test_render_rejects_native_media_without_display_size(tmp_path: Path, media_type: str) -> None:
    ast = _ast()
    suffix = ".emf" if media_type.endswith("emf") else ".wmf"
    payload = b"native" + suffix.encode("ascii")
    source_ref = dict(ast["document"]["blocks"]["p3"]["source_ref"])
    source_ref["part"] = f"word/media/image1{suffix}"
    ast["document"]["blocks"]["p3"]["inline_content"] = [{
        "kind": "image",
        "resource_id": "native-missing-size",
        "alt_text": "native",
    }]
    ast["document"]["resources"]["native-missing-size"] = {
        "kind": "image",
        "media_type": media_type,
        "content_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "source_ref": source_ref,
    }
    normalized = normalize(ast, _profile(), strategy="legacy")

    with pytest.raises(RenderError, match="display_size"):
        render(normalized, tmp_path / f"missing-size{suffix}.docx", resources={"native-missing-size": payload})


def test_docx_media_parser_contract_extracts_drawingml_and_vml_sizes(tmp_path: Path) -> None:
    document = Document()
    package = document.part.package

    def add_media(content_type: str, suffix: str) -> str:
        partname = package.next_partname("/word/media/image%d" + suffix)
        part = ImagePart(partname, content_type, b"raw-" + suffix.encode("ascii"))
        return document.part.relate_to(part, RT.IMAGE)

    drawing_run = document.add_paragraph().add_run()
    drawing_rid = add_media("image/x-emf", ".emf")
    drawing_run._r.append(CT_Inline.new_pic_inline(
        1,
        drawing_rid,
        "image.emf",
        Emu(914400),
        Emu(457200),
    ))

    for width, height in (("5in", "30pt"), ("414pt", "3in")):
        vml_run = document.add_paragraph().add_run()
        vml_rid = add_media("image/x-wmf", ".wmf")
        pict = OxmlElement("w:pict")
        shape = etree.Element("{urn:schemas-microsoft-com:vml}shape")
        shape.set("style", f"width:{width};height:{height}")
        imagedata = etree.Element("{urn:schemas-microsoft-com:vml}imagedata")
        imagedata.set(qn("r:id"), vml_rid)
        shape.append(imagedata)
        pict.append(shape)
        vml_run._r.append(pict)

    source = tmp_path / "media.docx"
    document.save(source)
    facts = parse(source)
    images = [
        token
        for unit in facts["units"].values()
        for token in unit.get("inline_content", [])
        if token.get("kind") == "image"
    ]
    drawing = next(token for token in images if token["source_embedding"] == "drawingml")
    vml_sizes = [token["display_size"] for token in images if token["source_embedding"] == "vml"]
    assert drawing["display_size"] == {"width_emu": 914400, "height_emu": 457200}
    assert vml_sizes == [
        {"width_emu": 4572000, "height_emu": 381000},
        {"width_emu": 5257800, "height_emu": 2743200},
    ]
    assert facts["resources"][drawing["resource_id"]]["media_type"] == "image/x-emf"


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


def test_saved_output_audit_keeps_tab_only_paragraph_in_order(tmp_path: Path) -> None:
    ast = _ast()
    ast["document"]["blocks"]["p3"]["inline_content"] = [{"kind": "tab"}]
    normalized = normalize(ast, _profile(), strategy="legacy")
    output = tmp_path / "tab-only.docx"
    result = render(normalized, output)

    findings = check_saved_output(output, normalized, result["render_map"])

    assert not any(issue["code"] == "paragraph_order_changed" for issue in findings["issues"]), findings


def test_saved_output_audit_accepts_cleanup_of_line_break_only_paragraph(tmp_path: Path) -> None:
    ast = _ast()
    ast["document"]["blocks"]["p3"]["inline_content"] = [{"kind": "break"}]
    normalized = normalize(ast, _profile(), strategy="legacy")
    output = tmp_path / "break-only.docx"
    result = render(normalized, output)

    findings = check_saved_output(output, normalized, result["render_map"])

    assert not any(issue["code"] == "paragraph_order_changed" for issue in findings["issues"]), findings
    assert findings["passed"] is True
    assert result["diagnostics"] == [
        {"code": "empty_body_paragraphs_removed", "count": 1},
    ]


def test_empty_paragraph_cleanup_preserves_page_and_section_breaks() -> None:
    document = Document()
    document.add_paragraph("正文")
    document.add_paragraph()
    soft_break = document.add_paragraph()
    soft_break.add_run().add_break()
    page_break = document.add_paragraph()
    page_break.add_run().add_break(WD_BREAK.PAGE)
    section_paragraph = document.add_paragraph()
    section_properties = OxmlElement("w:sectPr")
    section_paragraph._p.get_or_add_pPr().append(section_properties)

    removed = _remove_empty_body_paragraphs(document)

    assert len(removed) == 2
    assert len(document.paragraphs) == 3
    assert document.paragraphs[1]._p.find(".//" + qn("w:br")).get(qn("w:type")) == "page"
    assert document.paragraphs[2]._p.find(".//" + qn("w:sectPr")) is not None


def test_saved_output_audit_keeps_tab_inside_numbered_item_text(tmp_path: Path) -> None:
    ast = _ast()
    for block_id in ("p1", "p2"):
        ast["document"]["blocks"].pop(block_id)
    for container_id in ("l2", "i2", "i3"):
        ast["document"]["containers"].pop(container_id)
    ast["document"]["blocks"]["p3"]["inline_content"] = [
        {"kind": "text", "text": "字段", "marks": []},
        {"kind": "tab"},
        {"kind": "text", "text": "说明", "marks": []},
    ]
    ast["document"]["containers"]["l1"] = {
        "kind": "list",
        "list_kind": "ordered",
        "source_start": 1,
        "source_sequence_id": "sequence-1",
    }
    ast["document"]["containers"]["i1"] = {
        "kind": "list_item",
        "source_marker": "1.",
        "source_ordinal": 1,
        "source_sequence_id": "sequence-1",
    }
    ast["document"]["children"] = {
        "root": ["l1"],
        "l1": ["i1"],
        "i1": ["p3"],
    }
    normalized = normalize(ast, _profile(), strategy="legacy")
    output = tmp_path / "numbered-tab.docx"
    result = render(normalized, output)

    findings = check_saved_output(output, normalized, result["render_map"])

    assert not any(issue["code"] == "list_numbering_missing" for issue in findings["issues"]), findings


def test_the_inline_contract_treats_a_preserved_field_as_opaque() -> None:
    """Both sides drop a retained field, however its cached result is stored.

    The source keeps a cross-reference as a retained field atom (decision 9) and
    the saved paragraph holds the source's field XML (begin / instruction /
    separate / result / end).  A preserved field is not a contract item, so the
    expected signature and the saved-XML signature must agree even when the cached
    result is split across several runs.
    """

    from wxdf.auditing import _inline_signature, _xml_inline_signature

    inline_content = [
        {"kind": "text", "text": "错误码，正常回值为0，"},
        {"kind": "tab"},
        {"kind": "text", "text": "其他见"},
        {"kind": "field", "resource_id": "res-1", "instruction": " REF _Ref9485 \\h ", "cached_result": "表 159"},
    ]
    paragraph = etree.fromstring(
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:r><w:t>错误码，正常回值为0，</w:t></w:r>"
        "<w:r><w:tab/></w:r>"
        "<w:r><w:t>其他见</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> REF _Ref9485 \\h </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>表 </w:t></w:r><w:r><w:t>159</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        "</w:p>"
    )

    assert _inline_signature(inline_content) == _xml_inline_signature(paragraph, {})
    assert _inline_signature(inline_content) == [
        {"kind": "text", "text": "错误码，正常回值为0，"},
        {"kind": "tab"},
        {"kind": "text", "text": "其他见"},
    ]


def test_the_inline_contract_reads_a_rebuilt_atom_as_text() -> None:
    """An atom with no retained payload is rebuilt as one text run on both sides."""

    from wxdf.auditing import _inline_signature, _xml_inline_signature

    inline_content = [
        {"kind": "text", "text": "见"},
        {"kind": "field", "cached_result": "表 159"},
    ]
    paragraph = etree.fromstring(
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:r><w:t>见</w:t></w:r><w:r><w:t>表 159</w:t></w:r>"
        "</w:p>"
    )

    assert _inline_signature(inline_content) == _xml_inline_signature(paragraph, {})


def test_a_simple_field_is_opaque_to_the_inline_contract() -> None:
    """A ``w:fldSimple`` result is not a contract item on either side."""

    from wxdf.auditing import _inline_signature, _xml_inline_signature

    inline_content = [{"kind": "text", "text": "见"}, {"kind": "field", "resource_id": "res-1"}]
    paragraph = etree.fromstring(
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:r><w:t>见</w:t></w:r>"
        '<w:fldSimple w:instr=" REF x "><w:r><w:t>表 1</w:t></w:r></w:fldSimple>'
        "</w:p>"
    )

    assert _inline_signature(inline_content) == _xml_inline_signature(paragraph, {})
