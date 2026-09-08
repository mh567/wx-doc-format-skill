"""Render operations derived from the normalized semantic view."""

from __future__ import annotations

from typing import Any, Mapping


def build_render_plan(normalized: Mapping[str, Any]) -> dict[str, Any]:
    ast = normalized["source_ast"]
    document = ast["document"]
    presentation = normalized.get("presentation", {}).get("nodes", {})
    operations: list[dict[str, Any]] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            if child_id in document["containers"]:
                container = document["containers"][child_id]
                if container.get("kind") == "list_item":
                    operations.append({"kind": "list_item", "node_id": child_id, "presentation": presentation.get(child_id, {})})
                walk(child_id)
            elif child_id in document["blocks"]:
                block = document["blocks"][child_id]
                operations.append({"kind": block.get("block_type"), "node_id": child_id, "role": block.get("role")})
                walk(child_id)

    walk(document["root_id"])
    generated = [
        {"kind": "generated_anchor", "node_id": node["node_id"], "generated_by": node["generated_by"]}
        for node in normalized.get("generated_nodes", [])
    ]
    return {
        "schema_version": "1.0",
        "source_revision": normalized["source_revision"],
        "strategy": normalized.get("presentation", {}).get("strategy"),
        "operations": operations,
        "generated_operations": generated,
        "diagnostics": list(normalized.get("diagnostics", [])),
    }
