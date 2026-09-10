"""Caption associations, placement and validated content overlays."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping

from text_utils import caption_parts

from ..model import inline_text_projection
from ..validation import validate_ast
from ..validation import validate_source_facts


_RAW_NUMBER_RE = re.compile(r"^\s*(?:表|图)\s*(?P<number>(?:[A-Z]\s*[.．]\s*)?\d+(?:\s*[.．]\s*\d+)*)")


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reading_order(ast: Mapping[str, Any]) -> list[str]:
    document = ast["document"]
    result: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            result.append(child_id)
            walk(child_id)

    walk(document["root_id"])
    return result


def _target_digest(ast: Mapping[str, Any], target_id: str) -> str:
    block = ast["document"]["blocks"].get(target_id)
    if block is None:
        raise ValueError(f"unknown caption target {target_id}")
    return _digest({"node_id": target_id, "block": block})


def _association_digest(ast: Mapping[str, Any], target_id: str) -> str:
    associations = [
        association
        for association in ast["document"].get("associations", [])
        if association.get("kind") == "caption_target" and association.get("target_id") == target_id
    ]
    return _digest(sorted(associations, key=lambda item: (str(item.get("source_id")), str(item.get("target_id")))))


def build_caption_overlay(ast: Mapping[str, Any], target_id: str, text: str, *, capability_version: str = "caption_gen@1.0") -> dict[str, Any]:
    """Build a validated overlay without changing the Source AST."""

    validate_ast(ast)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("caption text must be non-empty")
    target = ast["document"]["blocks"].get(target_id)
    if not target or target.get("block_type") not in {"table", "image"}:
        raise ValueError("caption target must be a table or image")
    return {
        "schema_version": "1.0",
        "kind": "caption_text",
        "target_node_id": target_id,
        "text": text,
        "target_content_sha256": _target_digest(ast, target_id),
        "association_sha256": _association_digest(ast, target_id),
        "association_revision": ast["revision"],
        "capability_version": capability_version,
        "validated": True,
    }


def invalidate_overlay(overlay: Mapping[str, Any], ast: Mapping[str, Any]) -> bool:
    """Return whether target content or association revision invalidates overlay."""

    target_id = overlay.get("target_node_id")
    try:
        digest_changed = overlay.get("target_content_sha256") != _target_digest(ast, str(target_id))
    except ValueError:
        return True
    association_changed = overlay.get("association_sha256") not in {None, _association_digest(ast, str(target_id))}
    return bool(digest_changed or association_changed)


def audit_caption_placement(ast: Mapping[str, Any]) -> dict[str, Any]:
    validate_ast(ast)
    document = ast["document"]
    order = _reading_order(ast)
    indexes = {node_id: index for index, node_id in enumerate(order)}
    associations: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for caption_id, caption in document["blocks"].items():
        if caption.get("role") != "caption":
            continue
        target_id = str(caption.get("target_id") or "")
        target = document["blocks"].get(target_id)
        if target is None:
            issues.append({"code": "caption_target_missing", "caption_id": caption_id})
            continue
        expected = "before" if target.get("block_type") == "table" else "after"
        actual = "before" if indexes.get(caption_id, -1) < indexes.get(target_id, -1) else "after"
        if actual != expected:
            issues.append({"code": "caption_placement_violation", "caption_id": caption_id, "target_id": target_id, "expected": expected, "actual": actual})
        associations.append({"caption_id": caption_id, "target_id": target_id, "caption_type": caption.get("caption_type"), "placement": actual})
    return {"passed": not issues, "issues": issues, "associations": associations}


def _caption_block(parent_id: str, index: int, caption_id: str, target_id: str, caption_type: str) -> dict[str, Any]:
    """Build the generated caption node for one eligible target."""

    return {
        "operation": "create_node",
        "property_scope": ["children"],
        "value": {
            "node_id": caption_id,
            "parent_id": parent_id,
            "index": index,
            "node": {
                "block_type": "paragraph",
                "role": "caption",
                "caption_type": caption_type,
                "target_id": target_id,
                "inline_content": [],
                "generated_by": "caption_semantics.sequence_caption",
                "generation_basis": [target_id],
            },
        },
    }


def _generated_caption_candidates(
    ast: Mapping[str, Any],
    target_id: str,
    caption_type: str,
    *,
    evidence_source: str,
    constraint: str,
) -> list[dict[str, Any]]:
    """Create one caption node and its caption_target association.

    A caption eligible target without an authored caption still needs a
    caption node so that sequence numbering and caption placement stay
    explicit in the Source AST.  The node is inserted immediately before a
    table and immediately after a figure.
    """

    document = ast["document"]
    blocks = document["blocks"]
    containers = document["containers"]
    children = document["children"]
    parent_id = next(
        (
            candidate_parent
            for candidate_parent, child_ids in children.items()
            if target_id in child_ids
        ),
        document["root_id"],
    )
    sibling_ids = list(children.get(parent_id, []))
    position = sibling_ids.index(target_id) if target_id in sibling_ids else len(sibling_ids)
    # A table caption sits above its table, a figure caption sits below.
    insert_at = position + 1 if caption_type == "figure" else position
    caption_id = f"native-caption-{target_id}"
    suffix = 2
    while caption_id in blocks or caption_id in containers:
        caption_id = f"native-caption-{target_id}-{suffix}"
        suffix += 1
    target_ref = blocks[target_id].get("source_ref")
    source_refs = [copy.deepcopy(dict(target_ref))] if isinstance(target_ref, Mapping) else []
    return [
        {
            "candidate_id": f"caption-node-{caption_id}",
            "module_id": "caption_semantics",
            "module_version": "1.0",
            "target_source_refs": source_refs,
            "target_node_ids": [caption_id],
            "proposal": _caption_block(
                parent_id,
                insert_at,
                caption_id,
                target_id,
                caption_type,
            ),
            "positive_evidence": [{"kind": "format_contract", "source": evidence_source}],
            "negative_evidence": [],
            "scope": {"stage": "relation"},
            "constraints": [{"kind": constraint}],
            "rationale": "caption node generated for a caption eligible target",
            "confidence": None,
        },
        {
            "candidate_id": f"caption-association-{caption_id}",
            "module_id": "caption_semantics",
            "module_version": "1.0",
            # The association binds the caption to its target, so the
            # precondition carries the target reference.  A generated caption
            # node has no source reference of its own.
            "target_source_refs": source_refs,
            "target_node_ids": [target_id],
            "proposal": {
                "operation": "update_association",
                "property_scope": ["association"],
                "value": {
                    "association": {
                        "kind": "caption_target",
                        "source_id": caption_id,
                        "target_id": target_id,
                    }
                },
            },
            "positive_evidence": [{"kind": "format_contract", "source": evidence_source}],
            "negative_evidence": [],
            "scope": {"stage": "relation"},
            "constraints": [{"kind": constraint}],
            "rationale": "caption target association derived from caption eligibility",
            "confidence": None,
        },
    ]


def _authored_caption_role_candidates(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Assert the caption role for captions materialized in the Source AST.

    Structure recognition proposes a default role for every paragraph, so an
    authored caption restates its role here.  Its ``caption_type`` and
    ``target_id`` come from the Source AST build.
    """

    result: list[dict[str, Any]] = []
    for node_id, block in ast["document"]["blocks"].items():
        if block.get("role") != "caption":
            continue
        if not block.get("target_id"):
            continue
        source_ref = block.get("source_ref")
        result.append({
            "candidate_id": f"caption-role-{node_id}",
            "module_id": "caption_semantics",
            "module_version": "1.0",
            "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
            "target_node_ids": [node_id],
            "proposal": {
                "operation": "update_role",
                "property_scope": ["role"],
                "value": {"role": "caption"},
            },
            "positive_evidence": [{"kind": "format_contract", "source": "authored_caption_role"}],
            "negative_evidence": [],
            "scope": {"stage": "relation"},
            "constraints": [{"kind": "preserve_caption_target"}],
            "rationale": "authored caption keeps its caption role against the default paragraph role",
            "confidence": None,
        })
    return result


def _captioned_targets(ast: Mapping[str, Any]) -> set[str]:
    return {
        str(block.get("target_id"))
        for block in ast["document"]["blocks"].values()
        if block.get("role") == "caption" and block.get("target_id")
    }


def collect_caption_candidates(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_ast(ast)
    result: list[dict[str, Any]] = _authored_caption_role_candidates(ast)
    captioned = _captioned_targets(ast)
    for node_id, block in ast["document"]["blocks"].items():
        if block.get("block_type") == "table" and block.get("table_type") == "data":
            result.append({"candidate_id": f"caption-target-{node_id}", "module_id": "caption_semantics", "module_version": "1.0", "target_source_refs": [copy.deepcopy(dict(block["source_ref"]))] if isinstance(block.get("source_ref"), Mapping) else [], "target_node_ids": [node_id], "proposal": {"operation": "create_overlay", "property_scope": ["overlay"], "value": {"caption_type": "table", "eligible": True}}, "positive_evidence": [{"kind": "format_contract", "source": "data_table_caption_eligibility"}], "negative_evidence": [], "scope": {"stage": "relation"}, "constraints": [{"kind": "caption_above_table"}], "rationale": "data table is caption eligible", "confidence": None})
            if node_id not in captioned:
                result.extend(_generated_caption_candidates(
                    ast,
                    node_id,
                    "table",
                    evidence_source="data_table_caption_eligibility",
                    constraint="caption_above_table",
                ))
        elif block.get("block_type") == "image":
            result.append({"candidate_id": f"caption-target-{node_id}", "module_id": "caption_semantics", "module_version": "1.0", "target_source_refs": [copy.deepcopy(dict(block["source_ref"]))] if isinstance(block.get("source_ref"), Mapping) else [], "target_node_ids": [node_id], "proposal": {"operation": "create_overlay", "property_scope": ["overlay"], "value": {"caption_type": "figure", "eligible": True}}, "positive_evidence": [{"kind": "format_contract", "source": "image_caption_eligibility"}], "negative_evidence": [], "scope": {"stage": "relation"}, "constraints": [{"kind": "caption_below_figure"}], "rationale": "image is figure caption eligible", "confidence": None})
            if node_id not in captioned:
                result.extend(_generated_caption_candidates(
                    ast,
                    node_id,
                    "figure",
                    evidence_source="image_caption_eligibility",
                    constraint="caption_below_figure",
                ))
    return result


def _source_location(facts: Mapping[str, Any], node_id: str) -> dict[str, Any] | None:
    evidence = facts.get("evidence", {}).get(node_id, {})
    for key in ("source_position", "line_start"):
        value = evidence.get(key)
        if isinstance(value, int) and value >= 0:
            return {"kind": key, "value": value}
    return None


def _raw_number(text: str) -> str | None:
    match = _RAW_NUMBER_RE.match(text)
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group("number")).replace("．", ".")


def collect_source_caption_semantics(
    facts: Mapping[str, Any],
    table_semantics: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve authored caption roles and target relationships from SourceFacts."""

    validate_source_facts(facts)
    tables = {str(item.get("node_id")): item for item in table_semantics}
    objects: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    for order, node_id in enumerate(facts["children"].get("root", [])):
        unit = facts["units"].get(node_id)
        container = facts["containers"].get(node_id, {})
        if unit is not None and unit.get("kind") == "paragraph":
            inline = list(unit.get("inline_content") or [])
            image_count = sum(item.get("kind") == "image" for item in inline)
            if image_count:
                objects.append({
                    "node_id": node_id,
                    "kind": "figure",
                    "order": order,
                    "location": _source_location(facts, node_id),
                })
            source_text = inline_text_projection(inline).strip()
            caption_type, label, _, text = caption_parts(source_text)
            if caption_type in {"table", "figure"}:
                captions.append({
                    "node_id": node_id,
                    "order": order,
                    "location": _source_location(facts, node_id),
                    "caption_type": caption_type,
                    "label": label,
                    "raw_number": _raw_number(source_text),
                    "text": text,
                })
        elif container.get("kind") == "table":
            semantic = tables.get(str(node_id), {})
            if semantic.get("caption_eligible"):
                objects.append({
                    "node_id": node_id,
                    "kind": "table",
                    "order": order,
                    "location": _source_location(facts, node_id),
                })

    assigned: set[str] = set()
    issues: list[dict[str, Any]] = []
    for caption in captions:
        caption_type = str(caption["caption_type"])
        if caption_type == "table":
            candidates = [item for item in objects if item["kind"] == "table" and item["order"] > caption["order"]]
            candidates.sort(key=lambda item: item["order"])
            required = "before"
        else:
            candidates = [item for item in objects if item["kind"] == "figure" and item["order"] < caption["order"]]
            candidates.sort(key=lambda item: item["order"], reverse=True)
            required = "after"
        target = next((item for item in candidates if item["node_id"] not in assigned), None)
        if target is None:
            caption.update({"target_location": None, "required_placement": required, "source_placement": None, "status": "orphan"})
            issues.append({"code": "caption_target_missing", "node_id": caption["node_id"], "caption_type": caption_type})
            continue
        assigned.add(str(target["node_id"]))
        caption.update({
            "target_node_id": target["node_id"],
            "target_location": copy.deepcopy(target["location"]),
            "required_placement": required,
            "source_placement": "before" if caption["order"] < target["order"] else "after",
            "status": "associated",
        })
    for caption in captions:
        caption.pop("order", None)
    return {"captions": captions, "issues": issues}


__all__ = ["audit_caption_placement", "build_caption_overlay", "collect_caption_candidates", "collect_source_caption_semantics", "invalidate_overlay"]
