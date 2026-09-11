from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from docx import Document
from docx.enum.table import WD_ROW_HEIGHT_RULE


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.run_store import content_digest, save_checkpoint


PYTHON = SCRIPTS / ".venv" / "bin" / "python3"
MAIN = SCRIPTS / "main.py"
SOURCE = ROOT / "input" / "test_baseline.md"
TABLE_SOURCE = ROOT / "input" / "benchmark_report.md"
TEMPLATE = ROOT / "assets" / "wx_template.docx"


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("WX_DOC_FORMAT_RELEASE", None)
    environment.pop("WX_DOC_LLM_ENHANCE", None)
    return environment


def _run(*arguments: str | Path, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), str(MAIN), *(str(argument) for argument in arguments)],
        cwd=ROOT,
        env=environment or _environment(),
        capture_output=True,
        text=True,
    )


def _generate_checkpoint(tmp_path: Path) -> Path:
    request_dir = tmp_path / "requests"
    completed = _run(
        "--input", SOURCE,
        "--template", TEMPLATE,
        "--llm-enhance", "force-a",
        "--generate-requests", request_dir,
    )
    assert completed.returncode == 0, completed.stderr
    return request_dir / "run.json"


def test_default_cli_records_canonical_native_identity(tmp_path: Path) -> None:
    output = tmp_path / "default.docx"
    report_path = tmp_path / "report.json"

    completed = _run(
        "--input", SOURCE,
        "--output", output,
        "--template", TEMPLATE,
        "--report", report_path,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["pipeline"] == "modular"
    assert report["profile"] == "wx-default-v2"
    assert report["list_policy"] == "wx-default-v2"
    assert report.get("migration_adapter") is None


def test_default_cli_applies_explicit_table_row_contract_to_real_docx(tmp_path: Path) -> None:
    output = tmp_path / "custom-row-contract.docx"

    completed = _run(
        "--input", TABLE_SOURCE,
        "--output", output,
        "--template", TEMPLATE,
        "--table-row-height-cm", "2",
        "--table-row-height-rule", "exact",
    )

    assert completed.returncode == 0, completed.stderr
    rows = [row for table in Document(output).tables for row in table.rows]
    assert rows
    assert all(row.height is not None and row.height.cm == pytest.approx(2, abs=0.02) for row in rows)
    assert all(row.height_rule == WD_ROW_HEIGHT_RULE.EXACTLY for row in rows)


def test_default_cli_keeps_default_table_row_contract(tmp_path: Path) -> None:
    output = tmp_path / "default-row-contract.docx"
    report_path = tmp_path / "default-row-contract-report.json"

    completed = _run(
        "--input", TABLE_SOURCE,
        "--output", output,
        "--template", TEMPLATE,
        "--report", report_path,
    )

    assert completed.returncode == 0, completed.stderr
    rows = [row for table in Document(output).tables for row in table.rows]
    assert rows
    assert all(row.height is not None and row.height.cm == pytest.approx(0.69, abs=0.02) for row in rows)
    assert all(row.height_rule == WD_ROW_HEIGHT_RULE.AT_LEAST for row in rows)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["unexpected_styles"] == []


def test_help_names_native_defaults_and_legacy_diagnostic_mode() -> None:
    completed = _run("--help")

    assert completed.returncode == 0
    assert "default: modular" in completed.stdout
    assert "default: native-v2" in completed.stdout
    assert "default: wx-default-v2" in completed.stdout
    assert "diagnostic" in completed.stdout.lower()


def test_native_markdown_report_uses_native_identity_and_audit_results(tmp_path: Path) -> None:
    output = tmp_path / "native-report.docx"
    report_path = tmp_path / "native-report.json"
    markdown_path = tmp_path / "native-report.md"

    completed = _run(
        "--input", SOURCE,
        "--output", output,
        "--template", TEMPLATE,
        "--report", report_path,
        "--report-md", markdown_path,
    )

    assert completed.returncode == 0, completed.stderr
    text = markdown_path.read_text(encoding="utf-8")
    assert "- pipeline：modular" in text
    assert "- profile：wx-default-v2" in text
    assert "- list_policy：wx-default-v2" in text
    assert "- status：completed" in text
    assert "- model_audit：passed" in text
    assert "- saved_output_audit：passed" in text
    assert "- unexpected_styles：[]" in text
    assert "审计：failed" not in text


def test_default_file_mode_waiting_report_records_list_policy(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    report_path = tmp_path / "waiting-report.json"

    completed = _run(
        "--input", SOURCE,
        "--template", TEMPLATE,
        "--llm-enhance", "force-a",
        "--generate-requests", request_dir,
        "--report", report_path,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "waiting_external"
    assert report["pipeline"] == "modular"
    assert report["profile"] == "wx-default-v2"
    assert report["list_policy"] == "wx-default-v2"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ("--pipeline", "legacy", "--modular-profile", "native-v2", "--list-policy", "legacy"),
            "legacy diagnostic pipeline requires --modular-profile legacy-full-v1",
        ),
        (
            ("--pipeline", "legacy", "--modular-profile", "legacy-full-v1", "--list-policy", "wx-default-v2"),
            "legacy diagnostic pipeline requires --list-policy legacy",
        ),
        (
            ("--pipeline", "modular", "--modular-profile", "legacy-full-v1", "--list-policy", "wx-default-v2"),
            "modular pipeline requires --modular-profile native-v2",
        ),
        (
            ("--pipeline", "modular", "--modular-profile", "native-v2", "--list-policy", "legacy"),
            "modular pipeline requires --list-policy wx-default-v2",
        ),
    ],
)
def test_illegal_pipeline_identity_combinations_are_argparse_errors(
    arguments: tuple[str, ...],
    message: str,
) -> None:
    completed = _run(*arguments, "--input", SOURCE, "--output", "ignored.docx")

    assert completed.returncode == 2
    assert message in completed.stderr


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("profile", "legacy-full-v1", "checkpoint_profile_mismatch"),
        ("list_policy", "legacy", "checkpoint_list_policy_mismatch"),
    ],
)
def test_native_resume_rejects_checkpoint_identity_drift_before_response_recording(
    tmp_path: Path,
    field: str,
    value: str,
    error_code: str,
) -> None:
    checkpoint_path = _generate_checkpoint(tmp_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint[field] = value
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
    before = checkpoint_path.read_bytes()

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode != 0
    assert error_code in resumed.stderr
    assert checkpoint_path.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("pipeline", "legacy", "checkpoint_pipeline_mismatch"),
        ("modular_profile", "legacy-full-v1", "checkpoint_profile_mismatch"),
        ("list_policy", "legacy", "checkpoint_list_policy_mismatch"),
    ],
)
def test_native_resume_reports_checkpoint_configuration_identity_drift_in_kernel(
    tmp_path: Path,
    field: str,
    value: str,
    error_code: str,
) -> None:
    checkpoint_path = _generate_checkpoint(tmp_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["configuration"][field] = value
    checkpoint["resource_summary"]["fingerprints"]["configuration"] = content_digest(
        checkpoint["configuration"]
    )
    save_checkpoint(checkpoint, checkpoint_path)
    before = checkpoint_path.read_bytes()

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode == 1
    assert error_code in resumed.stderr
    assert checkpoint_path.read_bytes() == before


def test_native_resume_migrates_transitional_profile_literal_before_use(tmp_path: Path) -> None:
    checkpoint_path = _generate_checkpoint(tmp_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["profile"] = "wx_full_rules-native-v2"
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode == 0, resumed.stderr
    migrated = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert migrated["checkpoint_schema_version"] == "2.0"
    assert migrated["profile"] == "wx-default-v2"
    assert migrated["configuration"]["pipeline"] == "modular"
    assert migrated["configuration"]["modular_profile"] == "native-v2"
    assert migrated["list_policy"] == "wx-default-v2"


@pytest.mark.parametrize(
    ("checkpoint", "error_code"),
    [
        ({"protocol_version": "1.0", "args": {}}, "LEGACY_CHECKPOINT_UNSUPPORTED"),
        ({"checkpoint_schema_version": "9.0"}, "CHECKPOINT_SCHEMA_UNSUPPORTED"),
        ({"args": {}}, "CHECKPOINT_FORMAT_UNSUPPORTED"),
    ],
)
def test_resume_rejects_unsupported_checkpoint_versions_with_stable_code(
    tmp_path: Path,
    checkpoint: dict,
    error_code: str,
) -> None:
    checkpoint_path = tmp_path / "unsupported-run.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    resumed = _run("--resume", checkpoint_path)

    assert resumed.returncode == 2
    assert error_code in resumed.stderr


def test_default_cli_ignores_legacy_environment_and_runs_with_legacy_seams_blocked(
    tmp_path: Path,
) -> None:
    output = tmp_path / "blocked-default.docx"
    report_path = tmp_path / "blocked-default-report.json"
    child = "\n".join(
        [
            "import sys",
            "BLOCKED = ('docx_pipeline', 'md_pipeline', 'model_normalization', "
            "'template_finalizer', 'wxdf.legacy_pipeline', "
            "'wxdf.semantics.source_projection_bridge')",
            "class _Blocker:",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname in BLOCKED:",
            "            raise ImportError('WXDF-T06-BLOCKED:' + fullname)",
            "        return None",
            "sys.path.insert(0, sys.argv[1])",
            "sys.meta_path.insert(0, _Blocker())",
            "try:",
            "    import main",
            "    main.main(sys.argv[2:])",
            "except ImportError as exc:",
            "    if 'WXDF-T06-BLOCKED:' in str(exc):",
            "        sys.stderr.write(str(exc) + '\\n')",
            "        raise SystemExit(3)",
            "    raise",
        ]
    )
    environment = _environment()
    environment["WX_DOC_LLM_ENHANCE"] = "1"

    completed = subprocess.run(
        [
            str(PYTHON), "-c", child, str(ROOT / "scripts"),
            "--input", str(SOURCE),
            "--output", str(output),
            "--template", str(TEMPLATE),
            "--report", str(report_path),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["profile"] == "wx-default-v2"
    assert report["enhancement"]["status"] == "disabled"
    assert report.get("migration_adapter") is None


@pytest.mark.parametrize("enhancement_mode", ["force-a", "force-c"])
def test_online_and_review_modes_use_native_defaults_without_architecture_flags(
    tmp_path: Path,
    enhancement_mode: str,
) -> None:
    adapter = tmp_path / "empty-response.py"
    adapter.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "response = {\n"
        "    'request_id': payload['request_id'],\n"
        "    'response_id': 'response-' + payload['request_id'],\n"
        "    'candidate_set_version': '2.0',\n"
        "    'base_revision': payload['model_revision'],\n"
        "    'view_sha256': payload['view_sha256'],\n"
        "    'candidates': [],\n"
        "}\n"
        "print(json.dumps(response, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    command = " ".join(shlex.quote(value) for value in (str(PYTHON), str(adapter)))
    output = tmp_path / f"{enhancement_mode}.docx"
    report_path = tmp_path / f"{enhancement_mode}-report.json"

    completed = _run(
        "--input", SOURCE,
        "--output", output,
        "--template", TEMPLATE,
        "--llm-enhance", enhancement_mode,
        "--llm-command", command,
        "--report", report_path,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["pipeline"] == "modular"
    assert report["profile"] == "wx-default-v2"
    assert report["list_policy"] == "wx-default-v2"
    assert report.get("migration_adapter") is None


def test_default_file_mode_writes_canonical_native_checkpoint(tmp_path: Path) -> None:
    checkpoint_path = _generate_checkpoint(tmp_path)

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["checkpoint_schema_version"] == "2.0"
    assert checkpoint["profile"] == "wx-default-v2"
    assert checkpoint["configuration"]["pipeline"] == "modular"
    assert checkpoint["configuration"]["modular_profile"] == "native-v2"
    assert checkpoint["list_policy"] == "wx-default-v2"
