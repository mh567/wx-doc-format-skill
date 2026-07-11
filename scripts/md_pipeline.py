from __future__ import annotations

import re
from typing import Callable

from text_utils import (
    clean_note_prefix,
    heading_level_from_text,
    is_appendix_title,
    is_formula_text,
    looks_like_list_item,
    list_kind_for_text,
    strip_heading_marker,
    strip_list_marker,
)
from document_model import (
    append_block,
    appendix_block,
    body_block,
    heading_block,
    list_item_block,
    new_document_model,
    source_record,
    table_block,
)


def is_md_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index].strip()
    next_line = lines[index + 1].strip()
    if not current or not next_line:
        return False
    has_pipe = "|" in current
    separator = next_line.replace(" ", "").replace("|", "-")
    is_separator = all(c == "-" for c in separator.strip("-"))
    return has_pipe and is_separator and "|" in next_line


def parse_md_table(lines: list[str], index: int) -> tuple[list[list[str]], int]:
    header = [c.strip() for c in lines[index].strip().strip("|").split("|")]
    rows = []
    index += 2
    while index < len(lines):
        line = lines[index].strip()
        if not line or not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
        index += 1
    return rows, index


def model_list_type_for_kind(kind: str) -> str:
    mapping = {
        "letter": "lower_letter_paren",
        "decimal": "decimal_paren",
        "dash": "dash",
        "bullet2": "bullet_dot",
    }
    return mapping.get(kind, "lower_letter_paren")


def md_rows_to_table_block_rows(rows: list[list[str]], table_type: str, header_rows: int) -> list[list[dict]]:
    result = []
    for ri, row in enumerate(rows):
        cells = []
        for text in row:
            if table_type == "code_sample":
                cell_role = "code"
            elif ri < header_rows:
                cell_role = "header"
            else:
                cell_role = "body"
            cells.append({"text": text.strip(), "cell_role": cell_role})
        result.append(cells)
    return result


def parse_md_to_model(
    src,
    report: dict,
    *,
    skill_version: Callable[[], str],
) -> dict:
    from pathlib import Path
    if isinstance(src, (str, Path)):
        src_path = Path(src)
        text_content = src_path.read_text(encoding="utf-8")
    else:
        text_content = src
        src_path = Path("inline.md")
    model = new_document_model(src_path, "markdown", skill_version())
    lines = text_content.split("\n")
    block_index = 1
    active_list_levels: set[int] = set()

    # Initialize parse_report for suspicion scoring
    parse_report = report.setdefault("parse_report", {})
    parse_report.setdefault("ambiguous_short_paragraphs", [])
    parse_report.setdefault("inferred_headings", [])
    parse_report.setdefault("inferred_lists", [])
    parse_report.setdefault("unstyled_paragraphs", 0)
    unstyled_count = 0

    def next_id() -> str:
        nonlocal block_index
        bid = f"b{block_index:04d}"
        block_index += 1
        return bid

    def reset_lists() -> None:
        active_list_levels.clear()

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if is_md_table_start(lines, index):
            rows, next_index = parse_md_table(lines, index)
            append_block(
                model,
                table_block(
                    next_id(),
                    "data",
                    md_rows_to_table_block_rows(rows, "data", 1),
                    header_rows=1,
                    source=source_record(format="markdown_table"),
                ),
            )
            reset_lists()
            report["tables_processed"] += 1
            index = next_index
            continue
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            md_level = len(match.group(1))
            text = match.group(2).strip()
            clean_text = strip_heading_marker(text)
            if md_level == 1:
                append_block(
                    model,
                    heading_block(
                        next_id(),
                        clean_text,
                        0,
                        role="title",
                        source=source_record(raw_text=text, format="md_heading", md_level=md_level),
                    ),
                )
            else:
                heading_level = min(md_level - 1, 5)
                append_block(
                    model,
                    heading_block(
                        next_id(),
                        clean_text,
                        heading_level,
                        source=source_record(raw_text=text, format="md_heading", md_level=md_level),
                    ),
                )
            reset_lists()
            index += 1
            continue
        inferred_level = heading_level_from_text(line)
        if inferred_level is not None:
            clean_text = strip_heading_marker(line)
            append_block(
                model,
                heading_block(
                    next_id(),
                    clean_text,
                    inferred_level,
                    source=source_record(raw_text=line, format="md_text_heading"),
                ),
            )
            report["inferred_headings"].append({"text": line, "level": inferred_level, "source": "md-text"})
            parse_report["inferred_headings"].append({"text": line, "level": inferred_level, "source": "md-text"})
            reset_lists()
            index += 1
            continue
        role = "note" if line.startswith(("**\u5907\u6ce8\uff1a**", "**\u7f16\u5199\u63d0\u793a\uff1a**", "\u5907\u6ce8\uff1a", "\u7f16\u5199\u63d0\u793a\uff1a")) else "body"
        if line.startswith(("\u6ce81\uff1a", "\u6ce82\uff1a", "\u6ce83\uff1a", "\u6ce84\uff1a", "\u6ce85\uff1a")):
            role = "numbered_note"
        elif is_formula_text(line):
            role = "formula"
        elif is_appendix_title(line):
            append_block(
                model,
                appendix_block(
                    next_id(),
                    line,
                    source=source_record(raw_text=line, role="appendix_title", format="md_text"),
                ),
            )
            reset_lists()
            index += 1
            continue
        if role == "body" and looks_like_list_item(line):
            kind = list_kind_for_text(line)
            lst_level = 1 if kind in {"decimal", "bullet2"} else 0
            restart = lst_level not in active_list_levels
            active_list_levels.add(lst_level)
            clean_text = strip_list_marker(line)
            append_block(
                model,
                list_item_block(
                    next_id(),
                    clean_text,
                    lst_level,
                    model_list_type_for_kind(kind),
                    restart=restart,
                    source=source_record(raw_text=line, format="md_list_item"),
                ),
            )
            report["inferred_lists"].append({"text": line, "source": "md-text"})
            parse_report["inferred_lists"].append({"text": line, "source": "md-text"})
        else:
            # Track ambiguous short paragraphs for suspicion scoring
            if role == "body" and len(line) <= 30 and not line[-1] in ("。", "；", ";", "，", ","):
                parse_report["ambiguous_short_paragraphs"].append(line)
            unstyled_count += 1
            append_block(
                model,
                body_block(
                    next_id(),
                    clean_note_prefix(line),
                    source=source_record(raw_text=line, role=role, format="md_text"),
                ),
            )
            reset_lists()
        index += 1

    # Record unstyled paragraph count for suspicion scoring
    parse_report["unstyled_paragraphs"] = unstyled_count

    return model
