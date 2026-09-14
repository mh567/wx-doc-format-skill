"""FU-3 convergence contracts for the three unfinished T06/T07 review items.

1. ``transaction`` has one apply-semantics source: ``_apply_*`` and
   ``revision_applied`` dispatch through one operation registry, and a parity
   battery fails if the mutation and the predicate drift apart.
2. The rolled-back transaction proof has a single owner: ``decision_ledger``
   verifies it and ``checkpoint_store`` only calls that owner.
3. The persistence module graph is acyclic: ``errors`` and ``response_identity``
   are low level modules, and no module dodges the import cycle with a
   function-local import.
"""

from __future__ import annotations

import ast
import copy
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

WXDF = SCRIPTS / "wxdf"
DECLARED = (
    "errors",
    "response_identity",
    "checkpoint_store",
    "request_append",
    "response_store",
    "decision_ledger",
    "transaction",
)


def _source(module: str) -> str:
    return (WXDF / f"{module}.py").read_text(encoding="utf-8")


def _module_level_imports(module: str) -> set[str]:
    tree = ast.parse(_source(module))
    edges: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            edges.add(node.module)
    return edges


def _all_imports(module: str) -> set[str]:
    tree = ast.parse(_source(module))
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            edges.add(node.module)
    return edges


def _ref() -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": 0, "end": 1, "unit": "unicode_code_point"},
    }


def _paragraph(text: str = "内容") -> dict:
    return {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
        "source_ref": _ref(),
    }


def _ast() -> dict:
    return {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {"p0": _paragraph()},
            "containers": {"root": {"kind": "document"}, "c1": {"kind": "document"}},
            "children": {"root": ["p0", "c1"], "c1": []},
            "associations": [],
            "resources": {},
        },
    }


def _revision(operation: str, value, targets: list[str]) -> dict:
    return {
        "revision_id": "r-" + operation,
        "decision": {"operation": operation, "value": value},
        "target_node_ids": targets,
        "property_scope": ["value"],
    }


def _decision(operation: str, value, targets: list[str], *, source_refs=None) -> dict:
    return {
        "decision_id": "d-" + operation,
        "transaction_id": "tx-fu3",
        "operation": operation,
        "value": value,
        "target_source_refs": [_ref()] if source_refs is None else source_refs,
        "target_node_ids": targets,
        "property_scope": ["value"],
        "expected_revision": 1,
    }


# ---------------------------------------------------------------------------
# Item 1: one apply-semantics source.
# ---------------------------------------------------------------------------

def test_operation_registry_drives_both_apply_and_predicate(monkeypatch) -> None:
    from wxdf import transaction

    registry = transaction._STAGE_OPERATIONS
    assert set(registry) == {
        "create_node",
        "update_node_semantics",
        "update_node_content",
        "update_role",
        "move_node",
        "update_association",
        "delete_node",
        "update_overlay",
        "set_presentation_override",
    }
    for name, entry in registry.items():
        assert callable(entry.apply), name
        assert callable(entry.applied), name

    calls: list[str] = []

    def fake_apply(snapshot, decision):  # noqa: ANN001 - test double
        calls.append("apply")

    def fake_applied(ast, revision, snapshot=None):  # noqa: ANN001 - test double
        calls.append("applied")
        return True

    monkeypatch.setitem(
        registry,
        "update_role",
        transaction._StageOperation(fake_apply, fake_applied),
    )
    snapshot = transaction.make_snapshot(_ast())
    # Both dispatchers must read the same registry entry.
    assert transaction.revision_applied(
        snapshot.ast,
        {"decision": {"operation": "update_role"}, "target_node_ids": ["p0"]},
        snapshot,
    ) is True
    transaction._apply_decision(snapshot, _decision("update_role", {"role": "note"}, ["p0"]))
    assert calls == ["applied", "apply"]


def test_apply_and_predicate_agree_for_every_operation() -> None:
    from wxdf import transaction

    def before_after(operation: str, value, targets: list[str], *, source_refs=None) -> None:
        snapshot = transaction.make_snapshot(_ast())
        revision = _revision(operation, value, targets)
        assert transaction.revision_applied(snapshot.ast, revision, snapshot) is False, operation
        result = transaction.apply_stage_batch(
            snapshot,
            [_decision(operation, value, targets, source_refs=source_refs)],
            expected_revision=1,
        )
        assert transaction.revision_applied(result.snapshot.ast, revision, result.snapshot) is True, operation

    before_after("update_role", {"role": "note"}, ["p0"])
    before_after("update_node_semantics", {"semantic_role": "paragraph"}, ["p0"])
    before_after(
        "update_node_content",
        {"inline_content": [{"kind": "text", "text": "新", "marks": []}]},
        ["p0"],
    )
    before_after("move_node", {"new_parent_id": "c1"}, ["p0"])
    before_after(
        "create_node",
        {"node_id": "p1", "parent_id": "root", "node": _paragraph("新增")},
        ["p1"],
    )
    before_after(
        "update_association",
        {"kind": "source_relation", "source_id": "p0", "target_id": "p0"},
        [],
        source_refs=[],
    )
    before_after("delete_node", None, ["p0"])
    before_after("update_overlay", {"overlay_alpha": "x"}, ["p0"])
    before_after(
        "set_presentation_override",
        {"list_item_id": "p0", "override_alpha": "x"},
        ["p0"],
    )


# ---------------------------------------------------------------------------
# Item 2: the transaction proof has a single owner.
# ---------------------------------------------------------------------------

def _rollback_checkpoint(*, applied: bool = True) -> dict:
    from wxdf import decision_ledger

    digest = "sha256:" + "c" * 64
    response = {
        "request_id": "role-0000",
        "response_sha256": "sha256:" + "b" * 64,
        "applied": applied,
        "apply_started": False,
        "transaction_id": "tx-1",
        "transaction_content_sha256": digest,
    }
    binding = decision_ledger.response_transaction_binding(response, "tx-1", digest)
    return {
        "responses": [response],
        "response_transaction_bindings": {"role-0000": binding},
        "applied_transactions": [{"transaction_id": "tx-1", "content_sha256": digest}],
        "transaction_content_digests": {"tx-1": digest},
    }


def test_rolled_back_proof_verification_is_owned_by_decision_ledger() -> None:
    from wxdf import decision_ledger

    assert callable(decision_ledger.verify_rolled_back_response_proof)
    # The rolled-back reassembly moved out of checkpoint_store into the owner.
    assert "rolled-back response proof changed" not in _source("checkpoint_store")
    assert "rolled-back response proof changed" in _source("decision_ledger")
    assert "verify_rolled_back_response_proof" in _source("checkpoint_store")


def test_rolled_back_proof_owner_accepts_a_consistent_checkpoint() -> None:
    from wxdf import decision_ledger

    decision_ledger.verify_rolled_back_response_proof(_rollback_checkpoint())


def test_rolled_back_proof_owner_rejects_a_tampered_binding() -> None:
    from wxdf import decision_ledger

    checkpoint = _rollback_checkpoint()
    checkpoint["response_transaction_bindings"]["role-0000"]["binding_sha256"] = "sha256:" + "0" * 64

    with pytest.raises(decision_ledger.LedgerError) as caught:
        decision_ledger.verify_rolled_back_response_proof(checkpoint)

    assert caught.value.code == "response_transaction_binding_mismatch"
    assert "rolled-back response proof changed for role-0000" in str(caught.value)


def test_rolled_back_proof_owner_rejects_a_rejected_response_with_metadata() -> None:
    from wxdf import decision_ledger

    with pytest.raises(decision_ledger.LedgerError) as caught:
        decision_ledger.verify_rolled_back_response_proof(_rollback_checkpoint(applied=False))

    assert caught.value.code == "response_application_state_mismatch"
    assert "rejected response application state changed for role-0000" in str(caught.value)


def test_rolled_back_proof_owner_rejects_extra_bindings() -> None:
    from wxdf import decision_ledger

    checkpoint = _rollback_checkpoint()
    checkpoint["response_transaction_bindings"]["ghost-0000"] = copy.deepcopy(
        checkpoint["response_transaction_bindings"]["role-0000"]
    )

    with pytest.raises(decision_ledger.LedgerError) as caught:
        decision_ledger.verify_rolled_back_response_proof(checkpoint)

    assert caught.value.code == "response_transaction_binding_mismatch"
    assert "rolled-back response bindings do not match applied responses" in str(caught.value)


# ---------------------------------------------------------------------------
# Item 3: acyclic module graph and the low-level modules.
# ---------------------------------------------------------------------------

def test_errors_module_owns_checkpoint_error() -> None:
    from wxdf import checkpoint_store, errors

    assert checkpoint_store.CheckpointError is errors.CheckpointError
    assert errors.CheckpointError.__module__ == "wxdf.errors"


def test_response_identity_owns_the_content_address_helpers() -> None:
    from wxdf import response_identity, response_store

    for name in ("content_digest", "request_digest", "response_digest", "record_response"):
        assert hasattr(response_identity, name), name
    assert isinstance(response_identity._APPLICATION_FIELDS, frozenset)
    # response_store re-exports the names so existing imports keep working.
    assert response_store.request_digest is response_identity.request_digest
    assert response_store.response_digest is response_identity.response_digest
    # The response seam names keep their response_store home.
    assert response_store.content_digest.__module__ == "wxdf.response_store"
    assert response_store.record_response.__module__ == "wxdf.response_store"


def test_declared_module_graph_is_acyclic() -> None:
    graph = {name: _module_level_imports(name) for name in DECLARED}
    color = {name: 0 for name in DECLARED}  # 0 white, 1 grey, 2 black

    def visit(name: str, stack: list[str]) -> None:
        color[name] = 1
        for dep in sorted(graph[name]):
            if dep not in DECLARED:
                continue
            if color[dep] == 1:
                raise AssertionError("import cycle: " + " -> ".join([*stack, name, dep]))
            if color[dep] == 0:
                visit(dep, [*stack, name])
        color[name] = 2

    for name in DECLARED:
        if color[name] == 0:
            visit(name, [])

    # Target direction: response_store -> checkpoint_store -> request_append ->
    # response_identity / errors.
    assert "checkpoint_store" in graph["response_store"]
    assert "request_append" in graph["checkpoint_store"]
    assert {"response_identity", "errors"} <= graph["request_append"]
    assert "response_store" not in _module_level_imports("request_append")
    assert "checkpoint_store" not in _all_imports("request_append")
    assert "response_store" not in _all_imports("request_append")
    assert "response_store" not in _all_imports("checkpoint_store")
    assert graph["errors"] == set()


def test_request_append_imports_without_checkpoint_store() -> None:
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import wxdf.request_append as journal; "
        "assert journal.append_native_waiting_requests.__module__ == 'wxdf.request_append'; "
        "print('ok')"
    ) % str(SCRIPTS)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
