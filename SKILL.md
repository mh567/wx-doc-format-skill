---
name: wx-doc-format
description: Convert Markdown or DOCX documents into WX template-formatted .docx. Three-stage pipeline (parse → normalize → render) with LLM semantic enhancement, style compliance auditing, and full audit reports.
metadata:
  short-description: Convert MD/DOCX to template-formatted DOCX
---

# WX 文档格式

将 Markdown 或 DOCX 文档转换为模板格式的 Word (.docx) 文档。三段式流水线（解析 → 规范化 → 模板渲染），输出样式严格来自模板文件。支持可选的 LLM 语义增强（Phase A/B/C 三级），自动识别功能点列表、修正章节层级、生成表格题注。

## 一键安装

对任何支持 skill 的 Agent 说：

> 从 GitHub 仓库 mh567/wx-doc-format-skill 安装 wx-doc-format skill

## 架构概览

```
输入 (MD / DOCX)
  │
  ▼
Step 1: 解析为 source AST
  │  scripts/md_pipeline.py 或 scripts/docx_pipeline.py
  │  产出: 语义块模型（标题、列项、表格、正文、题注、附录等）
  │
  ▼
Step 2: 规范化 AST + LLM 语义增强（可选）
  │  model_normalization.py  清理手工编号、重定型、表格角色修正
  │  llm_enhancer.py         三段式 LLM 增强：
  │    Phase A — 段落角色重分类
  │    Phase B — 章节层级修正
  │    Phase C — 表格/题注语义增强（含自动题注生成）
  │
  ▼
Step 3: 模板驱动渲染
  │  1. 从模板 .docx 创建空文档
  │  2. word_model_renderer.py  渲染 AST → 段落
  │  3. docx_render.py          渲染 DOCX 直接路径（图片/表格克隆）
  │  4. template_finalizer.py   格式收口（样式别名、run 字体、表格）
  │  5. audit.py + reporting.py 输出审计和报告
  │
  ▼
输出: 样式合规的 .docx + JSON/MD 报告
```

## 模块说明

| 模块 | 功能 |
|------|------|
| `document_model.py` | AST 结构定义和校验 |
| `text_utils.py` | 纯文本解析（标题/列项/题注识别与清理） |
| `md_pipeline.py` | Markdown 解析到 source AST |
| `docx_pipeline.py` | DOCX 解析（`infer_docx_role` 语义角色推断） |
| `model_normalization.py` | AST 规范化与审计修补 |
| `llm_enhancer.py` | LLM 语义增强（Phase A/B/C + 分片批处理） |
| `list_semantic_enhancer.py` | LLM 增强的纯函数兼容接口 |
| `word_model_renderer.py` | AST → Word 渲染 |
| `docx_render.py` | DOCX 直接路径渲染（图片/表格克隆、题注 SEQ） |
| `template_profile.py` | 模板样式和编号定义读取 |
| `template_finalizer.py` | 格式收口（样式别名、run 字体重刷、表格属性） |
| `audit.py` | Word 输出审计与内容复核 |
| `reporting.py` | 报告结构和 Markdown 报告输出 |
| `fallback_styles.py` | 非模板模式的 fallback 样式 |
| `main.py` | CLI 编排入口 |
| `update_installed_skill.py` | 在线更新脚本 |

## CLI 用法

```bash
# 基础用法
python3 -m main \
  --input source.md \
  --output output.docx \
  --template template.docx \
  --report report.json

# 全量 LLM 增强
python3 -m main \
  --input source.docx \
  --output output.docx \
  --template template.docx \
  --llm-enhance abc

# 仅 LLM Phase A（段落角色）
--llm-enhance a

# 自动模式（怀疑度评分触发）
--llm-enhance auto

# 关闭 LLM 增强
--llm-enhance off

# 导出 AST 调试
--ast model.json
--source-ast source.json

# 风险检测严格模式
--fail-on-risk
```

## LLM 增强模式

| 模式 | 说明 |
|------|------|
| `off` | 纯规则，不调用 LLM |
| `auto` | 基于怀疑度评分自动决定是否增强 |
| `a` | 仅 Phase A（段落角色重分类） |
| `ab` | Phase A + Phase B（章节层级修正） |
| `abc` | Phase A + B + C（全量增强） |

配合 `--llm-hint "注意功能点列表识别"` 可向 LLM 注入自然语言提示。

## 样式合规不变量

- 输出文档只使用模板样式。`unexpected_styles` 必须为空
- 标题文本不含手工编号（"第一章"、"1.1 " 等）
- 列表每章节重启，新 numId 带 `lvlOverride > startOverride`
- 表格单元格全部为 `表正文`（API 示例表除外）
- 图片段落仅 `jc=center`，不继承 Normal 缩进
- 题注使用 SEQ 域代码（`SEQ Table` / `SEQ Figure`）

## 使用要求

- Python 3.10+
- python-docx、lxml
- macOS：运行 `scripts/bootstrap_macos_lxml.sh` 创建隔离环境

## 已知边界

- 文本框、形状、SmartArt、批注、修订记录不会完整重建
- Markdown 路径不会自动嵌入图片
- DOCX 路径的图片克隆依赖源文档媒体关系映射
- 页眉页脚、页码、复杂目录域需要人工复核
