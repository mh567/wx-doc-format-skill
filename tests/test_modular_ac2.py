from __future__ import annotations

from types import SimpleNamespace
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.semantic_modules import ModuleExecutionContext
from wxdf.registry import ModuleDescriptor, ModuleRegistry, PlanError, RegisteredModule


CONFIG = {"pipeline": "modular", "list_policy": "wx-default-v2"}


def test_plan_rejects_capability_version_mismatch() -> None:
    provider = ModuleDescriptor(
        "provider",
        "1.0",
        ("source",),
        (),
        ("candidate_set",),
        provides_versions={"candidate_set": "2.0"},
    )
    consumer = ModuleDescriptor(
        "consumer",
        "1.0",
        ("relation",),
        ("candidate_set",),
        (),
        requires_versions={"candidate_set": "1.0"},
    )

    with pytest.raises(PlanError, match="capability version mismatch"):
        ModuleRegistry([
            RegisteredModule(provider, lambda _context: None),
            RegisteredModule(consumer, lambda _context: None),
        ]).build(CONFIG)


def test_plan_rejects_missing_dependency() -> None:
    with pytest.raises(PlanError, match="missing dependency"):
        ModuleRegistry([
            RegisteredModule(
                ModuleDescriptor("consumer", "1.0", ("relation",), ("missing",), ()),
                lambda _context: None,
            ),
        ]).build(CONFIG)


def test_plan_rejects_circular_dependency() -> None:
    modules = [
        RegisteredModule(
            ModuleDescriptor("a", "1.0", ("relation",), ("b",), ("a",)),
            lambda _context: None,
        ),
        RegisteredModule(
            ModuleDescriptor("b", "1.0", ("relation",), ("a",), ("b",)),
            lambda _context: None,
        ),
    ]

    with pytest.raises(PlanError, match="circular dependency"):
        ModuleRegistry(modules).build(CONFIG)


def test_plan_orders_same_stage_modules_deterministically() -> None:
    def module(module_id: str) -> RegisteredModule:
        return RegisteredModule(
            ModuleDescriptor(module_id, "1.0", ("relation",), (), (module_id,)),
            lambda _context: None,
        )

    forward = ModuleRegistry([module("zeta"), module("alpha")]).build(CONFIG)
    reverse = ModuleRegistry([module("alpha"), module("zeta")]).build(CONFIG)

    assert [item.module_id for item in forward.modules] == ["alpha", "zeta"]
    assert [item.module_id for item in reverse.modules] == ["alpha", "zeta"]


@pytest.mark.parametrize("operation", ["read", "write"])
def test_runtime_rejects_undeclared_capability_access(operation: str) -> None:
    def execute(context: ModuleExecutionContext) -> None:
        if operation == "read":
            context.require("seeded")
        else:
            context.provide("undeclared", {})

    module = RegisteredModule(
        ModuleDescriptor("runtime", "1.0", ("relation",), (), ()),
        execute,
    )
    registry = ModuleRegistry([module])
    plan = registry.build(CONFIG, initial_capabilities=("seeded",))
    context = ModuleExecutionContext(
        request=SimpleNamespace(options={}, input_path=ROOT / "input" / "test_baseline.md"),
        artifacts={"seeded": {}},
    )

    with pytest.raises(PlanError, match="undeclared"):
        registry.execute(plan, context)


def test_runtime_rejects_illegal_capability_payload() -> None:
    module = RegisteredModule(
        ModuleDescriptor("runtime", "1.0", ("relation",), (), ("decision_set",)),
        lambda context: context.provide("decision_set", "invalid"),
    )
    registry = ModuleRegistry([module])
    plan = registry.build(CONFIG)
    context = ModuleExecutionContext(
        request=SimpleNamespace(options={}, input_path=ROOT / "input" / "test_baseline.md"),
        artifacts={},
    )

    with pytest.raises(PlanError, match="invalid capability payload"):
        registry.execute(plan, context)


def test_runtime_rejects_undeclared_service_and_option_access() -> None:
    def execute(context: ModuleExecutionContext) -> None:
        context.services.get("secret_service")
        context.request.options.get("secret_option")

    module = RegisteredModule(
        ModuleDescriptor("runtime", "1.0", ("relation",), (), ()),
        execute,
    )
    registry = ModuleRegistry([module])
    plan = registry.build(CONFIG)
    context = ModuleExecutionContext(
        request=SimpleNamespace(
            options={"secret_option": "value"},
            input_path=ROOT / "input" / "test_baseline.md",
        ),
        artifacts={},
        services={"secret_service": object()},
    )

    with pytest.raises(PlanError, match="undeclared"):
        registry.execute(plan, context)
