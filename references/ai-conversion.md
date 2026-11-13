# AI 增强格式转换

执行前按 [SKILL.md](../SKILL.md) 准备路径与运行环境。使用下列文件协议由 agent 逐批处理请求。若宿主已有可用的模型命令，也可通过 `--llm-command` 传入，由命令读取标准输入的 JSON 请求并把 JSON 响应写到标准输出。

增强能力包括 `toc_region_review`（目录区域复核）、`list_detect`（列表识别）、`caption_gen`（题注生成）和 `document_review`（审计后受限复核）。按请求中的能力、任务与源证据处理；复核修复范围以 `review_packet.targets` 为准。

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

## 题注生成候选

`caption_gen` 根据图片、表格与相邻文字提供名称，编号交给渲染器。正文按章节编号，例如 `表 9-1 设备接入核对表`，附录保留已有的 `表A.1` 编号。相邻且完全相同的重复题注只呈现一次，原始来源仍保留在处理记录中；不同图表的同名题注分别保留。

已有题注节点时，用 `update_node_content` 更新其名称。缺少题注时，用 `create_node` 新增段落，候选的 `target_node_ids` 必须为新题注的 ID。`value.parent_id` 取源图表在 `document.children` 中的父节点，`anchor_node_id` 指向源图表，图题注放在 `after`，表题注放在 `before`。`target_source_refs` 使用源图表的完整引用，新节点通过 `generated_by` 与 `generation_basis` 记录生成依据，不能伪造源引用。

图片对象的 `block_type` 是 `image`，图题注的 `caption_type` 必须是 `figure`。下面是新增图题注的 `proposal.value` 示例，候选其余字段沿用上面的 2.0 形态：

```json
{
  "node_id": "$new_caption_id",
  "parent_id": "$target_parent_id",
  "anchor_node_id": "$target_image_id",
  "anchor_position": "after",
  "node": {
    "block_type": "paragraph",
    "role": "caption",
    "caption_type": "figure",
    "target_id": "$target_image_id",
    "inline_content": [{"kind": "text", "text": "安全事件处理流程", "marks": []}],
    "generated_by": "caption_gen",
    "generation_basis": ["$target_image_id"]
  }
}
```

同一目标只提供一个新增题注候选。续跑后按「审计与交付」核查实际输出与报告；候选建议不能替代成功转换的 DOCX。截图与页面检查仅按 [SKILL.md 的按需检查](../SKILL.md#按需检查) 执行。

3. 恢复运行：

```bash
"$SKILL_ROOT/scripts/run.sh" --resume "$REQUEST_DIR/run.json"
```

状态再次为 `waiting_external` 时重复第 2 步和第 3 步。完成条件：状态进入完成、`completed_with_warnings` 或失败。完成后返回 [SKILL.md 的审计与交付](../SKILL.md#审计与交付)，依据本次报告决定交付。
