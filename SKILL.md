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
4. LLM 增强模式按能力分批发起请求，当前提供的能力为 `toc_region_review`（目录区域复核）、`list_detect`（列表识别）、`caption_gen`（题注生成）和 `document_review`（审计后受限复核）。`document_review` 请求会附带 `review_packet`，修复范围以其 `targets` 为准。完成条件：能力集与请求契约明确。

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

通过外部命令调用 LLM。发布运行时需通过 `--llm-command` 提供该命令，由它读取标准输入的 JSON 请求并把 JSON 响应写到标准输出。

1. 生成请求：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --input "$INPUT_FILE" \
  --output "$OUTPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report "$REPORT_FILE" \
  --llm-enhance all \
  --llm-command "$LLM_COMMAND" \
  --generate-requests "$REQUEST_DIR"
```

完成条件：`run.json`（Checkpoint 2.0）与 `llm_requests.jsonl` 已生成，或报告状态已进入失败。

2. 逐行读取 `llm_requests.jsonl`，为尚未响应的请求生成顶层响应对象。每条请求包含 `request_id`、`capability`、`phase`、`items`、`model_revision`、`view_sha256`、`contract_versions`、`request_sha256`、`source_refs`、`budget` 和 `timeout_seconds`；`items[0]` 内的源 AST 视图版本为 `view_schema_version: "2.0"`，修订号即 `model_revision`。依据 `capability/phase` 及视图中的源证据产生候选。`document_review` 请求另有 `review_packet`（含 `baseline_revision`、`baseline_semantic_digest`、`targets`），其 `targets[].block_id` 与 `source_ref` 指明待修节点，`allowed_operations` 与 `property_scope`、`allowed_values` 限定可做的修复。保留请求文件原样。

每行响应使用以下任一 JSON 模板。以对应请求的值替换 `$request.*`，以本次运行中唯一的非空字符串替换 `$response_id`。替换保留原值类型：`base_revision` 与 `view_sha256` 必须与请求一致（`base_revision` 为整数）。将实际候选对象放入 `candidates`；普通增强没有可靠候选时可使用空数组，`document_review` 复核请求需要基于 `review_packet.targets` 给出有证据支持的修复候选才能通过。

候选集 2.0 形态（普通增强与复核通用）：

<!-- wxdf:candidate-set-response -->
```json
{
  "request_id": "$request.request_id",
  "response_id": "$response_id",
  "candidate_set_version": "2.0",
  "base_revision": "$request.model_revision",
  "view_sha256": "$request.view_sha256",
  "candidates": []
}
```

补丁 2.0 形态（复核请求可直接回决策）：

<!-- wxdf:patch-response -->
```json
{
  "request_id": "$request.request_id",
  "response_id": "$response_id",
  "patch_schema_version": "2.0",
  "base_revision": "$request.model_revision",
  "candidates": [],
  "decisions": []
}
```

下面是将目标节点确定为标题时的最小候选模板。普通增强从 Source AST 的 `document.blocks` 取得目标节点 ID 和完整 `source_ref`；复核从 `review_packet.targets` 取得 `block_id`、`source_ref` 及修复依据。将 `$target.node_id` 替换为该 ID，`$target.source_ref` 替换为完整对象，`$target.expected_level` 替换为源证据支持的标题级别整数。候选 ID 在响应内唯一。根据实际任务填写证据、理由及置信度；仅在当前能力和阶段需要调整标题语义时使用该候选。

<!-- wxdf:role-candidate -->
```json
{
  "candidate_id": "$candidate_id",
  "module_id": "$request.capability",
  "module_version": "2.0",
  "target_source_refs": ["$target.source_ref"],
  "target_node_ids": ["$target.node_id"],
  "proposal": {
    "operation": "update_role",
    "property_scope": ["role", "level"],
    "value": {"role": "heading", "level": "$target.expected_level"}
  },
  "positive_evidence": [{"kind": "source_structure"}],
  "negative_evidence": [],
  "scope": {"stages": ["role", "normalize", "render", "audit"]},
  "constraints": [],
  "confidence": 0.95,
  "rationale": "源结构证据支持此标题级别"
}
```

将替换完成的整个响应对象压缩为一行 JSON，写入同目录的 `llm_responses.jsonl`，文件内只存 JSON 行。后续等待阶段保留已有响应原样，仅为新增请求追加响应；重复提交同一请求时使用同一响应。完成条件：每个待处理请求恰有一个对应响应，全部占位值均已替换，`request_id`、整数 `base_revision` 和 `view_sha256` 与对应请求一致，且所有 `response_id` 唯一。

3. 恢复运行：

```bash
"$SKILL_ROOT/scripts/run.sh" --resume "$REQUEST_DIR/run.json"
```

状态再次为 `waiting_external` 时重复第 2 步和第 3 步。完成条件：状态进入完成、`completed_with_warnings` 或失败。发布运行时不会自行访问互联网模型服务。

## 审计与交付

仅使用 JSON 报告中的 `public_summary` 判断公开交付结果。该摘要的 `schema_version` 必须为 `1.0`，并包含 `status`、`audits`、`unexpected_styles_count`、`manual_review_required` 和 `diagnostic_codes`。`audits` 覆盖 `audit`、`model_audit`、`saved_output_audit` 与 `appendix_preservation_audit` 四项。

1. `status=completed`、已执行审计均为 `passed`、`unexpected_styles_count` 为整数 `0` 且输出文件存在时，可以交付 DOCX。
2. `status=completed_with_warnings` 时，文档可按带警告交付，但需在交付说明中逐项列出 `audits` 内未通过或带警告的项以及 `diagnostic_codes`。暂不视为发布阻断，除非 `manual_review_required=true`。
3. `status=waiting_external` 时继续 LLM 文件协议，暂不交付。
4. `status=failed`、摘要缺失、摘要版本错误，任一审计未通过，或 `unexpected_styles_count` 为 `null`、非整数及非零值时停止交付，并报告 `diagnostic_codes`。
5. `manual_review_required=true` 时，在交付说明中逐项列出人工复核要求。

完成条件：已交付 DOCX、公开摘要结果与人工复核项，或已停止并给出稳定状态及诊断码。

## 人工复核

1. 在 Word 或 WPS 中更新目录页码、分节和复杂域。
2. 复核复杂嵌入对象及无法完整重建的文本框、形状、SmartArt、批注和修订记录。
3. Kylin V10 ARM64 候选制品需在目标真机完成安装和转换冒烟。
4. 页面视觉效果仅在用户明确要求时使用无界面渲染产物检查，并如实说明验证范围。
