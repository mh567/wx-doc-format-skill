"""Compatibility adapter for capabilities written against the legacy model.

The adapter gives old collectors a read-only projection and keeps the mapping
from a legacy ID to the AST node that was observed at request time.  Legacy
patches become candidates only.  The adapter never mutates a Source AST.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import AST_SCHEMA_VERSION, PATCH_SCHEMA_VERSION, TABLE_TYPES
from .migration import legacy_view
from .model import build_indexes, inline_text_projection
from .validation import validate_ast


class LegacyCapabilityError(ValueError):
    """Raised when a legacy view or patch cannot be mapped without guessing."""

    def __init__(self, message: str, *, code: str = "ambiguous_legacy_patch", targets: list[str] | None = None) -> None:
        self.code = code
        self.targets = tuple(targets or ())
        super().__init__(f"{code}: {message}")


class _FrozenDict(dict):
    """A JSON-friendly deep immutable mapping whose deepcopy is isolated."""

    def __setitem__(self, key: Any, value: Any) -> None:  # pragma: no cover - exercised through callers
        raise TypeError("mapping is read-only")

    def __delitem__(self, key: Any) -> None:  # pragma: no cover - exercised through callers
        raise TypeError("mapping is read-only")

    def clear(self) -> None:  # pragma: no cover - exercised through callers
        raise TypeError("mapping is read-only")

    def pop(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - exercised through callers
        raise TypeError("mapping is read-only")

    def popitem(self) -> Any:  # pragma: no cover - exercised through callers
        raise TypeError("mapping is read-only")

    def setdefault(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - exercised through callers
        raise TypeError("mapping is read-only")

    def update(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - exercised through callers
        raise TypeError("mapping is read-only")

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        result = {copy.deepcopy(key, memo): copy.deepcopy(value, memo) for key, value in self.items()}
        memo[id(self)] = result
        return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen = _FrozenDict()
        dict.__init__(frozen, {key: _freeze(item) for key, item in value.items()})
        return frozen
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(_thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_ref(node: Mapping[str, Any]) -> dict[str, Any] | None:
    value = node.get("source_ref")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None


def _node_map(ast: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    document = ast["document"]
    result: dict[str, Mapping[str, Any]] = {}
    result.update(document["blocks"])
    result.update(document["containers"])
    return result


def _children_of(ast: Mapping[str, Any], node_id: str, *, blocks_only: bool = False) -> list[str]:
    document = ast["document"]
    children = list(document["children"].get(node_id, []))
    if not blocks_only:
        return children
    return [child for child in children if child in document["blocks"]]


def _add_alias(entries: dict[str, dict[str, Any]], alias: str, entry: dict[str, Any]) -> None:
    if not alias:
        return
    previous = entries.get(alias)
    if previous is None:
        entries[alias] = entry
        return
    previous_node = previous.get("node_id")
    if previous_node == entry.get("node_id") and previous.get("list_item_id") == entry.get("list_item_id"):
        return
    entries[alias] = {
        "legacy_id": alias,
        "node_id": None,
        "target_node_ids": sorted({str(value) for value in (previous.get("target_node_ids") or [previous_node]) + (entry.get("target_node_ids") or [entry.get("node_id")]) if value}),
        "mapping_kind": "ambiguous_id_reuse",
        "legacy_addressable": False,
    }


def _entry(
    *,
    alias: str,
    node_id: str,
    node: Mapping[str, Any],
    parent_id: str | None,
    owner_item_id: str | None,
    mapping_kind: str = "direct",
    list_item_id: str | None = None,
    legacy_addressable: bool = True,
) -> dict[str, Any]:
    return {
        "legacy_id": alias,
        "node_id": node_id,
        "target_node_ids": [node_id],
        "kind": "block" if "block_type" in node else "container",
        "block_type": node.get("block_type"),
        "container_kind": node.get("kind"),
        "role": node.get("role"),
        "parent_id": parent_id,
        "owner_item_id": owner_item_id,
        "list_item_id": list_item_id,
        "source_ref": _source_ref(node),
        "mapping_kind": mapping_kind,
        "legacy_addressable": legacy_addressable,
    }


@dataclass(frozen=True)
class LegacyCapabilityBundle:
    """Read-only legacy input plus the revision-bound target mapping."""

    view: Mapping[str, Any]
    target_map: Mapping[str, Any]

    def __iter__(self):
        yield self.view
        yield self.target_map


def make_view(ast: Mapping[str, Any]) -> LegacyCapabilityBundle:
    """Build an immutable legacy view and its immutable revision-bound map."""

    validate_ast(ast)
    view = legacy_view(ast)
    document = ast["document"]
    indexes = build_indexes(ast, validate=False)
    nodes = _node_map(ast)
    entries: dict[str, dict[str, Any]] = {}

    for node_id, node in nodes.items():
        parent_id = indexes.parent_by_id.get(node_id)
        owner_item_id = indexes.owner_item_by_id.get(node_id)
        alias = node_id
        generated = bool(node.get("generated") or node.get("is_generated") or node.get("generation_basis"))
        mapping_kind = "generated" if generated else "direct"
        legacy_addressable = node_id in view["blocks"] or node_id in document["containers"]
        if generated:
            legacy_addressable = False
        if node.get("kind") == "list_item":
            paragraphs = [child for child in _children_of(ast, node_id, blocks_only=True) if nodes[child].get("block_type") == "paragraph"]
            alias = str(node.get("legacy_id") or node_id)
            mapping_kind = "list_item_first_paragraph" if len(paragraphs) == 1 else "list_item_ambiguous_paragraphs"
            legacy_addressable = len(paragraphs) == 1 and alias in view["blocks"]
            target_id = paragraphs[0] if len(paragraphs) == 1 else node_id
            item_entry = _entry(
                alias=alias,
                node_id=target_id,
                node=nodes[target_id],
                parent_id=parent_id,
                owner_item_id=node_id,
                mapping_kind=mapping_kind,
                list_item_id=node_id,
                legacy_addressable=legacy_addressable,
            )
            item_entry["list_item_source_ref"] = _source_ref(node)
            if generated:
                item_entry["mapping_kind"] = "generated"
                item_entry["legacy_addressable"] = False
            _add_alias(entries, alias, item_entry)
            if target_id != node_id:
                direct = _entry(
                    alias=target_id,
                    node_id=target_id,
                    node=nodes[target_id],
                    parent_id=node_id,
                    owner_item_id=node_id,
                    mapping_kind="list_item_paragraph",
                    list_item_id=node_id,
                    legacy_addressable=False,
                )
                _add_alias(entries, target_id, direct)
            continue

        if node_id in view["blocks"]:
            legacy_addressable = True
        if generated:
            legacy_addressable = False
        _add_alias(
            entries,
            alias,
            _entry(
                alias=alias,
                node_id=node_id,
                node=node,
                parent_id=parent_id,
                owner_item_id=owner_item_id,
                mapping_kind=mapping_kind,
                list_item_id=owner_item_id,
                legacy_addressable=legacy_addressable,
            ),
        )

    target_map_plain = {
        "schema_version": "2.0",
        "revision": int(ast["revision"]),
        "view_sha256": _digest(view),
        "model_sha256": _digest(ast),
        "contracts": {
            "ast": AST_SCHEMA_VERSION,
            "legacy_view": "1.0",
            "target_map": "2.0",
            "patch": PATCH_SCHEMA_VERSION,
            "legacy_patch": "1.0",
            "capability_input": "2.0",
        },
        "entries": entries,
        "source_refs": [
            entry["source_ref"]
            for entry in entries.values()
            if isinstance(entry.get("source_ref"), Mapping)
        ],
    }
    target_map_plain["target_map_sha256"] = _digest(target_map_plain)
    return LegacyCapabilityBundle(view=view, target_map=_freeze(target_map_plain))


def _snapshot_ast(snapshot: Any) -> Mapping[str, Any]:
    ast = getattr(snapshot, "ast", snapshot)
    if not isinstance(ast, Mapping):
        raise LegacyCapabilityError("snapshot must contain an AST", code="invalid_snapshot")
    return ast


def _decision_targets(decision: Mapping[str, Any]) -> str:
    for key in ("block_id", "target_id", "legacy_id", "id"):
        value = decision.get(key)
        if isinstance(value, str) and value:
            return value
    targets = decision.get("target_node_ids")
    if isinstance(targets, list) and len(targets) == 1 and isinstance(targets[0], str):
        return targets[0]
    raise LegacyCapabilityError("legacy decision has no stable target ID", code="invalid_legacy_patch")


def _value_mapping(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    value = decision.get("to", decision.get("value", {}))
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        return {"role": value}
    return {}


def _current_entry(entry: Mapping[str, Any], ast: Mapping[str, Any], legacy_id: str) -> tuple[str, Mapping[str, Any]]:
    if entry.get("mapping_kind") == "ambiguous_id_reuse" or not entry.get("legacy_addressable", True):
        raise LegacyCapabilityError("legacy ID cannot be mapped uniquely", targets=[legacy_id])
    node_id = entry.get("node_id")
    nodes = _node_map(ast)
    if not isinstance(node_id, str) or node_id not in nodes:
        raise LegacyCapabilityError("target node was removed or reused", code="stale_legacy_id", targets=[legacy_id])
    node = nodes[node_id]
    expected_ref = entry.get("source_ref")
    current_ref = node.get("source_ref")
    if isinstance(expected_ref, Mapping) and isinstance(current_ref, Mapping):
        if expected_ref.get("content_sha256") != current_ref.get("content_sha256") or expected_ref.get("span") != current_ref.get("span"):
            raise LegacyCapabilityError("target source reference changed", code="stale_source_ref", targets=[legacy_id])
    list_item_id = entry.get("list_item_id")
    if isinstance(list_item_id, str):
        containers = ast["document"]["containers"]
        if list_item_id not in containers or containers[list_item_id].get("kind") != "list_item":
            raise LegacyCapabilityError("list item target was removed or reused", code="stale_legacy_id", targets=[legacy_id])
        expected_item_ref = entry.get("list_item_source_ref")
        current_item_ref = containers[list_item_id].get("source_ref")
        if isinstance(expected_item_ref, Mapping) and isinstance(current_item_ref, Mapping):
            if expected_item_ref.get("content_sha256") != current_item_ref.get("content_sha256") or expected_item_ref.get("span") != current_item_ref.get("span"):
                raise LegacyCapabilityError("list item source reference changed", code="stale_source_ref", targets=[legacy_id])
    return node_id, node


def _candidate(index: int, legacy_id: str, entry: Mapping[str, Any], node_id: str, proposal: dict[str, Any], *, constraints: list[dict[str, Any]] | None = None, rationale: str = "legacy capability patch") -> dict[str, Any]:
    source_refs = [copy.deepcopy(dict(entry["source_ref"]))] if isinstance(entry.get("source_ref"), Mapping) else []
    digest = _digest({"legacy_id": legacy_id, "node_id": node_id, "proposal": proposal, "source_refs": source_refs})[7:23]
    return {
        "candidate_id": f"legacy-{index:04d}-{digest}",
        "module_id": "legacy_capability_adapter",
        "module_version": "1.0",
        "target_source_refs": source_refs,
        "target_node_ids": [node_id],
        "proposal": copy.deepcopy(proposal),
        "positive_evidence": [{"kind": "legacy_patch", "legacy_id": legacy_id}],
        "negative_evidence": [],
        "scope": {"legacy_id": legacy_id, "mapping_kind": entry.get("mapping_kind", "direct")},
        "constraints": constraints or [],
        "rationale": rationale,
        "confidence": None,
    }


def _require_list_context(entry: Mapping[str, Any], ast: Mapping[str, Any], legacy_id: str) -> tuple[str, str]:
    list_item_id = entry.get("list_item_id") or entry.get("owner_item_id")
    if not isinstance(list_item_id, str):
        raise LegacyCapabilityError("list parent or item evidence is missing", targets=[legacy_id])
    parent_id = build_indexes(ast, validate=False).parent_by_id.get(list_item_id)
    if not isinstance(parent_id, str) or ast["document"]["containers"].get(parent_id, {}).get("kind") != "list":
        raise LegacyCapabilityError("list parent is not unique", targets=[legacy_id])
    return list_item_id, parent_id


def _caption_target(ast: Mapping[str, Any], node_id: str, node: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    if node.get("block_type") in {"table", "image"}:
        return node_id, node
    document = ast["document"]
    matches = [
        association.get("target_id")
        for association in document.get("associations", [])
        if association.get("kind") == "caption_target" and association.get("source_id") == node_id
    ]
    if node.get("target_id"):
        matches.append(node.get("target_id"))
    unique = sorted({str(value) for value in matches if isinstance(value, str) and value in _node_map(ast)})
    if len(unique) == 1:
        return unique[0], _node_map(ast)[unique[0]]
    return None


def translate(patch: Mapping[str, Any], target_map: Mapping[str, Any], snapshot: Any) -> dict[str, Any]:
    """Translate a legacy patch into restricted candidates without AST writes."""

    ast = _snapshot_ast(snapshot)
    validate_ast(ast)
    if not isinstance(target_map, Mapping) or target_map.get("schema_version") != "2.0":
        raise LegacyCapabilityError("target map schema is unsupported", code="unsupported_target_map")
    if target_map.get("revision") != ast.get("revision"):
        raise LegacyCapabilityError(
            f"target map revision {target_map.get('revision')} does not match model revision {ast.get('revision')}",
            code="revision_conflict",
        )
    expected_model_digest = target_map.get("model_sha256")
    if expected_model_digest and expected_model_digest != _digest(ast):
        raise LegacyCapabilityError("model content changed since the request", code="model_digest_conflict")
    if not isinstance(patch, Mapping):
        raise LegacyCapabilityError("legacy patch must be an object", code="invalid_legacy_patch")
    patch_version = patch.get("patch_schema_version", patch.get("schema_version", "1.0"))
    if patch_version not in {"1.0", "2.0"}:
        raise LegacyCapabilityError("legacy patch schema is unsupported", code="unsupported_patch")
    if patch.get("target_map_sha256") and patch.get("target_map_sha256") != target_map.get("target_map_sha256"):
        raise LegacyCapabilityError("patch was produced for another legacy view", code="view_digest_conflict")
    decisions = patch.get("decisions", patch.get("operations", []))
    if not isinstance(decisions, list):
        raise LegacyCapabilityError("legacy patch decisions must be a list", code="invalid_legacy_patch")
    entries = target_map.get("entries")
    if not isinstance(entries, Mapping):
        raise LegacyCapabilityError("target map entries are missing", code="invalid_target_map")

    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, Mapping):
            raise LegacyCapabilityError(f"decision {index} is not an object", code="invalid_legacy_patch")
        legacy_id = _decision_targets(decision)
        entry = entries.get(legacy_id)
        if not isinstance(entry, Mapping):
            raise LegacyCapabilityError("legacy ID is absent from the request map", code="stale_legacy_id", targets=[legacy_id])
        node_id, node = _current_entry(entry, ast, legacy_id)
        operation = str(decision.get("operation") or decision.get("op") or "")
        values = _value_mapping(decision)

        if operation == "retype":
            role = str(values.get("role", values.get("block_type", "")))
            if role == "list_item":
                _require_list_context(entry, ast, legacy_id)
                list_item_id, list_parent_id = _require_list_context(entry, ast, legacy_id)
                candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "retype_list_item", "value": {"list_item_id": list_item_id, "list_parent_id": list_parent_id, "paragraph_id": node_id}}, constraints=[{"kind": "preserve_list_parent"}, {"kind": "rebuild_list_relationship"}], rationale="legacy list-item retype with evidenced parent"))
            elif role in {"body", "heading", "caption", "note", "numbered_note"}:
                value = {"role": role}
                if role == "heading" and "level" in values:
                    value["level"] = values["level"]
                constraints = [{"kind": "preserve_source_ref"}]
                if role == "caption":
                    target = _caption_target(ast, node_id, node)
                    if target is None:
                        constraints.append({"kind": "caption_target_required"})
                    else:
                        value["target_id"] = target[0]
                candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "update_role", "property_scope": ["role"], "value": value}, constraints=constraints))
            else:
                raise LegacyCapabilityError("retype target role is unsupported", code="unsupported_legacy_operation", targets=[legacy_id])

        elif operation == "adjust_level":
            level = values.get("level", decision.get("level"))
            if not isinstance(level, int) or isinstance(level, bool):
                raise LegacyCapabilityError("heading level is invalid", code="invalid_legacy_patch", targets=[legacy_id])
            if node.get("role") == "heading":
                if level < 1 or level > 6:
                    raise LegacyCapabilityError("heading level is invalid", code="invalid_legacy_patch", targets=[legacy_id])
                candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "update_role", "property_scope": ["role", "level"], "value": {"role": "heading", "level": level}}, constraints=[{"kind": "rebuild_section_relation"}]))
            elif entry.get("list_item_id"):
                if level < 0 or level > 8:
                    raise LegacyCapabilityError("list level is invalid", code="invalid_legacy_patch", targets=[legacy_id])
                list_item_id, parent_id = _require_list_context(entry, ast, legacy_id)
                candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "adjust_list_level", "property_scope": ["children"], "value": {"list_item_id": list_item_id, "list_parent_id": parent_id, "level": level}}, constraints=[{"kind": "unique_parent_required"}]))
            else:
                raise LegacyCapabilityError("level target is neither heading nor uniquely mapped list item", targets=[legacy_id])

        elif operation == "set_restart":
            list_item_id, list_parent_id = _require_list_context(entry, ast, legacy_id)
            restart = decision.get("restart", values.get("restart"))
            if not isinstance(restart, bool):
                raise LegacyCapabilityError("restart must be boolean", code="invalid_legacy_patch", targets=[legacy_id])
            candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "set_presentation_override", "property_scope": ["presentation"], "value": {"list_item_id": list_item_id, "list_parent_id": list_parent_id, "restart": restart, "source_semantics": "preserved"}}, constraints=[{"kind": "restart_scope", "value": "list_sequence"}, {"kind": "no_global_restart_inference"}]))

        elif operation == "set_table_type":
            if node.get("block_type") != "table":
                raise LegacyCapabilityError("table type target is not a table", code="unsupported_legacy_operation", targets=[legacy_id])
            table_type = str(values.get("table_type", values.get("type", decision.get("table_type", ""))))
            if table_type not in TABLE_TYPES:
                raise LegacyCapabilityError("table type is unsupported", code="invalid_legacy_patch", targets=[legacy_id])
            candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "update_overlay", "property_scope": ["overlay"], "value": {"table_type": table_type}}, constraints=[{"kind": "table_target_required"}, {"kind": "layout_table_protected", "allow_explicit_override": table_type == "layout"}]))

        elif operation in {"caption", "generate_caption", "caption_gen", "set_caption_text"}:
            target = _caption_target(ast, node_id, node)
            text = values.get("text", values.get("caption", decision.get("text")))
            if target is None or not isinstance(text, str) or not text.strip():
                raise LegacyCapabilityError("caption target or validated text is ambiguous", targets=[legacy_id])
            target_id, target_node = target
            content_digest = _digest({"node_id": target_id, "node": target_node})
            candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "update_overlay", "property_scope": ["overlay"], "value": {"kind": "caption_text", "target_node_id": target_id, "text": text, "target_content_sha256": content_digest, "association_revision": ast["revision"], "validated": True}}, constraints=[{"kind": "caption_overlay_only"}, {"kind": "target_content_digest", "value": content_digest}]))

        elif operation == "set_caption_type":
            caption_type = str(values.get("caption_type", values.get("type", "")))
            if caption_type not in {"figure", "table"}:
                raise LegacyCapabilityError("caption type is unsupported", code="invalid_legacy_patch", targets=[legacy_id])
            target = _caption_target(ast, node_id, node)
            if target is None:
                raise LegacyCapabilityError("caption target association is ambiguous", targets=[legacy_id])
            candidates.append(_candidate(index, legacy_id, entry, node_id, {"operation": "update_role", "property_scope": ["role", "caption_type", "target_id"], "value": {"role": "caption", "caption_type": caption_type, "target_id": target[0]}}, constraints=[{"kind": "caption_target_required"}]))

        else:
            raise LegacyCapabilityError("legacy operation is unsupported", code="unsupported_legacy_operation", targets=[legacy_id])

    return {
        "candidate_set_version": "2.0",
        "base_revision": ast["revision"],
        "view_sha256": target_map.get("view_sha256"),
        "target_map_sha256": target_map.get("target_map_sha256"),
        "contracts": copy.deepcopy(dict(target_map.get("contracts", {}))),
        "candidates": candidates,
        "diagnostics": diagnostics,
    }
