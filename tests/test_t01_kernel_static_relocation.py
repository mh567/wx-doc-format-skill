"""T01 contracts: kernel translation statics live with their artifact owners.

The kernel keeps only lifecycle concerns (``convert``, the native conversion
implementation, exception routing and the publication trigger).  Every value
that used to be computed by a private kernel static is now owned by the module
that owns the artifact it describes.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

TEMPLATE = ROOT / "assets" / "wx_template.docx"

# Symbols the T01 ticket relocates or deletes.  None of them may remain on the
# kernel class, and the dead one must not reappear anywhere.
MIGRATED_SYMBOLS = (
    "_native_should_block_publish",
    "_facts_to_ast",
    "_materialize_list_semantics",
    "_native_source_type",
    "_native_preflight",
    "_native_execution_plan_report",
    "_native_capability_status",
    "_native_checkpoint_modules",
    "_native_ledger_replay_report",
    "_lock_native_checkpoint_identity",
    "_native_v2_run_result",
)


# ---------------------------------------------------------------------------
# Kernel keeps only lifecycle.
# ---------------------------------------------------------------------------


def test_kernel_class_no_longer_exposes_migrated_translation_statics() -> None:
    from wxdf.kernel import ConversionKernel

    for name in MIGRATED_SYMBOLS:
        assert not hasattr(ConversionKernel, name), name


def test_kernel_module_defines_only_lifecycle_entry_points() -> None:
    from wxdf import kernel

    source = inspect.getsource(kernel)
    for name in MIGRATED_SYMBOLS:
        assert f"def {name}" not in source, name

    # Only the two lifecycle methods remain on the kernel.
    methods = {
        name
        for name, value in inspect.getmembers(
            kernel.ConversionKernel, predicate=callable
        )
        if not name.startswith("__")
    }
    assert methods == {"convert", "_convert_modular_native_v2"}


# ---------------------------------------------------------------------------
# Each translation is testable on its artifact owner's interface.
# ---------------------------------------------------------------------------


def test_parsers_owns_source_type_detection() -> None:
    from wxdf.parsers import detect_source_type

    assert detect_source_type(Path("report.docx")) == "docx"
    assert detect_source_type(Path("report.DOCX")) == "docx"
    assert detect_source_type(Path("report.md")) == "markdown"
    assert detect_source_type("report") == "markdown"


def test_registry_owns_native_preflight() -> None:
    from wxdf.registry import native_preflight

    plan = native_preflight(None, "markdown", "wx-default-v2")

    assert plan.pipeline == "modular"
    assert plan.list_policy == "wx-default-v2"
    assert [module.module_id for module in plan.modules]


def test_registry_owns_execution_plan_report() -> None:
    from wxdf.registry import build_native_v2_registry, execution_plan_report

    plan = build_native_v2_registry().build(
        {"pipeline": "modular", "list_policy": "wx-default-v2"}
    )
    report = execution_plan_report(plan)

    assert [entry["module_id"] for entry in report] == [
        module.module_id for module in plan.modules
    ]
    for entry in report:
        assert set(entry) == {
            "module_id",
            "version",
            "contract_version",
            "failure_policy",
            "required_audits",
        }


def test_registry_owns_checkpoint_modules() -> None:
    from wxdf.registry import build_native_v2_registry, checkpoint_modules

    plan = build_native_v2_registry().build(
        {"pipeline": "modular", "list_policy": "wx-default-v2"}
    )
    modules = checkpoint_modules(plan)

    assert modules == [
        {
            "id": module.module_id,
            "version": module.version,
            "contract_version": module.contract_version,
        }
        for module in plan.modules
    ]


class _Committed:
    status = "committed"


def test_registry_owns_capability_status() -> None:
    from wxdf.registry import capability_status

    # No candidate mentions the module: nothing to deliver.
    assert capability_status("list_semantics", [], {}, _Committed(), True) == "delivered"

    candidates = [{"module_id": "list_semantics", "candidate_id": "list-relation-1"}]
    decision_set = {"decisions": [{"candidate_ids": ["list-relation-1"]}], "unresolved": []}
    assert (
        capability_status("list_semantics", candidates, decision_set, _Committed(), True)
        == "consumed"
    )
    assert (
        capability_status(
            "list_semantics",
            candidates,
            {"decisions": [], "unresolved": []},
            _Committed(),
            True,
        )
        == "partial"
    )


def test_registry_owns_capability_migration_report() -> None:
    from wxdf.registry import capability_migration_report

    report = capability_migration_report([], {"decisions": [], "unresolved": []}, _Committed(), True)

    assert report["source_parser"] == "consumed"
    assert set(report) == {
        "source_region",
        "document_structure",
        "protected_content",
        "list_semantics",
        "table_semantics",
        "caption_semantics",
        "appendix_semantics",
        "source_parser",
    }
    assert all(value == "delivered" for key, value in report.items() if key != "source_parser")


def test_run_result_owns_the_run_result_translation() -> None:
    from wxdf import run_result

    assert callable(run_result.build_native_run_result)


def test_decision_ledger_owns_ledger_replay_report() -> None:
    from wxdf.decision_ledger import ledger_replay_report

    assert ledger_replay_report(None) is None
    assert ledger_replay_report({"enabled": True}, {"decisions": [{}, {}]}) == {
        "enabled": True,
        "decision_count": 2,
    }
    assert ledger_replay_report({"enabled": True}) == {"enabled": True}


def test_checkpoint_store_owns_native_checkpoint_identity_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import checkpoint_store

    monkeypatch.setattr(checkpoint_store, "load_checkpoint", lambda _path: {})

    with pytest.raises(checkpoint_store.CheckpointError) as raised:
        checkpoint_store.lock_native_checkpoint_identity(Path("/tmp/unused.json"))

    assert raised.value.code == "checkpoint_configuration_missing"


def test_checkpoint_store_migrates_the_legacy_native_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import checkpoint_store

    checkpoint = {
        "configuration": {
            "pipeline": "modular",
            "list_policy": "wx-default-v2",
        },
        "list_policy": "wx-default-v2",
        "profile": "wx_full_rules-native-v2",
    }
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        checkpoint_store, "load_checkpoint", lambda _path: dict(checkpoint)
    )
    monkeypatch.setattr(
        checkpoint_store,
        "save_checkpoint",
        lambda value, path: saved.update(value=value, path=path),
    )

    migrated = checkpoint_store.lock_native_checkpoint_identity(Path("/tmp/cp.json"))

    assert migrated["profile"] == "wx-default-v2"
    assert saved["value"]["profile"] == "wx-default-v2"


def test_checkpoint_store_rejects_a_non_modular_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import checkpoint_store

    checkpoint = {
        "configuration": {
            "pipeline": "legacy",
            "list_policy": "wx-default-v2",
        },
        "list_policy": "wx-default-v2",
        "profile": "wx-default-v2",
    }
    monkeypatch.setattr(
        checkpoint_store, "load_checkpoint", lambda _path: dict(checkpoint)
    )

    with pytest.raises(checkpoint_store.CheckpointError) as raised:
        checkpoint_store.lock_native_checkpoint_identity(Path("/tmp/cp.json"))

    assert raised.value.code == "checkpoint_pipeline_mismatch"


# ---------------------------------------------------------------------------
# The relocated code keeps the public report contract.
# ---------------------------------------------------------------------------


def test_native_conversion_report_contract_is_unchanged(tmp_path: Path) -> None:
    from wxdf.kernel import ConversionKernel, ConversionRequest

    source = tmp_path / "relocation.md"
    source.write_text(
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
            ]
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    result = ConversionKernel().convert(
        ConversionRequest(
            input_path=source,
            output_path=tmp_path / "output.docx",
            template_path=TEMPLATE,
            pipeline="modular",
            list_policy="wx-default-v2",
            options={"report_path": report_path},
        )
    )

    assert result.status in {"completed", "completed_with_warnings"}, result.diagnostics
    assert result.exit_code == 0
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["profile"] == "wx-default-v2"
    assert saved["capability_migration"]["source_parser"] == "consumed"
    assert [entry["module_id"] for entry in saved["execution_plan"]["modules"]] == list(
        saved["native_module_evidence"]
    )
    assert saved["native_transaction"]["status"] in {"committed", "already_committed"}
