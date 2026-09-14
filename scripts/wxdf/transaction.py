"""Stage transaction isolation and one-shot AST commits."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .digest import digest_hex
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


@dataclass(frozen=True)
class CandidateRevision:
    """An isolated transaction result with an immutable rollback baseline."""

    baseline: StageSnapshot
    candidate: CommitResult

    def rollback(self) -> StageSnapshot:
        return StageSnapshot(
            ast=copy.deepcopy(self.baseline.ast),
            transactions=dict(self.baseline.transactions),
            overlays=copy.deepcopy(self.baseline.overlays),
            presentation_overrides=copy.deepcopy(self.baseline.presentation_overrides),
        )


def make_snapshot(ast: Mapping[str, Any]) -> StageSnapshot:
    validate_ast(ast)
    return StageSnapshot(ast=ast if isinstance(ast, dict) else copy.deepcopy(dict(ast)))


def _canonical_decision_content(decision: Mapping[str, Any]) -> dict[str, Any]:
    nested = decision.get("decision")
    operation = nested.get("operation") if isinstance(nested, Mapping) else decision.get("operation")
    value = nested.get("value") if isinstance(nested, Mapping) else decision.get("value")
    invalidates = nested.get("invalidates") if isinstance(nested, Mapping) else decision.get("invalidates")
    return {
        "target_source_refs": copy.deepcopy(list(decision.get("target_source_refs") or [])),
        "target_node_ids": copy.deepcopy(list(decision.get("target_node_ids") or [])),
        "property_scope": copy.deepcopy(list(decision.get("property_scope") or [])),
        "operation": operation,
        "value": copy.deepcopy(value),
        "invalidates": copy.deepcopy(list(invalidates or [])),
        "preconditions": copy.deepcopy(list(decision.get("preconditions") or [])),
        "evidence": copy.deepcopy(list(decision.get("evidence") or [])),
        "supersedes": copy.deepcopy(list(decision.get("supersedes") or [])),
        "status": str(decision.get("status") or "active"),
    }


def transaction_content_digest(decisions: Sequence[Mapping[str, Any]]) -> str:
    """Return the shared transaction proof used by snapshots and ledgers."""

    return "sha256:" + digest_hex([
        _canonical_decision_content(decision)
        for decision in decisions
    ])


def _transaction_id(decisions: Sequence[Mapping[str, Any]]) -> str:
    explicit = [str(decision.get("transaction_id")) for decision in decisions if decision.get("transaction_id")]
    if explicit and len(set(explicit)) == 1:
        return explicit[0]
    return "stage-" + digest_hex(list(decisions))


def _node_map(ast: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    document = ast["document"]
    result: dict[str, dict[str, Any]] = {}
    result.update(document["blocks"])
    result.update(document["containers"])
    return result


def _source_ref_matches(expected: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    for field in ("source_id", "part", "content_sha256", "span", "node_path", "parser_version"):
        if field in expected or field in current:
            if current.get(field) != expected.get(field):
                return False
    return True


def _validate_source_preconditions(ast: Mapping[str, Any], decision: Mapping[str, Any]) -> None:
    if decision.get("operation") == "create_node":
        return
    expected_refs = [ref for ref in decision.get("target_source_refs", []) if isinstance(ref, Mapping)]
    if not expected_refs:
        return
    nodes = _node_map(ast)
    target_nodes = [nodes.get(str(target_id)) for target_id in decision.get("target_node_ids", [])]
    current_refs = [node.get("source_ref") for node in target_nodes if isinstance(node, Mapping) and isinstance(node.get("source_ref"), Mapping)]
    if not current_refs or not all(any(_source_ref_matches(expected, current) for current in current_refs) for expected in expected_refs):
        raise TransactionConflict("source reference precondition failed")


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
                -int((item[1].get("value") or {}).get("index", 10**9)),
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
    move_decisions = [decision for decision in other_decisions if decision.get("operation") == "move_node"]
    remaining_decisions = [decision for decision in other_decisions if decision.get("operation") != "move_node"]
    move_decisions.sort(
        key=lambda decision: (
            str((decision.get("value") or {}).get("new_parent_id")),
            int((decision.get("value") or {}).get("index", 10**9)),
            str((decision.get("target_node_ids") or [""])[0]),
        )
    )
    return [*ordered, *move_decisions, *remaining_decisions]


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
        elif role != "heading":
            node.pop("level", None)
        for field in ("caption_type", "target_id"):
            if field in value:
                node[field] = copy.deepcopy(value[field])


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
        for field in (
            "appendix_id",
            "appendix_role",
            "appendix_relative_level",
            "semantic_scope_id",
            "source_heading_level",
            "page_break_before",
            "source_number_preserved",
            "materialization_mode",
        ):
            if field in value:
                node[field] = copy.deepcopy(value[field])


def _apply_update_node_content(ast: dict[str, Any], decision: Mapping[str, Any]) -> None:
    value = decision.get("value") or {}
    inline_content = value.get("inline_content") if isinstance(value, Mapping) else None
    if not isinstance(inline_content, list):
        raise TransactionConflict("update_node_content requires value.inline_content")
    blocks = ast["document"]["blocks"]
    for target_id in decision.get("target_node_ids", []):
        block = blocks.get(target_id)
        if not isinstance(block, Mapping) or block.get("block_type") != "paragraph":
            raise TransactionConflict(f"update_node_content target {target_id} is not a paragraph")
        block["inline_content"] = copy.deepcopy(inline_content)


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
    metadata: dict[str, Any] = {}
    destination_node = nodes.get(new_parent_id)
    if isinstance(destination_node, Mapping):
        for field in ("appendix_id", "semantic_scope_id", "source_number_preserved"):
            if field in destination_node:
                metadata[field] = copy.deepcopy(destination_node[field])
    destination_parent = parent.get(new_parent_id)
    while destination_parent is not None:
        ancestor = nodes.get(destination_parent)
        if isinstance(ancestor, Mapping):
            for field in ("appendix_id", "semantic_scope_id", "source_number_preserved"):
                if field in ancestor and field not in metadata:
                    metadata[field] = copy.deepcopy(ancestor[field])
        destination_parent = parent.get(destination_parent)
    explicit_metadata = value.get("semantic_metadata")
    if isinstance(explicit_metadata, Mapping):
        metadata.update(copy.deepcopy(dict(explicit_metadata)))
    if metadata:
        for descendant_id in descendants:
            descendant = nodes.get(descendant_id)
            if descendant is None:
                continue
            for field in ("appendix_id", "semantic_scope_id", "source_number_preserved"):
                if field in metadata:
                    descendant[field] = copy.deepcopy(metadata[field])


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


def revision_applied(
    ast: Mapping[str, Any],
    revision: Mapping[str, Any],
    snapshot: StageSnapshot | None = None,
) -> bool:
    """Return whether one ledger revision already matches the current commit.

    This is the single source of the apply semantics: it mirrors the
    ``_apply_*`` operations, so an operation change is made once. It is a pure
    predicate and never mutates ``ast`` or ``snapshot``.
    """

    decision = revision["decision"]
    operation = decision.get("operation")
    document = ast["document"]
    nodes = {**document["blocks"], **document["containers"]}
    if operation == "create_node":
        value = decision.get("value") or {}
        node_id = value.get("node_id")
        parent_id = value.get("parent_id")
        return (
            isinstance(node_id, str)
            and isinstance(parent_id, str)
            and node_id in nodes
            and nodes[node_id] == value.get("node")
            and node_id in document["children"].get(parent_id, [])
        )
    if operation == "move_node":
        target_ids = revision.get("target_node_ids") or []
        value = decision.get("value") or {}
        if len(target_ids) != 1 or not isinstance(value.get("new_parent_id"), str):
            return False
        target_id = target_ids[0]
        siblings = document["children"].get(value["new_parent_id"], [])
        if target_id not in siblings:
            return False
        index = value.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            expected_index = max(0, min(index, len(siblings) - 1))
            if siblings.index(target_id) != expected_index:
                return False
        metadata = value.get("semantic_metadata")
        if isinstance(metadata, Mapping):
            nodes = {**document["blocks"], **document["containers"]}
            descendants = {target_id}
            frontier = [target_id]
            while frontier:
                current = frontier.pop()
                for child_id in document["children"].get(current, []):
                    if child_id not in descendants:
                        descendants.add(child_id)
                        frontier.append(child_id)
            for node_id in descendants:
                node = nodes.get(node_id, {})
                for field in ("appendix_id", "semantic_scope_id", "source_number_preserved"):
                    if field in metadata and node.get(field) != metadata[field]:
                        return False
        return True
    if operation == "update_role":
        value = decision.get("value") or {}
        role = value.get("role")
        if not isinstance(role, str):
            return False
        target_ids = list(revision.get("target_node_ids") or [])
        if not target_ids:
            return False
        return all(
            target_id in document["blocks"]
            and document["blocks"][target_id].get("role") == role
            and ("level" not in value or document["blocks"][target_id].get("level") == value["level"])
            and (role == "heading" or "level" not in document["blocks"][target_id])
            and all(
                field not in value or document["blocks"][target_id].get(field) == value[field]
                for field in ("caption_type", "target_id")
            )
            for target_id in target_ids
        )
    if operation == "update_node_semantics":
        value = decision.get("value") or {}
        if not isinstance(value, Mapping) or not isinstance(value.get("semantic_role"), str):
            return False
        nodes = {**document["blocks"], **document["containers"]}
        fields = {
            "semantic_role": "semantic_role",
            "source_operation": "semantic_source_operation",
            "adapter_id": "semantic_adapter",
            "paragraph_id": "semantic_paragraph_id",
            "list_parent_id": "semantic_list_parent_id",
            "appendix_id": "appendix_id",
            "appendix_role": "appendix_role",
            "appendix_relative_level": "appendix_relative_level",
            "semantic_scope_id": "semantic_scope_id",
            "source_heading_level": "source_heading_level",
            "page_break_before": "page_break_before",
            "source_number_preserved": "source_number_preserved",
            "materialization_mode": "materialization_mode",
        }
        target_ids = list(revision.get("target_node_ids") or [])
        if not target_ids:
            return False
        return all(
            target_id in nodes
            and all(
                field not in value or nodes[target_id].get(ast_field) == value[field]
                for field, ast_field in fields.items()
            )
            for target_id in target_ids
        )
    if operation == "update_node_content":
        value = decision.get("value") or {}
        inline_content = value.get("inline_content") if isinstance(value, Mapping) else None
        if not isinstance(inline_content, list):
            return False
        target_ids = list(revision.get("target_node_ids") or [])
        if not target_ids:
            return False
        return all(
            target_id in document["blocks"]
            and document["blocks"][target_id].get("inline_content") == inline_content
            for target_id in target_ids
        )
    if operation == "update_association":
        value = decision.get("value") or {}
        association = value.get("association", value) if isinstance(value, Mapping) else None
        return isinstance(association, Mapping) and dict(association) in document["associations"]
    if operation == "delete_node":
        nodes = {**document["blocks"], **document["containers"]}
        return all(target_id not in nodes for target_id in revision.get("target_node_ids") or [])
    if operation in {"update_overlay", "set_presentation_override"}:
        if snapshot is None:
            return False
        value = decision.get("value") or {}
        if not isinstance(value, Mapping):
            return False
        target_ids = revision.get("target_node_ids") or []
        nodes = {**document["blocks"], **document["containers"]}
        if any(target_id not in nodes for target_id in target_ids):
            return False
        store = snapshot.overlays if operation == "update_overlay" else snapshot.presentation_overrides
        if operation == "update_overlay":
            target_keys = [str(target_id) for target_id in target_ids]
        else:
            list_item_id = value.get("list_item_id")
            target_keys = [str(list_item_id)] if isinstance(list_item_id, str) and list_item_id else [str(target_id) for target_id in target_ids]
        return all(
            isinstance(store.get(target_key), Mapping)
            and all(store[target_key].get(key) == item for key, item in value.items())
            for target_key in target_keys
        )
    return False


def _apply_decision(snapshot: StageSnapshot, decision: Mapping[str, Any]) -> None:
    ast = snapshot.ast
    _validate_source_preconditions(ast, decision)
    operation = decision.get("operation")
    if operation == "update_role":
        _apply_update_role(ast, decision)
    elif operation == "update_node_semantics":
        _apply_update_node_semantics(ast, decision)
    elif operation == "update_node_content":
        _apply_update_node_content(ast, decision)
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
    fallback_transaction_id = _transaction_id(decisions)
    transaction_groups: dict[str, list[Mapping[str, Any]]] = {}
    for decision in decisions:
        transaction_id = str(decision.get("transaction_id") or fallback_transaction_id)
        transaction_groups.setdefault(transaction_id, []).append(decision)
    if not transaction_groups:
        transaction_groups[fallback_transaction_id] = []
    fresh_groups: dict[str, list[Mapping[str, Any]]] = {}
    group_digests: dict[str, str] = {}
    for transaction_id, group in transaction_groups.items():
        decision_digest = transaction_content_digest(group)
        group_digests[transaction_id] = decision_digest
        previous_digest = snapshot.transactions.get(transaction_id)
        if previous_digest is None:
            fresh_groups[transaction_id] = group
            continue
        if group and all(decision.get("revision_id") for decision in group):
            continue
        normalized_previous = "sha256:" + str(previous_digest).removeprefix("sha256:")
        if normalized_previous != decision_digest:
            raise TransactionConflict(f"transaction {transaction_id} has a different content digest")
    if not fresh_groups:
        return CommitResult(
            status="already_committed",
            snapshot=snapshot,
            metrics={"full_model_copy_count": 0, "full_graph_validation_count": 0, "index_rebuild_count": 0},
            transaction_id=next(iter(transaction_groups)),
        )
    transaction_id = (
        next(iter(fresh_groups))
        if len(fresh_groups) == 1
        else fallback_transaction_id
    )
    fresh_transaction_ids = set(fresh_groups)
    fresh_decisions = [
        decision
        for decision in decisions
        if str(decision.get("transaction_id") or fallback_transaction_id) in fresh_transaction_ids
    ]

    isolated = StageSnapshot(
        ast=copy.deepcopy(snapshot.ast),
        transactions=dict(snapshot.transactions),
        overlays=copy.deepcopy(snapshot.overlays),
        presentation_overrides=copy.deepcopy(snapshot.presentation_overrides),
    )
    metrics = {"full_model_copy_count": 1, "full_graph_validation_count": 0, "index_rebuild_count": 0}
    try:
        for decision in _order_stage_decisions(isolated, fresh_decisions):
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
    for fresh_transaction_id in fresh_groups:
        committed.transactions[fresh_transaction_id] = group_digests[fresh_transaction_id]
    return CommitResult(status="committed", snapshot=committed, metrics=metrics, transaction_id=transaction_id)


def stage_candidate_revision(
    snapshot: StageSnapshot,
    decisions: Sequence[Mapping[str, Any]],
    *,
    expected_revision: int,
) -> CandidateRevision:
    """Apply review decisions without mutating the rollback revision."""

    baseline = StageSnapshot(
        ast=copy.deepcopy(snapshot.ast),
        transactions=dict(snapshot.transactions),
        overlays=copy.deepcopy(snapshot.overlays),
        presentation_overrides=copy.deepcopy(snapshot.presentation_overrides),
    )
    candidate = apply_stage_batch(snapshot, decisions, expected_revision)
    return CandidateRevision(baseline=baseline, candidate=candidate)
