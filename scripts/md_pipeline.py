from __future__ import annotations

import re
import hashlib
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import Any, Callable

from document_model import (
    append_block, appendix_block, body_block, caption_block, heading_block,
    image_block, list_item_block, new_document_model, source_record, table_block,
)
from list_group_detection import annotate_semantic_list_groups
from text_utils import caption_parts, clean_note_prefix, heading_level_from_text, is_appendix_title, is_formula_text, strip_heading_marker
from unordered_lists import annotate_unordered_candidates
from appendix_semantics import parse_appendix_title


def model_list_type_for_kind(kind: str) -> str:
    """Map a legacy text-marker kind onto the stable AST list type name."""
    mapping = {
        "letter": "lower_letter_paren",
        "decimal": "decimal_paren",
        "dash": "dash",
        "bullet2": "bullet_dot",
    }
    return mapping.get(kind, "lower_letter_paren")


def _markdown_parser():
    """Build the Markdown block and inline tokenizer only for Markdown input."""
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise RuntimeError(
            "Markdown input requires markdown-it-py. Install the project dependencies first."
        ) from error
    return MarkdownIt("commonmark", {"html": False}).enable("table")


def _read_markdown_source(src) -> tuple[Path, str]:
    if isinstance(src, Path):
        return src, src.read_text(encoding="utf-8")
    if hasattr(src, "read"):
        text = src.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        return Path("inline.md"), str(text)
    if isinstance(src, str):
        candidate = Path(src)
        if "\n" not in src:
            try:
                if candidate.exists():
                    return candidate, candidate.read_text(encoding="utf-8")
            except OSError:
                pass
        return Path("inline.md"), src
    return Path("inline.md"), str(src)


def _inline_text(token, parser) -> str:
    inline_tokens = token.children or parser.parseInline(token.content)[0].children or []
    fragments: list[str] = []
    links: list[str] = []
    for inline in inline_tokens:
        if inline.type in {"text", "code_inline", "html_inline"}:
            fragments.append(inline.content)
        elif inline.type == "softbreak":
            fragments.append(" ")
        elif inline.type == "hardbreak":
            fragments.append("\n")
        elif inline.type == "link_open":
            links.append(str(inline.attrGet("href") or ""))
        elif inline.type == "link_close" and links:
            href = links.pop()
            if href:
                fragments.append(f" ({href})")
        elif inline.type == "image":
            target = str(inline.attrGet("src") or "")
            fragments.append(f"图片：{inline.content}" + (f" ({target})" if target else ""))
    return "".join(fragments).strip()


def _standalone_image(token, parser) -> tuple[str, str] | None:
    children = token.children or parser.parseInline(token.content)[0].children or []
    meaningful = [child for child in children if child.type not in {"softbreak", "hardbreak"}]
    if len(meaningful) != 1 or meaningful[0].type != "image":
        return None
    image = meaningful[0]
    return image.content.strip(), str(image.attrGet("src") or "").strip()


def _source_text(lines: list[str], token) -> str:
    if token.map is None:
        return ""
    start, end = token.map
    return "\n".join(lines[start:end]).strip()


def _asset_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _local_image_path(source_path: Path, target: str) -> Path | None:
    parsed = urlsplit(target)
    if parsed.scheme and parsed.scheme.casefold() != "file":
        return None
    if parsed.scheme.casefold() == "file" and parsed.netloc not in {"", "localhost"}:
        return None
    decoded_path = unquote(parsed.path)
    path = Path(decoded_path)
    if not path.is_absolute():
        path = source_path.parent / path
    return path.resolve()


def _body_or_semantic_block(block_id: str, text: str, *, source: dict[str, Any]):
    role = "note" if text.startswith(("备注：", "编写提示：")) else "body"
    if text.startswith(("注1：", "注2：", "注3：", "注4：", "注5：")):
        role = "numbered_note"
    if is_formula_text(text):
        role = "formula"
    if is_appendix_title(text):
        return appendix_block(block_id, text, source=source_record(**source, role="appendix_title"))
    caption_type, label, raw_number, caption_text = caption_parts(text)
    if caption_type != "unknown":
        return caption_block(block_id, caption_text, caption_type, label=label, raw_number=raw_number, source=source)
    inferred_level = heading_level_from_text(text)
    if inferred_level is not None:
        return heading_block(block_id, strip_heading_marker(text), inferred_level, source=source_record(**source, inferred=True))
    return body_block(block_id, clean_note_prefix(text), role=role if role != "body" else None, source=source)


def _markdown_source_audit() -> dict[str, Any]:
    return {
        "adapter": "markdown-it-py", "title_count": 0, "heading_count": 0,
        "list_items": 0, "table_count": 0, "table_rows": 0,
        "table_header_rows": 0, "fence_blocks": 0, "blockquotes": 0,
        "inline_code": 0, "task_list_items": 0, "token_inventory": {},
        "unsupported_tokens": [],
    }


def parse_md_to_model(src, report: dict, *, skill_version: Callable[[], str]) -> dict:
    """Adapt Markdown tokens into the shared source ``DocumentModel`` contract."""
    src_path, text_content = _read_markdown_source(src)
    parser = _markdown_parser()
    tokens = parser.parse(text_content)
    lines = text_content.splitlines()
    model = new_document_model(src_path, "markdown", skill_version())
    block_index = 1
    title_seen = False
    heading_level: int | None = None
    quote_depth = 0
    list_stack: list[dict[str, Any]] = []
    list_item_stack: list[dict[str, Any]] = []
    table: dict[str, Any] | None = None
    appendix_count = 0
    current_row: list[dict[str, str]] | None = None
    current_cell_role: str | None = None
    source_audit = _markdown_source_audit()
    parse_report = report.setdefault("parse_report", {})
    for key, value in {
        "ambiguous_short_paragraphs": [], "inferred_headings": [],
        "inferred_lists": [], "unstyled_paragraphs": 0,
    }.items():
        parse_report.setdefault(key, value)

    def next_id() -> str:
        nonlocal block_index
        value = f"b{block_index:04d}"
        block_index += 1
        return value

    def line_number(token) -> int | None:
        return token.map[0] + 1 if token.map is not None else None

    def append_text_block(token, text: str, *, role: str | None = None) -> None:
        nonlocal appendix_count
        text = text.strip()
        if not text:
            return
        source = source_record(
            raw_text=_source_text(lines, token) or text,
            format="markdown_quote" if quote_depth else "markdown_paragraph",
            line_start=line_number(token), role=role, semantic_origin="markdown_token",
            container_quote_depth=quote_depth or None,
        )
        if role in {"quote", "code_block"}:
            append_block(model, body_block(next_id(), text, role=role, source=source))
            return
        block = _body_or_semantic_block(next_id(), text, source=source)
        if block.get("block_type") == "appendix":
            appendix_count += 1
            title_data = parse_appendix_title(text, appendix_count)
            block.update({
                "appendix_id": title_data["appendix_id"],
                "title": title_data["title"],
                "title_lines": title_data["title_lines"],
                "soft_break_count": max(0, len(title_data["title_lines"]) - 1),
            })
            if title_data.get("classification"):
                block["classification"] = title_data["classification"]
        if block.get("block_type") == "heading":
            source_audit["heading_count"] += 1
            parse_report["inferred_headings"].append({"text": text, "level": block["level"], "source": "md-text"})
        elif block.get("block_type") == "body":
            parse_report["unstyled_paragraphs"] += 1
            if len(text) <= 30 and text[-1:] not in ("。", "；", ";", "，", ","):
                parse_report["ambiguous_short_paragraphs"].append(text)
        append_block(model, block)

    def append_list_item(item: dict[str, Any], text: str) -> None:
        if not text:
            return
        existing = item.get("block")
        if existing is not None:
            existing["text"] = f"{existing['text']} {text}".strip()
            return
        list_info = item["list"]
        list_info["items"] += 1
        level = int(list_info["level"])
        ordered = bool(list_info["ordered"])
        source_type = "decimal_paren" if ordered else ("dash" if list_info["marker"] == "-" else "bullet_dot")
        task_match = re.match(r"^\[(?P<state>[ xX])\]\s*", text)
        task_state = task_match.group("state").casefold() if task_match else None
        if task_match:
            source_audit["task_list_items"] += 1
            source_audit["unsupported_tokens"].append({"type": "task_list_state", "line_start": line_number(item["token"])})
        block = list_item_block(
            next_id(), text, level, "decimal_paren" if ordered else source_type,
            restart=list_info["items"] == 1,
            source=source_record(
                raw_text=_source_text(lines, item["token"]) or text, format="markdown_list_item", line_start=line_number(item["token"]),
                semantic_origin="markdown_token", container_quote_depth=quote_depth or None,
                task_state=task_state,
                numbering={"status": "detected", "ilvl": level, "restart": list_info["items"] == 1, "source_list_type": source_type, "list_type": "decimal_paren" if ordered else source_type, "family": "ordered" if ordered else "unordered", "marker": list_info["marker"]},
            ),
        )
        append_block(model, block)
        if level > 0:
            try:
                parent = list_item_stack[-2].get("block")
            except IndexError:
                parent = None
            if parent is not None:
                block["parent_list_item_id"] = parent.get("id")
        item["block"] = block
        source_audit["list_items"] += 1
        item_data = {"text": text, "source": "markdown-token"}
        report["inferred_lists"].append(item_data)
        parse_report["inferred_lists"].append(item_data)

    for token in tokens:
        token_type = token.type
        source_audit["token_inventory"][token_type] = source_audit["token_inventory"].get(token_type, 0) + 1
        if token_type == "heading_open":
            heading_level = int(token.tag[1:])
        elif token_type == "heading_close":
            heading_level = None
        elif token_type == "blockquote_open":
            quote_depth += 1
            source_audit["blockquotes"] += 1
        elif token_type == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
        elif token_type in {"ordered_list_open", "bullet_list_open"}:
            list_stack.append({
                "level": len(list_stack), "ordered": token_type == "ordered_list_open",
                "marker": token.markup, "items": 0, "quote_depth": quote_depth,
            })
        elif token_type in {"ordered_list_close", "bullet_list_close"}:
            if list_stack:
                list_stack.pop()
        elif token_type == "list_item_open":
            list_item_stack.append({"block": None, "list": list_stack[-1] if list_stack else None, "token": token})
        elif token_type == "list_item_close":
            if list_item_stack:
                list_item_stack.pop()
        elif token_type == "table_open":
            table = {"rows": [], "header_rows": 0, "token": token}
        elif token_type == "thead_open" and table is not None:
            table["in_header"] = True
        elif token_type == "thead_close" and table is not None:
            table["in_header"] = False
        elif token_type == "tr_open" and table is not None:
            current_row = []
        elif token_type == "tr_close" and table is not None and current_row is not None:
            table["rows"].append(current_row)
            if table.get("in_header"):
                table["header_rows"] += 1
            current_row = None
        elif token_type in {"th_open", "td_open"} and table is not None:
            current_cell_role = "header" if token_type == "th_open" else "body"
        elif token_type in {"th_close", "td_close"}:
            current_cell_role = None
        elif token_type == "table_close":
            if table is not None:
                rows = table["rows"]
                header_rows = int(table["header_rows"])
                append_block(model, table_block(
                    next_id(), "data", rows, header_rows=header_rows,
                    source=source_record(format="markdown_table", raw_text=_source_text(lines, table["token"]), line_start=line_number(table["token"]), semantic_origin="markdown_token", container_quote_depth=quote_depth or None),
                ))
                report["tables_processed"] += 1
                source_audit["table_count"] += 1
                source_audit["table_rows"] += len(rows)
                source_audit["table_header_rows"] += header_rows
            table = None
        elif token_type == "fence":
            append_block(model, body_block(
                next_id(), token.content, role="code_block",
                source=source_record(raw_text=_source_text(lines, token), format="markdown_fence", language=token.info.strip() or None, line_start=line_number(token), semantic_origin="markdown_token", container_quote_depth=quote_depth or None),
            ))
            source_audit["fence_blocks"] += 1
        elif token_type == "code_block":
            append_block(model, body_block(
                next_id(), token.content, role="code_block",
                source=source_record(raw_text=_source_text(lines, token), format="markdown_indented_code", line_start=line_number(token), semantic_origin="markdown_token", container_quote_depth=quote_depth or None),
            ))
            source_audit["fence_blocks"] += 1
        elif token_type == "inline":
            if table is not None and current_row is not None and current_cell_role is not None:
                current_row.append({"text": _inline_text(token, parser), "cell_role": current_cell_role})
                source_audit["inline_code"] += sum(child.type == "code_inline" for child in token.children or [])
                continue
            text = _inline_text(token, parser)
            source_audit["inline_code"] += sum(child.type == "code_inline" for child in token.children or [])
            if any(child.type == "text" and re.search(r"<[^>]+>", child.content) for child in token.children or []):
                text = re.sub(r"<[^>]+>", "", text).strip()
                source_audit["unsupported_tokens"].append({"type": "html_inline", "line_start": line_number(token)})
            if heading_level is not None:
                if list_item_stack and list_item_stack[-1].get("list") is not None:
                    append_list_item(list_item_stack[-1], text)
                    source_audit["unsupported_tokens"].append({"type": "list_item_heading", "line_start": line_number(token)})
                    continue
                if quote_depth:
                    append_text_block(token, text, role="quote")
                    source_audit["unsupported_tokens"].append({"type": "quoted_heading", "line_start": line_number(token)})
                    continue
                source = source_record(raw_text=_source_text(lines, token) or text, format="markdown_heading", md_level=heading_level, line_start=line_number(token), semantic_origin="markdown_token")
                if heading_level == 1 and not title_seen:
                    append_block(model, heading_block(next_id(), strip_heading_marker(text), 0, role="title", source=source))
                    title_seen = True
                    source_audit["title_count"] += 1
                else:
                    level = max(1, min(heading_level - 1, 5))
                    append_block(model, heading_block(next_id(), strip_heading_marker(text), level, source=source))
                    source_audit["heading_count"] += 1
            elif list_item_stack and list_item_stack[-1].get("list") is not None:
                item = list_item_stack[-1]
                if quote_depth and not item["list"].get("quote_depth"):
                    append_text_block(token, text, role="quote")
                else:
                    append_list_item(item, text)
            else:
                standalone_image = _standalone_image(token, parser)
                if standalone_image is not None:
                    alt_text, target = standalone_image
                    asset_path = _local_image_path(src_path, target)
                    if asset_path is None:
                        append_text_block(token, f"图片：{alt_text} ({target})")
                        source_audit["unsupported_tokens"].append({"type": "remote_image", "line_start": line_number(token), "target": target})
                    else:
                        asset_sha256 = _asset_sha256(asset_path)
                        if asset_sha256 is None:
                            source_audit["unsupported_tokens"].append({"type": "local_image_missing", "line_start": line_number(token), "target": target})
                        append_block(model, image_block(
                            next_id(), alt_text=alt_text,
                            source=source_record(raw_text=_source_text(lines, token), format="markdown_image", asset_path=str(asset_path), asset_sha256=asset_sha256, line_start=line_number(token), semantic_origin="markdown_token"),
                        ))
                else:
                    for child in token.children or []:
                        if child.type == "image" and re.match(r"^[a-z][a-z0-9+.-]*://", str(child.attrGet("src") or ""), re.IGNORECASE):
                            source_audit["unsupported_tokens"].append({"type": "mixed_remote_image", "line_start": line_number(token), "target": child.attrGet("src")})
                    append_text_block(token, text, role="quote" if quote_depth else None)
        elif token_type in {"html_block", "hr"}:
            readable = re.sub(r"<[^>]+>", "", token.content).strip() if token_type == "html_block" else "分隔线"
            if readable:
                append_text_block(token, readable)
            source_audit["unsupported_tokens"].append({"type": token_type, "line_start": line_number(token)})

    report["source_lists"] = {"detected": source_audit["list_items"], "ambiguous": 0, "ignored": 0, "source": "markdown-it-py"}
    report["markdown_source_audit"] = source_audit
    annotate_unordered_candidates(model, parse_report)
    annotate_semantic_list_groups(model, parse_report)
    return model


def audit_markdown_preservation(source_model: dict | None, normalized_model: dict | None, report: dict, rendered_model: dict | None = None) -> dict:
    """Check token-derived source semantics across every later model boundary."""
    source = report.get("markdown_source_audit", {})
    source_blocks = (source_model or {}).get("document", {}).get("blocks", [])
    normalized_blocks = (normalized_model or {}).get("document", {}).get("blocks", [])
    rendered_blocks = (rendered_model or {}).get("document", {}).get("blocks", [])

    def selected(blocks, block_type=None, role=None):
        return [block for block in blocks if (block_type is None or block.get("block_type") == block_type) and (role is None or block.get("role") == role)]

    def table_matrix(block):
        return [[(cell.get("text", ""), cell.get("cell_role", "")) for cell in row] for row in block.get("rows", [])]

    def list_shape(block):
        numbering = block.get("source", {}).get("numbering", {})
        return (block.get("text"), block.get("level"), numbering.get("family"), block.get("restart"), block.get("parent_list_item_id"))

    def headings(blocks):
        return [(block.get("role"), block.get("level"), block.get("text")) for block in selected(blocks, "heading")]

    def semantic_snapshot(blocks):
        """Return the source-AST document sequence without role fallbacks."""
        result = []
        for block in blocks:
            if block.get("source", {}).get("semantic_origin") != "markdown_token":
                continue
            block_type = block.get("block_type")
            if block_type == "table":
                result.append(("table", block.get("role"), block.get("table_type"),
                               block.get("header_rows"), table_matrix(block)))
            elif block_type == "list_item":
                numbering = block.get("source", {}).get("numbering", {})
                family = numbering.get("family") or ("unordered" if block.get("list_type") in {"dash", "bullet_dot"} else "ordered")
                result.append(("list_item", block.get("role"), block.get("text"), block.get("level"), family,
                               block.get("restart"), block.get("parent_list_item_id"),
                               block.get("source", {}).get("task_state"), block.get("source", {}).get("container_quote_depth")))
            elif block_type in {"heading", "body"}:
                result.append((block_type, block.get("role"), block.get("level"), block.get("text"),
                               block.get("source", {}).get("container_quote_depth")))
            elif block_type == "image":
                result.append(("image", block.get("role"), block.get("asset_id"), block.get("alt_text"),
                               block.get("source", {}).get("asset_path"), block.get("source", {}).get("asset_sha256")))
            elif block_type == "caption":
                result.append(("caption", block.get("role"), block.get("caption_type"), block.get("label"),
                               block.get("raw_number"), block.get("text")))
            elif block_type == "appendix":
                result.append(("appendix", block.get("role"), block.get("appendix_id"), block.get("classification"),
                               tuple(block.get("title_lines") or []), block.get("title")))
        return result

    source_tables = selected(source_blocks, "table")
    normalized_tables = selected(normalized_blocks, "table")
    rendered_tables = selected(rendered_blocks, "table")
    table_matrix_mismatches = [
        index + 1 for index, (before, after) in enumerate(zip(source_tables, normalized_tables))
        if table_matrix(before) != table_matrix(after) or before.get("header_rows") != after.get("header_rows")
    ]
    table_count_mismatch = len(source_tables) != len(normalized_tables)
    list_shape_mismatch = [
        index + 1 for index, (before, after) in enumerate(zip(selected(source_blocks, "list_item"), selected(normalized_blocks, "list_item")))
        if list_shape(before) != list_shape(after)
    ]
    token_inventory = source.get("token_inventory", {})
    token_inventory_mismatch = {
        "list_item_open": (token_inventory.get("list_item_open", 0), len(selected(source_blocks, "list_item"))),
        "table_open": (token_inventory.get("table_open", 0), len(source_tables)),
        "code_blocks": (token_inventory.get("fence", 0) + token_inventory.get("code_block", 0), len(selected(source_blocks, role="code_block"))),
    }
    rendered_image_relation_failures = [
        index + 1 for index, block in enumerate(selected(rendered_blocks, "image"))
        if block.get("source", {}).get("image_relationship_valid") is not True
    ] if rendered_model is not None else []
    token_inventory_mismatch = {
        key: value for key, value in token_inventory_mismatch.items() if value[0] != value[1]
    }
    normalized_counts = {
        "titles": len(selected(normalized_blocks, role="title")),
        "headings": len(selected(normalized_blocks, "heading")),
        "list_items": len(selected(normalized_blocks, "list_item")),
        "tables": len(normalized_tables),
    }
    result = {
        "source": source,
        "source_ast": {"titles": len(selected(source_blocks, role="title")), "headings": len(selected(source_blocks, "heading")), "list_items": len(selected(source_blocks, "list_item")), "tables": len(source_tables), "table_rows": sum(len(block.get("rows", [])) for block in source_tables)},
        "normalized_ast": normalized_counts,
        "rendered_model": {"titles": len(selected(rendered_blocks, role="title")), "headings": len(selected(rendered_blocks, "heading")), "list_items": len(selected(rendered_blocks, "list_item")), "tables": len(rendered_tables)},
        "heading_text_mismatch": headings(source_blocks) != headings(normalized_blocks),
        "table_matrix_mismatches": table_matrix_mismatches,
        "table_count_mismatch": table_count_mismatch,
        "list_shape_mismatches": list_shape_mismatch,
        "list_count_mismatch": len(selected(source_blocks, "list_item")) != len(selected(normalized_blocks, "list_item")),
        "token_inventory_mismatch": token_inventory_mismatch,
        "rendered_image_relation_failures": rendered_image_relation_failures,
        "rendered_model_mismatch": {
            key: (normalized_counts[key], rendered)
            for key, rendered in {
                "titles": len(selected(rendered_blocks, role="title")),
                "headings": len(selected(rendered_blocks, "heading")),
                "list_items": len(selected(rendered_blocks, "list_item")),
                "tables": len(rendered_tables),
            }.items() if rendered_model is not None and normalized_counts[key] != rendered
        },
        "source_normalized_semantic_mismatch": semantic_snapshot(source_blocks) != semantic_snapshot(normalized_blocks),
        "rendered_semantic_mismatch": semantic_snapshot(normalized_blocks) != semantic_snapshot(rendered_blocks) if rendered_model is not None else False,
        "markdown_residue": report.get("audit", {}).get("markdown_residue", []),
        "unsupported_tokens": source.get("unsupported_tokens", []),
        "semantic_sequence": {
            "source": semantic_snapshot(source_blocks),
            "normalized": semantic_snapshot(normalized_blocks),
            "rendered": semantic_snapshot(rendered_blocks),
        },
    }
    before, after = result["source_ast"], result["normalized_ast"]
    result["passed"] = (
        before["titles"] == after["titles"] == 1 and not result["heading_text_mismatch"]
        and not result["table_count_mismatch"] and not result["table_matrix_mismatches"]
        and not result["list_count_mismatch"] and not result["list_shape_mismatches"]
        and not result["token_inventory_mismatch"] and not result["rendered_model_mismatch"]
        and not result["rendered_image_relation_failures"]
        and not result["source_normalized_semantic_mismatch"] and not result["rendered_semantic_mismatch"]
        and not result["markdown_residue"] and not result["unsupported_tokens"]
    )
    return result
