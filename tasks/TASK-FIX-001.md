# TASK-FIX-001：修复 6 项测试中暴露的问题

## 状态

READY

## 优先级 🔴 高优（必修）

### F1. 修复 benchmark.py per-phase 统计 bug

问题：`collect_metrics` 用 `prev_total_applied` 累加差分计算 Phase B/C counts，但 phase_summaries 记录的是每个 phase 自身的计数，导致 Phase B applied 值可能大于 total_applied。

修复：直接从 `phase_summaries[i].applied_count` 读取各 phase 的 applied 数量，不要做差分。

文件：`scripts/benchmark.py` 的 `collect_metrics()` 函数。

### F2. MD 路径输出 parse_report

问题：`compute_suspicion_score` 依赖 `report["parse_report"]` 中的信号（suspect_visual_headings 等），但 MD 解析器 `md_pipeline.py` 不填充该字段，导致 MD 输入的 `auto` 模式永远不触发。

修复：在 `md_pipeline.py` 的 `parse_md_to_model()` 中，对以下场景输出 parse_report：
- **ambiguous_short_paragraphs**：正文段 ≤30 字且不以标点结尾的文本列表
- **inferred_headings**：从文本模式推断的标题（当前已有 heading_level_from_text 逻辑）
- **inferred_lists**：从文本模式推断的列表
- **unstyled_paragraphs**：MD 输入全算 unstyled（设 body 总数）

文件：`scripts/md_pipeline.py`

## 优先级 🟠 中优（重要）

### F3. 封面元信息识别增强

问题：`is_front_matter_text` 只检查居中对齐 + ≤80 字，漏掉了许多封面元信息模式。

修复：在 `text_utils.py` 的 `is_front_matter_text` 或 `looks_like_visual_heading` 中增加：
- 匹配 `^版本[：:]`、`^文档编号[：:]`、`^文件编号[：:]` 等前缀 → 强制排除
- 封面首几个 block 中连续居中短文本 → 直接判为 front matter
- 增加已排除的模式：`密级`、`状态`、`作者`、`日期`（这些已存在，确认覆盖）

文件：`scripts/text_utils.py`

### F4. 连续短段落 → 列表推断

问题：当 3+ 个相邻 body 段落都在 15-30 字且结构相似时，规则应将其推断为列表，减少 LLM 不必要的修正。

修复：在 `docx_pipeline.py` 的 `infer_docx_role` 或 `model_normalization.py` 的 `normalize_document_model_simple` 中增加：
- 检测连续 N 个（N≥3）短 body 段落（15-30 字、不以句号结尾、相邻段落长度差异 < 50%）
- 将此序列推断为 `list_item`（lower_letter_paren, level=0）
- 仅在 strict_normalize=True 时启用
- 不覆盖已显式标记的列表（source role 已是 list 的跳过）

文件：`scripts/model_normalization.py` 或 `scripts/docx_pipeline.py`

## 优先级 🟡 低优（改进）

### F5. LLM 超时分文档大小动态调整

问题：Phase B/C 的硬编码 30s 超时对大文档不够。

修复：在 `llm_enhancer.py` 的 `_resolve_llm_call` 或 `enhance_document_model` 中：
- 根据文档 block 数量动态设置超时：`timeout = max(30, block_count / 20)`
- 将超时值记录到 report 中

文件：`scripts/llm_enhancer.py`

### F6. XML tag 兼容性

问题：`build_role_overrides` 中的 `}w:p` / `}w:tbl` / `}w:sectPr` 匹配在直接创建 Document（非 save/load）时失效。

修复：确认 tag 匹配同时支持 `}p` 和 `}w:p`（已部分修复，检查是否遗漏）。

文件：`scripts/llm_enhancer.py`

## 禁止修改

- `scripts/main.py`
- `scripts/document_model.py`
- `tests/` 目录（本次不改）

## 验证命令

```bash
cd /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts
.venv/bin/python3 -m pytest ../tests/ -q
```