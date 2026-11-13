---
name: wx-doc-format
description: 将 Markdown 或 DOCX 转换为模板化技术文档 DOCX，或审查文档是否符合模板要求并给出评分与整改报告。当用户要求技术文档格式、WX 格式转换、按模板整理标题、目录、列表、表格、题注与附录，或要求格式审查、格式检查、格式评分与整改建议时使用。
compatibility: Requires Python 3.8 or newer and a POSIX shell.
metadata:
  short-description: 技术文档格式转换与审查
---

# WX 文档格式

将 Markdown 或 DOCX 整理成 WX 模板格式的 DOCX，或审查现有 DOCX 的格式并给出评分与整改建议。

## 确定任务

1. 取得可读取的输入文件，确认输出位置。转换接受 Markdown 或 DOCX；审查接受 DOCX。缺少输入时请用户补充。
2. 根据用户请求选择模式。用户已指定时直接执行；只有转换或审查意图不明确时才询问。普通任务使用普通模式，用户要求 AI 增强时进入对应增强模式。

| 模式 | 用途 | 执行说明 |
|------|------|----------|
| 普通格式转换 | 按模板整理标题、正文、目录、列表、表格、题注和附录 | 下文「格式转换」 |
| AI 增强格式转换 | 辅助复核目录、列表、题注和审计问题 | 读取 [AI 增强转换协议](references/ai-conversion.md) |
| 普通格式审查 | 检查格式，输出评分、问题位置和整改建议 | 下文「格式审查」 |
| AI 增强格式审查 | 额外检查引用文件排序、术语与缩略语、列项用法 | 读取 [AI 增强审查协议](references/ai-review.md) |

普通模式由本地脚本处理。AI 增强由 agent 按协议提供响应，脚本校验后继续运行。

## 准备运行

将 `SKILL_ROOT` 解析为包含本文件的绝对目录。输入、输出、模板、报告和请求目录均使用绝对路径。默认模板为 `$SKILL_ROOT/assets/wx_template.docx`。

运行需要 Python 3.8 或更新版本和 POSIX shell。统一通过 `scripts/run.sh` 启动，由启动脚本选择解释器并准备依赖；需要指定解释器时设置 `WXDF_PYTHON`。离线环境使用包内依赖，准备失败时停止并报告错误，不自动联网补装。

运行前设置以下环境变量，使转换报告包含交付所需的公开摘要：

```bash
export WX_DOC_FORMAT_RELEASE=1
```

## 格式转换

```bash
sh "$SKILL_ROOT/scripts/run.sh" \
  --input "$INPUT_FILE" \
  --output "$OUTPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report "$REPORT_FILE"
```

题注默认按所在章节编号，例如 `图5-1`、`表6-2`。用户要求全文连续编号时追加 `--caption-numbering sequential`。附录题注保留已有的 `表A.1` 形态。

格式整理和输出审计由脚本完成。命令退出后，确认本次输出与报告已生成，再按「审计与交付」核查；失败时报告退出码与诊断信息。

## 格式审查

```bash
sh "$SKILL_ROOT/scripts/run.sh" \
  --review \
  --input "$INPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report-md "$REVIEW_REPORT_FILE"
```

审查保留原文件，输出百分制评分、问题位置和整改建议，覆盖样式与元素格式、大纲、列表、表格、目录、附录、题注、注、页码与编号。报告将同类问题合并展示，评分仍按实际出现次数计算。

`--report-md`、`--report-html`、`--report` 分别保存 Markdown、HTML、JSON 报告。不指定报告路径时只打印评分摘要。确认命令完成、评分已输出，且用户要求的报告文件已生成后交付。

## 审计与交付

普通转换与 AI 增强转换的默认核查范围相同：确认本次输出文件存在，读取本次转换报告，按下列规则决定交付。完成交付或报告停止原因后即结束核查，不自行追加独立格式审查、页面渲染、截图或 Computer Use。仅因输入或输出为 DOCX，不自动叠加其他文档技能的渲染验收流程；用户明确要求的额外检查按「按需检查」执行。

仅使用 JSON 报告中的 `public_summary` 判断公开交付结果。该摘要的 `schema_version` 必须为 `1.0`，并包含 `status`、`audits`、`unexpected_styles_count`、`manual_review_required` 和 `diagnostic_codes`。`audits` 覆盖 `audit`、`model_audit`、`saved_output_audit` 与 `appendix_preservation_audit` 四项。

1. `status=completed`、已执行审计均为 `passed`、`unexpected_styles_count` 为整数 `0` 且输出文件存在时，可以交付 DOCX。
2. `status=completed_with_warnings` 时，文档可按带警告交付，但需在交付说明中逐项列出 `audits` 内未通过或带警告的项以及 `diagnostic_codes`。暂不视为发布阻断，除非 `manual_review_required=true`。
3. `status=waiting_external` 时继续 [AI 增强转换协议](references/ai-conversion.md)，暂不交付。
4. `status=failed`、摘要缺失、摘要版本错误，任一审计未通过，或 `unexpected_styles_count` 为 `null`、非整数及非零值时停止交付，并报告 `diagnostic_codes`。
5. `manual_review_required=true` 时，在交付说明中逐项列出人工复核要求。

报告异常时依据状态与诊断码定位原因，必要时检查相关模型或 DOCX XML；截图与原生应用检查不替代失败的审计，也不解除交付阻断。

完成条件：已交付 DOCX、公开摘要结果与人工复核项，或已停止并给出稳定状态及诊断码。交付说明按实际执行情况注明验证范围，例如「转换审计通过，未执行页面视觉检查及 Word/WPS 原生应用验收」。

## 人工复核项与按需检查

### 人工复核项

以下为按适用情况告知用户的事项，不构成代理默认执行步骤。`manual_review_required=true` 时按「审计与交付」处理，列出具体要求并保留其阻断状态，不自动启动桌面应用或将复核项标记为已完成。

1. 目录页码与复杂域需在 Word 或 WPS 中更新，分节效果需在原生应用中复核。
2. 复杂嵌入对象及无法完整重建的文本框、形状、SmartArt、批注和修订记录需人工复核。

### 按需检查

| 触发条件 | 执行方式 | 完成条件 |
|----------|----------|----------|
| 用户明确要求页面视觉检查或成品预览 | 使用无界面渲染生成 PDF 或页面图片，检查要求范围内的分页、裁切、重叠和表格溢出 | 返回检查结论或预览，注明渲染器与实际检查范围 |
| 用户明确要求 Word/WPS 原生应用验收 | 操作指定应用，必要时使用 Computer Use，定向验证域刷新、交叉引用或实际分页等要求 | 记录应用与实际验证结果，未完成项明确列出 |

无界面渲染不可用时报告限制，不自动改用 Computer Use。结构审计、无界面渲染与原生应用验收分别表述，不将其中一项通过宣称为其余各项通过。
