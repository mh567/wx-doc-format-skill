from __future__ import annotations

from pathlib import Path


def new_report(skill_version: str) -> dict:
    return {
        "skill_version": skill_version,
        "inferred_headings": [],
        "suspect_visual_headings": [],
        "inferred_lists": [],
        "automatic_numbers": [],
        "ambiguous_short_paragraphs": [],
        "content_warnings": [],
        "tables_processed": 0,
        "semantic_object_splits": [],
        "mixed_text_graphic_paragraphs_split": [],
        "heading_numbering_repairs": [],
        "graphic_paragraphs_preserved": 0,
        "media_relationships_preserved": 0,
        "non_text_objects": {},
        "risk_warnings": [],
        "document_model_summary": {},
        "document_model_issues": [],
        "model_normalization_repairs": [],
        "rendered_document_model_summary": {},
        "rendered_document_model_issues": [],
        "document_model_diff": {},
        "source_document_model_summary": {},
        "source_document_model_issues": [],
        "template_finalizer": {},
        "note_preservation_audit": {},
        "audit": {},
        "review_packet": {},
        "review_loop": {},
    }


def add_risk_warnings(report: dict, row_height_rule: str) -> None:
    non_text = report.get("non_text_objects", {})
    risky_objects = {key: value for key, value in non_text.items() if value}
    source_media = int(non_text.get("media_files") or 0)
    preserved_media = int(report.get("media_relationships_preserved") or 0)
    report["media_preservation_ratio"] = (preserved_media / source_media) if source_media else 1.0
    if source_media > preserved_media:
        report["risk_warnings"].append(
            {
                "type": "media_not_fully_preserved",
                "message": "Source document contains media files that were not all preserved in the output.",
                "source_media_files": source_media,
                "preserved_media_relationships": preserved_media,
            }
        )
    if risky_objects:
        report["risk_warnings"].append(
            {
                "type": "non_text_objects",
                "message": "Source document contains objects that may not be fully rebuilt by text normalization.",
                "objects": risky_objects,
            }
        )
    note_audit = report.get("note_preservation_audit", {})
    if note_audit and not note_audit.get("passed", False):
        report["risk_warnings"].append(
            {
                "type": "note_preservation",
                "message": "Source note semantics do not match the normalized AST or rendered styles.",
                "count": len(note_audit.get("issues", [])),
            }
        )
    clipped_cells = report.get("audit", {}).get("table_cells_may_clip", [])
    if row_height_rule == "exact" and clipped_cells:
        report["risk_warnings"].append(
            {
                "type": "table_row_height",
                "message": "Fixed table row height may clip long cell text. Render and review affected tables.",
                "count": len(clipped_cells),
            }
        )
    table_contract = report.get("audit", {}).get("table_format_contract", {})
    if table_contract and not table_contract.get("passed", False):
        issue_count = sum(
            len(value)
            for key, value in table_contract.items()
            if (key.endswith("issues") or key == "invalid_table_styles") and isinstance(value, list)
        )
        report["risk_warnings"].append(
            {
                "type": "table_format_contract",
                "message": "Rendered tables contain formatting that overrides the WX table contract.",
                "count": issue_count,
            }
        )
    list_without_restart = report.get("audit", {}).get("ordered_list_nums_without_restart", [])
    if list_without_restart:
        report["risk_warnings"].append(
            {
                "type": "ordered_list_restart",
                "message": "Some ordered list numbering instances do not explicitly restart at 1.",
                "count": len(list_without_restart),
            }
        )
    heading_warnings = report.get("audit", {}).get("heading_hierarchy_warnings", [])
    if heading_warnings:
        report["risk_warnings"].append(
            {
                "type": "heading_hierarchy",
                "message": "Heading hierarchy may be inconsistent. Review inferred heading levels.",
                "count": len(heading_warnings),
            }
        )
    unnumbered_headings = report.get("audit", {}).get("heading_paragraphs_without_numbering", [])
    if unnumbered_headings:
        report["risk_warnings"].append(
            {
                "type": "heading_auto_numbering",
                "message": "Some heading paragraphs do not have direct Word automatic numbering.",
                "count": len(unnumbered_headings),
            }
        )
    manual_heading_numbers = report.get("audit", {}).get("heading_text_still_has_manual_number", [])
    if manual_heading_numbers:
        report["risk_warnings"].append(
            {
                "type": "heading_manual_number_text",
                "message": "Some heading paragraphs still contain rendered numbering in text.",
                "count": len(manual_heading_numbers),
            }
        )
    model_issues = report.get("document_model_issues", [])
    if model_issues:
        report["risk_warnings"].append(
            {
                "type": "document_model",
                "message": "Normalized document model violates one or more structural invariants.",
                "count": len(model_issues),
            }
        )
    source_model_issues = report.get("source_document_model_issues", [])
    if source_model_issues:
        report["risk_warnings"].append(
            {
                "type": "source_document_model",
                "message": "Parsed source document model violates one or more structural invariants.",
                "count": len(source_model_issues),
            }
        )
    appendix_preservation = report.get("appendix_preservation_audit", {})
    if appendix_preservation and not appendix_preservation.get("passed", False):
        report["risk_warnings"].append(
            {
                "type": "appendix_preservation",
                "message": "Appendix structure or 2.2.5 formatting contract has issues.",
                "count": len(appendix_preservation.get("issues", [])),
                "issues": appendix_preservation.get("issues", []),
            }
        )
    model_diff_warnings = report.get("document_model_diff", {}).get("warnings", [])
    if model_diff_warnings:
        report["risk_warnings"].append(
            {
                "type": "document_model_diff",
                "message": "Source and normalized document models differ in block counts.",
                "count": len(model_diff_warnings),
            }
        )
    unexpected_template_styles = report.get("template_finalizer", {}).get("style_audit", {}).get("unexpected_styles", [])
    if unexpected_template_styles:
        report["risk_warnings"].append(
            {
                "type": "template_styles",
                "message": "Rendered document uses paragraph styles outside the resolved template style map.",
                "count": len(unexpected_template_styles),
            }
        )
    template_layout_warnings = report.get("template_finalizer", {}).get("layout_audit", {}).get("warnings", [])
    if template_layout_warnings:
        report["risk_warnings"].append(
            {
                "type": "template_layout",
                "message": "Template layout audit found items that require Word/WPS review.",
                "count": len(template_layout_warnings),
                "warnings": template_layout_warnings,
            }
        )
    output_structure = report.get("output_structure_audit", {})
    if output_structure and not output_structure.get("passed", False):
        report["risk_warnings"].append(
            {
                "type": "output_structure",
                "message": "Output does not follow the canonical TOC, title, and body order.",
                "issues": output_structure.get("issues", []),
            }
        )
    table_semantics = report.get("table_semantics_audit", {})
    if table_semantics and not table_semantics.get("passed", False):
        report["risk_warnings"].append(
            {
                "type": "table_semantics",
                "message": "Table semantics or caption eligibility audit found issues.",
                "issues": table_semantics.get("issues", []),
            }
        )
    caption_model = report.get("caption_placement_model_audit", {})
    caption_output = report.get("caption_placement_audit", {})
    caption_issues = list(caption_model.get("issues", [])) + list(caption_output.get("issues", []))
    if caption_issues:
        report["risk_warnings"].append(
            {
                "type": "caption_placement",
                "message": "Caption association or placement violates the WX document contract.",
                "count": len(caption_issues),
                "issues": caption_issues,
            }
        )


def write_markdown_report(report: dict, path: Path) -> None:
    lines = ["# WX 文档格式转换报告", ""]
    audit = report.get("audit", {})
    parse_report = report.get("parse_report", {})
    template_finalizer = report.get("template_finalizer", {})
    template_style_audit = template_finalizer.get("style_audit", {})
    lines.extend(
        [
            "## 概览",
            f"- Skill 版本：{report.get('skill_version', 'unknown')}",
            f"- 段落数：{audit.get('paragraph_count', 0)}",
            f"- 表格数：{audit.get('table_count', 0)}",
            f"- 已处理表格数：{report.get('tables_processed', 0)}",
            f"- 已拆分语义对象混合段落数：{len(report.get('semantic_object_splits', []))}",
            f"- 已保留图片段落数：{report.get('graphic_paragraphs_preserved', 0)}",
            f"- 已保留媒体关系数：{report.get('media_relationships_preserved', 0)}",
            f"- 媒体保留比例：{report.get('media_preservation_ratio', 1.0):.2f}",
            f"- 源中间结构块数：{report.get('source_document_model_summary', {}).get('block_count', 0)}",
            f"- 源中间结构问题数：{report.get('source_document_model_summary', {}).get('issue_count', 0)}",
            f"- 中间结构块数：{report.get('document_model_summary', {}).get('block_count', 0)}",
            f"- 中间结构问题数：{report.get('document_model_summary', {}).get('issue_count', 0)}",
            f"- 模板格式收口修复数：{len(template_finalizer.get('corrections', []))}",
            f"- 非模板样式数：{len(template_style_audit.get('unexpected_styles', []))}",
            f"- 首部结构审计：{'passed' if report.get('output_structure_audit', {}).get('passed') else 'failed'}",
            f"- 表格语义审计：{'passed' if report.get('table_semantics_audit', {}).get('passed') else 'failed'}",
            f"- 题注位置审计：{'passed' if report.get('caption_placement_audit', {}).get('passed') else 'failed'}",
            f"- 注语义保留审计：{'passed' if report.get('note_preservation_audit', {}).get('passed') else 'failed'}",
            f"- 语义列表候选组：{parse_report.get('parallel_content_candidates', 0)}",
            f"- 待增强复核列表组：{parse_report.get('unresolved_parallel_groups', 0)}",
            "",
        ]
    )
    for title, key in [
        ("推断标题", "inferred_headings"),
        ("疑似视觉标题", "suspect_visual_headings"),
        ("推断列项", "inferred_lists"),
        ("自动编号", "automatic_numbers"),
        ("标题编号收口修复", "heading_numbering_repairs"),
        ("已拆分语义对象混合段落", "semantic_object_splits"),
        ("模糊短段落", "ambiguous_short_paragraphs"),
        ("源中间结构问题", "source_document_model_issues"),
        ("中间结构问题", "document_model_issues"),
    ]:
        items = report.get(key, [])
        lines.append(f"## {title}")
        if not items:
            lines.append("- 无")
        else:
            for item in items[:50]:
                lines.append(f"- {item}")
        lines.append("")
    lines.append("## 中间结构差异")
    model_diff = report.get("document_model_diff", {})
    if not model_diff or not model_diff.get("available"):
        lines.append("- 无")
    else:
        lines.append(f"- 源块数：{model_diff.get('source_block_count', 0)}")
        lines.append(f"- 最终块数：{model_diff.get('final_block_count', 0)}")
        lines.append(f"- 块数差异：{model_diff.get('block_count_delta', 0)}")
        lines.append(f"- 类型差异：{model_diff.get('type_count_delta', {})}")
        warnings = model_diff.get("warnings", [])
        if warnings:
            lines.append(f"- 差异提示：{warnings[:20]}")
    lines.append("")
    lines.append("## 非文本对象")
    for key, value in report.get("non_text_objects", {}).items():
        lines.append(f"- {key}：{value}")
    lines.append("")
    lines.append("## 风险提示")
    risk_warnings = report.get("risk_warnings", [])
    if not risk_warnings:
        lines.append("- 无")
    else:
        for warning in risk_warnings:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append("## 增强模式审计闭环")
    review = report.get("review_loop", {})
    packet = report.get("review_packet", {})
    lines.append(f"- 状态：{review.get('status', 'not_requested')}")
    lines.append(f"- 是否触发：{review.get('triggered', False)}")
    lines.append(f"- 执行轮数：{review.get('rounds', 0)}")
    lines.append(f"- 可修复审计项：{packet.get('repairable_count', 0)}")
    if review.get("before_score"):
        lines.append(f"- 修复前评分：{review.get('before_score')}")
        lines.append(f"- 修复后评分：{review.get('after_score')}")
    if review.get("reason"):
        lines.append(f"- 处置原因：{review.get('reason')}")
    lines.append("")
    lines.append("## 内容型复核提示")
    content_warnings = report.get("content_warnings", [])
    if not content_warnings:
        lines.append("- 无")
    else:
        for warning in content_warnings:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append("## 审计问题")
    problem_keys = [
        "table_paragraphs_not_table_body",
        "table_rows_bad_height",
        "table_cells_may_clip",
        "code_sample_table_alignment_issues",
        "markdown_residue",
        "heading_paragraphs_without_numbering",
        "heading_text_still_has_manual_number",
        "ordered_list_nums_without_restart",
        "heading_hierarchy_warnings",
    ]
    has_problem = False
    for key in problem_keys:
        values = audit.get(key, [])
        if values:
            has_problem = True
            lines.append(f"### {key}")
            for value in values[:50]:
                lines.append(f"- {value}")
            lines.append("")
    if not has_problem:
        lines.append("- 未发现结构化审计问题")
    lines.append("")
    table_contract = audit.get("table_format_contract", {})
    lines.append("## 表格格式合同")
    lines.append(f"- 是否通过：{table_contract.get('passed', False)}")
    lines.append(f"- 表格数：{table_contract.get('table_count', 0)}")
    lines.append(f"- 行数：{table_contract.get('row_count', 0)}")
    lines.append(f"- 单元格数：{table_contract.get('cell_count', 0)}")
    lines.append(f"- 段落数：{table_contract.get('paragraph_count', 0)}")
    for key, values in table_contract.items():
        if (key.endswith("issues") or key == "invalid_table_styles") and values:
            lines.append(f"- {key}：{values[:20]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
