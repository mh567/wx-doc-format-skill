---
name: wx-doc-format
description: Convert Markdown or DOCX documents into WX template-formatted .docx. Three-stage pipeline (parse → normalize → render) with optional LLM enhancement for list detection and caption generation.
metadata:
  short-description: Convert MD/DOCX to template-formatted DOCX
---

# WX 文档格式

将 Markdown 或 DOCX 文档转换为模板格式的 Word (.docx) 文档。三段式流水线（解析 → 规范化 → 模板渲染），输出样式严格来自模板。支持可选的 LLM 语义增强，自动识别功能点列表、生成表格题注。

## 使用方式

当用户要求转换文档时，Agent 必须先弹出两个选项供用户选择：

1. **普通模式**（默认）— 纯规则转换，不调用 LLM
2. **LLM 增强模式** — 启用功能点列表识别和题注生成

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
Step 1: 解析为 source AST
  │  scripts/md_pipeline.py / docx_pipeline.py
  │
  ▼
Step 2: 规范化 + LLM 增强（可选）
  │  model_normalization.py — 清理手工编号、重定型、表格角色修正
  │  llm_enhancer.py — Capability 插拔架构：
  │    • list_detect — 功能点列表识别
  │    • caption_gen — 无题注表格自动生成题注文字
  │
  ▼
Step 3: 模板驱动渲染
  │  template_finalizer.py — 格式收口、表格全框线、附录合并
  │  audit.py — 输出审计
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

# LLM 增强（功能点列表 + 题注生成）
--llm-enhance all

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
| `list_detect` | `--llm-enhance list_detect` | 识别规则遗漏的功能点列表段落 |
| `caption_gen` | `--llm-enhance caption_gen` | 根据上下文为无题注表格生成简短题注 |
| `all` | `--llm-enhance all` | 两者都启用 |
| `off` | 默认 | 纯规则，不调用 LLM |

新增能力通过 `CapabilityConfig` 注册即可，无需修改核心调度逻辑。

## 样式合规不变量

- 输出文档只使用模板样式，`unexpected_styles` 必须为空
- 标题文本不含手工编号
- 列表每章节重启
- 表格单元格全部为 `表正文`
- 题注使用 SEQ 域代码（`SEQ Table` / `SEQ Figure`）
- 所有表格统一添加全框线

## 使用要求

- Python 3.10+
- python-docx、lxml
- macOS：运行 `scripts/bootstrap_macos_lxml.sh` 创建隔离环境

## 已知边界

- 文本框、形状、SmartArt、批注、修订记录不会完整重建
- Markdown 路径不会自动嵌入图片
- 页眉页脚、页码、复杂目录域需要人工复核
