# CLAUDE.md — wx-doc-format

## 项目

wx-doc-format：Markdown / DOCX 转 WX 模板格式 DOCX。模块化流水线 + LLM Capability 插拔增强。默认入口 `scripts/.venv/bin/python3 -m main`。

## 架构

八阶段流水线由 `scripts/wxdf/ConversionKernel` 编排，`ModuleRegistry` 调度 module：

```
解析 → 范围 → 角色 → 关系 → 规范化 → 渲染 → 审计 → 发布
```

LLM 能力：`toc_region_review` / `list_detect` / `caption_gen` / `document_review`，注册在 `scripts/wxdf/capabilities.py`。

## 关键文件

| 文件 | 用途 |
|------|------|
| `scripts/main.py` | CLI 入口 |
| `scripts/wxdf/kernel.py` | `ConversionKernel`，流水线编排 |
| `scripts/wxdf/registry.py` | `ModuleRegistry`，阶段调度 |
| `scripts/wxdf/parsers.py` | 源文档解析（DOCX / Markdown） |
| `scripts/wxdf/semantics/` | 各语义能力模块 |
| `scripts/wxdf/normalization.py` | 归一化语义视图 |
| `scripts/wxdf/rendering.py` | DOCX 渲染 |
| `scripts/wxdf/auditing.py` | 审计 |
| `scripts/wxdf/publication.py` | 交付决定 |
| `scripts/wxdf/enhancement.py` | LLM 增强执行边界 |

## 注意

- 不修改 `scripts/main.py` 与 `scripts/document_model.py` 的公开接口
- venv: `scripts/.venv/`
- 测试: `cd scripts && .venv/bin/python3 -m pytest ../tests/ -q`
- 编码偏好与关键决策记录见 `AGENTS.md`
- 双仓库/分支/发布流程见 `docs/development-workflow.md`
