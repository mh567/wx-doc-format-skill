"""Conversion lifecycle primitives shared by modular and legacy paths."""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .registry import ModuleRegistry, PlanError


_WX_FULL_RULE_CAPABILITIES = (
    "source_parser",
    "source_region",
    "document_structure",
    "protected_content",
    "list_semantics",
    "table_semantics",
    "caption_semantics",
    "appendix_semantics",
    "template_provider",
    "wx_normalization",
    "renderer",
    "saved_output_audit",
    "template_audit",
    "publisher",
    "semantic_equivalence_audit",
    "source_parser_equivalence_audit",
)

_NATIVE_PROFILE = "wx-default-v2"


@dataclass(frozen=True)
class ConversionRequest:
    input_path: Path
    output_path: Path
    template_path: Path | None = None
    pipeline: str = "legacy"
    list_policy: str = "legacy"
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_path", Path(self.input_path))
        object.__setattr__(self, "output_path", Path(self.output_path))
        if self.template_path is not None:
            object.__setattr__(self, "template_path", Path(self.template_path))
        if self.pipeline not in {"legacy", "modular"}:
            raise ValueError(f"unsupported pipeline {self.pipeline!r}")
        if self.list_policy not in {"legacy", "wx-default-v2"}:
            raise ValueError(f"unsupported list policy {self.list_policy!r}")
        if self.pipeline == "legacy" and self.list_policy != "legacy":
            raise ValueError("legacy pipeline only accepts legacy list policy")


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
    def __init__(
        self,
        *,
        legacy_runner: Callable[[ConversionRequest], RunResult] | None = None,
        modular_runner: Callable[[ConversionRequest], RunResult] | None = None,
    ) -> None:
        self._legacy_runner = legacy_runner
        self._modular_runner = modular_runner

    def convert(self, request: ConversionRequest) -> RunResult:
        if request.pipeline == "modular":
            return self._convert_modular(request)
        if self._legacy_runner is None:
            return RunResult(
                status="failed",
                exit_code=1,
                diagnostics=[{"code": "LEGACY_RUNNER_UNSET", "message": "legacy runner must be provided by the stable CLI entry"}],
            )
        return self._legacy_runner(request)

    @staticmethod
    def _facts_to_ast(facts: Mapping[str, Any]) -> dict[str, Any]:
        """Compatibility seam for the generic SourceFacts to AST construction.

        The construction itself lives in ``wxdf.semantics.source_ast`` so that
        the native chain owns it as a registered Module.
        """

        from .semantics.source_ast import build_source_ast

        return build_source_ast(facts)

    def _convert_modular(self, request: ConversionRequest) -> RunResult:
        options = request.options or {}
        if self._modular_runner is None and options.get("profile") == "native_v2":
            return self._convert_modular_native_v2(request)
        if self._modular_runner is None and options.get("profile") == "experimental_native":
            return self._convert_modular_native_experimental(request)
        if self._modular_runner is None and options.get("llm_enhance", "off") != "off":
            return RunResult(
                status="unsupported",
                exit_code=2,
                diagnostics=[{
                    "code": "MODULAR_ENHANCEMENT_UNSUPPORTED",
                    "message": "modular pure-rule path does not accept online enhancement",
                }],
            )
        if self._modular_runner is None and options.get("protocol_mode") in {"generate_requests", "resume"}:
            return RunResult(
                status="unsupported",
                exit_code=2,
                diagnostics=[{
                    "code": "MODULAR_PROTOCOL_UNSUPPORTED",
                    "message": "modular pure-rule path does not accept request generation or resume protocol",
                }],
            )
        if self._modular_runner is None:
            return RunResult(
                status="unsupported",
                exit_code=2,
                diagnostics=[{
                    "code": "MODULAR_PROFILE_INCOMPLETE",
                    "message": "wx_full_rules requires a complete conversion adapter",
                    "missing_capabilities": list(_WX_FULL_RULE_CAPABILITIES),
                }],
            )

        from .builtin_modules import (
            ModuleExecutionContext,
            appendix_semantics_module,
            caption_semantics_module,
            document_structure_module,
            legacy_full_adapter_module,
            semantic_equivalence_module,
            source_parser_equivalence_module,
            source_projection_module,
            protected_content_module,
            list_semantics_module,
            table_semantics_module,
            source_facts_module,
            source_region_module,
        )

        registry = ModuleRegistry([
            source_facts_module(),
            source_region_module(),
            source_projection_module(),
            document_structure_module(),
            protected_content_module(),
            list_semantics_module(),
            table_semantics_module(),
            caption_semantics_module(),
            appendix_semantics_module(),
            legacy_full_adapter_module(
                self._modular_runner,
                provides=tuple(
                    capability
                    for capability in _WX_FULL_RULE_CAPABILITIES
                    if capability not in {
                        "source_region",
                        "source_parser",
                        "document_structure",
                        "protected_content",
                        "list_semantics",
                        "table_semantics",
                        "caption_semantics",
                        "appendix_semantics",
                        "semantic_equivalence_audit",
                        "source_parser_equivalence_audit",
                    }
                ),
            ),
            source_parser_equivalence_module(),
            semantic_equivalence_module(),
        ])
        execution_context = ModuleExecutionContext(request=request)
        try:
            plan = registry.build(
                {"pipeline": "modular", "list_policy": request.list_policy},
                required_capabilities=_WX_FULL_RULE_CAPABILITIES,
            )
            results = registry.execute(plan, execution_context)
        except PlanError as exc:
            return RunResult(
                status="unsupported",
                exit_code=2,
                diagnostics=[{"code": "MODULAR_PLAN_INVALID", "message": str(exc)}],
            )
        except Exception as exc:
            return RunResult(
                status="failed",
                exit_code=1,
                diagnostics=[{
                    "code": "MODULAR_MODULE_EXECUTION_FAILED",
                    "message": str(exc),
                    "type": type(exc).__name__,
                }],
            )

        result = execution_context.require("legacy_run_result")
        if not isinstance(result, RunResult):
            return RunResult(
                status="failed",
                exit_code=1,
                diagnostics=[{
                    "code": "MODULAR_ADAPTER_RESULT_INVALID",
                    "message": "full conversion adapter must return RunResult",
                }],
            )
        equivalence_audit = execution_context.require("semantic_equivalence_audit")
        parser_equivalence_audit = execution_context.require("source_parser_equivalence_audit")
        shadow_state = {
            "equivalent": "shadow_verified",
            "mismatch": "shadow_mismatch",
            "unobserved": "shadow_unobserved",
        }.get(parser_equivalence_audit.get("status"), "shadow_unobserved")
        parser_consumption = result.report.get("source_parser_consumption") or {}
        parser_consumed = bool(parser_consumption)
        parser_complete = (
            parser_consumed
            and int(parser_consumption.get("unmatched_count", 0)) == 0
            and int(parser_consumption.get("ambiguous_count", 0)) == 0
            and int(parser_consumption.get("paragraph_candidate_count", 0))
            == int(parser_consumption.get("paragraph_consumed_count", -1))
            and int(parser_consumption.get("table_candidate_count", 0))
            == int(parser_consumption.get("table_consumed_count", -1))
        )
        if parser_complete:
            parser_state = {
                "equivalent": "native_consumed",
                "mismatch": "native_consumed_mismatch",
                "unobserved": "native_consumed_unobserved",
            }.get(parser_equivalence_audit.get("status"), "native_consumed_unobserved")
        elif parser_consumed:
            parser_state = "native_partial"
        else:
            parser_state = shadow_state
        structure_consumption = result.report.get("structure_decision_consumption") or {}
        structure_consumed = bool(structure_consumption)
        structure_complete = (
            structure_consumed
            and int(structure_consumption.get("unmatched_count", 0)) == 0
            and int(structure_consumption.get("ambiguous_count", 0)) == 0
        )
        if structure_complete:
            structure_state = {
                "equivalent": "native_consumed",
                "mismatch": "native_consumed_mismatch",
                "unobserved": "native_consumed_unobserved",
            }.get(equivalence_audit.get("status"), "native_consumed_unobserved")
        elif structure_consumed:
            structure_state = "native_partial"
        else:
            structure_state = "native_delivered"
        region_consumption = result.report.get("source_region_decision_consumption") or {}
        region_consumed = bool(region_consumption)
        region_complete = (
            region_consumed
            and int(region_consumption.get("unmatched_count", 0)) == 0
            and int(region_consumption.get("ambiguous_count", 0)) == 0
        )
        if region_complete:
            region_state = {
                "equivalent": "native_consumed",
                "mismatch": "native_consumed_mismatch",
                "unobserved": "native_consumed_unobserved",
            }.get(equivalence_audit.get("status"), "native_consumed_unobserved")
        elif region_consumed:
            region_state = "native_partial"
        else:
            region_state = "native_delivered"
        protected_consumption = result.report.get("protected_content_consumption") or {}
        protected_consumed = bool(protected_consumption)
        protected_complete = (
            protected_consumed
            and int(protected_consumption.get("unmatched_count", 0)) == 0
            and int(protected_consumption.get("ambiguous_count", 0)) == 0
            and int(protected_consumption.get("candidate_count", 0))
            == (
                int(protected_consumption.get("applied_count", 0))
                + int(protected_consumption.get("unchanged_count", 0))
            )
        )
        if protected_complete:
            protected_state = "native_consumed"
        elif protected_consumed:
            protected_state = "native_partial"
        else:
            protected_state = "native_delivered"
        list_consumption = result.report.get("list_semantics_consumption") or {}
        list_consumed = bool(list_consumption)
        list_complete = (
            list_consumed
            and int(list_consumption.get("unmatched_count", 0)) == 0
            and int(list_consumption.get("ambiguous_count", 0)) == 0
            and int(list_consumption.get("item_candidate_count", 0))
            == int(list_consumption.get("item_consumed_count", -1))
            and int(list_consumption.get("continuation_candidate_count", 0))
            == int(list_consumption.get("continuation_consumed_count", -1))
        )
        list_audit = result.report.get("list_preservation_audit") or {}
        if list_complete and list_audit.get("passed") is True:
            list_state = "native_consumed"
        elif list_complete:
            list_state = "native_consumed_mismatch"
        elif list_consumed:
            list_state = "native_partial"
        else:
            list_state = "native_delivered"
        table_consumption = result.report.get("table_semantics_consumption") or {}
        table_consumed = bool(table_consumption)
        table_complete = (
            table_consumed
            and int(table_consumption.get("unmatched_count", 0)) == 0
            and int(table_consumption.get("ambiguous_count", 0)) == 0
            and int(table_consumption.get("candidate_count", 0))
            == int(table_consumption.get("consumed_count", -1))
        )
        table_audit = result.report.get("table_semantics_audit") or {}
        if table_complete and table_audit.get("passed") is True:
            table_state = "native_consumed"
        elif table_complete:
            table_state = "native_consumed_mismatch"
        elif table_consumed:
            table_state = "native_partial"
        else:
            table_state = "native_delivered"
        caption_consumption = result.report.get("caption_semantics_consumption") or {}
        caption_consumed = bool(caption_consumption)
        caption_complete = (
            caption_consumed
            and int(caption_consumption.get("unmatched_count", 0)) == 0
            and int(caption_consumption.get("ambiguous_count", 0)) == 0
            and int(caption_consumption.get("candidate_count", 0))
            == int(caption_consumption.get("consumed_count", -1))
            and int(caption_consumption.get("candidate_count", 0))
            == int(caption_consumption.get("associated_count", -1))
        )
        caption_audit = result.report.get("caption_placement_model_audit") or {}
        if caption_complete and caption_audit.get("passed") is True:
            caption_state = "native_consumed"
        elif caption_complete:
            caption_state = "native_consumed_mismatch"
        elif caption_consumed:
            caption_state = "native_partial"
        else:
            caption_state = "native_delivered"
        appendix_consumption = result.report.get("appendix_semantics_consumption") or {}
        appendix_consumed = bool(appendix_consumption)
        appendix_complete = (
            appendix_consumed
            and int(appendix_consumption.get("unmatched_count", 0)) == 0
            and int(appendix_consumption.get("ambiguous_count", 0)) == 0
            and int(appendix_consumption.get("range_candidate_count", 0))
            == int(appendix_consumption.get("range_consumed_count", -1))
            and int(appendix_consumption.get("heading_candidate_count", 0))
            == int(appendix_consumption.get("heading_consumed_count", -1))
        )
        appendix_audit = result.report.get("appendix_preservation_audit") or {}
        if appendix_complete and appendix_audit.get("passed") is True:
            appendix_state = "native_consumed"
        elif appendix_complete:
            appendix_state = "native_consumed_mismatch"
        elif appendix_consumed:
            appendix_state = "native_partial"
        else:
            appendix_state = "native_delivered"
        result.report.update({
            "pipeline": "modular",
            "list_policy": request.list_policy,
            "migration_adapter": "legacy-full-v1",
            "native_module_evidence": copy.deepcopy(execution_context.evidence),
            "semantic_equivalence_audit": copy.deepcopy(equivalence_audit),
            "source_parser_equivalence_audit": copy.deepcopy(parser_equivalence_audit),
            "capability_migration": {
                "source_parser": parser_state,
                "source_region": region_state,
                "document_structure": structure_state,
                "protected_content": protected_state,
                "list_semantics": list_state,
                "table_semantics": table_state,
                "caption_semantics": caption_state,
                "appendix_semantics": appendix_state,
            },
            "execution_plan": {
                "profile": "wx_full_rules",
                "modules": [
                    {"module_id": module.module_id, "version": module.version}
                    for module in plan.modules
                ],
                "required_capabilities": list(_WX_FULL_RULE_CAPABILITIES),
            },
        })
        report_path = options.get("report_path")
        if report_path is not None and result.status not in {"waiting_external"}:
            report_path = Path(report_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result.as_report(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result

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
                diagnostics=[{
                    "code": "PUBLICATION_RECOVERY_FAILED",
                    "severity": "error",
                    "message": str(exc),
                }],
            )
        if not request.input_path.is_file():
            return RunResult(status="failed", exit_code=1, diagnostics=[{"code": "INPUT_NOT_FOUND", "message": str(request.input_path)}])

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


    def _convert_modular_native_experimental(self, request: ConversionRequest) -> RunResult:
        """Retain the incomplete native path for staged module replacement tests."""

        options = request.options or {}
        if not request.input_path.is_file():
            return RunResult(status="failed", exit_code=1, diagnostics=[{"code": "INPUT_NOT_FOUND", "message": str(request.input_path)}])

        from .builtin_modules import ModuleExecutionContext, experimental_native_modules
        from .metrics import MetricsCollector
        from .registry import ModuleRegistry

        metrics = MetricsCollector()
        report: dict[str, Any] = {
            "pipeline": "modular",
            "list_policy": request.list_policy,
            "status": "running",
            "contract_versions": {"source_facts": "1.0", "ast": "2.0", "normalized": "1.0"},
        }
        try:
            registry = ModuleRegistry(experimental_native_modules())
            plan = registry.build({"pipeline": "modular", "list_policy": request.list_policy})
            context = ModuleExecutionContext(request=request, services={"metrics": metrics})
            registry.execute(plan, context)

            source_ast = context.require("source_ast")
            normalized = context.require("normalized")
            list_result = context.require("list_semantics_result")
            model_findings = context.require("model_audit")
            saved_findings = context.require("saved_output_audit")

            report.update({
                "status": "completed" if not list_result["diagnostics"] else "completed_with_warnings",
                "source_facts_summary": context.require("source_facts_summary"),
                "source_ast_revision": source_ast["revision"],
                "semantic_digest": normalized["semantic_digest"],
                "presentation_digest": normalized["presentation_digest"],
                "list_semantics": {
                    "list_count": len(list_result["lists"]),
                    "item_count": len(list_result["items"]),
                    "diagnostics": list(list_result["diagnostics"]),
                },
                "model_audit": model_findings,
                "saved_output_audit": saved_findings,
            })
            diagnostics = list(list_result["diagnostics"])
            if not model_findings["passed"] or not saved_findings["passed"]:
                report["status"] = "completed_with_warnings"
                diagnostics.extend(model_findings["issues"])
                diagnostics.extend(saved_findings["issues"])
            artifacts = {"docx": str(request.output_path)}
            for option_key, artifact_key, payload in (
                ("source_ast_path", "source_ast", source_ast),
                ("normalized_ast_path", "normalized_ast", normalized),
            ):
                path = options.get(option_key)
                if path is not None and not options.get("release"):
                    path = Path(path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    artifacts[artifact_key] = str(path)
            report["artifacts"] = artifacts
            result = RunResult(
                status=report["status"],
                exit_code=0,
                report=report,
                artifacts=artifacts,
                diagnostics=diagnostics,
                metrics=metrics.snapshot(),
            )
            report_path = options.get("report_path")
            if report_path is not None:
                report_path = Path(report_path)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(result.as_report(), ensure_ascii=False, indent=2), encoding="utf-8")
            return result
        except Exception as exc:
            return RunResult(
                status="failed",
                exit_code=1,
                report={**report, "status": "failed"},
                diagnostics=[{"code": "MODULAR_CONVERSION_FAILED", "message": str(exc), "type": type(exc).__name__}],
                metrics=metrics.snapshot(),
            )
