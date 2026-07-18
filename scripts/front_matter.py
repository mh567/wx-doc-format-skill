"""Normalize DOCX front matter into one canonical document title.

The source document stays unchanged. Cover blocks are represented by stable
source positions, excluded from semantic parsing and direct rendering, and
replaced with one synthetic title block. The finalizer can then prepend the
canonical TOC without carrying the source cover into the output.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from document_model import heading_block, source_record


_TITLE_STYLE_NAMES = {
    "title", "documenttitle", "document title", "文档标题", "文件标题", "主标题",
}
_METADATA_RE = re.compile(
    r"^(?:版本|version|文档编号|文件编号|密级|状态|作者|编制|日期|"
    r"修订|审核|批准|文档属性|编制单位)\s*[：:]?",
    re.I,
)
_GENERIC_COVER_RE = re.compile(
    r"^(?:内部(?:技术)?文件|内部资料|仅供内部使用|机密|秘密|公开)$",
    re.I,
)
_DOCUMENT_KIND_RE = re.compile(
    r"(?:方案|报告|说明|规范|手册|设计|计划|总结|纪要|指南|需求|"
    r"标准|办法|制度|细则|规程|建议书|可研|初设|详设)$"
)
_DATE_RE = re.compile(r"^(?:\d{4}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?|二〇?二\S+年)")
_FILENAME_SUFFIX_RE = re.compile(
    r"(?:[-_\s](?:重构稿|修订稿|最终稿|正式稿|定稿|初稿|final|draft|v?\d+(?:\.\d+)*))+$",
    re.I,
)


def _normalized_style(style: str | None) -> str:
    return re.sub(r"\s+", "", (style or "").casefold())


def _is_title_style(style: str | None) -> bool:
    normalized = _normalized_style(style)
    return normalized in {_normalized_style(name) for name in _TITLE_STYLE_NAMES}


def _has_page_boundary(paragraph: Paragraph) -> bool:
    element = paragraph._p
    if any(br.get(qn("w:type")) == "page" for br in element.iter(qn("w:br"))):
        return True
    if next(element.iter(qn("w:lastRenderedPageBreak")), None) is not None:
        return True
    return next(element.iter(qn("w:sectPr")), None) is not None


def _paragraph_format(paragraph: Paragraph) -> tuple[bool, float | None]:
    run_count = 0
    bold_count = 0
    max_size = 0.0
    for run in paragraph.runs:
        run_count += 1
        if run.bold:
            bold_count += 1
        if run.font.size is not None:
            max_size = max(max_size, float(run.font.size.pt))
    mostly_bold = bool(run_count and bold_count > run_count / 2)
    return mostly_bold, max_size or None


def _record_source_blocks(doc) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for position, child in enumerate(
        item for item in doc.element.body.iterchildren()
        if item.tag in {qn("w:p"), qn("w:tbl")}
    ):
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, doc)
            text = paragraph.text.strip()
            style = paragraph.style.name if paragraph.style is not None else ""
            mostly_bold, max_size = _paragraph_format(paragraph)
            records.append({
                "position": position,
                "kind": "paragraph",
                "text": text,
                "style": style,
                "is_title_style": _is_title_style(style),
                "is_heading_style": style.casefold().startswith("heading"),
                "is_heading_one": bool(re.match(r"^heading\s*1$", style, re.I)),
                "is_centered": paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER,
                "mostly_bold": mostly_bold,
                "max_size_pt": max_size,
                "has_page_boundary": _has_page_boundary(paragraph),
            })
            continue

        table = Table(child, doc)
        items = [
            paragraph.text.strip()
            for row in table.rows
            for cell in row.cells
            for paragraph in cell.paragraphs
            if paragraph.text.strip()
        ]
        records.append({
            "position": position,
            "kind": "table",
            "text": " ".join(items),
            "style": "",
            "items": items,
            "is_title_style": False,
            "is_heading_style": False,
            "is_heading_one": False,
            "is_centered": False,
            "mostly_bold": False,
            "max_size_pt": None,
            "has_page_boundary": False,
        })
    return records


def _is_metadata_text(text: str) -> bool:
    value = text.strip()
    return bool(
        not value
        or _METADATA_RE.match(value)
        or _DATE_RE.match(value)
        or _GENERIC_COVER_RE.match(value)
    )


def _is_metadata_table(record: dict[str, Any]) -> bool:
    if record.get("kind") != "table":
        return False
    items = record.get("items", [])
    matches = sum(bool(_METADATA_RE.match(item.strip())) for item in items)
    return matches >= 1 or (len(items) >= 4 and all(len(item) <= 40 for item in items))


def _title_score(record: dict[str, Any]) -> tuple[float, list[str]]:
    text = record.get("text", "").strip()
    if record.get("kind") != "paragraph" or not text or _is_metadata_text(text):
        return -1000.0, ["metadata_or_empty"]
    if len(text) > 100 or text.endswith(("。", "；", ";")):
        return -1000.0, ["body_like_text"]

    score = 0.0
    evidence: list[str] = []
    if record.get("is_title_style"):
        score += 100
        evidence.append("title_style")
    if record.get("is_centered"):
        score += 25
        evidence.append("centered")
    if record.get("mostly_bold"):
        score += 20
        evidence.append("mostly_bold")
    max_size = record.get("max_size_pt")
    if max_size is not None and max_size >= 14:
        score += min(35, max_size)
        evidence.append("large_font")
    if 4 <= len(text) <= 60:
        score += 12
        evidence.append("title_length")
    if _DOCUMENT_KIND_RE.search(text):
        score += 30
        evidence.append("document_kind_suffix")
    if record.get("is_heading_style"):
        score += 10
        evidence.append("heading_style")
    score += max(0, 8 - int(record.get("position") or 0) * 0.5)
    return score, evidence


def _first_structural_heading(records: list[dict[str, Any]], start: int = 0) -> int | None:
    heading_one = next(
        (record["position"] for record in records if record["position"] >= start and record.get("is_heading_one")),
        None,
    )
    if heading_one is not None:
        return heading_one
    return next(
        (record["position"] for record in records if record["position"] >= start and record.get("is_heading_style")),
        None,
    )


def _cover_score(records: list[dict[str, Any]]) -> tuple[int, list[str]]:
    evidence: list[str] = []
    score = 0
    if any(record.get("has_page_boundary") for record in records):
        score += 2
        evidence.append("page_boundary")
    if any(_is_metadata_table(record) for record in records):
        score += 2
        evidence.append("metadata_table")
    if any(_title_score(record)[0] >= 40 for record in records):
        score += 2
        evidence.append("prominent_title")
    if sum(not record.get("text") for record in records if record.get("kind") == "paragraph") >= 2:
        score += 1
        evidence.append("leading_whitespace")
    body_like = [
        record for record in records
        if record.get("kind") == "paragraph"
        and len(record.get("text", "")) > 120
        and not _is_metadata_text(record.get("text", ""))
    ]
    if body_like:
        score -= 3
        evidence.append("body_like_content")
    return score, evidence


def _selected_toc_bounds(toc_context: dict | None) -> tuple[int | None, int | None]:
    if not toc_context:
        return None, None
    selected = [
        candidate for candidate in toc_context.get("document", {}).get("blocks", [])
        if candidate.get("selected")
    ]
    if not selected:
        return None, None
    return (
        int(selected[0]["start_source_position"]),
        int(selected[0]["end_source_position"]),
    )


def _filename_title(source_path: Path | str) -> str:
    stem = Path(source_path).stem.strip()
    value = _FILENAME_SUFFIX_RE.sub("", stem).strip("- _")
    return value or stem or "文档"


def analyze_front_matter(
    doc,
    toc_context: dict | None,
    source_path: Path | str,
    report: dict,
) -> dict:
    """Detect a source cover and select one canonical document title."""
    records = _record_source_blocks(doc)
    toc_start, toc_end = _selected_toc_bounds(toc_context)
    structural_start = _first_structural_heading(records, (toc_end + 1) if toc_end is not None else 0)
    cover_end: int | None = None
    cover_evidence: list[str] = []

    if toc_start is not None and toc_start > 0:
        cover_end = toc_start - 1
        cover_evidence = ["content_before_source_toc"]
    elif toc_start is None:
        scan_end = structural_start if structural_start is not None else min(len(records), 30)
        boundaries = [
            record["position"] for record in records
            if record["position"] < scan_end and record.get("has_page_boundary")
        ]
        if boundaries:
            candidate_end = boundaries[0]
            score, evidence = _cover_score(records[:candidate_end + 1])
            if score >= 4:
                cover_end = candidate_end
                cover_evidence = evidence
        if cover_end is None and structural_start is not None and structural_start > 0:
            prefix = [record for record in records if record["position"] < structural_start]
            score, evidence = _cover_score(prefix)
            if score >= 4:
                cover_end = structural_start - 1
                cover_evidence = evidence + ["before_first_structural_heading"]

    if toc_start is not None:
        cover_evidence.append("source_toc_boundary")

    candidate_pool: list[dict[str, Any]] = []
    if cover_end is not None:
        candidate_pool.extend(record for record in records if record["position"] <= cover_end)

    pre_heading_limit = structural_start if structural_start is not None else min(len(records), 8)
    candidate_pool.extend(
        record for record in records
        if record["position"] < pre_heading_limit and record.get("is_title_style")
    )
    by_position = {record["position"]: record for record in candidate_pool}
    candidates = []
    for record in by_position.values():
        score, evidence = _title_score(record)
        if score >= 40:
            candidates.append((score, -record["position"], record, evidence))
    candidates.sort(reverse=True, key=lambda item: (item[0], item[1]))

    title_record = candidates[0][2] if candidates else None
    title_evidence = candidates[0][3] if candidates else []
    core_title = (getattr(doc.core_properties, "title", "") or "").strip()
    if title_record is not None:
        title_text = title_record["text"].strip()
        title_source = "source"
        title_position = int(title_record["position"])
    elif core_title:
        title_text = core_title
        title_source = "core_properties"
        title_position = None
        title_evidence = ["core_properties_title"]
    else:
        title_text = _filename_title(source_path)
        title_source = "filename"
        title_position = None
        title_evidence = ["filename_fallback"]

    excluded: set[int] = set()
    if cover_end is not None:
        excluded.update(range(0, cover_end + 1))
    if title_position is not None:
        excluded.add(title_position)

    if cover_end is not None:
        status = "cover_detected"
    elif title_position is not None:
        status = "title_normalized"
    else:
        status = "no_cover"

    body_start = (
        toc_end + 1 if toc_end is not None
        else cover_end + 1 if cover_end is not None
        else next((record["position"] for record in records if record.get("text")), 0)
    )
    context = {
        "status": status,
        "body_start_source_position": body_start,
        "cover_end_source_position": cover_end,
        "excluded_source_positions": sorted(excluded),
        "evidence": cover_evidence,
        "title": {
            "text": title_text,
            "source": title_source,
            "source_position": title_position,
            "evidence": title_evidence,
        },
    }
    report["source_front_matter"] = context
    return context


def front_matter_source_positions(context: dict | None) -> set[int]:
    return set((context or {}).get("excluded_source_positions", []))


def inject_document_title(model: dict, context: dict) -> dict:
    """Ensure the semantic AST starts with exactly one title block."""
    title = context.get("title", {})
    text = str(title.get("text") or "文档").strip()
    blocks = model.get("document", {}).get("blocks", [])
    blocks[:] = [
        block for block in blocks
        if not (
            block.get("block_type") == "heading"
            and (block.get("role") == "title" or int(block.get("level") or 0) <= 0)
        )
    ]
    blocks.insert(0, heading_block(
        "b_title",
        text,
        0,
        role="title",
        source=source_record(
            origin="front_matter_normalization",
            source_position=title.get("source_position"),
            title_source=title.get("source"),
        ),
    ))
    model["document"]["title"] = text
    return model


def audit_output_structure(doc, profile: dict | None = None) -> dict:
    """Audit the canonical TOC, title, and body ordering."""
    profile = profile or {}
    expected_title_style = profile.get("resolved_styles", {}).get("title", "文档标题")
    body_children = [
        child for child in doc.element.body.iterchildren()
        if child.tag in {qn("w:p"), qn("w:tbl")}
    ]
    records: list[dict[str, Any]] = []
    for index, child in enumerate(body_children):
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, doc)
            instruction = " ".join(node.text or "" for node in child.iter(qn("w:instrText")))
            style = paragraph.style.name if paragraph.style is not None else ""
            records.append({
                "index": index,
                "kind": "paragraph",
                "text": paragraph.text.strip(),
                "style": style,
                "has_toc_field": bool(re.search(r"\bTOC\b", instruction, re.I)),
                "has_page_break": any(br.get(qn("w:type")) == "page" for br in child.iter(qn("w:br"))),
            })
        else:
            records.append({"index": index, "kind": "table", "text": "", "style": ""})

    first = records[0] if records else None
    toc = records[1] if len(records) > 1 else None
    post_toc = next(
        (record for record in records[2:] if record.get("kind") == "table" or record.get("text")),
        None,
    )
    title_records = [
        record for record in records
        if record.get("kind") == "paragraph"
        and _normalized_style(record.get("style")) == _normalized_style(expected_title_style)
    ]
    issues: list[dict[str, Any]] = []
    if not first or first.get("text") not in {"目次", "目  次", "目录", "目  录"}:
        issues.append({"type": "toc_not_first"})
    if not toc or not toc.get("has_toc_field"):
        issues.append({"type": "toc_field_not_second"})
    elif not toc.get("has_page_break"):
        issues.append({"type": "toc_missing_page_break"})
    if not post_toc or post_toc.get("kind") != "paragraph":
        issues.append({"type": "title_missing_after_toc"})
    elif _normalized_style(post_toc.get("style")) != _normalized_style(expected_title_style):
        issues.append({
            "type": "first_content_after_toc_is_not_title",
            "style": post_toc.get("style"),
            "text": post_toc.get("text", "")[:120],
        })
    if len(title_records) != 1:
        issues.append({"type": "document_title_count", "count": len(title_records)})
    return {
        "toc_title_index": first.get("index") if first else None,
        "toc_field_index": toc.get("index") if toc else None,
        "document_title_index": post_toc.get("index") if post_toc else None,
        "document_title_text": post_toc.get("text") if post_toc else None,
        "document_title_style": post_toc.get("style") if post_toc else None,
        "document_title_count": len(title_records),
        "issues": issues,
        "passed": not issues,
    }
