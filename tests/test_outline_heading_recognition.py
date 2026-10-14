"""Headings the source declares through ``w:outlineLvl`` survive conversion.

A source may mark its section titles with a direct outline level on a plain-styled
paragraph instead of a heading style.  The reader records that level as evidence,
the structure module grades the heading on it, the list module never promotes such
a heading to a list item, and the audit reports the loss if the delivered model
ends up with no heading at all.  These tests pin each link.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from text_utils import strip_heading_marker_prefix
from wxdf.auditing import audit_declared_outline_presence
from wxdf.kernel import ConversionKernel
from wxdf.model import inline_text_projection
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.captions import collect_caption_candidates
from wxdf.semantics.regions import classify_regions
from wxdf.semantics.source_ast import build_source_ast
from wxdf.semantics.structure import collect_structure_candidates


TEMPLATE = ROOT / "assets" / "wx_template.docx"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# A 1x1 transparent PNG, enough for python-docx to embed a picture part.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _set_outline(paragraph, level: int | None) -> None:
    if level is None:
        return
    properties = paragraph._p.get_or_add_pPr()
    element = OxmlElement("w:outlineLvl")
    element.set(qn("w:val"), str(level))
    properties.append(element)


def _outline_docx(tmp_path: Path, spec: list[tuple[str, int | None]]) -> Path:
    document = Document()
    for text, level in spec:
        paragraph = document.add_paragraph(text)
        _set_outline(paragraph, level)
    source = tmp_path / "outline.docx"
    document.save(str(source))
    return source


def _roles_by_text(facts: dict, candidates: list[dict]) -> dict[str, dict]:
    text_by_node = {
        str(node_id): inline_text_projection(list(unit.get("inline_content") or []))
        for node_id, unit in facts["units"].items()
    }
    return {
        text_by_node.get(str(candidate["target_node_ids"][0]), ""): candidate["proposal"]["value"]
        for candidate in candidates
        if candidate["proposal"]["operation"] == "update_role"
    }


def test_direct_outline_level_grades_a_heading(tmp_path: Path) -> None:
    source = _outline_docx(
        tmp_path,
        [("一、前言", 0), ("1.1 概述", 1), ("正文内容", None)],
    )
    facts = parse(source)

    roles = _roles_by_text(facts, collect_structure_candidates(facts, classify_regions(facts)))

    assert roles["一、前言"] == {"role": "heading", "level": 1}
    assert roles["1.1 概述"] == {"role": "heading", "level": 2}
    assert roles["正文内容"] == {"role": "body"}


def test_outline_level_nine_is_body_text_not_a_heading(tmp_path: Path) -> None:
    source = _outline_docx(tmp_path, [("普通段落", 9)])

    facts = parse(source)

    assert "outline_level" not in facts["evidence"][facts["children"]["root"][0]]


def test_outline_heading_text_loses_its_manual_marker(tmp_path: Path) -> None:
    source = _outline_docx(tmp_path, [("一、前言", 0), ("5.3.1 详细设计", 2)])
    facts = parse(source)

    written = {
        str(candidate["target_node_ids"][0]): inline_text_projection(
            candidate["proposal"]["value"]["inline_content"]
        )
        for candidate in collect_structure_candidates(facts, classify_regions(facts))
        if candidate["proposal"]["operation"] == "update_node_content"
    }
    text_by_node = {
        str(node_id): inline_text_projection(list(unit.get("inline_content") or []))
        for node_id, unit in facts["units"].items()
    }

    assert {text_by_node[node_id]: text for node_id, text in written.items()} == {
        "一、前言": "前言",
        "5.3.1 详细设计": "详细设计",
    }


def test_marker_vocabulary_covers_paren_and_letter_marks() -> None:
    assert strip_heading_marker_prefix("1）刷新") == "刷新"
    assert strip_heading_marker_prefix("（1）新增服务") == "新增服务"
    assert strip_heading_marker_prefix("A. 修改") == "修改"
    # An appendix number and a Latin abbreviation are not manual markers.
    assert strip_heading_marker_prefix("A.1 字段列表") == "A.1 字段列表"
    assert strip_heading_marker_prefix("U.S. 报告") == "U.S. 报告"


def test_outline_heading_is_never_promoted_to_a_list_item(tmp_path: Path) -> None:
    # A plain-styled "5.3.1 …" carries an outline level and a dotted number.  The
    # dotted number alone reads as a list marker, but the declared heading wins
    # and the paragraph renders as a heading rather than a numbered list item.
    source = _outline_docx(tmp_path, [("5.3.1 配置中状态的测试项目详情", 2), ("正文。", None)])
    output = tmp_path / "out.docx"

    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))

    assert result.status in {"completed", "completed_with_warnings"}, result.diagnostics
    rows = [(paragraph.style.name, paragraph.text) for paragraph in Document(str(output)).paragraphs]
    heading_row = next(row for row in rows if row[1] == "配置中状态的测试项目详情")
    assert heading_row[0].startswith("Heading"), rows
    assert not any("配置中状态的测试项目详情" in text for _style, text in rows if "列项" in _style)


def test_source_toc_control_excludes_its_tables(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("标题")
    control = parse_xml(
        f'<w:sdt xmlns:w="{W[1:-1]}"><w:sdtContent>'
        "<w:tbl><w:tblPr/><w:tr><w:tc><w:p><w:r><w:t>版本/状态</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>作者</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-3" </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>目录内容</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        "</w:sdtContent></w:sdt>"
    )
    document.element.body.insert(0, control)
    document.add_paragraph("正文段落")
    source = tmp_path / "cover-toc.docx"
    document.save(str(source))

    facts = parse(source)
    regions = classify_regions(facts)
    excluded_container_ids = {
        node_id
        for region in regions["regions"]
        if region.get("semantic_scope") == "excluded" and region.get("kind") == "toc"
        for node_id in region.get("node_ids", [])
    }

    table_ids = [
        node_id for node_id, container in facts["containers"].items() if container.get("kind") == "table"
    ]
    assert len(table_ids) == 1
    assert facts["evidence"][table_ids[0]].get("source_toc") is True
    assert table_ids[0] in excluded_container_ids


def test_inline_icon_image_takes_no_caption_while_a_figure_does(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(_PNG)
    document = Document()
    document.add_paragraph("点击").add_run().add_picture(str(image), width=114300)
    document.add_paragraph().add_run().add_picture(str(image), width=2286000)
    source = tmp_path / "images.docx"
    document.save(str(source))

    facts = parse(source)
    ast = build_source_ast(facts)
    image_ids = [
        node_id
        for node_id, block in ast["document"]["blocks"].items()
        if block.get("block_type") == "image"
    ]

    targets = {
        candidate["target_node_ids"][0]
        for candidate in collect_caption_candidates(ast, facts)
        if candidate["proposal"]["operation"] == "create_overlay"
        and candidate["proposal"]["value"].get("caption_type") == "figure"
    }

    assert len(image_ids) == 2
    assert len(targets) == 1


def test_outline_presence_audit_reports_a_lost_outline(tmp_path: Path) -> None:
    source = _outline_docx(tmp_path, [("一、前言", 0), ("二、平台简介", 0)])
    facts = parse(source)
    normalized = {
        "source_ast": {
            "document": {
                "blocks": {
                    facts["children"]["root"][0]: {"block_type": "paragraph", "role": "body"},
                    facts["children"]["root"][1]: {"block_type": "paragraph", "role": "body"},
                }
            }
        }
    }

    result = audit_declared_outline_presence(facts, normalized)

    assert result["passed"] is False
    assert result["issues"][0]["code"] == "source_outline_not_materialized"


def test_outline_presence_audit_passes_once_headings_exist(tmp_path: Path) -> None:
    source = _outline_docx(tmp_path, [("一、前言", 0), ("正文。", None)])
    facts = parse(source)
    normalized = {
        "source_ast": {
            "document": {
                "blocks": {
                    facts["children"]["root"][0]: {"block_type": "paragraph", "role": "heading", "level": 1},
                    facts["children"]["root"][1]: {"block_type": "paragraph", "role": "body"},
                }
            }
        }
    }

    result = audit_declared_outline_presence(facts, normalized)

    assert result["passed"] is True


def test_outline_presence_audit_accepts_a_materialized_title(tmp_path: Path) -> None:
    # A paragraph that carries an outline level may be graded the document title
    # rather than a heading; both are materialized outline structure.
    source = _outline_docx(tmp_path, [("文档标题", 0), ("正文。", None)])
    facts = parse(source)
    normalized = {
        "source_ast": {
            "document": {
                "blocks": {
                    facts["children"]["root"][0]: {"block_type": "paragraph", "role": "title"},
                    facts["children"]["root"][1]: {"block_type": "paragraph", "role": "body"},
                }
            }
        }
    }

    assert audit_declared_outline_presence(facts, normalized)["passed"] is True


def test_direct_body_text_outline_level_overrides_the_style(tmp_path: Path) -> None:
    # Word numbers "body text" as outline level 9.  A direct value is decisive, so
    # the paragraph is not re-read from a style that carries a real outline level.
    document = Document()
    paragraph = document.add_paragraph("小标题", style="Subtitle")
    _set_outline(paragraph, 9)
    source = tmp_path / "subtitle.docx"
    document.save(str(source))

    facts = parse(source)

    assert "outline_level" not in facts["evidence"][facts["children"]["root"][0]]


def test_outline_headings_render_with_heading_styles_end_to_end(tmp_path: Path) -> None:
    source = _outline_docx(tmp_path, [("一、前言", 0), ("1.1 概述", 1), ("正文内容。", None)])
    output = tmp_path / "out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))

    assert result.status in {"completed", "completed_with_warnings"}, result.diagnostics
    styled = [
        (paragraph.style.name, paragraph.text)
        for paragraph in Document(str(output)).paragraphs
        if paragraph.style.name in {"Heading 1", "Heading 2"}
    ]

    assert styled == [("Heading 1", "前言"), ("Heading 2", "概述")]
