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


def test_modular_markdown_conversion_runs_complete_native_chain(tmp_path: Path) -> None:
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

    assert result.status == "completed", result.diagnostics
    assert result.exit_code == 0
    assert output.is_file()
    assert source_ast.is_file()
    assert normalized_ast.is_file()
    assert report.is_file()
    assert all(
        "legacy" not in item["module_id"]
        for item in result.report["execution_plan"]["modules"]
    )


def test_modular_preflight_rejects_online_enhancement_without_adapter(tmp_path: Path) -> None:
    output = tmp_path / "candidate.docx"
    request = ConversionRequest(
        input_path=ROOT / "input" / "test_baseline.md",
        output_path=output,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "abc"},
    )

    result = ConversionKernel().convert(request)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.diagnostics[0]["code"] == "NATIVE_ENHANCEMENT_ADAPTER_MISSING"
    assert not output.exists()


def test_cli_rejects_transitional_modular_legacy_adapter_combination(tmp_path: Path) -> None:
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

    assert completed.returncode == 2
    assert "modular pipeline requires --list-policy wx-default-v2" in completed.stderr
    assert not output.exists()
    assert not report.exists()


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
    source_document = source_ast["document"]
    title_blocks = [
        block for block in source_document["blocks"].values()
        if block.get("block_type") == "paragraph" and block.get("role") == "title"
    ]
    assert len(title_blocks) == 1
    assert title_blocks[0]["inline_content"][0]["text"] == "LLM 增强基准对比测试报告"

    table_ids = [
        block_id for block_id, block in source_document["blocks"].items()
        if block.get("block_type") == "table"
    ]
    assert len(table_ids) == 2
    for table_id in table_ids:
        first_row_id = source_document["children"][table_id][0]
        first_row_cells = source_document["children"][first_row_id]
        assert first_row_cells
        assert all(
            source_document["containers"][cell_id]["cell_role"] == "header"
            for cell_id in first_row_cells
        )

    normalized_document = normalized["source_ast"]["document"]
    captions = {
        block_id: block
        for block_id, block in normalized_document["blocks"].items()
        if block.get("block_type") == "paragraph" and block.get("role") == "caption"
    }
    assert {block["target_id"] for block in captions.values()} == set(table_ids)
    assert {
        (association["source_id"], association["target_id"])
        for association in normalized_document["associations"]
        if association.get("kind") == "caption_target"
    } == {(caption_id, caption["target_id"]) for caption_id, caption in captions.items()}
    assert normalized["presentation"]["template_contract"]["toc"]["mode"] == "template_fragment"
    assert normalized["presentation"]["template_contract"]["tables"]["name"] == "Table Grid"


def test_modular_cli_generates_native_file_protocol_requests(tmp_path: Path) -> None:
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


def test_modular_nested_markdown_lists_run_complete_native_chain(tmp_path: Path) -> None:
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

    assert result.status == "completed", result.diagnostics
    assert output.is_file()
    with ZipFile(output) as archive:
        assert archive.testzip() is None


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
    assert saved_report["profile"] == "wx-default-v2"
    assert saved_report["saved_output_audit"]["passed"] is True
    assert saved_report["native_module_evidence"]["native-renderer"]["status"] == "completed"
    assert saved_report["native_module_evidence"]["native-auditor"]["status"] == "completed"
