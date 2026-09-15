"""Deterministic Source AST normalization and presentation planning."""

from __future__ import annotations

import copy
from dataclasses import dataclass
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


_ORDERED_NUMBER_FORMATS = frozenset(
    {"decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman"}
)
_UNORDERED_NUMBER_FORMATS = frozenset({"bullet"})


def _template_numbering_catalog(
    template_profile: Mapping[str, Any],
) -> tuple[dict[int, dict[int, dict[str, str]]], dict[int, int]]:
    """Return ``({numId: {ilvl: level}}, {numId: abstractNumId})`` from the profile.

    A level carries the ``numFmt`` / ``lvlText`` / ``pStyle`` the template
    declares, which is what the synthesized list-only instance is built from.
    """

    numbering = template_profile.get("numbering")
    if not isinstance(numbering, Mapping):
        return {}, {}
    num_to_abstract = numbering.get("num_to_abstract") or {}
    abstract_levels = numbering.get("abstract_levels") or {}
    catalog: dict[int, dict[int, dict[str, str]]] = {}
    abstract_by_num: dict[int, int] = {}
    for num_key, abstract_key in num_to_abstract.items():
        try:
            num_id = int(num_key)
        except (TypeError, ValueError):
            continue
        try:
            abstract_by_num[num_id] = int(abstract_key)
        except (TypeError, ValueError):
            pass
        levels: dict[int, dict[str, str]] = {}
        for level in abstract_levels.get(str(abstract_key)) or []:
            if not isinstance(level, Mapping):
                continue
            try:
                ilvl = int(level.get("ilvl") or 0)
            except (TypeError, ValueError):
                ilvl = 0
            levels[ilvl] = {
                "numFmt": str(level.get("numFmt") or ""),
                "lvlText": str(level.get("lvlText") or ""),
                "pStyle": str(level.get("pStyle") or ""),
            }
        catalog[num_id] = levels
    return catalog, abstract_by_num


def _template_abstract_ids(template_profile: Mapping[str, Any]) -> list[int]:
    """Return every ``abstractNumId`` the template numbering part declares."""

    numbering = template_profile.get("numbering")
    if not isinstance(numbering, Mapping):
        return []
    abstract_ids: list[int] = []
    for key in (numbering.get("abstract_levels") or {}):
        try:
            abstract_ids.append(int(key))
        except (TypeError, ValueError):
            continue
    return abstract_ids


@dataclass(frozen=True)
class SynthesizedNumbering:
    """A list-only numbering instance the produced document materializes.

    The instance is never written into the template: normalization names the
    ids and the source definitions, and the renderer clones them into the
    ``w:numbering`` part of the produced document.
    """

    num_id: int
    abstract_num_id: int
    deeper_abstract_num_id: int
    level_zero_abstract_num_id: int | None = None
    level_zero_num_fmt: str | None = None
    level_zero_lvl_text: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "num_id": self.num_id,
            "abstract_num_id": self.abstract_num_id,
            "deeper_abstract_num_id": self.deeper_abstract_num_id,
            "level_zero_abstract_num_id": self.level_zero_abstract_num_id,
            "level_zero_num_fmt": self.level_zero_num_fmt,
            "level_zero_lvl_text": self.level_zero_lvl_text,
        }


def _numbering_plan(
    template_profile: Mapping[str, Any],
    policy: ListPolicy,
) -> tuple[dict[str, Any], list[SynthesizedNumbering]]:
    """Resolve the numbering instance each style role is bound to.

    The template binds ``list_letter`` to the first-level letter instance and
    ``list_decimal`` to a single-level second-level instance, and its only
    multi-level ordered instance is the heading outline (``w:styles`` binds
    heading 1 to 6 to it), so a plain rebind would make a nested list item share
    the heading counters.  When the policy asks for multi-level numbering this
    plan therefore names ONE list-only instance: a copy of the template's
    multi-level outline for the deeper levels, whose level 0 is the template's
    own first-level letter definition.  Every ordered role binds to that
    instance, so level-0 and deeper ordered items share one counter set, ``%1``
    tracks the list instead of the chapter, and a level-0 item restarts the
    levels below it.

    The ``<role>_abstract`` companion is updated too, so the saved-output audit
    reads a consistent abstract id.  A template without a multi-level ordered
    instance keeps the original bindings, and the renderer reports the clamp
    instead of hiding it.
    """

    numbering_ids: dict[str, Any] = dict(template_profile.get("numbering_ids") or {})
    if not policy.bind_multilevel_numbering:
        return numbering_ids, []
    deeper_ordered_roles = [
        role for role in policy.deeper_roles() if role not in policy.unordered_roles
    ]
    if not deeper_ordered_roles:
        return numbering_ids, []
    catalog, abstract_by_num = _template_numbering_catalog(template_profile)
    if not catalog:
        return numbering_ids, []
    candidates = [
        (len(levels), num_id)
        for num_id, levels in catalog.items()
        if len(levels) > 1 and (levels.get(0) or {}).get("numFmt") in _ORDERED_NUMBER_FORMATS
    ]
    if not candidates:
        return numbering_ids, []
    deeper_num_id = max(candidates, key=lambda item: (item[0], -item[1]))[1]
    deeper_abstract_id = abstract_by_num.get(deeper_num_id)
    if deeper_abstract_id is None:
        return numbering_ids, []
    abstract_ids = _template_abstract_ids(template_profile)
    abstract_num_id = max(abstract_ids) + 1 if abstract_ids else 0
    num_id = max(max(catalog), max(abstract_by_num, default=0)) + 1
    level_zero_abstract_id: int | None = None
    level_zero_num_fmt: str | None = None
    level_zero_lvl_text: str | None = None
    letter_num_id = numbering_ids.get(policy.ordered_roles[0])
    try:
        letter_num_id = int(letter_num_id) if letter_num_id is not None else None
    except (TypeError, ValueError):
        letter_num_id = None
    if letter_num_id is not None:
        letter_abstract_id = abstract_by_num.get(letter_num_id)
        letter_level = (catalog.get(letter_num_id) or {}).get(0)
        if (
            letter_abstract_id is not None
            and letter_abstract_id != deeper_abstract_id
            and letter_level
        ):
            level_zero_abstract_id = letter_abstract_id
            level_zero_num_fmt = letter_level.get("numFmt") or None
            level_zero_lvl_text = letter_level.get("lvlText") or None
    for role in policy.ordered_roles:
        numbering_ids[role] = num_id
        numbering_ids[f"{role}_abstract"] = abstract_num_id
    return numbering_ids, [
        SynthesizedNumbering(
            num_id=num_id,
            abstract_num_id=abstract_num_id,
            deeper_abstract_num_id=deeper_abstract_id,
            level_zero_abstract_num_id=level_zero_abstract_id,
            level_zero_num_fmt=level_zero_num_fmt,
            level_zero_lvl_text=level_zero_lvl_text,
        )
    ]


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
    numbering_ids, synthesized_numbering = _numbering_plan(template_profile, policy)
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
        if node is None or not policy.chapter_scoped_sequence or node["marker_kind"] != "ordered":
            continue
        scope_id = node["scope_id"]
        counters[scope_id] = counters.get(scope_id, 0) + 1
        node["target_ordinal"] = counters[scope_id]
        node["restart"] = node["target_ordinal"] == 1

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
            "synthesized_numbering": [instance.as_payload() for instance in synthesized_numbering],
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
