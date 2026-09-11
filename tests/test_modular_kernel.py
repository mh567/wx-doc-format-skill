from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.metrics import MetricsCollector
from wxdf.registry import ModuleDescriptor, ModuleRegistry, PlanError, RegisteredModule, build_native_v2_registry, build_plan


ROOT = Path(__file__).resolve().parents[1]


def test_plan_builder_defaults_to_native_identity() -> None:
    plan = build_plan({}, [])

    assert plan.pipeline == "modular"
    assert plan.list_policy == "wx-default-v2"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"pipeline": "legacy", "list_policy": "wx-default-v2"}, "unsupported pipeline 'legacy'"),
        ({"pipeline": "modular", "list_policy": "legacy"}, "unsupported list policy 'legacy'"),
    ],
)
def test_plan_builder_rejects_retired_runtime_identity(config: dict, message: str) -> None:
    with pytest.raises(PlanError, match=message):
        build_plan(config, [])


def test_plan_builder_rejects_missing_dependencies() -> None:
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
                "native-test-adapter",
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
            {"pipeline": "modular", "list_policy": "wx-default-v2"},
            required_capabilities=("wx_full_rules", "template_audit"),
        )

    plan = registry.build(
        {"pipeline": "modular", "list_policy": "wx-default-v2"},
        required_capabilities=("wx_full_rules", "saved_output_audit"),
    )
    results = registry.execute(plan, {"request_id": "run-1"})

    assert results == ["done"]
    assert calls == ["run-1"]


def test_native_registry_executes_bound_stage_implementations() -> None:
    config = {
        "pipeline": "modular",
        "list_policy": "wx-default-v2",
        "source_type": "markdown",
        "node_kinds": ["document"],
        "available_audits": ["model_audit", "saved_output_audit"],
    }
    calls: list[str] = []
    registered = [module.module_id for module in build_native_v2_registry().build(config).modules]
    bindings = {
        module_id: (lambda _context, module_id=module_id: calls.append(module_id) or module_id)
        for module_id in registered
    }
    registry = build_native_v2_registry(bindings)
    plan = registry.build(config)

    results = registry.execute(plan, {})

    assert results == calls
    assert calls == [module.module_id for module in plan.modules]
    assert calls[0] == "source-facts-parser"
    assert len(calls) == 17


def test_metrics_collect_stage_timings_and_counters_without_business_data() -> None:
    metrics = MetricsCollector()
    with metrics.stage("source"):
        metrics.increment("full_model_copy_count")
    metrics.increment("full_model_copy_count", 2)

    snapshot = metrics.snapshot()

    assert snapshot["counters"]["full_model_copy_count"] == 3
    assert snapshot["stage_seconds"]["source"] >= 0
    assert snapshot["stage_order"] == ["source"]


def test_cli_modular_selection_uses_native_v2_defaults(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    output = tmp_path / "output.docx"
    command = [
        sys.executable,
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
    assert saved_report["pipeline"] == "modular"
    assert saved_report["profile"] == "wx-default-v2"
    assert saved_report["list_policy"] == "wx-default-v2"
    assert saved_report["capability_migration"]["document_structure"] == "consumed"
    assert saved_report.get("migration_adapter") is None


def test_cli_native_v2_explicit_enhancement_off_ignores_retired_env_toggle(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    output = tmp_path / "output.docx"
    marker = tmp_path / "adapter-called"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('called', encoding='utf-8')\n",
        encoding="utf-8",
    )
    llm_command = " ".join(
        shlex.quote(value)
        for value in (sys.executable, str(probe), str(marker))
    )
    env = os.environ.copy()
    env["WX_DOC_LLM_ENHANCE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "main.py"),
            "--input", str(ROOT / "input" / "test_baseline.md"),
            "--output", str(output),
            "--template", str(ROOT / "assets" / "wx_template.docx"),
            "--pipeline", "modular",
            "--modular-profile", "native-v2",
            "--list-policy", "wx-default-v2",
            "--llm-enhance", "off",
            "--llm-command", llm_command,
            "--report", str(report),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
    saved_report = json.loads(report.read_text(encoding="utf-8"))
    assert saved_report["enhancement"]["status"] == "disabled"


def test_native_v2_enabled_enhancement_requires_programmatic_adapter_before_publish(tmp_path: Path) -> None:
    output = tmp_path / "output.docx"
    request = ConversionRequest(
        input_path=ROOT / "input" / "test_baseline.md",
        output_path=output,
        template_path=ROOT / "assets" / "wx_template.docx",
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"profile": "native_v2", "llm_enhance": "a"},
    )

    result = ConversionKernel().convert(request)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.diagnostics == [{
        "code": "NATIVE_ENHANCEMENT_ADAPTER_MISSING",
        "severity": "error",
        "message": "native-v2 enhancement requires an explicitly injected adapter",
    }]
    assert result.report["enhancement"]["status"] == "missing_adapter"
    assert not output.exists()
