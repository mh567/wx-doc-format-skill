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
                    "role": unit.get("role") or "body",
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
                    "raw_text": str(unit.get("raw_text") or ""),
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
        """Build list structure operations without changing the source revision."""

        ast = copy.deepcopy(dict(source_ast))
        document = ast["document"]
        blocks = document["blocks"]
        containers = document["containers"]
        children = document["children"]
        nodes = {**blocks, **containers}
        original_parents = {
            child_id: parent_id
            for parent_id, child_ids in children.items()
            for child_id in child_ids
        }
        original_positions = {
            (parent_id, child_id): index
            for parent_id, child_ids in children.items()
            for index, child_id in enumerate(child_ids)
        }
        list_specs = {str(item["list_id"]): item for item in list_result.get("lists", [])}
        item_records: dict[str, list[Mapping[str, Any]]] = {}
        for relation in list_result.get("items", []):
            item_records.setdefault(str(relation["list_id"]), []).append(relation)

        item_map: dict[str, str] = {}
        for relation in list_result.get("items", []):
            source_item_id = str(relation["item_id"])
            source_node = containers.get(source_item_id)
            if isinstance(source_node, Mapping) and source_node.get("kind") == "list_item":
                item_map[source_item_id] = source_item_id
                continue
            item_id = f"native-list-item-{source_item_id}"
            while item_id in nodes or item_id in item_map.values():
                item_id = f"{item_id}-copy"
            item_map[source_item_id] = item_id

        def source_node(source_id: str) -> Mapping[str, Any]:
            return facts.get("units", {}).get(source_id) or facts.get("containers", {}).get(source_id) or {}

        def source_ref(source_id: str) -> dict[str, Any] | None:
            value = source_node(source_id).get("source_ref")
            return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None

        def parent_for_list(list_id: str, spec: Mapping[str, Any], records: list[Mapping[str, Any]]) -> tuple[str, int]:
            parent_item_id = spec.get("parent_item_id")
            if parent_item_id is not None:
                parent_id = item_map.get(str(parent_item_id), str(parent_item_id))
                return parent_id, len(children.get(parent_id, []))
            first_source_id = str(records[0]["item_id"]) if records else ""
            parent_id = original_parents.get(first_source_id, document["root_id"])
            if parent_id in item_map:
                parent_id = item_map[parent_id]
            return parent_id, original_positions.get((parent_id, first_source_id), len(children.get(parent_id, [])))

        candidates: list[dict[str, Any]] = []
        for list_id, spec in list_specs.items():
            records = item_records.get(list_id, [])
            if list_id not in containers:
                parent_id, index = parent_for_list(list_id, spec, records)
                first_source_id = str(records[0]["item_id"]) if records else ""
                list_node: dict[str, Any] = {
                    "kind": "list",
                    "list_kind": str(spec.get("list_kind") or "ordered"),
                    "source_start": max(1, int(spec.get("source_start") or 1)),
                    "source_sequence_id": str(spec.get("source_sequence_id") or list_id),
                }
                if isinstance(spec.get("group_boundary"), Mapping):
                    list_node["group_boundary"] = copy.deepcopy(dict(spec["group_boundary"]))
                ref = source_ref(first_source_id)
                if ref is not None:
                    list_node["source_ref"] = ref
                candidates.append({
                    "candidate_id": f"list-structure-{list_id}",
                    "module_id": "list_semantics",
                    "module_version": "1.0",
                    "target_source_refs": [ref] if ref is not None else [],
                    "target_node_ids": [list_id],
                    "proposal": {
                        "operation": "create_node",
                        "property_scope": ["children"],
                        "value": {"node_id": list_id, "parent_id": parent_id, "index": index, "node": list_node},
                    },
                    "positive_evidence": [{"kind": "structural", "source": "source_facts_list_structure"}],
                    "negative_evidence": [],
                    "scope": {"stages": ["relation"]},
                    "constraints": [],
                    "rationale": "list container derived from immutable source facts",
                })

            for index, relation in enumerate(records):
                source_item_id = str(relation["item_id"])
                item_id = item_map[source_item_id]
                existing_item = item_id in containers and containers[item_id].get("kind") == "list_item"
                if not existing_item:
                    unit = source_node(source_item_id)
                    evidence = facts.get("evidence", {}).get(source_item_id, {})
                    item_node: dict[str, Any] = {
                        "kind": "list_item",
                        "source_marker": relation.get("source_marker") or evidence.get("marker_text"),
                        "source_ordinal": relation.get("source_ordinal") or evidence.get("ordinal"),
                        "source_sequence_id": relation.get("source_sequence_id") or source_item_id,
                    }
                    if isinstance(unit.get("source_ref"), Mapping):
                        item_node["source_ref"] = copy.deepcopy(dict(unit["source_ref"]))
                    candidates.append({
                        "candidate_id": f"list-item-structure-{source_item_id}",
                        "module_id": "list_semantics",
                        "module_version": "1.0",
                        "target_source_refs": [source_ref(source_item_id)] if source_ref(source_item_id) else [],
                        "target_node_ids": [item_id],
                        "proposal": {
                            "operation": "create_node",
                            "property_scope": ["children"],
                            "value": {"node_id": item_id, "parent_id": list_id, "index": index, "node": item_node},
                        },
                        "positive_evidence": [{"kind": "structural", "source": "source_facts_list_item"}],
                        "negative_evidence": [],
                        "scope": {"stages": ["relation"]},
                        "constraints": [],
                        "rationale": "list item owner derived from immutable source facts",
                    })

        for candidate in list_result.get("candidates", []):
            mapped = copy.deepcopy(dict(candidate))
            target_ids = [str(node_id) for node_id in candidate.get("target_node_ids", [])]
            if len(target_ids) != 1:
                candidates.append(mapped)
                continue
            source_item_id = target_ids[0]
            item_id = item_map.get(source_item_id, source_item_id)
            proposal = dict(mapped.get("proposal") or {})
            value = dict(proposal.get("value") or {})
            list_id = str(value.get("list_id") or value.get("new_parent_id") or "")
            existing_item = item_id in containers and containers[item_id].get("kind") == "list_item"
            value["new_parent_id"] = list_id if existing_item else item_id
            value["list_id"] = list_id
            if value.get("parent_item_id") is not None:
                value["parent_item_id"] = item_map.get(str(value["parent_item_id"]), str(value["parent_item_id"]))
            value["index"] = next(
                (index for index, relation in enumerate(item_records.get(list_id, [])) if str(relation.get("item_id")) == source_item_id),
                0,
            ) if existing_item else 0
            proposal["value"] = value
            mapped["proposal"] = proposal
            mapped["target_node_ids"] = [source_item_id]
            candidates.append(mapped)

        existing_associations = {
            (str(association.get("kind")), str(association.get("source_id")), str(association.get("target_id")))
            for association in document.get("associations", [])
            if isinstance(association, Mapping)
        }
        continuation_tails: dict[str, str] = {}
        for continuation in list_result.get("continuations", []):
            source_item_id = str(continuation.get("owner_item_id"))
            source_id = continuation_tails.get(source_item_id, item_map.get(source_item_id, source_item_id))
            target_id = str(continuation.get("node_id"))
            association = {
                "kind": "sequence_continuation",
                "source_id": source_id,
                "target_id": target_id,
            }
            if (association["kind"], source_id, target_id) in existing_associations:
                continue
            target_ref = source_ref(target_id)
            candidates.append({
                "candidate_id": f"list-continuation-{source_id}-{target_id}",
                "module_id": "list_semantics",
                "module_version": "1.0",
                "target_source_refs": [target_ref] if target_ref is not None else [],
                "target_node_ids": [target_id],
                "proposal": {
                    "operation": "update_association",
                    "property_scope": ["association"],
                    "value": {"association": association},
                },
                "positive_evidence": [{"kind": "structural", "source": "source_facts_list_continuation"}],
                "negative_evidence": [],
                "scope": {"stages": ["relation"], "scope_id": continuation.get("scope_id")},
                "constraints": [{"kind": "preserve_list_owner", "owner_item_id": source_id}],
                "rationale": "continuation association derived from list item ownership",
            })
            continuation_tails[source_item_id] = target_id

        for relation in list_result.get("items", []):
            if not relation.get("generated_anchor_required"):
                continue
            source_item_id = str(relation.get("item_id"))
            item_id = item_map.get(source_item_id, source_item_id)
            anchor_id = f"generated-anchor-{item_id}"
            if anchor_id in nodes:
                continue
            anchor_reason = str(relation.get("anchor_reason") or "list_item_anchor")
            anchor_node = {
                "block_type": "paragraph",
                "role": "body",
                "inline_content": [],
                "generated_by": "list_semantics.marker_anchor",
                "generation_basis": [source_item_id, anchor_reason],
            }
            anchor_ref = source_ref(source_item_id)
            candidates.append({
                "candidate_id": f"list-anchor-{item_id}",
                "module_id": "list_semantics",
                "module_version": "1.0",
                "target_source_refs": [anchor_ref] if anchor_ref is not None else [],
                "target_node_ids": [anchor_id],
                "proposal": {
                    "operation": "create_node",
                    "property_scope": ["children"],
                    "value": {"node_id": anchor_id, "parent_id": item_id, "index": 0, "node": anchor_node},
                },
                "positive_evidence": [{"kind": "structural", "source": "source_facts_list_anchor"}],
                "negative_evidence": [],
                "scope": {"stages": ["relation"], "scope_id": relation.get("scope_id")},
                "constraints": [{"kind": "single_marker_anchor", "reason": anchor_reason}],
                "rationale": "generated marker anchor required by list semantics",
            })

        from .validation import validate_ast

        validate_ast(ast)
        return ast, candidates, item_map

    @staticmethod
    def _native_preflight(source_ast: Mapping[str, Any], source_type: str, list_policy: str, *, registry: Any = None) -> Any:
        """Validate the native module contract before candidate execution."""

        from .registry import build_native_v2_registry

        node_kinds = {
            str(node.get("block_type") or node.get("kind"))
            for collection in (source_ast["document"]["blocks"], source_ast["document"]["containers"])
            for node in collection.values()
            if node.get("block_type") or node.get("kind")
        }
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

    def _execute_native_stage_chain(
        self,
        request: ConversionRequest,
        facts: Mapping[str, Any],
        source_ast: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
        decision_set: Mapping[str, Any],
        list_result: Mapping[str, Any],
        appendix_semantics: Mapping[str, Any] | None,
        store: Any,
        metrics: Any,
        staging_path: Path,
    ) -> dict[str, Any]:
        """Execute the native stage implementations through the registered chain."""

        from .auditing import check_model, check_saved_output
        from .normalization import normalize
        from .registry import build_native_v2_registry
        from .rendering import render
        from .semantics.appendices import audit_native_appendix
        from .template import load_template_bundle
        from .transaction import apply_stage_batch, make_snapshot

        context: dict[str, Any] = {
            "facts": facts,
            "source_ast": source_ast,
            "candidates": candidates,
            "decision_set": decision_set,
            "list_result": list_result,
            "appendix_semantics": appendix_semantics,
            "request": request,
            "metrics": metrics,
            "store": store,
            "staging_path": staging_path,
        }

        def source_parser(runtime: dict[str, Any]) -> dict[str, Any]:
            runtime["artifacts"] = {"source_facts": runtime["facts"]}
            return runtime["facts"]

        def candidate_set(runtime: dict[str, Any]) -> list[Mapping[str, Any]]:
            runtime["artifacts"]["candidate_set"] = runtime["candidates"]
            return runtime["candidates"]

        def resolver(runtime: dict[str, Any]) -> Mapping[str, Any]:
            runtime["artifacts"]["decision_set"] = runtime["decision_set"]
            return runtime["decision_set"]

        def transaction(runtime: dict[str, Any]) -> Any:
            with metrics.stage("transaction"):
                committed = apply_stage_batch(
                    make_snapshot(runtime["source_ast"]),
                    runtime["decision_set"]["decisions"],
                    expected_revision=runtime["source_ast"]["revision"],
                )
            runtime["committed"] = committed
            runtime["artifacts"]["source_ast_revision"] = committed.snapshot.ast["revision"]
            return committed

        def normalizer(runtime: dict[str, Any]) -> Mapping[str, Any]:
            committed = runtime["committed"]
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
                    committed.snapshot.ast,
                    template,
                    strategy=request.list_policy,
                    overlay=committed.snapshot.overlays,
                    presentation_override=committed.snapshot.presentation_overrides,
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
            runtime["template"] = template
            runtime["normalized"] = normalized
            runtime["resources"] = resources
            runtime["artifacts"]["normalized"] = normalized
            return normalized

        def renderer(runtime: dict[str, Any]) -> Mapping[str, Any]:
            with metrics.stage("render"):
                if hasattr(runtime["template"], "path"):
                    result = render(runtime["normalized"], staging_path, template_bundle=runtime["template"], resources=runtime["resources"])
                else:
                    result = render(runtime["normalized"], staging_path, resources=runtime["resources"])
                metrics.record_render()
            runtime["render_result"] = result
            runtime["artifacts"]["render_plan"] = result["render_plan"]
            return result

        def auditor(runtime: dict[str, Any]) -> dict[str, Any]:
            with metrics.stage("audit"):
                model_findings = check_model(runtime["normalized"])
                saved_findings = check_saved_output(staging_path, runtime["normalized"], runtime["render_result"]["render_map"])
                appendix_findings = (
                    audit_native_appendix(runtime["normalized"]["source_ast"], runtime["appendix_semantics"])
                    if runtime.get("appendix_semantics") is not None
                    else {"passed": True, "issues": []}
                )
            runtime["model_findings"] = model_findings
            runtime["saved_findings"] = saved_findings
            runtime["appendix_findings"] = appendix_findings
            runtime["artifacts"]["model_audit"] = model_findings
            runtime["artifacts"]["saved_output_audit"] = saved_findings
            runtime["artifacts"]["appendix_audit"] = appendix_findings
            return {"model": model_findings, "saved": saved_findings, "appendix": appendix_findings}

        def publisher(runtime: dict[str, Any]) -> bool:
            audits_passed = (
                runtime["model_findings"].get("passed") is True
                and runtime["saved_findings"].get("passed") is True
                and runtime["appendix_findings"].get("passed") is True
            )
            fatal_diagnostics = [
                item for item in runtime["list_result"].get("diagnostics", [])
                if item.get("severity") in {"error", "warning"}
            ]
            if not audits_passed or fatal_diagnostics:
                return False
            staging_path.replace(request.output_path)
            runtime["artifacts"]["published_output"] = str(request.output_path)
            return True

        bindings = {
            "native-source-parser": source_parser,
            "native-candidate-set": candidate_set,
            "native-resolver": resolver,
            "native-transaction": transaction,
            "native-normalizer": normalizer,
            "native-renderer": renderer,
            "native-auditor": auditor,
            "native-publisher": publisher,
        }
        registry = build_native_v2_registry(bindings)
        execution_plan = self._native_preflight(
            source_ast,
            facts["source"]["source_type"],
            request.list_policy,
            registry=registry,
        )
        if self._native_should_block_publish(execution_plan, decision_set):
            return {"status": "blocked", "execution_plan": execution_plan}
        registry.execute(execution_plan, context)
        return {
            "status": "published" if context["artifacts"].get("published_output") else "failed",
            "execution_plan": execution_plan,
            "committed": context["committed"],
            "normalized": context["normalized"],
            "render_result": context["render_result"],
            "model_findings": context["model_findings"],
            "saved_findings": context["saved_findings"],
            "appendix_findings": context["appendix_findings"],
        }

    def _convert_modular_native_v2(self, request: ConversionRequest) -> RunResult:
        """Run the modular source, decision, transaction and render chain."""

        options = request.options or {}
        if not request.input_path.is_file():
            return RunResult(status="failed", exit_code=1, diagnostics=[{"code": "INPUT_NOT_FOUND", "message": str(request.input_path)}])

        from .metrics import MetricsCollector
        from .parsers import digest_source_facts, parse, summarize_source_facts
        from .decision_ledger import LedgerError, project_ledger, revision_decision
        from .resolver import resolve
        from .semantics.appendices import (
            audit_native_appendix,
            collect_native_appendix_candidates,
            collect_source_appendix_semantics,
            prepare_native_appendix_semantics,
        )
        from .semantics.captions import collect_caption_candidates
        from .semantics.lists import analyze_lists
        from .semantics.protected import collect_protected_candidates
        from .semantics.regions import resolve_source_regions
        from .semantics.structure import collect_structure_candidates
        from .semantics.tables import collect_source_table_semantics, collect_table_candidates
        from .source_store import SourceStore
        from .transaction import TransactionConflict

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
        staging_path = request.output_path.with_name(f".{request.output_path.name}.native-v2.staging")
        store = SourceStore()
        ledger_replay: dict[str, Any] | None = None
        try:
            with metrics.stage("source"):
                facts = parse(request.input_path, {"source_store": store})
                metrics.record_source_opened()
                source_summary = summarize_source_facts(facts)
                source_facts_digest = digest_source_facts(facts)
            with metrics.stage("scope"):
                regions = resolve_source_regions(request.input_path, facts)
            with metrics.stage("role"):
                structure_candidates = collect_structure_candidates(
                    facts,
                    regions["regions"],
                    protect_semantics=True,
                )
                protected_candidates = collect_protected_candidates(facts)
            with metrics.stage("relation"):
                list_result = analyze_lists(facts)
                source_ast = self._facts_to_ast(facts)
                source_ast, list_candidates, item_map = self._materialize_list_semantics(source_ast, facts, list_result)
                appendix_semantics = collect_source_appendix_semantics(facts)
                appendix_semantics = prepare_native_appendix_semantics(facts, source_ast, appendix_semantics, list_result)
                appendix_candidates = collect_native_appendix_candidates(facts, source_ast, appendix_semantics, list_result)
                table_source = collect_source_table_semantics(facts)
                table_candidates = collect_table_candidates(source_ast)
                caption_candidates = collect_caption_candidates(source_ast)

            candidates = [
                *regions.get("candidates", []),
                *structure_candidates,
                *protected_candidates,
                *list_candidates,
                *appendix_candidates,
                *table_candidates,
                *caption_candidates,
            ]
            supplied_ledger = options.get("decision_ledger")
            if supplied_ledger is not None:
                try:
                    if not isinstance(supplied_ledger, Mapping) or not isinstance(supplied_ledger.get("source_binding"), Mapping):
                        raise LedgerError(
                            "native decision ledger must carry a SourceFacts binding",
                            code="source_binding_missing",
                        )
                    baseline_revision = source_ast["revision"]
                    existing_node_ids = set(source_ast["document"]["blocks"]) | set(source_ast["document"]["containers"])
                    ledger_view = project_ledger(facts, supplied_ledger, existing_node_ids=existing_node_ids)
                    blocking_ledger_diagnostics = [
                        item
                        for item in ledger_view.get("diagnostics", [])
                        if item.get("code") in {"revision_precondition_failed", "revision_conflict"}
                    ]
                    if blocking_ledger_diagnostics:
                        report.update({
                            "status": "failed",
                            "decision_ledger_replay": {
                                "enabled": True,
                                "error_code": "replay_blocked",
                                "diagnostics": blocking_ledger_diagnostics,
                            },
                        })
                        return RunResult(
                            status="failed",
                            exit_code=1,
                            report=report,
                            diagnostics=[
                                {"code": "replay_blocked", "severity": "error", "message": "cannot apply invalidated or conflicted revisions"},
                                *blocking_ledger_diagnostics,
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
                for revision in ledger_view.get("active_revisions", []):
                    ledger_decision = revision_decision(revision, source_ast["revision"])
                    candidates.append({
                        "candidate_id": f"ledger-revision-{revision['revision_id']}",
                        "module_id": "decision_ledger",
                        "module_version": str(revision.get("resolver_version") or "1.0"),
                        "target_source_refs": copy.deepcopy(revision.get("target_source_refs") or []),
                        "target_node_ids": list(revision.get("target_node_ids") or []),
                        "proposal": {
                            "operation": ledger_decision["operation"],
                            "property_scope": list(revision.get("property_scope") or []),
                            "value": copy.deepcopy(ledger_decision.get("value") or {}),
                            "invalidates": list(ledger_decision.get("invalidates") or []),
                        },
                        "positive_evidence": [{"kind": "ledger_revision", "revision_id": revision["revision_id"]}],
                        "negative_evidence": [],
                        "scope": {"stages": list(revision.get("invalidates") or [])},
                        "constraints": [],
                        "rationale": "accepted revision restored from the decision ledger",
                    })
                decision_set = resolve(candidates, {"revision": source_ast["revision"]}, ledger_view)
                ledger_replay = {
                    "enabled": True,
                    "ledger_revision": supplied_ledger.get("revision"),
                    "active_revision_count": len(ledger_view.get("active_revisions") or []),
                    "baseline_ast_revision": baseline_revision,
                    "decision_count": len(decision_set["decisions"]),
                }
            else:
                decision_set = resolve(candidates, {"revision": source_ast["revision"]})
            native_run = self._execute_native_stage_chain(
                request,
                facts,
                source_ast,
                candidates,
                decision_set,
                list_result,
                appendix_semantics,
                store,
                metrics,
                staging_path,
            )
            execution_plan = native_run["execution_plan"]
            if native_run["status"] == "blocked":
                report.update({
                    "status": "failed",
                    "source_ast_revision": source_ast["revision"],
                    "decision_set": decision_set,
                    "decision_ledger_replay": ledger_replay,
                    "execution_plan": {
                        "profile": "wx_full_rules-native-v2",
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
            committed = native_run["committed"]
            source_facts_digest_after = digest_source_facts(facts)
            source_facts_immutable = source_facts_digest == source_facts_digest_after
            snapshot = committed.snapshot
            normalized = native_run["normalized"]
            render_result = native_run["render_result"]
            model_findings = native_run["model_findings"]
            saved_findings = native_run["saved_findings"]
            appendix_audit = native_run["appendix_findings"]
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
            if native_run["status"] != "published" or not audits_passed or blocking_diagnostics:
                report.update({
                    "status": "failed",
                    "source_ast_revision": snapshot.ast["revision"],
                    "source_facts_digest": source_facts_digest,
                    "source_facts_digest_after": source_facts_digest_after,
                    "source_facts_immutable": source_facts_immutable,
                    "decision_set": decision_set,
                    "decision_ledger_replay": ledger_replay,
                    "model_audit": model_findings,
                    "saved_output_audit": saved_findings,
                    "appendix_semantics": appendix_semantics,
                    "appendix_semantics_consumption": appendix_consumption,
                    "appendix_preservation_audit": appendix_audit,
                    "execution_plan": {
                        "profile": "wx_full_rules-native-v2",
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
                "source_facts_summary": source_summary,
                "source_facts_digest": source_facts_digest,
                "source_facts_digest_after": source_facts_digest_after,
                "source_facts_immutable": source_facts_immutable,
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
                "table_semantics": {"candidate_count": len(table_candidates), "source_count": len(table_source)},
                "appendix_semantics": appendix_semantics,
                "appendix_semantics_consumption": appendix_consumption,
                "appendix_preservation_audit": appendix_audit,
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
                "decision_ledger_replay": ledger_replay,
                "execution_plan": {
                    "profile": "wx_full_rules-native-v2",
                    "modules": self._native_execution_plan_report(execution_plan),
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
                        **(ledger_replay or {"enabled": True}),
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
