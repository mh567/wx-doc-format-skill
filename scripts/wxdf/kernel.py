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
        """Convert public SourceFacts to the first Source AST revision."""

        units: dict[str, dict[str, Any]] = {}
        for unit_id, unit in facts["units"].items():
            kind = unit.get("kind")
            source_ref = copy.deepcopy(unit["source_ref"])
            if kind == "paragraph":
                block: dict[str, Any] = {
                    "block_type": "paragraph",
                    "role": unit.get("role", "body"),
                    "inline_content": copy.deepcopy(unit.get("inline_content", [])),
                    "source_ref": source_ref,
                }
                if block["role"] == "heading":
                    block["level"] = int(unit.get("level") or 1)
                units[unit_id] = block
            elif kind in {"code_block", "opaque", "separator"}:
                units[unit_id] = {
                    "block_type": "opaque",
                    "diagnostic_ids": list(unit.get("diagnostic_ids") or [f"source_{kind}"]),
                    "source_ref": source_ref,
                }
            else:
                units[unit_id] = {
                    "block_type": "opaque",
                    "diagnostic_ids": [f"unsupported_source_unit:{kind}"],
                    "source_ref": source_ref,
                }

        blocks = dict(units)
        containers: dict[str, dict[str, Any]] = {}
        for container_id, container in facts["containers"].items():
            kind = container.get("kind")
            if kind == "table":
                table = {"block_type": "table", "table_type": "data"}
                if "source_ref" in container:
                    table["source_ref"] = copy.deepcopy(container["source_ref"])
                else:
                    table["generated_by"] = "source_facts_adapter"
                    table["generation_basis"] = [container_id]
                blocks[container_id] = table
                continue
            containers[container_id] = copy.deepcopy(dict(container))
        containers.setdefault("root", {"kind": "document"})
        ast = {
            "schema_version": "2.0",
            "revision": 1,
            "document": {
                "root_id": "root",
                "blocks": blocks,
                "containers": containers,
                "children": copy.deepcopy(facts["children"]),
                "associations": [],
                "resources": copy.deepcopy(facts["resources"]),
            },
        }
        from .validation import validate_ast

        validate_ast(ast)
        return ast

    def _convert_modular(self, request: ConversionRequest) -> RunResult:
        options = request.options or {}
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

    def _convert_modular_native_experimental(self, request: ConversionRequest) -> RunResult:
        """Retain the incomplete native path for staged module replacement tests."""

        options = request.options or {}
        if not request.input_path.is_file():
            return RunResult(status="failed", exit_code=1, diagnostics=[{"code": "INPUT_NOT_FOUND", "message": str(request.input_path)}])

        from .auditing import check_model, check_saved_output
        from .metrics import MetricsCollector
        from .normalization import normalize
        from .parsers import parse, summarize_source_facts
        from .rendering import render
        from .semantics.lists import analyze_lists
        from .source_store import SourceStore
        from template_profile import load_template_profile

        metrics = MetricsCollector()
        report: dict[str, Any] = {
            "pipeline": "modular",
            "list_policy": request.list_policy,
            "status": "running",
            "contract_versions": {"source_facts": "1.0", "ast": "2.0", "normalized": "1.0"},
        }
        try:
            with metrics.stage("source"):
                store = SourceStore()
                facts = parse(request.input_path, {"source_store": store})
                metrics.record_source_opened()
                source_summary = summarize_source_facts(facts)
            with metrics.stage("scope"):
                list_result = analyze_lists(facts)
            with metrics.stage("role"):
                source_ast = self._facts_to_ast(facts)
            with metrics.stage("normalize"):
                profile = load_template_profile(request.template_path) if request.template_path else {"resolved_styles": {}, "numbering_ids": {}}
                normalized = normalize(source_ast, profile, strategy=request.list_policy)
            with metrics.stage("render"):
                render_result = render(normalized, request.output_path, template_path=request.template_path)
                metrics.record_render()
            with metrics.stage("audit"):
                model_findings = check_model(normalized)
                saved_findings = check_saved_output(request.output_path, normalized, render_result["render_map"])

            report.update({
                "status": "completed" if not list_result["diagnostics"] else "completed_with_warnings",
                "source_facts_summary": source_summary,
                "source_ast_revision": source_ast["revision"],
                "semantic_digest": normalized["semantic_digest"],
                "presentation_digest": normalized["presentation_digest"],
                "list_semantics": {"list_count": len(list_result["lists"]), "item_count": len(list_result["items"]), "diagnostics": list_result["diagnostics"]},
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
