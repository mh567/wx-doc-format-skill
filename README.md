# wx-doc-format

Markdown / DOCX 转 WX 模板格式 DOCX 的格式转换服务。按八个阶段把源文档经过解析、裁决、规范化、渲染、审计，最终按模板合同输出 DOCX 与 JSON 审计报告。

## 功能

- 格式转换：把 Markdown 或 DOCX 转换为符合 WX 模板的技术文档 DOCX。
- 格式审查：按模板合同审查 DOCX，输出百分制评分与逐项整改建议。
- LLM 增强：目录区域复核、列表识别、题注生成、审计后受限复核，以及引用文件排序、术语与缩略语、列项用法检查。

四种模式与触发说法见 [`SKILL.md`](SKILL.md)。

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

LLM 增强以 Capability 插拔方式接入，当前能力：`toc_region_review`、`list_detect`、`caption_gen`、`document_review`。新增能力在 `scripts/wxdf/capabilities.py` 注册 `CapabilityConfig`。

## 仓库布局

| 路径 | 用途 |
|------|------|
| `SKILL.md` | Skill 入口说明，Agent 按此调用启动脚本 |
| `scripts/run.sh` | 启动脚本：选择解释器、检查依赖、执行 CLI |
| `scripts/setup.sh` | 创建 `.venv` 并安装依赖 |
| `scripts/main.py` | CLI 入口 |
| `scripts/wxdf/` | 流水线实现：kernel、registry、parser、semantics、normalization、rendering、audit、publication |
| `scripts/wxdf/semantics/` | 各语义能力模块：lists、tables、captions、appendices、regions、structure、protected |
| `scripts/*.py` | 流水线复用的顶层模块：语义词汇、文本工具、模板合同与 LLM 能力注册 |
| `assets/wx_template.docx` | WX 格式模板 |
| `tests/` | 测试 |
| `docs/` | 设计文档与开发流程 |

## 环境要求

- Python 3.10 或更新版本。
- `python-docx`、`lxml`、`markdown-it-py`，版本见 `requirements.txt`。

## 安装

```bash
./scripts/setup.sh
```

该命令在仓库根创建 `.venv` 并安装运行时依赖，需要网络。

也可以对现有 Python 环境安装：

```bash
python3 -m pip install -r requirements.txt
```

## 运行

```bash
./scripts/run.sh \
  --input input/xxx.docx \
  --output outputs/xxx.docx \
  --template assets/wx_template.docx \
  --report outputs/report.json
```

格式审查：

```bash
./scripts/run.sh \
  --review \
  --input input/xxx.docx \
  --template assets/wx_template.docx \
  --report-md outputs/review.md
```

也可以直接使用虚拟环境：

```bash
cd scripts && ../.venv/bin/python3 -m main \
  --input ../input/xxx.docx \
  --output ../outputs/xxx.docx \
  --template ../assets/wx_template.docx \
  --report ../outputs/report.json
```

常用标志：

- `--source-ast / --ast` 导出源 AST 或归一化 AST
- `--llm-enhance all --llm-command <cmd>` 启用 LLM 增强
- `--strict-normalize / --fail-on-risk`
- `--table-row-height-cm / --table-row-height-rule`
- `--caption-numbering chapter|sequential`

## 测试

```bash
./scripts/setup.sh --dev
.venv/bin/python3 -m pytest tests/ -q
```
