from __future__ import annotations

from pathlib import Path


def _audit_status(report: dict, key: str) -> str:
    audit = report.get(key)
    if not isinstance(audit, dict):
        return "not_run"
    return "passed" if audit.get("passed") is True else "failed"


# An audit issue carries structured evidence instead of a message.  These are
# the fields worth printing for one, in the order a reader wants them.
_DIAGNOSTIC_DETAIL_FIELDS = ("node_id", "expected_level", "actual_level", "severity")


def _diagnostic_summary(diagnostic: dict) -> str:
    """Return a printable summary for a diagnostic, including evidence-only ones.

    An audit issue carries structured fields instead of a message, so a report
    that printed the message alone would show an empty line.
    """

    message = diagnostic.get("message")
    if message:
        return str(message)
    details = [
        f"{key}={diagnostic[key]}"
        for key in _DIAGNOSTIC_DETAIL_FIELDS
        if key in diagnostic
    ]
    return ", ".join(details) if details else "无附加说明"


def write_markdown_report(report: dict, path: Path) -> None:
    saved_output_audit = report.get("saved_output_audit")
    unexpected_styles = (
        saved_output_audit.get("unexpected_styles", [])
        if isinstance(saved_output_audit, dict)
        else []
    )
    lines = [
        "# WX 文档格式转换报告",
        "",
        "## 执行概览",
        f"- profile：{report.get('profile', 'unknown')}",
        f"- list_policy：{report.get('list_policy', 'unknown')}",
        f"- status：{report.get('status', 'unknown')}",
        f"- model_audit：{_audit_status(report, 'model_audit')}",
        f"- saved_output_audit：{_audit_status(report, 'saved_output_audit')}",
        f"- unexpected_styles：{unexpected_styles}",
        "",
        "## 诊断",
    ]
    diagnostics = report.get("diagnostics", [])
    if diagnostics:
        for diagnostic in diagnostics:
            if isinstance(diagnostic, dict):
                lines.append(
                    f"- {diagnostic.get('code', 'UNKNOWN')}：{_diagnostic_summary(diagnostic)}"
                )
            else:
                lines.append(f"- {diagnostic}")
    else:
        lines.append("- 无")
    heading_outline = report.get("heading_outline")
    entries = heading_outline.get("entries", []) if isinstance(heading_outline, dict) else []
    if entries:
        lines.extend(["", "## 大纲归一"])
        for entry in entries:
            lines.append(
                f"- {entry.get('node_id')}：源层级 {entry.get('source_level')} → 渲染层级 {entry.get('outline_level')}"
            )
    lines.extend([
        "",
        "## 风险提示",
    ])
    risk_warnings = report.get("risk_warnings", [])
    if risk_warnings:
        lines.extend(f"- {warning}" for warning in risk_warnings)
    else:
        lines.append("- 无")
    lines.extend(["", "## 内容型复核提示"])
    content_warnings = report.get("content_warnings", [])
    if content_warnings:
        lines.extend(f"- {warning}" for warning in content_warnings)
    else:
        lines.append("- 无")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
