from pathlib import Path
import base64
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from docx import Document
from docx.oxml.ns import qn

from format_document import (
    audit_document,
    convert_docx,
    ensure_auto_numbering,
    ensure_fallback_styles,
    existing_heading_number,
    heading_level_from_text,
    new_report,
    new_num_for_abstract,
    paragraph_has_graphics,
    resolved_heading_level,
    scan_non_text_objects,
    strip_heading_marker,
)
from update_installed_skill import read_version


def test_report_includes_skill_version():
    assert new_report()["skill_version"] == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


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


def test_new_list_numbering_instances_restart_at_one():
    doc = Document()
    numbering_ids = ensure_auto_numbering(doc)
    num_id = new_num_for_abstract(doc, numbering_ids["list_letter_abstract"])
    numbering = doc.part.numbering_part.element
    num = next(element for element in numbering.findall(qn("w:num")) if element.get(qn("w:numId")) == str(num_id))
    start = num.find(".//" + qn("w:startOverride"))
    assert start is not None
    assert start.get(qn("w:val")) == "1"


def test_audit_tracks_heading_sequence_and_list_restart_groups():
    doc = Document()
    ensure_fallback_styles(doc)
    numbering_ids = ensure_auto_numbering(doc)
    heading = doc.add_paragraph("测试章节", style="Heading 1")
    from format_document import apply_numbering

    apply_numbering(heading, numbering_ids["heading"], 0)
    num_id = new_num_for_abstract(doc, numbering_ids["list_letter_abstract"])
    item = doc.add_paragraph("测试列项", style="1.1一级列项-编号")
    apply_numbering(item, num_id, 0)

    audit = audit_document(doc, 0.69, "at-least")
    assert audit["heading_sequence"][0]["text"] == "测试章节"
    assert audit["list_restart_groups"][0]["restart_at_one"] is True
    assert audit["ordered_list_nums_without_restart"] == []


def test_updater_reads_version(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    assert read_version(skill_dir) == "9.9.9"
    assert read_version(tmp_path / "missing") == "unknown"


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


def test_media_scan_ignores_zip_directory_entries(tmp_path):
    docx_path = tmp_path / "source.docx"
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("word/media/", "")
        zf.writestr("word/media/image1.png", b"png")
        zf.writestr("word/document.xml", "<w:document><w:drawing/></w:document>")

    counts = scan_non_text_objects(docx_path)

    assert counts["media_files"] == 1
    assert counts["drawings"] == 1
