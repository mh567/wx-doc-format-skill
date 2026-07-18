---
name: wx-doc-format
description: Convert Markdown or DOCX documents into WX template-formatted .docx. Removes source covers, places a generated TOC first, normalizes one document title, classifies data tables and single-cell content containers, and runs a three-stage pipeline (parse → normalize → render), with optional LLM enhancement for ambiguous TOC review, list detection, and eligible data-table caption generation.
metadata:
  short-description: Convert MD/DOCX to template-formatted DOCX
---

# WX 文档格式

将 Markdown 或 DOCX 文档转换为模板格式的 Word (.docx) 文档。DOCX 先识别源封面与源目录，移除封面并提取唯一文档标题，再预分析 Word OOXML 编号，随后进入解析、规范化和模板渲染流水线。输出固定以唯一主目录开头，分页后从文档标题和正文开始。可选 LLM 能力用于模糊目录候选、模糊编号候选、语义列表和表格题注处理。

## 使用方式

当用户要求转换文档时，Agent 必须先弹出两个选项供用户选择：

1. **普通模式**（默认）：纯规则转换，不调用 LLM
2. **LLM 增强模式**：增加模糊目录复核、功能点列表识别和题注生成

用户选择后直接执行对应模式，无需额外确认。

## 一键安装

对任何支持 skill 的 Agent 说：

> 从 GitHub 仓库 mh567/wx-doc-format-skill 安装 wx-doc-format skill

更新时重复上述指令即可，Agent 会自动比对版本并拉取最新代码。

## 架构概览

```
输入 (MD / DOCX)
  │
  ▼
DOCX 源目录预处理
  │  toc_detector.py：确定性检测与候选边界
  │  toc_region_review：仅复核模糊候选（LLM 增强模式）
  ▼
DOCX 首部规范化
  │  front_matter.py：封面边界、标题提取、统一排除和顺序审计
  ▼
DOCX 编号预分析
  │  list_detector.py：源编号定义、层级、连续组与保护角色
  │  list_style_mapping.py：AST 层级到 WX 列表样式的统一映射
  │  list_detect：复核模糊编号候选（LLM 增强模式）
  ▼
DOCX 表格语义分类
  │  table_semantics.py：data、code_sample、callout、layout 与题注准入
  ▼
Step 1: 解析为 source AST
  │  scripts/md_pipeline.py / docx_pipeline.py
  │
  ▼
Step 2: 规范化 + LLM 增强（可选）
  │  model_normalization.py：清理手工编号、重定型、表格角色修正
  │  llm_enhancer.py：Capability 插拔架构：
  │    • toc_region_review：模糊源目录候选复核
  │    • list_detect：功能点列表识别
  │    • caption_gen：无题注表格自动生成题注文字
  │
  ▼
Step 3: 模板驱动渲染
  │  template_finalizer.py：格式收口、表格全框线、附录合并
  │  table_formatting.py：表格五层格式合同与幂等规范化
  │  audit.py：输出审计
  │
  ▼
输出: 样式合规的 .docx + JSON 报告
```

## CLI 用法

```bash
# 基础用法
python3 -m main \
  --input source.docx \
  --output output.docx \
  --template template.docx

# LLM 增强（模糊目录复核 + 功能点列表 + 题注生成）
--llm-enhance all

# 仅模糊目录复核
--llm-enhance toc_region_review

# 仅列表识别
--llm-enhance list_detect

# 仅题注生成
--llm-enhance caption_gen

# 导出 AST 调试
--ast model.json
--source-ast source.json

# 风险检测严格模式
--fail-on-risk
```

## LLM 增强能力

| 能力 | CLI | 说明 |
|------|-----|------|
| `toc_region_review` | `--llm-enhance toc_region_review` | 从确定性检测器给出的模糊候选中选择完整目录区间 |
| `list_detect` | `--llm-enhance list_detect` | 复核模糊 Word 编号候选，并识别缺少编号结构的语义列表 |
| `caption_gen` | `--llm-enhance caption_gen` | 根据上下文为无题注表格生成简短题注 |
| `all` | `--llm-enhance all` | 启用全部三项能力 |
| `off` | 默认 | 纯规则，不调用 LLM |

新增能力通过 `CapabilityConfig` 注册即可，无需修改核心调度逻辑。

### 列表处理边界

- 普通模式读取 `numId`、`abstractNumId`、`ilvl`、`numFmt`、`lvlText` 和起始值。
- 有效编号定义还需列表样式、连续同组段落或可见列表标记作为高置信度证据。
- Heading、Title、Caption、TOC、目录、题注、注释和公式样式属于保护角色。
- 孤立的普通样式编号段落作为模糊候选保留正文，并在报告中记录。
- 源 `numFmt` 和 `lvlText` 保存在 `source.numbering`，不直接决定 WX 一级、二级样式。
- AST `level=0` 的有序列表使用 `1.1一级列项-编号`，更深层有序列表使用 `2.1二级列项-有编号`。
- AST `level=0` 的无序列表使用 `1.2一级列项-无编号`，更深层无序列表使用 `2.2二级列项-无编号`。
- LLM 对模糊 OOXML 候选只能确认预分析建议的层级和 WX 目标列表类型，不能改变原文或编号边界。
- 直接渲染通过稳定 `source_position` 使用规范化 AST 的最终列表角色，避免重复推断。

## LLM 增强配置

用户选择 LLM 增强模式后，Agent 需将自己的 LLM 能力传递给 `main.py`。推荐使用文件协议分阶段流程，Agent 无需设置环境变量即可完成 LLM 调用。

### 方式一（推荐）：文件协议分阶段流程

```bash
# Step 1: 解析文档，生成 LLM 请求文件
python3 -m main \
  --input source.docx \
  --output output.docx \
  --template assets/wx_template.docx \
  --llm-enhance all \
  --generate-requests .wx-doc-format/
```

这将生成：

- `.wx-doc-format/run.json`：运行配置（源文件路径、CLI 参数等）
- `.wx-doc-format/llm_requests.jsonl`：LLM 请求列表（每行包含 `request_id`、`phase`、`capability`、`prompt`、`input_hash`）

Agent 读取 `llm_requests.jsonl`，逐行生成 `raw_response`（LLM 的 JSON patch 输出），写入 `llm_responses.jsonl` 后：

```bash
# Step 2: 恢复运行，验证并应用响应
python3 -m main --resume .wx-doc-format/run.json
```

系统自动执行：

1. 验证每个响应的 `input_hash` 完整性（防止提示词被篡改）
2. 校验 JSON patch schema（`schema_version`、`phase`、`decisions` 结构）
3. 校验每个 decision 的 `block_id`、`operation` 和目标类型
4. 应用高置信度 patch 到文档模型
5. 渲染最终输出

当存在模糊目录候选时，首次恢复会应用 `toc_region_review` 响应，然后追加 AST 阶段请求。Agent 补全新请求的响应后再恢复一次。高置信度目录会直接进入 AST 阶段。

### 方式二（兼容）：API Key

设置 `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY` 环境变量，无需额外配置。

### 方式三（兼容）：内置桥接脚本

Skill 内置 `scripts/llm_bridge.py`。Agent 设置 `LLM_COMMAND` 环境变量后即可使用：

```bash
LLM_COMMAND="codex exec" python3 -m main \
  --llm-command "python3 scripts/llm_bridge.py" \
  --llm-enhance all ...
```

`LLM_COMMAND` 设为 Agent 的 LLM 调用命令（如 `codex exec`、`hermes llm-call`），桥接脚本自动中转 stdin/stdout。

## 样式合规不变量

- 输出文档只使用模板样式，`unexpected_styles` 必须为空
- 输出只包含一个 WX 主目录域，原目录不在正文重复
- 输出不保留源封面，顶层顺序固定为主目录、分页、唯一文档标题、正文
- 标题文本不含手工编号
- 列表每章节重启
- 高置信度源列表、AST 列表和输出列表数量一致
- AST 列表层级与最终 WX 列表样式一致
- 表格单元格全部为 `表正文`
- 表格行保持 0.69 cm 最小行高和 `atLeast`
- 表格段落不保留覆盖模板的源间距、行距、缩进或字符直接格式
- 表格使用模板中的有效表格样式 ID，重复规范化不产生变化
- 单单元格自然语言说明框使用 `callout`，接口报文使用 `code_sample`，两者均不自动生成题注
- 只有 `data` 表允许自动生成题注，源文档已有人工题注继续保留
- 题注使用 SEQ 域代码（`SEQ Table` / `SEQ Figure`）
- 所有表格统一添加全框线

## 使用要求

- Python 3.10+
- python-docx、lxml

## 已知边界

- 文本框、形状、SmartArt、批注、修订记录不会完整重建
- Markdown 路径不会自动嵌入图片
- 页眉页脚、页码、复杂目录域需要人工复核
