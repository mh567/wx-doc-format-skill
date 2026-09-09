"""Coverage comparison between SourceFacts and the legacy source AST."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from ..validation import validate_ast, validate_source_facts
from .source_projection import (
    legacy_formula_list_differences,
    project_source_ast,
    project_source_facts,
)


def _domain(native: Any, legacy: Any) -> dict[str, Any]:
    return {
        "status": "equivalent" if native == legacy else "mismatch",
        "native_count": len(native) if isinstance(native, list) else native,
        "legacy_count": len(legacy) if isinstance(legacy, list) else legacy,
    }


def _inventory_domain(native: list[str], legacy: list[str]) -> dict[str, Any]:
    return {
        "status": "equivalent" if Counter(native) == Counter(legacy) else "mismatch",
        "native_count": len(native),
        "legacy_count": len(legacy),
    }


def compare_parser_equivalence(
    facts: Mapping[str, Any],
    regions: Mapping[str, Any],
    legacy_ast: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare source material required before the native parser can own output."""

    validate_source_facts(facts)
    validate_ast(legacy_ast)
    native = project_source_facts(facts, regions)
    approved_differences, ignored_legacy_items = legacy_formula_list_differences(native, legacy_ast)
    legacy = project_source_ast(legacy_ast, ignored_list_items=ignored_legacy_items)
    ignored_formula_texts = Counter(
        item["source_text"] for item in approved_differences if item.get("source_text")
    )
    native_texts = list((Counter(native["text_inventory"]) - ignored_formula_texts).elements())
    legacy_texts = legacy["text_inventory"]
    native_tables = native["table_shapes"]
    legacy_tables = legacy["table_shapes"]
    native_images = native["image_count"]
    legacy_images = legacy["image_count"]
    native_lists = native["list_item_count"]
    legacy_lists = legacy["list_item_count"]

    domains = {
        "text": _inventory_domain(native_texts, legacy_texts),
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
        "excluded_node_count": native["excluded_node_count"],
        "domains": domains,
        "issues": issues,
        "approved_differences": approved_differences,
    }


__all__ = ["compare_parser_equivalence"]
