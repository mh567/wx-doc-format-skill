---
name: wx-doc-format
description: 将 Markdown 或 DOCX 转换为模板化技术文档 DOCX，或审查文档是否符合模板要求并给出评分与整改报告。当用户要求技术文档格式、WX 格式转换、按模板整理标题、目录、列表、表格、题注与附录，或要求格式审查、格式检查、格式评分与整改建议时使用。
compatibility: Requires Python 3.8 or newer. Offline ARM64 packages cover standard CPython 3.8 through 3.14 with its lzma standard library; no venv, pip or ensurepip required.
metadata:
  short-description: 技术文档格式转换与审查
---

# WX 文档格式

将用户提供的 Markdown 或 DOCX 转换为模板化 DOCX，并依据公开审计摘要决定是否交付。

## 安装方式

### 在线安装

1. 把仓库地址 `https://github.com/mh567/wx-doc-format-skill` 交给 agent，并要求安装 `wx-doc-format` skill。支持 `cmd skills add` 的宿主可直接执行 `cmd skills add mh567/wx-doc-format-skill`。
2. agent 将仓库安装到自己的 skills 目录，目录名为 `wx-doc-format`，保证该目录根下是本文文件。
3. 安装完成后不需要手工执行任何脚本。用户提出转换或审查请求时，agent 按本文调用 `scripts/run.sh`；首次调用自动在 Skill 目录创建 `.venv` 并从 PyPI 安装依赖。

### 离线安装

1. 用 `python3 --version` 确认版本，从 Release 下载匹配平台的统一包：`wx-doc-format-<VERSION>-macos-arm64.zip` 或 `wx-doc-format-<VERSION>-linux-arm64.zip`。每包覆盖常规 CPython 3.8 至 3.14，并小于 14 MB（14,000,000 字节）。多解释器设备可设置 `WXDF_PYTHON` 为目标解释器的绝对路径。
2. 解压到 agent 的 skills 目录，得到 `wx-doc-format/`，该目录根下是本文文件。
3. 安装完成后不需要手工执行任何脚本。用户提出转换或审查请求时，agent 按本文调用 `scripts/run.sh`；首次调用自动从包内 `wheelhouse/` 解出依赖到 Skill 私有 `.deps/` 并直接加载，不访问网络或修改系统 Python。

目标机只需标准系统 Python，使用其自带的 `lzma` 标准库，无需 `venv`、`pip` 或 `ensurepip`。Linux ARM64 要求 glibc 2.28 或更新版本；macOS ARM64 要求 macOS 11 或更新版本。包内 `wheelhouse/wheels.tar.xz` 保存完整 wheel，启动脚本按清单选择并验证摘要，直接加载依赖。未覆盖的 Python 小版本、PyPy 与自由线程版不承诺离线运行；禁止为缺少 wheel 自动联网补装。

## 触发与模式

1. 确认输入为可读取的 Markdown 或 DOCX。缺少输入时请用户补充。完成条件：已取得一个明确的输入文件。
2. 请用户四选一。用户未指定时默认建议普通格式转换。完成条件：运行模式唯一确定。

   | 选项 | 用途 | 章节 |
   |------|------|------|
   | 普通格式转换 | 把文档转换为模板化 DOCX，全部本地确定性处理 | 「普通模式」 |
   | AI 增强格式转换 | 转换前用 LLM 复核目录区域、列表识别与题注 | 「LLM 文件协议」 |
   | 普通格式审查 | 审查文档是否符合模板要求，输出评分与整改报告；完全离线 | 「文档格式审查」 |
   | AI 增强格式审查 | 在普通审查之上增加语义检查（引用文件排序、术语与缩略语、列项用法） | 「AI 增强格式审查」 |

3. 普通格式转换与普通格式审查不需要任何模型；两个 AI 选项都需要代理提供响应：线上由 `--llm-command` 直接调用，离线按文件协议由代理填写响应后恢复。完成条件：已进入对应命令分支。
4. LLM 增强格式转换按能力分批发起请求，当前提供的能力为 `toc_region_review`（目录区域复核）、`list_detect`（列表识别）、`caption_gen`（题注生成）和 `document_review`（审计后受限复核）。`document_review` 请求会附带 `review_packet`，修复范围以其 `targets` 为准。AI 增强格式审查提供的语义检查见对应章节。完成条件：能力集与请求契约明确。

## 平台与启动

本 Skill 以 Python 源码分发，最低要求 Python 3.8。Windows 源码安装需在 Git Bash 或 WSL 等 POSIX shell 中运行，离线包仅提供 macOS 与 Linux ARM64。始终通过本 Skill 的启动脚本运行：

1. 首次运行 `"$SKILL_ROOT/scripts/run.sh"` 时自动准备依赖。离线包从 `wheelhouse/` 解出到私有 `.deps/`，直接加载，不调用安装工具或访问网络；源码安装从 PyPI 安装。
2. 离线包复用校验通过的私有缓存，始终优先于系统第三方包；源码安装的依赖已就绪时直接执行。
3. 离线环境只使用包内 wheel。禁止执行联网的 `pip install -r requirements.txt`。
4. 启动脚本按 `WXDF_PYTHON`、`$SKILL_ROOT/.venv`、`$SKILL_ROOT/scripts/.venv`、`python3`、`python3.14` 到 `python3.8`、`/usr/bin/python3` 的顺序选择解释器，并将所选解释器传给安装脚本。
5. 找不到 Python 3.8 或更新版本时输出 `WXDF-E-PYTHON-UNAVAILABLE`，退出码 3。
6. 源码自动安装依赖失败时输出 `WXDF-E-DEPENDENCY-BOOTSTRAP`；离线包无法准备依赖时输出 `WXDF-E-VENDOR-UNAVAILABLE`，退出码 3。
7. 包内 `wheelhouse/` 不覆盖当前解释器小版本时输出 `WXDF-E-VENDOR-UNAVAILABLE`，退出码 3。

完成条件：启动脚本找到可用解释器与依赖，或已按退出码 3 停止且没有尝试转换。

## 普通模式

将 `SKILL_ROOT` 解析为包含本文件的绝对目录。输入、输出、模板和报告均使用绝对路径：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --input "$INPUT_FILE" \
  --output "$OUTPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report "$REPORT_FILE"
```

题注默认按关联章节编号输出（`图5-1`、`表6-2`，章节号取所在一级标题）。需要全文连续编号（`图1`、`表1`）时追加 `--caption-numbering sequential`。附录题注始终保留源编号（`表A.1`）。

保存输出前，清理正文顶层仅含空白文本或软换行且没有有效段落属性的空段落。保留显式分页符、分节属性、图片、域、书签及其他对象；表格单元格中的必需段落不参与清理。清理后再执行输出审计。

完成条件：命令退出，输出文件与报告文件均已生成，输出中没有无效正文空段落；任一条件不满足时停止交付并报告退出码。

## 文档格式审查

审查模式不转换文档，而是审查一个输入 DOCX 是否符合模板要求，输出百分制评分和整改报告。使用独立入口，与普通模式、LLM 文件协议互斥：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --review \
  --input "$INPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report-md "$REVIEW_REPORT_FILE"
```

`--report`、`--report-md`、`--report-html` 分别写出 JSON、Markdown 与 HTML 报告；不指定报告路径时只打印评分摘要。审查覆盖样式与元素格式、大纲层级、列表结构（含每章节重启）、表格格式要求、目录、附录、题注、注、页码与编号九个维度，逐项给出问题分级与整改建议。报告把同一类问题合并为一条，列出每处位置，位置用「章节路径 + 段落/表格/图片序号」表达（如「第二章 系统设计 > 2.3 部署要求 · 第1张表格」），措辞不含内部术语。

评分仍按每处问题逐条计算，合并只影响问题清单的展示；`summary` 保留出现次数口径，另给 `issue_types` 表示合并后的条目数。

元素格式按共享格式合同比对，流程与文档使用哪些样式无关：先按**角色**识别每个段落（正文、几级标题、题注、注、列项、附录标题、公式），识别依据语义证据（大纲级别、编号、注/题注/附录语义）与样式名（模板名与通用名）以及文本特征；再解析该段落的**有效格式值**（docDefaults → 样式链 → 编号级别 → 段落直接属性），与该角色的规格期望值比对，并标明偏差来自文档默认、某个样式、编号级别还是直接格式。因此使用 Word 默认样式或自定义样式的文档同样能报出格式问题，不依赖转换器。

页码与编号维度检查三项，期望形状同样取自模板：分节与页码格式（前置节罗马数字、正文节阿拉伯数字并从 1 重编）、公式编号连续性与居中/编号右对齐、图表编号全文统一（附录题注按 `表A.1` 形态单独成组）。完成条件：命令退出且评分与问题摘要已输出；需要报告时对应文件已生成。

## AI 增强格式审查

在普通审查之上增加三项语义检查。它们判断的是脚本读不出的规则，请求与响应各自一份文件，与转换侧的 LLM 文件协议分开：

| 能力 | 相位 | 检查内容 |
|------|------|----------|
| `citation_order_review` | D | 引用文件／依据文件是否按「国标、国军标、行业标、团体标、企标、国际标、规章制度、其他技术文件」排序；标准号与数字之间是否有空格；是否与正文引用一一对应 |
| `glossary_review` | E | 术语是否按条排列、中文优先；缩略语是否按数字、字母顺序；缩略语一章是否误写定义 |
| `list_prose_review` | F | 列项是否被当作标题使用；列项内是否出现句号 |

两段式流程。生成请求：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --review \
  --input "$INPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report-md "$REVIEW_REPORT_FILE" \
  --llm-enhance all \
  --generate-requests "$REQUEST_DIR"
```

完成条件：`review_requests.jsonl` 与 `review_run.json` 已生成，或报告状态已进入失败。

逐行读取 `review_requests.jsonl`，为每个请求写出一行响应到同目录的 `review_responses.jsonl`。请求含 `request_id`、`capability`、`phase`、`task`、`allowed_codes`、`items` 和 `view_sha256`；`items` 是该能力可判断的文本条目。响应形态：

```json
{
  "request_id": "$request.request_id",
  "response_id": "$response_id",
  "view_sha256": "$request.view_sha256",
  "findings": [
    {
      "code": "$candidate_code",
      "level": "medium",
      "location": "出现位置",
      "evidence": "判断依据",
      "suggestion": "整改建议"
    }
  ]
}
```

`code` 必须取自该请求的 `allowed_codes`，`level` 必须为 `critical` / `high` / `medium` / `low`，`location` 不得为空，`view_sha256` 必须与请求一致。没有发现问题时 `findings` 用空数组。违反任何一条的响应会被记为 `llm_response_invalid` 诊断，不产生问题条目。完成条件：每个待处理请求恰有一个对应响应，且占位值均已替换。

恢复：

```bash
"$SKILL_ROOT/scripts/run.sh" --review --resume "$REQUEST_DIR/review_run.json"
```

恢复会校验输入与模板的摘要与生成请求时一致，不一致即以 `REVIEW_SOURCE_CHANGED` 停止，避免把针对另一份文档的回答套用上来。完成条件：命令退出且评分、问题摘要与 AI 语义检查状态已输出；需要报告时对应文件已生成。

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

#### 题注生成候选

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

同一目标只提供一个新增题注候选。续跑后按「审计与交付」核查实际输出与报告；候选建议不能替代成功转换的 DOCX。截图与页面检查仅按「按需检查」的触发条件执行。

3. 恢复运行：

```bash
"$SKILL_ROOT/scripts/run.sh" --resume "$REQUEST_DIR/run.json"
```

状态再次为 `waiting_external` 时重复第 2 步和第 3 步。完成条件：状态进入完成、`completed_with_warnings` 或失败。发布运行时不会自行访问互联网模型服务。

## 审计与交付

普通转换与 AI 增强转换的默认核查范围相同：确认本次输出文件存在，读取本次转换报告，按下列规则决定交付。完成交付或报告停止原因后即结束核查，不自行追加独立格式审查、页面渲染、截图或 Computer Use。仅因输入或输出为 DOCX，不自动叠加其他文档技能的渲染验收流程；用户明确要求的额外检查按「按需检查」执行。

仅使用 JSON 报告中的 `public_summary` 判断公开交付结果。该摘要的 `schema_version` 必须为 `1.0`，并包含 `status`、`audits`、`unexpected_styles_count`、`manual_review_required` 和 `diagnostic_codes`。`audits` 覆盖 `audit`、`model_audit`、`saved_output_audit` 与 `appendix_preservation_audit` 四项。

1. `status=completed`、已执行审计均为 `passed`、`unexpected_styles_count` 为整数 `0` 且输出文件存在时，可以交付 DOCX。
2. `status=completed_with_warnings` 时，文档可按带警告交付，但需在交付说明中逐项列出 `audits` 内未通过或带警告的项以及 `diagnostic_codes`。暂不视为发布阻断，除非 `manual_review_required=true`。
3. `status=waiting_external` 时继续 LLM 文件协议，暂不交付。
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
