from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.auditing import check_saved_output
from wxdf.contracts import ContractError
from wxdf.kernel import ConversionKernel
from wxdf.rendering import _caption_audit_inline, _write_caption
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import _parallel_clause_segments
from wxdf.validation import validate_ast

TEMPLATE = ROOT / "assets" / "wx_template.docx"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _source_with_rich_caption(path: Path) -> Path:
    document = Document()
    document.add_heading("第一章", level=1)
    image_path = path / "figure.png"
    Image.new("RGB", (24, 18), (60, 90, 120)).save(image_path)
    document.add_paragraph().add_run().add_picture(str(image_path))
    caption = document.add_paragraph(style="Caption")
    number_anchor_start = OxmlElement("w:bookmarkStart")
    number_anchor_start.set(qn("w:id"), "38")
    number_anchor_start.set(qn("w:name"), "caption_number")
    caption._p.append(number_anchor_start)
    prefix = caption.add_run("图4-4 ")
    prefix.bold = True
    number_anchor_end = OxmlElement("w:bookmarkEnd")
    number_anchor_end.set(qn("w:id"), "38")
    caption._p.append(number_anchor_end)
    body = caption.add_run("流程")
    body.bold = True

    hyperlink = OxmlElement("w:hyperlink")
    relationship_id = caption.part.relate_to("https://example.com/guide", RT.HYPERLINK, is_external=True)
    hyperlink.set(qn("r:id"), relationship_id)
    link_run = OxmlElement("w:r")
    link_text = OxmlElement("w:t")
    link_text.text = "链接"
    link_run.append(link_text)
    hyperlink.append(link_run)
    caption._p.append(hyperlink)

    caption.add_run("\t")
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "37")
    start.set(qn("w:name"), "caption_ref")
    caption._p.append(start)
    for kind, value in (("begin", None), ("instrText", " REF caption_ref \\h "), ("separate", None), ("text", "图4-4"), ("end", None)):
        run = OxmlElement("w:r")
        if kind in {"begin", "separate", "end"}:
            field_char = OxmlElement("w:fldChar")
            field_char.set(qn("w:fldCharType"), kind)
            run.append(field_char)
        elif kind == "instrText":
            instruction = OxmlElement("w:instrText")
            instruction.set(qn("xml:space"), "preserve")
            instruction.text = value
            run.append(instruction)
        else:
            text = OxmlElement("w:t")
            text.text = value
            run.append(text)
        caption._p.append(run)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "37")
    caption._p.append(end)
    math = OxmlElement("m:oMath")
    math_run = OxmlElement("m:r")
    math_text = OxmlElement("m:t")
    math_text.text = "x"
    math_run.append(math_text)
    math.append(math_run)
    caption._p.append(math)
    caption.add_run().add_break()
    page_run = caption.add_run()
    page_run.add_break(WD_BREAK.PAGE)
    column_run = caption.add_run()
    column_run.add_break(WD_BREAK.COLUMN)

    source = path / "rich-caption.docx"
    document.save(source)
    return source


def _convert(tmp_path: Path, source: Path, monkeypatch, *, template_path: Path = TEMPLATE) -> tuple[Path, object, dict, dict]:
    import wxdf.rendering as rendering_module

    captured: dict[str, object] = {}
    original = rendering_module.render

    def capture_render(normalized, output_path, **kwargs):
        outcome = original(normalized, output_path, **kwargs)
        captured["render_map"] = outcome["render_map"]
        captured["normalized"] = normalized
        return outcome

    monkeypatch.setattr(rendering_module, "render", capture_render)
    output = tmp_path / "rich-caption-output.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=template_path,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off", "delivery_policy": "desktop"},
    )
    result = ConversionKernel().convert(request)
    return output, result, captured["normalized"], captured["render_map"]


def _caption_paragraph(path: Path):
    document = Document(path)
    return next(paragraph for paragraph in document.paragraphs if "流程" in paragraph.text)


def test_caption_round_trip_preserves_rich_inline_objects_and_break_types(tmp_path: Path, monkeypatch) -> None:
    source = _source_with_rich_caption(tmp_path)
    output, result, normalized, render_map = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    assert render_map["__artifact__"]["retained_inline_run_provenance"]
    assert render_map["__artifact__"]["retained_inline_element_provenance"]
    paragraph = _caption_paragraph(output)
    paragraph_xml = paragraph._p
    assert paragraph.runs[0].text == "图 ", [(k, v.get("role"), v.get("text")) for k, v in normalized["source_ast"]["document"]["blocks"].items()]
    assert any(run.bold and run.text == "流程" for run in paragraph.runs)
    assert paragraph_xml.find("w:hyperlink", namespaces={"w": W[1:-1]}) is not None
    assert paragraph_xml.find(".//w:bookmarkStart", namespaces={"w": W[1:-1]}) is not None
    assert any(node.tag.endswith("}oMath") for node in paragraph_xml.iter())
    assert "REF caption_ref" in "".join(node.text or "" for node in paragraph_xml.findall(".//w:instrText", namespaces={"w": W[1:-1]}))
    breaks = [node.get(qn("w:type")) or "line" for node in paragraph_xml.iter(qn("w:br"))]
    assert breaks == ["line", "page", "column"]
    children = list(paragraph_xml)
    number_anchor_start = next(node for node in children if node.tag == qn("w:bookmarkStart") and node.get(qn("w:name")) == "caption_number")
    number_anchor_end = next(node for node in children if node.tag == qn("w:bookmarkEnd") and node.get(qn("w:id")) == "38")
    sequence_field_ends = [node for node in paragraph_xml.iter(qn("w:fldChar")) if node.get(qn("w:fldCharType")) == "end"]
    body_run = next(node for node in children if node.tag == qn("w:r") and any((text.text or "").strip() == "流程" for text in node.iter(qn("w:t"))))
    assert children.index(number_anchor_start) < children.index(paragraph.runs[0]._r)
    prefix_field_ends = [node for node in sequence_field_ends if children.index(node.getparent()) < children.index(number_anchor_end)]
    assert prefix_field_ends
    assert any("SEQ 图" in (instruction.text or "") for instruction in paragraph_xml.iter(qn("w:instrText")))
    assert children.index(number_anchor_end) < children.index(body_run)

    original_audit = check_saved_output(output, normalized, render_map)
    assert original_audit["passed"] is True, original_audit["issues"]

    source_ast = normalized["source_ast"]
    caption_block = next(block for block in source_ast["document"]["blocks"].values() if block.get("role") == "caption")
    page_break = next(item for item in caption_block["inline_content"] if item.get("kind") == "break" and item.get("break_type") == "page")
    page_break["break_type"] = "diagonal"
    try:
        validate_ast(source_ast)
    except ContractError as error:
        assert error.code == "invalid_break_type"
    else:
        raise AssertionError("unsupported break_type was accepted")

    for tamper in ("link", "page-break", "bookmark", "ref-field", "math"):
        modified = tmp_path / f"tampered-{tamper}.docx"
        doc = Document(output)
        para = next(p for p in doc.paragraphs if "流程" in p.text)
        if tamper == "link":
            link = para._p.find("w:hyperlink", namespaces={"w": W[1:-1]})
            para._p.remove(link)
        else:
            if tamper == "page-break":
                page_break = next(br for br in para._p.iter(qn("w:br")) if br.get(qn("w:type")) == "page")
                page_break.set(qn("w:type"), "textWrapping")
            elif tamper == "bookmark":
                bookmark = next(node for node in para._p.iter(qn("w:bookmarkStart")))
                para._p.remove(bookmark)
            elif tamper == "ref-field":
                instruction = next(node for node in para._p.iter(qn("w:instrText")) if "REF caption_ref" in (node.text or ""))
                instruction.getparent().getparent().remove(instruction.getparent())
            else:
                math = next(node for node in para._p.iter() if node.tag.endswith("}oMath"))
                para._p.remove(math)
        doc.save(modified)
        audit = check_saved_output(modified, normalized, render_map)
        assert audit["passed"] is False
        expected_codes = {"retained_inline_changed"} if tamper in {"bookmark", "ref-field", "math"} else {"inline_content_changed", "inline_paragraph_missing"}
        assert any(issue["code"] in expected_codes for issue in audit["issues"]), audit["issues"]


def test_page_and_column_breaks_do_not_split_parallel_list_clauses() -> None:
    unit = {
        "inline_content": [
            {"kind": "text", "text": "应当落实访问控制；"},
            {"kind": "break", "break_type": "page"},
            {"kind": "text", "text": "应当保留审计记录。"},
        ]
    }
    assert _parallel_clause_segments(unit) is None


def test_custom_sequence_identifier_keeps_the_source_visible_label() -> None:
    paragraph = Document().add_paragraph()
    block = {
        "role": "caption",
        "caption_type": "figure",
        "inline_content": [{"kind": "text", "text": "图4-4 流程", "marks": []}],
    }
    _write_caption(
        paragraph,
        block,
        {},
        {"figure_sequence": "图解"},
        {"chapter": 3, "sequence": 1},
    )
    assert paragraph.runs[0].text == "图 "
    assert "SEQ 图解" in "".join(node.text or "" for node in paragraph._p.iter(qn("w:instrText")))
    contract = _caption_audit_inline(block, chapter_numbered=True)
    assert contract[0] == {"kind": "text", "text": "图 - "}


def test_custom_sequence_identifier_end_to_end_keeps_source_label_and_render_map_text(tmp_path: Path, monkeypatch) -> None:
    custom_template = Document(TEMPLATE)
    for instruction in custom_template.element.iter(qn("w:instrText")):
        if "SEQ 图 " in (instruction.text or ""):
            instruction.text = instruction.text.replace("SEQ 图 ", "SEQ 图解 ")
    template_path = tmp_path / "custom-sequence-template.docx"
    custom_template.save(template_path)
    source = _source_with_rich_caption(tmp_path)

    output, result, _, render_map = _convert(tmp_path, source, monkeypatch, template_path=template_path)

    assert result.exit_code == 0, result.diagnostics
    paragraph = _caption_paragraph(output)
    assert paragraph.text.startswith("图 1-1 流程")
    assert "SEQ 图解" in "".join(node.text or "" for node in paragraph._p.iter(qn("w:instrText")))
    mapped = next(item for item in render_map.values() if item.get("kind") == "paragraph" and item.get("role") == "caption")
    assert mapped["text"] == "图 1-1 流程链接\t图4-4\n\n\n"


def test_unconsumable_caption_prefix_is_reported_without_dropping_source_field(tmp_path: Path, monkeypatch) -> None:
    document = Document()
    document.add_heading("第一章", level=1)
    image_path = tmp_path / "atomic-prefix-figure.png"
    Image.new("RGB", (24, 18), (60, 90, 120)).save(image_path)
    document.add_paragraph().add_run().add_picture(str(image_path))
    caption = document.add_paragraph(style="Caption")
    caption.add_run("图")
    for kind, value in (("begin", None), ("instrText", " REF figure_number "), ("separate", None), ("text", "4-4 "), ("end", None)):
        run = OxmlElement("w:r")
        if kind in {"begin", "separate", "end"}:
            field_char = OxmlElement("w:fldChar")
            field_char.set(qn("w:fldCharType"), kind)
            run.append(field_char)
        elif kind == "instrText":
            instruction = OxmlElement("w:instrText")
            instruction.set(qn("xml:space"), "preserve")
            instruction.text = value
            run.append(instruction)
        else:
            text = OxmlElement("w:t")
            text.text = value
            run.append(text)
        caption._p.append(run)
    caption.add_run("流程")
    source = tmp_path / "atomic-caption-prefix.docx"
    document.save(source)

    output, result, _, render_map = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    diagnostic = next(item for item in result.diagnostics if item["code"] == "caption_number_prefix_not_consumed")
    assert diagnostic["severity"] == "warning"
    assert result.status == "completed_with_warnings"
    paragraph = _caption_paragraph(output)
    assert "REF figure_number" in "".join(node.text or "" for node in paragraph._p.iter(qn("w:instrText")))
    caption_map = next(item for item in render_map.values() if item.get("role") == "caption")
    assert caption_map["text"].startswith("图 1-1 图4-4")


def test_page_only_paragraph_is_excluded_from_text_order_but_still_audited(tmp_path: Path) -> None:
    document = Document()
    page_only = document.add_paragraph()
    page_only.add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("正文")
    output = tmp_path / "page-only.docx"
    document.save(output)
    render_map = {
        "page-break": {
            "kind": "paragraph",
            "text": "\n",
            "inline_content": [{"kind": "break", "break_type": "page"}],
        },
        "body": {"kind": "paragraph", "text": "正文", "inline_content": [{"kind": "text", "text": "正文"}]},
    }
    expected = {"expected_table_count": 0}
    assert check_saved_output(output, expected, render_map)["passed"] is True

    damaged = Document(output)
    damaged.paragraphs[0]._p.clear_content()
    damaged_path = tmp_path / "page-only-removed.docx"
    damaged.save(damaged_path)
    audit = check_saved_output(damaged_path, expected, render_map)
    assert audit["passed"] is False
    assert any(issue["code"] == "inline_paragraph_missing" for issue in audit["issues"])
