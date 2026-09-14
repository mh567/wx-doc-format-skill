"""Conversion lifecycle primitives for the native-v2 pipeline."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from .run_result import ConversionRequest, RunResult

_NATIVE_PROFILE = "wx-default-v2"


class ConversionKernel:
    def convert(self, request: ConversionRequest) -> RunResult:
        return self._convert_modular_native_v2(request)

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

        from .native_stages import (
            LedgerReplayBlocked,
            NativeEnhancementBlocked,
            NativeFileProtocolPause,
            UnresolvedDecisionError,
        )
        from .semantic_modules import ModuleExecutionContext
        from .checkpoint_store import lock_native_checkpoint_identity
        from .decision_ledger import LedgerError, ledger_replay_report
        from .metrics import MetricsCollector
        from .parsers import detect_source_type
        from .registry import (
            build_native_run_result,
            build_native_v2_registry,
            checkpoint_modules,
            execution_plan_report,
            native_preflight,
        )
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
            execution_plan = native_preflight(
                None,
                detect_source_type(request.input_path),
                request.list_policy,
                registry=registry,
            )
            if protocol_mode == "resume":
                lock_native_checkpoint_identity(Path(options["checkpoint_path"]))
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
                    modules=checkpoint_modules(execution_plan),
                    configuration=options.get("checkpoint_configuration") or {},
                    source_store=store,
                )
                if not ready:
                    status = str(checkpoint["status"])
                    return RunResult(
                        status=status,
                        exit_code=1 if status in {"failed", "cancelled"} else 0,
                        terminal_replay=(
                            status in {"completed", "completed_with_warnings"}
                            or (
                                status == "failed"
                                and isinstance(checkpoint.get("review_outcome"), Mapping)
                                and checkpoint["review_outcome"].get("status") == "rolled_back"
                            )
                        ),
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
                    "module_registry": registry,
                },
            )
            registry.execute(execution_plan, context)
            result, publication_payloads = build_native_run_result(
                request,
                options,
                report,
                execution_plan,
                context,
                metrics,
                profile=_NATIVE_PROFILE,
            )
            if publication_payloads is not None:
                try:
                    from .publication import PublicationError, publish_files_atomically

                    publication_payloads[request.output_path] = Path(context.require("published_output")).read_bytes()
                    if options.get("protocol_mode") == "resume" and options.get("checkpoint_path") is not None:
                        context.provide("pending_publication_payloads", publication_payloads)
                    else:
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
                        report={**report, "status": "failed"},
                        artifacts={},
                        diagnostics=[*result.diagnostics, diagnostic],
                        metrics=metrics.snapshot(),
                    )
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
                    modules=checkpoint_modules(execution_plan),
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
                "decision_ledger_replay": ledger_replay_report(context),
                "execution_plan": {
                    "profile": _NATIVE_PROFILE,
                    "modules": execution_plan_report(execution_plan),
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
                        **(ledger_replay_report(context) or {"enabled": True}),
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

