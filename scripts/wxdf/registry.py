"""Fixed-stage module registration and preflight checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

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
        results: list[Any] = []
        for descriptor in plan.modules:
            module = self._modules.get(descriptor.module_id)
            if module is None:
                raise PlanError(f"module {descriptor.module_id} is not registered")
            results.append(module.execute(context))
        return results


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
