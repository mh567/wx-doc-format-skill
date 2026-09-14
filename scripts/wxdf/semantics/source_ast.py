"""Generic SourceFacts to Source AST construction and list materialization.

The native chain builds its first Source AST revision here.  Neither function
performs recognition: structure, region, list, table, caption and appendix
semantics stay in their own ``semantics`` modules and publish candidates that
the transaction commits.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


def _image_alt_text(
    item: Mapping[str, Any],
    resource_ref: Mapping[str, Any],
    resource_id: Any,
) -> str:
    """Return a non-empty alt text for a materialized image node."""

    alt_text = str(item.get("alt_text") or "").strip()
    if alt_text:
        return alt_text
    part = str(resource_ref.get("part") or "")
    name = part.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return name or str(resource_id)


def _without_images(inline_content: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove image tokens while preserving the visible caption text."""

    result: list[dict[str, Any]] = []
    for item in inline_content:
        if item.get("kind") == "image":
            continue
        copied = copy.deepcopy(dict(item))
        if copied.get("kind") == "link":
            children = _without_images(copied.get("children", []))
            if not children:
                continue
            copied["children"] = children
        result.append(copied)
    return result


def _inline_image_nodes(
    unit_id: str,
    unit: Mapping[str, Any],
    resources: Mapping[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Build explicit image nodes for the images embedded in one paragraph.

    The paragraph keeps its text, its other inline tokens and its inline
    images, so the source projection stays intact.  Each image additionally
    becomes an ``image`` node that carries the reference of the image part it
    came from and records the source paragraph it was materialized from.
    Normal inline images remain owned by the paragraph.  A mixed authored
    figure caption changes that ownership explicitly during AST materialization.
    """

    source_ref = copy.deepcopy(dict(unit["source_ref"]))
    nodes: list[tuple[str, dict[str, Any]]] = []
    image_index = 0
    for inline_index, item in enumerate(unit.get("inline_content") or []):
        if item.get("kind") != "image":
            continue
        image_index += 1
        node_id = f"{unit_id}-image" if image_index == 1 else f"{unit_id}-image{image_index}"
        resource = resources.get(item.get("resource_id")) or {}
        resource_ref = resource.get("source_ref")
        resource_ref = resource_ref if isinstance(resource_ref, Mapping) else {}
        node = {
            "block_type": "image",
            "resource_id": item.get("resource_id"),
            "alt_text": _image_alt_text(item, resource_ref, item.get("resource_id")),
            "source_ref": copy.deepcopy(dict(resource_ref)) if resource_ref else copy.deepcopy(source_ref),
            "generated_by": "source_ast.inline_image",
            "generation_basis": [unit_id, inline_index],
        }
        for field in ("source_embedding", "display_size"):
            if field in item:
                node[field] = copy.deepcopy(item[field])
        nodes.append((
            node_id,
            node,
        ))
    return nodes


def _expand_children(
    facts: Mapping[str, Any],
    additions: Mapping[str, list[str]],
    prepend: set[str] | None = None,
) -> dict[str, list[str]]:
    """Place materialized nodes around their source paragraph."""

    prepend = prepend or set()
    children: dict[str, list[str]] = {}
    for parent_id, child_ids in facts["children"].items():
        expanded: list[str] = []
        for child_id in child_ids:
            image_ids = additions.get(str(child_id), [])
            if any(image_id in prepend for image_id in image_ids):
                expanded.extend(image_ids)
                expanded.append(child_id)
            else:
                expanded.append(child_id)
                expanded.extend(image_ids)
        children[parent_id] = expanded
    for node_ids in additions.values():
        for node_id in node_ids:
            children.setdefault(node_id, [])
    return children


def _caption_target(
    facts: Mapping[str, Any],
    target_id: str,
    image_targets: Mapping[str, list[str]],
) -> tuple[str, str] | None:
    """Resolve a SourceFacts caption target to its Source AST node kind."""

    if facts["containers"].get(target_id, {}).get("kind") == "table":
        return target_id, "table"
    images = image_targets.get(target_id)
    if images:
        return images[0], "figure"
    return None


def _authored_caption_materialization(
    facts: Mapping[str, Any],
    image_targets: Mapping[str, list[str]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Resolve authored caption roles and associations for the Source AST.

    Caption recognition stays in the caption semantics module.  This helper
    only consumes its public result, so that an authored caption becomes a
    real ``role == "caption"`` block with a valid target instead of a body
    paragraph that would later attract a generated caption.
    """

    from .captions import collect_source_caption_semantics
    from .tables import collect_source_table_semantics

    semantics = collect_source_caption_semantics(
        facts, collect_source_table_semantics(facts)
    )
    roles: dict[str, dict[str, Any]] = {}
    associations: list[dict[str, Any]] = []
    for caption in semantics.get("captions", []):
        if caption.get("status") != "associated":
            continue
        caption_id = str(caption.get("node_id") or "")
        if not caption_id:
            continue
        resolved = _caption_target(
            facts, str(caption.get("target_node_id") or ""), image_targets
        )
        if resolved is None:
            continue
        target_id, caption_type = resolved
        if caption_type != str(caption.get("caption_type") or ""):
            continue
        roles[caption_id] = {"caption_type": caption_type, "target_id": target_id}
        associations.append({
            "kind": "caption_target",
            "source_id": caption_id,
            "target_id": target_id,
        })
    return roles, associations


def build_source_ast(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Convert public SourceFacts to the first Source AST revision."""

    resources = facts["resources"]
    blocks: dict[str, dict[str, Any]] = {}
    image_targets: dict[str, list[str]] = {}
    materialized: dict[str, list[str]] = {}
    for unit_id, unit in facts["units"].items():
        kind = unit.get("kind")
        source_ref = copy.deepcopy(unit["source_ref"])
        if kind == "paragraph":
            role = str(unit.get("role") or "body")
            block: dict[str, Any] = {
                "block_type": "paragraph",
                "role": role,
                "inline_content": copy.deepcopy(unit.get("inline_content", [])),
                "source_ref": source_ref,
            }
            if role == "heading":
                block["level"] = int(unit.get("level") or 1)
            if isinstance(unit.get("source_paragraph_format"), Mapping):
                block["source_paragraph_format"] = copy.deepcopy(
                    dict(unit["source_paragraph_format"])
                )
            blocks[unit_id] = block
            image_nodes = _inline_image_nodes(str(unit_id), unit, resources)
            if image_nodes:
                image_targets[str(unit_id)] = [node_id for node_id, _ in image_nodes]
                materialized[str(unit_id)] = image_targets[str(unit_id)]
                for node_id, node in image_nodes:
                    blocks[node_id] = node
        elif kind in {"code_block", "opaque", "separator"}:
            blocks[unit_id] = {
                "block_type": "opaque",
                "diagnostic_ids": list(unit.get("diagnostic_ids") or [f"source_{kind}"]),
                "raw_text": str(unit.get("raw_text") or ""),
                "source_ref": source_ref,
            }
        else:
            blocks[unit_id] = {
                "block_type": "opaque",
                "diagnostic_ids": [f"unsupported_source_unit:{kind}"],
                "source_ref": source_ref,
            }

    caption_roles, caption_associations = _authored_caption_materialization(
        facts, image_targets
    )
    standalone_images: set[str] = set()
    for caption_id, caption_role in caption_roles.items():
        block = blocks.get(caption_id)
        if block is None or block.get("block_type") != "paragraph":
            continue
        # A mixed authored figure caption is split into a standalone image
        # node followed by a text-only caption node.  The image ownership is
        # explicit in the Source AST so later stages do not infer it from
        # renderer behavior.
        if caption_role["caption_type"] == "figure" and caption_id in materialized:
            block["inline_content"] = _without_images(block.get("inline_content", []))
            for image_id in materialized[caption_id]:
                blocks[image_id]["materialization_mode"] = "standalone"
                standalone_images.add(image_id)
        block["role"] = "caption"
        block["caption_type"] = caption_role["caption_type"]
        block["target_id"] = caption_role["target_id"]
        block.pop("level", None)

    containers: dict[str, dict[str, Any]] = {}
    for container_id, container in facts["containers"].items():
        kind = container.get("kind")
        if kind == "table":
            table = {"block_type": "table", "table_type": "data"}
            table_semantics = container.get("table_semantics")
            if isinstance(table_semantics, Mapping):
                table["table_type"] = str(table_semantics.get("table_type") or "data")
                if "header_rows" in table_semantics:
                    table["header_rows"] = int(table_semantics.get("header_rows") or 0)
            if isinstance(container.get("layout"), Mapping):
                table["layout"] = copy.deepcopy(dict(container["layout"]))
            if "source_ref" in container:
                table["source_ref"] = copy.deepcopy(container["source_ref"])
            else:
                table["generated_by"] = "source_facts_adapter"
                table["generation_basis"] = [container_id]
            blocks[container_id] = table
            continue
        containers[container_id] = copy.deepcopy(dict(container))
    containers.setdefault("root", {"kind": "document"})
    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": blocks,
            "containers": containers,
            "children": _expand_children(facts, materialized, standalone_images),
            "associations": copy.deepcopy(caption_associations),
            "resources": copy.deepcopy(facts["resources"]),
        },
    }
    from ..validation import validate_ast

    validate_ast(ast)
    return ast


def _normalized_list_marker(
    relation: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> str:
    marker = relation.get("source_marker") or evidence.get("marker_text")
    if isinstance(marker, str) and marker.strip():
        return marker

    source_list_type = str(
        relation.get("source_list_type") or evidence.get("source_list_type") or ""
    ).lower()
    list_kind = str(relation.get("list_kind") or "").lower()
    if source_list_type == "dash":
        return "-"
    if source_list_type in {"bullet", "bullet_dot"} or list_kind == "unordered":
        return "•"

    ordinal = relation.get("source_ordinal") or evidence.get("ordinal") or 1
    try:
        normalized_ordinal = max(1, int(ordinal))
    except (TypeError, ValueError):
        normalized_ordinal = 1
    if source_list_type.startswith("lower_letter"):
        letters = ""
        while normalized_ordinal:
            normalized_ordinal, remainder = divmod(normalized_ordinal - 1, 26)
            letters = chr(ord("a") + remainder) + letters
        return f"{letters})"
    suffix = ")" if source_list_type.endswith("_paren") else "."
    return f"{normalized_ordinal}{suffix}"


def materialize_list_semantics(
    source_ast: Mapping[str, Any],
    facts: Mapping[str, Any],
    list_result: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    """Build list structure operations without changing the source revision."""

    ast = copy.deepcopy(dict(source_ast))
    document = ast["document"]
    blocks = document["blocks"]
    containers = document["containers"]
    children = document["children"]
    nodes = {**blocks, **containers}
    original_parents = {
        child_id: parent_id
        for parent_id, child_ids in children.items()
        for child_id in child_ids
    }
    original_positions = {
        (parent_id, child_id): index
        for parent_id, child_ids in children.items()
        for index, child_id in enumerate(child_ids)
    }
    list_specs = {str(item["list_id"]): item for item in list_result.get("lists", [])}
    item_records: dict[str, list[Mapping[str, Any]]] = {}
    for relation in list_result.get("items", []):
        item_records.setdefault(str(relation["list_id"]), []).append(relation)

    item_map: dict[str, str] = {}
    for relation in list_result.get("items", []):
        source_item_id = str(relation["item_id"])
        source_node = containers.get(source_item_id)
        if isinstance(source_node, Mapping) and source_node.get("kind") == "list_item":
            item_map[source_item_id] = source_item_id
            continue
        item_id = f"native-list-item-{source_item_id}"
        while item_id in nodes or item_id in item_map.values():
            item_id = f"{item_id}-copy"
        item_map[source_item_id] = item_id

    def source_node(source_id: str) -> Mapping[str, Any]:
        return facts.get("units", {}).get(source_id) or facts.get("containers", {}).get(source_id) or {}

    def source_ref(source_id: str) -> dict[str, Any] | None:
        value = source_node(source_id).get("source_ref")
        return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None

    def parent_for_list(list_id: str, spec: Mapping[str, Any], records: list[Mapping[str, Any]]) -> tuple[str, int]:
        parent_item_id = spec.get("parent_item_id")
        if parent_item_id is not None:
            parent_id = item_map.get(str(parent_item_id), str(parent_item_id))
            return parent_id, len(children.get(parent_id, []))
        first_source_id = str(records[0]["item_id"]) if records else ""
        parent_id = original_parents.get(first_source_id, document["root_id"])
        if parent_id in item_map:
            parent_id = item_map[parent_id]
        return parent_id, original_positions.get((parent_id, first_source_id), len(children.get(parent_id, [])))

    candidates: list[dict[str, Any]] = []
    for list_id, spec in list_specs.items():
        records = item_records.get(list_id, [])
        if list_id not in containers:
            parent_id, index = parent_for_list(list_id, spec, records)
            first_source_id = str(records[0]["item_id"]) if records else ""
            list_node: dict[str, Any] = {
                "kind": "list",
                "list_kind": str(spec.get("list_kind") or "ordered"),
                "source_start": max(1, int(spec.get("source_start") or 1)),
                "source_sequence_id": str(spec.get("source_sequence_id") or list_id),
            }
            if isinstance(spec.get("group_boundary"), Mapping):
                list_node["group_boundary"] = copy.deepcopy(dict(spec["group_boundary"]))
            ref = source_ref(first_source_id)
            if ref is not None:
                list_node["source_ref"] = ref
            candidates.append({
                "candidate_id": f"list-structure-{list_id}",
                "module_id": "list_semantics",
                "module_version": "1.0",
                "target_source_refs": [ref] if ref is not None else [],
                "target_node_ids": [list_id],
                "proposal": {
                    "operation": "create_node",
                    "property_scope": ["children"],
                    "value": {"node_id": list_id, "parent_id": parent_id, "index": index, "node": list_node},
                },
                "positive_evidence": [{"kind": "structural", "source": "source_facts_list_structure"}],
                "negative_evidence": [],
                "scope": {"stages": ["relation"]},
                "constraints": [],
                "rationale": "list container derived from immutable source facts",
            })

        for index, relation in enumerate(records):
            source_item_id = str(relation["item_id"])
            item_id = item_map[source_item_id]
            existing_item = item_id in containers and containers[item_id].get("kind") == "list_item"
            if not existing_item:
                unit = source_node(source_item_id)
                evidence = facts.get("evidence", {}).get(source_item_id, {})
                item_node: dict[str, Any] = {
                    "kind": "list_item",
                    "source_marker": _normalized_list_marker(relation, evidence),
                    "source_ordinal": relation.get("source_ordinal") or evidence.get("ordinal"),
                    "source_sequence_id": relation.get("source_sequence_id") or source_item_id,
                }
                if isinstance(unit.get("source_ref"), Mapping):
                    item_node["source_ref"] = copy.deepcopy(dict(unit["source_ref"]))
                candidates.append({
                    "candidate_id": f"list-item-structure-{source_item_id}",
                    "module_id": "list_semantics",
                    "module_version": "1.0",
                    "target_source_refs": [source_ref(source_item_id)] if source_ref(source_item_id) else [],
                    "target_node_ids": [item_id],
                    "proposal": {
                        "operation": "create_node",
                        "property_scope": ["children"],
                        "value": {"node_id": item_id, "parent_id": list_id, "index": index, "node": item_node},
                    },
                    "positive_evidence": [{"kind": "structural", "source": "source_facts_list_item"}],
                    "negative_evidence": [],
                    "scope": {"stages": ["relation"]},
                    "constraints": [],
                    "rationale": "list item owner derived from immutable source facts",
                })

    for candidate in list_result.get("candidates", []):
        mapped = copy.deepcopy(dict(candidate))
        target_ids = [str(node_id) for node_id in candidate.get("target_node_ids", [])]
        if len(target_ids) != 1:
            candidates.append(mapped)
            continue
        source_item_id = target_ids[0]
        item_id = item_map.get(source_item_id, source_item_id)
        proposal = dict(mapped.get("proposal") or {})
        value = dict(proposal.get("value") or {})
        list_id = str(value.get("list_id") or value.get("new_parent_id") or "")
        existing_item = item_id in containers and containers[item_id].get("kind") == "list_item"
        value["new_parent_id"] = list_id if existing_item else item_id
        value["list_id"] = list_id
        if value.get("parent_item_id") is not None:
            value["parent_item_id"] = item_map.get(str(value["parent_item_id"]), str(value["parent_item_id"]))
        value["index"] = next(
            (index for index, relation in enumerate(item_records.get(list_id, [])) if str(relation.get("item_id")) == source_item_id),
            0,
        ) if existing_item else 0
        proposal["value"] = value
        mapped["proposal"] = proposal
        mapped["target_node_ids"] = [source_item_id]
        candidates.append(mapped)

    existing_associations = {
        (str(association.get("kind")), str(association.get("source_id")), str(association.get("target_id")))
        for association in document.get("associations", [])
        if isinstance(association, Mapping)
    }
    continuation_tails: dict[str, str] = {}
    for continuation in list_result.get("continuations", []):
        source_item_id = str(continuation.get("owner_item_id"))
        source_id = continuation_tails.get(source_item_id, item_map.get(source_item_id, source_item_id))
        target_id = str(continuation.get("node_id"))
        association = {
            "kind": "sequence_continuation",
            "source_id": source_id,
            "target_id": target_id,
        }
        if (association["kind"], source_id, target_id) in existing_associations:
            continue
        target_ref = source_ref(target_id)
        candidates.append({
            "candidate_id": f"list-continuation-{source_id}-{target_id}",
            "module_id": "list_semantics",
            "module_version": "1.0",
            "target_source_refs": [target_ref] if target_ref is not None else [],
            "target_node_ids": [target_id],
            "proposal": {
                "operation": "update_association",
                "property_scope": ["association"],
                "value": {"association": association},
            },
            "positive_evidence": [{"kind": "structural", "source": "source_facts_list_continuation"}],
            "negative_evidence": [],
            "scope": {"stages": ["relation"], "scope_id": continuation.get("scope_id")},
            "constraints": [{"kind": "preserve_list_owner", "owner_item_id": source_id}],
            "rationale": "continuation association derived from list item ownership",
        })
        continuation_tails[source_item_id] = target_id

    for relation in list_result.get("items", []):
        if not relation.get("generated_anchor_required"):
            continue
        source_item_id = str(relation.get("item_id"))
        item_id = item_map.get(source_item_id, source_item_id)
        anchor_id = f"generated-anchor-{item_id}"
        if anchor_id in nodes:
            continue
        anchor_reason = str(relation.get("anchor_reason") or "list_item_anchor")
        anchor_node = {
            "block_type": "paragraph",
            "role": "body",
            "inline_content": [],
            "generated_by": "list_semantics.marker_anchor",
            "generation_basis": [source_item_id, anchor_reason],
        }
        anchor_ref = source_ref(source_item_id)
        candidates.append({
            "candidate_id": f"list-anchor-{item_id}",
            "module_id": "list_semantics",
            "module_version": "1.0",
            "target_source_refs": [anchor_ref] if anchor_ref is not None else [],
            "target_node_ids": [anchor_id],
            "proposal": {
                "operation": "create_node",
                "property_scope": ["children"],
                "value": {"node_id": anchor_id, "parent_id": item_id, "index": 0, "node": anchor_node},
            },
            "positive_evidence": [{"kind": "structural", "source": "source_facts_list_anchor"}],
            "negative_evidence": [],
            "scope": {"stages": ["relation"], "scope_id": relation.get("scope_id")},
            "constraints": [{"kind": "single_marker_anchor", "reason": anchor_reason}],
            "rationale": "generated marker anchor required by list semantics",
        })

    from ..validation import validate_ast

    validate_ast(ast)
    return ast, candidates, item_map


__all__ = ["build_source_ast", "materialize_list_semantics"]
