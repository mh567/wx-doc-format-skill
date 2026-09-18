from __future__ import annotations

from pathlib import Path
from typing import Any

SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "high": "高优先级",
    "medium": "中优先级",
    "low": "低优先级",
}

SEVERITY_ICONS: dict[str, str] = {
    "critical": "🔴 ",
    "high": "🟠 ",
    "medium": "🟡 ",
    "low": "🔵 ",
}


def _format_issues_by_severity(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"critical": [], "high": [], "medium": [], "low": []}
    for issue in issues:
        level = issue.get("level", "low")
        if level not in grouped:
            level = "low"
        grouped[level].append(issue)
    return grouped


def _format_issue(issue: dict[str, Any], index: int) -> list[str]:
    lines: list[str] = []
    title = issue.get("title", issue.get("code", "unknown"))
    lines.append(f"#### {index}. {title}")
    lines.append("")
    location = issue.get("location")
    if location:
        lines.append(f"- **位置**：{location}")
    suggestion = issue.get("suggestion")
    if suggestion:
        lines.append(f"- **建议**：{suggestion}")
    actual_style = issue.get("actual_style")
    if actual_style:
        lines.append(f"- **当前样式**：{actual_style}")
    if str(issue.get("source") or "") == "llm":
        lines.append(f"- **来源**：AI 语义检查（{issue.get('capability') or 'unknown'}）")
    evidence = issue.get("evidence")
    if evidence:
        lines.append(f"- **依据**：{evidence}")
    lines.append("")
    return lines


def write_review_report(result: dict[str, Any], path: Path) -> None:
    path = Path(path)
    lines: list[str] = []
    score = result.get("score", 0)
    grade = result.get("grade", "unknown")
    summary = result.get("summary", {})

    lines.append("# 文档格式审查报告")
    lines.append("")
    lines.append("## 总体评分")
    lines.append("")
    lines.append(f"**总分：{score}/100** | 等级：**{grade}**")
    lines.append("")
    lines.append(
        "评分是对样式、大纲层级、列表结构、表格、目录、附录、题注和注八个维度"
        "按预定义权重加权的结果，每个问题按严重程度扣分（严重 25、高 15、中 8、低 3）；"
        "具体问题与整改建议见下。"
    )
    lines.append("")

    lines.append("| 等级 | 数量 |")
    lines.append("|------|------|")
    lines.append(f"| 严重 | {summary.get('critical', 0)} |")
    lines.append(f"| 高 | {summary.get('high', 0)} |")
    lines.append(f"| 中 | {summary.get('medium', 0)} |")
    lines.append(f"| 低 | {summary.get('low', 0)} |")
    lines.append("")

    issues = result.get("issues", [])
    if issues:
        lines.append("## 问题清单")
        lines.append("")
        grouped = _format_issues_by_severity(issues)
        issue_index = 0
        for level in ["critical", "high", "medium", "low"]:
            group = grouped.get(level, [])
            if not group:
                continue
            icon = SEVERITY_ICONS.get(level, "")
            label = SEVERITY_LABELS.get(level, level)
            lines.append(f"### {icon}{label}问题（{len(group)} 项）")
            lines.append("")
            for issue in group:
                issue_index += 1
                lines.extend(_format_issue(issue, issue_index))

    lines.append("## 整改建议")
    lines.append("")
    immediate_actions = []
    suggested_actions = []
    for issue in issues:
        if issue.get("level") in ("critical", "high"):
            immediate_actions.append(issue.get("suggestion", ""))
        else:
            suggested_actions.append(issue.get("suggestion", ""))

    if immediate_actions:
        lines.append("### 立即修改")
        lines.append("")
        for index, action in enumerate(immediate_actions, 1):
            lines.append(f"{index}. {action}")
        lines.append("")

    if suggested_actions:
        lines.append("### 建议优化")
        lines.append("")
        for index, action in enumerate(suggested_actions, 1):
            lines.append(f"{index}. {action}")
        lines.append("")

    compliant_items = result.get("compliant_items", [])
    if compliant_items:
        lines.append("## 合规项摘要")
        lines.append("")
        for item in compliant_items:
            lines.append(f"- {item}")
        lines.append("")

    ai_review = result.get("ai_review") or {}
    if ai_review.get("status") not in {None, "not_requested"}:
        lines.append("## AI 语义检查")
        lines.append("")
        lines.append(f"- 状态：{ai_review.get('status')}")
        for name, info in sorted((ai_review.get("capabilities") or {}).items()):
            lines.append(f"- {name}：{info.get('status')}（{info.get('finding_count', 0)} 项）")
        for diagnostic in ai_review.get("diagnostics") or []:
            lines.append(f"- 诊断：{diagnostic.get('code')}（{diagnostic.get('message', '')}）")
        lines.append("")

    risk_warnings = result.get("risk_warnings", [])
    if risk_warnings:
        lines.append("## 风险提示")
        lines.append("")
        lines.append("以下项目需要人工复核：")
        lines.append("")
        for warning in risk_warnings:
            lines.append(f"- {warning}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_review_report_html(result: dict[str, Any], path: Path) -> None:
    path = Path(path)
    score = result.get("score", 0)
    grade = result.get("grade", "unknown")
    summary = result.get("summary", {})
    issues = result.get("issues", [])

    score_color = "#28a745" if score >= 80 else "#ffc107" if score >= 60 else "#dc3545"

    html_parts: list[str] = []
    html_parts.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>文档格式审查报告</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; color: #333; }
h1 { color: #1a1a1a; border-bottom: 2px solid #eee; padding-bottom: 10px; }
h2 { color: #2c3e50; margin-top: 30px; }
h3 { color: #34495e; }
.score-box { background: #f8f9fa; border-radius: 8px; padding: 20px; text-align: center; margin: 20px 0; }
.score-value { font-size: 48px; font-weight: bold; color: """ + score_color + """; }
.score-grade { font-size: 24px; color: #666; }
.summary-table { width: 100%; border-collapse: collapse; margin: 20px 0; }
.summary-table th, .summary-table td { border: 1px solid #ddd; padding: 10px; text-align: center; }
.summary-table th { background: #f8f9fa; }
.issue-card { border-left: 4px solid #ddd; padding: 12px; margin: 12px 0; background: #fafafa; }
.issue-critical { border-left-color: #dc3545; }
.issue-high { border-left-color: #fd7e14; }
.issue-medium { border-left-color: #ffc107; }
.issue-low { border-left-color: #6c7745; }
.issue-title { font-weight: bold; margin-bottom: 4px; }
.issue-location { color: #666; font-size: 14px; }
.issue-suggestion { margin-top: 8px; }
.compliant-list { list-style: none; padding: 0; }
.compliant-list li { padding: 8px; background: #d4edda; border-radius: 4px; margin: 4px 0; }
.warning-box { background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 12px; margin: 12px 0; }
</style>
</head>
<body>
""")

    html_parts.append("<h1>文档格式审查报告</h1>")
    html_parts.append(f'<div class="score-box"><div class="score-value">{score}</div><div class="score-grade">等级：{grade}</div></div>')

    html_parts.append('<h2>问题统计</h2>')
    html_parts.append('<table class="summary-table"><tr><th>严重</th><th>高</th><th>中</th><th>低</th></tr>')
    html_parts.append(f'<tr><td>{summary.get("critical", 0)}</td><td>{summary.get("high", 0)}</td><td>{summary.get("medium", 0)}</td><td>{summary.get("low", 0)}</td></tr>')
    html_parts.append('</table>')

    if issues:
        html_parts.append('<h2>问题清单</h2>')
        for issue in issues:
            level = issue.get("level", "low")
            title = issue.get("title", issue.get("code", "unknown"))
            location = issue.get("location", "")
            suggestion = issue.get("suggestion", "")
            html_parts.append(f'<div class="issue-card issue-{level}">')
            html_parts.append(f'<div class="issue-title">{title}</div>')
            if location:
                html_parts.append(f'<div class="issue-location">位置：{location}</div>')
            if suggestion:
                html_parts.append(f'<div class="issue-suggestion">建议：{suggestion}</div>')
            html_parts.append('</div>')

    compliant_items = result.get("compliant_items", [])
    if compliant_items:
        html_parts.append('<h2>合规项摘要</h2>')
        html_parts.append('<ul class="compliant-list">')
        for item in compliant_items:
            html_parts.append(f'<li>{item}</li>')
        html_parts.append('</ul>')

    risk_warnings = result.get("risk_warnings", [])
    if risk_warnings:
        html_parts.append('<h2>风险提示</h2>')
        for warning in risk_warnings:
            html_parts.append(f'<div class="warning-box">{warning}</div>')

    html_parts.append("</body></html>")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(html_parts), encoding="utf-8")
