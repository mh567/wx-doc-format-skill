"""Deterministic Source AST normalization and presentation planning."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .digest import digest
from .model import build_indexes, inline_text_projection
from .template import TemplateBundle, materialize_profile, preflight_template, presentation_contract_from
from .validation import validate_ast


class NormalizationError(ValueError):
    """Raised when normalization inputs or strategy are invalid."""


def _node_map(ast: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    document = ast["document"]
    result: dict[str, Mapping[str, Any]] = {}
    result.update(document["blocks"])
    result.update(document["containers"])
    return result


def _scope_for_node(
    document: Mapping[str, Any],
    indexes: Any,
    node_id: str,
    *,
    stop_at_list_item: bool = False,
) -> str | None:
    current = indexes.parent_by_id.get(node_id)
    while current is not None:
        kind = document["containers"].get(current, {}).get("kind")
        if stop_at_list_item and kind == "list_item":
            return None
        if kind in {"table_cell", "quote"}:
            return f"scope:{current}"
        current = indexes.parent_by_id.get(current)
    return "scope:document"


def _semantic_scope_for_node(
    document: Mapping[str, Any],
    indexes: Any,
    node_id: str,
) -> str:
    node = document["containers"].get(node_id) or document["blocks"].get(node_id) or {}
    explicit = node.get("semantic_scope_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    current = node_id
    while current is not None:
        container = document["containers"].get(current, {})
        kind = container.get("kind")
        if kind == "appendix":
            appendix_id = str(container.get("appendix_id") or current)
            return str(container.get("semantic_scope_id") or f"semantic:appendix:{appendix_id}")
        if kind == "section":
            return f"semantic:section:{current}"
        if kind in {"table_cell", "quote"}:
            return f"semantic:{kind}:{current}"
        current = indexes.parent_by_id.get(current)
    return "semantic:main"


def _list_contexts(ast: Mapping[str, Any], indexes: Any) -> dict[str, dict[str, Any]]:
    document = ast["document"]
    contexts: dict[str, dict[str, Any]] = {}
    for list_id, container in document["containers"].items():
        if container.get("kind") != "list":
            continue
        depth = 0
        current = list_id
        while current in indexes.parent_by_id:
            current = indexes.parent_by_id[current]
            if document["containers"].get(current, {}).get("kind") == "list":
                depth += 1
        numbering_scope_id = _scope_for_node(document, indexes, list_id)
        semantic_scope_id = _semantic_scope_for_node(document, indexes, list_id)
        contexts[list_id] = {
            "depth": depth,
            "scope_id": numbering_scope_id,
            "numbering_scope_id": numbering_scope_id,
            "semantic_scope_id": semantic_scope_id,
            "list_kind": container.get("list_kind", "ordered"),
            "source_sequence_id": container.get("source_sequence_id", list_id),
            "source_start": max(1, int(container.get("source_start") or 1)),
        }
    return contexts


def _anchor(ast: Mapping[str, Any], item_id: str) -> tuple[str, list[dict[str, Any]]]:
    document = ast["document"]
    children = document["children"].get(item_id, [])
    if children:
        first = children[0]
        block = document["blocks"].get(first)
        if block and block.get("block_type") == "paragraph" and block.get("role") == "body":
            visible = inline_text_projection(block.get("inline_content", []))
            if visible or any(item.get("kind") == "image" for item in block.get("inline_content", [])):
                return first, []
    source_ref = document["containers"].get(item_id, {}).get("source_ref")
    generated_id = f"generated-anchor-{item_id}"
    generated = {
        "node_id": generated_id,
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [],
        "generated_by": "normalization.marker_anchor",
        "generation_basis": [item_id, source_ref] if source_ref else [item_id],
    }
    return generated_id, [generated]


def _reading_order(ast: Mapping[str, Any]) -> list[str]:
    document = ast["document"]
    order: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            order.append(child_id)
            walk(child_id)

    walk(document["root_id"])
    return order


def _style_role(list_kind: str, level: int) -> str:
    if list_kind == "unordered":
        return "list_dash" if level == 0 else "list_bullet"
    return "list_letter" if level == 0 else "list_decimal"


def normalize(
    source_ast: Mapping[str, Any],
    profile: Mapping[str, Any] | TemplateBundle,
    *,
    strategy: str = "legacy",
    overlay: Mapping[str, Any] | None = None,
    presentation_override: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a normalized immutable semantic view without changing Source AST."""

    if strategy not in {"legacy", "wx-default-v2"}:
        raise NormalizationError(f"unknown normalization strategy {strategy!r}")
    validate_ast(source_ast)
    template_profile = materialize_profile(profile)
    ast = copy.deepcopy(dict(source_ast))
    indexes = build_indexes(ast, validate=False)
    list_contexts = _list_contexts(ast, indexes)
    required_roles = []
    for item in list_contexts.values():
        required_roles.append(_style_role(item["list_kind"], 0 if strategy == "wx-default-v2" else item["depth"]))
    preflight = preflight_template(template_profile, required_roles=required_roles)

    item_by_node: dict[str, dict[str, Any]] = {}
    document = ast["document"]
    for item_id, container in document["containers"].items():
        if container.get("kind") != "list_item":
            continue
        list_id = indexes.parent_by_id.get(item_id)
        if not list_id or document["containers"].get(list_id, {}).get("kind") != "list":
            continue
        list_info = list_contexts[list_id]
        target_level = 0 if strategy == "wx-default-v2" and list_info["list_kind"] == "ordered" else list_info["depth"]
        marker_anchor, generated = _anchor(ast, item_id)
        item_by_node[item_id] = {
            "marker_anchor": marker_anchor,
            "target_sequence": list_info["numbering_scope_id"] if strategy == "wx-default-v2" else list_info["source_sequence_id"],
            "target_level": target_level,
            "marker_kind": list_info["list_kind"],
            "source_marker": container.get("source_marker"),
            "source_ordinal": container.get("source_ordinal"),
            "source_start": list_info["source_start"],
            "target_ordinal": None if list_info["list_kind"] == "unordered" else (container.get("source_ordinal") if strategy == "legacy" else None),
            "restart": False,
            "style_role": _style_role(list_info["list_kind"], target_level),
            "scope_id": list_info["scope_id"],
            "numbering_scope_id": list_info["numbering_scope_id"],
            "semantic_scope_id": list_info["semantic_scope_id"],
            "generated_anchor": bool(generated),
        }

    counters: dict[str, int] = {}

    def heading_scope(node_id: str) -> str | None:
        return _scope_for_node(document, indexes, node_id, stop_at_list_item=True)

    for node_id in _reading_order(ast):
        block = document["blocks"].get(node_id)
        if block is not None and (
            block.get("block_type") == "heading"
            or block.get("role") in {"heading", "appendix_title"}
            or block.get("appendix_role") in {"title", "heading"}
        ):
            scope_id = heading_scope(node_id)
            if scope_id is not None:
                counters[scope_id] = 0
            continue
        node = item_by_node.get(node_id)
        if node is None or strategy != "wx-default-v2" or node["marker_kind"] != "ordered":
            continue
        scope_id = node["scope_id"]
        counters[scope_id] = counters.get(scope_id, 0) + 1
        node["target_ordinal"] = counters[scope_id]
        node["restart"] = node["target_ordinal"] == 1

    for node in item_by_node.values():
        if node["marker_kind"] == "unordered":
            node["restart"] = node["source_ordinal"] in {None, 1}
        elif strategy == "legacy":
            node["restart"] = node["source_ordinal"] == node.get("source_start")

    override = presentation_override or {}
    for node_id, values in override.items():
        if node_id not in item_by_node or not isinstance(values, Mapping):
            continue
        for key in ("marker_anchor", "target_level", "style_role"):
            if key in values:
                item_by_node[node_id][key] = copy.deepcopy(values[key])

    generated_nodes = [generated for item_id in item_by_node for generated in _anchor(ast, item_id)[1]]
    semantic_payload = {"source_revision": source_ast["revision"], "source_ast": source_ast, "overlay": overlay or {}}
    presentation_payload = {
        "strategy": strategy,
        "profile": {
            "resolved_styles": dict(template_profile.get("resolved_styles") or {}),
            "numbering_ids": dict(template_profile.get("numbering_ids") or {}),
            "table_style": dict(template_profile.get("table_style") or {}),
        },
        "template_contract": presentation_contract_from(profile),
        "nodes": item_by_node,
        "overrides": override,
    }
    return {
        "schema_version": "1.0",
        "source_revision": source_ast["revision"],
        "source_ast": ast,
        "overlay": copy.deepcopy(dict(overlay or {})),
        "presentation_override": copy.deepcopy(dict(presentation_override or {})),
        "presentation": presentation_payload,
        "generated_nodes": generated_nodes,
        "template_preflight": preflight,
        "diagnostics": [],
        "semantic_digest": digest(semantic_payload),
        "presentation_digest": digest(presentation_payload),
        "context": copy.deepcopy(dict(context or {})),
    }


def semantic_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return left.get("semantic_digest") == right.get("semantic_digest")


def presentation_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return left.get("presentation_digest") == right.get("presentation_digest")
