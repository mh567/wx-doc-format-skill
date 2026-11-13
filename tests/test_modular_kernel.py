from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import zipfile
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


def test_registry_builds_selected_child_plan_from_seeded_capabilities() -> None:
    registry = ModuleRegistry([
        RegisteredModule(
            ModuleDescriptor("seed-consumer", "1.0", ("relation",), ("source_ast",), ("decision_set",)),
            lambda _context: None,
        ),
    ])

    plan = registry.build(
        {"pipeline": "modular", "list_policy": "wx-default-v2"},
        module_ids=("seed-consumer",),
        initial_capabilities=("source_ast",),
        required_capabilities=("source_ast", "decision_set"),
    )

    assert plan.initial_capabilities == ("source_ast",)
    assert [module.module_id for module in plan.modules] == ["seed-consumer"]


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
        "message": "enhancement requires an explicitly injected adapter",
    }]
    assert result.report["enhancement"]["status"] == "missing_adapter"
    assert not output.exists()


def test_kernel_staging_paths_are_unique_and_independently_cleaned(tmp_path: Path) -> None:
    from wxdf.kernel import _staging_paths

    output = tmp_path / "output.docx"
    first_staging, first_candidate = _staging_paths(output)
    second_staging, second_candidate = _staging_paths(output)

    assert first_staging != second_staging
    assert first_candidate == first_staging.with_name(first_staging.name + ".review-candidate")
    assert second_candidate == second_staging.with_name(second_staging.name + ".review-candidate")
    assert first_staging.name.startswith(f".{output.name}.staging-")
    # A bare 32-hex token would be mistaken for a publication transaction.
    assert not re.fullmatch(r"[0-9a-f]{32}", first_staging.name.split(".staging-", 1)[1])

    first_staging.write_bytes(b"first")
    second_staging.write_bytes(b"second")
    first_staging.unlink()
    assert not first_staging.exists()
    assert second_staging.read_bytes() == b"second"


def test_two_processes_can_write_the_same_output_without_leftover_staging(tmp_path: Path) -> None:
    """Two concurrent CLI runs share one output path and leave no staging files."""

    source = tmp_path / "input.md"
    source.write_text("# 文档\n\n## 章节\n\n正文内容。\n", encoding="utf-8")
    output = tmp_path / "output.docx"
    commands = [
        [
            sys.executable,
            str(ROOT / "scripts" / "main.py"),
            "--input", str(source),
            "--output", str(output),
            "--template", str(ROOT / "assets" / "wx_template.docx"),
            "--report", str(tmp_path / f"report-{index}.json"),
        ]
        for index in range(2)
    ]

    first = subprocess.Popen(commands[0], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(commands[1], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_stdout, first_stderr = first.communicate(timeout=30)
    second_stdout, second_stderr = second.communicate(timeout=30)

    assert first.returncode == 0, first_stderr or first_stdout
    assert second.returncode == 0, second_stderr or second_stdout
    assert output.is_file()
    assert not list(tmp_path.glob(".*.staging*"))
    with zipfile.ZipFile(output) as archive:
        assert "word/document.xml" in archive.namelist()


def test_recovery_waits_for_an_active_publication_lock(tmp_path: Path) -> None:
    from wxdf.publication import recover_pending_publications_locked
    from wxdf.publication_lock import publication_lock

    target = tmp_path / "output.docx"
    transaction_id = "a" * 32
    staged = tmp_path / f".{target.name}.staging-{transaction_id}"
    backup = tmp_path / f".{target.name}.rollback-{transaction_id}"
    journal_path = tmp_path / f".wxdf-publication-{transaction_id}.journal"
    target.write_bytes(b"new content")
    backup.write_bytes(b"old content")
    journal_path.write_text(json.dumps({
        "schema_version": "1.0",
        "transaction_id": transaction_id,
        "state": "prepared",
        "entries": [{
            "target": str(target.resolve()),
            "staged": str(staged.resolve()),
            "backup": str(backup.resolve()),
            "had_original": True,
        }],
        "journal_paths": [str(journal_path.resolve())],
    }), encoding="utf-8")
    marker = tmp_path / "locked"
    script = f"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'scripts')!r})
from wxdf.publication_lock import publication_lock
target, journal_path, backup, marker = (Path(value) for value in sys.argv[1:5])
with publication_lock([target]):
    marker.write_text("locked", encoding="utf-8")
    time.sleep(0.8)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["state"] = "committed"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    backup.unlink(missing_ok=True)
    journal_path.unlink(missing_ok=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(target), str(journal_path), str(backup), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), child.stderr.read() if child.poll() is not None else "lock marker missing"

        started = time.monotonic()
        recovered = recover_pending_publications_locked(target)
        elapsed = time.monotonic() - started
    finally:
        child.communicate(timeout=10)

    assert recovered["status"] == "clean"
    assert target.read_bytes() == b"new content"
    assert elapsed >= 0.5
    assert not journal_path.exists()
    lock_file = tmp_path / ".wxdf-publication.lock"
    assert lock_file.is_file()
    assert lock_file.stat().st_mode & 0o777 == 0o600


def test_two_outputs_in_one_directory_publish_concurrently(tmp_path: Path) -> None:
    source = tmp_path / "input.md"
    source.write_text("# 文档\n\n## 章节\n\n正文内容。\n", encoding="utf-8")
    template = ROOT / "assets" / "wx_template.docx"
    worker = tmp_path / "worker.py"
    worker.write_text(f"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'scripts')!r})
from wxdf import publication
from wxdf.kernel import ConversionKernel, ConversionRequest

source, output, report, template, marker, delay = sys.argv[1:7]
output_path = Path(output)
report_path = Path(report)
marker_path = Path(marker)
real_replace = publication.os.replace

def delayed_replace(src, dst):
    result = real_replace(src, dst)
    if Path(src).name.startswith(f".{{output_path.name}}.staging-") and Path(dst).resolve() == output_path.resolve():
        marker_path.write_text("staged", encoding="utf-8")
        time.sleep(float(delay))
    return result

publication.os.replace = delayed_replace
result = ConversionKernel().convert(ConversionRequest(
    input_path=Path(source),
    output_path=output_path,
    template_path=Path(template),
    pipeline="modular",
    list_policy="wx-default-v2",
    options={{"report_path": report_path}},
))
print(json.dumps(result.diagnostics, ensure_ascii=False))
raise SystemExit(result.exit_code)
""", encoding="utf-8")

    first_output = tmp_path / "first.docx"
    second_output = tmp_path / "second.docx"
    first_report = tmp_path / "first-report.json"
    second_report = tmp_path / "second-report.json"
    first_marker = tmp_path / "first-staged"
    first = subprocess.Popen(
        [sys.executable, str(worker), str(source), str(first_output), str(first_report), str(template), str(first_marker), "1.0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not first_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert first_marker.exists(), first.stderr.read() if first.poll() is not None else "first run did not stage"

        second = subprocess.Popen(
            [sys.executable, str(worker), str(source), str(second_output), str(second_report), str(template), str(tmp_path / "second-staged"), "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second_stdout, second_stderr = second.communicate(timeout=30)
        first_stdout, first_stderr = first.communicate(timeout=30)
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate(timeout=10)

    assert first.returncode == 0, first_stderr or first_stdout
    assert second.returncode == 0, second_stderr or second_stdout
    for output in (first_output, second_output):
        with zipfile.ZipFile(output) as archive:
            assert "word/document.xml" in archive.namelist()
    for report in (first_report, second_report):
        assert json.loads(report.read_text(encoding="utf-8"))["status"] == "completed"
    assert not list(tmp_path.glob(".wxdf-publication-*.journal"))
    assert not list(tmp_path.glob(".*.staging-*"))
