"""Input-document review orchestration.

The reviewer audits a source DOCX against the template before conversion.  It
runs the same parsers and semantic modules the conversion runs, then evaluates
one check per template requirement (see ``review_checks``), so a finding means
the source would have driven the pipeline to the same conclusion.

The result is a 100-point compliance score, a graded issue list ordered by
severity, the items that already comply, and the risk notes a human should
confirm.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from docx import Document

from template_profile import load_template_profile
from wxdf.parsers import parse
from wxdf.review_checks import (
    check_appendix,
    check_captions,
    check_format_conformance,
    check_headings,
    check_lists,
    check_notes,
    check_page_numbering,
    check_styles,
    check_table_contract,
    check_tables,
    check_toc,
)
from wxdf.review_scoring import build_scoring_result
from wxdf.review_enhancement import (
    build_review_requests,
    call_direct,
    collect_findings,
)
from wxdf.semantics.captions import collect_source_caption_semantics
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.tables import collect_source_table_semantics

DIMENSION_FORMAT = "format_conformance"
DIMENSION_HEADING = "heading_hierarchy"
DIMENSION_LIST = "list_structure"
DIMENSION_TABLE = "table_format"
DIMENSION_TOC = "toc_structure"
DIMENSION_APPENDIX = "appendix_structure"
DIMENSION_CAPTION = "caption"
DIMENSION_NOTE = "note_structure"
DIMENSION_NUMBERING = "numbering_page"
DIMENSION_AI = "ai_review"

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_DIMENSION_LABELS = {
    DIMENSION_FORMAT: "样式与元素格式符合模板",
    DIMENSION_HEADING: "标题层级与编号正确",
    DIMENSION_LIST: "列表结构与编号合规",
    DIMENSION_TABLE: "表格格式合规",
    DIMENSION_TOC: "目录符合模板要求",
    DIMENSION_APPENDIX: "附录结构合规",
    DIMENSION_CAPTION: "表格与图片题注完整",
    DIMENSION_NOTE: "注样式使用合规",
    DIMENSION_NUMBERING: "页码、公式与图表编号合规",
    DIMENSION_AI: "语义检查通过（引用文件、术语缩略语、列项用法）",
}


def _ai_review(facts: Mapping[str, Any], enhancement: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the review's AI capabilities and return ``(findings, status)``.

    The status records what happened per capability, so a report can say which
    semantic checks ran and which did not, instead of leaving the reader to guess
    whether an absent finding means "clean" or "never checked".
    """

    if enhancement is None or not getattr(enhancement, "enabled", False):
        return [], {"status": "not_requested", "capabilities": {}}

    requests = build_review_requests(facts, str(enhancement.mode))
    if not requests:
        return [], {"status": "no_material", "capabilities": {}}

    call = getattr(enhancement, "call", None)
    if call is not None:
        responses = call_direct(requests, call)
    else:
        responses = list(getattr(enhancement, "responses", ()) or ())

    findings, diagnostics = collect_findings(requests, responses)
    responded = {str(item.get("request_id")) for item in responses if isinstance(item, Mapping)}
    capabilities: dict[str, Any] = {}
    for request in requests:
        capability = str(request.get("capability"))
        responded_here = str(request.get("request_id")) in responded
        capabilities[capability] = {
            "status": "completed" if responded_here else "missing_response",
            "finding_count": sum(1 for finding in findings if finding.get("capability") == capability),
        }
    return findings, {
        "status": "completed",
        "capabilities": capabilities,
        "item_count": sum(len(request.get("items") or []) for request in requests),
        "diagnostics": diagnostics,
    }


def plan_review_requests(input_path: Path | str, template_path: Path | str, mode: str) -> list[dict[str, Any]]:
    """Build the AI requests for a document without running the review.

    The offline transport needs the requests before any response exists, so the
    caller can write them out for an agent to answer and resume afterwards.
    """

    facts = parse(Path(input_path), {"source_store": None, "source_type": "docx"})
    return build_review_requests(facts, mode)


def review_input_document(
    input_path: Path | str,
    template_path: Path | str,
    *,
    enhancement: Any = None,
) -> dict[str, Any]:
    input_path = Path(input_path)
    template_path = Path(template_path)
    doc = Document(input_path)
    template_doc = Document(template_path)
    template_profile = load_template_profile(template_path)
    facts = parse(input_path, {"source_store": None, "source_type": "docx"})

    ai_findings, ai_review = _ai_review(facts, enhancement)

    list_analysis = analyze_lists(facts)
    table_semantics = collect_source_table_semantics(facts)
    caption_semantics = collect_source_caption_semantics(facts, table_semantics)

    table_check = check_tables(facts, table_semantics)
    roles = [str(table.get("table_type") or "data") for table in table_semantics]
    table_contract = check_table_contract(doc, template_profile, roles)
    table_check = {
        "total_items": table_check["total_items"],
        "issues": [*table_check["issues"], *table_contract["issues"]],
    }

    style_check = check_styles(doc, template_profile)
    format_check = check_format_conformance(doc, template_doc, template_profile)

    dimension_results = {
        DIMENSION_FORMAT: {
            "total_items": max(style_check["total_items"], format_check["total_items"]),
            "issues": [*style_check["issues"], *format_check["issues"]],
        },
        DIMENSION_HEADING: check_headings(facts),
        DIMENSION_LIST: check_lists(facts, list_analysis),
        DIMENSION_TABLE: table_check,
        DIMENSION_TOC: check_toc(doc),
        DIMENSION_APPENDIX: check_appendix(facts),
        DIMENSION_CAPTION: check_captions(doc, table_semantics, caption_semantics),
        DIMENSION_NOTE: check_notes(doc, template_profile),
        DIMENSION_NUMBERING: check_page_numbering(doc, template_doc),
    }
    # The AI dimension is present only when the semantic checks ran.  Scoring
    # skips a dimension that is absent, so a plain review neither gains nor loses
    # points for checks it never made.
    if ai_review.get("status") in {"completed", "no_material"}:
        dimension_results[DIMENSION_AI] = {
            "total_items": max(int(ai_review.get("item_count") or 0), 1),
            "issues": ai_findings,
        }

    scoring = build_scoring_result(dimension_results)
    all_issues: list[dict[str, Any]] = []
    for result in dimension_results.values():
        all_issues.extend(result.get("issues", []))
    all_issues.sort(key=lambda issue: _SEVERITY_ORDER.get(str(issue.get("level")), 4))

    counts = {level: 0 for level in _SEVERITY_ORDER}
    for issue in all_issues:
        level = str(issue.get("level"))
        if level in counts:
            counts[level] += 1

    compliant_items = [
        label
        for dimension, label in _DIMENSION_LABELS.items()
        if not (dimension_results.get(dimension) or {}).get("issues")
        and dimension in dimension_results
    ]

    risk_warnings = []
    if counts["critical"] > 0:
        risk_warnings.append(f"存在 {counts['critical']} 个严重问题，可能影响文档可读性")
    if counts["high"] > 0:
        risk_warnings.append(f"存在 {counts['high']} 个高优先级问题，建议立即修复")
    if counts["low"] > 0:
        risk_warnings.append(f"存在 {counts['low']} 个低优先级问题，可按需修正")
    if not risk_warnings:
        risk_warnings.append("未发现需要人工复核的高风险项")

    return {
        "input_file": str(input_path),
        "template_file": str(template_path),
        "score": scoring["score"],
        "grade": scoring["grade"],
        "passed": scoring["passed"],
        "dimension_scores": scoring["dimension_scores"],
        "summary": {
            "total_issues": len(all_issues),
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
        },
        "issues": all_issues,
        "compliant_items": compliant_items,
        "risk_warnings": risk_warnings,
        "ai_review": ai_review,
    }
