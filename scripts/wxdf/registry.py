"""Fixed-stage module registration and preflight checks."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .contracts import (
    AST_BLOCK_TYPES,
    AST_CONTAINER_KINDS,
    CAPABILITY_CONTRACTS,
    SOURCE_TYPES,
    STAGES,
    capability_contract,
)


class PlanError(ValueError):
    """Raised when a pipeline plan cannot satisfy its declared contract."""


@dataclass(frozen=True)
class ModuleDescriptor:
    module_id: str
    version: str
    stages: tuple[str, ...]
    requires: tuple[str, ...]
    provides: tuple[str, ...]
    exclusive_group: str | None = None
    contract_version: str = "1.0"
    supported_source_types: tuple[str, ...] = ()
    supported_node_kinds: tuple[str, ...] = ()
    failure_policy: str = "fail"
    required_audits: tuple[str, ...] = ()
    requires_versions: Mapping[str, str] = field(default_factory=dict)
    provides_versions: Mapping[str, str] = field(default_factory=dict)
    runtime_services: tuple[str, ...] = ()
    option_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.module_id.strip():
            raise PlanError("module ID must not be empty")
        if not self.version.strip():
            raise PlanError(f"module {self.module_id} has no version")
        if not self.stages:
            raise PlanError(f"module {self.module_id} has no stage")
        unknown = set(self.stages) - set(STAGES)
        if unknown:
            raise PlanError(f"module {self.module_id} declares unknown stage {sorted(unknown)[0]}")
        if not self.contract_version.strip():
            raise PlanError(f"module {self.module_id} has no contract version")
        unknown_sources = set(self.supported_source_types) - set(SOURCE_TYPES)
        if unknown_sources:
            raise PlanError(
                f"module {self.module_id} declares unknown source type {sorted(unknown_sources)[0]}"
            )
        unknown_nodes = set(self.supported_node_kinds) - set(AST_BLOCK_TYPES) - set(AST_CONTAINER_KINDS)
        if unknown_nodes:
            raise PlanError(
                f"module {self.module_id} declares unknown node kind {sorted(unknown_nodes)[0]}"
            )
        if self.failure_policy not in {"fail", "warn", "skip", "fallback"}:
            raise PlanError(f"module {self.module_id} has unsupported failure policy {self.failure_policy!r}")
        if len(set(self.supported_source_types)) != len(self.supported_source_types):
            raise PlanError(f"module {self.module_id} repeats a supported source type")
        if len(set(self.supported_node_kinds)) != len(self.supported_node_kinds):
            raise PlanError(f"module {self.module_id} repeats a supported node kind")
        if len(set(self.required_audits)) != len(self.required_audits):
            raise PlanError(f"module {self.module_id} repeats a required audit")
        for label, configured, capabilities in (
            ("requires_versions", self.requires_versions, self.requires),
            ("provides_versions", self.provides_versions, self.provides),
        ):
            unknown = set(configured) - set(capabilities)
            if unknown:
                raise PlanError(
                    f"module {self.module_id} declares a version for undeclared capability {sorted(unknown)[0]}"
                )
            if any(not str(version).strip() for version in configured.values()):
                raise PlanError(f"module {self.module_id} has an empty {label} entry")
        if len(set(self.runtime_services)) != len(self.runtime_services):
            raise PlanError(f"module {self.module_id} repeats a runtime service")
        if len(set(self.option_keys)) != len(self.option_keys):
            raise PlanError(f"module {self.module_id} repeats an option key")

    def required_version(self, capability: str) -> str:
        return str(self.requires_versions.get(capability, capability_contract(capability).version))

    def provided_version(self, capability: str) -> str:
        return str(self.provides_versions.get(capability, capability_contract(capability).version))


@dataclass(frozen=True)
class ExecutionPlan:
    pipeline: str
    list_policy: str
    stages: tuple[str, ...]
    modules: tuple[ModuleDescriptor, ...]
    initial_capabilities: tuple[str, ...] = ()
    capability_contracts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RegisteredModule:
    """Bind a serializable descriptor to its in-process implementation."""

    descriptor: ModuleDescriptor
    execute: Callable[[Any], Any]
    fallback: Callable[[Any], Any] | None = None


class ModuleRegistry:
    """Explicit executable module registry used by ConversionKernel."""

    def __init__(self, modules: Iterable[RegisteredModule] = ()) -> None:
        self._modules: dict[str, RegisteredModule] = {}
        for module in modules:
            self.register(module)

    def register(self, module: RegisteredModule) -> None:
        module_id = module.descriptor.module_id
        if module_id in self._modules:
            raise PlanError(f"duplicate module {module_id}")
        self._modules[module_id] = module

    def implementation_module(self, module_id: str) -> str:
        """Return the implementation module for a registered Module.

        This is a small diagnostic seam used by architecture audits.  It keeps
        ownership checks on the registry interface instead of exposing the
        registry's private storage to callers.
        """

        module = self._modules.get(module_id)
        if module is None:
            raise PlanError(f"module {module_id} is not registered")
        return module.execute.__module__

    def build(
        self,
        config: dict,
        *,
        required_capabilities: Iterable[str] = (),
        module_ids: Iterable[str] | None = None,
        initial_capabilities: Iterable[str] = (),
        initial_capability_versions: Mapping[str, str] | None = None,
    ) -> ExecutionPlan:
        if module_ids is None:
            selected = tuple(self._modules.values())
        else:
            requested = tuple(str(module_id) for module_id in module_ids)
            unknown = sorted(set(requested) - set(self._modules))
            if unknown:
                raise PlanError(f"module {unknown[0]} is not registered")
            selected = tuple(self._modules[module_id] for module_id in requested)
        plan = build_plan(
            config,
            (module.descriptor for module in selected),
            initial_capabilities=initial_capabilities,
            initial_capability_versions=initial_capability_versions,
        )
        provided = {
            capability
            for descriptor in plan.modules
            for capability in descriptor.provides
        } | set(plan.initial_capabilities)
        missing = sorted(set(required_capabilities) - provided)
        if missing:
            raise PlanError(f"missing required capability {missing[0]}")
        return plan

    def execute(self, plan: ExecutionPlan, context: Any) -> list[Any]:
        """Dispatch the frozen plan and enforce every runtime capability contract.

        When the context is a capability bus, each Module execution is observed
        through ``require`` and ``provide`` and checked against its descriptor.
        Failures follow the descriptor ``failure_policy`` deterministically.
        """

        observes = hasattr(context, "begin_module") and hasattr(context, "end_module")
        results: list[Any] = []
        for position, descriptor in enumerate(plan.modules):
            module = self._modules.get(descriptor.module_id)
            if module is None:
                raise PlanError(f"module {descriptor.module_id} is not registered")
            if observes:
                context.begin_module(descriptor, position)
            try:
                result = module.execute(context)
            except Exception as exc:
                result = self._recover(module, descriptor, context, observes, exc)
                if result is _RERAISED:
                    raise
                results.append(result)
                continue
            if observes:
                observation = context.end_module("completed")
                self._enforce(descriptor, observation)
            results.append(result)
        return results

    def _recover(
        self,
        module: RegisteredModule,
        descriptor: ModuleDescriptor,
        context: Any,
        observes: bool,
        exc: Exception,
    ) -> Any:
        """Apply the descriptor failure policy to a raised Module error."""

        policy = descriptor.failure_policy
        if policy == "fallback" and module.fallback is not None:
            if observes:
                context.reset_observation()
            try:
                result = module.fallback(context)
            except Exception as fallback_exc:
                if observes:
                    context.end_module("failed", fallback_exc)
                raise fallback_exc
            if observes:
                self._enforce(descriptor, context.end_module("completed"))
            return result
        if observes:
            context.end_module("failed" if policy in {"fail", "fallback"} else "warned" if policy == "warn" else "skipped", exc)
        if policy in {"warn", "skip"}:
            return None
        return _RERAISED

    @staticmethod
    def _enforce(descriptor: ModuleDescriptor, observation: Mapping[str, Any]) -> None:
        """Reject a completed Module whose observed capabilities break its contract."""

        consumed = list(observation.get("consumed") or ())
        produced = list(observation.get("produced") or ())
        undeclared_reads = [name for name in consumed if name not in descriptor.requires]
        if undeclared_reads:
            raise PlanError(
                f"module {descriptor.module_id} read undeclared capability {undeclared_reads[0]}"
            )
        undeclared_writes = [name for name in produced if name not in descriptor.provides]
        if undeclared_writes:
            raise PlanError(
                f"module {descriptor.module_id} provided undeclared capability {undeclared_writes[0]}"
            )
        missing = [name for name in descriptor.provides if name not in produced]
        if missing:
            raise PlanError(
                f"module {descriptor.module_id} did not produce {missing[0]}"
            )
        services = list(observation.get("services") or ())
        undeclared_services = [
            name for name in services if name not in descriptor.runtime_services
        ]
        if undeclared_services:
            raise PlanError(
                f"module {descriptor.module_id} read undeclared service {undeclared_services[0]}"
            )
        options = list(observation.get("options") or ())
        undeclared_options = [name for name in options if name not in descriptor.option_keys]
        if undeclared_options:
            raise PlanError(
                f"module {descriptor.module_id} read undeclared option {undeclared_options[0]}"
            )


class _Reraised:
    """Sentinel telling ``execute`` to propagate the original Module error."""


_RERAISED = _Reraised()


# Capabilities the native conversion chain exchanges between Modules.
NATIVE_CHAIN_CAPABILITIES = tuple(CAPABILITY_CONTRACTS)

_DETERMINISTIC_MODULE_ORDER = {
    module_id: position
    for position, module_id in enumerate(
        (
            "source-facts-parser",
            "native-source-parser",
            "source-region",
            "document-structure",
            "protected-content",
            "list-semantics",
            "table-semantics",
            "caption-semantics",
            "appendix-semantics",
            "native-candidate-set",
            "native-review-candidate-set",
            "native-resolver",
            "native-transaction",
            "native-normalizer",
            "native-renderer",
            "native-auditor",
            "native-reviewer",
            "native-publisher",
        )
    )
}


def build_native_v2_registry(
    bindings: Mapping[str, Callable[[Any], Any]] | None = None,
    *,
    semantic_modules: Iterable[RegisteredModule] | None = None,
) -> ModuleRegistry:
    """Return the registered contract for the direct conversion chain.

    The factory has a single shape: the semantic Modules that own parsing,
    regions, structure, protected content, lists, tables, captions and
    appendices run first, then the native stage chain that builds the source
    AST, aggregates the CandidateSet, resolves, commits the transaction,
    normalizes, renders, audits and publishes.

    ``semantic_modules`` replaces the registered semantic set for contract
    tests.  A missing or empty value falls back to the registered default set,
    so the default call always returns the complete native chain.

    ``bindings`` replaces the in-process execution of any registered Module by
    its identifier.  Modules without a binding keep their registered
    implementation.
    """

    from . import native_stages, semantic_modules as semantic_implementation

    bindings = dict(bindings or {})
    semantic = (
        tuple(semantic_modules)
        if semantic_modules
        else semantic_implementation.native_semantic_modules()
    )
    stage_executors = native_stages.native_stage_executors()
    supported_nodes = tuple(sorted(set(AST_BLOCK_TYPES) | set(AST_CONTAINER_KINDS)))
    common = {
        "contract_version": "2.0",
        "supported_source_types": ("markdown", "docx"),
        "supported_node_kinds": supported_nodes,
        "failure_policy": "fail",
    }

    def bound(module: RegisteredModule) -> RegisteredModule:
        binding = bindings.get(module.descriptor.module_id)
        if binding is None:
            return module
        return replace(module, execute=binding)

    return ModuleRegistry(bound(module) for module in [
        *semantic,
        RegisteredModule(
            ModuleDescriptor(
                "native-source-parser",
                "2.0",
                ("source",),
                ("source_facts",),
                ("source_ast",),
                runtime_services=("metrics",),
                option_keys=("restored_source_ast",),
                **common,
            ),
            stage_executors["native-source-parser"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-candidate-set",
                "2.0",
                ("relation",),
                (
                    "source_facts",
                    "source_ast",
                    "source_region_candidates",
                    "document_structure_candidates",
                    "protected_content_candidates",
                    "list_semantics_candidates",
                    "appendix_semantics_candidates",
                    "table_semantics_candidates",
                    "caption_semantics_candidates",
                ),
                ("candidate_set", "ledger_view", "ledger_replay", "enhancement_result"),
                option_keys=(
                    "decision_ledger",
                    "checkpoint_resume_stage",
                    "restored_candidate_baseline_id",
                    "llm_enhance",
                    "enhancement_budget",
                    "enhancement_timeout_seconds",
                    "enhancement_call",
                    "enhancement_request_sink",
                    "protocol_mode",
                ),
                **common,
            ),
            stage_executors["native-candidate-set"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-resolver",
                "2.0",
                ("relation",),
                ("candidate_set", "source_ast", "ledger_view"),
                ("decision_set",),
                runtime_services=("execution_plan",),
                **common,
            ),
            stage_executors["native-resolver"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-transaction",
                "2.0",
                ("relation",),
                ("source_ast", "decision_set"),
                ("transaction_commit", "source_ast_revision", "source_ast_hierarchy"),
                runtime_services=("candidate_baseline", "metrics"),
                option_keys=("restored_transaction_snapshot",),
                **common,
            ),
            stage_executors["native-transaction"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-normalizer",
                "2.0",
                ("normalize",),
                ("source_facts", "transaction_commit", "decision_set"),
                ("normalized", "template_bundle", "resources"),
                runtime_services=("metrics", "store"),
                option_keys=("restored_template_bundle", "restored_resource_bytes"),
                **common,
            ),
            stage_executors["native-normalizer"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-renderer",
                "2.0",
                ("render",),
                ("normalized", "template_bundle", "resources"),
                ("render_plan", "render_result"),
                runtime_services=("metrics", "staging_path"),
                option_keys=("table_row_height_cm", "table_row_height_rule", "caption_numbering"),
                **common,
            ),
            stage_executors["native-renderer"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-auditor",
                "2.0",
                ("audit",),
                ("source_facts", "source_facts_digest", "normalized", "render_result", "appendix_semantics", "template_bundle"),
                ("model_audit", "saved_output_audit", "appendix_audit", "audit_findings", "source_facts_integrity"),
                required_audits=("model_audit", "saved_output_audit"),
                runtime_services=("metrics", "staging_path"),
                **common,
            ),
            stage_executors["native-auditor"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-reviewer",
                "2.0",
                ("audit",),
                (
                    "source_facts",
                    "source_facts_digest",
                    "transaction_commit",
                    "normalized",
                    "render_result",
                    "model_audit",
                    "saved_output_audit",
                    "appendix_audit",
                    "audit_findings",
                    "template_bundle",
                    "resources",
                    "appendix_semantics",
                    "enhancement_result",
                ),
                ("review_packet", "review_result", "acceptance"),
                required_audits=("model_audit", "saved_output_audit"),
                runtime_services=("module_registry", "staging_path"),
                option_keys=(
                    "llm_enhance",
                    "profile",
                    "enhancement_budget",
                    "enhancement_timeout_seconds",
                    "enhancement_call",
                    "enhancement_request_sink",
                    "protocol_mode",
                    "available_response_ids",
                ),
                **common,
            ),
            stage_executors["native-reviewer"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-publisher",
                "2.0",
                ("publish",),
                ("review_result", "acceptance", "list_semantics_result"),
                ("published_output",),
                **common,
            ),
            stage_executors["native-publisher"],
        ),
    ])


def build_plan(
    config: dict,
    module_descriptors: Iterable[ModuleDescriptor],
    *,
    initial_capabilities: Iterable[str] = (),
    initial_capability_versions: Mapping[str, str] | None = None,
) -> ExecutionPlan:
    pipeline = config.get("pipeline", "modular")
    list_policy = config.get("list_policy", "wx-default-v2")
    if pipeline != "modular":
        raise PlanError(f"unsupported pipeline {pipeline!r}")
    if list_policy != "wx-default-v2":
        raise PlanError(f"unsupported list policy {list_policy!r}")

    modules = tuple(module_descriptors)
    module_ids: set[str] = set()
    seeded = tuple(sorted({str(capability) for capability in initial_capabilities}))
    configured_seed_versions = {
        str(name): str(version)
        for name, version in (initial_capability_versions or {}).items()
    }
    unknown_seed_versions = set(configured_seed_versions) - set(seeded)
    if unknown_seed_versions:
        raise PlanError(
            f"initial capability version declared without capability {sorted(unknown_seed_versions)[0]}"
        )
    providers: dict[str, tuple[str | None, str]] = {
        capability: (
            None,
            configured_seed_versions.get(capability, capability_contract(capability).version),
        )
        for capability in seeded
    }
    exclusive_groups: dict[str, str] = {}
    for module in modules:
        if module.module_id in module_ids:
            raise PlanError(f"duplicate module {module.module_id}")
        module_ids.add(module.module_id)
        if module.exclusive_group:
            previous = exclusive_groups.get(module.exclusive_group)
            if previous is not None:
                raise PlanError(
                    f"mutually exclusive modules {previous} and {module.module_id} "
                    f"share group {module.exclusive_group}"
                )
            exclusive_groups[module.exclusive_group] = module.module_id
        overlap = set(providers) & set(module.provides)
        if overlap:
            raise PlanError(f"duplicate capability provider {sorted(overlap)[0]}")
        for capability in module.provides:
            providers[capability] = (module.module_id, module.provided_version(capability))

        source_type = config.get("source_type")
        if source_type and module.supported_source_types and source_type not in module.supported_source_types:
            raise PlanError(
                f"module {module.module_id} does not support source type {source_type}"
            )
        configured_nodes = set(config.get("node_kinds") or ())
        if configured_nodes and module.supported_node_kinds:
            unsupported_nodes = configured_nodes - set(module.supported_node_kinds)
            if unsupported_nodes:
                raise PlanError(
                    f"module {module.module_id} does not support node kind {sorted(unsupported_nodes)[0]}"
                )
        available_audits = set(config.get("available_audits") or ())
        if available_audits:
            missing_audits = set(module.required_audits) - available_audits
            if missing_audits:
                raise PlanError(
                    f"module {module.module_id} requires audit {sorted(missing_audits)[0]}"
                )

    for module in modules:
        missing = set(module.requires) - set(providers)
        if missing:
            raise PlanError(f"missing dependency {sorted(missing)[0]} for module {module.module_id}")
        for capability in module.requires:
            provided_by, provided_version = providers[capability]
            required_version = module.required_version(capability)
            if required_version != provided_version:
                raise PlanError(
                    f"capability version mismatch for {capability}: "
                    f"module {module.module_id} requires {required_version}, "
                    f"provider supplies {provided_version}"
                )

    stage_index = {stage: index for index, stage in enumerate(STAGES)}
    module_stage = {
        module.module_id: min(stage_index[stage] for stage in module.stages)
        for module in modules
    }
    providers_by_id = {
        capability: provider_id
        for capability, (provider_id, _version) in providers.items()
        if provider_id is not None
    }
    for module in modules:
        for capability in module.requires:
            provider_id = providers_by_id.get(capability)
            if provider_id is not None and module_stage[provider_id] > module_stage[module.module_id]:
                raise PlanError(
                    f"dependency {capability} for module {module.module_id} is scheduled after provider"
                )

    ordered: list[ModuleDescriptor] = []
    for stage_number, _stage_name in enumerate(STAGES):
        stage_modules = [module for module in modules if module_stage[module.module_id] == stage_number]
        if not stage_modules:
            continue
        remaining = {module.module_id: module for module in stage_modules}
        while remaining:
            ready = [
                module
                for module in remaining.values()
                if all(
                    providers_by_id.get(capability) not in remaining
                    for capability in module.requires
                )
            ]
            if not ready:
                cycle = sorted(remaining)[0]
                raise PlanError(f"circular dependency involving module {cycle}")
            for module in sorted(
                ready,
                key=lambda item: (
                    _DETERMINISTIC_MODULE_ORDER.get(item.module_id, len(_DETERMINISTIC_MODULE_ORDER)),
                    item.module_id,
                    item.version,
                ),
            ):
                ordered.append(module)
                del remaining[module.module_id]
    ordered_modules = tuple(ordered)
    return ExecutionPlan(
        pipeline=pipeline,
        list_policy=list_policy,
        stages=STAGES,
        modules=ordered_modules,
        initial_capabilities=seeded,
        capability_contracts=tuple(
            sorted(
                (capability, version)
                for capability, (_provider, version) in providers.items()
            )
        ),
    )


def native_preflight(
    source_ast: Mapping[str, Any] | None,
    source_type: str,
    list_policy: str,
    *,
    registry: Any = None,
) -> Any:
    """Validate the native module contract before module execution.

    ``source_ast`` is optional.  Without it the request omits the node kind
    contract because the chain builds that revision inside a registered Module.
    """

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


def execution_plan_report(execution_plan: Any) -> list[dict[str, Any]]:
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


def checkpoint_modules(execution_plan: Any) -> list[dict[str, Any]]:
    """Report the module identities a checkpoint binds for resume."""

    return [
        {
            "id": module.module_id,
            "version": module.version,
            "contract_version": module.contract_version,
        }
        for module in execution_plan.modules
    ]


def capability_status(
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


def capability_migration_report(
    candidates: list[Mapping[str, Any]],
    decision_set: Mapping[str, Any],
    committed: Any,
    audits_passed: bool,
) -> dict[str, str]:
    """Report the delivery state of every migrated native capability."""

    capability = {
        "source_region": capability_status("source_regions", candidates, decision_set, committed, audits_passed),
        "document_structure": capability_status("source_structure", candidates, decision_set, committed, audits_passed),
        "protected_content": capability_status("protected_content", candidates, decision_set, committed, audits_passed),
        "list_semantics": capability_status("list_semantics", candidates, decision_set, committed, audits_passed),
        "table_semantics": capability_status("table_semantics", candidates, decision_set, committed, audits_passed),
        "caption_semantics": capability_status("caption_semantics", candidates, decision_set, committed, audits_passed),
        "appendix_semantics": capability_status("appendix_semantics", candidates, decision_set, committed, audits_passed),
    }
    capability["source_parser"] = "consumed" if audits_passed else "consumed_mismatch"
    return capability
