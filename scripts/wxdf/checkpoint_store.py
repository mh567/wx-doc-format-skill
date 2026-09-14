"""File-backed checkpoint storage for modular runs.

The store serialises only JSON data.  Response records are committed before
the injected stage callback runs, so a process failure leaves a replayable
pending response instead of silently losing external work.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import CHECKPOINT_STATUSES, STAGES, ContractError
from .decision_ledger import (
    response_transaction_binding,
    snapshot_transaction_state,
    stored_transaction_digests,
)
from .digest import canonical_json, digest
from .validation import validate_checkpoint
from .request_append import (
    _exclusive_file_lock,
    _recover_native_request_append,
    append_native_waiting_requests,
)


class CheckpointError(RuntimeError):
    """Raised when a checkpoint cannot be safely stored or resumed."""

    def __init__(self, message: str, *, code: str = "checkpoint_error") -> None:
        self.code = code
        super().__init__(message)


# The native conversion profile stored by a valid native-v2 checkpoint.
_NATIVE_PROFILE = "wx-default-v2"


_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"created", "running", "failed", "cancelled"}),
    "running": frozenset({"running", "waiting_external", "completed", "completed_with_warnings", "failed", "cancelled"}),
    "waiting_external": frozenset({"waiting_external", "running", "completed", "completed_with_warnings", "failed", "cancelled"}),
    "completed": frozenset({"completed"}),
    "completed_with_warnings": frozenset({"completed_with_warnings"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}


def file_digest(path: Path | str | None) -> str:
    """Return a checkpoint digest for one local resource."""

    payload = b"" if path is None else _path(path).read_bytes()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def checkpoint_target_map(source_ast: Mapping[str, Any]) -> dict[str, Any]:
    """Build the revision-bound target map stored by native file mode."""

    document = source_ast["document"]
    entries: dict[str, dict[str, Any]] = {}
    for node_id, node in {**document["blocks"], **document["containers"]}.items():
        source_ref = node.get("source_ref") if isinstance(node, Mapping) else None
        entries[str(node_id)] = {
            "node_id": str(node_id),
            "source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
        }
    return {
        "revision": int(source_ast["revision"]),
        "entries": entries,
        "view_sha256": digest(entries),
    }


def candidate_baseline_digest(source_ast: Mapping[str, Any], candidates: list[Any]) -> str:
    """Bind a deterministic pre enhancement candidate set to its Source AST."""

    return digest({
        "source_ast": source_ast,
        "candidates": sorted(candidates, key=canonical_json),
    })


def overlay_entries(values: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Serialize keyed transaction overlays without losing node identity."""

    entries: list[dict[str, Any]] = []
    for node_id in sorted(values):
        entry = copy.deepcopy(dict(values[node_id]))
        entry["node_id"] = str(node_id)
        entries.append(entry)
    return entries


def _resource_entries(source_facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": str(resource_id), **copy.deepcopy(dict(resource))}
        for resource_id, resource in sorted(source_facts.get("resources", {}).items())
        if isinstance(resource, Mapping)
    ]


def _read_json_lines(path: Path, *, kind: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CheckpointError(f"cannot read native {kind} file: {path}", code="checkpoint_read_failed") from exc
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CheckpointError(
                f"invalid native {kind} JSON at line {line_number}",
                code=f"invalid_{kind}",
            ) from exc
        if not isinstance(value, dict):
            raise CheckpointError(
                f"native {kind} at line {line_number} must be an object",
                code=f"invalid_{kind}",
            )
        values.append(value)
    return values


def _verified_resource_bytes(
    source_path: Path | str,
    source_facts: Mapping[str, Any],
    source_store: Any,
) -> dict[str, bytes]:
    """Bind resource bytes whose content identity was captured by SourceFacts."""

    from .source_store import SourceStoreError

    verified: dict[str, bytes] = {}
    source_root = _path(source_path).parent
    unresolved_parts = {
        str(diagnostic.get("message"))
        for diagnostic in source_facts.get("diagnostics", [])
        if isinstance(diagnostic, Mapping)
        and diagnostic.get("code") == "external_resource_unresolved"
    }
    for resource_id, resource in source_facts.get("resources", {}).items():
        if not isinstance(resource, Mapping):
            continue
        source_ref = resource.get("source_ref")
        if not isinstance(source_ref, Mapping):
            continue
        part = source_ref.get("part")
        if not isinstance(part, str) or not part:
            continue
        try:
            value = source_store.resolve(dict(source_ref))
        except SourceStoreError:
            candidate = Path(part)
            if not candidate.is_absolute():
                candidate = source_root / candidate
            try:
                value = candidate.read_bytes()
            except OSError as exc:
                if part in unresolved_parts:
                    continue
                raise CheckpointError(
                    f"resource became unavailable while binding {resource_id}",
                    code="fingerprint_mismatch",
                ) from exc
            actual_digest = "sha256:" + hashlib.sha256(value).hexdigest()
            if actual_digest != resource.get("content_sha256"):
                raise CheckpointError(
                    f"resource changed while binding {resource_id}",
                    code="fingerprint_mismatch",
                )
            source_store.bind_part(part, value)
        if not isinstance(value, bytes):
            continue
        actual_digest = "sha256:" + hashlib.sha256(value).hexdigest()
        if actual_digest != resource.get("content_sha256"):
            raise CheckpointError(
                f"resource snapshot does not match SourceFacts for {resource_id}",
                code="fingerprint_mismatch",
            )
        verified[str(resource_id)] = value
    return verified


def write_native_waiting_checkpoint(
    *,
    checkpoint_path: Path | str,
    requests_path: Path | str,
    responses_path: Path | str,
    profile: str,
    list_policy: str,
    configuration: Mapping[str, Any],
    source_facts: Mapping[str, Any],
    source_ast: Mapping[str, Any],
    candidate_set: list[Any],
    modules: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    template_path: Path | str | None,
) -> dict[str, Any]:
    """Create and persist one complete native Checkpoint 2.0 waiting state."""

    from .decision_ledger import source_binding

    checkpoint_path = _path(checkpoint_path)
    requests_path = _path(requests_path)
    responses_path = _path(responses_path)
    requests_path.parent.mkdir(parents=True, exist_ok=True)
    requests = copy.deepcopy(requests)
    requests_path.write_text(
        "".join(canonical_json(item) + "\n" for item in requests),
        encoding="utf-8",
    )
    responses_path.write_text("", encoding="utf-8")

    source_facts = copy.deepcopy(dict(source_facts))
    source_ast = copy.deepcopy(dict(source_ast))
    configuration = copy.deepcopy(dict(configuration))
    contracts = {
        "source_facts": "1.0",
        "ast": "2.0",
        "patch": "2.0",
        "checkpoint": "2.0",
    }
    source_sha256 = str(source_facts["source"]["content_sha256"])
    resources = _resource_entries(source_facts)
    fingerprints = {
        "source": source_sha256,
        "template": file_digest(template_path),
        "resources": digest(resources),
        "configuration": digest(configuration),
        "contracts": digest(contracts),
        "modules": digest(modules),
        "parser": digest(source_facts.get("parser", {})),
        "protocol": "2.0",
        "requests": file_digest(requests_path),
    }
    candidate_baseline_id = candidate_baseline_digest(source_ast, candidate_set)
    checkpoint = {
        "checkpoint_schema_version": "2.0",
        "transaction_digest_version": "2.0",
        "run_id": "native-file-" + candidate_baseline_id[len("sha256:"):len("sha256:") + 20],
        "status": "waiting_external",
        "resume_stage": "relation",
        "profile": profile,
        "list_policy": list_policy,
        "configuration": configuration,
        "files": {
            "requests_path": str(requests_path.resolve()),
            "responses_path": str(responses_path.resolve()),
        },
        "source_facts": source_facts,
        "decision_ledger": {
            "schema_version": "2.0",
            "revision": 0,
            "revisions": [],
            "source_binding": source_binding(source_facts),
        },
        "source_ast": source_ast,
        "normalized_ast": None,
        "content_overlay": [],
        "presentation_override": [],
        "target_map": checkpoint_target_map(source_ast),
        "resource_summary": {
            "resources": resources,
            "source_sha256": source_sha256,
            "fingerprints": fingerprints,
        },
        "contracts": contracts,
        "modules": copy.deepcopy(modules),
        "requests": requests,
        "responses": [],
        "response_transaction_bindings": {},
        "applied_transactions": [],
        "transaction_content_digests": {},
        "budget": {"limit": len(requests), "consumed": 0, "waiting_seconds": 0.0},
        "round": 0,
        "candidate_baseline_id": candidate_baseline_id,
    }
    save_checkpoint(checkpoint, checkpoint_path)
    return checkpoint


def lock_native_checkpoint_identity(checkpoint_path: Path) -> dict[str, Any]:
    """Validate and migrate the native identity before responses are read."""

    checkpoint_path = _path(checkpoint_path)
    checkpoint = load_checkpoint(checkpoint_path)
    configuration = checkpoint.get("configuration")
    if not isinstance(configuration, Mapping):
        raise CheckpointError(
            "native checkpoint has no configuration identity",
            code="checkpoint_configuration_missing",
        )
    if configuration.get("pipeline") != "modular":
        raise CheckpointError(
            "native checkpoint pipeline is not modular",
            code="checkpoint_pipeline_mismatch",
        )
    if configuration.get("modular_profile") != "native-v2":
        raise CheckpointError(
            "native checkpoint modular profile is not native-v2",
            code="checkpoint_profile_mismatch",
        )
    if configuration.get("list_policy") != _NATIVE_PROFILE:
        raise CheckpointError(
            "native checkpoint configuration list policy is not wx-default-v2",
            code="checkpoint_list_policy_mismatch",
        )
    if checkpoint.get("list_policy") != _NATIVE_PROFILE:
        raise CheckpointError(
            "native checkpoint list policy is not wx-default-v2",
            code="checkpoint_list_policy_mismatch",
        )

    profile = checkpoint.get("profile")
    if profile == "wx_full_rules-native-v2":
        migrated = copy.deepcopy(checkpoint)
        migrated["profile"] = _NATIVE_PROFILE
        save_checkpoint(migrated, checkpoint_path)
        return migrated
    if profile != _NATIVE_PROFILE:
        raise CheckpointError(
            "native checkpoint profile is unsupported",
            code="checkpoint_profile_mismatch",
        )
    return checkpoint


def prepare_native_resume(
    checkpoint_path: Path | str,
    *,
    source_path: Path | str,
    template_path: Path | str | None,
    contracts: Mapping[str, str],
    modules: list[dict[str, Any]],
    configuration: Mapping[str, Any],
    source_store: Any,
) -> tuple[dict[str, Any], bool, dict[str, dict[str, Any]], dict[str, Any]]:
    """Validate identities and commit external responses before execution."""

    checkpoint_path = _path(checkpoint_path)
    checkpoint_lock_path = checkpoint_path.with_name(
        checkpoint_path.name + ".request-append.lock"
    )
    with _exclusive_file_lock(checkpoint_lock_path):
        from .publication import PublicationError, recover_pending_publications

        recovery_targets = [checkpoint_path]
        for field in ("output", "source_ast", "ast", "report", "report_md"):
            value = configuration.get(field)
            if value:
                recovery_targets.append(Path(value))
        try:
            recover_pending_publications(recovery_targets)
        except PublicationError as exc:
            raise CheckpointError(
                f"cannot recover native terminal generation: {exc}",
                code="checkpoint_recovery_failed",
            ) from exc
        checkpoint = load_checkpoint(checkpoint_path)
        request_lock_path = Path(checkpoint["files"]["requests_path"]).with_name(
            Path(checkpoint["files"]["requests_path"]).name + ".append.lock"
        )
        with _exclusive_file_lock(request_lock_path):
            _recover_native_request_append(checkpoint_path)
            checkpoint = load_checkpoint(checkpoint_path)
    if (
        checkpoint["status"] == "waiting_external"
        and checkpoint.get("resume_stage") != "relation"
    ):
        raise CheckpointError(
            f"unsupported native resume stage {checkpoint.get('resume_stage')!r}",
            code="unsupported_resume_stage",
        )
    baseline_id = str(checkpoint.get("candidate_baseline_id") or "")
    expected_run_id = "native-file-" + baseline_id.removeprefix("sha256:")[:20]
    if checkpoint["status"] == "waiting_external" and checkpoint.get("run_id") != expected_run_id:
        raise CheckpointError(
            "relation checkpoint candidate baseline identity changed",
            code="candidate_baseline_mismatch",
        )
    request_path = Path(checkpoint["files"]["requests_path"])
    from .parsers import parse

    current_source_facts = parse(source_path, {"source_store": source_store})
    verified_resources = _verified_resource_bytes(source_path, current_source_facts, source_store)
    try:
        template_bytes = b"" if template_path is None else _path(template_path).read_bytes()
    except OSError as exc:
        raise CheckpointError(
            f"cannot read native template file: {template_path}",
            code="checkpoint_read_failed",
        ) from exc
    template_digest = "sha256:" + hashlib.sha256(template_bytes).hexdigest()
    checkpoint = load_checkpoint(
        checkpoint_path,
        expected_fingerprints={
            "source": current_source_facts["source"]["content_sha256"],
            "template": template_digest,
            "configuration": digest(configuration),
            "contracts": digest(contracts),
            "modules": digest(modules),
            "parser": digest(current_source_facts.get("parser", {})),
            "resources": digest(_resource_entries(current_source_facts)),
            "requests": file_digest(request_path),
        },
    )
    if digest(current_source_facts) != digest(checkpoint["source_facts"]):
        raise CheckpointError("current SourceFacts do not match the checkpoint", code="fingerprint_mismatch")
    if checkpoint["status"] == "waiting_external":
        _validate_relation_resume_snapshot(checkpoint, current_source_facts)
        from .semantic_modules import build_native_candidate_baseline

        live_candidates = build_native_candidate_baseline(
            source_path,
            current_source_facts,
            checkpoint["source_ast"],
        )
        live_baseline_id = candidate_baseline_digest(checkpoint["source_ast"], live_candidates)
        if live_baseline_id != checkpoint["candidate_baseline_id"]:
            raise CheckpointError(
                "live native candidate baseline does not match the relation checkpoint",
                code="candidate_baseline_mismatch",
            )
    on_disk_requests = _read_json_lines(request_path, kind="request")
    if on_disk_requests != checkpoint["requests"]:
        raise CheckpointError("native request file does not match the checkpoint", code="request_identity_conflict")
    if checkpoint["status"] not in {"completed", "completed_with_warnings"}:
        for request in on_disk_requests:
            items = request.get("items")
            if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping):
                raise CheckpointError("native request has an invalid source view", code="invalid_request")
            request_ast = items[0].get("source_ast")
            if not isinstance(request_ast, Mapping):
                raise CheckpointError("native request has an invalid source AST", code="invalid_request")
            request_revision = request_ast.get("revision")
            if (
                items[0].get("revision") != request_revision
                or request.get("model_revision") != request_revision
                or request.get("view_sha256") != digest(request_ast)
            ):
                raise CheckpointError("native request view identity is inconsistent", code="request_identity_conflict")
            is_review = request.get("capability") == "document_review"
            if (
                checkpoint["status"] == "waiting_external"
                and not is_review
                and request_ast != checkpoint["source_ast"]
            ):
                raise CheckpointError("native request Source AST does not match the checkpoint", code="request_identity_conflict")
            packet = request.get("review_packet")
            if is_review and (
                not isinstance(packet, Mapping)
                or packet.get("baseline_revision") != request_revision
            ):
                raise CheckpointError("native review request baseline is inconsistent", code="request_identity_conflict")
    response_path = Path(checkpoint["files"]["responses_path"])
    responses = _read_json_lines(response_path, kind="response") if response_path.is_file() else []
    from .response_store import record_response

    recorded = checkpoint
    for response in responses:
        recorded, _outcome = record_response(recorded, response)
    if recorded != checkpoint and checkpoint["status"] == "waiting_external":
        save_checkpoint(recorded, checkpoint_path)
    response_map = {
        str(item["request_id"]): {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if key not in {"response_sha256", "applied", "apply_started", "transaction_id", "transaction_content_sha256"}
        }
        for item in recorded["responses"]
    }
    resume_options = {
        "restored_source_facts": copy.deepcopy(current_source_facts),
        "restored_source_ast": copy.deepcopy(recorded["source_ast"]),
        "decision_ledger": copy.deepcopy(recorded["decision_ledger"]),
        "checkpoint_resume_stage": recorded["resume_stage"],
        "restored_candidate_baseline_id": recorded["candidate_baseline_id"],
        "restored_resource_bytes": verified_resources,
        "restored_transaction_snapshot": {
            "transactions": _checkpoint_transactions(recorded),
            "overlays": _entries_to_overlay(recorded.get("content_overlay")),
            "presentation_overrides": _entries_to_overlay(recorded.get("presentation_override")),
        },
    }
    if template_path is not None:
        from .template import load_template_bundle

        resume_options["restored_template_bundle"] = load_template_bundle(
            template_path,
            template_bytes=template_bytes,
        )
    if checkpoint["status"] in {"completed", "completed_with_warnings"} or (
        checkpoint["status"] == "failed"
        and isinstance(checkpoint.get("review_outcome"), Mapping)
        and checkpoint["review_outcome"].get("status") == "rolled_back"
    ):
        return recorded, False, response_map, resume_options
    required_ids = {
        str(item["request_id"])
        for item in recorded["requests"]
        if isinstance(item, Mapping) and item.get("request_id")
    }
    ready = bool(required_ids) and set(response_map) == required_ids
    if ready:
        save_checkpoint(recorded, checkpoint_path)
    return recorded, ready, response_map, resume_options


def _validate_relation_resume_snapshot(
    checkpoint: Mapping[str, Any],
    source_facts: Mapping[str, Any],
) -> None:
    """Cross check one relation checkpoint against deterministic ledger replay."""

    from .decision_ledger import LedgerError, _replay_snapshot, project_ledger
    from .semantics.source_ast import build_source_ast
    from .transaction import TransactionConflict, make_snapshot

    try:
        base_ast = build_source_ast(source_facts)
        base_node_ids = set(base_ast["document"]["blocks"]) | set(base_ast["document"]["containers"])
        ledger_view = project_ledger(
            source_facts,
            checkpoint["decision_ledger"],
            existing_node_ids=base_node_ids,
        )
        blocking = {
            "revision_precondition_failed",
            "revision_conflict",
        }
        if any(item.get("code") in blocking for item in ledger_view.get("diagnostics", [])):
            raise LedgerError("relation checkpoint ledger cannot be replayed", code="replay_blocked")
        replayed = _replay_snapshot(source_facts, checkpoint["decision_ledger"], make_snapshot(base_ast))
    except (LedgerError, TransactionConflict, KeyError, TypeError, ValueError) as exc:
        raise CheckpointError(
            f"relation checkpoint replay failed: {exc}",
            code="resume_snapshot_mismatch",
        ) from exc

    expected_state = {
        "source_ast": replayed.ast,
        "content_overlay": overlay_entries(replayed.overlays),
        "presentation_override": overlay_entries(replayed.presentation_overrides),
    }
    actual_state = {
        "source_ast": checkpoint.get("source_ast"),
        "content_overlay": checkpoint.get("content_overlay"),
        "presentation_override": checkpoint.get("presentation_override"),
    }
    if actual_state != expected_state:
        raise CheckpointError(
            "relation checkpoint semantic snapshot does not match ledger replay",
            code="resume_snapshot_mismatch",
        )

    expected_transactions: dict[str, str] = {}
    for revision in ledger_view.get("revisions", []):
        transaction_id = revision.get("transaction_id")
        transaction_digest = revision.get("transaction_content_sha256")
        if not isinstance(transaction_id, str) or not isinstance(transaction_digest, str):
            raise CheckpointError(
                "relation checkpoint ledger has no transaction proof",
                code="resume_snapshot_mismatch",
            )
        previous = expected_transactions.get(transaction_id)
        if previous is not None and previous != transaction_digest:
            raise CheckpointError(
                "relation checkpoint ledger transaction digests conflict",
                code="resume_snapshot_mismatch",
            )
        expected_transactions[transaction_id] = transaction_digest
    stored_transactions = {
        str(transaction_id): str(digest)
        for transaction_id, digest in checkpoint.get("transaction_content_digests", {}).items()
    }
    stored_applied = {
        str(item.get("transaction_id")): str(item.get("content_sha256"))
        for item in checkpoint.get("applied_transactions", [])
        if isinstance(item, Mapping)
    }
    if stored_transactions != expected_transactions or stored_applied != expected_transactions:
        raise CheckpointError(
            "relation checkpoint transaction snapshot does not match its ledger",
            code="resume_snapshot_mismatch",
        )


def complete_native_checkpoint(
    checkpoint_path: Path | str,
    checkpoint: Mapping[str, Any],
    *,
    status: str,
    source_ast: Mapping[str, Any],
    normalized: Mapping[str, Any],
    decision_set: Mapping[str, Any],
    overlays: Mapping[str, Mapping[str, Any]],
    presentation_overrides: Mapping[str, Mapping[str, Any]],
    transactions: Mapping[str, str],
    transaction_id: str,
    applied_request_ids: set[str],
    request_transaction_ids: Mapping[str, str] | None = None,
    review_outcome: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Validate and optionally persist the terminal native checkpoint."""

    from .decision_ledger import accept_decisions

    completed = copy.deepcopy(dict(checkpoint))
    transaction_digests = {
        str(saved_id): "sha256:" + str(digest).removeprefix("sha256:")
        for saved_id, digest in transactions.items()
    }
    applied_transactions = [
        {"transaction_id": saved_id, "content_sha256": digest}
        for saved_id, digest in sorted(transaction_digests.items())
    ]
    request_transaction_ids = dict(request_transaction_ids or {})
    response_transaction_bindings: dict[str, dict[str, str]] = {}
    for response in completed["responses"]:
        if response.get("request_id") in applied_request_ids:
            response_transaction_id = request_transaction_ids.get(
                str(response["request_id"]),
                transaction_id,
            )
            response_transaction_digest = transaction_digests.get(response_transaction_id)
            if response_transaction_digest is None:
                raise CheckpointError(
                    f"transaction state is missing for response {response['request_id']}",
                    code="transaction_state_missing",
                )
            response["applied"] = True
            response["apply_started"] = False
            response["transaction_id"] = response_transaction_id
            response["transaction_content_sha256"] = response_transaction_digest
            response_transaction_bindings[str(response["request_id"])] = response_transaction_binding(
                response,
                response_transaction_id,
                response_transaction_digest,
            )
    fresh_decisions = [
        decision
        for decision in decision_set.get("decisions", [])
        if not decision.get("revision_id")
    ]
    accepted_ledger = completed["decision_ledger"]
    decisions_by_transaction: dict[str, list[Mapping[str, Any]]] = {}
    for decision in fresh_decisions:
        decision_transaction_id = str(decision.get("transaction_id") or transaction_id)
        decisions_by_transaction.setdefault(decision_transaction_id, []).append(decision)
    for accepted_transaction_id, accepted_decisions in sorted(
        decisions_by_transaction.items(),
        key=lambda item: min(int(decision.get("expected_revision") or 0) for decision in item[1]),
    ):
        accepted_ledger = accept_decisions(
            accepted_ledger,
            {
                "transaction_id": accepted_transaction_id,
                "decisions": accepted_decisions,
            },
        )
    completed.update({
        "status": status,
        "transaction_digest_version": "2.0",
        "resume_stage": None,
        "source_ast": copy.deepcopy(dict(source_ast)),
        "normalized_ast": copy.deepcopy(dict(normalized)),
        "decision_ledger": accepted_ledger,
        "content_overlay": overlay_entries(overlays),
        "presentation_override": overlay_entries(presentation_overrides),
        "target_map": checkpoint_target_map(source_ast),
        "response_transaction_bindings": response_transaction_bindings,
        "applied_transactions": applied_transactions,
        "transaction_content_digests": transaction_digests,
        "budget": {**completed["budget"], "consumed": len(completed["responses"])},
        "semantic_digest": normalized["semantic_digest"],
        "presentation_digest": normalized["presentation_digest"],
    })
    if review_outcome is not None:
        completed["review_outcome"] = copy.deepcopy(dict(review_outcome))
    validate_checkpoint_for_store(completed)
    validate_embedded_fingerprints(completed)
    if persist:
        save_checkpoint(completed, checkpoint_path)
    return completed


def _path(value: Path | str) -> Path:
    return value if isinstance(value, Path) else Path(value)


def _validate_legacy_checkpoint_replay(
    checkpoint: Mapping[str, Any],
    canonical_digests: Mapping[str, str],
) -> None:
    """Require old proof maps to describe the semantic state replay produces."""

    from .decision_ledger import LedgerError, _replay_snapshot, project_ledger
    from .semantics.source_ast import build_source_ast
    from .transaction import TransactionConflict, make_snapshot

    try:
        source_facts = checkpoint["source_facts"]
        ledger = checkpoint["decision_ledger"]
        base_ast = build_source_ast(source_facts)
        base_node_ids = set(base_ast["document"]["blocks"]) | set(base_ast["document"]["containers"])
        view = project_ledger(source_facts, ledger, existing_node_ids=base_node_ids)
        if any(
            item.get("code") in {"revision_precondition_failed", "revision_conflict"}
            for item in view.get("diagnostics", [])
        ):
            raise LedgerError("legacy ledger cannot be replayed", code="replay_blocked")
        replayed = _replay_snapshot(source_facts, ledger, make_snapshot(base_ast))
    except (LedgerError, TransactionConflict, KeyError, TypeError, ValueError) as exc:
        raise CheckpointError(
            f"legacy checkpoint replay failed: {exc}",
            code="resume_snapshot_mismatch",
        ) from exc
    expected_state = {
        "source_ast": replayed.ast,
        "content_overlay": overlay_entries(replayed.overlays),
        "presentation_override": overlay_entries(replayed.presentation_overrides),
    }
    stored_state = {
        "source_ast": checkpoint.get("source_ast"),
        "content_overlay": checkpoint.get("content_overlay"),
        "presentation_override": checkpoint.get("presentation_override"),
    }
    if stored_state != expected_state or set(replayed.transactions) != set(canonical_digests):
        raise CheckpointError(
            "legacy checkpoint semantic state does not match ledger replay",
            code="resume_snapshot_mismatch",
        )


def _migrate_transaction_digest_version(checkpoint: dict[str, Any]) -> bool:
    """Upgrade a valid unversioned T04 proof chain to canonical digest v2."""

    if checkpoint.get("transaction_digest_version") is not None:
        return False
    from .decision_ledger import LedgerError, ledger_transaction_digests

    ledger = checkpoint.get("decision_ledger", {})
    transaction_digests, applied_digests = stored_transaction_digests(checkpoint)
    if transaction_digests != applied_digests:
        raise CheckpointError(
            "legacy checkpoint transaction maps disagree",
            code="transaction_state_mismatch",
        )
    canonical_digests: dict[str, str] = {}
    try:
        canonical_digests = ledger_transaction_digests(
            ledger,
            version="2.0",
            verify_stored=False,
        )
        current_digests = ledger_transaction_digests(ledger, version="2.0")
    except LedgerError:
        current_digests = {}
    if current_digests == transaction_digests:
        checkpoint["transaction_digest_version"] = "2.0"
        return True
    if (
        current_digests
        and not transaction_digests
        and checkpoint.get("status") not in {"completed", "completed_with_warnings", "failed"}
        and not any(
            isinstance(response, Mapping) and response.get("applied") is True
            for response in checkpoint.get("responses", [])
        )
    ):
        checkpoint["transaction_digest_version"] = "2.0"
        return True
    try:
        legacy_digests = ledger_transaction_digests(ledger, version="1.0")
    except LedgerError as exc:
        raise CheckpointError(
            f"legacy checkpoint ledger proof changed: {exc}",
            code="transaction_state_mismatch",
        ) from exc
    if set(legacy_digests) != set(transaction_digests):
        if checkpoint.get("status") not in {"completed", "completed_with_warnings", "failed"}:
            checkpoint["transaction_digest_version"] = "1.0"
            return True
        raise CheckpointError(
            "legacy checkpoint transaction IDs disagree",
            code="transaction_state_mismatch",
        )
    bindings = checkpoint.get("response_transaction_bindings") or {}
    for response in checkpoint.get("responses", []):
        if not isinstance(response, Mapping) or response.get("applied") is not True:
            continue
        request_id = str(response.get("request_id") or "")
        transaction_id = str(response.get("transaction_id") or "")
        stored_digest = transaction_digests.get(transaction_id)
        if response.get("transaction_content_sha256") != stored_digest:
            raise CheckpointError(
                f"legacy response proof changed for {request_id}",
                code="transaction_state_mismatch",
            )
        expected_binding = response_transaction_binding(
            response,
            transaction_id,
            str(stored_digest or ""),
        )
        if bindings.get(request_id) != expected_binding:
            raise CheckpointError(
                f"legacy response binding changed for {request_id}",
                code="response_transaction_binding_mismatch",
            )
    _validate_legacy_checkpoint_replay(checkpoint, canonical_digests)
    for revision in ledger.get("revisions", []):
        if isinstance(revision, dict):
            revision["transaction_content_sha256"] = canonical_digests[str(revision["transaction_id"])]
    checkpoint["transaction_content_digests"] = copy.deepcopy(canonical_digests)
    checkpoint["applied_transactions"] = [
        {"transaction_id": transaction_id, "content_sha256": digest}
        for transaction_id, digest in sorted(canonical_digests.items())
    ]
    migrated_bindings: dict[str, dict[str, str]] = {}
    for response in checkpoint.get("responses", []):
        if not isinstance(response, dict) or response.get("applied") is not True:
            continue
        transaction_id = str(response["transaction_id"])
        digest = canonical_digests[transaction_id]
        response["transaction_content_sha256"] = digest
        migrated_bindings[str(response["request_id"])] = response_transaction_binding(
            response,
            transaction_id,
            digest,
        )
    checkpoint["response_transaction_bindings"] = migrated_bindings
    checkpoint["transaction_digest_version"] = "2.0"
    return True


def validate_checkpoint_for_store(checkpoint: Mapping[str, Any]) -> None:
    """Validate one checkpoint before it is persisted or trusted on load."""

    try:
        validate_checkpoint(checkpoint)
    except ContractError as exc:
        stable_codes = {
            "response_application_state_mismatch",
            "response_transaction_binding_mismatch",
        }
        code = exc.code if exc.code in stable_codes else "invalid_checkpoint"
        raise CheckpointError(f"checkpoint validation failed: {exc}", code=code) from exc
    except Exception as exc:
        raise CheckpointError(f"checkpoint validation failed: {exc}", code="invalid_checkpoint") from exc
    terminal_rollback = (
        checkpoint.get("status") == "failed"
        and isinstance(checkpoint.get("review_outcome"), Mapping)
        and checkpoint["review_outcome"].get("status") == "rolled_back"
    )
    if checkpoint.get("status") in {"completed", "completed_with_warnings"} or terminal_rollback:
        if checkpoint.get("transaction_digest_version") != "2.0":
            raise CheckpointError(
                "completed checkpoint has no supported transaction digest version",
                code="transaction_state_mismatch",
            )
        try:
            from .decision_ledger import ledger_transaction_digests

            ledger_digests = ledger_transaction_digests(
                checkpoint.get("decision_ledger", {}),
                version="2.0",
            )
        except Exception as exc:
            raise CheckpointError(
                f"completed ledger transaction content changed: {exc}",
                code="transaction_state_mismatch",
            ) from exc
        transaction_digests, applied_digests = stored_transaction_digests(checkpoint)
        if ledger_digests != transaction_digests or applied_digests != transaction_digests:
            raise CheckpointError(
                "completed transaction proofs do not match the decision ledger",
                code="transaction_state_mismatch",
            )
    if terminal_rollback:
        bindings = checkpoint.get("response_transaction_bindings") or {}
        bound_ids: set[str] = set()
        for response in checkpoint.get("responses", []):
            request_id = str(response.get("request_id") or "")
            if response.get("applied") is True:
                transaction_id = str(response.get("transaction_id") or "")
                digest = response.get("transaction_content_sha256")
                expected_binding = response_transaction_binding(
                    response,
                    transaction_id,
                    str(digest or ""),
                )
                if (
                    applied_digests.get(transaction_id) != digest
                    or bindings.get(request_id) != expected_binding
                ):
                    raise CheckpointError(
                        f"rolled-back response proof changed for {request_id}",
                        code="response_transaction_binding_mismatch",
                    )
                bound_ids.add(request_id)
            elif (
                response.get("applied") is not False
                or response.get("apply_started", False) is not False
                or response.get("transaction_id") is not None
                or response.get("transaction_content_sha256") is not None
                or request_id in bindings
            ):
                raise CheckpointError(
                    f"rejected response application state changed for {request_id}",
                    code="response_application_state_mismatch",
                )
        if set(bindings) != bound_ids:
            raise CheckpointError(
                "rolled-back response bindings do not match applied responses",
                code="response_transaction_binding_mismatch",
            )


def validate_embedded_fingerprints(checkpoint: Mapping[str, Any]) -> None:
    """Require embedded identities and request or response digests to match."""

    from .response_store import request_digest, response_digest

    resource_summary = checkpoint.get("resource_summary")
    fingerprints = resource_summary.get("fingerprints") if isinstance(resource_summary, Mapping) else None
    if not isinstance(fingerprints, Mapping):
        return
    for fingerprint, field in (
        ("configuration", "configuration"),
        ("contracts", "contracts"),
        ("modules", "modules"),
    ):
        if fingerprint in fingerprints and field in checkpoint:
            if fingerprints[fingerprint] != digest(checkpoint[field]):
                raise CheckpointError(
                    f"checkpoint {field} identity does not match its fingerprint",
                    code="fingerprint_mismatch",
                )
    if "configuration" in checkpoint:
        source_facts = checkpoint.get("source_facts")
        if isinstance(source_facts, Mapping):
            embedded = {
                "parser": source_facts.get("parser", {}),
                "resources": _resource_entries(source_facts),
            }
            for fingerprint, value in embedded.items():
                if fingerprint in fingerprints and fingerprints[fingerprint] != digest(value):
                    raise CheckpointError(
                        f"checkpoint {fingerprint} identity does not match its fingerprint",
                        code="fingerprint_mismatch",
                    )
    seen_requests: set[str] = set()
    for request in checkpoint.get("requests", []):
        if not isinstance(request, Mapping):
            continue
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id or request_id in seen_requests:
            raise CheckpointError("checkpoint request identities are invalid", code="request_identity_conflict")
        seen_requests.add(request_id)
        stored_digest = request.get("request_sha256")
        if stored_digest is not None and stored_digest != request_digest(request):
            raise CheckpointError(
                f"request content changed for {request_id}",
                code="request_identity_conflict",
            )
    for response in checkpoint.get("responses", []):
        if not isinstance(response, Mapping):
            continue
        request_id = response.get("request_id")
        stored_digest = response.get("response_sha256")
        if stored_digest is None or stored_digest != response_digest(response):
            raise CheckpointError(
                f"stored response content changed for {request_id}",
                code="response_identity_mismatch",
            )


def _write_json_atomic(checkpoint: Mapping[str, Any], path: Path, *, crash_at: str | None = None) -> None:
    from .publication import PublicationError, publish_files_atomically

    payload = (canonical_json(checkpoint) + "\n").encode("utf-8")
    try:
        publish_files_atomically({path: payload}, crash_at=crash_at)
    except PublicationError as exc:
        code = "injected_failure" if crash_at is not None else "checkpoint_write_failed"
        raise CheckpointError(str(exc), code=code) from exc


def save_checkpoint(
    checkpoint: Mapping[str, Any],
    path: Path | str | None = None,
    *,
    run_path: Path | str | None = None,
    crash_at: str | None = None,
) -> Path:
    """Validate and atomically persist one complete checkpoint."""

    if path is None:
        path = run_path
    if path is None:
        candidate = checkpoint.get("checkpoint_path") if isinstance(checkpoint, Mapping) else None
        path = candidate
    if path is None:
        raise CheckpointError("checkpoint path is required", code="missing_checkpoint_path")
    if not isinstance(checkpoint, Mapping):
        raise CheckpointError("checkpoint must be a mapping", code="invalid_checkpoint")
    payload = copy.deepcopy(dict(checkpoint))
    payload.setdefault("applied_transactions", [])
    payload.setdefault("transaction_content_digests", {})
    _migrate_transaction_digest_version(payload)
    ledger = payload.get("decision_ledger")
    source_facts = payload.get("source_facts")
    if (
        isinstance(ledger, Mapping)
        and ledger.get("schema_version") == "2.0"
        and isinstance(source_facts, Mapping)
    ):
        from .decision_ledger import source_binding

        ledger["source_binding"] = source_binding(source_facts)
    validate_checkpoint_for_store(payload)
    destination = _path(path)
    _write_json_atomic(payload, destination, crash_at=crash_at)
    return destination


def _compare_fingerprints(checkpoint: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    resource_summary = checkpoint.get("resource_summary")
    stored = resource_summary.get("fingerprints", {}) if isinstance(resource_summary, Mapping) else {}
    if not isinstance(stored, Mapping):
        raise CheckpointError("checkpoint fingerprints are invalid", code="invalid_fingerprints")
    for name, value in expected.items():
        if name in {"source_sha256", "source"}:
            actual = resource_summary.get("source_sha256") if isinstance(resource_summary, Mapping) else None
            actual = stored.get("source", actual)
        else:
            actual = stored.get(name)
        if actual != value:
            raise CheckpointError(f"fingerprint mismatch for {name}", code="fingerprint_mismatch")


def _entries_to_overlay(entries: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        node_id = entry.get("node_id") or entry.get("target_node_id") or entry.get("list_item_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        value = copy.deepcopy(dict(entry))
        value.pop("node_id", None)
        result[node_id] = value
    return result


def _overlay_to_entries(overlays: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node_id in sorted(overlays):
        entry = copy.deepcopy(dict(overlays[node_id]))
        entry["node_id"] = node_id
        result.append(entry)
    return result


def _checkpoint_transactions(checkpoint: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["transaction_id"]): str(item["content_sha256"])
        for item in checkpoint.get("applied_transactions", [])
        if isinstance(item, Mapping) and item.get("transaction_id") and item.get("content_sha256")
    }


def load_checkpoint(
    path: Path | str,
    expected_fingerprints: Mapping[str, Any] | None = None,
    *,
    expected_contracts: Mapping[str, str] | None = None,
    current_source_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load, validate and optionally bind a checkpoint to current inputs."""

    source = _path(path)
    from .publication import PublicationError, recover_pending_publications

    try:
        recover_pending_publications(source)
    except PublicationError as exc:
        raise CheckpointError(
            f"cannot recover checkpoint publication: {exc}",
            code="checkpoint_recovery_failed",
        ) from exc
    try:
        checkpoint = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read checkpoint: {source}", code="checkpoint_read_failed") from exc
    if not isinstance(checkpoint, dict):
        raise CheckpointError("checkpoint root must be an object", code="invalid_checkpoint")
    checkpoint.setdefault("applied_transactions", [])
    checkpoint.setdefault("transaction_content_digests", {})
    migrated_transaction_digests = _migrate_transaction_digest_version(checkpoint)
    validate_checkpoint_for_store(checkpoint)
    validate_embedded_fingerprints(checkpoint)
    if expected_fingerprints:
        _compare_fingerprints(checkpoint, expected_fingerprints)
    if expected_contracts:
        actual = checkpoint.get("contracts", {})
        for name, value in expected_contracts.items():
            if actual.get(name) != value:
                raise CheckpointError(f"contract mismatch for {name}", code="contract_mismatch")
    if current_source_facts is not None:
        from .decision_ledger import LedgerError, _replay_snapshot, project_ledger, source_binding
        from .semantics.source_ast import build_source_ast
        from .transaction import make_snapshot

        try:
            current_digest = current_source_facts.get("source", {}).get("content_sha256")
            stored_digest = checkpoint["source_facts"].get("source", {}).get("content_sha256")
            source_matches = current_digest == stored_digest and digest(current_source_facts) == digest(checkpoint["source_facts"])
            if not source_matches:
                unbound_ledger = copy.deepcopy(checkpoint["decision_ledger"])
                unbound_ledger.pop("source_binding", None)
                projection = project_ledger(current_source_facts, unbound_ledger, check_source_binding=False)
                checkpoint["decision_ledger_projection"] = projection
                checkpoint["decision_ledger"] = copy.deepcopy(projection)
                checkpoint["content_overlay"] = []
                checkpoint["presentation_override"] = []
                checkpoint["source_ast"] = build_source_ast(current_source_facts)
                checkpoint["normalized_ast"] = None
                checkpoint["target_map"] = {
                    "revision": checkpoint["source_ast"]["revision"],
                    "entries": {},
                    "view_sha256": digest({}),
                }
                checkpoint["applied_transactions"] = []
                checkpoint["transaction_content_digests"] = {}
                checkpoint["source_facts"] = copy.deepcopy(dict(current_source_facts))
                checkpoint["decision_ledger"]["source_binding"] = source_binding(current_source_facts)
                resource_summary = checkpoint.get("resource_summary")
                if isinstance(resource_summary, Mapping):
                    resource_summary = copy.deepcopy(dict(resource_summary))
                    resource_summary["source_sha256"] = current_digest
                    fingerprints = resource_summary.get("fingerprints")
                    if isinstance(fingerprints, Mapping):
                        fingerprints = copy.deepcopy(dict(fingerprints))
                        fingerprints["source"] = current_digest
                        resource_summary["fingerprints"] = fingerprints
                    checkpoint["resource_summary"] = resource_summary
                save_checkpoint(checkpoint, source)
                raise CheckpointError(
                    "current source facts do not match the checkpoint source",
                    code="source_changed",
                )
            checkpoint["decision_ledger"].setdefault("source_binding", source_binding(current_source_facts))
            projection = project_ledger(current_source_facts, checkpoint["decision_ledger"])
            checkpoint["decision_ledger_projection"] = projection
            checkpoint["decision_ledger"] = copy.deepcopy(projection)
            source_ast = build_source_ast(current_source_facts)
            blocking = {
                "revision_precondition_failed",
                "revision_conflict",
            }
            if any(item.get("code") in blocking for item in projection.get("diagnostics", [])):
                raise CheckpointError("decision ledger replay is blocked", code="replay_blocked")
            else:
                snapshot = make_snapshot(source_ast)
                snapshot.transactions = _checkpoint_transactions(checkpoint)
                snapshot.overlays = _entries_to_overlay(checkpoint.get("content_overlay"))
                snapshot.presentation_overrides = _entries_to_overlay(checkpoint.get("presentation_override"))
                recovered = _replay_snapshot(current_source_facts, projection, snapshot)
                checkpoint["source_ast"] = recovered.ast
                checkpoint["content_overlay"] = _overlay_to_entries(recovered.overlays)
                checkpoint["presentation_override"] = _overlay_to_entries(recovered.presentation_overrides)
                applied_transactions, transaction_digests = snapshot_transaction_state(recovered.transactions)
                checkpoint["applied_transactions"] = applied_transactions
                checkpoint["transaction_content_digests"] = transaction_digests
            checkpoint["source_facts"] = copy.deepcopy(dict(current_source_facts))
            resource_summary = checkpoint.get("resource_summary")
            if isinstance(resource_summary, Mapping):
                resource_summary = copy.deepcopy(dict(resource_summary))
                resource_summary["source_sha256"] = current_digest
                fingerprints = resource_summary.get("fingerprints")
                if isinstance(fingerprints, Mapping):
                    fingerprints = copy.deepcopy(dict(fingerprints))
                    fingerprints["source"] = current_digest
                    resource_summary["fingerprints"] = fingerprints
                checkpoint["resource_summary"] = resource_summary
            checkpoint["normalized_ast"] = None
            checkpoint["target_map"] = {
                "revision": checkpoint["source_ast"]["revision"],
                "entries": {},
                "view_sha256": digest({}),
            }
            validate_checkpoint_for_store(checkpoint)
        except LedgerError as exc:
            raise CheckpointError(f"decision ledger projection failed: {exc}", code=exc.code) from exc
        except CheckpointError:
            raise
        except Exception as exc:
            raise CheckpointError(f"source AST recovery failed: {exc}", code="source_ast_rebuild_failed") from exc
    if migrated_transaction_digests:
        _write_json_atomic(checkpoint, source)
    return checkpoint


def validate_status_transition(current: str, target: str) -> None:
    if current not in CHECKPOINT_STATUSES or target not in CHECKPOINT_STATUSES:
        raise CheckpointError(f"unknown checkpoint status {current!r} or {target!r}", code="unknown_status")
    if target not in _TRANSITIONS[current]:
        raise CheckpointError(f"invalid status transition {current!r} -> {target!r}", code="invalid_status_transition")


def transition_status(checkpoint: Mapping[str, Any], status: str, *, resume_stage: str | None = None) -> dict[str, Any]:
    """Return a validated status transition without mutating the input."""

    validate_checkpoint_for_store(checkpoint)
    current = str(checkpoint["status"])
    validate_status_transition(current, status)
    if status == "waiting_external" and resume_stage not in STAGES:
        raise CheckpointError("waiting_external requires a valid resume stage", code="invalid_resume_stage")
    if status != "waiting_external" and resume_stage is not None:
        raise CheckpointError("resume stage is only valid for waiting_external", code="invalid_resume_stage")
    result = copy.deepcopy(dict(checkpoint))
    result["status"] = status
    result["resume_stage"] = resume_stage if status == "waiting_external" else None
    validate_checkpoint_for_store(result)
    return result


__all__ = [
    "CheckpointError",
    "append_native_waiting_requests",
    "checkpoint_target_map",
    "complete_native_checkpoint",
    "file_digest",
    "load_checkpoint",
    "lock_native_checkpoint_identity",
    "overlay_entries",
    "prepare_native_resume",
    "save_checkpoint",
    "transition_status",
    "validate_checkpoint_for_store",
    "validate_embedded_fingerprints",
    "validate_status_transition",
    "write_native_waiting_checkpoint",
]
