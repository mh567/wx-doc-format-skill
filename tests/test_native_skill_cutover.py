"""T00 red baseline for the native-v2 Skill cutover.

Every test in this file names the later execution ticket that makes it pass.
The file is written before any cutover implementation lands so that each
failure states the missing contract instead of a fixture problem.

Frozen literal contracts that no current public boundary names yet are
recorded in ``docs/execution-v4/evidence/T00.md``:

* ``native_module_evidence`` reuses the report key of the transitional
  modular adapter.  T01 fills it with one record per executed Module.
* ``profile`` and ``list_policy`` reuse existing report and CLI names.  T04
  reuses them as checkpoint identity fields.
* ``publish_bundle(..., acceptance=...)`` follows the existing
  ``publish_bundle`` signature.  T05 adds the acceptance gate.
* ``manifest["platforms"][platform_id]["status"]`` follows the existing
  ``available_platforms`` / ``unavailable_platforms`` manifest shape.  T07
  adds the per-platform status values.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.parsers import parse
from wxdf.publication import PublicationError, publish_bundle
from wxdf.review import accept_candidate, evaluate_candidate, mode_manifest
from wxdf.validation import validate_checkpoint, validate_patch

TEMPLATE = ROOT / "assets" / "wx_template.docx"
BASELINE_MARKDOWN = ROOT / "input" / "test_baseline.md"
BENCHMARK_MARKDOWN = ROOT / "input" / "benchmark_report.md"

# T06 freezes ``wx-default-v2`` as the canonical native profile name.  Until
# T06 lands, the native report contract of the current kernel uses
# ``wx_full_rules-native-v2``.  Both literals are accepted so that T00 pins
# the contract rather than one transitional string.
NATIVE_PROFILE_LITERALS = ("wx-default-v2", "wx_full_rules-native-v2")

# T01 semantic ownership: parsing, regions, structure, lists, appendices,
# tables and captions must each run as a real registered module.  The module
# IDs are the ones already registered in ``wxdf.builtin_modules``.
REQUIRED_SEMANTIC_MODULES = (
    "source-facts-parser",
    "source-region",
    "document-structure",
    "list-semantics",
    "table-semantics",
    "caption-semantics",
    "appendix-semantics",
)

# T01 frozen record shape: ``report["native_module_evidence"][module_id]``
# carries ``order``, ``produced`` and ``consumed`` observed at runtime.
MODULE_EVIDENCE_KEYS = ("order", "produced", "consumed")

# Capabilities the transitional forwarding adapter exposes.  A native run
# must not carry them because they exist only to relay precomputed legacy
# results.
SHALLOW_FORWARDING_CAPABILITIES = ("legacy_run_result", "legacy_source_ast")

# T02 blocks the transitional seams in the conversion subprocess.
BLOCKED_LEGACY_MODULES = (
    "docx_pipeline",
    "md_pipeline",
    "model_normalization",
    "template_finalizer",
    "wxdf.legacy_pipeline",
    "wxdf.semantics.source_projection_bridge",
)

# T08 forbids these production surfaces in the release runtime.
FORBIDDEN_LEGACY_LITERALS = (
    "wxdf.legacy_",
    "legacy-full-v1",
    "legacy_online",
    "legacy_file",
    "legacy_review",
)

NATIVE_MODES = ("modular_pure", "modular_online", "modular_file", "modular_review")

_NATIVE_PLATFORMS = ("macos-arm64", "kylin-v10-arm64")

# Marker written by the injected LLM command response of T03.
T03_SEMANTIC_MARKER = "t00_t03_command_reclassification"

# A one pixel PNG keeps the media fixtures local and generated.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _venv_python() -> Path:
    candidate = SCRIPTS / ".venv" / "bin" / "python3"
    return candidate if candidate.is_file() else Path(sys.executable)


def _cli_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("WX_DOC_FORMAT_RELEASE", None)
    environment.pop("WX_DOC_LLM_ENHANCE", None)
    environment["PYTHONPATH"] = str(SCRIPTS)
    return environment


def _write_rich_markdown(destination: Path) -> Path:
    """Write a Markdown fixture covering text, list, table, media and appendix."""

    media = destination.parent / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "architecture.png").write_bytes(_ONE_PIXEL_PNG)
    destination.write_text(
        "\n".join(
            [
                "# 系统概览",
                "",
                "正文段落说明系统总体结构。",
                "",
                "![图1 系统架构](media/architecture.png)",
                "",
                "## 功能列表",
                "",
                "1. 身份管理",
                "2. 策略管理",
                "",
                "| 项目 | 说明 |",
                "| --- | --- |",
                "| 信任评估 | 基于行为分析 |",
                "",
                "# 附录A 补充材料",
                "",
                "### A.1 术语表",
                "",
                "补充说明内容。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination


def _write_rich_docx(destination: Path) -> Path:
    """Write a DOCX fixture covering heading, body, ordered list, table and image."""

    from docx import Document

    media = destination.parent / "docx-media"
    media.mkdir(parents=True, exist_ok=True)
    picture = media / "architecture.png"
    picture.write_bytes(_ONE_PIXEL_PNG)

    document = Document()
    document.add_paragraph("系统设计说明", style="Heading 1")
    document.add_paragraph("这是正文段落。")
    document.add_paragraph("第一条", style="List Number")
    document.add_paragraph("第二条", style="List Number")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "信任评估"
    table.cell(1, 1).text = "行为分析"
    document.add_picture(str(picture))
    document.save(destination)
    return destination


def _native_request(tmp_path: Path, source: Path, **options) -> ConversionRequest:
    request_options = {
        "profile": "native_v2",
        "report_path": tmp_path / "native-report.json",
        "source_ast_path": tmp_path / "native-source-ast.json",
        "normalized_ast_path": tmp_path / "native-normalized-ast.json",
    }
    request_options.update(options)
    return ConversionRequest(
        input_path=source,
        output_path=tmp_path / "native-output.docx",
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options=request_options,
    )


def _run_native_kernel(tmp_path: Path, source: Path) -> dict:
    result = ConversionKernel().convert(_native_request(tmp_path, source))
    assert result.status in {"completed", "completed_with_warnings"}, result.diagnostics
    return {"status": result.status, "report": result.report, "artifacts": result.artifacts}


def _run_blocked_native_cli(tmp_path: Path, source: Path) -> tuple:
    """Run the public CLI with the transitional legacy seams blocked."""

    output = tmp_path / "blocked-output.docx"
    source_ast = tmp_path / "blocked-source-ast.json"
    normalized_ast = tmp_path / "blocked-normalized-ast.json"
    report = tmp_path / "blocked-report.json"
    child = "\n".join(
        [
            "import sys",
            f"BLOCKED = {BLOCKED_LEGACY_MODULES!r}",
            "class _Blocker:",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname in BLOCKED:",
            '            raise ImportError("WXDF-T02-BLOCKED:" + fullname)',
            "        return None",
            "sys.path.insert(0, sys.argv[1])",
            "sys.meta_path.insert(0, _Blocker())",
            "try:",
            "    import main",
            "    main.main(sys.argv[2:])",
            "except ImportError as exc:",
            '    if "WXDF-T02-BLOCKED:" in str(exc):',
            "        sys.stderr.write(str(exc) + '\\n')",
            "        raise SystemExit(3)",
            "    raise",
        ]
    )
    completed = subprocess.run(
        [
            str(_venv_python()),
            "-c",
            child,
            str(SCRIPTS),
            "--input", str(source),
            "--output", str(output),
            "--template", str(TEMPLATE),
            "--pipeline", "modular",
            "--modular-profile", "native-v2",
            "--list-policy", "wx-default-v2",
            "--llm-enhance", "off",
            "--report", str(report),
            "--source-ast", str(source_ast),
            "--ast", str(normalized_ast),
        ],
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
    )
    return (
        subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            completed.stdout,
            completed.stderr,
        ),
        output,
        source_ast,
        normalized_ast,
        report,
    )


def _assert_native_saved_contract(
    completed: subprocess.CompletedProcess,
    output: Path,
    source_ast_path: Path,
    normalized_ast_path: Path,
    report_path: Path,
) -> None:
    assert completed.returncode == 0, (
        "native-v2 conversion did not complete while the legacy parser, "
        "normalization, finalizer, source projection bridge and legacy pipeline "
        f"were blocked: {completed.stderr.strip()[-400:]}"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    saved_source = json.loads(source_ast_path.read_text(encoding="utf-8"))
    saved_normalized = json.loads(normalized_ast_path.read_text(encoding="utf-8"))
    assert saved_source["schema_version"] == "2.0"
    assert saved_normalized["source_ast"] == saved_source, (
        "the saved normalized AST must embed the exact saved source AST"
    )
    assert report["saved_output_audit"]["passed"] is True, report["saved_output_audit"]
    assert zipfile.is_zipfile(output), output


def _inline_kinds(ast: dict) -> set[str]:
    kinds: set[str] = set()
    for block in ast["document"]["blocks"].values():
        for item in block.get("inline_content") or []:
            if isinstance(item, dict) and item.get("kind"):
                kinds.add(str(item["kind"]))
    return kinds


def _container_kinds(ast: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for container in ast["document"]["containers"].values():
        kind = str(container.get("kind"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _block_types(ast: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in ast["document"]["blocks"].values():
        kind = str(block.get("block_type"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _load_build_module():
    path = ROOT / "packaging" / "nuitka" / "build_distribution.py"
    spec = importlib.util.spec_from_file_location("wx_t00_build_distribution", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_smoke_module():
    path = ROOT / "packaging" / "nuitka" / "smoke_distribution.py"
    spec = importlib.util.spec_from_file_location("wx_t00_smoke_distribution", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_public_assembler_module():
    path = ROOT / "packaging" / "nuitka" / "assemble_public_skill.py"
    spec = importlib.util.spec_from_file_location("wx_t00_public_skill", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_distribution = _load_build_module()
smoke_distribution = _load_smoke_module()
public_assembler = _load_public_assembler_module()


# ---------------------------------------------------------------------------
# T01: real registered semantic Modules with observable runtime produced and
# consumed capabilities in execution order.
# ---------------------------------------------------------------------------


def _module_evidence(report: dict) -> dict[str, dict]:
    evidence = report.get("native_module_evidence")
    assert isinstance(evidence, dict) and evidence, (
        "native report carries no per-module execution evidence"
    )
    return {str(module_id): record for module_id, record in evidence.items()}


def test_t01_native_conversion_reports_runtime_produced_and_consumed_capabilities(
    tmp_path: Path,
) -> None:
    report = _run_native_kernel(tmp_path, BENCHMARK_MARKDOWN)["report"]
    evidence = _module_evidence(report)

    for module_id, record in evidence.items():
        assert isinstance(record, dict), f"module {module_id} evidence is not a record"
        missing = [key for key in MODULE_EVIDENCE_KEYS if key not in record]
        assert not missing, f"module {module_id} evidence omits {missing}"
        assert isinstance(record["order"], int) and not isinstance(record["order"], bool)
        assert isinstance(record["produced"], list)
        assert isinstance(record["consumed"], list)

    orders = [record["order"] for record in evidence.values()]
    assert len(set(orders)) == len(orders), f"module execution order is not unique: {orders}"

    producer = {
        str(capability): module_id
        for module_id, record in evidence.items()
        for capability in record["produced"]
    }
    for module_id, record in evidence.items():
        for capability in record["consumed"]:
            assert str(capability) in producer, (
                f"module {module_id} consumed {capability!r} which no module produced"
            )
            assert producer[str(capability)] != module_id, (
                f"module {module_id} consumed its own output {capability!r}"
            )
            assert evidence[producer[str(capability)]]["order"] < record["order"], (
                f"module {module_id} consumed {capability!r} before it was produced"
            )

    for module_id in REQUIRED_SEMANTIC_MODULES:
        assert module_id in evidence, (
            f"native run reports no execution for semantic module {module_id!r}; "
            f"reported modules are {sorted(evidence)}"
        )
        record = evidence[module_id]
        assert record["produced"], f"module {module_id} produced nothing"
        if module_id != "source-facts-parser":
            assert record["consumed"], (
                f"module {module_id} consumed nothing observable"
            )
        consumers = [
            other_id
            for other_id, other in evidence.items()
            if set(other["consumed"]) & set(record["produced"])
        ]
        assert consumers, (
            f"module {module_id} outputs {record['produced']} are consumed by no later module"
        )
        for consumer_id in consumers:
            assert evidence[consumer_id]["order"] > record["order"], (
                f"module {module_id} output is consumed by {consumer_id} before it runs"
            )


def test_t01_native_conversion_has_no_shallow_legacy_forwarding_module(tmp_path: Path) -> None:
    report = _run_native_kernel(tmp_path, BENCHMARK_MARKDOWN)["report"]
    evidence = _module_evidence(report)

    module_ids = [str(entry["module_id"]) for entry in report["execution_plan"]["modules"]]
    assert "legacy-full-adapter" not in module_ids, module_ids
    assert not [name for name in module_ids if name.startswith("legacy")], module_ids
    assert not [name for name in evidence if name.startswith("legacy")], sorted(evidence)
    assert report.get("migration_adapter") is None, report.get("migration_adapter")
    assert report["execution_plan"]["profile"] in NATIVE_PROFILE_LITERALS, (
        report["execution_plan"]["profile"]
    )

    produced = {
        str(capability)
        for record in evidence.values()
        for capability in record["produced"]
    }
    forwarded = produced & set(SHALLOW_FORWARDING_CAPABILITIES)
    assert not forwarded, (
        f"native run still forwards precomputed legacy capabilities: {sorted(forwarded)}"
    )


# ---------------------------------------------------------------------------
# T02: native parsing and semantic ownership without the legacy seams.
# ---------------------------------------------------------------------------


def test_t02_native_markdown_conversion_completes_with_legacy_imports_blocked(tmp_path: Path) -> None:
    completed, output, source_ast, normalized_ast, report = _run_blocked_native_cli(
        tmp_path, BENCHMARK_MARKDOWN
    )
    _assert_native_saved_contract(completed, output, source_ast, normalized_ast, report)

    saved_source = json.loads(source_ast.read_text(encoding="utf-8"))
    assert _block_types(saved_source).get("table", 0) >= 1
    saved_normalized = json.loads(normalized_ast.read_text(encoding="utf-8"))
    associations = saved_normalized["source_ast"]["document"]["associations"]
    assert any(
        item.get("kind") == "caption_target" for item in associations
    ), associations


def test_t02_native_media_and_appendix_conversion_completes_with_legacy_imports_blocked(
    tmp_path: Path,
) -> None:
    source = _write_rich_markdown(tmp_path / "media-and-appendix.md")
    completed, output, source_ast, normalized_ast, report = _run_blocked_native_cli(
        tmp_path, source
    )
    _assert_native_saved_contract(completed, output, source_ast, normalized_ast, report)

    saved_source = json.loads(source_ast.read_text(encoding="utf-8"))
    assert "image" in _inline_kinds(saved_source), _inline_kinds(saved_source)
    assert _container_kinds(saved_source).get("list", 0) >= 1
    assert _block_types(saved_source).get("table", 0) >= 1
    assert _container_kinds(saved_source).get("appendix", 0) >= 1


def test_t02_native_docx_conversion_completes_with_legacy_imports_blocked(tmp_path: Path) -> None:
    source = _write_rich_docx(tmp_path / "rich-fixture.docx")
    completed, output, source_ast, normalized_ast, report = _run_blocked_native_cli(
        tmp_path, source
    )
    _assert_native_saved_contract(completed, output, source_ast, normalized_ast, report)

    saved_source = json.loads(source_ast.read_text(encoding="utf-8"))
    assert _container_kinds(saved_source).get("list_item", 0) >= 1
    assert _container_kinds(saved_source).get("table_row", 0) >= 1
    assert "image" in _inline_kinds(saved_source), _inline_kinds(saved_source)


# ---------------------------------------------------------------------------
# T03: native command enhancement through the EnhancementExecutor.
# ---------------------------------------------------------------------------


def _t03_target_node(source: Path) -> tuple[str, int, dict]:
    """Return the deterministic target node, base revision and source ref."""

    facts = parse(source)
    revision = ConversionKernel._facts_to_ast(facts)["revision"]
    root_children = facts["children"]["root"]
    target = next(
        node_id
        for node_id in root_children
        if facts["units"].get(node_id, {}).get("role") == "body"
    )
    return target, revision, json.loads(json.dumps(facts["units"][target]["source_ref"]))


def _t03_response_patch(source: Path) -> dict:
    """Build a Patch 2.0 that reclassifies one body paragraph."""

    target, revision, source_ref = _t03_target_node(source)
    return {
        "patch_schema_version": "2.0",
        "patch_id": "t00-t03-patch",
        "base_revision": revision,
        "phase": "role",
        "candidates": [
            {
                "candidate_id": "t00-t03-candidate",
                "module_id": "enhancement-command",
                "module_version": "2.0",
                "target_source_refs": [source_ref],
                "target_node_ids": [target],
                "proposal": {
                    "operation": "update_node_semantics",
                    "property_scope": ["semantics"],
                    "value": {"semantic_role": T03_SEMANTIC_MARKER},
                },
                "positive_evidence": [{"kind": "llm", "source": "llm-command"}],
                "negative_evidence": [],
                "scope": {"stages": ["role"]},
                "constraints": [],
                "rationale": "command enhancement reclassification",
                "confidence": 0.9,
            }
        ],
        "decisions": [
            {
                "decision_id": "t00-t03-decision",
                "candidate_id": "t00-t03-candidate",
                "operation": "update_node_semantics",
                "target_node_ids": [target],
                "expected_revision": revision,
                "property_scope": ["semantics"],
                "value": {"semantic_role": T03_SEMANTIC_MARKER},
                "preconditions": [],
                "evidence": [{"kind": "llm", "source": "llm-command"}],
                "invalidates": ["role", "normalize", "render", "audit"],
            }
        ],
    }


def _t03_command(tmp_path: Path, patch_path: Path) -> str:
    """Return a command that echoes one Patch per requested item."""

    helper = tmp_path / "t03_response.py"
    helper.write_text(
        "\n".join(
            [
                "import json, sys",
                "raw = sys.stdin.read().strip()",
                "try:",
                "    payload = json.loads(raw) if raw[:1] in '{[' else {}",
                "except ValueError:",
                "    payload = {}",
                "items = payload.get('items') if isinstance(payload, dict) else None",
                "count = len(items) if isinstance(items, list) and items else 1",
                "with open(sys.argv[1], encoding='utf-8') as handle:",
                "    patch = json.load(handle)",
                "if count == 1:",
                "    sys.stdout.write(json.dumps(patch, ensure_ascii=False))",
                "else:",
                "    responses = []",
                "    for index in range(count):",
                "        entry = json.loads(json.dumps(patch))",
                "        entry['patch_id'] = f\"{patch['patch_id']}-{index}\"",
                "        responses.append(entry)",
                "    sys.stdout.write(json.dumps(responses, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )
    return f"{shlex.quote(str(_venv_python()))} {shlex.quote(str(helper))} {shlex.quote(str(patch_path))}"


def test_t03_native_command_enhancement_is_resolved_and_committed(tmp_path: Path) -> None:
    target, _revision, _ref = _t03_target_node(BENCHMARK_MARKDOWN)
    patch = _t03_response_patch(BENCHMARK_MARKDOWN)
    assert validate_patch(patch) is None
    patch_path = tmp_path / "t03-patch.json"
    patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")

    marker = tmp_path / "command-invoked.txt"
    command = f"{_t03_command(tmp_path, patch_path)} && printf invoked > {shlex.quote(str(marker))}"
    output = tmp_path / "enhanced.docx"
    report_path = tmp_path / "enhanced-report.json"
    source_ast_path = tmp_path / "enhanced-source-ast.json"

    completed = subprocess.run(
        [
            str(_venv_python()),
            str(SCRIPTS / "main.py"),
            "--input", str(BENCHMARK_MARKDOWN),
            "--output", str(output),
            "--template", str(TEMPLATE),
            "--pipeline", "modular",
            "--modular-profile", "native-v2",
            "--list-policy", "wx-default-v2",
            "--llm-enhance", "all",
            "--llm-command", command,
            "--report", str(report_path),
            "--source-ast", str(source_ast_path),
        ],
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr.strip()[-400:]
    assert marker.is_file(), "the injected LLM command was never invoked"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("migration_adapter") is None, report.get("migration_adapter")
    assert report["profile"] in NATIVE_PROFILE_LITERALS, report["profile"]

    resolutions = [
        decision
        for decision in report.get("decision_set", {}).get("decisions", [])
        if target in (decision.get("target_node_ids") or [])
        and T03_SEMANTIC_MARKER in json.dumps(decision.get("value"), ensure_ascii=False)
    ]
    assert resolutions, (
        "the command response change is absent from the native resolution for "
        f"{target}: {json.dumps(report.get('decision_set', {}).get('decisions', []), ensure_ascii=False)[:400]}"
    )
    assert report["native_transaction"]["status"] in {"committed", "already_committed"}
    assert report["native_transaction"]["decision_count"] == len(
        report["decision_set"]["decisions"]
    )

    committed = json.loads(source_ast_path.read_text(encoding="utf-8"))
    assert committed["document"]["blocks"][target].get("semantic_role") == T03_SEMANTIC_MARKER

    off_source_ast = tmp_path / "plain-source-ast.json"
    off_marker = tmp_path / "off-command-invoked.txt"
    off_command = f"printf invoked > {shlex.quote(str(off_marker))}"
    off_run = subprocess.run(
        [
            str(_venv_python()),
            str(SCRIPTS / "main.py"),
            "--input", str(BENCHMARK_MARKDOWN),
            "--output", str(tmp_path / "plain.docx"),
            "--template", str(TEMPLATE),
            "--pipeline", "modular",
            "--modular-profile", "native-v2",
            "--list-policy", "wx-default-v2",
            "--llm-enhance", "off",
            "--llm-command", off_command,
            "--source-ast", str(off_source_ast),
        ],
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
    )
    assert off_run.returncode == 0, off_run.stderr.strip()[-400:]
    assert not off_marker.exists(), "ordinary --llm-enhance off invoked the LLM command"
    plain = json.loads(off_source_ast.read_text(encoding="utf-8"))
    assert plain["document"]["blocks"][target].get("semantic_role") != T03_SEMANTIC_MARKER


# ---------------------------------------------------------------------------
# T04: native file protocol checkpoint identity.
# ---------------------------------------------------------------------------


def test_t04_generate_requests_writes_native_checkpoint_identity(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    completed = subprocess.run(
        [
            str(_venv_python()),
            str(SCRIPTS / "main.py"),
            "--input", str(BENCHMARK_MARKDOWN),
            "--template", str(TEMPLATE),
            "--pipeline", "modular",
            "--modular-profile", "native-v2",
            "--list-policy", "wx-default-v2",
            "--llm-enhance", "force-a",
            "--generate-requests", str(request_dir),
        ],
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr.strip()[-400:]

    checkpoint_path = request_dir / "run.json"
    assert checkpoint_path.is_file(), sorted(
        path.name for path in request_dir.iterdir()
    ) if request_dir.is_dir() else "request directory was not created"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint.get("checkpoint_schema_version") == "2.0", sorted(checkpoint)
    assert validate_checkpoint(checkpoint) is None
    assert checkpoint["profile"] in NATIVE_PROFILE_LITERALS, checkpoint.get("profile")
    assert checkpoint["list_policy"] == "wx-default-v2"
    assert [entry for entry in checkpoint["modules"] if entry.get("version")]
    assert checkpoint["decision_ledger"]["source_binding"]
    fingerprints = checkpoint["resource_summary"]["fingerprints"]
    assert fingerprints["template"].startswith("sha256:")
    assert isinstance(checkpoint["content_overlay"], list)
    assert isinstance(checkpoint["presentation_override"], list)
    assert isinstance(checkpoint["applied_transactions"], list)
    assert isinstance(checkpoint["transaction_content_digests"], dict)


# ---------------------------------------------------------------------------
# T05: native review mode manifest and candidate-gated publication.
# ---------------------------------------------------------------------------


def test_t05_native_mode_manifest_enables_modular_review() -> None:
    manifest = mode_manifest()

    assert manifest["modular_review"]["status"] == "enabled", manifest["modular_review"]
    assert manifest["modular_review"].get("entrypoint")
    assert manifest["modular_pure"]["status"] == "enabled"


def _review_bundle(*, level: int, presentation: str) -> dict:
    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p0": {
                    "block_type": "paragraph",
                    "role": "heading",
                    "level": level,
                    "inline_content": [{"kind": "text", "text": "段落0", "marks": []}],
                    "source_ref": {
                        "source_id": "p0",
                        "part": "document.md",
                        "content_sha256": "sha256:" + "a" * 64,
                    },
                }
            },
            "containers": {"root": {"kind": "document"}},
            "children": {"root": ["p0"]},
            "associations": [],
            "resources": {},
        },
    }
    return {
        "source_ast": ast,
        "normalized_ast": {
            "semantic_digest": f"semantic-{level}",
            "presentation_digest": presentation,
            "source_ast": ast,
        },
        "artifacts": {"docx": "candidate.docx"},
    }


def test_t05_publication_requires_an_accepted_candidate(tmp_path: Path) -> None:
    baseline = _review_bundle(level=1, presentation="presentation-1")
    candidate = _review_bundle(level=2, presentation="presentation-1")
    finding = {
        "audit_id": "heading:p0",
        "severity": "high",
        "repairable": True,
        "block_id": "p0",
        "category": "semantic",
    }
    artifact = tmp_path / "candidate.docx"
    artifact.write_bytes(b"docx")
    destination = tmp_path / "published"

    accepted_decision, _ = accept_candidate(
        baseline,
        candidate,
        {"baseline": [finding], "candidate": []},
        decisions=[{"target_node_ids": ["p0"]}],
    )
    assert accepted_decision.accepted is True
    result = publish_bundle(
        {"docx": artifact},
        destination,
        run_id="run-t05-accepted",
        acceptance=accepted_decision.to_dict(),
    )
    assert result["status"] == "completed"

    rejected_decision = evaluate_candidate(
        baseline,
        _review_bundle(level=2, presentation="presentation-2"),
        {"baseline": [finding], "candidate": [finding]},
    )
    assert rejected_decision.accepted is False
    with pytest.raises(PublicationError, match="accepted candidate"):
        publish_bundle(
            {"docx": artifact},
            tmp_path / "rejected",
            run_id="run-t05-rejected",
            acceptance=rejected_decision.to_dict(),
        )
    with pytest.raises(PublicationError, match="accepted candidate"):
        publish_bundle(
            {"docx": artifact},
            tmp_path / "missing",
            run_id="run-t05-missing",
        )
    assert not (tmp_path / "rejected" / "COMMITTED").exists()


# ---------------------------------------------------------------------------
# T06: default public CLI switches to native-v2 without silent fallback.
# ---------------------------------------------------------------------------


def test_t06_default_cli_invocation_uses_native_pipeline_and_policy(tmp_path: Path) -> None:
    output = tmp_path / "default.docx"
    report_path = tmp_path / "default-report.json"
    completed = subprocess.run(
        [
            str(_venv_python()),
            str(SCRIPTS / "main.py"),
            "--input", str(BENCHMARK_MARKDOWN),
            "--output", str(output),
            "--template", str(TEMPLATE),
            "--report", str(report_path),
        ],
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr.strip()[-400:]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("pipeline") == "modular", sorted(report)
    assert report.get("list_policy") == "wx-default-v2", report.get("list_policy")
    assert report.get("profile") in NATIVE_PROFILE_LITERALS, report.get("profile")
    assert report.get("migration_adapter") is None, report.get("migration_adapter")

    explicit_output = tmp_path / "explicit-legacy.docx"
    explicit_report = tmp_path / "explicit-legacy-report.json"
    explicit = subprocess.run(
        [
            str(_venv_python()),
            str(SCRIPTS / "main.py"),
            "--input", str(BENCHMARK_MARKDOWN),
            "--output", str(explicit_output),
            "--template", str(TEMPLATE),
            "--pipeline", "legacy",
            "--list-policy", "legacy",
            "--report", str(explicit_report),
        ],
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
    )
    assert explicit.returncode == 0, explicit.stderr.strip()[-400:]
    assert explicit_output.is_file()
    assert json.loads(explicit_report.read_text(encoding="utf-8")).get("pipeline") != "modular"


# ---------------------------------------------------------------------------
# T07: public Skill platform contract.
# ---------------------------------------------------------------------------


def _write_launcher_artifact(artifacts_dir: Path, version: str, platform_id: str) -> Path:
    """Write a dummy release artifact that the public launcher can install."""
    from test_t09_compiled_acceptance import write_archive_fixture

    assert version == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    return write_archive_fixture(artifacts_dir, platform_id, marker="WXDF-T07-LAUNCHER-OK")


def _run_public_launcher(public_skill: Path, tmp_path: Path, *, platform: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["WX_DOC_FORMAT_ARCHIVE_DIR"] = str(tmp_path / "artifacts")
    environment["WX_DOC_FORMAT_CACHE_DIR"] = str(tmp_path / f"cache-{platform}")
    system, machine = {"macos-arm64": ("Darwin", "arm64"), "kylin-v10-arm64": ("Linux", "aarch64")}[platform]
    shim = tmp_path / f"uname-shim-{platform}"
    shim.mkdir(parents=True, exist_ok=True)
    fake_uname = shim / "uname"
    fake_uname.write_text(
        f'#!/bin/sh\nif [ "$1" = "-s" ]; then printf "{system}\\n"; else printf "{machine}\\n"; fi\n',
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)
    if platform == "macos-arm64":
        fake_version = shim / "sw_vers"
        fake_version.write_text('#!/bin/sh\nprintf "26.0\\n"\n', encoding="utf-8")
        fake_version.chmod(0o755)
    environment["PATH"] = f"{shim}{os.pathsep}{environment['PATH']}"
    return subprocess.run(
        ["/bin/sh", str(public_skill / "scripts" / "run.sh"), "--version"],
        env=environment,
        capture_output=True,
        text=True,
    )


def _public_manifest_path(public_skill: Path) -> Path:
    return public_skill / "artifacts" / "manifest.json"


def _platform_statuses(public_skill: Path) -> dict[str, str]:
    manifest = json.loads(_public_manifest_path(public_skill).read_text(encoding="utf-8"))
    platforms = manifest.get("platforms")
    assert isinstance(platforms, dict) and platforms, (
        "the public Skill manifest exposes no per-platform status: "
        f"{sorted(manifest)}"
    )
    return {
        str(platform_id): str(entry["status"])
        for platform_id, entry in platforms.items()
    }


def test_t07_public_skill_supported_targets_exclude_windows(tmp_path: Path) -> None:
    supported = tuple(public_assembler.SUPPORTED_ARTIFACTS)
    assert "windows-x86_64" not in supported, supported

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    with pytest.raises(ValueError):
        public_assembler.assemble_public_skill(
            artifacts_dir,
            tmp_path / "windows-skill",
            ("windows-x86_64",),
        )


def test_t07_public_skill_manifest_reports_per_platform_status(tmp_path: Path) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    for platform_id in _NATIVE_PLATFORMS:
        _write_launcher_artifact(artifacts_dir, version, platform_id)

    public_skill = public_assembler.assemble_public_skill(
        artifacts_dir,
        tmp_path / "public-skill",
        _NATIVE_PLATFORMS,
        "26.0",
    )
    statuses = _platform_statuses(public_skill)
    assert statuses["macos-arm64"] == "accepted", statuses
    assert statuses["kylin-v10-arm64"] == "candidate", statuses
    assert statuses.get("windows-x86_64", "unavailable") == "unavailable", statuses


def test_t07_public_skill_launcher_consumes_platform_status(tmp_path: Path) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    for platform_id in _NATIVE_PLATFORMS:
        _write_launcher_artifact(artifacts_dir, version, platform_id)

    public_skill = public_assembler.assemble_public_skill(
        artifacts_dir,
        tmp_path / "public-skill",
        _NATIVE_PLATFORMS,
        "26.0",
    )

    # Keep the Kylin artifact and its checksum entry in place, and let the
    # manifest mark the platform unavailable.  A launcher driven by artifact
    # presence would still select it, so this isolates status consumption.
    manifest_path = _public_manifest_path(public_skill)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("platforms", {})["kylin-v10-arm64"] = {"status": "unavailable"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    accepted = _run_public_launcher(public_skill, tmp_path, platform="macos-arm64")
    assert accepted.returncode == 0, accepted.stderr.strip()[-300:]
    assert "WXDF-T07-LAUNCHER-OK" in accepted.stdout

    rejected = _run_public_launcher(public_skill, tmp_path, platform="kylin-v10-arm64")
    assert rejected.returncode == 3, (
        "the launcher selected a platform the manifest marks unavailable: "
        f"{rejected.stdout.strip()} {rejected.stderr.strip()[-300:]}"
    )
    assert "WXDF-E-RUNTIME-UNAVAILABLE" in rejected.stderr, rejected.stderr


# ---------------------------------------------------------------------------
# T08: release runtime carries no legacy production surface.
# ---------------------------------------------------------------------------


def test_t08_release_manifests_exclude_legacy_production_surface() -> None:
    manifest = build_distribution.modular_module_manifest()
    serialized = json.dumps(manifest, ensure_ascii=False)

    for literal in FORBIDDEN_LEGACY_LITERALS:
        assert literal not in serialized, f"release module manifest exposes {literal}"
    assert not [
        name for name in manifest["source_modules"] if "legacy" in name
    ], manifest["source_modules"]
    assert set(manifest["open_modes"]) == set(NATIVE_MODES), sorted(manifest["open_modes"])
    for mode in NATIVE_MODES:
        assert manifest["open_modes"][mode]["status"] == "enabled", manifest["open_modes"][mode]


# ---------------------------------------------------------------------------
# T09: distribution smoke validation of the native-only module set.
# ---------------------------------------------------------------------------

NATIVE_RUNTIME_MODULES = (
    "wxdf",
    "wxdf.auditing",
    "wxdf.builtin_modules",
    "wxdf.contracts",
    "wxdf.decision_ledger",
    "wxdf.enhancement",
    "wxdf.kernel",
    "wxdf.metrics",
    "wxdf.model",
    "wxdf.normalization",
    "wxdf.parsers",
    "wxdf.publication",
    "wxdf.registry",
    "wxdf.render_plan",
    "wxdf.rendering",
    "wxdf.resolver",
    "wxdf.review",
    "wxdf.run_store",
    "wxdf.semantics",
    "wxdf.semantics.appendices",
    "wxdf.semantics.captions",
    "wxdf.semantics.lists",
    "wxdf.semantics.protected",
    "wxdf.semantics.regions",
    "wxdf.semantics.structure",
    "wxdf.semantics.tables",
    "wxdf.source_store",
    "wxdf.template",
    "wxdf.transaction",
    "wxdf.validation",
)


def _native_release_manifest() -> dict:
    return {
        "modular": {
            "package": "wxdf",
            "source_modules": list(NATIVE_RUNTIME_MODULES),
            "runtime_modules": list(NATIVE_RUNTIME_MODULES),
            "module_set_status": "matched",
            "runtime_evidence": "explicit_nuitka_inclusion",
            "contract_versions": {
                "source_facts": "1.0",
                "ast": "2.0",
                "patch": "2.0",
                "checkpoint": "2.0",
                "capability_input": "2.0",
                "normalized": "1.0",
            },
            "open_modes": {
                mode: {"status": "enabled", "entrypoint": mode, "contract": "T09"}
                for mode in NATIVE_MODES
            },
        }
    }


def test_t09_distribution_smoke_accepts_native_only_module_set() -> None:
    manifest = _native_release_manifest()

    assert smoke_distribution.validate_modular_manifest(manifest) is None


def test_t09_distribution_smoke_rejects_legacy_production_module() -> None:
    manifest = _native_release_manifest()
    legacy_modules = [*manifest["modular"]["source_modules"], "wxdf.legacy_pipeline"]
    manifest["modular"]["source_modules"] = legacy_modules
    manifest["modular"]["runtime_modules"] = legacy_modules

    with pytest.raises(RuntimeError, match="wxdf\\.legacy_pipeline"):
        smoke_distribution.validate_modular_manifest(manifest)


def test_t09_distribution_smoke_rejects_legacy_production_mode() -> None:
    manifest = _native_release_manifest()
    manifest["modular"]["open_modes"]["legacy_online"] = {
        "status": "enabled",
        "entrypoint": "legacy CLI",
        "contract": "legacy",
    }

    with pytest.raises(RuntimeError, match="legacy_online"):
        smoke_distribution.validate_modular_manifest(manifest)
