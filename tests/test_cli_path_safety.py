from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest
from docx import Document


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TEMPLATE = ROOT / "assets" / "wx_template.docx"
sys.path.insert(0, str(SCRIPTS))

import main as cli_main


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_cli(*arguments: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "main.py"), *(str(item) for item in arguments)],
        env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
        capture_output=True,
        text=True,
    )


def _write_markdown(path: Path, body: str = "正文内容。\n") -> Path:
    path.write_text(f"# 文档标题\n\n{body}", encoding="utf-8")
    return path


def _template_copy(tmp_path: Path) -> Path:
    template = tmp_path / "template.docx"
    template.write_bytes(TEMPLATE.read_bytes())
    return template


def _filesystem_case_insensitive(directory: Path) -> bool:
    probe = directory / "__wxdf_case_probe__"
    probe.write_text("", encoding="utf-8")
    try:
        return (directory / "__WXDF_CASE_PROBE__").exists()
    finally:
        probe.unlink()


@pytest.mark.parametrize(
    ("conflict", "expected_names"),
    [
        ("output-input", ("--output", "--input")),
        ("report-input", ("--report", "--input")),
        ("output-template", ("--output", "--template")),
        ("report-output", ("--output", "--report")),
        ("hardlink-output", ("--output", "--input")),
    ],
)
def test_cli_rejects_output_path_conflicts(
    tmp_path: Path, conflict: str, expected_names: tuple[str, str]
) -> None:
    source = _write_markdown(tmp_path / "source.md")
    template = _template_copy(tmp_path)
    output = tmp_path / "output.docx"
    report = tmp_path / "report.json"
    if conflict == "output-input":
        output = source
    elif conflict == "report-input":
        report = source
    elif conflict == "output-template":
        output = template
    elif conflict == "report-output":
        report = output
    else:
        output = tmp_path / "alias.docx"
        os.link(source, output)

    source_digest = _sha256(source)
    template_digest = _sha256(template)
    result = _run_cli(
        "--input", source,
        "--output", output,
        "--template", template,
        "--report", report,
    )

    assert result.returncode == 2
    for name in expected_names:
        assert name in result.stderr
    assert _sha256(source) == source_digest
    assert _sha256(template) == template_digest


def test_cli_rejects_missing_case_insensitive_alias(tmp_path: Path) -> None:
    if not _filesystem_case_insensitive(tmp_path):
        pytest.skip("filesystem is case sensitive")
    source = _write_markdown(tmp_path / "source.md")
    template = _template_copy(tmp_path)
    upper_report = tmp_path / "Report.json"
    lower_report = tmp_path / "report.json"

    result = _run_cli(
        "--input", source,
        "--output", tmp_path / "output.docx",
        "--template", template,
        "--report", upper_report,
        "--report-md", lower_report,
    )

    assert result.returncode == 2
    assert "--report" in result.stderr
    assert "--report-md" in result.stderr
    assert not upper_report.exists()
    assert not lower_report.exists()


def test_cli_rejects_output_symlink_to_input(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path / "source.md")
    template = _template_copy(tmp_path)
    alias = tmp_path / "alias.md"
    alias.symlink_to(source)
    source_digest = _sha256(source)

    result = _run_cli(
        "--input", source,
        "--output", alias,
        "--template", template,
    )

    assert result.returncode == 2
    assert "--output" in result.stderr
    assert "--input" in result.stderr
    assert _sha256(source) == source_digest


def test_cli_reports_symlink_loop_clearly(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path / "source.md")
    template = _template_copy(tmp_path)
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(loop)
    except OSError:
        pytest.skip("symlinks are unavailable")

    result = _run_cli(
        "--input", source,
        "--output", loop,
        "--template", template,
    )

    assert result.returncode == 2
    assert "cannot resolve path" in result.stderr
    assert "loop" in result.stderr


def test_cli_rejects_output_with_parent_normalization(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path / "source.md")
    template = _template_copy(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    output = nested / ".." / "source.md"

    result = _run_cli(
        "--input", source,
        "--output", output,
        "--template", template,
    )

    assert result.returncode == 2
    assert "--output" in result.stderr
    assert "--input" in result.stderr


@pytest.mark.parametrize("flag", ["--ast", "--source-ast"])
def test_review_rejects_model_output_equal_to_input(tmp_path: Path, flag: str) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    document.add_paragraph("正文内容")
    document.save(source)
    template = _template_copy(tmp_path)
    source_digest = _sha256(source)

    result = _run_cli(
        "--review",
        "--input", source,
        "--template", template,
        flag, source,
    )

    assert result.returncode == 2
    assert flag in result.stderr
    assert "--input" in result.stderr
    assert _sha256(source) == source_digest


def test_review_rejects_duplicate_report_targets(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    document.add_paragraph("正文内容")
    document.save(source)
    template = _template_copy(tmp_path)
    report = tmp_path / "review.json"

    result = _run_cli(
        "--review",
        "--input", source,
        "--template", template,
        "--report", report,
        "--report-md", report,
    )

    assert result.returncode == 2
    assert "--report" in result.stderr
    assert "--report-md" in result.stderr
    assert not report.exists()


def test_review_rejects_report_path_equal_to_input(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("文档标题", style="Heading 1")
    document.add_paragraph("正文内容")
    document.save(source)
    source_digest = _sha256(source)

    result = _run_cli(
        "--review",
        "--input", source,
        "--template", _template_copy(tmp_path),
        "--report-html", source,
    )

    assert result.returncode == 2
    assert "--report-html" in result.stderr
    assert "--input" in result.stderr
    assert _sha256(source) == source_digest


def test_normal_conversion_to_distinct_paths_succeeds(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path / "source.md")
    output = tmp_path / "output.docx"
    report = tmp_path / "report.json"

    result = _run_cli(
        "--input", source,
        "--output", output,
        "--template", _template_copy(tmp_path),
        "--report", report,
    )

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert report.is_file()


def test_report_write_failure_keeps_old_report_and_removes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"status": "old"}', encoding="utf-8")

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError(f"cannot replace {destination}")

    monkeypatch.setattr(cli_main.os, "replace", fail_replace)
    with pytest.raises(OSError):
        cli_main._write_json_report(report, {"status": "new"})

    assert json.loads(report.read_text(encoding="utf-8")) == {"status": "old"}
    assert sorted(path.name for path in tmp_path.iterdir()) == ["report.json"]


def _media_payloads(path: Path) -> list[bytes]:
    with ZipFile(path) as archive:
        return [
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("word/media/")
        ]


def _markdown_with_external_image(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / "source"
    nested = source_dir / "sub"
    nested.mkdir(parents=True)
    image = source_dir / "outside.png"
    image.write_bytes(PNG_1X1)
    source = nested / "source.md"
    source.write_text("# 文档标题\n\n![外部图](../outside.png)\n", encoding="utf-8")
    return source, image


def test_markdown_external_image_is_blocked_by_default(tmp_path: Path) -> None:
    source, image = _markdown_with_external_image(tmp_path)
    output = tmp_path / "output.docx"
    report = tmp_path / "report.json"

    result = _run_cli(
        "--input", source,
        "--output", output,
        "--template", _template_copy(tmp_path),
        "--report", report,
    )

    assert result.returncode == 0, result.stderr
    assert PNG_1X1 not in _media_payloads(output)
    assert "外部图" in "\n".join(paragraph.text for paragraph in Document(output).paragraphs)
    assert "external_asset_outside_source" in report.read_text(encoding="utf-8")


def test_markdown_external_image_is_embedded_when_allowed(tmp_path: Path) -> None:
    source, image = _markdown_with_external_image(tmp_path)
    output = tmp_path / "output.docx"
    report = tmp_path / "report.json"

    result = _run_cli(
        "--input", source,
        "--output", output,
        "--template", _template_copy(tmp_path),
        "--report", report,
        "--allow-external-assets",
    )

    assert result.returncode == 0, result.stderr
    assert image.read_bytes() in _media_payloads(output)


def test_checkpoint_configuration_records_external_assets_only_when_enabled(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    args = SimpleNamespace(
        input=tmp_path / "source.md",
        template=None,
        report=None,
        report_md=None,
        ast=None,
        source_ast=None,
        fail_on_risk=False,
        llm_enhance="off",
        llm_hint=None,
        strict_normalize=True,
        delivery_policy="strict",
        table_row_height_cm=0.7,
        table_row_height_rule="at-least",
        caption_numbering="chapter",
    )
    output_path = tmp_path / "output.docx"

    default = cli_main._native_checkpoint_configuration(args, output_path=output_path)
    args.allow_external_assets = True
    enabled = cli_main._native_checkpoint_configuration(args, output_path=output_path)

    assert "allow_external_assets" not in default
    assert enabled["allow_external_assets"] is True


def test_resume_keeps_allow_external_assets(tmp_path: Path) -> None:
    source, _image = _markdown_with_external_image(tmp_path)
    output = tmp_path / "output.docx"
    request_dir = tmp_path / "requests"
    template = _template_copy(tmp_path)

    generated = _run_cli(
        "--input", source,
        "--output", output,
        "--template", template,
        "--llm-enhance", "force-a",
        "--allow-external-assets",
        "--generate-requests", request_dir,
    )
    assert generated.returncode == 0, generated.stderr
    checkpoint = request_dir / "run.json"
    configuration = json.loads(checkpoint.read_text(encoding="utf-8"))["configuration"]
    assert configuration["allow_external_assets"] is True

    resumed = _run_cli("--resume", checkpoint)

    assert resumed.returncode == 0, resumed.stderr
    assert "fingerprint_mismatch" not in resumed.stderr
