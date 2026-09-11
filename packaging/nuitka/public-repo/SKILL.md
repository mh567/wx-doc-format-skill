---
name: wx-doc-format
description: 将 Markdown 或 DOCX 转换为模板化技术文档 DOCX。当用户要求技术文档格式、WX 格式转换，或要求按模板整理标题、目录、列表、表格、题注和附录时使用。
metadata:
  short-description: 转换为模板化技术文档 DOCX
---

# WX 文档格式

将用户提供的 Markdown 或 DOCX 转换为模板化 DOCX，并依据公开审计摘要决定是否交付。

## 触发与模式

1. 确认输入为可读取的 Markdown 或 DOCX。缺少输入时请用户补充。完成条件：已取得一个明确的输入文件。
2. 用户已指定普通模式或 LLM 增强模式时直接采用。用户未指定时请其二选一，默认建议普通模式。完成条件：运行模式唯一确定。
3. 普通模式执行本地确定性转换。LLM 增强模式转到“LLM 文件协议”。完成条件：已进入对应命令分支。

## 平台与启动

公开发布目标为 macOS ARM64 和 Kylin V10 ARM64。始终通过本 Skill 的启动脚本运行：

1. macOS 状态为 `accepted` 时继续。
2. Kylin 状态为 `candidate` 时，启动脚本输出 `WXDF-W-RUNTIME-CANDIDATE` 后继续。交付时保留真机复核项。
3. 平台状态为 `unavailable`，平台条目缺失，状态未知或 manifest 损坏时，启动脚本以退出码 3 停止。保留原始文件并向用户报告稳定错误码。
4. 在线仓库安装首次运行时下载当前版本制品并校验 SHA256。离线安装包直接使用包内运行时，不执行下载。

完成条件：启动脚本接受当前平台，或已按退出码 3 停止且没有尝试转换。

## 普通模式

将 `SKILL_ROOT` 解析为包含本文件的绝对目录。输入、输出、模板和报告均使用绝对路径：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --input "$INPUT_FILE" \
  --output "$OUTPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report "$REPORT_FILE"
```

完成条件：命令退出，输出文件与报告文件均已生成；任一文件缺失时停止交付并报告退出码。

## LLM 文件协议

1. 生成请求：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --input "$INPUT_FILE" \
  --output "$OUTPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report "$REPORT_FILE" \
  --llm-enhance all \
  --generate-requests "$REQUEST_DIR"
```

完成条件：`run.json` 与 `llm_requests.jsonl` 已生成，或报告状态已进入失败。

2. 逐行处理 `llm_requests.jsonl`。每个响应保留原请求的 `protocol_version`、`request_id` 和 `input_hash`，模型原始 JSON 文本写入 `raw_response`。将响应写入同目录的 `llm_responses.jsonl`。完成条件：每个待处理请求都有唯一响应。

3. 恢复运行：

```bash
"$SKILL_ROOT/scripts/run.sh" --resume "$REQUEST_DIR/run.json"
```

状态再次为 `waiting_external` 时重复第 2 步和第 3 步。完成条件：状态进入完成或失败。发布运行时不会自行访问互联网模型服务。

## 审计与交付

仅使用 JSON 报告中的 `public_summary` 判断公开交付结果。该摘要的 `schema_version` 必须为 `1.0`，并包含 `status`、`pipeline`、`profile`、`list_policy`、`audits`、`unexpected_styles_count`、`manual_review_required` 和 `diagnostic_codes`。

1. `status=completed`、已执行审计均为 `passed`、`unexpected_styles_count` 为整数 `0` 且输出文件存在时，可以交付 DOCX。
2. `status=waiting_external` 时继续 LLM 文件协议，暂不交付。
3. `status=failed`、摘要缺失、摘要版本错误、审计失败，或 `unexpected_styles_count` 为 `null`、非整数及非零值时停止交付，并报告 `diagnostic_codes`。
4. `manual_review_required=true` 时，在交付说明中逐项列出人工复核要求。

完成条件：已交付 DOCX、公开摘要结果与人工复核项，或已停止并给出稳定状态及诊断码。

## 人工复核

1. 在 Word 或 WPS 中更新目录页码、分节和复杂域。
2. 复核复杂嵌入对象及无法完整重建的文本框、形状、SmartArt、批注和修订记录。
3. Kylin V10 ARM64 候选制品需在目标真机完成安装和转换冒烟。
4. 页面视觉效果仅在用户明确要求时使用无界面渲染产物检查，并如实说明验证范围。
