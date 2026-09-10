"""T01 focused contracts for the native module chain and the registry seam.

Every test here names the behaviour the T01 ticket moves into registered
Modules.  The file is written before the implementation so that each failure
states the missing contract instead of a fixture problem.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.builtin_modules import ModuleExecutionContext, native_semantic_modules
from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.metrics import MetricsCollector
from wxdf.registry import (
    NATIVE_CHAIN_CAPABILITIES,
    ModuleDescriptor,
    ModuleRegistry,
    PlanError,
    RegisteredModule,
    build_native_v2_registry,
)
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics
from wxdf.source_store import SourceStore


TEMPLATE = ROOT / "assets" / "wx_template.docx"
BENCHMARK_MARKDOWN = ROOT / "input" / "benchmark_report.md"

# T00 froze these semantic ownership identifiers.
FROZEN_SEMANTIC_MODULES = (
    "source-facts-parser",
    "source-region",
    "document-structure",
    "protected-content",
    "list-semantics",
    "table-semantics",
    "caption-semantics",
    "appendix-semantics",
)

# Capabilities the transitional forwarding adapter exposed.  A native run must
# not carry them.
SHALLOW_FORWARDING_CAPABILITIES = ("legacy_run_result", "legacy_source_ast")

_MODULAR_PLAN_CONFIG = {"pipeline": "modular", "list_policy": "wx-default-v2"}

# The native conversion profile requests its plan with a resolved source type
# and the two mandatory audits.
_NATIVE_PLAN_CONFIG = {
    "pipeline": "modular",
    "list_policy": "wx-default-v2",
    "source_type": "markdown",
    "available_audits": ("model_audit", "saved_output_audit"),
}


def _write_semantic_fixture(destination: Path) -> Path:
    destination.write_text(
        "\n".join(
            [
                "# 系统标题",
                "",
                "正文段落说明。",
                "",
                "1. 第一项",
                "2. 第二项",
                "",
                "| 项目 | 说明 |",
                "| --- | --- |",
                "| 信任评估 | 行为分析 |",
                "",
                "# 附录A 补充材料",
                "",
                "### A.1 术语表",
                "",
                "补充说明。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination


def _request(source: Path) -> ConversionRequest:
    return ConversionRequest(
        input_path=source,
        output_path=source.with_suffix(".out.docx"),
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"profile": "native_v2"},
    )


def _semantic_context(source: Path) -> ModuleExecutionContext:
    """Run only the owned semantic Modules over a small local fixture."""

    registry = build_native_v2_registry()
    plan = registry.build(dict(_NATIVE_PLAN_CONFIG))
    semantic_ids = {module.descriptor.module_id for module in native_semantic_modules()}
    semantic_ids.update({"native-source-parser"})
    modules = tuple(module for module in plan.modules if module.module_id in semantic_ids)
    context = ModuleExecutionContext(
        request=_request(source),
        services={"store": SourceStore(), "metrics": MetricsCollector()},
    )
    registry.execute(
        type(plan)(
            pipeline=plan.pipeline,
            list_policy=plan.list_policy,
            stages=plan.stages,
            modules=modules,
        ),
        context,
    )
    return context


def _two_module_registry(*, first_policy: str = "fail", fallback=None) -> ModuleRegistry:
    def explode(_context: object) -> None:
        raise RuntimeError("module exploded")

    return ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor(
                    module_id="first",
                    version="1.0",
                    stages=("source",),
                    requires=(),
                    provides=("alpha",),
                    failure_policy=first_policy,
                ),
                execute=explode,
                fallback=fallback,
            ),
            RegisteredModule(
                descriptor=ModuleDescriptor(
                    module_id="second",
                    version="2.3",
                    stages=("role",),
                    requires=("alpha",),
                    provides=("beta",),
                ),
                execute=lambda context: context.provide("beta", "b"),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Context observation: produced and consumed capabilities at runtime.
# ---------------------------------------------------------------------------


def test_context_records_actual_produced_and_consumed_capabilities() -> None:
    registry = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("source",), (), ("alpha",)),
                execute=lambda context: context.provide("alpha", 1),
            ),
            RegisteredModule(
                descriptor=ModuleDescriptor("consumer", "1.0", ("scope",), ("alpha",), ("beta",)),
                execute=lambda context: context.provide("beta", context.require("alpha") + 1),
            ),
        ]
    )
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))
    context = ModuleExecutionContext(request=None)

    registry.execute(plan, context)

    assert context.evidence["producer"] == {
        "order": 0,
        "produced": ["alpha"],
        "consumed": [],
        "status": "completed",
    }
    assert context.evidence["consumer"] == {
        "order": 1,
        "produced": ["beta"],
        "consumed": ["alpha"],
        "status": "completed",
    }


def test_context_observes_only_capabilities_read_through_require() -> None:
    registry = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("source",), (), ("alpha",)),
                execute=lambda context: context.provide("alpha", 1),
            ),
            RegisteredModule(
                descriptor=ModuleDescriptor("reader", "1.0", ("scope",), ("alpha",), ("beta",)),
                execute=lambda context: (
                    context.require("alpha"),
                    context.require("alpha"),
                    context.provide("beta", context.artifacts["alpha"]),
                ),
            ),
        ]
    )
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))
    context = ModuleExecutionContext(request=None)

    registry.execute(plan, context)

    assert context.evidence["reader"]["consumed"] == ["alpha"]
    assert context.evidence["reader"]["produced"] == ["beta"]


def test_context_does_not_attribute_reads_made_outside_a_module() -> None:
    registry = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("source",), (), ("alpha",)),
                execute=lambda context: context.provide("alpha", "value"),
            )
        ]
    )
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))
    context = ModuleExecutionContext(request=None)

    registry.execute(plan, context)
    assert context.require("alpha") == "value"

    assert context.evidence["producer"]["consumed"] == []


# ---------------------------------------------------------------------------
# Registry runtime contract enforcement.
# ---------------------------------------------------------------------------


def test_registry_rejects_a_module_that_reads_an_undeclared_capability() -> None:
    registry = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("provider", "1.0", ("source",), (), ("beta",)),
                execute=lambda context: context.provide("beta", 2),
            ),
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("scope",), (), ("alpha", "gamma")),
                execute=lambda context: (
                    context.provide("alpha", 1),
                    context.provide("gamma", context.require("beta")),
                ),
            ),
        ]
    )
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))

    with pytest.raises(PlanError, match="undeclared capability beta"):
        registry.execute(plan, ModuleExecutionContext(request=None))


def test_registry_rejects_a_module_that_reads_an_unavailable_capability() -> None:
    registry = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("source",), (), ("alpha",)),
                execute=lambda context: context.provide("alpha", 1),
            ),
            RegisteredModule(
                descriptor=ModuleDescriptor("reader", "1.0", ("scope",), ("alpha",), ("beta",)),
                execute=lambda context: context.provide("beta", context.require("missing")),
            ),
        ]
    )
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))

    with pytest.raises(PlanError):
        registry.execute(plan, ModuleExecutionContext(request=None))


def test_registry_rejects_a_module_that_provides_an_undeclared_capability() -> None:
    registry = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("source",), (), ("alpha",)),
                execute=lambda context: context.provide("surprise", 1),
            )
        ]
    )
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))

    with pytest.raises(PlanError, match="undeclared capability surprise"):
        registry.execute(plan, ModuleExecutionContext(request=None))


def test_registry_rejects_successful_completion_without_a_declared_output() -> None:
    registry = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("source",), (), ("alpha", "beta")),
                execute=lambda context: context.provide("alpha", 1),
            )
        ]
    )
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))

    with pytest.raises(PlanError, match="did not produce beta"):
        registry.execute(plan, ModuleExecutionContext(request=None))


def test_registry_rejects_a_module_missing_from_the_registry() -> None:
    plan = ModuleRegistry(
        [
            RegisteredModule(
                descriptor=ModuleDescriptor("producer", "1.0", ("source",), (), ("alpha",)),
                execute=lambda context: context.provide("alpha", 1),
            )
        ]
    ).build(dict(_MODULAR_PLAN_CONFIG))
    registry = ModuleRegistry()

    with pytest.raises(PlanError, match="producer is not registered"):
        registry.execute(plan, ModuleExecutionContext(request=None))


def test_registry_preserves_execution_order_and_module_versions() -> None:
    registry = _two_module_registry()
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))
    context = ModuleExecutionContext(request=None)

    with pytest.raises(RuntimeError):
        registry.execute(plan, context)

    assert [module.version for module in plan.modules] == ["1.0", "2.3"]
    assert context.evidence["first"]["order"] == 0
    assert context.evidence["first"]["status"] == "failed"
    assert context.evidence["first"]["error"]["type"] == "RuntimeError"


def test_registry_fail_policy_propagates_and_stops_the_plan() -> None:
    registry = _two_module_registry(first_policy="fail")
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))

    with pytest.raises(RuntimeError, match="module exploded"):
        registry.execute(plan, ModuleExecutionContext(request=None))


def test_registry_warn_policy_records_and_continues() -> None:
    registry = _two_module_registry(first_policy="warn")
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))
    context = ModuleExecutionContext(request=None)

    registry.execute(plan, context)

    assert context.evidence["first"]["status"] == "warned"
    assert context.evidence["second"]["status"] == "completed"
    assert context.require("beta") == "b"


def test_registry_skip_policy_records_and_continues() -> None:
    registry = _two_module_registry(first_policy="skip")
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))
    context = ModuleExecutionContext(request=None)

    registry.execute(plan, context)

    assert context.evidence["first"]["status"] == "skipped"
    assert context.evidence["second"]["status"] == "completed"


def test_registry_fallback_policy_runs_the_registered_fallback() -> None:
    def fallback(context: ModuleExecutionContext) -> None:
        context.provide("alpha", "from-fallback")

    registry = _two_module_registry(first_policy="fallback", fallback=fallback)
    plan = registry.build(dict(_MODULAR_PLAN_CONFIG))
    context = ModuleExecutionContext(request=None)

    registry.execute(plan, context)

    assert context.evidence["first"]["status"] == "completed"
    assert context.require("alpha") == "from-fallback"


# ---------------------------------------------------------------------------
# Semantic Module ownership: each Module owns its own artifacts.
# ---------------------------------------------------------------------------


def test_frozen_semantic_module_identifiers_are_registered() -> None:
    registered = {module.descriptor.module_id for module in native_semantic_modules()}

    assert set(FROZEN_SEMANTIC_MODULES) <= registered


def test_native_registry_composes_the_semantic_modules_with_the_stage_chain() -> None:
    plan = build_native_v2_registry().build(dict(_NATIVE_PLAN_CONFIG))
    module_ids = [module.module_id for module in plan.modules]

    for module_id in (*FROZEN_SEMANTIC_MODULES, "native-source-parser"):
        assert module_id in module_ids
    assert module_ids.index("source-facts-parser") < module_ids.index("native-source-parser")
    assert module_ids.index("native-source-parser") < module_ids.index("list-semantics")
    assert module_ids.index("list-semantics") < module_ids.index("native-candidate-set")
    assert module_ids.index("appendix-semantics") < module_ids.index("native-candidate-set")
    assert module_ids.index("native-candidate-set") < module_ids.index("native-resolver")
    assert module_ids.index("native-resolver") < module_ids.index("native-transaction")
    assert module_ids.index("native-transaction") < module_ids.index("native-normalizer")
    assert not [module_id for module_id in module_ids if module_id.startswith("legacy")]


def test_composed_native_chain_only_exchanges_declared_capabilities() -> None:
    plan = build_native_v2_registry().build(dict(_NATIVE_PLAN_CONFIG))
    declared = {capability for module in plan.modules for capability in module.provides}

    assert declared <= set(NATIVE_CHAIN_CAPABILITIES)
    for module in plan.modules:
        assert set(module.requires) <= declared, module.module_id


def test_default_factory_returns_one_full_sixteen_module_chain() -> None:
    """The default call builds the only native chain: no shallow eight module shape."""

    default_modules = [
        module.module_id for module in build_native_v2_registry().build(dict(_NATIVE_PLAN_CONFIG)).modules
    ]
    injected_modules = [
        module.module_id
        for module in build_native_v2_registry(semantic_modules=native_semantic_modules())
        .build(dict(_NATIVE_PLAN_CONFIG))
        .modules
    ]

    assert default_modules == injected_modules
    assert default_modules == [
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
        "native-resolver",
        "native-transaction",
        "native-normalizer",
        "native-renderer",
        "native-auditor",
        "native-publisher",
    ]
    assert len(default_modules) == 16


def test_no_registered_module_forwards_precomputed_legacy_values() -> None:
    plan = build_native_v2_registry().build(dict(_NATIVE_PLAN_CONFIG))

    for module in plan.modules:
        assert not module.module_id.startswith("legacy")
        assert not set(module.provides) & set(SHALLOW_FORWARDING_CAPABILITIES)


def test_source_facts_parser_module_owns_parsing(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "owned.md")
    context = _semantic_context(source)

    facts = context.require("source_facts")

    assert facts["facts_schema_version"] == "1.0"
    assert context.require("source_facts_summary")["unit_count"] > 0
    assert context.require("source_facts_digest").startswith("sha256:")


def test_source_region_module_owns_region_resolution(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "regions.md")
    context = _semantic_context(source)

    assert context.require("source_regions")
    assert isinstance(context.require("source_region_candidates"), list)


def test_document_structure_module_owns_structure_candidates(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "structure.md")
    context = _semantic_context(source)

    candidates = context.require("document_structure_candidates")
    roles = {
        candidate["proposal"]["value"]["role"]
        for candidate in candidates
        if candidate["proposal"].get("value")
    }

    assert candidates
    assert "title" in roles or "heading" in roles


def test_protected_content_module_owns_protected_candidates(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "protected.md")
    context = _semantic_context(source)

    assert isinstance(context.require("protected_content_candidates"), list)


def test_native_source_ast_module_owns_ast_construction(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "ast.md")
    context = _semantic_context(source)

    ast = context.require("source_ast")

    assert ast["schema_version"] == "2.0"
    assert ast["document"]["blocks"]


def test_list_semantics_module_owns_list_candidate_materialization(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "lists.md")
    context = _semantic_context(source)

    candidates = context.require("list_semantics_candidates")
    operations = {candidate["proposal"]["operation"] for candidate in candidates}
    item_map = context.require("list_item_map")
    list_result = context.require("list_semantics_result")

    assert list_result["lists"]
    assert candidates
    assert {candidate["module_id"] for candidate in candidates} == {"list_semantics"}
    assert operations
    assert set(item_map) == {str(relation["item_id"]) for relation in list_result["items"]}


def test_table_semantics_module_owns_table_candidates(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "tables.md")
    context = _semantic_context(source)

    candidates = context.require("table_semantics_candidates")

    assert candidates
    assert {candidate["module_id"] for candidate in candidates} == {"table_semantics"}


def test_caption_and_appendix_modules_own_their_candidates(tmp_path: Path) -> None:
    source = _write_semantic_fixture(tmp_path / "appendices.md")
    context = _semantic_context(source)

    assert context.require("appendix_semantics_result")["candidates"]
    assert context.require("appendix_semantics_candidates")
    assert isinstance(context.require("caption_semantics_candidates"), list)
    assert context.require("caption_semantics_result")["captions"] is not None


def test_source_ast_builder_and_list_materializer_are_reusable(tmp_path: Path) -> None:
    from wxdf.parsers import parse
    from wxdf.semantics.lists import analyze_lists

    source = _write_semantic_fixture(tmp_path / "reuse.md")
    facts = parse(source, {"source_store": SourceStore()})

    ast = build_source_ast(facts)
    list_result = analyze_lists(facts)
    materialized, candidates, item_map = materialize_list_semantics(ast, facts, list_result)

    assert materialized == ast
    assert candidates
    assert isinstance(item_map, dict)


# ---------------------------------------------------------------------------
# Real native conversion: ownership is observable in the report.
# ---------------------------------------------------------------------------


def _native_result(tmp_path: Path, source: Path) -> dict:
    result = ConversionKernel().convert(
        ConversionRequest(
            input_path=source,
            output_path=tmp_path / "native-output.docx",
            template_path=TEMPLATE,
            pipeline="modular",
            list_policy="wx-default-v2",
            options={
                "profile": "native_v2",
                "report_path": tmp_path / "native-report.json",
                "source_ast_path": tmp_path / "native-source-ast.json",
                "normalized_ast_path": tmp_path / "native-normalized-ast.json",
            },
        )
    )
    assert result.status in {"completed", "completed_with_warnings"}, result.diagnostics
    return result.report


def test_native_report_observes_semantic_modules_consuming_each_others_outputs(tmp_path: Path) -> None:
    report = _native_result(tmp_path, BENCHMARK_MARKDOWN)
    evidence = report["native_module_evidence"]

    aggregator = evidence["native-candidate-set"]
    assert aggregator["consumed"], aggregator
    assert {
        "document_structure_candidates",
        "protected_content_candidates",
        "list_semantics_candidates",
        "table_semantics_candidates",
        "caption_semantics_candidates",
        "appendix_semantics_candidates",
    } <= set(aggregator["consumed"])

    assert evidence["source-facts-parser"]["produced"] == [
        "source_facts",
        "source_facts_summary",
        "source_facts_digest",
    ]
    assert evidence["native-source-parser"]["consumed"] == ["source_facts"]


def test_native_report_execution_plan_lists_every_executed_module(tmp_path: Path) -> None:
    report = _native_result(tmp_path, BENCHMARK_MARKDOWN)
    planned = [module["module_id"] for module in report["execution_plan"]["modules"]]
    evidence = report["native_module_evidence"]

    assert planned == list(evidence)
    assert len(planned) == 16
    for module_id in (*FROZEN_SEMANTIC_MODULES, "protected-content"):
        assert module_id in planned
    assert [
        record["order"] for record in evidence.values()
    ] == list(range(len(planned)))


def test_native_report_does_not_forward_precomputed_legacy_capabilities(tmp_path: Path) -> None:
    report = _native_result(tmp_path, BENCHMARK_MARKDOWN)

    produced = {
        capability
        for record in report["native_module_evidence"].values()
        for capability in record["produced"]
    }

    assert not produced & set(SHALLOW_FORWARDING_CAPABILITIES)
    assert report["execution_plan"]["profile"] in {"wx-default-v2", "wx_full_rules-native-v2"}
    assert report.get("migration_adapter") is None
    assert report["status"] in {"completed", "completed_with_warnings"}
    saved = json.loads((tmp_path / "native-source-ast.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == "2.0"
