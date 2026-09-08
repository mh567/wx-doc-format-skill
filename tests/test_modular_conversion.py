from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel, ConversionRequest


ROOT = Path(__file__).resolve().parents[1]


def test_modular_markdown_conversion_requires_complete_adapter(tmp_path: Path) -> None:
    output = tmp_path / "candidate.docx"
    source_ast = tmp_path / "source.json"
    normalized_ast = tmp_path / "normalized.json"
    report = tmp_path / "report.json"
    request = ConversionRequest(
        input_path=ROOT / "input" / "test_baseline.md",
        output_path=output,
        template_path=ROOT / "assets" / "wx_template.docx",
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"source_ast_path": source_ast, "normalized_ast_path": normalized_ast, "report_path": report},
    )

    result = ConversionKernel().convert(request)

    assert result.status == "unsupported"
    assert result.exit_code == 2
    assert result.diagnostics[0]["code"] == "MODULAR_PROFILE_INCOMPLETE"
    assert not output.exists()


def test_modular_preflight_rejects_online_enhancement_without_legacy_fallback(tmp_path: Path) -> None:
    output = tmp_path / "candidate.docx"
    request = ConversionRequest(
        input_path=ROOT / "input" / "test_baseline.md",
        output_path=output,
        pipeline="modular",
        list_policy="legacy",
        options={"llm_enhance": "abc"},
    )

    result = ConversionKernel().convert(request)

    assert result.status == "unsupported"
    assert result.exit_code == 2
    assert result.diagnostics[0]["code"] == "MODULAR_ENHANCEMENT_UNSUPPORTED"
    assert not output.exists()


def test_cli_modular_mode_runs_real_pure_rule_path(tmp_path: Path) -> None:
    output = tmp_path / "candidate.docx"
    report = tmp_path / "report.json"
    command = [
        str(ROOT / "scripts" / ".venv" / "bin" / "python3"),
        str(ROOT / "scripts" / "main.py"),
        "--input", str(ROOT / "input" / "test_baseline.md"),
        "--output", str(output),
        "--template", str(ROOT / "assets" / "wx_template.docx"),
        "--pipeline", "modular",
        "--list-policy", "legacy",
        "--llm-enhance", "off",
        "--report", str(report),
    ]

    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert output.exists()
    saved_report = json.loads(report.read_text(encoding="utf-8"))
    assert saved_report["status"] == "completed"
    assert saved_report["pipeline"] == "modular"
    assert saved_report["migration_adapter"] == "legacy-full-v1"
    assert saved_report["execution_plan"]["profile"] == "wx_full_rules"


def test_modular_cli_exports_ast2_with_title_headers_and_caption_relations(tmp_path: Path) -> None:
    output = tmp_path / "candidate.docx"
    source_ast_path = tmp_path / "source.json"
    normalized_ast_path = tmp_path / "normalized.json"
    command = [
        str(ROOT / "scripts" / ".venv" / "bin" / "python3"),
        str(ROOT / "scripts" / "main.py"),
        "--input", str(ROOT / "input" / "benchmark_report.md"),
        "--output", str(output),
        "--template", str(ROOT / "assets" / "wx_template.docx"),
        "--pipeline", "modular",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", "off",
        "--source-ast", str(source_ast_path),
        "--ast", str(normalized_ast_path),
    ]

    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    source_ast = json.loads(source_ast_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_ast_path.read_text(encoding="utf-8"))
    assert source_ast["schema_version"] == "2.0"
    assert normalized["schema_version"] == "1.0"
    assert source_ast["document"]["blocks"]["b0001"]["role"] == "title"
    assert source_ast["document"]["blocks"]["b0008"]["header_rows"] == 1
    assert normalized["source_ast"]["document"]["blocks"]["caption-auto-1"]["target_id"] == "b0008"
    assert normalized["source_ast"]["document"]["associations"][0] == {
        "kind": "caption_target",
        "source_id": "caption-auto-1",
        "target_id": "b0008",
    }
    assert normalized["presentation"]["template_contract"]["toc"]["mode"] == "template_fragment"
    assert normalized["presentation"]["template_contract"]["tables"]["name"] == "Table Grid"


def test_modular_cli_preserves_legacy_file_protocol_request_generation(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    command = [
        str(ROOT / "scripts" / ".venv" / "bin" / "python3"),
        str(ROOT / "scripts" / "main.py"),
        "--input", str(ROOT / "input" / "benchmark_report.md"),
        "--template", str(ROOT / "assets" / "wx_template.docx"),
        "--pipeline", "modular",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", "force-a",
        "--generate-requests", str(request_dir),
    ]

    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert (request_dir / "run.json").is_file()
    assert (request_dir / "llm_requests.jsonl").is_file()


def test_modular_nested_markdown_lists_require_complete_adapter(tmp_path: Path) -> None:
    source = tmp_path / "nested.md"
    source.write_text("1. 父项\n   - 子项\n2. 第二项\n", encoding="utf-8")
    output = tmp_path / "nested.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=ROOT / "assets" / "wx_template.docx",
        pipeline="modular",
        list_policy="wx-default-v2",
    )

    result = ConversionKernel().convert(request)

    assert result.status == "unsupported"
    assert result.diagnostics[0]["code"] == "MODULAR_PROFILE_INCOMPLETE"
    assert not output.exists()


def test_modular_cli_preserves_real_template_title_toc_and_table_captions(tmp_path: Path) -> None:
    output = tmp_path / "benchmark-report.docx"
    report = tmp_path / "report.json"
    command = [
        str(ROOT / "scripts" / ".venv" / "bin" / "python3"),
        str(ROOT / "scripts" / "main.py"),
        "--input", str(ROOT / "input" / "benchmark_report.md"),
        "--output", str(output),
        "--template", str(ROOT / "assets" / "wx_template.docx"),
        "--pipeline", "modular",
        "--list-policy", "wx-default-v2",
        "--llm-enhance", "off",
        "--report", str(report),
    ]

    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert output.exists()
    document = Document(output)
    title = next(paragraph for paragraph in document.paragraphs if paragraph.text == "LLM 增强基准对比测试报告")
    assert title.style.name == "文档标题"
    assert [cell.text for cell in document.tables[0].rows[0].cells] == [
        "文档",
        "类别",
        "模式",
        "非模板样式",
        "标题层级警告",
        "风险提示",
    ]
    assert document.tables[0].style.name == "Table Grid"
    assert all(
        paragraph.style.name == "表正文"
        for table in document.tables
        for row in table.rows
        for cell in row.cells
        for paragraph in cell.paragraphs
    )
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert document_xml.count("TOC \\o") == 1
    assert document_xml.count("SEQ Table") == 2

    saved_report = json.loads(report.read_text(encoding="utf-8"))
    assert saved_report["template_finalizer"]
    assert saved_report["toc_replacement_audit"]["passed"]
    assert saved_report["caption_placement_audit"]["passed"]
