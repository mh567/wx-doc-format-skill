"""Conversion lifecycle primitives for the native-v2 pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import json
from pathlib import Path
from typing import Any, Mapping

_NATIVE_PROFILE = "wx-default-v2"


@dataclass(frozen=True)
class ConversionRequest:
    input_path: Path
    output_path: Path
    template_path: Path | None = None
    pipeline: str = "modular"
    list_policy: str = "wx-default-v2"
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_path", Path(self.input_path))
        object.__setattr__(self, "output_path", Path(self.output_path))
        if self.template_path is not None:
            object.__setattr__(self, "template_path", Path(self.template_path))
        if self.pipeline != "modular":
            raise ValueError(f"unsupported pipeline {self.pipeline!r}")
        if self.list_policy != "wx-default-v2":
            raise ValueError(f"unsupported list policy {self.list_policy!r}")


@dataclass
class RunResult:
    status: str
    exit_code: int
    report: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "report": self.report,
            "artifacts": self.artifacts,
            "diagnostics": self.diagnostics,
            "metrics": self.metrics,
        }

    def as_report(self) -> dict[str, Any]:
        payload = dict(self.report)
        payload.update({
            "status": self.status,
            "exit_code": self.exit_code,
            "artifacts": dict(self.artifacts),
            "diagnostics": list(self.diagnostics),
            "metrics": dict(self.metrics),
        })
        return payload


class ConversionKernel:
    def convert(self, request: ConversionRequest) -> RunResult:
        return self._convert_modular_native_v2(request)

    @staticmethod
    def _facts_to_ast(facts: Mapping[str, Any]) -> dict[str, Any]:
        """Compatibility seam for the generic SourceFacts to AST construction.

        The construction itself lives in ``wxdf.semantics.source_ast`` so that
        the native chain owns it as a registered Module.
        """

        from .semantics.source_ast import build_source_ast

        return build_source_ast(facts)

    @staticmethod
    def _materialize_list_semantics(
        source_ast: Mapping[str, Any],
        facts: Mapping[str, Any],
        list_result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
        """Compatibility seam for native list AST materialization.

        The materialization itself lives in ``wxdf.semantics.source_ast`` so
        that the list semantics Module owns it.
        """

        from .semantics.source_ast import materialize_list_semantics

        return materialize_list_semantics(source_ast, facts, list_result)

    @staticmethod
    def _native_preflight(source_ast: Mapping[str, Any] | None, source_type: str, list_policy: str, *, registry: Any = None) -> Any:
        """Validate the native module contract before module execution.

        ``source_ast`` is optional.  Without it the request omits the node kind
        contract because the chain builds that revision inside a registered
        Module.
        """

        from .registry import build_native_v2_registry

        node_kinds: list[str] = []
        if source_ast is not None:
            node_kinds = sorted({
                str(node.get("block_type") or node.get("kind"))
                for collection in (source_ast["document"]["blocks"], source_ast["document"]["containers"])
                for node in collection.values()
                if node.get("block_type") or node.get("kind")
            })
        registry = registry or build_native_v2_registry()
        return registry.build(
            {
                "pipeline": "modular",
                "list_policy": list_policy,
                "source_type": source_type,
                "node_kinds": sorted(node_kinds),
                "available_audits": ("model_audit", "saved_output_audit"),
            },
        )

    @staticmethod
    def _native_should_block_publish(execution_plan: Any, decision_set: Mapping[str, Any]) -> bool:
        """Apply the registered failure policy before a transaction or publish step."""

        unresolved = decision_set.get("unresolved") or []
        return bool(unresolved) and any(module.failure_policy == "fail" for module in execution_plan.modules)

    @staticmethod
    def _native_execution_plan_report(execution_plan: Any) -> list[dict[str, Any]]:
        """Report every Module of the executed native plan in execution order."""

        return [
            {
                "module_id": module.module_id,
                "version": module.version,
                "contract_version": module.contract_version,
                "failure_policy": module.failure_policy,
                "required_audits": list(module.required_audits),
            }
            for module in execution_plan.modules
        ]

    @staticmethod
    def _native_capability_status(
        module_id: str,
        candidates: list[Mapping[str, Any]],
        decision_set: Mapping[str, Any],
        committed: Any,
        audits_passed: bool,
    ) -> str:
        """Derive delivery state from candidate IDs, resolution and commit evidence."""

        module_candidates = [candidate for candidate in candidates if candidate.get("module_id") == module_id]
        if not module_candidates:
            return "delivered"
        candidate_ids = {str(candidate.get("candidate_id")) for candidate in module_candidates}
        decision_ids = {
            str(candidate_id)
            for decision in decision_set.get("decisions", [])
            for candidate_id in decision.get("candidate_ids", [])
            if candidate_id is not None
        }
        unresolved_ids = {
            str(candidate_id)
            for unresolved in decision_set.get("unresolved", [])
            for candidate_id in unresolved.get("candidate_ids", [])
        }
        if unresolved_ids & candidate_ids or not candidate_ids.issubset(decision_ids):
            return "partial"
        if committed.status in {"committed", "already_committed"} and audits_passed:
            return "consumed"
        return "consumed_mismatch"

    @staticmethod
    def _native_source_type(input_path: Path) -> str:
        """Return the source type the native chain preflights against."""

        return "docx" if input_path.suffix.lower() == ".docx" else "markdown"

    @staticmethod
    def _native_checkpoint_modules(execution_plan: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": module.module_id,
                "version": module.version,
                "contract_version": module.contract_version,
            }
            for module in execution_plan.modules
        ]

    @staticmethod
    def _native_ledger_replay_report(context: Any) -> dict[str, Any] | None:
        """Complete the ledger replay record with the resolved decision count."""

        if context is None:
            return None
        replay = context.artifacts.get("ledger_replay")
        if replay is None:
            return None
        record = dict(replay)
        decision_set = context.artifacts.get("decision_set")
        if decision_set is not None:
            record["decision_count"] = len(decision_set.get("decisions") or [])
        return record

    @staticmethod
    def _lock_native_checkpoint_identity(checkpoint_path: Path) -> dict[str, Any]:
        """Validate and migrate the native identity before responses are read."""

        from .run_store import CheckpointError, load_checkpoint, save_checkpoint

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
        if configuration.get("list_policy") != "wx-default-v2":
            raise CheckpointError(
                "native checkpoint configuration list policy is not wx-default-v2",
                code="checkpoint_list_policy_mismatch",
            )
        if checkpoint.get("list_policy") != "wx-default-v2":
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

    def _convert_modular_native_v2(self, request: ConversionRequest) -> RunResult:
        """Execute the registered native Module chain and collect its RunResult."""

        options = dict(request.options or {})
        request = ConversionRequest(
            input_path=request.input_path,
            output_path=request.output_path,
            template_path=request.template_path,
            pipeline=request.pipeline,
            list_policy=request.list_policy,
            options=options,
        )
        report: dict[str, Any] = {
            "pipeline": "modular",
            "list_policy": request.list_policy,
            "profile": _NATIVE_PROFILE,
            "status": "running",
            "contract_versions": {"source_facts": "1.0", "ast": "2.0", "decision_set": "2.0", "normalized": "1.0"},
            "module_chain": [
                "SourceParser", "SourceFacts", "SemanticModuleCandidateSet", "SemanticResolverDecisionSet",
                "TransactionManagerSourceAST", "NormalizationPolicy", "RenderPlan", "Renderer", "AuditModule", "ReviewEvaluator", "Publisher",
            ],
        }
        from .publication import PublicationError, recover_pending_publications

        recovery_targets = [request.output_path]
        for key in ("source_ast_path", "normalized_ast_path", "report_path", "checkpoint_path"):
            value = options.get(key)
            if value is not None:
                recovery_targets.append(Path(value))
        try:
            recover_pending_publications(recovery_targets)
        except PublicationError as exc:
            return RunResult(
                status="failed",
                exit_code=1,
                report={**report, "status": "failed"},
                diagnostics=[{
                    "code": "PUBLICATION_RECOVERY_FAILED",
                    "severity": "error",
                    "message": str(exc),
                }],
            )
        if not request.input_path.is_file():
            return RunResult(
                status="failed",
                exit_code=1,
                report={**report, "status": "failed"},
                diagnostics=[{"code": "INPUT_NOT_FOUND", "message": str(request.input_path)}],
            )

        from .builtin_modules import (
            LedgerReplayBlocked,
            ModuleExecutionContext,
            NativeEnhancementBlocked,
            NativeFileProtocolPause,
            UnresolvedDecisionError,
        )
        from .decision_ledger import LedgerError
        from .metrics import MetricsCollector
        from .registry import build_native_v2_registry
        from .run_store import (
            CheckpointError,
            append_native_waiting_requests,
            complete_native_checkpoint,
            prepare_native_resume,
            write_native_waiting_checkpoint,
        )
        from .source_store import SourceStore
        from .transaction import TransactionConflict

        metrics = MetricsCollector()
        enhancement_mode = str(options.get("llm_enhance") or "off")
        protocol_mode = options.get("protocol_mode")
        if (
            enhancement_mode != "off"
            and options.get("enhancement_call") is None
            and protocol_mode not in {"generate_requests", "resume"}
        ):
            diagnostic = {
                "code": "NATIVE_ENHANCEMENT_ADAPTER_MISSING",
                "severity": "error",
                "message": "native-v2 enhancement requires an explicitly injected adapter",
            }
            report.update({
                "status": "failed",
                "enhancement": {
                    "status": "missing_adapter",
                    "mode": enhancement_mode,
                    "candidate_count": 0,
                    "consumed": 0,
                    "results": [],
                    "diagnostics": [diagnostic],
                },
            })
            return RunResult(
                status="failed",
                exit_code=1,
                report=report,
                diagnostics=[diagnostic],
            )
        staging_path = request.output_path.with_name(f".{request.output_path.name}.native-v2.staging")
        review_candidate_path = request.output_path.with_name(
            f".{request.output_path.name}.native-v2.review-candidate.staging"
        )
        store = SourceStore()
        context: Any = None
        checkpoint: dict[str, Any] | None = None
        try:
            registry = build_native_v2_registry()
            execution_plan = self._native_preflight(
                None,
                self._native_source_type(request.input_path),
                request.list_policy,
                registry=registry,
            )
            if protocol_mode == "resume":
                self._lock_native_checkpoint_identity(Path(options["checkpoint_path"]))
                contracts = {
                    "source_facts": "1.0",
                    "ast": "2.0",
                    "patch": "2.0",
                    "checkpoint": "2.0",
                }
                checkpoint, ready, response_map, resume_options = prepare_native_resume(
                    options["checkpoint_path"],
                    source_path=request.input_path,
                    template_path=request.template_path,
                    contracts=contracts,
                    modules=self._native_checkpoint_modules(execution_plan),
                    configuration=options.get("checkpoint_configuration") or {},
                    source_store=store,
                )
                if not ready:
                    status = str(checkpoint["status"])
                    return RunResult(
                        status=status,
                        exit_code=1 if status in {"failed", "cancelled"} else 0,
                        report={
                            "pipeline": "modular",
                            "profile": _NATIVE_PROFILE,
                            "list_policy": request.list_policy,
                            "status": status,
                            "checkpoint": str(options["checkpoint_path"]),
                            **(
                                {"review_outcome": copy.deepcopy(checkpoint["review_outcome"])}
                                if isinstance(checkpoint.get("review_outcome"), Mapping)
                                else {}
                            ),
                        },
                    )
                options.update(resume_options)
                options["enhancement_request_sink"] = []
                options["available_response_ids"] = list(response_map)
                stored_requests = {
                    str(item["request_id"]): item
                    for item in checkpoint["requests"]
                }
                def replay_response(payload: Mapping[str, Any]) -> Mapping[str, Any]:
                    request_id = str(payload.get("request_id") or "")
                    if request_id not in response_map:
                        raise KeyError(f"no recorded response for {request_id}")
                    stored_request = stored_requests.get(request_id)
                    request_identity = {
                        "capability": payload.get("capability"),
                        "model_revision": payload.get("model_revision"),
                        "view_sha256": payload.get("view_sha256"),
                        "items": json.loads(json.dumps(payload.get("items"), ensure_ascii=False)),
                    }
                    if stored_request is None or request_identity != {
                        key: stored_request.get(key)
                        for key in request_identity
                    }:
                        raise KeyError(f"runtime request identity changed for {request_id}")
                    stored_packet = stored_request.get("review_packet")
                    live_packet = json.loads(json.dumps(
                        payload.get("context", {}).get("review_packet"),
                        ensure_ascii=False,
                    ))
                    if stored_packet is not None and live_packet != stored_packet:
                        raise KeyError(f"runtime ReviewPacket identity changed for {request_id}")
                    return copy.deepcopy(response_map[request_id])

                options["enhancement_call"] = replay_response
            context = ModuleExecutionContext(
                request=request,
                services={
                    "metrics": metrics,
                    "store": store,
                    "staging_path": staging_path,
                    "execution_plan": execution_plan,
                },
            )
            registry.execute(execution_plan, context)
            result = self._native_v2_run_result(request, options, report, execution_plan, context, metrics)
            review_result = context.require("review_result") if context is not None else {}
            terminal_review_rollback = (
                isinstance(review_result, Mapping)
                and review_result.get("status") == "rolled_back"
            )
            if checkpoint is not None and (result.exit_code == 0 or terminal_review_rollback):
                committed = review_result["transaction_commit"]
                enhancement = context.require("enhancement_result")
                relation_commit = context.require("transaction_commit")
                relation_decision_set = context.require("decision_set")
                applied_request_ids = {
                    str(record.get("request_summary", {}).get("request_id"))
                    for record in enhancement.get("results", [])
                    if record.get("status") == "completed"
                    and record.get("request_summary", {}).get("request_id")
                }
                request_transaction_ids = {
                    request_id: relation_commit.transaction_id
                    for request_id in applied_request_ids
                }
                checkpoint_decision_set = relation_decision_set
                if review_result["acceptance"].get("accepted") is True:
                    review_request_id = (
                        review_result.get("enhancement", {})
                        .get("request_summary", {})
                        .get("request_id")
                    )
                    if review_request_id:
                        review_request_id = str(review_request_id)
                        applied_request_ids.add(review_request_id)
                        request_transaction_ids[review_request_id] = committed.transaction_id
                    review_decision_set = review_result["decision_set"]
                    checkpoint_decision_set = {
                        **relation_decision_set,
                        "transaction_id": committed.transaction_id,
                        "decisions": [
                            *relation_decision_set.get("decisions", []),
                            *review_decision_set.get("decisions", []),
                        ],
                        "unresolved": [
                            *relation_decision_set.get("unresolved", []),
                            *review_decision_set.get("unresolved", []),
                        ],
                    }
                completed_checkpoint = complete_native_checkpoint(
                    options["checkpoint_path"],
                    checkpoint,
                    status=result.status,
                    source_ast=committed.snapshot.ast,
                    normalized=review_result["normalized"],
                    decision_set=checkpoint_decision_set,
                    overlays=committed.snapshot.overlays,
                    presentation_overrides=committed.snapshot.presentation_overrides,
                    transactions=committed.snapshot.transactions,
                    transaction_id=committed.transaction_id,
                    applied_request_ids=applied_request_ids,
                    request_transaction_ids=request_transaction_ids,
                    review_outcome=(
                        {
                            "status": "rolled_back",
                            "acceptance": copy.deepcopy(review_result.get("acceptance") or {}),
                            "audit_exception": copy.deepcopy(review_result.get("audit_exception")),
                        }
                        if terminal_review_rollback
                        else None
                    ),
                    persist=False,
                )
                publication_payloads = (
                    dict(context.require("pending_publication_payloads"))
                    if result.exit_code == 0
                    else {}
                )
                report_path = options.get("report_path")
                if result.exit_code != 0 and report_path is not None:
                    report_path = Path(report_path)
                    result.artifacts["report"] = str(report_path)
                    result.report["artifacts"] = dict(result.artifacts)
                    report_payload = result.as_report()
                    report_transform = options.get("report_transform")
                    if callable(report_transform):
                        report_payload = report_transform(report_payload)
                    publication_payloads[report_path] = json.dumps(
                        report_payload,
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8")
                publication_payloads[Path(options["checkpoint_path"])] = (
                    json.dumps(
                        completed_checkpoint,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ) + "\n"
                ).encode("utf-8")
                try:
                    from .publication import publish_files_atomically

                    publish_files_atomically(publication_payloads)
                except (OSError, PublicationError) as exc:
                    diagnostic = {
                        "code": "PUBLICATION_FAILED",
                        "severity": "error",
                        "message": str(exc),
                    }
                    return RunResult(
                        status="failed",
                        exit_code=1,
                        report={**result.report, "status": "failed"},
                        artifacts={},
                        diagnostics=[*result.diagnostics, diagnostic],
                        metrics=result.metrics,
                    )
            return result
        except NativeFileProtocolPause:
            pending_requests = list(options.get("enhancement_request_sink") or [])
            if checkpoint is None:
                checkpoint = write_native_waiting_checkpoint(
                    checkpoint_path=options["checkpoint_path"],
                    requests_path=options["requests_path"],
                    responses_path=options["responses_path"],
                    profile=_NATIVE_PROFILE,
                    list_policy=request.list_policy,
                    configuration=options.get("checkpoint_configuration") or {},
                    source_facts=context.require("source_facts"),
                    source_ast=context.require("source_ast"),
                    candidate_set=context.require("candidate_set"),
                    modules=self._native_checkpoint_modules(execution_plan),
                    requests=pending_requests,
                    template_path=request.template_path,
                )
            else:
                checkpoint = append_native_waiting_requests(
                    options["checkpoint_path"],
                    checkpoint,
                    pending_requests,
                )
            return RunResult(
                status="waiting_external",
                exit_code=0,
                report={
                    "pipeline": "modular",
                    "profile": checkpoint["profile"],
                    "list_policy": checkpoint["list_policy"],
                    "status": "waiting_external",
                    "checkpoint": str(options["checkpoint_path"]),
                    "request_count": len(checkpoint["requests"]),
                },
                artifacts={"checkpoint": str(options["checkpoint_path"])},
            )
        except CheckpointError as exc:
            return RunResult(
                status="failed",
                exit_code=1,
                report={**report, "status": "failed"},
                diagnostics=[{"code": exc.code, "severity": "error", "message": str(exc)}],
                metrics=metrics.snapshot(),
            )
        except NativeEnhancementBlocked as exc:
            diagnostics = exc.diagnostics or [{
                "code": "NATIVE_ENHANCEMENT_BLOCKED",
                "severity": "error",
                "message": str(exc),
            }]
            report.update({
                "status": "failed",
                "enhancement": exc.result,
            })
            return RunResult(
                status="failed",
                exit_code=1,
                report=report,
                diagnostics=diagnostics,
                metrics=metrics.snapshot(),
            )
        except LedgerReplayBlocked as exc:
            replay = context.require("ledger_replay") if context is not None else {
                "enabled": True,
                "error_code": "replay_blocked",
                "diagnostics": exc.diagnostics,
            }
            report.update({"status": "failed", "decision_ledger_replay": replay})
            return RunResult(
                status="failed",
                exit_code=1,
                report=report,
                diagnostics=[
                    {"code": "replay_blocked", "severity": "error", "message": "cannot apply invalidated or conflicted revisions"},
                    *exc.diagnostics,
                ],
                metrics=metrics.snapshot(),
            )
        except LedgerError as exc:
            report.update({
                "status": "failed",
                "decision_ledger_replay": {"enabled": True, "error_code": exc.code, "message": str(exc)},
            })
            return RunResult(
                status="failed",
                exit_code=1,
                report=report,
                diagnostics=[{"code": exc.code, "severity": "error", "message": str(exc)}],
                metrics=metrics.snapshot(),
            )
        except UnresolvedDecisionError as exc:
            decision_set = context.require("decision_set") if context is not None else {"unresolved": exc.unresolved}
            report.update({
                "status": "failed",
                "source_ast_revision": context.require("source_ast")["revision"] if context is not None else 1,
                "decision_set": decision_set,
                "decision_ledger_replay": self._native_ledger_replay_report(context),
                "execution_plan": {
                    "profile": _NATIVE_PROFILE,
                    "modules": self._native_execution_plan_report(execution_plan),
                },
            })
            return RunResult(
                status="failed",
                exit_code=1,
                report=report,
                diagnostics=list(decision_set.get("unresolved") or []),
                metrics=metrics.snapshot(),
            )
        except TransactionConflict as exc:
            supplied_ledger = options.get("decision_ledger")
            if supplied_ledger is not None:
                revision_ids = [
                    str(revision.get("revision_id"))
                    for revision in supplied_ledger.get("revisions", [])
                    if isinstance(revision, Mapping) and revision.get("status") == "active"
                ]
                replay_diagnostic = {
                    "code": "ledger_transaction_conflict",
                    "severity": "error",
                    "message": str(exc),
                    "revision_ids": revision_ids,
                }
                report.update({
                    "status": "failed",
                    "decision_ledger_replay": {
                        **(self._native_ledger_replay_report(context) or {"enabled": True}),
                        "error_code": replay_diagnostic["code"],
                        "diagnostics": [replay_diagnostic],
                    },
                })
                return RunResult(status="failed", exit_code=1, report=report, diagnostics=[replay_diagnostic], metrics=metrics.snapshot())
            return RunResult(
                status="failed",
                exit_code=1,
                report={**report, "status": "failed"},
                diagnostics=[{"code": "MODULAR_NATIVE_TRANSACTION_CONFLICT", "message": str(exc), "type": type(exc).__name__}],
                metrics=metrics.snapshot(),
            )
        except Exception as exc:
            return RunResult(
                status="failed",
                exit_code=1,
                report={**report, "status": "failed"},
                diagnostics=[{"code": "MODULAR_NATIVE_CONVERSION_FAILED", "message": str(exc), "type": type(exc).__name__}],
                metrics=metrics.snapshot(),
            )
        finally:
            if staging_path.is_file():
                staging_path.unlink()
            if review_candidate_path.is_file():
                review_candidate_path.unlink()
            store.close()

    def _native_v2_run_result(
        self,
        request: ConversionRequest,
        options: dict[str, Any],
        report: dict[str, Any],
        execution_plan: Any,
        context: Any,
        metrics: Any,
    ) -> RunResult:
        """Translate the committed native artifacts into the RunResult contract."""

        candidates = list(context.require("candidate_set"))
        enhancement = context.require("enhancement_result")
        decision_set = context.require("decision_set")
        list_candidates = list(context.require("list_semantics_candidates"))
        appendix_candidates = list(context.require("appendix_semantics_candidates"))
        appendix_semantics = context.require("appendix_semantics")
        list_result = context.require("list_semantics_result")
        review_result = context.require("review_result")
        committed = review_result["transaction_commit"]
        snapshot = committed.snapshot
        normalized = review_result["normalized"]
        selected_audits = review_result["audits"]
        model_findings = selected_audits["model"]
        saved_findings = selected_audits["saved_output"]
        appendix_audit = selected_audits["appendix"]
        published = context.require("published_output")
        integrity = context.require("source_facts_integrity")
        source_facts_digest = context.require("source_facts_digest")
        source_facts_digest_after = str(integrity["digest_after"])
        source_facts_immutable = bool(integrity["immutable"])
        ledger_replay = self._native_ledger_replay_report(context)
        review_decision_set = review_result.get("decision_set") or {}
        transaction_decision_set = (
            review_decision_set
            if review_result.get("status") == "accepted"
            else decision_set
        )
        native_review = {
            key: copy.deepcopy(review_result.get(key))
            for key in (
                "status",
                "enabled",
                "triggered",
                "acceptance",
                "baseline_artifact",
                "candidate_artifact",
                "selected_artifact",
                "enhancement",
                "decision_set",
                "audit_exception",
            )
            if review_result.get(key) is not None
        }

        appendix_range_candidate_ids = {
            str(candidate.get("candidate_id"))
            for candidate in appendix_candidates
            if str(candidate.get("candidate_id", "")).startswith("appendix-range-")
        }
        appendix_heading_candidate_ids = {
            str(candidate.get("candidate_id"))
            for candidate in appendix_candidates
            if str(candidate.get("candidate_id", "")).startswith("appendix-heading-")
        }
        appendix_decision_ids = {
            str(candidate_id)
            for decision in decision_set.get("decisions", [])
            for candidate_id in decision.get("candidate_ids", [])
        }
        appendix_consumption = {
            "candidate_count": len(appendix_candidates),
            "range_candidate_count": len(appendix_range_candidate_ids),
            "range_consumed_count": len(appendix_range_candidate_ids & appendix_decision_ids),
            "heading_candidate_count": len(appendix_heading_candidate_ids),
            "heading_consumed_count": len(appendix_heading_candidate_ids & appendix_decision_ids),
            "unmatched_count": len(appendix_range_candidate_ids | appendix_heading_candidate_ids) - len((appendix_range_candidate_ids | appendix_heading_candidate_ids) & appendix_decision_ids),
            "ambiguous_count": len(decision_set.get("unresolved") or []),
        }

        diagnostics = list(list_result.get("diagnostics") or [])
        if not source_facts_immutable:
            diagnostics.append({
                "code": "SOURCE_FACTS_MUTATED",
                "severity": "error",
                "message": "SourceFacts changed during native pipeline execution",
            })
        diagnostics.extend(decision_set.get("unresolved") or [])
        diagnostics.extend(model_findings.get("issues") or [])
        diagnostics.extend(saved_findings.get("issues") or [])
        diagnostics.extend(appendix_audit.get("issues") or [])
        audits_passed = (
            model_findings.get("passed") is True
            and saved_findings.get("passed") is True
            and appendix_audit.get("passed") is True
        )
        blocking_diagnostics = [
            item
            for item in diagnostics
            if item in (decision_set.get("unresolved") or [])
            or item in (model_findings.get("issues") or [])
            or item in (saved_findings.get("issues") or [])
            or item in (appendix_audit.get("issues") or [])
            or item.get("severity") in {"error", "warning"}
        ]
        if published is None or not audits_passed or blocking_diagnostics:
            report.update({
                "status": "failed",
                "source_ast_revision": snapshot.ast["revision"],
                "source_facts_digest": source_facts_digest,
                "source_facts_digest_after": source_facts_digest_after,
                "source_facts_immutable": source_facts_immutable,
                "decision_set": decision_set,
                "enhancement": enhancement,
                "decision_ledger_replay": ledger_replay,
                "model_audit": model_findings,
                "saved_output_audit": saved_findings,
                "review_packet": context.require("review_packet"),
                "native_review": native_review,
                "appendix_semantics": appendix_semantics,
                "appendix_semantics_consumption": appendix_consumption,
                "appendix_preservation_audit": appendix_audit,
                "execution_plan": {
                    "profile": _NATIVE_PROFILE,
                    "modules": self._native_execution_plan_report(execution_plan),
                },
            })
            return RunResult(
                status="failed",
                exit_code=1,
                report=report,
                diagnostics=diagnostics,
                metrics=metrics.snapshot(),
            )
        capability_status = {
            "source_region": self._native_capability_status("source_regions", candidates, decision_set, committed, audits_passed),
            "document_structure": self._native_capability_status("source_structure", candidates, decision_set, committed, audits_passed),
            "protected_content": self._native_capability_status("protected_content", candidates, decision_set, committed, audits_passed),
            "list_semantics": self._native_capability_status("list_semantics", candidates, decision_set, committed, audits_passed),
            "table_semantics": self._native_capability_status("table_semantics", candidates, decision_set, committed, audits_passed),
            "caption_semantics": self._native_capability_status("caption_semantics", candidates, decision_set, committed, audits_passed),
            "appendix_semantics": self._native_capability_status("appendix_semantics", candidates, decision_set, committed, audits_passed),
        }
        capability_status["source_parser"] = "consumed" if audits_passed else "consumed_mismatch"
        item_candidate_ids = {
            str(candidate.get("candidate_id"))
            for candidate in list_candidates
            if str(candidate.get("candidate_id", "")).startswith("list-relation-")
        }
        continuation_candidate_ids = {
            str(candidate.get("candidate_id"))
            for candidate in list_candidates
            if ((candidate.get("proposal") or {}).get("value") or {}).get("association", {}).get("kind") == "sequence_continuation"
        }
        anchor_candidate_ids = {
            str(candidate.get("candidate_id"))
            for candidate in list_candidates
            if str(candidate.get("candidate_id", "")).startswith("list-anchor-")
        }
        item_consumed_count = sum(
            1
            for decision in decision_set.get("decisions", [])
            if item_candidate_ids.intersection(str(item) for item in decision.get("candidate_ids", []))
        )
        continuation_consumed_count = sum(
            1
            for decision in decision_set.get("decisions", [])
            if continuation_candidate_ids.intersection(str(item) for item in decision.get("candidate_ids", []))
        )
        anchor_consumed_count = sum(
            1
            for decision in decision_set.get("decisions", [])
            if anchor_candidate_ids.intersection(str(item) for item in decision.get("candidate_ids", []))
        )
        status = "completed" if not diagnostics else "completed_with_warnings"
        report.update({
            "status": status,
            "source_facts_summary": context.require("source_facts_summary"),
            "source_facts_digest": source_facts_digest,
            "source_facts_digest_after": source_facts_digest_after,
            "source_facts_immutable": source_facts_immutable,
            "source_ast_revision": snapshot.ast["revision"],
            "semantic_digest": normalized["semantic_digest"],
            "presentation_digest": normalized["presentation_digest"],
            "source_ast_hierarchy": context.require("source_ast_hierarchy"),
            "list_semantics": {
                "list_count": len(list_result["lists"]),
                "item_count": len(list_result["items"]),
                "continuation_count": len(list_result["continuations"]),
                "diagnostics": list(list_result["diagnostics"]),
                "downgrades": list(list_result.get("downgrades") or []),
                "groups": list(list_result["lists"]),
            },
            "list_semantics_consumption": {
                "candidate_count": len(list_candidates),
                "item_candidate_count": len(list_result["items"]),
                "item_consumed_count": item_consumed_count,
                "continuation_candidate_count": len(list_result["continuations"]),
                "continuation_consumed_count": continuation_consumed_count,
                "anchor_candidate_count": len(anchor_candidate_ids),
                "anchor_consumed_count": anchor_consumed_count,
                "unmatched_count": 0,
                "ambiguous_count": len(decision_set.get("unresolved") or []),
            },
            "table_semantics": dict(context.require("table_semantics")),
            "appendix_semantics": appendix_semantics,
            "appendix_semantics_consumption": appendix_consumption,
            "appendix_preservation_audit": appendix_audit,
            "native_transaction": {
                "status": committed.status,
                "transaction_id": committed.transaction_id,
                "decision_count": len(transaction_decision_set["decisions"]),
                "unresolved_count": len(transaction_decision_set.get("unresolved") or []),
                "expected_revision": transaction_decision_set["expected_revision"],
                "committed_revision": snapshot.ast["revision"],
                "metrics": committed.metrics,
                "overlays": snapshot.overlays,
                "presentation_overrides": snapshot.presentation_overrides,
            },
            "decision_set": decision_set,
            "review_packet": context.require("review_packet"),
            "native_review": native_review,
            "enhancement": enhancement,
            "decision_ledger_replay": ledger_replay,
            "execution_plan": {
                "profile": _NATIVE_PROFILE,
                "modules": self._native_execution_plan_report(execution_plan),
            },
            "native_module_evidence": copy.deepcopy(context.evidence),
            "model_audit": model_findings,
            "saved_output_audit": saved_findings,
            "capability_migration": {
                **capability_status,
            },
        })
        artifacts = {"docx": str(request.output_path)}
        publication_payloads: dict[Path, bytes] = {}
        for option_key, artifact_key, payload in (
            ("source_ast_path", "source_ast", snapshot.ast),
            ("normalized_ast_path", "normalized_ast", normalized),
        ):
            path = options.get(option_key)
            if path is not None and not options.get("release"):
                path = Path(path)
                publication_payloads[path] = json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8")
                artifacts[artifact_key] = str(path)
        report_path = options.get("report_path")
        if report_path is not None:
            report_path = Path(report_path)
            artifacts["report"] = str(report_path)
        report["artifacts"] = artifacts
        result = RunResult(status=status, exit_code=0, report=report, artifacts=artifacts, diagnostics=diagnostics, metrics=metrics.snapshot())
        if report_path is not None:
            report_payload = result.as_report()
            report_transform = options.get("report_transform")
            if callable(report_transform):
                report_payload = report_transform(report_payload)
            publication_payloads[report_path] = json.dumps(
                report_payload,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
        try:
            from .publication import PublicationError, publish_files_atomically

            publication_payloads[request.output_path] = Path(published).read_bytes()
            if options.get("protocol_mode") == "resume" and options.get("checkpoint_path") is not None:
                context.provide("pending_publication_payloads", publication_payloads)
                return result
            publish_files_atomically(publication_payloads)
        except (OSError, PublicationError) as exc:
            diagnostic = {
                "code": "PUBLICATION_FAILED",
                "severity": "error",
                "message": str(exc),
            }
            failed_report = {**report, "status": "failed"}
            return RunResult(
                status="failed",
                exit_code=1,
                report=failed_report,
                artifacts={},
                diagnostics=[*diagnostics, diagnostic],
                metrics=metrics.snapshot(),
            )
        return result
