from pathlib import Path
import base64
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from docx import Document

from format_document import (
    convert_docx,
    ensure_auto_numbering,
    ensure_fallback_styles,
    existing_heading_number,
    heading_level_from_text,
    new_report,
    paragraph_has_graphics,
    resolved_heading_level,
    scan_non_text_objects,
    strip_heading_marker,
)


def test_chinese_number_heading_is_level_one():
    assert heading_level_from_text("一、核心定位") == 1
    assert heading_level_from_text("二、总体设计思路") == 1


def test_numbered_decimal_heading_levels_still_work():
    assert heading_level_from_text("1 总则") == 1
    assert heading_level_from_text("1.1 总体架构") == 2
    assert heading_level_from_text("11.1.1 全局视角") == 3


def test_chinese_number_marker_is_stripped():
    assert strip_heading_marker("一、核心定位") == "核心定位"
    assert strip_heading_marker("十五、最后收尾备忘") == "最后收尾备忘"
    assert existing_heading_number("一、核心定位")


def test_heading_style_level_survives_without_num_id():
    assert resolved_heading_level("Heading 2", 1, "分层架构设计") == 2
    assert resolved_heading_level("Heading 2", None, "统一认证中心") == 2


def test_docx_image_paragraphs_are_preserved(tmp_path):
    image_path = tmp_path / "tiny.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
    )
    src_path = tmp_path / "source.docx"
    output_path = tmp_path / "output.docx"

    src_doc = Document()
    src_doc.add_paragraph("1 总体定位", style="Heading 1")
    src_doc.add_picture(str(image_path))
    src_doc.save(src_path)

    out_doc = Document()
    ensure_fallback_styles(out_doc)
    numbering_ids = ensure_auto_numbering(out_doc)
    report = new_report()
    report["non_text_objects"] = scan_non_text_objects(src_path)
    convert_docx(src_path, out_doc, 0.69, "at-least", True, report, numbering_ids)
    out_doc.save(output_path)

    assert report["graphic_paragraphs_preserved"] == 1
    assert report["media_relationships_preserved"] == 1
    assert len(Document(output_path).inline_shapes) == 1
    assert paragraph_has_graphics(Document(src_path).paragraphs[1])
    with zipfile.ZipFile(output_path) as zf:
        assert any(name.startswith("word/media/") for name in zf.namelist())
