"""Deterministic Source AST normalization and presentation planning."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .digest import digest
from .list_policy import LIST_POLICIES, ListPolicy, ListProjection
from .model import build_indexes, is_body_outline_heading
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
            # The item's own paragraph carries the marker, empty or not.  An empty
            # numbered cell is still that paragraph, so generating a second one
            # would add a stray paragraph to the cell.
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


_INERT_INLINE_KINDS = frozenset({"bookmark", "break", "tab"})


def _inline_content_is_one_image(items: list[Mapping[str, Any]]) -> bool:
    """Return whether inline content carries an image and nothing else the author wrote.

    An image paragraph is a paragraph whose content is the image, so a paragraph
    that mixes text with an image is left to the template style.  Anchors and
    spacing are inert, and a link is scanned because the renderer draws an image
    nested inside a hyperlink too.
    """

    has_image = False
    for item in items:
        if not isinstance(item, Mapping):
            return False
        kind = item.get("kind")
        if kind == "image":
            has_image = True
        elif kind == "text":
            if str(item.get("text") or "").strip():
                return False
        elif kind == "link":
            if not _inline_content_is_one_image(list(item.get("children") or [])):
                return False
            has_image = True
        elif kind not in _INERT_INLINE_KINDS:
            return False
    return has_image


def _table_owned_nodes(document: Mapping[str, Any]) -> set[str]:
    """Return every node a table owns.

    A table cell's paragraph is formatted by the table's own contract, which a
    per-paragraph presentation record must not compete with.  A semantic cell is
    centred by the 表正文 style, and a layout cell keeps the source paragraph
    format the layout table restored (AGENTS.md decision 5), so the paragraph
    presentation stays out of both.
    """

    blocks = document.get("blocks") or {}
    containers = document.get("containers") or {}
    children = document.get("children") or {}
    owned: set[str] = set()
    pending = [
        str(node_id)
        for node_id, block in blocks.items()
        if isinstance(block, Mapping) and block.get("block_type") == "table"
    ]
    while pending:
        node_id = pending.pop()
        for child_id in children.get(node_id) or []:
            child_key = str(child_id)
            if child_key in owned:
                continue
            owned.add(child_key)
            if child_key in containers:
                pending.append(child_key)
    return owned


def _paragraph_presentation(ast: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the presentation each non-list paragraph owns.

    An image paragraph inherits no style, so its centred alignment is the whole
    of its presentation (AGENTS.md decision 2).  The template's own image
    paragraphs carry no style and ``jc=center``, which is what this record
    reproduces.  A formula number is right aligned in the source and no style
    says so, so that alignment is recorded too.  Any other alignment belongs to
    the template style, not to the source.

    An appendix title carries the outline level the appendix itself has.  The
    template's appendix title style sits at outline level 1, which the template's
    ``TOC \\o "1-3"`` field renders as a second-level entry, while both the
    template's and the source's cached tables of contents show it at level 1, so
    the record states the level the delivered title needs (decision 7, ruling of
    2026-09-16).

    ``outline_level`` here is a direct paragraph attribute the renderer writes,
    while ``presentation.outline[*].outline_level`` selects a heading style.  The
    two maps are consumed by different paths and no block carries both, because
    appendix blocks stay outside the body outline contract
    (``model.is_body_outline_heading``).
    """

    document = ast["document"]
    table_owned = _table_owned_nodes(document)
    result: dict[str, dict[str, Any]] = {}
    for node_id, block in document["blocks"].items():
        if not isinstance(block, Mapping):
            continue
        if block.get("appendix_role") == "title" and str(node_id) not in table_owned:
            result[str(node_id)] = {"outline_level": 0}
            continue
        if block.get("block_type") == "image":
            result[str(node_id)] = {"alignment": "center"}
            continue
        if block.get("block_type") != "paragraph":
            continue
        if block.get("role") != "body":
            continue
        if (
            str(node_id) not in table_owned
            and _inline_content_is_one_image(list(block.get("inline_content") or []))
        ):
            # A body paragraph whose content is the image is the image paragraph
            # the template centres.  A paragraph with its own style (heading,
            # caption, note) keeps that style's alignment, and the inline image
            # stays in the body paragraph that draws it.
            result[str(node_id)] = {"alignment": "center"}
            continue
        if block.get("semantic_role") != "formula_number":
            continue
        source_format = block.get("source_paragraph_format")
        alignment = source_format.get("alignment") if isinstance(source_format, Mapping) else None
        if isinstance(alignment, str) and alignment:
            result[str(node_id)] = {"alignment": alignment}
    return result


def _body_outline_overrides(ast: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Derive the outline level each body heading renders at.

    The WX outline descends one level at a time, while a source document may
    declare any heading level it likes, so a paragraph styled ``heading 5`` can
    follow one styled ``heading 2`` directly.  ``level`` keeps that source
    semantic level and the presentation record carries the derived level the
    template renders, which is how appendix semantics already separates a
    source heading level from its rendered one.

    The deepest derivable level is ``min(6, previous + 1)``, and the cursor
    follows the derived level, so one correction cascades instead of being
    re-derived from the source value.  Appendix headings render through their
    own relative level, so they are skipped and leave the cursor untouched,
    exactly like ``auditing.check_model``.  Only headings whose derived level
    differs from the source level get a record, so a document whose outline is
    already well formed keeps its previous presentation nodes byte for byte.
    """

    document = ast["document"]
    blocks = document["blocks"]
    overrides: dict[str, dict[str, int]] = {}
    previous = 0
    for node_id in _reading_order(ast):
        block = blocks.get(node_id)
        if not isinstance(block, Mapping) or not is_body_outline_heading(block):
            continue
        level = block.get("level")
        if not isinstance(level, int) or isinstance(level, bool):
            continue
        derived = min(6, previous + 1)
        outline_level = derived if level > derived else level
        if outline_level != level:
            overrides[str(node_id)] = {"outline_level": outline_level, "source_level": level}
        previous = outline_level
    return overrides


def _close_dropped_references(document: dict[str, Any], dropped: set[str]) -> None:
    """Drop the nodes and references a dropped subtree leaves behind.

    The source AST names other nodes in three places: ``document.associations``,
    a caption's ``target_id`` and an appendix container's ``title_ref``.  An
    association naming a dropped node has nothing left to describe, a caption
    whose table or figure the exclusion removed has no meaning, and an appendix
    whose title left the document has no range to open.  The closure repeats
    until no remaining node points into the dropped set.
    """

    while True:
        dependent = {
            str(node_id)
            for node_id, block in document["blocks"].items()
            if str(node_id) not in dropped
            and isinstance(block, Mapping)
            and block.get("role") == "caption"
            and block.get("target_id") is not None
            and str(block["target_id"]) in dropped
        }
        dependent |= {
            str(container_id)
            for container_id, container in document["containers"].items()
            if str(container_id) not in dropped
            and isinstance(container, Mapping)
            and container.get("title_ref") is not None
            and str(container["title_ref"]) in dropped
        }
        if not dependent:
            break
        dropped.update(dependent)
    associations = document.get("associations")
    if isinstance(associations, list):
        document["associations"] = [
            association
            for association in associations
            if isinstance(association, Mapping)
            and str(association.get("source_id")) not in dropped
            and str(association.get("target_id")) not in dropped
        ]


def _drop_excluded_blocks(ast: dict[str, Any], overlay: Mapping[str, Any] | None) -> None:
    """Remove every block the scope module excluded from the rendered body.

    A source region such as the document's own table of contents is identified
    so the template's replacement is the only one that renders.  The scope
    module records that as an overlay, so the normalized view drops the subtree
    and the committed Source AST keeps it untouched.
    """

    excluded = {
        str(node_id)
        for node_id, values in (overlay or {}).items()
        if isinstance(values, Mapping) and values.get("semantic_scope") == "excluded"
    }
    if not excluded:
        return
    document = ast["document"]
    children = document["children"]
    dropped: set[str] = set()

    def collect(node_id: str, *, inside_excluded: bool) -> None:
        drop = inside_excluded or node_id in excluded
        if drop:
            dropped.add(node_id)
        for child_id in children.get(node_id, []):
            collect(child_id, inside_excluded=drop)

    collect(document["root_id"], inside_excluded=False)
    _close_dropped_references(document, dropped)
    for parent_id in list(children):
        if parent_id in dropped:
            del children[parent_id]
            continue
        children[parent_id] = [child_id for child_id in children[parent_id] if child_id not in dropped]
    for node_id in dropped:
        document["blocks"].pop(node_id, None)
        document["containers"].pop(node_id, None)


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
    # The scope module decides which source regions the template replaces, so the
    # rendered body drops them before anything else reads the model.
    _drop_excluded_blocks(ast, overlay)
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
        # The marker paragraph may already exist as a generated anchor the list
        # semantics materialized, so the flag reports the paragraph's origin
        # rather than only the node this stage created.
        marker_block = (ast["document"].get("blocks") or {}).get(marker_anchor) or {}
        marker_generated = bool(generated) or str(marker_block.get("generated_by") or "").endswith("marker_anchor")
        # A table's own numbering column keeps the cell style and the template's
        # plain decimal instance (1, 2, 3) instead of a WX list style.
        numbering_only = str(container.get("numbering_role") or "") == "table_number"
        item_by_node[item_id] = {
            "marker_anchor": marker_anchor,
            "target_sequence": list_info["numbering_scope_id"] if policy.chapter_scoped_sequence else list_info["source_sequence_id"],
            "target_level": 0 if numbering_only else projection.render_level,
            "semantic_level": list_info["depth"],
            "render_level_clamped": projection.clamped,
            "marker_kind": list_info["list_kind"],
            "source_marker": container.get("source_marker"),
            "source_ordinal": container.get("source_ordinal"),
            "source_start": list_info["source_start"],
            "target_ordinal": None if list_info["list_kind"] == "unordered" else (None if policy.chapter_scoped_sequence else container.get("source_ordinal")),
            "restart": False,
            "style_role": None if numbering_only else projection.style_role,
            "scope_id": list_info["scope_id"],
            "numbering_scope_id": list_info["numbering_scope_id"],
            "semantic_scope_id": list_info["semantic_scope_id"],
            "generated_anchor": marker_generated,
        }
        if numbering_only:
            item_by_node[item_id]["numbering_only"] = True
            item_by_node[item_id]["numbering_instance"] = numbering_ids.get("table_number")

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
        # ``nodes`` stays the list-item presentation: several audits read it as
        # the set of rendered list items.  The body outline normalization is a
        # separate mapping of heading node id to the level it renders at.
        "nodes": item_by_node,
        "outline": _body_outline_overrides(ast),
        "paragraphs": _paragraph_presentation(ast),
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
