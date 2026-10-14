from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from main import _native_checkpoint_configuration, public_report_summary, report_for_output


def test_cli_accepts_desktop_policy_and_reports_it(tmp_path):
    source = tmp_path / "sample.md"
    source.write_text("# Delivery\n\nBody text.\n", encoding="utf-8")
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "main.py"), "--input", str(source),
         "--output", str(tmp_path / "output.docx"), "--template",
         str(ROOT / "assets" / "wx_template.docx"), "--report", str(report),
         "--delivery-policy", "desktop"],
        env={**os.environ, "PYTHONPATH": str(SCRIPTS)}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text())["delivery_policy"] == "desktop"


def test_public_summary_keeps_incomplete_audit_and_safe_delivery_findings():
    report = {
        "status": "completed_with_warnings", "delivery_policy": "desktop",
        "conversion_status": "completed", "audit_status": "incomplete",
        "delivery_status": "published",
        "saved_output_audit": {"passed": None, "status": "incomplete", "unexpected_styles": []},
        "audit_findings": [{"code": "audit_check_exception", "category": "audit",
                            "severity": "warning", "certainty": "unknown",
                            "block_id": "node-1", "location": "Paragraph 3",
                            "message": "Check incomplete", "source_ast": {"private": True},
                            "traceback": "private-path", "internal_path": "/private/source.py"}],
    }
    summary = public_report_summary(report)
    assert summary["audits"]["saved_output_audit"] == "incomplete"
    assert summary["audit_status"] == "incomplete"
    assert summary["delivery_status"] == "published"
    assert summary["manual_review_required"] is True
    assert summary["audit_findings"][0]["location"] == "Paragraph 3"
    assert not {"source_ast", "traceback", "internal_path"}.intersection(summary["audit_findings"][0])
    released = report_for_output(report, release=True)
    assert released["public_summary"] == summary


def test_release_delivery_findings_do_not_expose_arbitrary_message_or_location():
    report = {
        "status": "completed_with_warnings",
        "saved_output_audit": {"passed": False, "unexpected_styles": []},
        "audit_findings": [{"code": "audit_exception", "message": "/private/source.py secret",
                            "location": "/Users/private/person", "block_id": "/private/internal"}],
    }
    released = report_for_output(report, release=True)
    for findings in (released["audit_findings"], released["public_summary"]["audit_findings"]):
        assert "location" not in findings[0]
        assert "block_id" not in findings[0]
        assert "/private" not in json.dumps(findings)
        assert "secret" not in json.dumps(findings)


def test_checkpoint_freezes_policy_and_keeps_legacy_strict_identity(tmp_path):
    args = SimpleNamespace(
        input=tmp_path / "input.md", template=None, report=None, report_md=None,
        ast=None, source_ast=None, fail_on_risk=False, llm_enhance="off", llm_hint=None,
        strict_normalize=True, delivery_policy="desktop", table_row_height_cm=0.7,
        table_row_height_rule="at-least", caption_numbering="chapter",
    )
    assert _native_checkpoint_configuration(args, output_path=tmp_path / "out.docx")["delivery_policy"] == "desktop"
    args.delivery_policy = "strict"
    args._legacy_delivery_configuration = True
    assert "delivery_policy" not in _native_checkpoint_configuration(args, output_path=tmp_path / "out.docx")
