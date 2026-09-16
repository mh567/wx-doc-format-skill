# wx-doc-format

Markdown / DOCX 转 WX 模板格式 DOCX 的格式转换服务。默认走 native-v2 模块化流水线，按八个阶段把源文档经过解析、裁决、规范化、渲染、审计，最终按模板合同输出 DOCX 与 JSON 审计报告。

## 架构

流水线由 `scripts/wxdf/ConversionKernel` 编排，`ModuleRegistry` 调度注册好的 module。八个阶段：

```
解析 → 范围 → 角色 → 关系 → 规范化 → 渲染 → 审计 → 发布
```

- **解析**读源文档（DOCX 或 Markdown），产出 `SourceFacts` 与 source AST。
- **范围 / 角色 / 关系**产候选并裁决：区域排除、结构角色、列表/表格/题注/附录/受保护内容。
- **规范化**基于模板合同派生不可变语义视图，承载大纲与呈现。
- **渲染**按呈现合同写 DOCX。
- **审计**校验模型、保存输出、附录保存与样式不变量；产出 `public_summary` 决定交付。
- **发布**根据审计结果决定是否交付。

LLM 增强以 Capability 插拔方式接入，当前能力：`toc_region_review`、`list_detect`、`caption_gen`、`document_review`。新增能力在 `scripts/wxdf/llm_enhancer.py` 注册 `CapabilityConfig`。

legacy 流水线（`scripts/docx_render.py` / `scripts/template_finalizer.py`）只作为开发诊断模式保留，发布构建里被禁用；默认入口是 native-v2。

## 仓库布局

| 路径 | 用途 |
|------|------|
| `scripts/main.py` | CLI 入口，开发运行 `scripts/.venv/bin/python3 -m main` |
| `scripts/wxdf/` | native-v2 核心：kernel、registry、parser、semantics、normalization、rendering、audit、publication |
| `scripts/wxdf/semantics/` | 各语义能力模块：lists、tables、captions、appendices、regions、structure、protected |
| `tests/` | 92 个测试文件 |
| `packaging/nuitka/` | Nuitka 构建、公开薄 Skill 组装 |
| `docs/` | 设计文档与执行记录 |
| `assets/wx_template.docx` | WX 格式模板 |

文档分工：`SKILL.md` 与公开 README 是发布到公开仓库的薄 Skill 内容；本文件（README.md）、`AGENTS.md`、`CLAUDE.md` 是私有开发仓库的内部文档。

## 开发与运行

```bash
cd scripts && .venv/bin/python3 -m main \
  --input ../input/xxx.docx \
  --output ../outputs/xxx.docx \
  --template ../assets/wx_template.docx \
  --report ../outputs/report.json
```

常用标志：
- `--pipeline {legacy,modular}` 默认 `modular`；`legacy` 为开发诊断模式
- `--modular-profile native-v2` 默认；`--list-policy wx-default-v2` 默认
- `--source-ast / --ast` 导出源 AST 或归一化 AST（开发诊断）
- `--llm-enhance all --llm-command <cmd>` 启用 LLM 增强
- `--strict-normalize / --fail-on-risk`
- `--table-row-height-cm / --table-row-height-rule`

测试：

```bash
cd scripts && .venv/bin/python3 -m pytest ../tests/ -q
```

不要修改 `scripts/main.py` 与 `scripts/document_model.py` 的公开接口。

## 双仓库与分支模型

私有 `mh577/wx-doc-format-skill-dev`（远程名 `dev`）是源码与构建规则的唯一事实源，本地 `dev` 推送到远程 `dev/main`。公开 `mh577/wx-doc-format-skill`（远程名 `origin`）只保留生成的薄 Skill，本地 `main` 跟踪 `origin/main`。详见 [`docs/development-workflow.md`](docs/development-workflow.md)。

- 所有功能、修复、测试和构建工具变更只在本地 `dev` 开发并推送到 `dev/main`。
- 本地 `main` 只跟踪公开 `origin/main`，不承担源码开发。
- 公开发布只复制已验证的 `dist/public-skill/` 生成树。
- 公开分支禁止 merge 或 cherry-pick 私有分支；公开历史中不能出现 `.py` / `.pyc` / `.pyo`。
- 执行分支操作、构建、公开 Skill 生成、标签或 Release 前，必须完整读取 `docs/development-workflow.md`。
