"""Input-document review checks, one per template requirement.

Each check returns ``{"total_items": int, "issues": [...]}`` where ``total_items``
is the size of the surface the check examined.  A finding carries the same
``code`` / ``level`` / ``title`` / ``suggestion`` shape everywhere, so the
scoring engine and the report writers stay independent of which check produced
it.

The checks reuse the conversion's own owner of each rule wherever one exists:
the appendix contract from ``appendix_semantics``, the table format contract from
``table_formatting``, the note role from ``note_semantics``, and the heading
marker vocabulary from ``text_utils``.  A rule therefore cannot drift between
what the reviewer reports and what the converter does.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from docx import Document

from note_semantics import source_note_role
from table_formatting import audit_document_tables
from text_utils import strip_heading_marker_prefix
from toc_contract import TOC_MAX_LEVEL, TOC_TITLE_STYLE
from toc_detector import audit_toc_replacement
from wxdf.format_contract import audit_document_format
from wxdf.model import inline_text_projection
from wxdf.page_contract import audit_page_contract
from wxdf.review_suggestions import get_suggestion
from wxdf.semantics.appendices import collect_source_appendix_semantics
from wxdf.semantics.structure import collect_structure_candidates

MAX_LIST_LEVEL = 1
_APPENDIX_STYLES = ("附录标题", "附录一级标题", "附录二级标题", "附录三级标题")
# A figure caption in its strong form, a label followed by a number: "图1", "图 2.1".
_FIGURE_CAPTION = re.compile(r"^图\s*\d")


def _normalized(value: str | None) -> str:
    return (value or "").casefold().replace(" ", "")


def _is_allowed_style(style_name: str, allowed: set[str]) -> bool:
    normalized = _normalized(style_name)
    return any(normalized == _normalized(name) for name in allowed)


def _document_style_records(doc: Document) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for paragraph in doc.paragraphs:
        style_name = paragraph.style.name if paragraph.style else "Normal"
        records.append((style_name or "Normal", paragraph.text.strip()))
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    style_name = paragraph.style.name if paragraph.style else "Normal"
                    records.append((style_name or "Normal", paragraph.text.strip()))
    return records


def allowed_template_styles(template_profile: Mapping[str, Any]) -> set[str]:
    """Return every style name the template defines.

    The template's ``styles`` map is the authority, not the smaller set of role
    bindings: a style the template ships is a legal choice even when no
    conversion role consumes it, so only a style the template does not define is
    a finding.
    """

    allowed: set[str] = {"Normal"}
    styles = template_profile.get("styles", {})
    if isinstance(styles, Mapping):
        allowed.update(str(name) for name in styles)
    resolved = template_profile.get("resolved_styles", {})
    if isinstance(resolved, Mapping):
        allowed.update(str(name) for name in resolved.values() if name)
    allowed.update(f"TOC {level}" for level in range(1, TOC_MAX_LEVEL + 1))
    allowed.add(TOC_TITLE_STYLE)
    allowed.update(_APPENDIX_STYLES)
    return allowed


def check_styles(doc: Document, template_profile: Mapping[str, Any]) -> dict[str, Any]:
    allowed = allowed_template_styles(template_profile)
    issues: list[dict[str, Any]] = []
    used_styles: dict[str, str] = {}
    for style_name, text in _document_style_records(doc):
        used_styles.setdefault(style_name, text)
    for style_name, text in used_styles.items():
        if _is_allowed_style(style_name, allowed):
            continue
        issues.append({
            "code": "missing_template_style",
            "level": "high",
            "location": f"样式「{style_name}」，示例：{text[:40] or '（空段落）'}",
            "actual_style": style_name,
            "allowed_styles": sorted(allowed),
            **get_suggestion(
                "missing_template_style",
                allowed_styles="、".join(sorted(allowed)[:8]),
            ),
        })
    return {"total_items": max(len(used_styles), 1), "issues": issues}


def heading_records(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return ``(level, text, node_id)`` for every heading the source declares.

    The Source AST alone leaves paragraphs at their parser-assigned role, so the
    headings are read from the structure candidates, which resolve a declared
    heading style into a ``heading`` role with a level.
    """

    records: list[dict[str, Any]] = []
    for candidate in collect_structure_candidates(facts):
        proposal = candidate.get("proposal") if isinstance(candidate, Mapping) else None
        value = proposal.get("value") if isinstance(proposal, Mapping) else None
        if not isinstance(value, Mapping) or value.get("role") != "heading":
            continue
        level = value.get("level")
        if not isinstance(level, int):
            continue
        node_ids = candidate.get("target_node_ids") or []
        node_id = str(node_ids[0]) if node_ids else ""
        unit = facts.get("units", {}).get(node_id) or {}
        text = inline_text_projection(unit.get("inline_content", [])).strip()
        records.append({"level": level, "text": text, "node_id": node_id})
    return records


def check_headings(facts: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    records = heading_records(facts)
    previous = 0
    for record in records:
        level = record["level"]
        text = str(record.get("text") or "")
        if previous and level > min(6, previous + 1):
            expected = min(6, previous + 1)
            issues.append({
                "code": "heading_level_jump",
                "level": "high",
                "location": f"标题「{text[:40]}」",
                "from_level": previous,
                "to_level": level,
                "missing_level": expected,
                **get_suggestion(
                    "heading_level_jump",
                    from_level=previous,
                    to_level=level,
                    missing_level=expected,
                ),
            })
        if previous or level:
            previous = level
        if strip_heading_marker_prefix(text) != text:
            issues.append({
                "code": "heading_manual_number",
                "level": "low",
                "location": f"标题「{text[:40]}」",
                "text": text[:40],
                **get_suggestion("heading_manual_number", text=text[:40]),
            })
    return {"total_items": max(len(records), 1), "issues": issues}


def check_lists(facts: Mapping[str, Any], list_analysis: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    items = list(list_analysis.get("items") or [])
    seen_depths: set[int] = set()
    for item in items:
        level = item.get("semantic_level")
        if isinstance(level, int) and level > MAX_LIST_LEVEL and level not in seen_depths:
            seen_depths.add(level)
            marker = str(item.get("source_marker") or "")
            issues.append({
                "code": "list_nested_too_deep",
                "level": "medium",
                "location": f"列表项「{marker}」（第 {level + 1} 层）",
                "actual_depth": level + 1,
                **get_suggestion("list_nested_too_deep", actual_depth=level + 1),
            })

    heading_ids = {record["node_id"] for record in heading_records(facts)}
    evidence = facts.get("evidence", {})
    kind_by_item = {str(item.get("item_id")): item for item in items}
    after_heading = True
    reported_sequences: set[str] = set()
    for node_id in facts.get("children", {}).get("root", []):
        node_id = str(node_id)
        if node_id in heading_ids:
            after_heading = True
            continue
        item = kind_by_item.get(node_id)
        if item is None or item.get("list_kind") != "ordered":
            continue
        node_evidence = evidence.get(node_id) or {}
        visible = "visible_list_marker" in (node_evidence.get("numbering_evidence") or [])
        ordinal = node_evidence.get("ordinal")
        sequence = str(item.get("source_sequence_id") or node_id)
        if (
            after_heading
            and visible
            and isinstance(ordinal, int)
            and ordinal > 1
            and sequence not in reported_sequences
        ):
            reported_sequences.add(sequence)
            marker = str(item.get("source_marker") or "")
            issues.append({
                "code": "list_numbering_not_restarted",
                "level": "medium",
                "location": f"标题后的首个有序列表项「{marker}」",
                "actual_ordinal": ordinal,
                **get_suggestion("list_numbering_not_restarted", actual_ordinal=ordinal),
            })
        after_heading = False
    return {"total_items": max(len(items), 1), "issues": issues}


def check_tables(
    facts: Mapping[str, Any],
    table_semantics: list[Mapping[str, Any]],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for index, table in enumerate(table_semantics):
        if table.get("table_type") != "data":
            continue
        node_id = str(table.get("node_id"))
        cell_styles = _table_cell_styles(facts, node_id)
        if cell_styles and any(style and not _is_table_body(style) for style in cell_styles):
            wrong = sorted({style for style in cell_styles if style and not _is_table_body(style)})
            issues.append({
                "code": "table_cell_style",
                "level": "medium",
                "location": f"表格 {index + 1}",
                "actual_styles": wrong,
                **get_suggestion("table_cell_style", actual_styles="、".join(wrong)),
            })
    return {"total_items": max(len(table_semantics), 1), "issues": issues}


def _table_cell_styles(facts: Mapping[str, Any], table_id: str) -> list[str]:
    styles: list[str] = []
    for row_id in facts.get("children", {}).get(table_id, []):
        for cell_id in facts.get("children", {}).get(row_id, []):
            for child_id in facts.get("children", {}).get(cell_id, []):
                unit = facts.get("units", {}).get(child_id)
                if isinstance(unit, Mapping) and unit.get("kind") == "paragraph":
                    styles.append(str(unit.get("style") or ""))
    return styles


def _is_table_body(style_name: str) -> bool:
    return "表正文" in style_name or _normalized(style_name) in {"normal", "tablegrid"}


def check_table_contract(doc: Document, template_profile: Mapping[str, Any], roles: list[str]) -> dict[str, Any]:
    """Check a table against the template's table format contract.

    The contract is a template requirement like any other, so a deviation is
    reported at the requirement's own severity.  It is not softened on the
    grounds that some later step would rewrite it: the review judges the
    document in front of it, independently of any pipeline.
    """

    audit = audit_document_tables(doc, dict(template_profile), 0.7, "at-least", table_roles=roles)
    issues: list[dict[str, Any]] = []
    labels = {
        "table_layout_issues": ("table_layout", "表格布局未按模板自动适应"),
        "table_width_issues": ("table_width", "表格宽度与模板不一致"),
        "table_default_cell_margin_issues": ("table_default_cell_margin", "表格默认单元格边距与模板不一致"),
        "cell_margin_issues": ("table_cell_margin", "单元格设置了独立边距"),
        "paragraph_alignment_issues": ("table_paragraph_alignment", "单元格段落对齐与模板不一致"),
        "paragraph_direct_format_issues": ("table_paragraph_direct_format", "单元格段落存在直接格式"),
        "run_direct_format_issues": ("table_run_direct_format", "单元格文本存在直接格式"),
        "table_body_style_spacing_issues": ("table_body_style_spacing", "表正文样式行距未按模板设置"),
        "cell_vertical_alignment_issues": ("table_cell_vertical_alignment", "单元格垂直对齐与模板不一致"),
        "row_height_rule_issues": ("table_row_height_rule", "表格行高规则与模板不一致"),
        "row_height_issues": ("table_row_height", "表格行高与模板不一致"),
        "paragraph_style_issues": ("table_paragraph_style", "单元格段落样式与模板不一致"),
        "invalid_table_styles": ("table_invalid_style", "表格使用了模板未定义的表格样式"),
    }
    for key, (code, title) in labels.items():
        entries = audit.get(key) or []
        if not entries:
            continue
        issues.append({
            "code": code,
            "level": "medium",
            "title": title,
            "location": f"表格格式合同（{len(entries)} 处）",
            "suggestion": f"{title}。请在表格属性中按模板设置",
        })
    return {"total_items": max(len(doc.tables), 1), "issues": issues}


def check_format_conformance(
    doc: Document,
    template_doc: Document,
    template_profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare the source document's element formatting with the template.

    The shared format contract resolves each element's effective formatting (the
    style chain, its numbering level, and any direct property) and compares it
    with the value the template's own style resolves to.  The raw findings are one
    per property and paragraph, which is precise but far too granular for a
    reader, so they are grouped here into one issue per role and property with the
    affected paragraphs counted and sampled.
    """

    detail = audit_document_format(doc, template_profile, template_doc, consumer="review")
    checked = max(int(detail.get("checked") or 0), 1)

    mismatches: dict[str, dict[str, dict[str, Any]]] = {}
    affected: dict[tuple[str, str], set[str]] = {}
    for finding in detail["issues"]:
        code = str(finding.get("code"))
        role = str(finding.get("role") or "element")
        path = str(finding.get("path") or "")
        affected.setdefault((code, role), set()).add(path)
        if code == "format_value_mismatch":
            mismatches.setdefault(role, {}).setdefault(str(finding.get("property")), finding)

    issues: list[dict[str, Any]] = []
    for role, properties in mismatches.items():
        role_label = _ROLE_LABELS.get(role, role)
        parts = [
            f"{prop} 应为 {_display(finding.get('expected'))}、实际 {_display(finding.get('actual'))}"
            f"（来源：{_SOURCE_LABELS.get(str(finding.get('source')), str(finding.get('source')))}）"
            for prop, finding in properties.items()
        ]
        count = len(affected.get(("format_value_mismatch", role), ()))
        issues.append({
            **get_suggestion("format_value_mismatch", role=role_label, property="、".join(parts[:4])),
            "code": "format_value_mismatch",
            "level": _severity_for_ratio(count / checked),
            "location": f"{role_label}（{count} 段，{len(parts)} 项属性）",
            "role": role,
            "count": count,
            "properties": sorted(properties),
        })
    for (code, role), paths in affected.items():
        if code == "format_value_mismatch":
            continue
        role_label = _ROLE_LABELS.get(role, role)
        template = "format_run_direct_override" if code == "format_run_direct_override" else "format_unexpected_direct"
        label = "文本直接格式" if code == "format_run_direct_override" else "多余直接格式"
        issues.append({
            **get_suggestion(template),
            "code": code,
            "level": _severity_for_ratio(len(paths) / checked),
            "location": f"{role_label} · {label}（{len(paths)} 段）",
            "role": role,
            "count": len(paths),
        })
    return {"total_items": checked, "issues": issues}


def _severity_for_ratio(ratio: float) -> str:
    """Map the share of affected elements to a severity."""

    if ratio >= 0.5:
        return "critical"
    if ratio >= 0.2:
        return "high"
    if ratio >= 0.05:
        return "medium"
    return "low"


_ROLE_LABELS = {
    "body": "正文",
    "title": "文档标题",
    "caption": "图表标题",
    "table_body": "表正文",
    "note": "注",
    "numbered_note": "有编号注",
    "list_letter": "一级列项",
    "list_decimal": "二级列项",
    "list_dash": "一级无编号列项",
    "list_bullet": "二级无编号列项",
    "appendix_title": "附录标题",
    "formula": "公式",
    **{f"heading_{level}": f"标题{level}" for level in range(1, 7)},
    **{f"appendix_heading_{level}": f"附录{level}级标题" for level in range(1, 4)},
}

_SOURCE_LABELS = {
    "direct": "段落直接格式",
    "numbering": "编号级别",
    "docDefaults": "文档默认格式",
    "unset": "未定义",
}


def _display(value: Any) -> str:
    if value is None:
        return "未定义"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def check_page_numbering(doc: Document, template_doc: Document) -> dict[str, Any]:
    """Check the document's page, formula and caption numbering.

    These three requirements are properties of the page setup and of the
    numbering running through the document rather than of one paragraph's
    formatting, so they live in their own contract and are summarised here into
    one issue per finding code.
    """

    detail = audit_page_contract(doc, template_doc)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in detail["issues"]:
        grouped.setdefault(str(finding.get("code")), []).append(finding)

    issues: list[dict[str, Any]] = []
    for code, findings in grouped.items():
        first = findings[0]
        template = _PAGE_SUGGESTIONS.get(code)
        if template is None:
            continue
        suggestion = get_suggestion(template, **_page_suggestion_args(code, first))
        issues.append({
            **suggestion,
            "code": code,
            "level": str(first.get("level") or "medium"),
            "location": str(first.get("location") or "页面与编号"),
            "count": len(findings),
        })
    return {"total_items": max(int(detail.get("checked") or 0), 1), "issues": issues}


_PAGE_SUGGESTIONS = {
    "page_section_count_mismatch": "page_section_count_mismatch",
    "page_numbering_mismatch": "page_numbering_mismatch",
    "page_footer_missing": "page_footer_missing",
    "formula_numbering_broken": "formula_numbering_broken",
    "formula_not_centered": "formula_not_centered",
    "formula_number_not_right": "formula_number_not_right",
    "sequence_numbering_inconsistent": "sequence_numbering_inconsistent",
}

_PAGE_FMT_LABELS = {
    "upperRoman": "罗马数字",
    "lowerRoman": "小写罗马数字",
    "decimal": "阿拉伯数字",
    "upperLetter": "大写字母",
    "lowerLetter": "小写字母",
}


def _page_suggestion_args(code: str, finding: Mapping[str, Any]) -> dict[str, Any]:
    if code == "page_section_count_mismatch":
        return {
            "expected_count": finding.get("expected_count"),
            "actual_count": finding.get("actual_count"),
            "expected_formats": "、".join(
                _PAGE_FMT_LABELS.get(str(value), str(value)) for value in (finding.get("expected") or [])
            ),
        }
    if code == "page_numbering_mismatch":
        return {
            "section": finding.get("location"),
            "expected": _PAGE_FMT_LABELS.get(str(finding.get("expected")), str(finding.get("expected"))),
            "actual": _PAGE_FMT_LABELS.get(str(finding.get("actual")), str(finding.get("actual"))),
        }
    if code == "formula_numbering_broken":
        return {
            "expected": "、".join(str(value) for value in (finding.get("expected") or [])),
            "actual": "、".join(str(value) for value in (finding.get("actual") or [])),
        }
    if code == "sequence_numbering_inconsistent":
        schemes = finding.get("schemes") or {}
        return {
            "schemes": "；".join(f"{name}（如 {sample}）" for name, sample in schemes.items()),
        }
    return {}


def check_toc(doc: Document) -> dict[str, Any]:
    """Check the source table of contents against the template contract.

    The template fixes the TOC title text, the title style, the TOC 1-4 entry
    styles and the outline depth the field covers, so a source that ships its
    own TOC is checked against exactly those.  A source with no TOC at all is
    reported too: the template requires one, even though the converter inserts
    it.
    """

    audit = audit_toc_replacement(doc, None)
    issues: list[dict[str, Any]] = []
    if not audit.get("toc_field_count") and not _has_toc_title(doc):
        issues.append({
            "code": "toc_missing",
            "level": "medium",
            "location": "文档目录",
            **get_suggestion("toc_missing"),
        })
        return {"total_items": 1, "issues": issues}

    title_issues = list(audit.get("title_issues") or [])
    if title_issues:
        actual = next(
            (entry.get("actual") for entry in title_issues if entry.get("type") == "title_style"),
            None,
        )
        issues.append({
            "code": "toc_title_style",
            "level": "medium",
            "location": "目录标题",
            "expected_style": TOC_TITLE_STYLE,
            "actual_style": actual,
            **get_suggestion("toc_title_style", expected_style=TOC_TITLE_STYLE, actual_style=actual or "未定义"),
        })

    entry_issues = list(audit.get("toc_style_issues") or [])
    if entry_issues:
        kinds = "、".join(sorted({str(entry.get("type")) for entry in entry_issues}))
        issues.append({
            "code": "toc_entry_style",
            "level": "medium",
            "location": f"目录条目样式（{len(entry_issues)} 处）",
            "issue_types": sorted({str(entry.get("type")) for entry in entry_issues}),
            **get_suggestion("toc_entry_style", issue_types=kinds),
        })

    for entry in audit.get("field_level_issues") or []:
        actual = entry.get("actual_max_level")
        expected = entry.get("expected_max_level")
        issues.append({
            "code": "toc_level_too_deep",
            "level": "low",
            "location": "目录域",
            "actual_level": actual,
            "expected_level": expected,
            **get_suggestion("toc_level_too_deep", actual_level=actual, expected_level=expected),
        })

    if audit.get("toc_field_count") and not audit.get("update_fields_on_open"):
        issues.append({
            "code": "toc_update_fields",
            "level": "low",
            "location": "目录域",
            **get_suggestion("toc_update_fields"),
        })

    return {"total_items": max(int(audit.get("toc_field_count") or 0), 1), "issues": issues}


def _has_toc_title(doc: Document) -> bool:
    from toc_detector import _TOC_TITLES

    return any(paragraph.text.strip() in _TOC_TITLES for paragraph in doc.paragraphs)


def check_appendix(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Check the appendix the source declares.

    The appendix contract the converter audits is expressed on a document
    rendered from the template, so its "the appendix styles exist" checks are
    wrong for an arbitrary source that has no appendix at all.  This check reads
    the source appendix semantics instead: content the appendix detector
    accepted is fine, and content that looked like an appendix but was rejected
    is reported, because the author's intent is ambiguous there.
    """

    semantics = collect_source_appendix_semantics(facts)
    issues: list[dict[str, Any]] = []
    ranges = list(semantics.get("ranges") or [])
    for entry in semantics.get("issues") or []:
        issues.append({
            "code": str(entry.get("code") or "appendix_structure_invalid"),
            "level": "medium",
            "location": f"附录位置 {entry.get('location', '')}",
            **get_suggestion("appendix_structure_invalid"),
        })
    for candidate in semantics.get("candidates") or []:
        if candidate.get("accepted"):
            continue
        negative = candidate.get("negative_evidence") or []
        # ``nested_main_numbered_heading`` is the documented negative evidence
        # for a main-body heading that merely says 附录, so it stays unreported.
        if negative and set(negative) <= {"nested_main_numbered_heading"}:
            continue
        issues.append({
            "code": "appendix_ambiguous",
            "level": "medium",
            "location": "疑似附录",
            **get_suggestion("appendix_structure_invalid"),
        })
    return {"total_items": max(len(ranges), 1), "issues": issues}


def _figure_note_position_issues(doc: Document) -> list[dict[str, Any]]:
    """Report a figure's note that sits below the figure's caption.

    The spec puts a figure's note and footnote above the figure title: the order
    is image, notes, caption.  A note placed after the caption is therefore the
    wrong side of it.  Only a note directly below the caption is reported, since
    a note further down is no longer clearly that figure's.
    """

    paragraphs = list(doc.paragraphs)
    issues: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs):
        if _FIGURE_CAPTION.match(paragraph.text.strip()) is None:
            continue
        cursor = index + 1
        while cursor < len(paragraphs) and not paragraphs[cursor].text.strip():
            cursor += 1
        if cursor >= len(paragraphs):
            continue
        note = paragraphs[cursor]
        text = note.text.strip()
        style_name = note.style.name if note.style is not None else ""
        if source_note_role(style_name, None, text) is None:
            continue
        issues.append({
            "code": "figure_note_below_caption",
            "level": "medium",
            "location": f"图题「{paragraph.text.strip()[:30]}」下方",
            "note_text": text[:30],
            **get_suggestion("figure_note_below_caption"),
        })
    return issues


def _caption_measurement(
    table_semantics: list[Mapping[str, Any]],
    caption_semantics: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], set[str], str | None]:
    """Return the caption objects, the captioned targets and a failure reason.

    The check reads the caption semantics module's published result: the object
    inventory it resolved against and the captions it associated with them.  The
    inventory is the denominator, so its absence means the measurement could not
    be made.  It must not read as "this document has no captions": that reading
    turns a missing key into a finding against the document.

    The table half of the inventory is cross-checked against ``table_semantics``,
    the eligibility list the producer itself resolves against, so an inventory
    that lost or gained an eligible table is caught even when the key is present.
    That is an internal-consistency guard, not an independent measurement: both
    sides read the same table result.  The figure half has no such guard, since
    no other module publishes a figure inventory.  A producer that dropped a
    figure therefore costs a missed finding rather than a false one, and the
    element-level checks in ``contracts.validate_capability_payload`` are what
    stand behind the figure side.
    """

    inventory = caption_semantics.get("objects")
    if not isinstance(inventory, list):
        return [], set(), "caption_inventory_missing"
    captions = caption_semantics.get("captions")
    if not isinstance(captions, list):
        return [], set(), "caption_inventory_missing"
    eligible_tables = {
        str(table.get("node_id"))
        for table in table_semantics
        if table.get("caption_eligible")
    }
    inventory_tables = {
        str(obj.get("node_id"))
        for obj in inventory
        if isinstance(obj, Mapping) and obj.get("kind") == "table"
    }
    if inventory_tables != eligible_tables:
        return [], set(), "caption_inventory_incomplete"
    targets = {
        str(caption.get("target_node_id"))
        for caption in captions
        if isinstance(caption, Mapping)
        and caption.get("status") == "associated"
        and caption.get("target_node_id")
    }
    return list(inventory), targets, None


def check_captions(
    doc: Document,
    table_semantics: list[Mapping[str, Any]],
    caption_semantics: Mapping[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    issues.extend(_figure_note_position_issues(doc))
    objects, targets, failure = _caption_measurement(table_semantics, caption_semantics)
    if failure is not None:
        # The dimension cannot be judged, so it states no verdict at all.  The
        # caller drops it from the score and reports the reason, the way an
        # absent AI dimension is left out rather than counted as a clean pass.
        return {
            "total_items": 1,
            "issues": issues,
            "unmeasured": True,
            "unmeasured_reason": failure,
        }
    total = 0
    for index, table in enumerate(table_semantics):
        if not table.get("caption_eligible"):
            continue
        total += 1
        if str(table.get("node_id")) not in targets:
            issues.append({
                "code": "table_missing_caption",
                "level": "medium",
                "location": f"表格 {index + 1}（{table.get('table_type')}）",
                **get_suggestion("table_missing_caption"),
            })
    figures = [obj for obj in objects if obj.get("kind") == "figure"]
    for index, obj in enumerate(figures):
        total += 1
        if str(obj.get("node_id")) not in targets:
            issues.append({
                "code": "figure_missing_caption",
                "level": "medium",
                "location": f"图片 {index + 1}（{obj.get('location', '')}）",
                **get_suggestion("figure_missing_caption"),
            })
    text_by_node = {
        str(caption.get("node_id")): str(caption.get("text") or "")
        for caption in (caption_semantics.get("captions") or [])
        if isinstance(caption, Mapping)
    }
    for record in caption_semantics.get("issues") or []:
        if not isinstance(record, Mapping):
            continue
        code = str(record.get("code") or "")
        if code != "caption_target_missing":
            continue
        text = text_by_node.get(str(record.get("node_id")), "")
        issues.append({
            "code": code,
            "location": f"题注「{text}」" if text else "题注",
            **get_suggestion(code),
        })
    return {"total_items": max(total, 1), "issues": issues}


def check_notes(doc: Document, template_profile: Mapping[str, Any]) -> dict[str, Any]:
    resolved = template_profile.get("resolved_styles", {})
    note_styles = {
        _normalized(resolved.get("note", "3.1注-无编号注")),
        _normalized(resolved.get("numbered_note", "3.2注-有编号注")),
    }
    issues: list[dict[str, Any]] = []
    total = 0
    for paragraph in doc.paragraphs:
        style_name = paragraph.style.name if paragraph.style else ""
        text = paragraph.text.strip()
        role = source_note_role(style_name, None, text)
        if role is None:
            continue
        total += 1
        if _normalized(style_name) not in note_styles:
            issues.append({
                "code": "note_style_missing",
                "level": "medium",
                "location": f"注段落「{text[:30]}」",
                "actual_style": style_name,
                **get_suggestion("note_style_missing"),
            })
    return {"total_items": max(total, 1), "issues": issues}
