from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel, ConversionRequest, RunResult
from wxdf.legacy_pipeline import LegacyPipeline
from wxdf.metrics import MetricsCollector
from wxdf.registry import ModuleDescriptor, ModuleRegistry, PlanError, RegisteredModule, build_plan


ROOT = Path(__file__).resolve().parents[1]


def test_plan_builder_rejects_unsupported_pipeline_policy_combinations() -> None:
    with pytest.raises(PlanError, match="legacy pipeline only accepts legacy"):
        build_plan({"pipeline": "legacy", "list_policy": "wx-default-v2"}, [])

    with pytest.raises(PlanError, match="missing dependency"):
        build_plan(
            {"pipeline": "modular", "list_policy": "wx-default-v2"},
            [ModuleDescriptor("lists", "1.0", ("role",), ("source_facts",), ("list_candidates",))],
        )


def test_plan_builder_freezes_stage_order_and_module_versions() -> None:
    descriptors = [
        ModuleDescriptor("source", "1.0", ("source",), (), ("source_facts",)),
        ModuleDescriptor("role", "1.0", ("role",), ("source_facts",), ("role_candidates",)),
    ]

    plan = build_plan({"pipeline": "modular", "list_policy": "wx-default-v2"}, descriptors)

    assert plan.pipeline == "modular"
    assert plan.list_policy == "wx-default-v2"
    assert plan.stages == ("source", "scope", "role", "relation", "normalize", "render", "audit", "publish")
    assert [module.module_id for module in plan.modules] == ["source", "role"]


def test_plan_builder_rejects_dependency_scheduled_after_consumer() -> None:
    descriptors = [
        ModuleDescriptor("consumer", "1.0", ("source",), ("role_candidates",), ("source_facts",)),
        ModuleDescriptor("provider", "1.0", ("role",), (), ("role_candidates",)),
    ]

    with pytest.raises(PlanError, match="scheduled after provider"):
        build_plan({"pipeline": "modular", "list_policy": "wx-default-v2"}, descriptors)


def test_executable_registry_rejects_missing_required_capability_and_runs_plan() -> None:
    calls: list[str] = []
    registry = ModuleRegistry([
        RegisteredModule(
            descriptor=ModuleDescriptor(
                "legacy-full-adapter",
                "1.0",
                ("source", "scope", "role", "relation", "normalize", "render", "audit", "publish"),
                (),
                ("wx_full_rules", "saved_output_audit"),
            ),
            execute=lambda context: calls.append(context["request_id"]) or "done",
        )
    ])

    with pytest.raises(PlanError, match="missing required capability template_audit"):
        registry.build(
            {"pipeline": "modular", "list_policy": "legacy"},
            required_capabilities=("wx_full_rules", "template_audit"),
        )

    plan = registry.build(
        {"pipeline": "modular", "list_policy": "legacy"},
        required_capabilities=("wx_full_rules", "saved_output_audit"),
    )
    results = registry.execute(plan, {"request_id": "run-1"})

    assert results == ["done"]
    assert calls == ["run-1"]


def test_kernel_rejects_modular_conversion_without_complete_capability_adapter(tmp_path: Path) -> None:
    request = ConversionRequest(
        input_path=ROOT / "input" / "test_baseline.md",
        output_path=tmp_path / "modular.docx",
        template_path=ROOT / "assets" / "wx_template.docx",
        pipeline="modular",
        list_policy="wx-default-v2",
    )

    result = ConversionKernel().convert(request)

    assert isinstance(result, RunResult)
    assert result.status == "unsupported"
    assert result.exit_code == 2
    assert result.diagnostics[0]["code"] == "MODULAR_PROFILE_INCOMPLETE"
    assert not request.output_path.exists()


def test_kernel_executes_native_source_region_and_structure_modules_before_adapter(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("目录\n\n# 正文标题\n\n正文内容\n", encoding="utf-8")
    output = tmp_path / "output.docx"
    legacy_source_ast = tmp_path / "legacy-source.json"
    adapter_calls: list[ConversionRequest] = []

    def adapter(request: ConversionRequest) -> RunResult:
        from wxdf.parsers import parse

        adapter_calls.append(request)
        assert request.options["_native_structure_context"]["source_facts"]["facts_schema_version"] == "1.0"
        assert request.options["_native_structure_context"]["candidates"]
        assert request.options["_native_region_context"]["excluded_source_positions"] == [1]
        output.write_bytes(b"adapter-output")
        facts = parse(source)
        ast = ConversionKernel._facts_to_ast(facts)
        toc_id = facts["children"]["root"][0]
        first_id = facts["children"]["root"][1]
        ast["document"]["blocks"].pop(toc_id)
        ast["document"]["children"]["root"].remove(toc_id)
        ast["document"]["blocks"][first_id]["role"] = "title"
        ast["document"]["blocks"][first_id].pop("level", None)
        legacy_source_ast.write_text(json.dumps(ast, ensure_ascii=False), encoding="utf-8")
        return RunResult(
            status="completed",
            exit_code=0,
            report={
                "structure_decision_consumption": {
                    "candidate_count": 2,
                    "applied_count": 2,
                    "unmatched_count": 0,
                    "ambiguous_count": 0,
                },
                "source_region_decision_consumption": {
                    "excluded_candidate_count": 1,
                    "removed_count": 1,
                    "unmatched_count": 0,
                    "ambiguous_count": 0,
                },
            },
            artifacts={"docx": str(output), "source_ast": str(legacy_source_ast)},
        )

    result = ConversionKernel(modular_runner=adapter).convert(
        ConversionRequest(
            input_path=source,
            output_path=output,
            pipeline="modular",
            list_policy="wx-default-v2",
        )
    )

    assert result.status == "completed", result.diagnostics
    assert adapter_calls
    assert [item["module_id"] for item in result.report["execution_plan"]["modules"]] == [
        "source-facts-parser",
        "source-region",
        "document-structure",
        "legacy-full-adapter",
        "source-parser-equivalence-audit",
        "semantic-equivalence-audit",
    ]
    native = result.report["native_module_evidence"]
    assert native["source-facts-parser"]["unit_count"] == 3
    assert native["source-region"]["excluded_node_count"] == 1
    assert native["document-structure"]["title_candidate_count"] == 1
    assert native["document-structure"]["heading_candidate_count"] == 0
    assert native["document-structure"]["body_candidate_count"] == 1
    assert result.report["semantic_equivalence_audit"]["status"] == "equivalent"
    assert result.report["capability_migration"]["source_region"] == "native_consumed"
    assert result.report["capability_migration"]["document_structure"] == "native_consumed"


def test_kernel_audits_source_parser_from_internal_adapter_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 文档标题\n\n正文内容\n", encoding="utf-8")
    output = tmp_path / "output.docx"

    def adapter(request: ConversionRequest) -> RunResult:
        from wxdf.parsers import parse

        facts = parse(source)
        ast = ConversionKernel._facts_to_ast(facts)
        title_id = facts["children"]["root"][0]
        ast["document"]["blocks"][title_id]["role"] = "title"
        ast["document"]["blocks"][title_id].pop("level", None)
        request.options["_adapter_artifacts"]["source_ast"] = ast
        output.write_bytes(b"adapter-output")
        return RunResult(
            status="completed",
            exit_code=0,
            report={
                "structure_decision_consumption": {
                    "candidate_count": 2,
                    "applied_count": 2,
                    "unmatched_count": 0,
                    "ambiguous_count": 0,
                },
                "source_region_decision_consumption": {
                    "excluded_candidate_count": 0,
                    "removed_count": 0,
                    "unmatched_count": 0,
                    "ambiguous_count": 0,
                },
            },
            artifacts={"docx": str(output)},
        )

    result = ConversionKernel(modular_runner=adapter).convert(
        ConversionRequest(
            input_path=source,
            output_path=output,
            pipeline="modular",
            list_policy="wx-default-v2",
        )
    )

    assert result.status == "completed", result.diagnostics
    assert result.report["source_parser_equivalence_audit"]["status"] == "equivalent"
    assert result.report["capability_migration"]["source_parser"] == "shadow_verified"
    assert "source_ast" not in result.artifacts


def test_legacy_pipeline_routes_to_stable_converter_by_suffix() -> None:
    calls: list[str] = []

    def markdown(*args, **kwargs):
        calls.append("markdown")
        return {"source": {}, "normalized": {}}

    def docx(*args, **kwargs):
        calls.append("docx")
        return {"source": {}, "normalized": {}}

    pipeline = LegacyPipeline(markdown_converter=markdown, docx_converter=docx)

    assert pipeline.convert(Path("input.md")) == {"source": {}, "normalized": {}}
    assert pipeline.convert(Path("input.docx")) == {"source": {}, "normalized": {}}
    assert calls == ["markdown", "docx"]


def test_metrics_collect_stage_timings_and_counters_without_business_data() -> None:
    metrics = MetricsCollector()
    with metrics.stage("source"):
        metrics.increment("full_model_copy_count")
    metrics.increment("full_model_copy_count", 2)

    snapshot = metrics.snapshot()

    assert snapshot["counters"]["full_model_copy_count"] == 3
    assert snapshot["stage_seconds"]["source"] >= 0
    assert snapshot["stage_order"] == ["source"]


def test_cli_modular_mode_has_explicit_preflight_failure(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    output = tmp_path / "output.docx"
    command = [
        str(ROOT / "scripts" / ".venv" / "bin" / "python3"),
        str(ROOT / "scripts" / "main.py"),
        "--input",
        str(ROOT / "input" / "test_baseline.md"),
        "--output",
        str(output),
        "--template",
        str(ROOT / "assets" / "wx_template.docx"),
        "--pipeline",
        "modular",
        "--list-policy",
        "wx-default-v2",
        "--report",
        str(report),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0
    assert output.exists()
    saved_report = json.loads(report.read_text(encoding="utf-8"))
    assert saved_report["status"] == "completed"
    assert saved_report["capability_migration"]["document_structure"] == "native_consumed"
    assert saved_report["source_parser_equivalence_audit"]["status"] in {"equivalent", "mismatch"}
