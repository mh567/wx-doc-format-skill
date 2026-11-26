from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from docx import Document


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TEMPLATE = ROOT / "assets" / "wx_template.docx"
sys.path.insert(0, str(SCRIPTS))

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
COMMENT_CODE = "WXDF-E-INPUT-COMMENTS"
REVISION_CODE = "WXDF-E-INPUT-TRACKED-CHANGES"
EVIDENCE_MISSING = "WXDF-E-AUDIT-EVIDENCE-MISSING"


def _environment(**extra: str) -> dict:
    environment = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    environment.pop("WX_DOC_LLM_ENHANCE", None)
    environment.pop("WX_DOC_FORMAT_RELEASE", None)
    environment.update(extra)
    return environment


def _convert(source: Path, tmp_path: Path, *extra: str, release: bool = False):
    output = tmp_path / "out" / "result.docx"
    report = tmp_path / "out" / "report.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "main.py"), "--input", str(source),
         "--output", str(output), "--template", str(TEMPLATE), "--report", str(report), *extra],
        env=_environment(**({"WX_DOC_FORMAT_RELEASE": "1"} if release else {})),
        capture_output=True, text=True,
    )
    return completed, output, report


def _base_docx(path: Path) -> Path:
    document = Document()
    document.add_paragraph("第一段正文")
    document.add_paragraph("第二段正文")
    document.sections[0].header.is_linked_to_previous = False
    document.sections[0].header.paragraphs[0].text = "页眉文字"
    document.save(str(path))
    return path


def _rewrite(path: Path, edits: dict) -> Path:
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    for part, edit in edits.items():
        entries[part] = edit(entries.get(part, b""))
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path


def _wrap_first_run(xml: bytes, wrapper: str) -> bytes:
    text = xml.decode("utf-8")
    index = text.index("<w:r>")
    end = text.index("</w:r>", index) + len("</w:r>")
    return (text[:index] + wrapper.replace("{run}", text[index:end]) + text[end:]).encode("utf-8")


def _insert_before_body_end(xml: bytes, fragment: str) -> bytes:
    text = xml.decode("utf-8")
    index = text.index("<w:sectPr")
    return (text[:index] + fragment + text[index:]).encode("utf-8")


def _with_comment(path: Path) -> Path:
    document = Document(str(path))
    document.add_comment(runs=document.paragraphs[0].runs, text="批注正文不应被转移", author="评审人", initials="P")
    document.save(str(path))
    return path


_INSERTED_RUN = '<w:ins w:id="901" w:author="作者" w:date="2026-01-01T00:00:00Z">{run}</w:ins>'
_DELETED_PARAGRAPH = (
    '<w:p><w:pPr><w:rPr><w:del w:id="902" w:author="作者" w:date="2026-01-01T00:00:00Z"/></w:rPr></w:pPr>'
    '<w:del w:id="903" w:author="作者" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>被删整段</w:delText></w:r></w:del></w:p>'
)
_WHOLE_PARAGRAPH_INSERTION = (
    '<w:p><w:pPr><w:rPr><w:ins w:id="904" w:author="作者" w:date="2026-01-01T00:00:00Z"/></w:rPr></w:pPr>'
    '<w:ins w:id="905" w:author="作者" w:date="2026-01-01T00:00:00Z"><w:r><w:t>新增整段</w:t></w:r></w:ins></w:p>'
)


def _with_body_revision(path: Path, fragment: str = _WHOLE_PARAGRAPH_INSERTION) -> Path:
    return _rewrite(path, {"word/document.xml": lambda xml: _insert_before_body_end(xml, fragment)})


def _assert_blocked(completed, output: Path, report: Path, *codes: str) -> dict:
    assert completed.returncode != 0, completed.stdout
    assert not output.exists()
    for code in codes:
        assert code in completed.stderr
    assert "Word" in completed.stderr and "WPS" in completed.stderr
    assert "另存" in completed.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert {item["code"] for item in data["diagnostics"]} >= set(codes)
    return data


def test_comments_block_conversion_with_guidance_and_no_output(tmp_path):
    source = _with_comment(_base_docx(tmp_path / "comments.docx"))
    completed, output, report = _convert(source, tmp_path)
    data = _assert_blocked(completed, output, report, COMMENT_CODE)
    assert REVISION_CODE not in completed.stderr
    assert "批注正文不应被转移" not in completed.stderr + json.dumps(data, ensure_ascii=False)
    assert "评审人" not in completed.stderr + json.dumps(data, ensure_ascii=False)


def test_body_whole_paragraph_revision_blocks(tmp_path):
    source = _with_body_revision(_base_docx(tmp_path / "ins.docx"))
    completed, output, report = _convert(source, tmp_path)
    data = _assert_blocked(completed, output, report, REVISION_CODE)
    assert COMMENT_CODE not in completed.stderr
    assert "新增整段" not in completed.stderr + json.dumps(data, ensure_ascii=False)


def test_body_deleted_paragraph_and_inline_insertion_block(tmp_path):
    source = _base_docx(tmp_path / "del.docx")
    _with_body_revision(source, _DELETED_PARAGRAPH)
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)
    source = _rewrite(_base_docx(tmp_path / "inline.docx"), {"word/document.xml": lambda xml: _wrap_first_run(xml, _INSERTED_RUN)})
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)


def test_non_body_story_revision_blocks(tmp_path):
    source = _rewrite(
        _base_docx(tmp_path / "header.docx"),
        {"word/header1.xml": lambda xml: _wrap_first_run(xml, _INSERTED_RUN)},
    )
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)

    footnotes = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes xmlns:w="{W}">'
        '<w:footnote w:id="1"><w:p><w:del w:id="9" w:author="a" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:delText>旧脚注</w:delText></w:r></w:del></w:p></w:footnote></w:footnotes>'
    ).encode("utf-8")
    source = _rewrite(_base_docx(tmp_path / "footnote.docx"), {"word/footnotes.xml": lambda _xml: footnotes})
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)


@pytest.mark.parametrize("element", [
    '<w:moveFrom w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z"><w:r><w:t>移走</w:t></w:r></w:moveFrom>',
    '<w:moveTo w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z"><w:r><w:t>移入</w:t></w:r></w:moveTo>',
    '<w:r><w:rPr><w:b/><w:rPrChange w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z"><w:rPr/></w:rPrChange></w:rPr><w:t>格式</w:t></w:r>',
])
def test_move_and_format_revision_elements_block(tmp_path, element):
    source = _with_body_revision(_base_docx(tmp_path / "move.docx"), f"<w:p>{element}</w:p>")
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)


def test_table_revision_blocks(tmp_path):
    table = (
        '<w:tbl><w:tblPr><w:tblPrChange w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z"><w:tblPr/></w:tblPrChange></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:trPr><w:ins w:id="2" w:author="a" w:date="2026-01-01T00:00:00Z"/></w:trPr>'
        '<w:tc><w:tcPr><w:cellIns w:id="3" w:author="a" w:date="2026-01-01T00:00:00Z"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>'
    )
    source = _with_body_revision(_base_docx(tmp_path / "table.docx"), table + "<w:p/>")
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)


def test_comments_and_revisions_report_both_categories(tmp_path):
    source = _with_body_revision(_with_comment(_base_docx(tmp_path / "both.docx")))
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, COMMENT_CODE, REVISION_CODE)


def test_prefix_independent_matching_uses_wordprocessingml_namespace(tmp_path):
    fragment = (
        f'<x:p xmlns:x="{W}"><x:ins x:id="7" x:author="a" x:date="2026-01-01T00:00:00Z">'
        '<x:r><x:t>别名前缀</x:t></x:r></x:ins></x:p>'
    )
    source = _with_body_revision(_base_docx(tmp_path / "prefix.docx"), fragment)
    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)


def test_foreign_namespace_elements_named_like_revisions_do_not_block(tmp_path):
    fragment = '<w:p><w:r><w:t>普通</w:t></w:r><o:ins xmlns:o="urn:example:other"/><o:comment xmlns:o="urn:example:other"/></w:p>'
    source = _with_body_revision(_base_docx(tmp_path / "foreign.docx"), fragment)
    completed, output, report = _convert(source, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()


def test_track_revisions_switch_and_rsid_without_records_continue(tmp_path):
    source = _base_docx(tmp_path / "switch.docx")

    def settings(xml: bytes) -> bytes:
        text = xml.decode("utf-8")
        index = text.index(">", text.index("<w:settings")) + 1
        return (text[:index] + '<w:trackRevisions/><w:rsids><w:rsidRoot w:val="00112233"/><w:rsid w:val="00112233"/></w:rsids>' + text[index:]).encode("utf-8")

    def rsid_paragraphs(xml: bytes) -> bytes:
        return xml.replace(b"<w:p>", b'<w:p w:rsidR="00112233" w:rsidRDefault="00112233">')

    _rewrite(source, {"word/settings.xml": settings, "word/document.xml": rsid_paragraphs})
    completed, output, report = _convert(source, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    assert "input_precheck" not in json.loads(report.read_text(encoding="utf-8"))


def test_empty_comments_part_without_records_continues(tmp_path):
    empty = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments xmlns:w="{W}"/>'.encode("utf-8")
    source = _rewrite(_base_docx(tmp_path / "emptycomments.docx"), {"word/comments.xml": lambda _xml: empty})
    completed, output, _report = _convert(source, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()


def test_blocked_run_keeps_existing_output_and_leaves_no_staging(tmp_path):
    source = _with_comment(_base_docx(tmp_path / "keep.docx"))
    output = tmp_path / "out" / "result.docx"
    output.parent.mkdir()
    output.write_bytes(b"previous delivery")
    completed, _output, report = _convert(source, tmp_path)
    assert completed.returncode != 0
    assert output.read_bytes() == b"previous delivery"
    leftovers = [path.name for path in output.parent.iterdir() if "staging" in path.name or path.suffix == ".tmp"]
    assert leftovers == []
    assert {"report.json", "result.docx"} <= {path.name for path in output.parent.iterdir()}
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "failed"


def test_release_report_keeps_stable_codes_and_separates_precheck_from_missing_audit(tmp_path):
    source = _with_body_revision(_with_comment(_base_docx(tmp_path / "release.docx")))
    completed, output, report = _convert(source, tmp_path, release=True)
    assert completed.returncode != 0 and not output.exists()
    data = json.loads(report.read_text(encoding="utf-8"))
    summary = data["public_summary"]
    assert summary["schema_version"] == "1.0"
    assert summary["status"] == "failed"
    assert COMMENT_CODE in summary["diagnostic_codes"] and REVISION_CODE in summary["diagnostic_codes"]
    assert EVIDENCE_MISSING not in summary["diagnostic_codes"]
    assert summary["manual_review_required"] is True
    assert summary["audits"] == {
        "audit": "not_run", "model_audit": "not_run",
        "saved_output_audit": "not_run", "appendix_preservation_audit": "not_run",
    }
    assert summary["delivery_status"] == "blocked"
    messages = {item["code"]: item.get("message", "") for item in data["diagnostics"]}
    assert "批注" in messages[COMMENT_CODE] and "修订" in messages[REVISION_CODE]
    assert all("Word" in text and "WPS" in text and "另存" in text for text in messages.values())
    assert "批注正文不应被转移" not in json.dumps(data, ensure_ascii=False)


def test_markdown_report_lists_precheck_diagnostics(tmp_path):
    source = _with_comment(_base_docx(tmp_path / "md.docx"))
    markdown = tmp_path / "out" / "report.md"
    completed, output, _report = _convert(source, tmp_path, "--report-md", str(markdown))
    assert completed.returncode != 0 and not output.exists()
    assert COMMENT_CODE in markdown.read_text(encoding="utf-8")


def test_fixed_guidance_text_has_no_dashes_or_contrast_phrasing():
    from wxdf.input_guard import GUIDANCE

    for text in GUIDANCE.values():
        assert "—" not in text and "--" not in text
        assert not re.search(r"不是.{0,20}而是", text)


def test_ai_conversion_is_blocked_before_any_request_or_llm_call(tmp_path):
    source = _with_comment(_base_docx(tmp_path / "ai.docx"))
    marker = tmp_path / "llm-called"
    script = tmp_path / "llm.py"
    script.write_text(f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x'); print('{{}}')\n", encoding="utf-8")
    completed, output, report = _convert(
        source, tmp_path, "--llm-enhance", "auto", "--llm-command", f"{sys.executable} {script}",
    )
    _assert_blocked(completed, output, report, COMMENT_CODE)
    assert not marker.exists()

    request_dir = tmp_path / "requests"
    completed, output, report = _convert(source, tmp_path, "--llm-enhance", "force-a", "--generate-requests", str(request_dir))
    _assert_blocked(completed, output, report, COMMENT_CODE)
    assert not (request_dir / "run.json").exists()
    assert not (request_dir / "llm_requests.jsonl").exists()


def test_review_mode_is_unchanged_for_documents_with_marks(tmp_path):
    source = _with_body_revision(_with_comment(_base_docx(tmp_path / "review.docx")))
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "main.py"), "--review", "--input", str(source), "--template", str(TEMPLATE)],
        env=_environment(), capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert COMMENT_CODE not in completed.stderr + completed.stdout


def test_resume_keeps_source_identity_protection_when_source_gained_marks(tmp_path):
    source = _base_docx(tmp_path / "resume.docx")
    request_dir = tmp_path / "requests"
    completed, output, _report = _convert(source, tmp_path, "--llm-enhance", "force-a", "--generate-requests", str(request_dir))
    run_file = request_dir / "run.json"
    assert completed.returncode == 0, completed.stderr
    assert run_file.is_file()
    before = run_file.read_bytes()
    _with_comment(source)
    resumed = subprocess.run(
        [sys.executable, str(SCRIPTS / "main.py"), "--resume", str(run_file)],
        env=_environment(), capture_output=True, text=True,
    )
    assert resumed.returncode != 0
    assert "fingerprint_mismatch" in resumed.stderr
    assert not output.exists()
    assert run_file.read_bytes() == before


def test_conversion_parses_the_snapshot_that_passed_precheck(tmp_path, monkeypatch):
    from wxdf import input_guard
    from wxdf.kernel import ConversionKernel, ConversionRequest

    source = _base_docx(tmp_path / "snapshot.docx")
    altered = Document(str(source))
    altered.add_paragraph("预检后替换的正文")
    replacement = tmp_path / "replacement.docx"
    altered.save(replacement)
    _with_body_revision(replacement)
    detect = input_guard.detect_input_marks

    def replace_after_precheck(store):
        codes = detect(store)
        replacement.replace(source)
        return codes

    monkeypatch.setattr(input_guard, "detect_input_marks", replace_after_precheck)
    output = tmp_path / "output.docx"
    result = ConversionKernel().convert(ConversionRequest(source, output, TEMPLATE))

    assert result.exit_code == 0, result.diagnostics
    paragraphs = [p.text for p in Document(str(output)).paragraphs]
    assert "第一段正文" in paragraphs
    assert "预检后替换的正文" not in paragraphs


def test_nonstandard_story_path_with_declared_content_type_blocks(tmp_path):
    source = _base_docx(tmp_path / "relocated.docx")
    with ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["word/stories/first.xml"] = _wrap_first_run(entries.pop("word/header1.xml"), _INSERTED_RUN)
    entries["[Content_Types].xml"] = entries["[Content_Types].xml"].replace(
        b"/word/header1.xml", b"/word/stories/first.xml",
    )
    rels = "word/_rels/document.xml.rels"
    entries[rels] = entries[rels].replace(b'="header1.xml"', b'="stories/first.xml"')
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)

    completed, output, report = _convert(source, tmp_path)
    _assert_blocked(completed, output, report, REVISION_CODE)
