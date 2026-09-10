"""Generic SourceFacts to Source AST construction and list materialization.

The native chain builds its first Source AST revision here.  Neither function
performs recognition: structure, region, list, table, caption and appendix
semantics stay in their own ``semantics`` modules and publish candidates that
the transaction commits.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


def build_source_ast(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Convert public SourceFacts to the first Source AST revision."""

    units: dict[str, dict[str, Any]] = {}
    for unit_id, unit in facts["units"].items():
        kind = unit.get("kind")
        source_ref = copy.deepcopy(unit["source_ref"])
        if kind == "paragraph":
            block: dict[str, Any] = {
                "block_type": "paragraph",
                "role": unit.get("role") or "body",
                "inline_content": copy.deepcopy(unit.get("inline_content", [])),
                "source_ref": source_ref,
            }
            if block["role"] == "heading":
                block["level"] = int(unit.get("level") or 1)
            units[unit_id] = block
        elif kind in {"code_block", "opaque", "separator"}:
            units[unit_id] = {
                "block_type": "opaque",
                "diagnostic_ids": list(unit.get("diagnostic_ids") or [f"source_{kind}"]),
                "raw_text": str(unit.get("raw_text") or ""),
                "source_ref": source_ref,
            }
        else:
            units[unit_id] = {
                "block_type": "opaque",
                "diagnostic_ids": [f"unsupported_source_unit:{kind}"],
                "source_ref": source_ref,
            }

    blocks = dict(units)
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
            "children": copy.deepcopy(facts["children"]),
            "associations": [],
            "resources": copy.deepcopy(facts["resources"]),
        },
    }
    from ..validation import validate_ast

    validate_ast(ast)
    return ast


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
                    "source_marker": relation.get("source_marker") or evidence.get("marker_text"),
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
