<<<<<<< HEAD
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repo contains tools for converting Markdown and DOCX documents into WX-formatted (企业微信文档格式) Word documents, using a template-driven approach. The core project is the `wx-doc-format-skill/` directory (v0.8.5), supplemented by standalone ad-hoc conversion scripts at the repo root.

## Common Commands

All commands run from `wx-doc-format-skill/`:

```bash
# Bootstrap macOS Python venv (first-time setup)
cd wx-doc-format-skill && ./scripts/bootstrap_macos_lxml.sh

# Convert a document (template mode)
cd wx-doc-format-skill/scripts && .venv/bin/python3 -m main \
  --input ../input/xxx.docx \
  --output ../wx_output/xxx.docx \
  --template ../assets/技术文件格式及书写要求.docx \
  --report ../wx_output/report.json

# Run tests
cd wx-doc-format-skill && .venv/bin/python3 -m pytest tests/ -q
```

## Architecture

### Primary project: `wx-doc-format-skill/`

Three-stage pipeline — all modules live in `scripts/`:

| Stage | Module | Role |
|-------|--------|------|
| 1. Parse | `md_pipeline.py` / `docx_pipeline.py` | Convert input to semantic AST (headings, lists, tables, captions, images) |
| 2. Normalize | `model_normalization.py` (+ optional `list_semantic_enhancer.py` for LLM augmentation) | Strip manual numbering, fix misclassified blocks, mark list restarts, correct table roles |
| 3. Render | `word_model_renderer.py` + `docx_render.py` → `template_finalizer.py` | Map AST blocks to template styles, clone images/tables, apply style aliases, run font overrides, audit compliance |

Key supporting modules:

- **`document_model.py`** — Intermediate AST data structures and validation
- **`text_utils.py`** — Heading/list/caption recognition heuristics (`looks_like_visual_heading`, `is_compact_function_heading_text`, etc.)
- **`template_profile.py`** — Reads template .docx to extract available styles and numbering definitions
- **`template_finalizer.py`** — Post-render cleanup: style alias normalization, appendix merging, run-level font re-application, style audit
- **`audit.py`** / **`reporting.py`** — Output validation (heading gaps, list restart correctness, unexpected styles, table style compliance)
- **`fallback_styles.py`** — Built-in styles for non-template mode (preview only)
- **`format_docx_ooxml.py`** — Emergency OOXML fallback when python-docx is unavailable
- **`main.py`** — CLI orchestrator, auto-detects venv, dispatches MD vs DOCX path

**Style compliance invariant**: In template mode, `unexpected_styles` in the audit report must be empty — the output document must only use styles defined in the template.

**List numbering strategy**: All numbered lists use a single-level style (`1.1一级列项-编号`, `a) b) c)` format), restarting from `a)` after each heading via new numId with `lvlOverride > startOverride`.

### Template file

`assets/技术文件格式及书写要求.docx` — the canonical WX format template. Contains all allowed paragraph styles, numbering definitions (headings, lists, appendix), and section formatting. All output documents derive from this template.

### Root-level scripts

- **`make_icd_v3.py`** — Hardcoded ICD (接口控制文件) document transformation. Modifies specific paragraphs in a source DOCX by replacing sections. Standalone, not part of the skill pipeline.
- **`make_arch_outline_docx.py`** — Converts the architecture outline Markdown (`零信任安全管理平台架构详细设计方案大纲 V3.md`) into a template-styled DOCX. Simpler predecessor to the skill pipeline; uses hardcoded paths and a one-off style mapping.

### Input/output conventions

- `input/` — Source documents organized by type (`接口类/` for API docs, `方案类/` for design docs)
- `wx_output/` — Generated outputs, organized by batch/date (e.g., `iam_batch_test_20260627/`, `xingwang_ticket_api_20260626/`)

## Coding Preferences (from AGENTS.md)

- Avoid the rhetorical pattern "不是...，而是" (not X, but Y)
- Avoid em dashes (破折号)
- Prefer non-Chinese sources when searching the web
- Do not rename established classes/functions or adjust stable interfaces
=======
# CLAUDE.md — wx-doc-format-skill

## 项目

wx-doc-format：将 Markdown/DOCX 转换为 WX 模板格式的 .docx。三段式流水线（parse → normalize → render）+ LLM 语义增强。

## 关键文件

| 文件 | 用途 |
|------|------|
| `scripts/main.py` | CLI 入口 |
| `scripts/llm_enhancer.py` | LLM 增强核心（Phase A/B/C + 分片） |
| `scripts/model_normalization.py` | AST 规范化 + 列项推断（F4） |
| `scripts/docx_render.py` | DOCX 路径渲染（表格/图片克隆） |
| `scripts/text_utils.py` | 文本解析工具（标题/列项/题注） |
| `scripts/benchmark.py` | 基准测试 |
| `tests/test_llm_enhancer.py` | LLM 增强测试（102 tests） |

## 注意

- 不修改 `scripts/main.py` 和 `scripts/document_model.py` 的公开接口
- Python 3.14, venv 在 `scripts/.venv/`
- 测试：`cd scripts && .venv/bin/python3 -m pytest ../tests/ -q`
- AGENTS.md 有编码偏好和关键决策记录，每次任务前参考
>>>>>>> 68560d0 (dev: 修复 .gitignore，追踪 AGENTS.md 和 CLAUDE.md)
