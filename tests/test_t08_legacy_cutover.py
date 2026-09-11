from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
CLI = ROOT / "scripts" / "main.py"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_ast import _ast
from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.resolver import resolve
from wxdf.transaction import TransactionConflict, apply_stage_batch, make_snapshot


RETIRED_RUNTIME_IMPORTS = (
    "docx_pipeline",
    "md_pipeline",
    "model_normalization",
    "template_finalizer",
    "docx_render",
    "word_model_renderer",
    "wxdf.legacy_pipeline",
    "wxdf.legacy_capability",
)


@pytest.mark.parametrize(
    ("option", "retired_value"),
    [
        ("--pipeline", "legacy"),
        ("--modular-profile", "legacy-full-v1"),
        ("--list-policy", "legacy"),
    ],
)
def test_release_cli_rejects_retired_execution_choices_at_argument_boundary(
    option: str,
    retired_value: str,
) -> None:
    environment = os.environ.copy()
    environment["WX_DOC_FORMAT_RELEASE"] = "1"
    completed = subprocess.run(
        [str(PYTHON), str(CLI), "--version", option, retired_value],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "LEGACY_MODE_UNAVAILABLE" in completed.stderr
    assert retired_value in completed.stderr


def test_development_cli_keeps_complete_legacy_diagnostic_tuple(tmp_path: Path) -> None:
    source = tmp_path / "legacy-diagnostic.md"
    source.write_text("# 诊断入口\n\n正文\n", encoding="utf-8")
    output = tmp_path / "legacy-diagnostic.docx"
    report = tmp_path / "legacy-diagnostic.json"
    environment = os.environ.copy()
    environment.pop("WX_DOC_FORMAT_RELEASE", None)
    environment.pop("WX_DOC_LLM_ENHANCE", None)

    completed = subprocess.run(
        [
            str(PYTHON), str(CLI),
            "--input", str(source),
            "--output", str(output),
            "--template", str(ROOT / "assets" / "wx_template.docx"),
            "--pipeline", "legacy",
            "--modular-profile", "legacy-full-v1",
            "--list-policy", "legacy",
            "--report", str(report),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    assert json.loads(report.read_text(encoding="utf-8")).get("pipeline") != "modular"


def test_kernel_default_request_uses_native_v2_only(tmp_path: Path) -> None:
    request = ConversionRequest(
        input_path=tmp_path / "missing.md",
        output_path=tmp_path / "output.docx",
    )

    assert request.pipeline == "modular"
    assert request.list_policy == "wx-default-v2"
    result = ConversionKernel().convert(request)
    assert result.status == "failed"
    assert result.diagnostics[0]["code"] == "INPUT_NOT_FOUND"


@pytest.mark.parametrize("retired_argument", ["legacy_runner", "modular_runner"])
def test_kernel_constructor_rejects_retired_runner_adapters(retired_argument: str) -> None:
    with pytest.raises(TypeError):
        ConversionKernel(**{retired_argument: lambda request: request})


def test_retired_adapter_seams_are_absent_from_runtime_sources() -> None:
    retired_symbols = {
        "scripts/main.py": ("_legacy_adapter_argv", "_run_legacy_full_adapter"),
        "scripts/wxdf/kernel.py": (
            "legacy_runner",
            "modular_runner",
            "def _convert_modular(",
            "legacy-full-v1",
        ),
        "scripts/wxdf/builtin_modules.py": (
            "legacy_full_adapter_module",
            "source_projection_module",
            "semantic_equivalence_module",
            "source_parser_equivalence_module",
            "experimental_native_modules",
        ),
    }

    for relative_path, symbols in retired_symbols.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for symbol in symbols:
            assert symbol not in source, f"{relative_path} still exposes {symbol}"


def test_retired_runtime_modules_are_deleted() -> None:
    for relative_path in (
        "scripts/wxdf/legacy_pipeline.py",
        "scripts/wxdf/legacy_capability.py",
    ):
        assert not (ROOT / relative_path).exists(), f"retired module remains: {relative_path}"


def test_importing_cli_does_not_attempt_retired_runtime_imports() -> None:
    child = "\n".join(
        [
            "import importlib.abc, json, sys",
            f"BLOCKED = {RETIRED_RUNTIME_IMPORTS!r}",
            "attempted = []",
            "class Recorder(importlib.abc.MetaPathFinder):",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname in BLOCKED:",
            "            attempted.append(fullname)",
            "        return None",
            "sys.meta_path.insert(0, Recorder())",
            "sys.path.insert(0, sys.argv[1])",
            "import main",
            "print(json.dumps(attempted))",
        ]
    )

    completed = subprocess.run(
        [str(PYTHON), "-c", child, str(SCRIPTS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def _run_cli_with_import_recorder(
    tmp_path: Path,
    label: str,
    arguments: list[str],
) -> dict:
    summary_path = tmp_path / f"{label}-imports.json"
    child = "\n".join(
        [
            "import importlib.abc, json, sys",
            f"BLOCKED = {RETIRED_RUNTIME_IMPORTS!r}",
            "attempted = []",
            "class Recorder(importlib.abc.MetaPathFinder):",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname in BLOCKED:",
            "            attempted.append(fullname)",
            "        return None",
            "sys.meta_path.insert(0, Recorder())",
            "sys.path.insert(0, sys.argv[1])",
            "import main",
            "try:",
            "    main.main(sys.argv[3:])",
            "    exit_code = 0",
            "except SystemExit as exc:",
            "    exit_code = exc.code if isinstance(exc.code, int) else 1",
            "summary = {'exit_code': exit_code, 'attempted': attempted, "
            "'loaded': [name for name in BLOCKED if name in sys.modules]}",
            "with open(sys.argv[2], 'w', encoding='utf-8') as handle:",
            "    json.dump(summary, handle)",
        ]
    )
    completed = subprocess.run(
        [str(PYTHON), "-c", child, str(SCRIPTS), str(summary_path), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert summary_path.is_file(), completed.stderr
    return json.loads(summary_path.read_text(encoding="utf-8"))


def test_native_cli_flows_do_not_import_retired_runtime_chain(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n正文\n", encoding="utf-8")
    template = ROOT / "assets" / "wx_template.docx"
    adapter = tmp_path / "empty-native-response.py"
    adapter.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({\n"
        "  'request_id': payload['request_id'],\n"
        "  'response_id': 'response-' + payload['request_id'],\n"
        "  'candidate_set_version': '2.0',\n"
        "  'base_revision': payload['model_revision'],\n"
        "  'view_sha256': payload['view_sha256'],\n"
        "  'candidates': [],\n"
        "}))\n",
        encoding="utf-8",
    )
    command = " ".join(shlex.quote(value) for value in (str(PYTHON), str(adapter)))
    request_dir = tmp_path / "requests"

    flows = [
        (
            "default",
            ["--input", str(source), "--output", str(tmp_path / "default.docx"), "--template", str(template)],
        ),
        (
            "command",
            [
                "--input", str(source),
                "--output", str(tmp_path / "command.docx"),
                "--template", str(template),
                "--llm-enhance", "force-a",
                "--llm-command", command,
            ],
        ),
        (
            "request",
            [
                "--input", str(source),
                "--template", str(template),
                "--llm-enhance", "force-a",
                "--generate-requests", str(request_dir),
            ],
        ),
    ]
    for label, arguments in flows:
        summary = _run_cli_with_import_recorder(tmp_path, label, arguments)
        assert summary == {"exit_code": 0, "attempted": [], "loaded": []}, label

    resumed = _run_cli_with_import_recorder(
        tmp_path,
        "resume",
        ["--resume", str(request_dir / "run.json")],
    )
    assert resumed == {"exit_code": 0, "attempted": [], "loaded": []}


@pytest.mark.parametrize("changed_field", ["content_sha256", "span"])
def test_native_candidate_rejects_changed_source_reference_without_mutation(
    changed_field: str,
) -> None:
    ast = _ast(1)
    source_ref = copy.deepcopy(ast["document"]["blocks"]["p0"]["source_ref"])
    candidate = {
        "candidate_id": "native-source-ref-guard",
        "module_id": "native-test",
        "module_version": "2.0",
        "target_source_refs": [source_ref],
        "target_node_ids": ["p0"],
        "proposal": {
            "operation": "update_node_semantics",
            "property_scope": ["semantics"],
            "value": {"semantic_role": "reviewed"},
        },
        "positive_evidence": [{"kind": "structural"}],
        "negative_evidence": [],
        "scope": {"stages": ["role"]},
        "constraints": [],
        "rationale": "exercise the native source reference guard",
        "confidence": 1.0,
    }
    decision = resolve([candidate], {"revision": ast["revision"]})["decisions"][0]
    changed = copy.deepcopy(ast)
    changed_ref = changed["document"]["blocks"]["p0"]["source_ref"]
    if changed_field == "content_sha256":
        changed_ref["content_sha256"] = "sha256:" + "f" * 64
    else:
        changed_ref["span"] = {"start": 10, "end": 11, "unit": "unicode_code_point"}
    snapshot = make_snapshot(changed)
    baseline = copy.deepcopy(snapshot.ast)

    with pytest.raises(TransactionConflict, match="source reference precondition failed"):
        apply_stage_batch(
            snapshot,
            [decision],
            expected_revision=changed["revision"],
        )

    assert snapshot.ast == baseline


@pytest.mark.parametrize(
    ("checkpoint", "expected_code"),
    [
        ({"protocol_version": "1.0", "args": {}}, "LEGACY_CHECKPOINT_UNSUPPORTED"),
        ({"checkpoint_schema_version": "9.0"}, "CHECKPOINT_SCHEMA_UNSUPPORTED"),
    ],
)
def test_retired_checkpoint_versions_are_rejected_with_stable_codes(
    tmp_path: Path,
    checkpoint: dict,
    expected_code: str,
) -> None:
    checkpoint_path = tmp_path / "retired-run.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    completed = subprocess.run(
        [str(PYTHON), str(CLI), "--resume", str(checkpoint_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert expected_code in completed.stderr


def test_unversioned_t04_transaction_proof_keeps_safe_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_modular_file_protocol import (
        test_terminal_resume_migrates_valid_legacy_t04_transaction_proofs,
    )

    monkeypatch.delenv("WX_DOC_FORMAT_RELEASE", raising=False)
    monkeypatch.delenv("WX_DOC_LLM_ENHANCE", raising=False)
    test_terminal_resume_migrates_valid_legacy_t04_transaction_proofs(tmp_path)
