"""Special-purpose contracts of the modules the native chain kept.

The legacy pipeline used to be the only place where these behaviours were
exercised end to end.  It is gone, so the same contracts are pinned here through
the native chain: SourceFacts for the table and list readers, the region
resolver for the source table of contents and the cover, and the appendix
semantics module for appendix titles.
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import json

from appendix_semantics import (
    appendix_heading_level,
    appendix_index_to_id,
    appendix_role_from_style,
    is_explicit_appendix_title,
    parse_appendix_title,
)
from text_utils import (
    caption_parts,
    clean_note_prefix,
    is_formula_number,
    looks_like_list_item,
    strip_heading_marker_prefix,
)
from unordered_lists import (
    leading_unordered_marker_candidate,
    paragraph_layout_evidence,
    strip_source_unordered_marker,
)
from wxdf.kernel import ConversionKernel
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.regions import resolve_source_regions


def _table_records(path: Path) -> list[dict]:
    """Return the classified table records one source produced."""

    facts = parse(path)
    return [
        container["table_semantics"]
        for container in facts["containers"].values()
        if container.get("kind") == "table"
    ]


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


def _save(document: Document, tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.docx"
    document.save(str(path))
    return path


# ---------------------------------------------------------------------------
# table_semantics: the classification the native parser publishes
# ---------------------------------------------------------------------------

def test_native_parse_classifies_single_cell_natural_language_as_callout(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "边界说明：该内容用于突出一段完整说明。"
    path = _save(document, tmp_path, "callout")

    (record,) = _table_records(path)

    assert record["table_type"] == "callout"
    assert record["header_rows"] == 0
    assert record["visual_cell_count"] == 1
    assert record["caption_eligible"] is False
    assert "natural_language_content" in record["evidence"]


def test_native_parse_classifies_single_cell_payload_as_code_sample(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = 'POST /api/auth\nContent-Type: application/json\n{"user":"demo"}'
    path = _save(document, tmp_path, "code-sample")

    (record,) = _table_records(path)

    assert record["table_type"] == "code_sample"
    assert record["header_rows"] == 0
    assert record["caption_eligible"] is False


def test_native_parse_classifies_a_catalog_as_a_captioned_data_table(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=3, cols=3)
    values = [
        ("序号", "接口名称", "接口能力"),
        ("1", "SDK_Init", "SDK初始化"),
        ("2", "Capture", "采集指纹"),
    ]
    for row, row_values in zip(table.rows, values):
        for cell, value in zip(row.cells, row_values):
            cell.text = value
    path = _save(document, tmp_path, "catalog")

    (record,) = _table_records(path)

    assert record["table_type"] == "data"
    assert record["caption_eligible"] is True
    assert record["header_rows"] >= 1


def test_native_parse_classifies_a_nested_cell_table_as_layout(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).add_table(rows=1, cols=1)
    path = _save(document, tmp_path, "nested")

    records = _table_records(path)

    # The outer cell and the nested table it holds are both layout tables.
    assert records
    assert {record["table_type"] for record in records} == {"layout"}
    assert all(record["caption_eligible"] is False for record in records)


def test_native_parse_classifies_a_borderless_math_number_table_as_layout(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).paragraphs[0]._p.append(OxmlElement("m:oMath"))
    table.cell(0, 1).text = "(1)"
    borders = OxmlElement("w:tblBorders")
    for name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{name}")
        edge.set(qn("w:val"), "none")
        borders.append(edge)
    table._tbl.tblPr.append(borders)
    path = _save(document, tmp_path, "formula-number")

    (record,) = _table_records(path)

    assert record["table_type"] == "layout"
    assert record["caption_eligible"] is False
    assert "formula_number_layout" in record["evidence"]


def test_native_parse_keeps_a_numbered_pair_without_math_as_data(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "指标"
    table.cell(0, 1).text = "(1)"
    path = _save(document, tmp_path, "numbered-pair")

    (record,) = _table_records(path)

    assert record["table_type"] == "data"


def test_native_parse_keeps_a_merged_visual_cell_as_one_cell(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "合并后的说明框"
    path = _save(document, tmp_path, "merged")

    (record,) = _table_records(path)

    assert record["visual_cell_count"] == 1
    assert record["table_type"] == "callout"


# ---------------------------------------------------------------------------
# unordered_lists
# ---------------------------------------------------------------------------

def test_leading_unordered_marker_candidate_covers_unknown_symbols() -> None:
    for marker in ("•", "▪", "◦", "‣", "-"):
        candidate = leading_unordered_marker_candidate(f"{marker} 条目内容")
        assert candidate is not None, marker
        assert candidate["marker"] == marker
        assert candidate["content_text"] == "条目内容"

    # A marker the parser does not know is still reported as a candidate, which
    # is what the LLM review capability consumes.
    unknown = leading_unordered_marker_candidate("‣ 陌生符号条目")
    assert unknown is not None and unknown["known_marker"] is False

    assert leading_unordered_marker_candidate("普通正文段落。") is None
    assert leading_unordered_marker_candidate("*") is None


def test_paragraph_layout_evidence_reports_indentation() -> None:
    document = Document()
    paragraph = document.add_paragraph("缩进条目")
    paragraph.paragraph_format.left_indent = 480

    evidence = paragraph_layout_evidence(paragraph)

    assert evidence is not None
    assert evidence.get("left_twips")


def test_strip_source_unordered_marker_removes_the_visible_marker() -> None:
    block = {
        "text": "• 条目内容",
        "source": {"unordered_candidate": {"marker": "•"}},
    }

    assert strip_source_unordered_marker(block) == "条目内容"
    # Without recorded candidate evidence the text is left untouched.
    assert strip_source_unordered_marker({"text": "• 条目内容"}) == "• 条目内容"


# ---------------------------------------------------------------------------
# appendix_semantics
# ---------------------------------------------------------------------------

def test_appendix_title_parser_preserves_classification_and_title_lines() -> None:
    parsed = parse_appendix_title("附录A\n（资料性）接口字段说明", 1)

    assert parsed["appendix_id"] == "A"
    assert parsed["classification"] == "资料性"
    assert parsed["title"] == "接口字段说明"
    # The classification line and the title each take their own line.
    assert parsed["title_lines"] == ["", "（资料性）", "接口字段说明"]
    assert parsed["soft_break_count"] == 2


def test_appendix_marker_and_style_roles() -> None:
    # The marker needs its letter to be explicit; a bare 附录 is a weaker signal
    # the region module handles separately.
    assert is_explicit_appendix_title("附录A") is True
    assert is_explicit_appendix_title("附 录 B：字段说明") is True
    assert is_explicit_appendix_title("附录") is False

    assert appendix_role_from_style("附录标题") == "appendix_title"
    assert appendix_role_from_style("附录二级标题") == "appendix_heading_2"
    assert appendix_role_from_style("Heading 1") is None

    assert appendix_heading_level("附录三级标题") == 3
    assert appendix_heading_level("附录标题") is None
    assert appendix_heading_level("正文") is None

    assert appendix_index_to_id(1) == "A"
    assert appendix_index_to_id(2) == "B"
    assert appendix_index_to_id(27) == "AA"


# ---------------------------------------------------------------------------
# toc_detector and front_matter: the native region resolver
# ---------------------------------------------------------------------------

def _manual_toc_document() -> Document:
    from docx.enum.text import WD_BREAK

    document = Document()
    document.add_paragraph("系统建设方案")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("目  录")
    document.add_paragraph("1 项目概述")
    document.add_paragraph("2 总体设计")
    document.add_paragraph("3 实施计划")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("1 项目概述", style="Heading 1")
    document.add_paragraph("正文内容。")
    document.add_paragraph("2 总体设计", style="Heading 1")
    document.add_paragraph("正文内容。")
    document.add_paragraph("3 实施计划", style="Heading 1")
    return document


def _positions_of(facts, text: str) -> set[int]:
    positions = set()
    for node_id, unit in facts["units"].items():
        if unit.get("kind") != "paragraph":
            continue
        inline = unit.get("inline_content") or []
        visible = "".join(
            item.get("text", "") if item.get("kind") == "text" else (item.get("cached_result") or "")
            for item in inline
        )
        if visible.strip() == text:
            position = facts["evidence"].get(node_id, {}).get("source_position")
            if isinstance(position, int):
                positions.add(position)
    return positions


def test_native_regions_exclude_a_cover_and_the_source_table_of_contents(tmp_path: Path) -> None:
    path = _save(_manual_toc_document(), tmp_path, "manual-toc")
    facts = parse(path)

    resolution = resolve_source_regions(path, facts)

    assert resolution["report"]["source_toc"]["status"] == "detected"
    assert resolution["report"]["source_toc"]["method"] == "rules"
    excluded = set(resolution["excluded_source_positions"])
    assert excluded, "the cover and the source TOC must be excluded"

    # The source's own TOC entries are excluded, the body headings are kept.
    toc_positions = _positions_of(facts, "2 总体设计")
    assert len(toc_positions) >= 2, "the TOC entry and the body heading share their text"
    assert min(toc_positions) in excluded, "the TOC entry must be excluded"
    assert max(toc_positions) not in excluded, "the body heading must stay"


def test_native_regions_keep_a_body_directory_heading(tmp_path: Path) -> None:
    from docx.enum.text import WD_BREAK

    document = Document()
    document.add_paragraph("系统建设方案")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("项目概述", style="Heading 1")
    document.add_paragraph("目录")
    document.add_paragraph("1 文件管理")
    document.add_paragraph("正文内容。")
    path = _save(document, tmp_path, "body-directory")

    facts = parse(path)
    resolution = resolve_source_regions(path, facts)

    body_positions = _positions_of(facts, "目录")
    assert body_positions
    assert not (body_positions & set(resolution["excluded_source_positions"]))


def test_native_regions_extract_the_cover_title(tmp_path: Path) -> None:
    path = _save(_manual_toc_document(), tmp_path, "cover-title")
    facts = parse(path)

    resolution = resolve_source_regions(path, facts)

    title = (resolution["front_matter_context"] or {}).get("title") or {}
    assert title.get("text") == "系统建设方案"


def _add_field_paragraph(document: Document, instruction: str, cached: str) -> None:
    """Append one paragraph holding a complex field with its cached result."""

    paragraph = document.add_paragraph()
    for kind, value in (
        ("begin", None),
        ("instrText", instruction),
        ("separate", None),
        ("text", cached),
        ("end", None),
    ):
        run = OxmlElement("w:r")
        if kind in {"begin", "separate", "end"}:
            element = OxmlElement("w:fldChar")
            element.set(qn("w:fldCharType"), kind)
        elif kind == "instrText":
            element = OxmlElement("w:instrText")
            element.set(qn("xml:space"), "preserve")
            element.text = value
        else:
            element = OxmlElement("w:t")
            element.text = value
        run.append(element)
        paragraph._p.append(run)


def _end_directory(document: Document) -> None:
    """Close an entry cluster the way a Word TOC region ends: a page break."""

    from docx.enum.text import WD_BREAK

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _field_toc_document(*, second_directory: bool = False) -> Document:
    """Build a source whose directory is a real TOC field, not plain entries."""

    document = Document()
    document.core_properties.title = "系统操作手册"
    document.add_paragraph("目  次")
    _add_field_paragraph(document, ' TOC \\o "1-3" \\h \\u ', "1 项目概述2 总体设计")
    for label in ("1 项目概述", "2 总体设计"):
        _add_field_paragraph(document, f" HYPERLINK \\l _Toc{label}  PAGEREF _Toc{label} \\h ", f"{label}1")
    _end_directory(document)
    if second_directory:
        document.add_paragraph("图  目  录")
        _add_field_paragraph(document, ' TOC \\h \\z \\c "图" ', "图1 系统架构4")
        _add_field_paragraph(document, " HYPERLINK \\l _TocGraph  PAGEREF _TocGraph \\h ", "图1 系统架构4")
        _end_directory(document)
    document.add_paragraph("1 项目概述", style="Heading 1")
    document.add_paragraph("正文内容。")
    document.add_paragraph("2 总体设计", style="Heading 1")
    document.add_paragraph("正文内容。")
    return document


def test_native_regions_merge_a_directory_title_and_its_toc_field_anchor(tmp_path: Path) -> None:
    path = _save(_field_toc_document(), tmp_path, "field-toc")
    facts = parse(path)

    resolution = resolve_source_regions(path, facts)
    source_toc = resolution["report"]["source_toc"]

    # Both anchors stay in the report, the folded one names what absorbed it.
    assert len(source_toc["candidates"]) == 2
    assert source_toc["status"] == "detected"
    selected = next(item for item in source_toc["candidates"] if item["selected"])
    folded = next(item for item in source_toc["candidates"] if item["candidate_id"] != selected["candidate_id"])
    assert selected["start_source_position"] == 0, "the anchor carrying the title must win"
    assert selected["candidate_id"] == source_toc["selected_candidate_id"]
    assert folded["selected"] is False
    assert folded["merged_into"] == selected["candidate_id"]
    assert {"toc_title", "toc_field"} <= set(selected["evidence"])

    # The title paragraph and every entry paragraph leave the body.
    excluded = set(resolution["excluded_source_positions"])
    assert excluded == set(range(0, int(selected["end_source_position"]) + 1))

    front_matter = resolution["front_matter_context"] or {}
    assert front_matter["status"] == "no_cover"
    assert front_matter["title"]["source"] == "core_properties"
    assert front_matter["title"]["text"] == "系统操作手册"
    assert front_matter["body_start_source_position"] == int(selected["end_source_position"]) + 1


def test_native_regions_do_not_merge_two_directories_on_separate_pages(tmp_path: Path) -> None:
    path = _save(_field_toc_document(second_directory=True), tmp_path, "two-directories")
    facts = parse(path)

    candidates = resolve_source_regions(path, facts)["report"]["source_toc"]["candidates"]

    # The main directory folds into one anchor pair; the figure list keeps its
    # own candidate because it ends at a different block.
    ends = sorted(item["end_source_position"] for item in candidates)
    assert len(set(ends)) == 2, ends
    figure_directory = next(item for item in candidates if item["end_source_position"] == max(ends))
    assert "merged_into" not in figure_directory
    main_directory = [
        item for item in candidates if item["end_source_position"] == min(ends)
    ]
    assert len(main_directory) == 2
    assert [item["start_source_position"] for item in main_directory] == [0, 1]


# ---------------------------------------------------------------------------
# appendix_semantics through a real conversion
# ---------------------------------------------------------------------------

def test_native_conversion_records_an_appendix_range_and_titles(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("演示文档")
    document.add_paragraph("附录A")
    document.add_paragraph("（资料性）字段说明")
    document.add_paragraph("A.1 字段列表", style="Heading 1")
    document.add_paragraph("字段内容。")
    source = _save(document, tmp_path, "appendix")

    output = tmp_path / "appendix.out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off", "normalized_ast_path": tmp_path / "appendix.ast.json"},
    ))
    report = result.as_report()

    assert report["appendix_preservation_audit"]["passed"] is True, (
        report["appendix_preservation_audit"]["issues"]
    )
    ast = json.loads((tmp_path / "appendix.ast.json").read_text(encoding="utf-8"))
    containers = [
        container
        for container in ast["source_ast"]["document"]["containers"].values()
        if container.get("kind") == "appendix"
    ]
    assert len(containers) == 1
    assert containers[0]["appendix_id"] == "A"
    assert containers[0]["page_break_before"] is True


def test_caption_parts_reads_a_label_and_a_title() -> None:
    assert caption_parts("表1 专业词汇") == ("table", "表", None, "专业词汇")
    assert caption_parts("图  安全AI中台逻辑架构图") == ("figure", "图", None, "安全AI中台逻辑架构图")
    assert caption_parts("普通正文")[0] == "unknown"


def test_formula_number_and_list_item_recognition() -> None:
    assert is_formula_number("（1）") is True
    assert is_formula_number("正文内容") is False

    assert looks_like_list_item("1. 条目") is True
    assert looks_like_list_item("a) 条目") is True
    assert looks_like_list_item("普通正文段落") is False


def test_heading_marker_prefix_is_stripped_from_the_kept_marker_vocabulary() -> None:
    assert strip_heading_marker_prefix("第一章 概述") == "概述"
    assert strip_heading_marker_prefix("1.1 设计说明") == "设计说明"
    assert strip_heading_marker_prefix("概述") == "概述"


def test_note_prefix_cleanup() -> None:
    assert clean_note_prefix("备注：说明内容") == "说明内容"
    assert clean_note_prefix("**备注：**说明内容") == "说明内容"
    assert clean_note_prefix("说明内容") == "说明内容"
