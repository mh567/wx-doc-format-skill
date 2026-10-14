from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.auditing import check_saved_output as actual_saved_audit
from wxdf.delivery import audit_state
from wxdf.kernel import ConversionKernel, ConversionRequest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


def test_advisory_audit_false_without_issue_is_still_reported_as_issues() -> None:
    audits = {
        "model": {"passed": True, "issues": []},
        "saved_output": {
            "passed": True,
            "issues": [],
            "advisory_audits": {"document_format": {"passed": False, "issues": []}},
        },
        "appendix": {"passed": True, "issues": []},
    }
    assert audit_state(audits) == "issues"


def test_missing_core_audit_is_reported_as_incomplete() -> None:
    audits = {"model": {"passed": True}, "saved_output": {"passed": True}}
    assert audit_state(audits) == "incomplete"


def _request(tmp_path: Path, policy: str) -> ConversionRequest:
    source = tmp_path / "source.md"
    source.write_text("# Delivery test\n\n## Section\n\nA paragraph for audit policy.\n", encoding="utf-8")
    return ConversionRequest(
        input_path=source,
        output_path=tmp_path / f"{policy}.docx",
        template_path=TEMPLATE,
        options={"delivery_policy": policy},
    )


@pytest.mark.parametrize("policy", ["strict", "desktop"])
def test_audit_failure_policy_keeps_auditable_status_and_delivery(tmp_path: Path, monkeypatch, policy: str) -> None:
    from wxdf import auditing

    def failed(*args, **kwargs):
        result = actual_saved_audit(*args, **kwargs)
        return {**result, "passed": False, "issues": [*result.get("issues", []), {"code": "inline_content_changed", "node_id": "block-7"}]}

    monkeypatch.setattr(auditing, "check_saved_output", failed)
    result = ConversionKernel().convert(_request(tmp_path, policy))

    if policy == "desktop":
        assert result.status == "completed_with_warnings"
        assert result.report["audit_status"] == "issues"
        assert result.report["delivery_status"] == "published"
        assert Path(result.artifacts["docx"]).is_file()
        finding = next(item for item in result.report["audit_findings"] if item["code"] == "inline_content_changed")
        assert finding["block_id"] == "block-7"
        assert finding["impact"] == "possible_content_change"
        assert finding["certainty"] == "suspected"
    else:
        assert result.status == "failed"
        assert result.report["delivery_status"] == "blocked"
        assert result.report["conversion_status"] == "completed"


def test_desktop_audit_exception_is_reported_as_incomplete_and_does_not_block(tmp_path: Path, monkeypatch) -> None:
    from wxdf import auditing

    def explode(*_args, **_kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(auditing, "check_saved_output", explode)
    result = ConversionKernel().convert(_request(tmp_path, "desktop"))

    assert result.status == "completed_with_warnings"
    assert result.report["audit_status"] == "incomplete"
    assert result.report["delivery_status"] == "published"
    assert any(item.get("code") == "audit_exception" for item in result.report["audit_findings"])
    assert Path(result.artifacts["docx"]).is_file()


def test_desktop_rejects_corrupt_rendered_docx(tmp_path: Path, monkeypatch) -> None:
    from wxdf import rendering

    def corrupt(_normalized, output_path, **_kwargs):
        Path(output_path).write_bytes(b"not a zip package")
        return {"render_plan": {"operations": []}, "render_map": {}, "diagnostics": []}

    monkeypatch.setattr(rendering, "render", corrupt)
    result = ConversionKernel().convert(_request(tmp_path, "desktop"))

    assert result.status == "failed"
    assert result.report["delivery_status"] == "blocked"
    assert any("docx_unreadable" in item for item in result.report["delivery_decision"]["hard_blocks"])
    assert not (tmp_path / "desktop.docx").exists()


def test_desktop_source_facts_integrity_failure_remains_a_hard_block(tmp_path: Path, monkeypatch) -> None:
    from wxdf import native_stages

    original = native_stages._native_transaction

    def mutate_after_transaction(context):
        result = original(context)
        context.artifacts["source_facts"]["injected_mutation"] = True
        return result

    monkeypatch.setattr(native_stages, "_native_transaction", mutate_after_transaction)
    result = ConversionKernel().convert(_request(tmp_path, "desktop"))

    assert result.status == "failed"
    assert result.report["source_facts_immutable"] is False
    assert "SOURCE_FACTS_MUTATED" in result.report["delivery_decision"]["hard_blocks"]
