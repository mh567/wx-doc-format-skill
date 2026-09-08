"""Coverage comparison between SourceFacts and the legacy source AST."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from ..model import inline_text_projection
from ..validation import validate_ast, validate_source_facts


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


def _excluded_fact_ids(regions: Mapping[str, Any], children: Mapping[str, list[str]]) -> set[str]:
    roots = {
        str(node_id)
        for region in regions.get("regions", [])
        if region.get("semantic_scope") == "excluded"
        for node_id in region.get("node_ids", [])
    }
    return _descendants(children, roots)


def _fact_texts(facts: Mapping[str, Any], excluded: set[str]) -> list[str]:
    texts: list[str] = []
    for unit_id, unit in facts["units"].items():
        if unit_id in excluded or unit.get("kind") != "paragraph":
            continue
        text = inline_text_projection(unit.get("inline_content", [])).strip()
        if text:
            texts.append(text)
    return texts


def _ast_texts(ast: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for block in ast["document"]["blocks"].values():
        if block.get("generated_by") or block.get("block_type") != "paragraph":
            continue
        text = inline_text_projection(block.get("inline_content", [])).strip()
        if text:
            texts.append(text)
    return texts


def _fact_table_shapes(facts: Mapping[str, Any], excluded: set[str]) -> list[list[int]]:
    shapes: list[list[int]] = []
    for table_id, container in facts["containers"].items():
        if table_id in excluded or container.get("kind") != "table":
            continue
        rows = facts["children"].get(table_id, [])
        shapes.append([len(facts["children"].get(row_id, [])) for row_id in rows])
    return shapes


def _ast_table_shapes(ast: Mapping[str, Any]) -> list[list[int]]:
    document = ast["document"]
    shapes: list[list[int]] = []
    for table_id, block in document["blocks"].items():
        if block.get("generated_by") or block.get("block_type") != "table":
            continue
        rows = document["children"].get(table_id, [])
        shapes.append([len(document["children"].get(row_id, [])) for row_id in rows])
    return shapes


def _fact_image_count(facts: Mapping[str, Any], excluded: set[str]) -> int:
    return sum(
        item.get("kind") == "image"
        for unit_id, unit in facts["units"].items()
        if unit_id not in excluded
        for item in unit.get("inline_content", [])
    )


def _fact_list_count(facts: Mapping[str, Any], excluded: set[str]) -> int:
    direct = sum(
        container.get("kind") == "list_item" and node_id not in excluded
        for node_id, container in facts["containers"].items()
    )
    if direct:
        return direct
    source_lists = facts.get("extensions", {}).get("docx_numbering_report", {}).get("source_lists", {})
    return max(0, int(source_lists.get("detected") or 0))


def _domain(native: Any, legacy: Any) -> dict[str, Any]:
    return {
        "status": "equivalent" if native == legacy else "mismatch",
        "native_count": len(native) if isinstance(native, list) else native,
        "legacy_count": len(legacy) if isinstance(legacy, list) else legacy,
    }


def compare_parser_equivalence(
    facts: Mapping[str, Any],
    regions: Mapping[str, Any],
    legacy_ast: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare source material required before the native parser can own output."""

    validate_source_facts(facts)
    validate_ast(legacy_ast)
    excluded = _excluded_fact_ids(regions, facts["children"])
    native_texts = _fact_texts(facts, excluded)
    legacy_texts = _ast_texts(legacy_ast)
    native_tables = _fact_table_shapes(facts, excluded)
    legacy_tables = _ast_table_shapes(legacy_ast)
    native_images = _fact_image_count(facts, excluded)
    legacy_images = sum(
        block.get("block_type") == "image" and not block.get("generated_by")
        for block in legacy_ast["document"]["blocks"].values()
    )
    native_lists = _fact_list_count(facts, excluded)
    legacy_lists = sum(
        container.get("kind") == "list_item"
        for container in legacy_ast["document"]["containers"].values()
    )

    domains = {
        "text": _domain(native_texts, legacy_texts),
        "tables": _domain(native_tables, legacy_tables),
        "images": _domain(native_images, legacy_images),
        "lists": _domain(native_lists, legacy_lists),
    }
    issues: list[dict[str, Any]] = []
    native_only = Counter(native_texts) - Counter(legacy_texts)
    legacy_only = Counter(legacy_texts) - Counter(native_texts)
    if native_only or legacy_only:
        issues.append({
            "type": "text_inventory_mismatch",
            "native_only_count": sum(native_only.values()),
            "legacy_only_count": sum(legacy_only.values()),
            "native_only_sample": list(native_only)[:5],
            "legacy_only_sample": list(legacy_only)[:5],
        })
    if native_tables != legacy_tables:
        issues.append({
            "type": "table_grid_mismatch",
            "native": native_tables,
            "legacy": legacy_tables,
        })
    if native_images != legacy_images:
        issues.append({
            "type": "image_count_mismatch",
            "native": native_images,
            "legacy": legacy_images,
        })
    if native_lists != legacy_lists:
        issues.append({
            "type": "list_item_count_mismatch",
            "native": native_lists,
            "legacy": legacy_lists,
        })

    return {
        "schema_version": "1.0",
        "status": "equivalent" if not issues else "mismatch",
        "passed": not issues,
        "excluded_node_count": len(excluded),
        "domains": domains,
        "issues": issues,
    }


__all__ = ["compare_parser_equivalence"]
