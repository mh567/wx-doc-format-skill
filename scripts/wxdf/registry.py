"""Fixed-stage module registration and preflight checks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping

from .contracts import AST_BLOCK_TYPES, AST_CONTAINER_KINDS, SOURCE_TYPES, STAGES


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


@dataclass(frozen=True)
class ExecutionPlan:
    pipeline: str
    list_policy: str
    stages: tuple[str, ...]
    modules: tuple[ModuleDescriptor, ...]


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

    def build(
        self,
        config: dict,
        *,
        required_capabilities: Iterable[str] = (),
    ) -> ExecutionPlan:
        plan = build_plan(config, (module.descriptor for module in self._modules.values()))
        provided = {
            capability
            for descriptor in plan.modules
            for capability in descriptor.provides
        }
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


class _Reraised:
    """Sentinel telling ``execute`` to propagate the original Module error."""


_RERAISED = _Reraised()


# Capabilities the native conversion chain exchanges between Modules.
NATIVE_CHAIN_CAPABILITIES = (
    "source_facts",
    "source_facts_summary",
    "source_facts_digest",
    "source_regions",
    "source_region_candidates",
    "source_region",
    "document_structure_candidates",
    "document_structure",
    "protected_content_candidates",
    "protected_content",
    "source_ast",
    "list_semantics_result",
    "list_semantics_candidates",
    "list_item_map",
    "list_semantics",
    "table_semantics_result",
    "table_semantics_candidates",
    "table_semantics",
    "caption_semantics_result",
    "caption_semantics_candidates",
    "caption_semantics",
    "appendix_semantics_result",
    "appendix_semantics_candidates",
    "appendix_semantics",
    "candidate_set",
    "enhancement_result",
    "ledger_view",
    "ledger_replay",
    "decision_set",
    "transaction_commit",
    "source_ast_revision",
    "source_ast_hierarchy",
    "normalized",
    "template_bundle",
    "resources",
    "render_plan",
    "render_result",
    "model_audit",
    "saved_output_audit",
    "appendix_audit",
    "source_facts_integrity",
    "published_output",
)


def build_native_v2_registry(
    bindings: Mapping[str, Callable[[Any], Any]] | None = None,
    *,
    semantic_modules: Iterable[RegisteredModule] | None = None,
) -> ModuleRegistry:
    """Return the registered contract for the direct native-v2 conversion chain.

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

    from . import builtin_modules as native_implementation

    bindings = dict(bindings or {})
    semantic = (
        tuple(semantic_modules)
        if semantic_modules
        else native_implementation.native_semantic_modules()
    )
    stage_executors = native_implementation.native_stage_executors()
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
            ModuleDescriptor("native-source-parser", "2.0", ("source",), ("source_facts",), ("source_ast",), **common),
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
                **common,
            ),
            stage_executors["native-candidate-set"],
        ),
        RegisteredModule(
            ModuleDescriptor("native-resolver", "2.0", ("relation",), ("candidate_set", "source_ast", "ledger_view"), ("decision_set",), **common),
            stage_executors["native-resolver"],
        ),
        RegisteredModule(
            ModuleDescriptor("native-transaction", "2.0", ("relation",), ("source_ast", "decision_set"), ("transaction_commit", "source_ast_revision", "source_ast_hierarchy"), **common),
            stage_executors["native-transaction"],
        ),
        RegisteredModule(
            ModuleDescriptor("native-normalizer", "2.0", ("normalize",), ("source_facts", "transaction_commit", "decision_set"), ("normalized", "template_bundle", "resources"), **common),
            stage_executors["native-normalizer"],
        ),
        RegisteredModule(
            ModuleDescriptor("native-renderer", "2.0", ("render",), ("normalized", "template_bundle", "resources"), ("render_plan", "render_result"), **common),
            stage_executors["native-renderer"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-auditor",
                "2.0",
                ("audit",),
                ("source_facts", "source_facts_digest", "normalized", "render_result", "appendix_semantics"),
                ("model_audit", "saved_output_audit", "appendix_audit", "source_facts_integrity"),
                required_audits=("model_audit", "saved_output_audit"),
                **common,
            ),
            stage_executors["native-auditor"],
        ),
        RegisteredModule(
            ModuleDescriptor(
                "native-publisher",
                "2.0",
                ("publish",),
                ("model_audit", "saved_output_audit", "appendix_audit", "list_semantics_result"),
                ("published_output",),
                **common,
            ),
            stage_executors["native-publisher"],
        ),
    ])


def build_plan(config: dict, module_descriptors: Iterable[ModuleDescriptor]) -> ExecutionPlan:
    pipeline = config.get("pipeline", "legacy")
    list_policy = config.get("list_policy", "legacy")
    if pipeline not in {"legacy", "modular"}:
        raise PlanError(f"unsupported pipeline {pipeline!r}")
    if list_policy not in {"legacy", "wx-default-v2"}:
        raise PlanError(f"unsupported list policy {list_policy!r}")
    if pipeline == "legacy" and list_policy != "legacy":
        raise PlanError("legacy pipeline only accepts legacy list policy")

    modules = tuple(module_descriptors)
    module_ids: set[str] = set()
    providers: set[str] = set()
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
        overlap = providers & set(module.provides)
        if overlap:
            raise PlanError(f"duplicate capability provider {sorted(overlap)[0]}")
        providers.update(module.provides)

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
        missing = set(module.requires) - providers
        if missing:
            raise PlanError(f"missing dependency {sorted(missing)[0]} for module {module.module_id}")

    stage_index = {stage: index for index, stage in enumerate(STAGES)}
    ordered_modules = tuple(sorted(modules, key=lambda item: min(stage_index[stage] for stage in item.stages)))
    available: set[str] = set()
    for module in ordered_modules:
        unavailable = set(module.requires) - available
        if unavailable:
            capability = sorted(unavailable)[0]
            raise PlanError(
                f"dependency {capability} for module {module.module_id} is scheduled after provider"
            )
        available.update(module.provides)
    return ExecutionPlan(
        pipeline=pipeline,
        list_policy=list_policy,
        stages=STAGES,
        modules=ordered_modules,
    )
