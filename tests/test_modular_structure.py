from __future__ import annotations

import copy
import sys
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.parsers import parse
from wxdf.model import inline_text_projection
from wxdf.semantics.equivalence import compare_structure_equivalence
from wxdf.semantics.parser_equivalence import compare_parser_equivalence
from wxdf.semantics.protected import collect_protected_candidates
from wxdf.semantics.region_bridge import apply_region_decisions
from wxdf.semantics.regions import classify_regions, collect_scope_candidates, resolve_source_regions
from wxdf.semantics.source_ast import build_source_ast
from wxdf.semantics.structure import collect_structure_candidates
from wxdf.semantics.structure_bridge import apply_structure_decisions
from wxdf.validation import validate_source_facts


def _facts(tmp_path: Path) -> dict:
    source = tmp_path / "source.md"
    source.write_text(
        "封面信息\n\n目录\n\n# 正文标题\n\n注：保留说明\n\n```python\nprint('1.')\n```\n\nAPI: /v1/items\n\n> 引用内容\n",
        encoding="utf-8",
    )
    return parse(source)


def test_regions_preserve_source_scope_and_exclude_explicit_front_matter_and_toc(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    baseline = copy.deepcopy(facts)
    positions = [item.get("line_start") for item in facts["evidence"].values() if item.get("line_start")]
    regions = classify_regions(facts, front_matter_positions={1}, toc_positions={3})

    assert regions["schema_version"] == "1.0"
    assert {item["kind"] for item in regions["regions"]} >= {"front_matter", "toc", "body"}
    assert any(item["semantic_scope"] == "excluded" for item in regions["regions"] if item["kind"] in {"front_matter", "toc"})
    assert positions
    assert facts == baseline

    candidates = collect_scope_candidates(facts, regions)
    assert candidates
    assert all(
        candidate["proposal"]["value"]["semantic_scope"] == "excluded"
        for candidate in candidates
    )


def test_docx_region_resolver_uses_real_cover_and_toc_positions(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("统一身份认证系统集成方案", style="Title")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("目  录")
    document.add_paragraph("1 项目概述")
    document.add_paragraph("2 总体设计")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("1 项目概述", style="Heading 1")
    document.add_paragraph("2 总体设计", style="Heading 1")
    document.save(source)
    facts = parse(source)

    resolution = resolve_source_regions(source, facts)

    assert resolution["front_matter_context"]["title"]["text"] == "统一身份认证系统集成方案"
    assert resolution["toc_context"]["source_context"]["toc_status"] == "detected"
    assert resolution["excluded_source_positions"] == [0, 1, 2, 3, 4, 5]
    assert resolution["report"]["source_toc"]["status"] == "detected"
    assert resolution["report"]["source_front_matter"]["status"] == "cover_detected"
    title_node_id = next(
        node_id
        for node_id in facts["children"]["root"]
        if facts["evidence"].get(node_id, {}).get("source_position") == 0
    )
    assert resolution["regions"]["node_scope"][title_node_id] == "canonical_title"
    assert resolution["evidence"]["excluded_node_count"] == 5


def test_docx_region_resolver_keeps_body_paragraphs_with_toc_named_styles(tmp_path: Path) -> None:
    source = tmp_path / "body-toc-style.docx"
    document = Document()
    document.styles.add_style("toc 9", WD_STYLE_TYPE.PARAGRAPH)
    document.add_paragraph("项目概述", style="Heading 1")
    document.add_paragraph("正文内容需要完整保留。")
    document.add_paragraph("总体流程", style="Heading 6")
    document.add_paragraph("总体业务流", style="toc 9")
    document.add_paragraph("图 1 总体业务流")
    boundary = document.add_paragraph("总体数据流", style="toc 9")
    boundary.add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("图 2 总体数据流")
    document.save(source)

    resolution = resolve_source_regions(source, parse(source))

    assert resolution["toc_context"]["source_context"]["toc_status"] == "no_candidate"
    candidates = resolution["toc_context"]["document"]["blocks"]
    assert len(candidates) == 1
    assert candidates[0]["selection_eligible"] is False
    assert candidates[0]["negative_evidence"] == ["missing_toc_identity"]
    assert resolution["front_matter_context"]["status"] == "no_cover"
    assert resolution["excluded_source_positions"] == []


def test_markdown_region_bridge_removes_excluded_source_toc_before_normalization(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("目录\n\n# 文档标题\n", encoding="utf-8")
    facts = parse(source)
    resolution = resolve_source_regions(source, facts)
    legacy_model = {
        "document": {
            "blocks": [
                {"id": "toc", "block_type": "body", "text": "目录", "source": {"line_start": 1}},
                {"id": "title", "block_type": "heading", "role": "title", "level": 0, "text": "文档标题", "source": {"line_start": 3}},
            ]
        }
    }

    applied = apply_region_decisions(legacy_model, facts, resolution["regions"])

    assert [block["id"] for block in applied.model["document"]["blocks"]] == ["title"]
    assert applied.evidence == {
        "excluded_candidate_count": 1,
        "removed_count": 1,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }


def test_markdown_heading_named_toc_remains_available_as_document_structure(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 目录\n\n正文内容\n", encoding="utf-8")
    facts = parse(source)

    resolution = resolve_source_regions(source, facts)
    candidates = collect_structure_candidates(facts, resolution["regions"])

    assert resolution["excluded_source_positions"] == []
    assert candidates[0]["proposal"]["value"] == {"role": "title"}


def test_markdown_toc_label_inside_quote_does_not_open_source_toc_region(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 文档标题\n\n> 目录\n", encoding="utf-8")
    facts = parse(source)

    resolution = resolve_source_regions(source, facts)

    assert resolution["excluded_source_positions"] == []
    assert all(
        region["semantic_scope"] != "excluded"
        for region in resolution["regions"]["regions"]
    )


def test_structure_candidates_keep_heading_and_body_roles_in_source_facts(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    regions = classify_regions(facts, front_matter_positions={1}, toc_positions={3})
    candidates = collect_structure_candidates(facts, regions)

    title_candidates = [item for item in candidates if item["proposal"].get("value", {}).get("role") == "title"]
    assert len(title_candidates) == 1
    assert title_candidates[0]["proposal"]["value"] == {"role": "title"}
    assert all(item["target_source_refs"] for item in candidates)


def test_structure_bridge_applies_native_roles_by_source_location_without_touching_special_roles(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 文档标题\n\n## 第一章\n\n正文\n", encoding="utf-8")
    facts = parse(source)
    candidates = collect_structure_candidates(facts, classify_regions(facts))
    legacy_model = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "a", "block_type": "body", "text": "文档标题", "source": {"line_start": 1}},
                {"id": "b", "block_type": "body", "text": "第一章", "source": {"line_start": 3}},
                {"id": "c", "block_type": "list_item", "text": "正文", "level": 1, "source": {"line_start": 5}},
            ]
        },
    }

    applied = apply_structure_decisions(legacy_model, facts, candidates)

    blocks = applied.model["document"]["blocks"]
    assert blocks[0]["block_type"] == "heading"
    assert blocks[0]["role"] == "title"
    assert blocks[0]["level"] == 0
    assert blocks[1]["block_type"] == "heading"
    assert blocks[1]["role"] == "heading"
    assert blocks[1]["level"] == 1
    assert blocks[2]["block_type"] == "list_item"
    assert blocks[2]["level"] == 1
    assert applied.evidence == {
        "candidate_count": 3,
        "applied_count": 2,
        "unchanged_count": 0,
        "skipped_special_role_count": 1,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    assert legacy_model["document"]["blocks"][0]["block_type"] == "body"


def test_structure_candidates_do_not_reclassify_nodes_excluded_by_source_regions(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    regions = classify_regions(facts, front_matter_positions={1}, toc_positions={3})

    candidates = collect_structure_candidates(facts, regions)
    candidate_ids = {
        node_id
        for candidate in candidates
        for node_id in candidate["target_node_ids"]
    }
    excluded_ids = {
        node_id
        for region in regions["regions"]
        if region["semantic_scope"] == "excluded"
        for node_id in region["node_ids"]
    }

    assert excluded_ids
    assert candidate_ids.isdisjoint(excluded_ids)


def test_structure_equivalence_reports_role_and_level_differences_per_source_node(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 文档标题\n\n## 第一章\n\n正文\n", encoding="utf-8")
    facts = parse(source)
    regions = classify_regions(facts)
    candidates = collect_structure_candidates(facts, regions)
    legacy_ast = build_source_ast(facts)
    paragraph_ids = [
        node_id
        for node_id in facts["children"]["root"]
        if node_id in legacy_ast["document"]["blocks"]
    ]
    legacy_ast["document"]["blocks"][paragraph_ids[0]]["role"] = "title"
    legacy_ast["document"]["blocks"][paragraph_ids[0]].pop("level", None)
    legacy_ast["document"]["blocks"][paragraph_ids[1]]["level"] = 1

    wrong_candidates = copy.deepcopy(candidates)
    first_value = wrong_candidates[0]["proposal"]["value"]
    first_value.clear()
    first_value.update({"role": "heading", "level": 1})
    wrong_candidates[1]["proposal"]["value"]["level"] = 2

    audit = compare_structure_equivalence(facts, regions, wrong_candidates, legacy_ast)

    assert audit["status"] == "mismatch"
    assert audit["matched_node_count"] == 3
    assert audit["compared_node_count"] == 3
    assert [(issue["type"], issue["text"]) for issue in audit["issues"]] == [
        ("role_mismatch", "文档标题"),
        ("level_mismatch", "第一章"),
    ]


def test_markdown_structure_candidates_match_legacy_title_and_shifted_heading_levels(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 文档标题\n\n## 第一章\n\n正文\n", encoding="utf-8")
    facts = parse(source)
    regions = classify_regions(facts)
    candidates = collect_structure_candidates(facts, regions)
    legacy_ast = build_source_ast(facts)
    paragraph_ids = facts["children"]["root"]
    legacy_ast["document"]["blocks"][paragraph_ids[0]]["role"] = "title"
    legacy_ast["document"]["blocks"][paragraph_ids[0]].pop("level", None)
    legacy_ast["document"]["blocks"][paragraph_ids[1]]["level"] = 1

    audit = compare_structure_equivalence(facts, regions, candidates, legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["issues"] == []


def test_docx_structure_candidates_use_styles_and_leave_caption_to_its_owner(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("", style="Title")
    document.add_paragraph("文档标题", style="Title")
    document.add_paragraph("第一章", style="Heading 1")
    document.add_paragraph("正文内容", style="Normal")
    document.add_paragraph("图1 示例", style="Caption")
    document.save(source)
    facts = parse(source)
    regions = classify_regions(facts)

    candidates = collect_structure_candidates(facts, regions)
    by_text = {
        "".join(
            item.get("text", "")
            for item in facts["units"][candidate["target_node_ids"][0]].get("inline_content", [])
            if item.get("kind") == "text"
        ): candidate["proposal"]["value"]
        for candidate in candidates
    }

    assert by_text["文档标题"] == {"role": "title"}
    assert by_text["第一章"] == {"role": "heading", "level": 1}
    assert by_text["正文内容"] == {"role": "body"}
    assert "图1 示例" not in by_text


def test_docx_structure_candidates_preserve_deep_heading_as_supported_heading(tmp_path: Path) -> None:
    source = tmp_path / "deep-heading.docx"
    document = Document()
    document.add_paragraph("深层标题", style="Heading 7")
    document.add_paragraph("更深层标题", style="Heading 8")
    document.save(source)

    facts = parse(source)
    candidates = collect_structure_candidates(facts, classify_regions(facts))
    by_text = {
        inline_text_projection(
            facts["units"][candidate["target_node_ids"][0]].get("inline_content", [])
        ): candidate["proposal"]["value"]
        for candidate in candidates
    }

    assert by_text["深层标题"] == {"role": "heading", "level": 6}
    assert by_text["更深层标题"] == {"role": "heading", "level": 6}


def test_docx_source_positions_count_only_convertible_body_blocks(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("第一段")
    document.add_paragraph("第二段")
    marker = OxmlElement("w:bookmarkStart")
    marker.set(qn("w:id"), "99")
    marker.set(qn("w:name"), "probe")
    document.element.body.insert(0, marker)
    document.save(source)

    facts = parse(source)
    positions = [
        facts["evidence"][node_id]["source_position"]
        for node_id in facts["children"]["root"]
        if node_id in facts["units"]
    ]

    assert positions == [0, 1]


def test_document_structure_does_not_claim_table_cell_paragraphs(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Title")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].text = "单元格内标题"
    table.cell(0, 0).paragraphs[0].style = "Heading 1"
    document.add_paragraph("正文章节", style="Heading 1")
    document.save(source)
    facts = parse(source)

    candidates = collect_structure_candidates(facts, classify_regions(facts))
    texts = {
        "".join(
            item.get("text", "")
            for item in facts["units"][candidate["target_node_ids"][0]].get("inline_content", [])
            if item.get("kind") == "text"
        )
        for candidate in candidates
    }

    assert "正文章节" in texts
    assert "单元格内标题" not in texts


def test_docx_heading_named_toc_is_not_excluded_without_toc_position_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("模板使用说明", style="Heading 1")
    document.add_paragraph("目次", style="Heading 2")
    document.add_paragraph("正文内容", style="Normal")
    document.save(source)
    facts = parse(source)

    regions = classify_regions(facts)
    scope_by_text = {
        "".join(
            item.get("text", "")
            for item in facts["units"][region["node_ids"][0]].get("inline_content", [])
            if item.get("kind") == "text"
        ): region["semantic_scope"]
        for region in regions["regions"]
        if region["node_ids"][0] in facts["units"]
    }

    assert scope_by_text["目次"] == "body"


def test_structure_equivalence_matches_docx_headings_after_manual_number_cleanup(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("系统建设方案", style="Title")
    document.add_paragraph("1 项目概述", style="Heading 1")
    document.save(source)
    facts = parse(source)
    regions = classify_regions(facts)
    candidates = collect_structure_candidates(facts, regions)
    legacy_ast = build_source_ast(facts)
    title_id, heading_id = facts["children"]["root"][:2]
    legacy_ast["document"]["blocks"][title_id]["role"] = "title"
    legacy_ast["document"]["blocks"][title_id].pop("level", None)
    legacy_ast["document"]["blocks"][heading_id]["role"] = "heading"
    legacy_ast["document"]["blocks"][heading_id]["level"] = 1
    legacy_ast["document"]["blocks"][heading_id]["inline_content"] = [
        {"kind": "text", "text": "项目概述", "marks": []}
    ]

    audit = compare_structure_equivalence(facts, regions, candidates, legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["matched_node_count"] == 2
    assert audit["issues"] == []


def test_structure_equivalence_ignores_roles_owned_by_later_semantic_modules(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("注：保留说明\n", encoding="utf-8")
    facts = parse(source)
    regions = classify_regions(facts)
    candidates = collect_structure_candidates(facts, regions)
    legacy_ast = build_source_ast(facts)
    node_id = facts["children"]["root"][0]
    legacy_ast["document"]["blocks"][node_id]["role"] = "note"

    audit = compare_structure_equivalence(facts, regions, candidates, legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["compared_node_count"] == 0
    assert audit["ignored_other_domain_count"] == 1
    assert audit["issues"] == []


def test_structure_equivalence_ignores_legacy_appendix_subtree(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("附录示例\n", encoding="utf-8")
    facts = parse(source)
    regions = classify_regions(facts)
    legacy_ast = build_source_ast(facts)
    title_id = facts["children"]["root"][0]
    legacy_ast["document"]["blocks"][title_id]["role"] = "heading"
    legacy_ast["document"]["blocks"][title_id]["level"] = 1
    legacy_ast["document"]["containers"]["appendix-a"] = {
        "kind": "appendix",
        "source_classification": "unknown",
        "title_ref": title_id,
    }
    legacy_ast["document"]["children"]["root"] = ["appendix-a"]
    legacy_ast["document"]["children"]["appendix-a"] = [title_id]

    audit = compare_structure_equivalence(facts, regions, [], legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["issues"] == []


def test_parser_equivalence_covers_source_text_without_claiming_semantic_roles(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 文档标题\n\n正文内容\n", encoding="utf-8")
    facts = parse(source)
    regions = classify_regions(facts)
    legacy_ast = build_source_ast(facts)

    audit = compare_parser_equivalence(facts, regions, legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["passed"] is True
    assert audit["domains"]["text"]["native_count"] == 2
    assert audit["domains"]["text"]["legacy_count"] == 2
    assert audit["domains"]["lists"]["status"] == "equivalent"
    assert audit["issues"] == []


def test_parser_equivalence_blocks_switch_when_legacy_list_has_no_native_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("条目内容\n", encoding="utf-8")
    facts = parse(source)
    regions = classify_regions(facts)
    legacy_ast = build_source_ast(facts)
    paragraph_id = facts["children"]["root"][0]
    legacy_ast["document"]["containers"].update({
        "legacy-list": {
            "kind": "list",
            "list_kind": "ordered",
            "source_start": 1,
            "source_sequence_id": "legacy-sequence",
        },
        "legacy-item": {
            "kind": "list_item",
            "source_marker": "1.",
            "source_ordinal": 1,
            "source_sequence_id": "legacy-sequence",
        },
    })
    legacy_ast["document"]["children"].update({
        "root": ["legacy-list"],
        "legacy-list": ["legacy-item"],
        "legacy-item": [paragraph_id],
    })

    audit = compare_parser_equivalence(facts, regions, legacy_ast)

    assert audit["status"] == "mismatch"
    assert audit["passed"] is False
    assert audit["domains"]["lists"] == {
        "status": "mismatch",
        "native_count": 0,
        "legacy_count": 1,
    }
    assert {issue["type"] for issue in audit["issues"]} == {"list_item_count_mismatch"}


def test_protected_candidates_mark_notes_code_api_and_quotes_without_retyping_them(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    validate_source_facts(facts)
    candidates = collect_protected_candidates(facts)

    reasons = {item["proposal"]["value"]["reason"] for item in candidates}
    assert "note" in reasons
    assert "opaque_code" in reasons
    assert "api_label" in reasons
    assert "quote" in reasons
    assert all(item["proposal"]["operation"] == "update_overlay" for item in candidates)
