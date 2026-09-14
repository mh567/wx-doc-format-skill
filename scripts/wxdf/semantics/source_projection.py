"""Canonical source-material projections used by parser migration gates."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from appendix_semantics import parse_appendix_title
from text_utils import caption_parts

from ..model import inline_text_projection
from ..validation import validate_ast, validate_source_facts
from .protected import protected_unit_semantics


_FORMULA_NUMBER_RE = re.compile(r"^\s*[\(（]\d+[\)）]\s*$")
_APPENDIX_HINT_RE = re.compile(r"(?:^|\n)\s*附录(?:\s+[A-Z])?(?:\s|$)")
_CLASSIFICATION_RE = re.compile(r"(?:^|\n)\s*[（(](?:规范性|资料性)[）)]\s*(?:\n|$)")


def _legacy_inline_runs(inline_content: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for item in inline_content:
        kind = item.get("kind")
        if kind == "text":
            text = str(item.get("text") or "")
            if text:
                run: dict[str, Any] = {"text": text}
                marks = list(item.get("marks") or [])
                if marks:
                    run["marks"] = marks
                runs.append(run)
        elif kind == "break":
            runs.append({"text": "\n"})
        elif kind == "tab":
            runs.append({"text": "\t"})
        elif kind == "link":
            for child in _legacy_inline_runs(list(item.get("children") or [])):
                child["href"] = str(item.get("target") or "")
                runs.append(child)
    return runs


def _descendants(children: Mapping[str, list[str]], node_ids: set[str]) -> set[str]:
    result = set(node_ids)
    pending = list(node_ids)
    while pending:
        node_id = pending.pop()
        for child_id in children.get(node_id, []):
            if child_id not in result:
                result.add(child_id)
                pending.append(child_id)
    return result


def excluded_source_nodes(regions: Mapping[str, Any], children: Mapping[str, list[str]]) -> set[str]:
    roots = {
        str(node_id)
        for region in regions.get("regions", [])
        if region.get("semantic_scope") == "excluded"
        for node_id in region.get("node_ids", [])
    }
    return _descendants(children, roots)


def _source_position(facts: Mapping[str, Any], node_id: str) -> int | None:
    value = facts.get("evidence", {}).get(node_id, {}).get("source_position")
    return value if isinstance(value, int) and value >= 0 else None


def _source_location(facts: Mapping[str, Any], node_id: str) -> dict[str, int] | None:
    evidence = facts.get("evidence", {}).get(node_id, {})
    for key in ("source_position", "line_start"):
        value = evidence.get(key)
        if isinstance(value, int) and value >= 0:
            return {"kind": key, "value": value}
    return None


def _canonical_paragraph(text: str, *, source_position: int | None, appendix_index: int) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    stripped = text.strip()
    caption_type, label, _, caption_text = caption_parts(stripped)
    caption = None
    if caption_type in {"table", "figure"}:
        caption = {
            "caption_type": caption_type,
            "label": label,
            "text": caption_text,
            "source_position": source_position,
        }
        return caption_text, caption, None

    appendix = None
    if _CLASSIFICATION_RE.search(stripped) or ("\n" in stripped and _APPENDIX_HINT_RE.search(stripped)):
        parsed = parse_appendix_title(stripped, appendix_index)
        appendix = {
            "appendix_id": parsed["appendix_id"],
            "classification": parsed["classification"],
            "title": parsed["title"],
            "source_position": source_position,
        }
        return str(parsed["title"]), None, appendix
    return stripped, None, None


def _fact_list_count(facts: Mapping[str, Any], excluded: set[str]) -> int:
    direct = sum(
        container.get("kind") == "list_item" and node_id not in excluded
        for node_id, container in facts["containers"].items()
    )
    if direct:
        return direct
    source_lists = facts.get("extensions", {}).get("docx_numbering_report", {}).get("source_lists", {})
    return max(0, int(source_lists.get("detected") or 0))


def project_source_facts(
    facts: Mapping[str, Any],
    regions: Mapping[str, Any],
    *,
    caption_semantics: Mapping[str, Any] | None = None,
    appendix_semantics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project SourceFacts into stable semantic material without template styles.

    ``caption_semantics`` and ``appendix_semantics`` are the published caption
    and appendix results.  When the caller supplies them, this projection reads
    the canonical ownership for those nodes instead of re-parsing captions and
    appendices.  Nodes the owners do not cover keep the local SourceFacts parse.
    """

    validate_source_facts(facts)
    excluded = excluded_source_nodes(regions, facts["children"])
    texts: list[str] = []
    captions: list[dict[str, Any]] = []
    appendices: list[dict[str, Any]] = []
    table_shapes: list[list[int]] = []

    caption_by_node = {
        str(item.get("node_id")): item
        for item in (caption_semantics or {}).get("captions", [])
        if item.get("node_id")
    }
    appendix_by_node = {
        str(item.get("title_node_id")): item
        for item in (appendix_semantics or {}).get("ranges", [])
        if item.get("title_node_id")
    }

    paragraphs: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []

    def add_paragraph(node_id: str, *, inside_cell: bool = False) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
        unit = facts["units"][node_id]
        inline_content = list(unit.get("inline_content", []))
        raw_text = inline_text_projection(inline_content)
        protected = None if inside_cell else protected_unit_semantics(unit, facts["evidence"].get(node_id, {}))
        protected_text = str(protected["text"]) if protected is not None else raw_text
        if not inside_cell and node_id in caption_by_node:
            entry = caption_by_node[node_id]
            text = str(entry.get("text") or protected_text).strip()
            caption = {
                "caption_type": entry.get("caption_type"),
                "label": entry.get("label"),
                "text": entry.get("text"),
                "source_position": _source_position(facts, node_id),
            }
            appendix = None
        elif not inside_cell and node_id in appendix_by_node:
            entry = appendix_by_node[node_id]
            text = str(entry.get("title") or protected_text).strip()
            caption = None
            appendix = {
                "appendix_id": entry.get("appendix_id"),
                "classification": entry.get("classification"),
                "title": entry.get("title"),
                "source_position": _source_position(facts, node_id),
            }
        else:
            text, caption, appendix = _canonical_paragraph(
                protected_text,
                source_position=_source_position(facts, node_id),
                appendix_index=len(appendices) + 1,
            )
        if caption is not None:
            captions.append(caption)
        if appendix is not None:
            appendices.append(appendix)
        semantic_kind = (
            "caption" if caption is not None
            else "appendix" if appendix is not None
            else str(protected["role"]) if protected is not None and protected.get("role")
            else "formula_number" if _FORMULA_NUMBER_RE.match(text)
            else "paragraph"
        )
        inline_runs = _legacy_inline_runs(inline_content) if text == raw_text else [{"text": text}]
        return text, semantic_kind, caption, appendix, inline_runs

    def visit(node_id: str, *, inside_cell: bool = False) -> list[str]:
        if node_id in excluded:
            return []
        unit = facts["units"].get(node_id)
        if unit is not None:
            if unit.get("kind") != "paragraph":
                return []
            text, semantic_kind, caption, appendix, inline_runs = add_paragraph(node_id, inside_cell=inside_cell)
            if text and not inside_cell:
                texts.append(text)
                paragraphs.append({
                    "node_id": node_id,
                    "location": _source_location(facts, node_id),
                    "text": text,
                    "semantic_kind": semantic_kind,
                    "inline_runs": inline_runs,
                    "caption": copy.deepcopy(caption),
                    "appendix": copy.deepcopy(appendix),
                })
            return [text] if text else []

        container = facts["containers"].get(node_id, {})
        kind = container.get("kind")
        if kind == "table":
            rows = facts["children"].get(node_id, [])
            table_shapes.append([len(facts["children"].get(row_id, [])) for row_id in rows])
            projected_rows: list[list[str]] = []
            projected_inline_rows: list[list[list[dict[str, Any]]]] = []
            for row_id in rows:
                projected_row: list[str] = []
                projected_inline_row: list[list[dict[str, Any]]] = []
                for cell_id in facts["children"].get(row_id, []):
                    cell_parts: list[str] = []
                    cell_runs: list[dict[str, Any]] = []
                    for child_id in facts["children"].get(cell_id, []):
                        cell_parts.extend(visit(child_id, inside_cell=True))
                        unit = facts["units"].get(child_id)
                        if unit is not None and unit.get("kind") == "paragraph":
                            raw_text = inline_text_projection(unit.get("inline_content", []))
                            canonical_text, _, _ = _canonical_paragraph(
                                raw_text,
                                source_position=_source_position(facts, child_id),
                                appendix_index=len(appendices) + 1,
                            )
                            if cell_runs and canonical_text:
                                cell_runs.append({"text": "\n"})
                            if canonical_text == raw_text:
                                cell_runs.extend(_legacy_inline_runs(list(unit.get("inline_content", []))))
                            elif canonical_text:
                                cell_runs.append({"text": canonical_text})
                    joined = "\n".join(part for part in cell_parts if part)
                    projected_row.append(joined)
                    projected_inline_row.append(cell_runs)
                    if joined:
                        texts.append(joined)
                projected_rows.append(projected_row)
                projected_inline_rows.append(projected_inline_row)
            tables.append({
                "node_id": node_id,
                "location": _source_location(facts, node_id),
                "rows": projected_rows,
                "inline_rows": projected_inline_rows,
            })
            return []
        result: list[str] = []
        for child_id in facts["children"].get(node_id, []):
            result.extend(visit(child_id, inside_cell=inside_cell))
        return result

    for child_id in facts["children"].get("root", []):
        visit(child_id)

    image_count = sum(
        item.get("kind") == "image"
        for unit_id, unit in facts["units"].items()
        if unit_id not in excluded
        for item in unit.get("inline_content", [])
    )
    formula_numbers = [
        {
            "source_position": _source_position(facts, unit_id),
            "source_text": inline_text_projection(unit.get("inline_content", [])).strip(),
        }
        for unit_id, unit in facts["units"].items()
        if unit_id not in excluded
        and unit.get("kind") == "paragraph"
        and _FORMULA_NUMBER_RE.match(inline_text_projection(unit.get("inline_content", [])).strip())
        and not facts.get("evidence", {}).get(unit_id, {}).get("marker_kind")
    ]
    return {
        "schema_version": "1.0",
        "text_inventory": texts,
        "paragraphs": paragraphs,
        "captions": captions,
        "appendices": appendices,
        "tables": tables,
        "table_shapes": table_shapes,
        "image_count": image_count,
        "list_item_count": _fact_list_count(facts, excluded),
        "formula_numbers": formula_numbers,
        "excluded_node_count": len(excluded),
    }


def project_source_ast(ast: Mapping[str, Any], *, ignored_list_items: set[str] | None = None) -> dict[str, Any]:
    """Project an AST into the same semantic material used for SourceFacts."""

    validate_ast(ast)
    ignored = ignored_list_items or set()
    document = ast["document"]
    blocks = document["blocks"]
    containers = document["containers"]
    children = document["children"]
    texts: list[str] = []
    table_shapes: list[list[int]] = []

    def paragraph_text(node_id: str) -> str:
        block = blocks[node_id]
        text = inline_text_projection(block.get("inline_content", [])).strip()
        caption_type, _, _, caption_text = caption_parts(text)
        if block.get("role") == "caption" or caption_type in {"table", "figure"}:
            return caption_text if caption_type in {"table", "figure"} else text
        return text

    def visit(node_id: str, *, inside_cell: bool = False) -> list[str]:
        block = blocks.get(node_id)
        if block is not None:
            if block.get("generated_by"):
                return []
            if block.get("block_type") == "table":
                rows = children.get(node_id, [])
                table_shapes.append([len(children.get(row_id, [])) for row_id in rows])
                for row_id in rows:
                    for cell_id in children.get(row_id, []):
                        parts: list[str] = []
                        for child_id in children.get(cell_id, []):
                            parts.extend(visit(child_id, inside_cell=True))
                        joined = "\n".join(part for part in parts if part)
                        if joined:
                            texts.append(joined)
                return []
            if block.get("block_type") != "paragraph":
                return []
            text = paragraph_text(node_id)
            if text and not inside_cell:
                texts.append(text)
            return [text] if text else []
        container = containers.get(node_id, {})
        result: list[str] = []
        for child_id in children.get(node_id, []):
            result.extend(visit(child_id, inside_cell=inside_cell))
        return result

    for child_id in children.get(document["root_id"], []):
        visit(child_id)
    image_count = sum(
        block.get("block_type") == "image" and not block.get("generated_by")
        for block in blocks.values()
    )
    list_item_count = sum(
        container.get("kind") == "list_item" and node_id not in ignored
        for node_id, container in containers.items()
    )
    return {
        "schema_version": "1.0",
        "text_inventory": texts,
        "table_shapes": table_shapes,
        "image_count": image_count,
        "list_item_count": list_item_count,
    }


def legacy_formula_list_differences(native_projection: Mapping[str, Any], legacy_ast: Mapping[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    """Identify the bounded legacy bug that turns a formula number into an empty list item."""

    formulas = {
        item.get("source_position"): str(item.get("source_text") or "")
        for item in native_projection.get("formula_numbers", [])
        if isinstance(item.get("source_position"), int)
    }
    document = legacy_ast["document"]
    approved: list[dict[str, Any]] = []
    ignored: set[str] = set()
    for item_id, container in document["containers"].items():
        if container.get("kind") != "list_item":
            continue
        child_ids = document["children"].get(item_id, [])
        if len(child_ids) != 1 or child_ids[0] not in document["blocks"]:
            continue
        block = document["blocks"][child_ids[0]]
        if block.get("block_type") != "paragraph" or inline_text_projection(block.get("inline_content", [])).strip():
            continue
        position = block.get("source_ref", {}).get("span", {}).get("start")
        if position not in formulas:
            continue
        approved.append({
            "type": "legacy_formula_number_empty_list_item",
            "source_position": position,
            "source_text": formulas[position],
            "legacy_item_id": item_id,
        })
        ignored.add(item_id)
    return approved, ignored


__all__ = [
    "excluded_source_nodes",
    "legacy_formula_list_differences",
    "project_source_ast",
    "project_source_facts",
]
