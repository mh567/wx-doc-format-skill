from __future__ import annotations

import re

from table_formatting import audit_document_tables


def _style_has_numbering(doc, style_name: str) -> bool:
    """Return True if the style definition binds to a numbering definition."""
    try:
        for style in doc.styles:
            if style.name == style_name and style.type is not None:
                # Check if style has numPr in its XML definition
                pPr = style._element.find(
                    '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr'
                )
                if pPr is not None:
                    numPr = pPr.find(
                        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr'
                    )
                    if numPr is not None:
                        numId = numPr.find(
                            '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numId'
                        )
                        return numId is not None
                break
    except Exception:
        pass
    return False


def paragraph_num_id(paragraph) -> str | None:
    p_pr = paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None and p_pr.numPr is not None else None
    if num_pr is None or num_pr.numId is None or num_pr.numId.val is None:
        return None
    return str(num_pr.numId.val)


def _is_ordered_list_style(style_name: str) -> bool:
    """Recognize template and common Word ordered-list style names."""
    normalized = style_name.casefold().replace(" ", "")
    if normalized.startswith("listnumber"):
        return True
    if "列项" not in style_name:
        return False
    return "编号" in style_name or "有编号" in style_name


def num_has_start_override(doc, num_id: str, qn) -> bool:
    numbering = doc.part.numbering_part.element
    for num in numbering.findall(qn("w:num")):
        if num.get(qn("w:numId")) != num_id:
            continue
        start = num.find(".//" + qn("w:startOverride"))
        return start is not None and start.get(qn("w:val")) == "1"
    return False


def heading_hierarchy_warnings(heading_sequence: list[dict]) -> list[dict]:
    warnings = []
    if not heading_sequence:
        return warnings
    first = heading_sequence[0]
    first_level = int(first.get("level") or 0)
    if first_level > 1:
        warnings.append(
            {
                "type": "first_heading_below_level_one",
                "paragraph": first.get("paragraph"),
                "level": first_level,
                "text": first.get("text"),
            }
        )
    previous_level = first_level
    for heading in heading_sequence[1:]:
        level = int(heading.get("level") or 0)
        if previous_level and level > previous_level + 1:
            warnings.append(
                {
                    "type": "heading_level_jump",
                    "paragraph": heading.get("paragraph"),
                    "previous_level": previous_level,
                    "level": level,
                    "text": heading.get("text"),
                }
            )
        previous_level = level
    return warnings


def audit_document(
    doc,
    row_height_cm: float,
    row_height_rule: str,
    *,
    heading_level_from_style,
    paragraph_direct_num_info,
    existing_heading_number,
    looks_like_code_sample_table,
    qn,
    center_alignment,
    template_profile: dict | None = None,
    table_roles: list[str] | None = None,
) -> dict:
    audit = {
        "paragraph_count": len(doc.paragraphs),
        "table_count": len(doc.tables),
        "heading_sequence": [],
        "heading_hierarchy_warnings": [],
        "list_restart_groups": [],
        "table_paragraphs_not_table_body": [],
        "table_rows_bad_height": [],
        "table_cells_may_clip": [],
        "code_sample_table_alignment_issues": [],
        "markdown_residue": [],
        "heading_paragraphs_without_numbering": [],
        "heading_text_still_has_manual_number": [],
        "ordered_list_nums_without_restart": [],
    }
    seen_list_num_ids: set[str] = set()
    for idx, paragraph in enumerate(doc.paragraphs, 1):
        text = paragraph.text.strip()
        if not text:
            continue
        if "**" in text or re.match(r"^#{1,6}\s+", text):
            audit["markdown_residue"].append({"paragraph": idx, "text": text[:120]})
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if style_name.startswith("Heading"):
            heading_level = heading_level_from_style(style_name)
            _, num_id = paragraph_direct_num_info(paragraph)
            audit["heading_sequence"].append(
                {"paragraph": idx, "level": heading_level, "num_id": num_id, "text": text[:120]}
            )
            if num_id is None:
                # Style-bound numbering (Heading style → numId via template)
                # is the expected mechanism in the new architecture.
                if not _style_has_numbering(doc, style_name):
                    audit["heading_paragraphs_without_numbering"].append(
                        {"paragraph": idx, "style": style_name, "text": text[:120]}
                    )
            if existing_heading_number(text):
                audit["heading_text_still_has_manual_number"].append(
                    {"paragraph": idx, "style": style_name, "text": text[:120]}
                )
        if _is_ordered_list_style(style_name):
            num_id_str = paragraph_num_id(paragraph)
            if num_id_str is not None and num_id_str not in seen_list_num_ids:
                seen_list_num_ids.add(num_id_str)
                has_restart = num_has_start_override(doc, num_id_str, qn)
                audit["list_restart_groups"].append(
                    {
                        "paragraph": idx,
                        "style": style_name,
                        "num_id": num_id_str,
                        "restart_at_one": has_restart,
                        "text": text[:120],
                    }
                )
                if not has_restart:
                    audit["ordered_list_nums_without_restart"].append(
                        {"paragraph": idx, "style": style_name, "num_id": num_id_str, "text": text[:120]}
                    )
    for table_idx, table in enumerate(doc.tables, 1):
        is_code_sample = looks_like_code_sample_table(table)
        for row_idx, row in enumerate(table.rows, 1):
            if row.height is None or abs(row.height.cm - row_height_cm) > 0.02:
                audit["table_rows_bad_height"].append({"table": table_idx, "row": row_idx})
            for cell in row.cells:
                cell_text = "\n".join(paragraph.text.strip() for paragraph in cell.paragraphs if paragraph.text.strip())
                if row_height_rule == "exact" and len(cell_text) > 45:
                    audit["table_cells_may_clip"].append(
                        {"table": table_idx, "row": row_idx, "text": cell_text[:120]}
                    )
                for paragraph in cell.paragraphs:
                    if paragraph.text.strip() and paragraph.style.name != "表正文":
                        audit["table_paragraphs_not_table_body"].append(
                            {"table": table_idx, "row": row_idx, "style": paragraph.style.name, "text": paragraph.text[:80]}
                        )
                    if is_code_sample and paragraph.text.strip() and paragraph.alignment == center_alignment:
                        audit["code_sample_table_alignment_issues"].append(
                            {"table": table_idx, "row": row_idx, "text": paragraph.text[:120]}
                        )
    audit["heading_hierarchy_warnings"] = heading_hierarchy_warnings(audit["heading_sequence"])
    audit["table_format_contract"] = audit_document_tables(
        doc,
        template_profile,
        row_height_cm,
        row_height_rule,
        table_roles=table_roles,
    )
    return audit


def collect_content_warnings(doc: object) -> list[dict]:
    texts = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]
    joined = "\n".join(texts)
    warnings = []
    if any(text in {"目次", "目录"} for text in texts):
        warnings.append({"type": "toc", "message": "发现目录或目次，应确认目录层级缩进、页码和域更新状态。"})
    if "引用文件" in joined or "依据文件" in joined:
        warnings.append({"type": "references", "message": "发现引用文件或依据文件，应人工核对排序、标准号空格、正文引用对应关系。"})
    if "术语" in joined or "缩略语" in joined:
        warnings.append({"type": "terms", "message": "发现术语或缩略语章节，应人工核对术语定义和缩略语排序。"})
    if "公式" in joined or any(re.match(r"^\(?\d+\)?$", text) for text in texts):
        warnings.append({"type": "formula", "message": "发现公式或公式编号，应人工核对公式居中、编号右对齐和全文连续编号。"})
    if any(text.startswith("附录") or re.match(r"^（?(资料性|规范性)）?$", text) for text in texts):
        warnings.append({"type": "appendix", "message": "发现附录内容，应人工核对附录标题、附录编号、附录目录显示和页码。"})
    if any("图" in text and "注" in text for text in texts) or any("脚注" in text for text in texts):
        warnings.append({"type": "figure_table_notes", "message": "发现图注、表注或脚注相关内容，应人工核对其位置和注样式。"})
    return warnings
