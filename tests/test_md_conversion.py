from __future__ import annotations

import sys
import base64
from copy import deepcopy
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from md_pipeline import audit_markdown_preservation, parse_md_to_model
from main import audit_document_wrapper
from audit import audit_document, markdown_residue_reasons
from model_normalization import normalize_document_model
from reporting import new_report
from template_profile import load_template_profile, template_numbering_ids
from word_model_renderer import render_document_model
from text_utils import build_document_model_from_output
from fallback_styles import clear_document_body
from document_model import appendix_block, append_block, new_document_model


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


def test_markdown_inline_syntax_does_not_reach_rendered_document(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(
        """# 标题

1. `代码` **强调** [链接](https://example.com)

名称 | 值
--- | ---
甲 | `单元格代码` **单元格强调**
""",
        encoding="utf-8",
    )
    report = new_report("test")
    source_model = parse_md_to_model(source, report, skill_version=lambda: "test")
    model = normalize_document_model(source_model, report)
    profile = load_template_profile(TEMPLATE)
    doc = Document(TEMPLATE)
    render_document_model(
        model,
        doc,
        report,
        0.69,
        "atLeast",
        template_numbering_ids(profile),
        template_profile=profile,
    )

    rendered_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    rendered_cells = "\n".join(
        paragraph.text
        for table in doc.tables
        for row in table.rows
        for cell in row.cells
        for paragraph in cell.paragraphs
    )
    assert "`" not in rendered_text + rendered_cells
    assert "**" not in rendered_text + rendered_cells
    assert "[链接](" not in rendered_text + rendered_cells
    assert report["source_lists"]["detected"] == 1
    audit = audit_document_wrapper(
        doc,
        0.69,
        "atLeast",
        template_profile=profile,
        table_roles=["data"],
    )
    assert audit["markdown_residue"] == []


def test_markdown_residue_audit_ignores_identifier_underscores() -> None:
    assert markdown_residue_reasons("client_id 与 redirect_uri 必须精确匹配。") == []
    assert markdown_residue_reasons("__Host-PORTAL_SESSION=<opaque value>") == []


def test_markdown_inline_code_spaces_links_and_breaks_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "inline.md"
    source.write_text("# 标题\n\n`a  b` [链接](https://example.com)  \n下一行\n", encoding="utf-8")
    report = new_report("test")
    model = parse_md_to_model(source, report, skill_version=lambda: "test")
    body = next(block for block in model["document"]["blocks"] if block.get("block_type") == "body")

    assert body["text"] == "a  b 链接 (https://example.com)\n下一行"


def test_markdown_residue_skips_code_block_by_paragraph_identity() -> None:
    assert markdown_residue_reasons("`template literal`") == ["code"]
    doc = Document()
    paragraph = doc.add_paragraph("`template literal`")
    result = audit_document(
        doc, 0.69, "atLeast", heading_level_from_style=lambda _: None,
        paragraph_direct_num_info=lambda _: (None, None), existing_heading_number=lambda _: False,
        looks_like_code_sample_table=lambda _: False, qn=lambda name: name, center_alignment=None,
        markdown_input=True, markdown_code_paragraphs={1},
    )
    assert result["markdown_residue"] == []

    unmarked = audit_document(
        doc, 0.69, "atLeast", heading_level_from_style=lambda _: None,
        paragraph_direct_num_info=lambda _: (None, None), existing_heading_number=lambda _: False,
        looks_like_code_sample_table=lambda _: False, qn=lambda name: name, center_alignment=None,
        markdown_input=True, markdown_code_texts={paragraph.text},
    )
    assert unmarked["markdown_residue"]


def test_markdown_render_save_readback_keeps_code_quote_and_nested_quote_list(tmp_path: Path) -> None:
    source = tmp_path / "semantic.md"
    source.write_text(
        "# 标题\n\n```\n  `code`  \n```\n\n> 引用\n\n> - 引用列项\n\n- 父列项\n  > 引用子块\n",
        encoding="utf-8",
    )
    report = new_report("test")
    source_model = parse_md_to_model(source, report, skill_version=lambda: "test")
    model = normalize_document_model(source_model, report)
    profile = load_template_profile(TEMPLATE)
    doc = Document(TEMPLATE)
    clear_document_body(doc)
    render_document_model(model, doc, report, 0.69, "atLeast", template_numbering_ids(profile), template_profile=profile)
    output = tmp_path / "semantic.docx"
    doc.save(output)
    readback = build_document_model_from_output(Document(output), source, report)
    report["audit"] = {"markdown_residue": []}
    preservation = audit_markdown_preservation(source_model, model, report, readback)

    assert preservation["passed"] is True
    blocks = readback["document"]["blocks"]
    assert any(block.get("role") == "code_block" and block["text"] == "  `code`  \n" for block in blocks)
    assert any(block.get("role") == "quote" and block["source"].get("container_quote_depth") == 1 for block in blocks)
    assert any(block["block_type"] == "list_item" and block["source"].get("container_quote_depth") == 1 for block in blocks)


def test_markdown_readback_uses_saved_ooxml_child_order(tmp_path: Path) -> None:
    source = tmp_path / "order.md"
    source.write_text("# 标题\n\n正文\n\n列 | 值\n--- | ---\n甲 | 乙\n\n- 列项\n\n> 引用\n", encoding="utf-8")
    report = new_report("test")
    source_model = parse_md_to_model(source, report, skill_version=lambda: "test")
    model = normalize_document_model(source_model, report)
    profile = load_template_profile(TEMPLATE)
    doc = Document(TEMPLATE)
    clear_document_body(doc)
    render_document_model(model, doc, report, 0.69, "atLeast", template_numbering_ids(profile), template_profile=profile)
    output = tmp_path / "order.docx"
    baseline = tmp_path / "order-baseline.docx"
    doc.save(baseline)

    def moved_result(left_type: str, right_type: str) -> dict:
        changed = Document(baseline)
        body = changed.element.body
        children = list(body.iterchildren())
        semantic = report["markdown_render_semantics"]["paragraphs"]
        ids = {item["block_type"]: item["para_id"] for item in semantic}
        left = next(child for child in children if child.get("{http://schemas.microsoft.com/office/word/2010/wordml}paraId") == ids[left_type])
        right = next(child for child in children if child.get("{http://schemas.microsoft.com/office/word/2010/wordml}paraId") == ids[right_type])
        left_index, right_index = children.index(left), children.index(right)
        body.remove(right)
        body.insert(left_index, right)
        changed.save(output)
        report["audit"] = {"markdown_residue": []}
        return audit_markdown_preservation(source_model, model, report, build_document_model_from_output(Document(output), source, report))

    assert moved_result("heading", "body")["passed"] is False
    assert moved_result("list_item", "body")["passed"] is False

    changed = Document(baseline)
    body = changed.element.body
    children = list(body.iterchildren())
    heading_id = next(item["para_id"] for item in report["markdown_render_semantics"]["paragraphs"] if item["block_type"] == "heading")
    heading = next(child for child in children if child.get("{http://schemas.microsoft.com/office/word/2010/wordml}paraId") == heading_id)
    table = next(child for child in children if child.tag.endswith("}tbl"))
    body.remove(table)
    body.insert(children.index(heading), table)
    changed.save(output)
    report["audit"] = {"markdown_residue": []}
    assert audit_markdown_preservation(source_model, model, report, build_document_model_from_output(Document(output), source, report))["passed"] is False


def test_markdown_image_and_caption_deletion_or_order_change_fails_audit(tmp_path: Path) -> None:
    source_model, report = _parse_for_conversion(tmp_path, "# 标题\n\n![图](x.png)\n\n图 1 架构\n")
    report["audit"] = {"markdown_residue": []}
    normalized = deepcopy(source_model)
    normalized["document"]["blocks"] = [
        block for block in normalized["document"]["blocks"] if block["block_type"] != "image"
    ]
    assert audit_markdown_preservation(source_model, normalized, report)["passed"] is False

    rendered = deepcopy(source_model)
    rendered["document"]["blocks"][1], rendered["document"]["blocks"][2] = rendered["document"]["blocks"][2], rendered["document"]["blocks"][1]
    assert audit_markdown_preservation(source_model, source_model, report, rendered)["passed"] is False


def _parse_for_conversion(tmp_path: Path, text: str) -> tuple[dict, dict]:
    source = tmp_path / "conversion.md"
    source.write_text(text, encoding="utf-8")
    report = new_report("test")
    return parse_md_to_model(source, report, skill_version=lambda: "test"), report


def test_markdown_image_relationship_hash_detects_swap_and_missing_relation(tmp_path: Path) -> None:
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg==")
    (tmp_path / "one.png").write_bytes(png)
    (tmp_path / "two.png").write_bytes(png + b"identity-two")
    source_model, report = _parse_for_conversion(tmp_path, "# 标题\n\n![一](one.png)\n\n![二](two.png)\n")
    model = normalize_document_model(source_model, report)
    profile = load_template_profile(TEMPLATE)
    doc = Document(TEMPLATE)
    clear_document_body(doc)
    render_document_model(model, doc, report, 0.69, "atLeast", template_numbering_ids(profile), template_profile=profile)
    output = tmp_path / "images.docx"
    doc.save(output)
    baseline = tmp_path / "image-type-baseline.docx"
    doc.save(baseline)

    def preserve(changed: Document) -> dict:
        changed.save(output)
        report["audit"] = {"markdown_residue": []}
        return audit_markdown_preservation(source_model, model, report, build_document_model_from_output(Document(output), tmp_path / "images.md", report))

    changed = Document(output)
    image_paragraphs = [
        paragraph for paragraph in changed.paragraphs
        if paragraph._p.get("{http://schemas.microsoft.com/office/word/2010/wordml}paraId") in {
            item["para_id"] for item in report["markdown_render_semantics"]["paragraphs"] if item["block_type"] == "image"
        }
    ]
    blips = [next(element for element in paragraph._p.iter() if element.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")) for paragraph in image_paragraphs]
    relation = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
    first, second = blips[0].get(relation), blips[1].get(relation)
    blips[0].set(relation, second)
    blips[1].set(relation, first)
    assert preserve(changed)["passed"] is False

    changed = Document(output)
    blip = next(element for element in changed.paragraphs[1]._p.iter() if element.get(relation))
    blip.set(relation, "rIdMissing")
    result = preserve(changed)
    assert result["passed"] is False
    assert result["rendered_image_relation_failures"]

    changed = Document(baseline)
    image_paragraph = next(paragraph for paragraph in changed.paragraphs if paragraph_has_image(paragraph))
    relation_id = next(element.get(relation) for element in image_paragraph._p.iter() if element.get(relation))
    changed.part.rels[relation_id]._reltype = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    result = preserve(changed)
    assert result["passed"] is False
    assert result["rendered_image_relation_failures"]


def test_markdown_appendix_source_and_empty_rendered_identity_are_stable(tmp_path: Path) -> None:
    source_model, report = _parse_for_conversion(tmp_path, "# 标题\n\n附录\n\n附录 A\n\n附录 B 示例标题\n")
    appendices = [block for block in source_model["document"]["blocks"] if block["block_type"] == "appendix"]
    assert [(block["appendix_id"], block["title"], block["title_lines"]) for block in appendices] == [
        ("A", "", [""]), ("A", "", [""]), ("B", "示例标题", ["", "示例标题"])
    ]
    normalized_appendices = [
        block for block in normalize_document_model(source_model, report)["document"]["blocks"]
        if block["block_type"] == "appendix"
    ]
    assert normalized_appendices[0]["appendix_id"] == "A"
    assert normalized_appendices[0]["title_lines"] == [""]

    model = new_document_model(tmp_path / "empty.md", "markdown", "test")
    append_block(model, appendix_block("a1", "", appendix_id="A", title_lines=[""], source={"semantic_origin": "markdown_token"}))
    profile = load_template_profile(TEMPLATE)
    doc = Document(TEMPLATE)
    clear_document_body(doc)
    render_document_model(model, doc, report, 0.69, "atLeast", template_numbering_ids(profile), template_profile=profile)
    output = tmp_path / "empty-appendix.docx"
    doc.save(output)
    readback = build_document_model_from_output(Document(output), tmp_path / "empty.md", report)
    assert any(block["block_type"] == "appendix" and block["appendix_id"] == "A" for block in readback["document"]["blocks"])


def test_markdown_percent_decoded_chinese_image_filename_round_trips(tmp_path: Path) -> None:
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg==")
    image_path = tmp_path / "图片 空格.png"
    image_path.write_bytes(png)
    source_model, report = _parse_for_conversion(tmp_path, "# 标题\n\n![图](%E5%9B%BE%E7%89%87%20%E7%A9%BA%E6%A0%BC.png?cache=1#view)\n")
    image = next(block for block in source_model["document"]["blocks"] if block["block_type"] == "image")
    assert image["source"]["asset_path"] == str(image_path)
    model = normalize_document_model(source_model, report)
    profile = load_template_profile(TEMPLATE)
    doc = Document(TEMPLATE)
    clear_document_body(doc)
    render_document_model(model, doc, report, 0.69, "atLeast", template_numbering_ids(profile), template_profile=profile)
    output = tmp_path / "chinese-image.docx"
    doc.save(output)
    report["audit"] = {"markdown_residue": []}
    assert audit_markdown_preservation(source_model, model, report, build_document_model_from_output(Document(output), tmp_path / "images.md", report))["passed"] is True


def paragraph_has_image(paragraph) -> bool:
    return any(element.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed") for element in paragraph._p.iter())
