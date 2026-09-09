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
                table_semantics = container.get("table_semantics")
                if isinstance(table_semantics, Mapping):
                    table["table_type"] = str(table_semantics.get("table_type") or "data")
                    if "header_rows" in table_semantics:
                        table["header_rows"] = int(table_semantics.get("header_rows") or 0)
                if isinstance(container.get("layout"), Mapping):
                    table["layout"] = copy.deepcopy(dict(container["layout"]))
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
        """Materialize implicit list relations while retaining source hierarchy."""

        ast = copy.deepcopy(dict(source_ast))
        document = ast["document"]
        blocks = document["blocks"]
        containers = document["containers"]
        children = document["children"]

        def node_map() -> dict[str, dict[str, Any]]:
            result: dict[str, dict[str, Any]] = {}
            result.update(blocks)
            result.update(containers)
            return result

        def parent_map() -> dict[str, str]:
            return {
                child_id: parent_id
                for parent_id, child_ids in children.items()
                for child_id in child_ids
            }

        original_parents = parent_map()
        original_positions = {
            (parent_id, child_id): index
            for parent_id, child_ids in children.items()
            for index, child_id in enumerate(child_ids)
        }
        list_map: dict[str, str] = {}
        item_map: dict[str, str] = {}
        list_specs = {str(item["list_id"]): item for item in list_result.get("lists", [])}
        item_records: dict[str, list[Mapping[str, Any]]] = {}
        for relation in list_result.get("items", []):
            item_records.setdefault(str(relation["list_id"]), []).append(relation)

        for list_id, spec in list_specs.items():
            list_map[list_id] = list_id
            if list_id in containers and containers[list_id].get("kind") == "list":
                continue
            records = item_records.get(list_id, [])
            first_id = str(records[0]["item_id"]) if records else ""
            source_node = facts.get("units", {}).get(first_id) or facts.get("containers", {}).get(first_id) or {}
            list_node: dict[str, Any] = {
                "kind": "list",
                "list_kind": str(spec.get("list_kind") or "ordered"),
                "source_start": max(1, int(spec.get("source_start") or 1)),
                "source_sequence_id": str(spec.get("source_sequence_id") or list_id),
            }
            if isinstance(source_node.get("source_ref"), Mapping):
                list_node["source_ref"] = copy.deepcopy(dict(source_node["source_ref"]))
            containers[list_id] = list_node
            children.setdefault(list_id, [])

        for relation in list_result.get("items", []):
            source_item_id = str(relation["item_id"])
            if source_item_id in containers and containers[source_item_id].get("kind") == "list_item":
                item_map[source_item_id] = source_item_id
                continue
            item_id = f"native-list-item-{source_item_id}"
            while item_id in node_map():
                item_id = f"{item_id}-copy"
            unit = facts.get("units", {}).get(source_item_id)
            evidence = facts.get("evidence", {}).get(source_item_id, {})
            item_node: dict[str, Any] = {
                "kind": "list_item",
                "source_marker": relation.get("source_marker") or evidence.get("marker_text"),
                "source_ordinal": relation.get("source_ordinal") or evidence.get("ordinal"),
                "source_sequence_id": relation.get("source_sequence_id") or source_item_id,
            }
            if isinstance(unit, Mapping) and isinstance(unit.get("source_ref"), Mapping):
                item_node["source_ref"] = copy.deepcopy(dict(unit["source_ref"]))
            containers[item_id] = item_node
            children[item_id] = [source_item_id]
            old_parent = original_parents.get(source_item_id)
            if old_parent in children and source_item_id in children[old_parent]:
                children[old_parent].remove(source_item_id)
            item_map[source_item_id] = item_id

        for list_id, spec in list_specs.items():
            actual_list_id = list_map[list_id]
            records = item_records.get(list_id, [])
            item_ids = [item_map[str(relation["item_id"])] for relation in records]
            for item_id in item_ids:
                current_parent = parent_map().get(item_id)
                if current_parent and current_parent != actual_list_id and item_id in children.get(current_parent, []):
                    children[current_parent].remove(item_id)
                if item_id not in children[actual_list_id]:
                    children[actual_list_id].append(item_id)

            if list_id in source_ast["document"]["containers"]:
                continue
            parent_item = spec.get("parent_item_id")
            parent_id = item_map.get(str(parent_item)) if parent_item is not None else None
            if parent_id is None and records:
                parent_id = original_parents.get(str(records[0]["item_id"]))
            parent_id = parent_id or document["root_id"]
            siblings = children.setdefault(parent_id, [])
            if actual_list_id not in siblings:
                first_source_id = str(records[0]["item_id"]) if records else ""
                index = original_positions.get((parent_id, first_source_id), len(siblings))
                siblings.insert(min(index, len(siblings)), actual_list_id)

        candidates: list[dict[str, Any]] = []
        for candidate in list_result.get("candidates", []):
            mapped = copy.deepcopy(dict(candidate))
            mapped["target_node_ids"] = [
                item_map.get(str(node_id), str(node_id))
                for node_id in candidate.get("target_node_ids", [])
            ]
            proposal = mapped.get("proposal")
            if isinstance(proposal, Mapping):
                proposal = dict(proposal)
                value = proposal.get("value")
                if isinstance(value, Mapping):
                    value = dict(value)
                    if "new_parent_id" in value:
                        value["new_parent_id"] = list_map.get(str(value["new_parent_id"]), str(value["new_parent_id"]))
                    if value.get("parent_item_id") is not None:
                        value["parent_item_id"] = item_map.get(str(value["parent_item_id"]), str(value["parent_item_id"]))
                    proposal["value"] = value
                mapped["proposal"] = proposal
            candidates.append(mapped)

        from .validation import validate_ast

        validate_ast(ast)
        return ast, candidates, item_map

    @staticmethod
    def _native_preflight(source_ast: Mapping[str, Any], source_type: str, list_policy: str) -> Any:
        """Validate the native module contract before candidate execution."""

        from .contracts import AST_BLOCK_TYPES, AST_CONTAINER_KINDS
        from .registry import ModuleDescriptor, build_plan

        supported_nodes = tuple(sorted(set(AST_BLOCK_TYPES) | set(AST_CONTAINER_KINDS)))
        common = {
            "contract_version": "2.0",
            "supported_source_types": ("markdown", "docx"),
            "supported_node_kinds": supported_nodes,
            "failure_policy": "fail",
        }
        descriptors = (
            ModuleDescriptor("native-source-parser", "2.0", ("source",), (), ("source_facts",), **common),
            ModuleDescriptor("native-candidate-set", "2.0", ("scope", "role", "relation"), ("source_facts",), ("candidate_set",), **common),
            ModuleDescriptor("native-resolver", "2.0", ("relation",), ("candidate_set",), ("decision_set",), **common),
            ModuleDescriptor("native-transaction", "2.0", ("relation",), ("decision_set",), ("source_ast_revision",), **common),
            ModuleDescriptor("native-normalizer", "2.0", ("normalize",), ("source_ast_revision",), ("normalized",), **common),
            ModuleDescriptor("native-renderer", "2.0", ("render",), ("normalized",), ("render_plan",), **common),
            ModuleDescriptor(
                "native-auditor",
                "2.0",
                ("audit",),
                ("render_plan",),
                ("model_audit", "saved_output_audit"),
                required_audits=("model_audit", "saved_output_audit"),
                **common,
            ),
            ModuleDescriptor(
                "native-publisher",
                "2.0",
                ("publish",),
                ("saved_output_audit",),
                ("published_output",),
                **common,
            ),
        )
        node_kinds = {
            str(node.get("block_type") or node.get("kind"))
            for collection in (source_ast["document"]["blocks"], source_ast["document"]["containers"])
            for node in collection.values()
            if node.get("block_type") or node.get("kind")
        }
        return build_plan(
            {
                "pipeline": "modular",
                "list_policy": list_policy,
                "source_type": source_type,
                "node_kinds": sorted(node_kinds),
                "available_audits": ("model_audit", "saved_output_audit"),
            },
            descriptors,
        )

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

    def _convert_modular_native_v2(self, request: ConversionRequest) -> RunResult:
        """Run the modular source, decision, transaction and render chain."""

        options = request.options or {}
        if not request.input_path.is_file():
            return RunResult(status="failed", exit_code=1, diagnostics=[{"code": "INPUT_NOT_FOUND", "message": str(request.input_path)}])

        from .auditing import check_model, check_saved_output
        from .metrics import MetricsCollector
        from .normalization import normalize
        from .parsers import parse, summarize_source_facts
        from .rendering import render
        from .resolver import resolve
        from .semantics.appendices import collect_source_appendix_semantics
        from .semantics.captions import collect_caption_candidates
        from .semantics.lists import analyze_lists
        from .semantics.protected import collect_protected_candidates
        from .semantics.regions import resolve_source_regions
        from .semantics.structure import collect_structure_candidates
        from .semantics.tables import collect_source_table_semantics, collect_table_candidates
        from .source_store import SourceStore
        from .template import load_template_bundle
        from .transaction import apply_stage_batch, make_snapshot

        metrics = MetricsCollector()
        report: dict[str, Any] = {
            "pipeline": "modular",
            "list_policy": request.list_policy,
            "profile": "wx_full_rules-native-v2",
            "status": "running",
            "contract_versions": {"source_facts": "1.0", "ast": "2.0", "decision_set": "2.0", "normalized": "1.0"},
            "module_chain": [
                "SourceParser", "SourceFacts", "SemanticModuleCandidateSet", "SemanticResolverDecisionSet",
                "TransactionManagerSourceAST", "NormalizationPolicy", "RenderPlan", "Renderer", "AuditModule", "Publisher",
            ],
        }
        store = SourceStore()
        try:
            with metrics.stage("source"):
                facts = parse(request.input_path, {"source_store": store})
                metrics.record_source_opened()
                source_summary = summarize_source_facts(facts)
            with metrics.stage("scope"):
                regions = resolve_source_regions(request.input_path, facts)
            with metrics.stage("role"):
                structure_candidates = collect_structure_candidates(facts, regions["regions"])
                protected_candidates = collect_protected_candidates(facts)
            with metrics.stage("relation"):
                list_result = analyze_lists(facts)
                source_ast = self._facts_to_ast(facts)
                source_ast, list_candidates, item_map = self._materialize_list_semantics(source_ast, facts, list_result)
                table_source = collect_source_table_semantics(facts)
                table_candidates = collect_table_candidates(source_ast)
                caption_candidates = collect_caption_candidates(source_ast)

            execution_plan = self._native_preflight(source_ast, facts["source"]["source_type"], request.list_policy)

            candidates = [
                *regions.get("candidates", []),
                *structure_candidates,
                *protected_candidates,
                *list_candidates,
                *table_candidates,
                *caption_candidates,
            ]
            decision_set = resolve(candidates, {"revision": source_ast["revision"]})
            with metrics.stage("transaction"):
                committed = apply_stage_batch(
                    make_snapshot(source_ast),
                    decision_set["decisions"],
                    expected_revision=source_ast["revision"],
                )
            snapshot = committed.snapshot
            with metrics.stage("normalize"):
                template = load_template_bundle(request.template_path) if request.template_path else {
                    "resolved_styles": {
                        "list_letter": "List Number",
                        "list_decimal": "List Number 2",
                        "list_dash": "List Bullet",
                        "list_bullet": "List Bullet 2",
                    },
                    "numbering_ids": {},
                }
                normalized = normalize(
                    snapshot.ast,
                    template,
                    strategy=request.list_policy,
                    overlay=snapshot.overlays,
                    presentation_override=snapshot.presentation_overrides,
                    context={"transaction_id": committed.transaction_id, "decision_set_version": decision_set["decision_set_version"]},
                )
            resources: dict[str, Any] = {}
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
            with metrics.stage("render"):
                if hasattr(template, "path"):
                    render_result = render(normalized, request.output_path, template_bundle=template, resources=resources)
                else:
                    render_result = render(normalized, request.output_path, resources=resources)
                metrics.record_render()
            with metrics.stage("audit"):
                model_findings = check_model(normalized)
                saved_findings = check_saved_output(request.output_path, normalized, render_result["render_map"])

            diagnostics = list(list_result.get("diagnostics") or [])
            diagnostics.extend(decision_set.get("unresolved") or [])
            diagnostics.extend(model_findings.get("issues") or [])
            diagnostics.extend(saved_findings.get("issues") or [])
            audits_passed = model_findings.get("passed") is True and saved_findings.get("passed") is True
            capability_status = {
                "source_region": self._native_capability_status("source_regions", candidates, decision_set, committed, audits_passed),
                "document_structure": self._native_capability_status("source_structure", candidates, decision_set, committed, audits_passed),
                "protected_content": self._native_capability_status("protected_content", candidates, decision_set, committed, audits_passed),
                "list_semantics": self._native_capability_status("list_semantics", candidates, decision_set, committed, audits_passed),
                "table_semantics": self._native_capability_status("table_semantics", candidates, decision_set, committed, audits_passed),
                "caption_semantics": self._native_capability_status("caption_semantics", candidates, decision_set, committed, audits_passed),
            }
            capability_status["source_parser"] = "consumed" if audits_passed else "consumed_mismatch"
            capability_status["appendix_semantics"] = "delivered"
            list_candidate_ids = {str(candidate.get("candidate_id")) for candidate in list_candidates}
            list_consumed_count = sum(
                1
                for decision in decision_set.get("decisions", [])
                if list_candidate_ids.intersection(str(item) for item in decision.get("candidate_ids", []))
            )
            status = "completed" if not diagnostics else "completed_with_warnings"
            report.update({
                "status": status,
                "source_facts_summary": source_summary,
                "source_ast_revision": snapshot.ast["revision"],
                "semantic_digest": normalized["semantic_digest"],
                "presentation_digest": normalized["presentation_digest"],
                "source_ast_hierarchy": {
                    "list_count": sum(node.get("kind") == "list" for node in snapshot.ast["document"]["containers"].values()),
                    "item_count": sum(node.get("kind") == "list_item" for node in snapshot.ast["document"]["containers"].values()),
                    "nested_list_count": sum(
                        1
                        for parent_id, node in snapshot.ast["document"]["containers"].items()
                        if node.get("kind") == "list_item"
                        and any(snapshot.ast["document"]["containers"].get(child, {}).get("kind") == "list" for child in snapshot.ast["document"]["children"].get(parent_id, []))
                    ),
                },
                "list_semantics": {
                    "list_count": len(list_result["lists"]),
                    "item_count": len(list_result["items"]),
                    "continuation_count": len(list_result["continuations"]),
                    "diagnostics": list(list_result["diagnostics"]),
                },
                "list_semantics_consumption": {
                    "candidate_count": len(list_candidates),
                    "item_candidate_count": len(list_result["items"]),
                    "item_consumed_count": list_consumed_count,
                    "continuation_candidate_count": len(list_result["continuations"]),
                    "continuation_consumed_count": 0,
                    "unmatched_count": 0,
                    "ambiguous_count": len(decision_set.get("unresolved") or []),
                },
                "table_semantics": {"candidate_count": len(table_candidates), "source_count": len(table_source)},
                "appendix_semantics": collect_source_appendix_semantics(facts),
                "native_transaction": {
                    "status": committed.status,
                    "transaction_id": committed.transaction_id,
                    "decision_count": len(decision_set["decisions"]),
                    "unresolved_count": len(decision_set.get("unresolved") or []),
                    "expected_revision": decision_set["expected_revision"],
                    "committed_revision": snapshot.ast["revision"],
                    "metrics": committed.metrics,
                    "overlays": snapshot.overlays,
                    "presentation_overrides": snapshot.presentation_overrides,
                },
                "decision_set": decision_set,
                "execution_plan": {
                    "profile": "wx_full_rules-native-v2",
                    "modules": [{"module_id": module.module_id, "version": module.version} for module in execution_plan.modules],
                },
                "model_audit": model_findings,
                "saved_output_audit": saved_findings,
                "capability_migration": {
                    **capability_status,
                },
            })
            artifacts = {"docx": str(request.output_path)}
            for option_key, artifact_key, payload in (
                ("source_ast_path", "source_ast", snapshot.ast),
                ("normalized_ast_path", "normalized_ast", normalized),
            ):
                path = options.get(option_key)
                if path is not None and not options.get("release"):
                    path = Path(path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    artifacts[artifact_key] = str(path)
            report["artifacts"] = artifacts
            result = RunResult(status=status, exit_code=0, report=report, artifacts=artifacts, diagnostics=diagnostics, metrics=metrics.snapshot())
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
                diagnostics=[{"code": "MODULAR_NATIVE_CONVERSION_FAILED", "message": str(exc), "type": type(exc).__name__}],
                metrics=metrics.snapshot(),
            )
        finally:
            store.close()

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
