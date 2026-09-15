"""Native lifecycle stages for the native-v2 conversion chain."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
from collections.abc import MutableMapping
from typing import Any, Callable, Iterable, Iterator, Mapping

from .contracts import validate_capability_payload
from .parsers import digest_source_facts, parse, summarize_source_facts
from .registry import ModuleDescriptor, PlanError, RegisteredModule
from .semantics.appendices import audit_native_appendix
from .semantics.source_ast import build_source_ast, materialize_list_semantics
from .semantic_modules import (
    ModuleExecutionContext,
    collect_native_candidates,
)


class LedgerReplayBlocked(RuntimeError):
    """Raised when a supplied decision ledger forbids the native replay."""

    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__("cannot apply invalidated or conflicted revisions")


class UnresolvedDecisionError(RuntimeError):
    """Raised when the registered failure policy forbids publishing unresolved groups."""

    def __init__(self, unresolved: list[dict[str, Any]]) -> None:
        self.unresolved = list(unresolved)
        super().__init__(
            f"{len(self.unresolved)} unresolved decision group(s) block publication"
        )


class NativeFileProtocolPause(RuntimeError):
    """Stop the native chain after request capture and before resolution."""

    def __init__(self) -> None:
        super().__init__("native file protocol is waiting for external responses")


class NativeEnhancementBlocked(RuntimeError):
    """Stop native execution when a requested enhancement was not validated."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        self.result = dict(result)
        self.diagnostics = [
            dict(diagnostic)
            for record in result.get("results", [])
            if isinstance(record, Mapping)
            for diagnostic in record.get("diagnostics", [])
            if isinstance(diagnostic, Mapping)
        ]
        super().__init__("native enhancement response validation failed")




def _native_source_ast(context: ModuleExecutionContext) -> dict[str, Any]:
    facts = context.require("source_facts")
    with context.stage("relation"):
        restored = (context.request.options or {}).get("restored_source_ast")
        ast = (
            json.loads(json.dumps(restored, ensure_ascii=False))
            if isinstance(restored, Mapping)
            else build_source_ast(facts)
        )
    context.provide("source_ast", ast)
    evidence = {
        "block_count": len(ast["document"]["blocks"]),
        "container_count": len(ast["document"]["containers"]),
    }
    context.evidence["native-source-parser"] = evidence
    return ast


def _native_candidate_set(context: ModuleExecutionContext) -> list[Mapping[str, Any]]:
    collect_candidates = collect_native_candidates
    candidate_groups = {
        "source_region_candidates": context.require("source_region_candidates"),
        "document_structure_candidates": context.require("document_structure_candidates"),
        "protected_content_candidates": context.require("protected_content_candidates"),
        "list_semantics_candidates": context.require("list_semantics_candidates"),
        "appendix_semantics_candidates": context.require("appendix_semantics_candidates"),
        "table_semantics_candidates": context.require("table_semantics_candidates"),
        "caption_semantics_candidates": context.require("caption_semantics_candidates"),
    }
    if "paragraph_semantics_candidates" in context.artifacts:
        candidate_groups["paragraph_semantics_candidates"] = context.require("paragraph_semantics_candidates")
    elif "source_facts" in context.artifacts:
        from .semantics.paragraphs import collect_paragraph_candidates

        candidate_groups["paragraph_semantics_candidates"] = collect_paragraph_candidates(
            context.require("source_facts"),
            context.require("source_ast"),
            caption_semantics_result=context.artifacts.get("caption_semantics_result"),
        )
    candidates = collect_candidates(**candidate_groups)
    baseline_candidates = list(candidates)
    options = context.request.options or {}
    supplied_ledger = options.get("decision_ledger")
    ledger_view: dict[str, Any] | None = None
    ledger_replay: dict[str, Any] | None = None
    if supplied_ledger is not None:
        from .decision_ledger import LedgerError, project_ledger, revision_decision

        if not isinstance(supplied_ledger, Mapping) or not isinstance(supplied_ledger.get("source_binding"), Mapping):
            raise LedgerError(
                "native decision ledger must carry a SourceFacts binding",
                code="source_binding_missing",
            )
        facts = context.require("source_facts")
        source_ast = context.require("source_ast")
        existing_node_ids = set(source_ast["document"]["blocks"]) | set(source_ast["document"]["containers"])
        ledger_view = project_ledger(facts, supplied_ledger, existing_node_ids=existing_node_ids)
        blocking = [
            item
            for item in ledger_view.get("diagnostics", [])
            if item.get("code") in {"revision_precondition_failed", "revision_conflict"}
        ]
        if blocking:
            context.provide("ledger_view", ledger_view)
            context.provide("ledger_replay", {
                "enabled": True,
                "error_code": "replay_blocked",
                "diagnostics": blocking,
            })
            raise LedgerReplayBlocked(blocking)
        if options.get("checkpoint_resume_stage") != "relation":
            for revision in ledger_view.get("active_revisions", []):
                ledger_decision = revision_decision(revision, source_ast["revision"])
                candidates.append({
                    "candidate_id": f"ledger-revision-{revision['revision_id']}",
                    "module_id": "decision_ledger",
                    "module_version": str(revision.get("resolver_version") or "1.0"),
                    "target_source_refs": _copy_refs(revision.get("target_source_refs")),
                    "target_node_ids": list(revision.get("target_node_ids") or []),
                    "proposal": {
                        "operation": ledger_decision["operation"],
                        "property_scope": list(revision.get("property_scope") or []),
                        "value": dict(ledger_decision.get("value") or {}),
                        "invalidates": list(ledger_decision.get("invalidates") or []),
                    },
                    "positive_evidence": [{"kind": "ledger_revision", "revision_id": revision["revision_id"]}],
                    "negative_evidence": [],
                    "scope": {"stages": list(revision.get("invalidates") or [])},
                    "constraints": [],
                    "rationale": "accepted revision restored from the decision ledger",
                })
        ledger_replay = {
            "enabled": True,
            "ledger_revision": supplied_ledger.get("revision"),
            "active_revision_count": len(ledger_view.get("active_revisions") or []),
            "baseline_ast_revision": source_ast["revision"],
        }
    expected_baseline = options.get("restored_candidate_baseline_id")
    if expected_baseline is not None:
        from .checkpoint_store import CheckpointError, candidate_baseline_digest

        actual_baseline = candidate_baseline_digest(context.require("source_ast"), baseline_candidates)
        if actual_baseline != expected_baseline:
            raise CheckpointError(
                f"relation checkpoint candidate baseline changed: expected {expected_baseline}, got {actual_baseline}",
                code="candidate_baseline_mismatch",
            )
    enhancement_candidates, enhancement_result = _native_enhancement_candidates(
        context.require("source_ast"),
        options,
    )
    candidates.extend(enhancement_candidates)
    context.provide("candidate_set", candidates)
    context.provide("ledger_view", ledger_view)
    context.provide("ledger_replay", ledger_replay)
    context.provide("enhancement_result", enhancement_result)
    context.evidence["native-candidate-set"] = {
        "rule_candidate_count": len(candidates) - len(enhancement_candidates),
        "enhancement_candidate_count": len(enhancement_candidates),
        "enhancement_status": enhancement_result["status"],
    }
    if options.get("protocol_mode") == "generate_requests" and enhancement_result["requested_capabilities"]:
        raise NativeFileProtocolPause()
    blocking_results = [
        record
        for record in enhancement_result.get("results", [])
        if record.get("status") != "completed"
    ]
    if blocking_results:
        raise NativeEnhancementBlocked(enhancement_result)
    return candidates


def _native_enhancement_candidates(
    source_ast: Mapping[str, Any],
    options: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    """Collect validated native candidates from explicitly enabled adapters."""

    from llm_enhancer import CAPABILITY_REGISTRY, normalize_mode, should_enhance

    from .enhancement import EnhancementExecutor, build_native_request, serialize_native_request

    mode = str(options.get("llm_enhance") or "off")
    if mode == "off":
        return [], {
            "status": "disabled",
            "mode": mode,
            "requested_capabilities": [],
            "candidate_count": 0,
            "consumed": 0,
            "results": [],
        }

    normalized_mode = normalize_mode(mode)
    selected = [
        capability
        for capability in CAPABILITY_REGISTRY
        if capability != "document_review"
        and should_enhance({}, capability, normalized_mode)
    ]
    configured_budget = options.get("enhancement_budget", len(selected))
    budget = configured_budget if isinstance(configured_budget, int) and not isinstance(configured_budget, bool) else -1
    timeout = options.get("enhancement_timeout_seconds", 30.0)
    timeout = float(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else 30.0
    adapter = options.get("enhancement_call")
    request_sink = options.get("enhancement_request_sink")
    executor = EnhancementExecutor()
    candidates: list[Mapping[str, Any]] = []
    records: list[dict[str, Any]] = []
    consumed = 0

    for capability in selected:
        remaining = max(0, budget - consumed)
        request = build_native_request(
            capability,
            source_ast,
            budget=min(1, remaining),
            timeout_seconds=timeout,
            request_id=f"native-{source_ast['revision']}-{capability}",
        )
        if isinstance(request_sink, list):
            request_sink.append(serialize_native_request(request))
        if options.get("protocol_mode") == "generate_requests":
            result = executor.execute(request, responses=[])
        else:
            result = executor.execute(request, call=adapter)
        consumed += result.consumed
        for candidate in result.candidates:
            item = json.loads(json.dumps(candidate, ensure_ascii=False))
            item["candidate_id"] = f"enhancement-{capability}-{item['candidate_id']}"
            item["module_id"] = f"enhancement-{capability}"
            candidates.append(item)
        records.append(
            {
                "capability": capability,
                "status": result.status,
                "candidate_count": len(result.candidates),
                "consumed": result.consumed,
                "diagnostics": [dict(item) for item in result.diagnostics],
                "request_summary": dict(result.request_summary),
            }
        )

    statuses = {record["status"] for record in records}
    if not selected:
        status = "no_targets"
    elif statuses == {"completed"}:
        status = "completed"
    elif "completed" in statuses:
        status = "completed_with_warnings"
    elif len(statuses) == 1:
        status = next(iter(statuses))
    else:
        status = "failed"
    return candidates, {
        "status": status,
        "mode": mode,
        "requested_capabilities": selected,
        "candidate_count": len(candidates),
        "consumed": consumed,
        "budget": {"limit": budget, "remaining": max(0, budget - consumed)},
        "results": records,
    }


def _copy_refs(value: Any) -> list[dict[str, Any]]:
    import copy as _copy

    if not isinstance(value, (list, tuple)):
        return []
    return [_copy.deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]


def _native_resolver(context: ModuleExecutionContext) -> dict[str, Any]:
    from .resolver import resolve

    source_ast = context.require("source_ast")
    decision_set = resolve(
        context.require("candidate_set"),
        {"revision": source_ast["revision"]},
        context.require("ledger_view"),
    )
    context.provide("decision_set", decision_set)
    if _plan_blocks_publication(context, decision_set):
        raise UnresolvedDecisionError(decision_set.get("unresolved") or [])
    return decision_set


def _plan_blocks_publication(context: ModuleExecutionContext, decision_set: Mapping[str, Any]) -> bool:
    unresolved = decision_set.get("unresolved") or []
    if not unresolved:
        return False
    plan = context.services.get("execution_plan")
    modules = getattr(plan, "modules", ()) or ()
    return any(module.failure_policy == "fail" for module in modules)


def _native_transaction(context: ModuleExecutionContext) -> Any:
    from .transaction import apply_stage_batch, make_snapshot, stage_candidate_revision

    source_ast = context.require("source_ast")
    decision_set = context.require("decision_set")
    snapshot = make_snapshot(source_ast)
    restored = (context.request.options or {}).get("restored_transaction_snapshot")
    if isinstance(restored, Mapping):
        snapshot.transactions = {
            str(transaction_id): str(digest)
            for transaction_id, digest in (restored.get("transactions") or {}).items()
        }
        snapshot.overlays = json.loads(json.dumps(restored.get("overlays") or {}, ensure_ascii=False))
        snapshot.presentation_overrides = json.loads(
            json.dumps(restored.get("presentation_overrides") or {}, ensure_ascii=False)
        )
    with context.stage("transaction"):
        candidate_baseline = context.services.get("candidate_baseline")
        if candidate_baseline is not None:
            committed = stage_candidate_revision(
                candidate_baseline,
                decision_set["decisions"],
                expected_revision=source_ast["revision"],
            ).candidate
        else:
            committed = apply_stage_batch(
                snapshot,
                decision_set["decisions"],
                expected_revision=source_ast["revision"],
            )
    snapshot = committed.snapshot
    containers = snapshot.ast["document"]["containers"]
    children = snapshot.ast["document"]["children"]
    context.provide("transaction_commit", committed)
    context.provide("source_ast_revision", snapshot.ast["revision"])
    context.provide("source_ast_hierarchy", {
        "list_count": sum(node.get("kind") == "list" for node in containers.values()),
        "item_count": sum(node.get("kind") == "list_item" for node in containers.values()),
        "nested_list_count": sum(
            1
            for parent_id, node in containers.items()
            if node.get("kind") == "list_item"
            and any(containers.get(child, {}).get("kind") == "list" for child in children.get(parent_id, []))
        ),
    })
    return committed


def _list_container_levels(context: ModuleExecutionContext) -> list[dict[str, Any]]:
    """Return the ``semantic_level`` the list semantics layer published per list.

    The list semantics module owns the logical level of every list relation, so
    the normalizer consumes that stored level instead of re-deriving it from the
    materialized container tree.  The artifact is absent from the review
    re-resolution context, which falls back to the structural walk.
    """

    result = context.artifacts.get("list_semantics_result")
    if not isinstance(result, Mapping):
        return []
    levels: dict[str, int] = {}
    for item in result.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        list_id = item.get("list_id")
        level = item.get("semantic_level")
        if not isinstance(list_id, str) or not list_id:
            continue
        if not isinstance(level, int) or isinstance(level, bool) or level < 0:
            continue
        levels[list_id] = min(levels.get(list_id, level), level)
    return [{"list_id": list_id, "semantic_level": level} for list_id, level in levels.items()]


def _native_normalizer(context: ModuleExecutionContext) -> Mapping[str, Any]:
    from .normalization import normalize
    from .template import load_template_bundle

    committed = context.require("transaction_commit")
    decision_set = context.require("decision_set")
    facts = context.require("source_facts")
    request = context.request
    options = request.options or {}
    store = context.services.get("store")
    with context.stage("normalize"):
        restored_template = options.get("restored_template_bundle")
        if restored_template is not None:
            template = restored_template
        elif request.template_path:
            template = load_template_bundle(request.template_path)
        else:
            template = {
                "resolved_styles": {
                    "list_letter": "List Number",
                    "list_decimal": "List Number 2",
                    "list_dash": "List Bullet",
                    "list_bullet": "List Bullet 2",
                },
                "numbering_ids": {},
            }
        normalized = normalize(
            committed.snapshot.ast,
            template,
            strategy=request.list_policy,
            overlay=committed.snapshot.overlays,
            presentation_override=committed.snapshot.presentation_overrides,
            context={
                "transaction_id": committed.transaction_id,
                "decision_set_version": decision_set["decision_set_version"],
                "list_container_levels": _list_container_levels(context),
            },
        )
    restored_resources = options.get("restored_resource_bytes")
    resources: dict[str, Any] = {}
    if isinstance(restored_resources, Mapping):
        resources = {
            str(resource_id): bytes(payload)
            for resource_id, payload in restored_resources.items()
            if isinstance(payload, (bytes, bytearray))
        }
    else:
        for resource_id, resource in facts.get("resources", {}).items():
            source_ref = resource.get("source_ref") if isinstance(resource, Mapping) else None
            if not isinstance(source_ref, Mapping):
                continue
            try:
                payload = store.resolve(dict(source_ref))
                if isinstance(payload, bytes):
                    resources[resource_id] = payload
            except Exception:
                part = source_ref.get("part")
                if isinstance(part, str):
                    candidate_path = request.input_path.parent / part
                    if candidate_path.is_file():
                        resources[resource_id] = candidate_path
    context.provide("template_bundle", template)
    context.provide("normalized", normalized)
    context.provide("resources", resources)
    return normalized


def _native_renderer(context: ModuleExecutionContext) -> Mapping[str, Any]:
    from .rendering import render

    normalized = context.require("normalized")
    template = context.require("template_bundle")
    resources = context.require("resources")
    staging_path = context.services["staging_path"]
    options = context.request.options or {}
    render_options = {
        "table_row_height_cm": float(options.get("table_row_height_cm", 0.69)),
        "table_row_height_rule": str(options.get("table_row_height_rule", "at-least")),
    }
    with context.stage("render"):
        if hasattr(template, "path"):
            result = render(
                normalized,
                staging_path,
                template_bundle=template,
                resources=resources,
                **render_options,
            )
        else:
            result = render(normalized, staging_path, resources=resources, **render_options)
        metrics = context.services.get("metrics")
        if metrics is not None:
            metrics.record_render()
    context.provide("render_plan", result["render_plan"])
    context.provide("render_result", result)
    return result


def _native_auditor(context: ModuleExecutionContext) -> dict[str, Any]:
    from .auditing import audit_bundle, collect_audit_findings

    normalized = context.require("normalized")
    render_result = context.require("render_result")
    appendix_semantics = context.require("appendix_semantics")
    staging_path = context.services["staging_path"]
    with context.stage("audit"):
        appendix_findings = (
            audit_native_appendix(normalized["source_ast"], appendix_semantics)
            if appendix_semantics is not None
            else {"passed": True, "issues": []}
        )
        audits = audit_bundle(
            staging_path,
            normalized,
            render_result["render_map"],
            appendix_audit=appendix_findings,
        )
    model_findings = audits["model"]
    saved_findings = audits["saved_output"]
    appendix_findings = audits["appendix"]
    list_readback_audit = audits["list_readback"]
    # A list readback mismatch means the saved numPr disagrees with the semantic
    # list, so it is folded into the report-visible saved-output audit while
    # keeping its own ``list_readback_mismatch`` code.  Renderer clamp
    # diagnostics stay non-blocking; they are carried on ``render_result`` and
    # surfaced under ``native_module_evidence`` rather than failing a document
    # whose list nests deeper than the two-level WX model.
    downstream_issues = [dict(item) for item in (list_readback_audit.get("issues") or [])]
    render_diagnostics = [dict(item) for item in (render_result.get("diagnostics") or [])]
    if downstream_issues:
        saved_findings = {
            **saved_findings,
            "issues": [*(saved_findings.get("issues") or []), *downstream_issues],
            "passed": False,
        }
    digest_after = digest_source_facts(dict(context.require("source_facts")))
    # The readback issues are merged into the saved-output view above, so the
    # normalized findings are computed from the merged audit instead of the
    # pre-merge ``audit_bundle`` output.  Otherwise a readback issue would be
    # visible under its own category but missing from the merged saved-output
    # category that drives publication.
    merged_findings = collect_audit_findings({**audits, "saved_output": saved_findings})
    context.provide("model_audit", model_findings)
    context.provide("saved_output_audit", saved_findings)
    context.provide("appendix_audit", appendix_findings)
    context.provide("audit_findings", merged_findings)
    context.provide("source_facts_integrity", {
        "digest_after": digest_after,
        "immutable": digest_after == context.require("source_facts_digest"),
    })
    # The list readback and renderer clamp results are surfaced through the
    # saved-output audit and ``render_result`` above; keep the raw records in the
    # module evidence so the separate audit seams stay inspectable without
    # introducing a new capability or module-evidence key.
    context.evidence["native-auditor"] = {
        "downstream": {
            "list_readback": _json_copy(list_readback_audit),
            "render_diagnostics": _json_copy(render_diagnostics),
        }
    }
    return {
        "model": model_findings,
        "saved": saved_findings,
        "appendix": appendix_findings,
        "list_readback": list_readback_audit,
    }


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def native_review_candidate_set_module() -> RegisteredModule:
    """Adapt validated review candidates into the regular native relation seam."""

    def execute(context: ModuleExecutionContext) -> list[Mapping[str, Any]]:
        candidates = _json_copy(context.require("review_candidates"))
        context.provide("candidate_set", candidates)
        context.provide("ledger_view", None)
        context.provide("ledger_replay", None)
        context.evidence["native-review-candidate-set"] = {
            "candidate_count": len(candidates),
            "source": "document_review",
        }
        return candidates

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            "native-review-candidate-set",
            "2.0",
            ("relation",),
            ("review_candidates",),
            ("candidate_set", "ledger_view", "ledger_replay"),
            contract_version="2.0",
            failure_policy="fail",
        ),
        execute=execute,
    )


def _baseline_acceptance(audits_passed: bool, reason: str) -> Any:
    from .review import AcceptanceDecision

    return AcceptanceDecision(
        "accepted" if audits_passed else "rejected",
        reason,
        audits_passed,
        False,
        False,
    )


def _native_reviewer(context: ModuleExecutionContext) -> dict[str, Any]:
    """Run one post-audit candidate revision and retain a rollback baseline."""

    from llm_enhancer import normalize_mode, should_enhance

    from .enhancement import EnhancementExecutor, build_native_request, serialize_native_request
    from .review import (
        AcceptanceDecision,
        accept_candidate,
        build_review_packet,
        validate_review_decisions,
    )
    from .response_store import request_digest

    request = context.request
    options = request.options or {}
    baseline_commit = context.require("transaction_commit")
    baseline_normalized = context.require("normalized")
    baseline_render = context.require("render_result")
    baseline_audits = {
        "model": context.require("model_audit"),
        "saved_output": context.require("saved_output_audit"),
        "appendix": context.require("appendix_audit"),
        "findings": list(context.require("audit_findings")),
    }
    baseline_audits["passed"] = all(
        baseline_audits[name].get("passed") is True
        for name in ("model", "saved_output", "appendix")
    )
    baseline_path = Path(context.services["staging_path"])
    candidate_path = baseline_path.with_name(
        f".{request.output_path.name}.native-v2.review-candidate.staging"
    )
    if candidate_path.is_file():
        candidate_path.unlink()
    baseline_bundle = {
        "source_ast": baseline_commit.snapshot.ast,
        "normalized_ast": baseline_normalized,
        "findings": baseline_audits["findings"],
        "artifacts": {"docx": str(baseline_path)},
        "round": 0,
    }
    packet = build_review_packet(baseline_bundle, baseline_audits["findings"])
    context.provide("review_packet", packet)
    mode = normalize_mode(str(options.get("llm_enhance") or "off"))
    enabled = should_enhance({"review_packet": packet}, "C", mode)
    reprocess_plan_ids: list[str] = []
    reprocess_evidence: dict[str, dict[str, Any]] = {}

    def finish(
        *,
        status: str,
        acceptance: Any,
        selected_commit: Any = baseline_commit,
        selected_normalized: Mapping[str, Any] = baseline_normalized,
        selected_render: Mapping[str, Any] = baseline_render,
        selected_audits: Mapping[str, Any] = baseline_audits,
        selected_artifact: Path | None = None,
        enhancement: Mapping[str, Any] | None = None,
        decision_set: Mapping[str, Any] | None = None,
        audit_exception: Mapping[str, Any] | None = None,
        candidate_artifact: Path | None = None,
    ) -> dict[str, Any]:
        result = {
            "status": status,
            "enabled": enabled,
            "triggered": status in {"accepted", "rolled_back"},
            "review_packet": packet,
            "acceptance": acceptance.to_dict(),
            "baseline_artifact": str(baseline_path),
            "candidate_artifact": str(candidate_artifact) if candidate_artifact is not None else None,
            "selected_artifact": str(selected_artifact) if selected_artifact is not None else None,
            "transaction_commit": selected_commit,
            "normalized": selected_normalized,
            "render_result": selected_render,
            "audits": selected_audits,
            "enhancement": dict(enhancement or {}),
            "decision_set": dict(decision_set or {}),
            "audit_exception": dict(audit_exception) if audit_exception is not None else None,
            "reprocess_plan": list(reprocess_plan_ids),
            "reprocess_module_evidence": _json_copy(reprocess_evidence),
        }
        context.provide("review_result", result)
        context.provide("acceptance", result["acceptance"])
        context.evidence["native-reviewer"] = {
            "status": status,
            "enabled": enabled,
            "triggered": result["triggered"],
            "baseline_revision": baseline_commit.snapshot.ast["revision"],
            "selected_revision": selected_commit.snapshot.ast["revision"],
            "reprocess_plan": list(reprocess_plan_ids),
        }
        return result

    if not enabled:
        acceptance = _baseline_acceptance(
            bool(baseline_audits["passed"]),
            "baseline_audits_passed" if baseline_audits["passed"] else "baseline_audit_failed",
        )
        return finish(
            status="not_requested",
            acceptance=acceptance,
            selected_artifact=baseline_path if acceptance.accepted else None,
        )
    if not packet["targets"]:
        acceptance = _baseline_acceptance(
            bool(baseline_audits["passed"]),
            "no_repairable_findings" if baseline_audits["passed"] else "baseline_audit_failed",
        )
        return finish(
            status="no_repairable_findings",
            acceptance=acceptance,
            selected_artifact=baseline_path if acceptance.accepted else None,
        )

    consumed = int((context.require("enhancement_result") or {}).get("consumed") or 0)
    configured_budget = options.get("enhancement_budget", consumed + 1)
    total_budget = (
        configured_budget
        if isinstance(configured_budget, int) and not isinstance(configured_budget, bool)
        else -1
    )
    remaining = max(0, total_budget - consumed)
    enhancement_request = build_native_request(
        "document_review",
        baseline_commit.snapshot.ast,
        budget=min(1, remaining),
        timeout_seconds=float(options.get("enhancement_timeout_seconds", 30.0)),
        request_id=f"native-{baseline_commit.snapshot.ast['revision']}-document_review",
    )
    request_sink = options.get("enhancement_request_sink")
    if isinstance(request_sink, list):
        serialized = serialize_native_request(enhancement_request)
        serialized["review_packet"] = _json_copy(packet)
        serialized["request_sha256"] = request_digest(serialized)
        request_sink.append(serialized)
    if options.get("protocol_mode") == "generate_requests":
        raise NativeFileProtocolPause()
    if (
        options.get("protocol_mode") == "resume"
        and enhancement_request.request_id not in set(options.get("available_response_ids") or ())
    ):
        raise NativeFileProtocolPause()

    enhancement_result = EnhancementExecutor().execute(
        enhancement_request,
        context={"review_packet": packet},
        call=options.get("enhancement_call"),
    )
    enhancement_record = enhancement_result.to_dict()
    if enhancement_result.status != "completed" or not enhancement_result.candidates:
        diagnostic = {
            "type": "EnhancementError",
            "message": f"document review ended with {enhancement_result.status}",
        }
        acceptance = AcceptanceDecision(
            "rejected",
            "review_response_failed",
            False,
            False,
            False,
            {"audit_exception": diagnostic},
        )
        return finish(
            status="rolled_back",
            acceptance=acceptance,
            enhancement=enhancement_record,
            audit_exception=diagnostic,
            candidate_artifact=candidate_path,
        )

    registry = context.services.get("module_registry")
    if registry is None:
        raise RuntimeError("native review requires the registered module registry")
    registry.register(native_review_candidate_set_module())
    resolution_plan = registry.build(
        {
            "pipeline": "modular",
            "list_policy": request.list_policy,
            "source_type": "docx" if request.input_path.suffix.lower() == ".docx" else "markdown",
            "available_audits": ("model_audit", "saved_output_audit"),
        },
        module_ids=(
            "native-review-candidate-set",
            "native-resolver",
        ),
        initial_capabilities=(
            "source_ast",
            "review_candidates",
        ),
    )
    reprocess_plan_ids.extend(module.module_id for module in resolution_plan.modules)
    review_context = ModuleExecutionContext(
        request=request,
        artifacts={
            "source_ast": baseline_commit.snapshot.ast,
            "source_facts": context.require("source_facts"),
            "source_facts_digest": context.require("source_facts_digest"),
            "appendix_semantics": context.require("appendix_semantics"),
            "review_candidates": enhancement_result.candidates,
        },
        services={
            **context.services.copy_values(),
            "staging_path": candidate_path,
            "execution_plan": resolution_plan,
            "candidate_baseline": baseline_commit.snapshot,
        },
    )
    try:
        registry.execute(resolution_plan, review_context)
    except UnresolvedDecisionError:
        decision_set = review_context.require("decision_set")
    else:
        decision_set = review_context.require("decision_set")
    reprocess_evidence.update(review_context.evidence)
    if decision_set.get("unresolved") or not decision_set.get("decisions"):
        acceptance = AcceptanceDecision(
            "rejected",
            "candidate_resolution_failed",
            False,
            False,
            False,
            {"unresolved": _json_copy(decision_set.get("unresolved") or [])},
        )
        return finish(
            status="rolled_back",
            acceptance=acceptance,
            enhancement=enhancement_record,
            decision_set=decision_set,
            candidate_artifact=candidate_path,
        )

    scope_violation = validate_review_decisions(packet, decision_set["decisions"])
    if scope_violation is not None:
        acceptance = AcceptanceDecision(
            "rejected",
            "review_scope_violation",
            False,
            False,
            False,
            {"review_scope_violation": scope_violation},
        )
        return finish(
            status="rolled_back",
            acceptance=acceptance,
            enhancement=enhancement_record,
            decision_set=decision_set,
            candidate_artifact=candidate_path,
        )

    transaction_plan = registry.build(
        {
            "pipeline": "modular",
            "list_policy": request.list_policy,
            "source_type": "docx" if request.input_path.suffix.lower() == ".docx" else "markdown",
            "available_audits": ("model_audit", "saved_output_audit"),
        },
        module_ids=(
            "native-transaction",
            "native-normalizer",
            "native-renderer",
            "native-auditor",
        ),
        initial_capabilities=(
            "source_ast",
            "source_facts",
            "source_facts_digest",
            "appendix_semantics",
            "decision_set",
        ),
    )
    reprocess_plan_ids.extend(module.module_id for module in transaction_plan.modules)
    review_context.services["execution_plan"] = transaction_plan
    try:
        registry.execute(transaction_plan, review_context)
    except Exception as exc:
        reprocess_evidence.update(review_context.evidence)
        audit_exception = {"type": type(exc).__name__, "message": str(exc)}
        transaction_failed = (
            reprocess_evidence.get("native-transaction", {}).get("status") == "failed"
        )
        acceptance = AcceptanceDecision(
            "rejected",
            "candidate_transaction_failed" if transaction_failed else "audit_exception",
            False,
            False,
            False,
            {"audit_exception": audit_exception},
        )
        if candidate_path.is_file():
            candidate_path.unlink()
        return finish(
            status="rolled_back",
            acceptance=acceptance,
            enhancement=enhancement_record,
            decision_set=decision_set,
            audit_exception=audit_exception,
            candidate_artifact=candidate_path,
        )
    reprocess_evidence.update(review_context.evidence)
    candidate_commit = review_context.require("transaction_commit")
    candidate_bundle: dict[str, Any] = {
        "source_ast": candidate_commit.snapshot.ast,
        "normalized_ast": baseline_normalized,
        "findings": baseline_audits["findings"],
        "artifacts": {"docx": str(candidate_path)},
        "round": 1,
    }
    try:
        candidate_normalized = review_context.require("normalized")
        candidate_render = review_context.require("render_result")
        candidate_audits = {
            "model": review_context.require("model_audit"),
            "saved_output": review_context.require("saved_output_audit"),
            "appendix": review_context.require("appendix_audit"),
            "findings": list(review_context.require("audit_findings")),
        }
        candidate_audits["passed"] = all(
            candidate_audits[name].get("passed") is True
            for name in ("model", "saved_output", "appendix")
        )
        candidate_bundle.update(
            {
                "normalized_ast": candidate_normalized,
                "findings": candidate_audits["findings"],
            }
        )
        acceptance, _accepted_bundle = accept_candidate(
            baseline_bundle,
            candidate_bundle,
            {
                "baseline": baseline_audits["findings"],
                "candidate": candidate_audits["findings"],
            },
            decisions=decision_set["decisions"],
            review_packet=packet,
        )
    except Exception as exc:
        audit_exception = {"type": type(exc).__name__, "message": str(exc)}
        candidate_bundle["audit_exception"] = audit_exception
        acceptance, _accepted_bundle = accept_candidate(
            baseline_bundle,
            candidate_bundle,
            {
                "baseline": baseline_audits["findings"],
                "candidate": candidate_bundle.get("findings") or [],
            },
            decisions=decision_set["decisions"],
            review_packet=packet,
        )
        if candidate_path.is_file():
            candidate_path.unlink()
        return finish(
            status="rolled_back",
            acceptance=acceptance,
            enhancement=enhancement_record,
            decision_set=decision_set,
            audit_exception=audit_exception,
            candidate_artifact=candidate_path,
        )

    if not acceptance.accepted:
        if candidate_path.is_file():
            candidate_path.unlink()
        return finish(
            status="rolled_back",
            acceptance=acceptance,
            enhancement=enhancement_record,
            decision_set=decision_set,
            candidate_artifact=candidate_path,
        )
    return finish(
        status="accepted",
        acceptance=acceptance,
        selected_commit=candidate_commit,
        selected_normalized=candidate_normalized,
        selected_render=candidate_render,
        selected_audits=candidate_audits,
        selected_artifact=candidate_path,
        enhancement=enhancement_record,
        decision_set=decision_set,
        candidate_artifact=candidate_path,
    )


def _native_publisher(context: ModuleExecutionContext) -> bool:
    from .publication import PublicationError, validate_acceptance

    review_result = context.require("review_result")
    try:
        validate_acceptance(context.require("acceptance"))
    except PublicationError:
        context.provide("published_output", None)
        return False
    audits = review_result["audits"]
    # A list-readback mismatch is folded into ``saved_output`` by the auditor
    # (see ``_native_auditor``), and ``saved_output.passed`` is the gate here, so
    # a readback failure blocks publication through the saved-output audit
    # rather than a separate ``list_readback`` key that never exists on this
    # dict.
    audits_passed = (
        audits["model"].get("passed") is True
        and audits["saved_output"].get("passed") is True
        and audits["appendix"].get("passed") is True
    )
    fatal_diagnostics = [
        item
        for item in context.require("list_semantics_result").get("diagnostics", [])
        if item.get("severity") in {"error", "warning"}
    ]
    if audits_passed and not fatal_diagnostics:
        selected_artifact = Path(review_result["selected_artifact"])
        if not selected_artifact.is_file():
            context.provide("published_output", None)
            return False
        context.provide("published_output", str(selected_artifact))
        return True
    context.provide("published_output", None)
    return False


def native_stage_executors() -> dict[str, Callable[[ModuleExecutionContext], Any]]:
    """Return the production implementation of the native stage chain."""

    return {
        "native-source-parser": _native_source_ast,
        "native-candidate-set": _native_candidate_set,
        "native-resolver": _native_resolver,
        "native-transaction": _native_transaction,
        "native-normalizer": _native_normalizer,
        "native-renderer": _native_renderer,
        "native-auditor": _native_auditor,
        "native-reviewer": _native_reviewer,
        "native-publisher": _native_publisher,
    }


__all__ = [
    "LedgerReplayBlocked",
    "NativeEnhancementBlocked",
    "NativeFileProtocolPause",
    "ModuleExecutionContext",
    "UnresolvedDecisionError",
    "native_stage_executors",
]
