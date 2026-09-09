"""Stage transaction isolation and one-shot AST commits."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .model import build_indexes
from .validation import validate_ast


class TransactionConflict(RuntimeError):
    """Raised when a stage batch cannot be applied without violating its base."""


@dataclass
class StageSnapshot:
    ast: dict[str, Any]
    transactions: dict[str, str] = field(default_factory=dict)
    overlays: dict[str, dict[str, Any]] = field(default_factory=dict)
    presentation_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class CommitResult:
    status: str
    snapshot: StageSnapshot
    metrics: dict[str, int]
    transaction_id: str


def make_snapshot(ast: Mapping[str, Any]) -> StageSnapshot:
    validate_ast(ast)
    return StageSnapshot(ast=ast if isinstance(ast, dict) else copy.deepcopy(dict(ast)))


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _transaction_id(decisions: Sequence[Mapping[str, Any]]) -> str:
    explicit = [str(decision.get("transaction_id")) for decision in decisions if decision.get("transaction_id")]
    if explicit and len(set(explicit)) == 1:
        return explicit[0]
    return "stage-" + _digest(list(decisions))


def _node_map(ast: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    document = ast["document"]
    result: dict[str, dict[str, Any]] = {}
    result.update(document["blocks"])
    result.update(document["containers"])
    return result


def _order_stage_decisions(snapshot: StageSnapshot, decisions: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Apply structure creation dependencies before edits in one stage batch."""

    create_decisions = [decision for decision in decisions if decision.get("operation") == "create_node"]
    other_decisions = [decision for decision in decisions if decision.get("operation") != "create_node"]
    available = set(_node_map(snapshot.ast))
    pending = list(enumerate(create_decisions))
    ordered: list[Mapping[str, Any]] = []
    while pending:
        ready: list[tuple[int, Mapping[str, Any]]] = []
        for original_index, decision in pending:
            value = decision.get("value") or {}
            parent_id = value.get("parent_id")
            if isinstance(parent_id, str) and parent_id in available:
                ready.append((original_index, decision))
        if not ready:
            raise TransactionConflict("create_node dependencies cannot be ordered")
        ready.sort(
            key=lambda item: (
                str((item[1].get("value") or {}).get("parent_id")),
                int((item[1].get("value") or {}).get("index", 10**9)),
                str((item[1].get("value") or {}).get("node_id")),
                item[0],
            )
        )
        for original_index, decision in ready:
            ordered.append(decision)
            node_id = (decision.get("value") or {}).get("node_id")
            if isinstance(node_id, str):
                available.add(node_id)
            pending = [item for item in pending if item[0] != original_index]
    return [*ordered, *other_decisions]


def _parent_map(ast: Mapping[str, Any]) -> dict[str, str]:
    parent: dict[str, str] = {}
    for parent_id, children in ast["document"]["children"].items():
        for child_id in children:
            parent[child_id] = parent_id
    return parent


def _apply_update_role(ast: dict[str, Any], decision: Mapping[str, Any]) -> None:
    value = decision.get("value") or {}
    nodes = ast["document"]["blocks"]
    for target_id in decision.get("target_node_ids", []):
        if target_id not in nodes:
            raise TransactionConflict(f"unknown update_role target {target_id}")
        node = nodes[target_id]
        if node.get("block_type") != "paragraph":
            raise TransactionConflict(f"update_role target {target_id} is not a paragraph")
        role = value.get("role")
        if role is None:
            raise TransactionConflict("update_role requires value.role")
        node["role"] = role
        if "level" in value:
            node["level"] = value["level"]
        elif role == "heading" and "level" not in node:
            node["level"] = 1


def _apply_update_node_semantics(ast: dict[str, Any], decision: Mapping[str, Any]) -> None:
    value = decision.get("value") or {}
    if not isinstance(value, Mapping):
        raise TransactionConflict("update_node_semantics requires a mapping value")
    semantic_role = value.get("semantic_role")
    if not isinstance(semantic_role, str) or not semantic_role:
        raise TransactionConflict("update_node_semantics requires value.semantic_role")
    nodes = _node_map(ast)
    target_ids = list(decision.get("target_node_ids") or [])
    if not target_ids:
        raise TransactionConflict("update_node_semantics requires at least one target")
    for target_id in target_ids:
        node = nodes.get(target_id)
        if node is None:
            raise TransactionConflict(f"unknown update_node_semantics target {target_id}")
        node["semantic_role"] = semantic_role
        if isinstance(value.get("source_operation"), str):
            node["semantic_source_operation"] = value["source_operation"]
        if isinstance(value.get("adapter_id"), str):
            node["semantic_adapter"] = value["adapter_id"]
        if isinstance(value.get("paragraph_id"), str):
            node["semantic_paragraph_id"] = value["paragraph_id"]
        if isinstance(value.get("list_parent_id"), str):
            node["semantic_list_parent_id"] = value["list_parent_id"]


def _apply_move_node(ast: dict[str, Any], decision: Mapping[str, Any]) -> None:
    target_ids = decision.get("target_node_ids", [])
    value = decision.get("value") or {}
    new_parent_id = value.get("new_parent_id")
    if len(target_ids) != 1 or not isinstance(new_parent_id, str):
        raise TransactionConflict("move_node requires one target and value.new_parent_id")
    node_id = target_ids[0]
    document = ast["document"]
    nodes = _node_map(ast)
    if node_id not in nodes or new_parent_id not in nodes:
        raise TransactionConflict("move_node references an unknown node")
    if node_id == document["root_id"]:
        raise TransactionConflict("root cannot be moved")
    parent = _parent_map(ast)
    old_parent = parent.get(node_id)
    if old_parent is None:
        raise TransactionConflict("move_node target has no parent")
    if new_parent_id == node_id:
        raise TransactionConflict("move_node would create a cycle")
    descendants = {node_id}
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        for child_id in document["children"].get(current, []):
            if child_id not in descendants:
                descendants.add(child_id)
                frontier.append(child_id)
    if new_parent_id in descendants:
        raise TransactionConflict("move_node would create a cycle")
    document["children"][old_parent].remove(node_id)
    destination = document["children"].setdefault(new_parent_id, [])
    index = value.get("index")
    if isinstance(index, int) and not isinstance(index, bool):
        destination.insert(max(0, min(index, len(destination))), node_id)
    else:
        destination.append(node_id)


def _apply_update_association(ast: dict[str, Any], decision: Mapping[str, Any]) -> None:
    value = decision.get("value") or {}
    association = value.get("association", value)
    if not isinstance(association, Mapping):
        raise TransactionConflict("update_association requires an association object")
    required = {"kind", "source_id", "target_id"}
    if not required.issubset(association):
        raise TransactionConflict("association requires kind, source_id and target_id")
    ast["document"]["associations"].append(copy.deepcopy(dict(association)))


def _apply_create_node(ast: dict[str, Any], decision: Mapping[str, Any]) -> None:
    value = decision.get("value") or {}
    node_id = value.get("node_id")
    parent_id = value.get("parent_id")
    node = value.get("node")
    if not isinstance(node_id, str) or not isinstance(parent_id, str) or not isinstance(node, Mapping):
        raise TransactionConflict("create_node requires node_id, parent_id and node")
    document = ast["document"]
    if node_id in _node_map(ast) or parent_id not in _node_map(ast):
        raise TransactionConflict("create_node references a duplicate or unknown node")
    if node.get("block_type"):
        document["blocks"][node_id] = copy.deepcopy(dict(node))
    elif node.get("kind"):
        document["containers"][node_id] = copy.deepcopy(dict(node))
    else:
        raise TransactionConflict("create_node node requires block_type or kind")
    document["children"].setdefault(node_id, [])
    siblings = document["children"].setdefault(parent_id, [])
    index = value.get("index")
    if isinstance(index, int) and not isinstance(index, bool):
        siblings.insert(max(0, min(index, len(siblings))), node_id)
    else:
        siblings.append(node_id)


def _apply_delete_node(ast: dict[str, Any], decision: Mapping[str, Any]) -> None:
    document = ast["document"]
    nodes = _node_map(ast)
    parent = _parent_map(ast)
    for node_id in decision.get("target_node_ids", []):
        if node_id not in nodes:
            raise TransactionConflict(f"delete_node references unknown node {node_id}")
        if document["children"].get(node_id):
            raise TransactionConflict(f"delete_node requires a leaf node: {node_id}")
        parent_id = parent.get(node_id)
        if parent_id is None:
            raise TransactionConflict(f"delete_node target has no parent: {node_id}")
        document["children"][parent_id].remove(node_id)
        document["children"].pop(node_id, None)
        document["blocks"].pop(node_id, None)
        document["containers"].pop(node_id, None)


def _apply_overlay(snapshot: StageSnapshot, decision: Mapping[str, Any]) -> None:
    value = decision.get("value")
    if not isinstance(value, Mapping):
        raise TransactionConflict("update_overlay requires a mapping value")
    nodes = _node_map(snapshot.ast)
    target_ids = list(decision.get("target_node_ids") or [])
    if not target_ids:
        raise TransactionConflict("update_overlay requires at least one target")
    for target_id in target_ids:
        if target_id not in nodes:
            raise TransactionConflict(f"unknown update_overlay target {target_id}")
        current = snapshot.overlays.setdefault(str(target_id), {})
        current.update(copy.deepcopy(dict(value)))


def _apply_presentation_override(snapshot: StageSnapshot, decision: Mapping[str, Any]) -> None:
    value = decision.get("value")
    if not isinstance(value, Mapping):
        raise TransactionConflict("set_presentation_override requires a mapping value")
    nodes = _node_map(snapshot.ast)
    target_ids = list(decision.get("target_node_ids") or [])
    if not target_ids:
        raise TransactionConflict("set_presentation_override requires at least one target")
    for target_id in target_ids:
        if target_id not in nodes:
            raise TransactionConflict(f"unknown presentation target {target_id}")
        key = str(value.get("list_item_id") or target_id)
        current = snapshot.presentation_overrides.setdefault(key, {})
        current.update(copy.deepcopy(dict(value)))


def _apply_decision(snapshot: StageSnapshot, decision: Mapping[str, Any]) -> None:
    ast = snapshot.ast
    operation = decision.get("operation")
    if operation == "update_role":
        _apply_update_role(ast, decision)
    elif operation == "update_node_semantics":
        _apply_update_node_semantics(ast, decision)
    elif operation == "move_node":
        _apply_move_node(ast, decision)
    elif operation == "update_association":
        _apply_update_association(ast, decision)
    elif operation == "create_node":
        _apply_create_node(ast, decision)
    elif operation == "delete_node":
        _apply_delete_node(ast, decision)
    elif operation == "update_overlay":
        _apply_overlay(snapshot, decision)
    elif operation == "set_presentation_override":
        _apply_presentation_override(snapshot, decision)
    else:
        raise TransactionConflict(f"unsupported stage operation {operation!r}")


def apply_stage_batch(
    snapshot: StageSnapshot,
    decisions: Sequence[Mapping[str, Any]],
    expected_revision: int,
) -> CommitResult:
    """Apply all decisions to one isolated copy and publish one new revision."""

    if snapshot.ast.get("revision") != expected_revision:
        raise TransactionConflict(
            f"revision conflict: expected {expected_revision}, current {snapshot.ast.get('revision')}"
        )
    transaction_id = _transaction_id(decisions)
    decision_digest = _digest(list(decisions))
    previous_digest = snapshot.transactions.get(transaction_id)
    if previous_digest is not None:
        if previous_digest != decision_digest:
            raise TransactionConflict(f"transaction {transaction_id} has a different content digest")
        return CommitResult(
            status="already_committed",
            snapshot=snapshot,
            metrics={"full_model_copy_count": 0, "full_graph_validation_count": 0, "index_rebuild_count": 0},
            transaction_id=transaction_id,
        )

    isolated = StageSnapshot(
        ast=copy.deepcopy(snapshot.ast),
        transactions=dict(snapshot.transactions),
        overlays=copy.deepcopy(snapshot.overlays),
        presentation_overrides=copy.deepcopy(snapshot.presentation_overrides),
    )
    metrics = {"full_model_copy_count": 1, "full_graph_validation_count": 0, "index_rebuild_count": 0}
    try:
        for decision in _order_stage_decisions(isolated, decisions):
            decision_revision = decision.get("expected_revision", expected_revision)
            if decision_revision != expected_revision:
                raise TransactionConflict(f"decision revision conflict: expected {expected_revision}, got {decision_revision}")
            _apply_decision(isolated, decision)
        isolated.ast["revision"] = expected_revision + 1
        validate_ast(isolated.ast)
        metrics["full_graph_validation_count"] = 1
        build_indexes(isolated.ast, validate=False)
        metrics["index_rebuild_count"] = 1
    except Exception as exc:
        if isinstance(exc, TransactionConflict):
            raise
        raise TransactionConflict(f"stage batch rejected: {exc}") from exc

    committed = StageSnapshot(
        ast=isolated.ast,
        transactions=dict(snapshot.transactions),
        overlays=isolated.overlays,
        presentation_overrides=isolated.presentation_overrides,
    )
    committed.transactions[transaction_id] = decision_digest
    return CommitResult(status="committed", snapshot=committed, metrics=metrics, transaction_id=transaction_id)
