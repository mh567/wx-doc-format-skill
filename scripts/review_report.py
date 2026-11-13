from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from wxdf.review_location import Finding, location_summary, location_view

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

# The dimension names in the order the score weighs them.  The sentence that
# explains the score reads this map rather than a fixed list, so it names the
# dimensions that were actually scored: a check that could not measure its
# surface leaves its dimension out of the score.
_DIMENSION_NAMES: dict[str, str] = {
    "format_conformance": "样式与元素格式",
    "heading_hierarchy": "大纲层级",
    "list_structure": "列表结构",
    "table_format": "表格",
    "toc_structure": "目录",
    "appendix_structure": "附录",
    "caption": "题注",
    "note_structure": "注",
    "numbering_page": "页码与编号",
    "ai_review": "AI 语义",
}


def _format_issues_by_severity(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"critical": [], "high": [], "medium": [], "low": []}
    for issue in issues:
        level = issue.get("level", "low")
        if level not in grouped:
            level = "low"
        grouped[level].append(issue)
    return grouped


def _format_issue(issue: Finding, index: int) -> list[str]:
    lines: list[str] = []
    title = issue.get("title", issue.get("code", "unknown"))
    lines.append(f"#### {index}. {title}")
    lines.append("")
    count, locations, omitted = location_view(issue)
    if len(locations) == 1 and count <= 1:
        lines.append(f"- **位置**：{locations[0]}")
    elif locations:
        lines.append(f"- **位置**（共 {count} 处）：")
        for location in locations:
            lines.append(f"  - {location}")
        if omitted:
            lines.append(f"  - （其余 {omitted} 处从略）")
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


def _action_line(issue: Finding) -> str:
    suggestion = str(issue.get("suggestion") or "")
    count, locations, _ = location_view(issue)
    if not locations:
        return suggestion
    return f"{location_summary(locations, count)}：{suggestion}"


def write_review_report(result: dict[str, Any], path: Path) -> None:
    path = Path(path)
    lines: list[str] = []
    score = result.get("score", 0)
    grade = result.get("grade", "unknown")

    lines.append("# 文档格式审查报告")
    lines.append("")
    lines.append("## 总体评分")
    lines.append("")
    lines.append(f"**总分：{score}/100** | 等级：**{grade}**")
    lines.append("")
    scored = result.get("dimension_scores") or {}
    scored_names = [name for key, name in _DIMENSION_NAMES.items() if key in scored]
    if scored_names:
        lines.append(
            f"评分是对{'、'.join(scored_names)}等 {len(scored_names)} 个维度"
            "按预定义权重加权的结果，每个问题按严重程度扣分（严重 25、高 15、中 8、低 3）；"
            "具体问题与整改建议见下。"
        )
    else:
        lines.append(
            "评分按预定义权重加权，每个问题按严重程度扣分（严重 25、高 15、中 8、低 3）；"
            "具体问题与整改建议见下。"
        )
    lines.append("")

    issues = result.get("issues", [])
    grouped = _format_issues_by_severity(issues)

    lines.append("| 等级 | 数量 |")
    lines.append("|------|------|")
    for level in ["critical", "high", "medium", "low"]:
        lines.append(f"| {SEVERITY_LABELS.get(level, level)} | {len(grouped.get(level, []))} |")
    lines.append("")

    if issues:
        lines.append("## 问题清单")
        lines.append("")
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
        action = _action_line(issue)
        if issue.get("level") in ("critical", "high"):
            immediate_actions.append(action)
        else:
            suggested_actions.append(action)

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
    score = str(result.get("score", 0))
    grade = html.escape(str(result.get("grade", "unknown")))
    issues = result.get("issues", [])

    try:
        numeric_score = float(score)
    except ValueError:
        numeric_score = 0.0
    score_color = "#28a745" if numeric_score >= 80 else "#ffc107" if numeric_score >= 60 else "#dc3545"
    score_html = html.escape(score)

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
    html_parts.append(f'<div class="score-box"><div class="score-value">{score_html}</div><div class="score-grade">等级：{grade}</div></div>')

    grouped = _format_issues_by_severity(issues)
    html_parts.append('<h2>问题统计</h2>')
    html_parts.append('<table class="summary-table"><tr><th>严重</th><th>高</th><th>中</th><th>低</th></tr>')
    counts = "".join(
        f'<td>{len(grouped.get(level, []))}</td>'
        for level in ("critical", "high", "medium", "low")
    )
    html_parts.append(f'<tr>{counts}</tr>')
    html_parts.append('</table>')

    if issues:
        html_parts.append('<h2>问题清单</h2>')
        for issue in issues:
            level = str(issue.get("level", "low"))
            if level not in {"critical", "high", "medium", "low"}:
                level = "low"
            title = html.escape(str(issue.get("title", issue.get("code", "unknown"))))
            count, locations, omitted = location_view(issue)
            suggestion = html.escape(str(issue.get("suggestion", "")))
            html_parts.append(f'<div class="issue-card issue-{level}">')
            html_parts.append(f'<div class="issue-title">{title}</div>')
            if len(locations) == 1 and count <= 1:
                html_parts.append(f'<div class="issue-location">位置：{html.escape(str(locations[0]))}</div>')
            elif locations:
                html_parts.append(f'<div class="issue-location">位置（共 {count} 处）：</div>')
                html_parts.append('<ul class="issue-locations">')
                for location in locations:
                    html_parts.append(f'<li>{html.escape(str(location))}</li>')
                if omitted:
                    html_parts.append(f'<li>（其余 {int(omitted)} 处从略）</li>')
                html_parts.append('</ul>')
            if suggestion:
                html_parts.append(f'<div class="issue-suggestion">建议：{suggestion}</div>')
            html_parts.append('</div>')

    compliant_items = result.get("compliant_items", [])
    if compliant_items:
        html_parts.append('<h2>合规项摘要</h2>')
        html_parts.append('<ul class="compliant-list">')
        for item in compliant_items:
            html_parts.append(f'<li>{html.escape(str(item))}</li>')
        html_parts.append('</ul>')

    risk_warnings = result.get("risk_warnings", [])
    if risk_warnings:
        html_parts.append('<h2>风险提示</h2>')
        for warning in risk_warnings:
            html_parts.append(f'<div class="warning-box">{html.escape(str(warning))}</div>')

    html_parts.append("</body></html>")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(html_parts), encoding="utf-8")
