"""Deterministic Source AST normalization and presentation planning."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .digest import digest
from .list_policy import LIST_POLICIES, ListPolicy, ListProjection
from .model import build_indexes, inline_text_projection
from .template import TemplateBundle, materialize_profile, preflight_template, presentation_contract_from
from .validation import validate_ast


class NormalizationError(ValueError):
    """Raised when normalization inputs or strategy are invalid."""


__all__ = [
    "LIST_POLICIES",
    "ListPolicy",
    "ListProjection",
    "NormalizationError",
    "list_policy",
    "normalize",
    "presentation_equal",
    "semantic_equal",
]


def list_policy(name: str) -> ListPolicy:
    """Return the named WX list policy; reject an unknown policy up front."""

    try:
        return LIST_POLICIES[name]
    except KeyError:
        raise NormalizationError(f"unknown list policy {name!r}") from None


def _policy_for_strategy(strategy: str) -> ListPolicy:
    """Resolve a strategy name, keeping the historical error message."""

    if strategy not in LIST_POLICIES:
        raise NormalizationError(f"unknown normalization strategy {strategy!r}")
    return LIST_POLICIES[strategy]


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


def _list_contexts(
    ast: Mapping[str, Any],
    indexes: Any,
    stored_levels: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return one context per list container.

    ``stored_levels`` carries the ``semantic_level`` the list semantics layer
    published for each list container, which is what the projection consumes.
    The structural walk (the number of ancestor ``list`` containers) is the
    fallback for a hand-built AST whose containers were never visited by the list
    semantics module; the two agree because the materialized container tree
    encodes the same level.
    """

    stored_levels = stored_levels or {}
    document = ast["document"]
    contexts: dict[str, dict[str, Any]] = {}
    for list_id, container in document["containers"].items():
        if container.get("kind") != "list":
            continue
        stored = stored_levels.get(list_id)
        if not isinstance(stored, int) or isinstance(stored, bool) or stored < 0:
            stored = None
        depth = stored
        if depth is None:
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
            "stored_level": stored,
            "scope_id": numbering_scope_id,
            "numbering_scope_id": numbering_scope_id,
            "semantic_scope_id": semantic_scope_id,
            "list_kind": container.get("list_kind", "ordered"),
            "source_sequence_id": container.get("source_sequence_id", list_id),
            "source_start": max(1, int(container.get("source_start") or 1)),
        }
    return contexts


def _stored_list_levels(context: Mapping[str, Any] | None) -> dict[str, int]:
    """Return ``{list_id: semantic_level}`` published by the list semantics layer.

    The native normalizer passes the levels of the list relations it already
    holds, so normalization consumes the stored semantic level instead of
    re-deriving it from the tree.  A direct ``normalize`` caller that has no list
    semantics result falls back to the structural walk.
    """

    if not isinstance(context, Mapping):
        return {}
    stored: dict[str, int] = {}
    for entry in context.get("list_container_levels") or []:
        if not isinstance(entry, Mapping):
            continue
        list_id = entry.get("list_id")
        level = entry.get("semantic_level")
        if not isinstance(list_id, str) or not list_id:
            continue
        if not isinstance(level, int) or isinstance(level, bool) or level < 0:
            continue
        stored.setdefault(list_id, level)
    return stored


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


def normalize(
    source_ast: Mapping[str, Any],
    profile: Mapping[str, Any] | TemplateBundle,
    *,
    strategy: str | ListPolicy = "legacy",
    overlay: Mapping[str, Any] | None = None,
    presentation_override: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a normalized immutable semantic view without changing Source AST.

    ``strategy`` selects the presentation policy: a policy name (``legacy`` or
    ``wx-default-v2``) or a :class:`ListPolicy` instance for a caller that wants
    a different projection without registering one.
    """

    policy = strategy if isinstance(strategy, ListPolicy) else _policy_for_strategy(strategy)
    validate_ast(source_ast)
    template_profile = materialize_profile(profile)
    # The policy binds each style role to the numbering instance the template
    # already provides for that role (``list_letter`` to the first-level letter
    # instance, ``list_decimal`` / ``list_bullet`` to the second-level instances).
    # ``wx-default-v2`` no longer synthesizes a multi-level instance: at most two
    # levels are recognised (AGENTS.md decision 1), so the second level shows the
    # template's own ``1)`` form and the two levels are distinguished by style,
    # instance and visible marker rather than by a deeper ``w:ilvl``.
    numbering_ids: dict[str, Any] = dict(template_profile.get("numbering_ids") or {})
    ast = copy.deepcopy(dict(source_ast))
    indexes = build_indexes(ast, validate=False)
    list_contexts = _list_contexts(ast, indexes, _stored_list_levels(context))
    required_roles = sorted(
        {policy.project(item["list_kind"], item["depth"]).style_role for item in list_contexts.values()}
    )
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
        projection = policy.project(list_info["list_kind"], list_info["depth"])
        marker_anchor, generated = _anchor(ast, item_id)
        item_by_node[item_id] = {
            "marker_anchor": marker_anchor,
            "target_sequence": list_info["numbering_scope_id"] if policy.chapter_scoped_sequence else list_info["source_sequence_id"],
            "target_level": projection.render_level,
            "semantic_level": list_info["depth"],
            "render_level_clamped": projection.clamped,
            "marker_kind": list_info["list_kind"],
            "source_marker": container.get("source_marker"),
            "source_ordinal": container.get("source_ordinal"),
            "source_start": list_info["source_start"],
            "target_ordinal": None if list_info["list_kind"] == "unordered" else (None if policy.chapter_scoped_sequence else container.get("source_ordinal")),
            "restart": False,
            "style_role": projection.style_role,
            "scope_id": list_info["scope_id"],
            "numbering_scope_id": list_info["numbering_scope_id"],
            "semantic_scope_id": list_info["semantic_scope_id"],
            "generated_anchor": bool(generated),
        }

    counters: dict[str, int] = {}

    def heading_scope(node_id: str) -> str | None:
        return _scope_for_node(document, indexes, node_id, stop_at_list_item=True)

    # Per-chapter ordered numbering.  AGENTS.md decision 1 requires the ordered
    # numbering to restart after each heading.  A single ``w:num`` instance holds
    # one counter for the whole document, so the restart is realised by binding
    # each later chapter's ordered items to their own ``w:num`` instance that
    # references the same abstract definition as the style role's template
    # instance.  The first chapter that uses a role keeps the template's own
    # instance, so a single-chapter document (or one with no ordered list) writes
    # exactly the numbering it wrote before; only a later chapter gets a
    # synthesized instance.  ``numbering_instances`` publishes the synthesized
    # instances so the renderer materializes them and the readback audit expects
    # the same ids.
    numbering_catalog = template_profile.get("numbering") if isinstance(template_profile, Mapping) else None
    template_num_to_abstract: dict[str, Any] = {}
    if isinstance(numbering_catalog, Mapping) and isinstance(numbering_catalog.get("num_to_abstract"), Mapping):
        template_num_to_abstract = {str(key): value for key, value in numbering_catalog["num_to_abstract"].items()}
    known_num_ids = [
        int(value) for value in numbering_ids.values() if isinstance(value, int) and not isinstance(value, bool)
    ]
    known_num_ids += [int(key) for key in template_num_to_abstract if str(key).lstrip("-").isdigit()]
    next_num_id = (max(known_num_ids) + 1) if known_num_ids else 1
    synthesized_instances: list[dict[str, Any]] = []
    role_first_chapter: dict[str, int] = {}
    role_chapter_instance: dict[tuple[str, int], int] = {}
    chapter_index = 0

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
            chapter_index += 1
            continue
        node = item_by_node.get(node_id)
        if node is None or not policy.chapter_scoped_sequence or node["marker_kind"] != "ordered":
            continue
        scope_id = node["scope_id"]
        counters[scope_id] = counters.get(scope_id, 0) + 1
        node["target_ordinal"] = counters[scope_id]
        node["restart"] = node["target_ordinal"] == 1
        role = str(node.get("style_role") or "")
        template_num = numbering_ids.get(role)
        if not isinstance(template_num, int) or isinstance(template_num, bool):
            continue
        first_chapter = role_first_chapter.get(role)
        if first_chapter is None:
            role_first_chapter[role] = chapter_index
            node["numbering_instance"] = template_num
        elif first_chapter == chapter_index:
            node["numbering_instance"] = template_num
        else:
            key = (role, chapter_index)
            instance = role_chapter_instance.get(key)
            if instance is None:
                instance = next_num_id
                next_num_id += 1
                role_chapter_instance[key] = instance
                abstract = numbering_ids.get(f"{role}_abstract")
                synthesized_instances.append({
                    "num_id": instance,
                    "template_num_id": template_num,
                    "abstract_num_id": int(abstract) if isinstance(abstract, int) and not isinstance(abstract, bool) else None,
                    "style_role": role,
                })
            node["numbering_instance"] = instance

    for node in item_by_node.values():
        if node["marker_kind"] == "unordered":
            node["restart"] = node["source_ordinal"] in {None, 1}
        elif not policy.chapter_scoped_sequence:
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
        "strategy": policy.name,
        "profile": {
            "resolved_styles": dict(template_profile.get("resolved_styles") or {}),
            "numbering_ids": numbering_ids,
            "table_style": dict(template_profile.get("table_style") or {}),
        },
        "template_contract": presentation_contract_from(profile),
        "nodes": item_by_node,
        "overrides": override,
        "numbering_instances": synthesized_instances,
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
