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
| `scripts/setup.sh` | 源码安装创建 `.venv`；离线包准备私有依赖目录 |
| `scripts/main.py` | CLI 入口 |
| `scripts/wxdf/` | 流水线实现：kernel、registry、parser、semantics、normalization、rendering、audit、publication |
| `scripts/wxdf/semantics/` | 各语义能力模块：lists、tables、captions、appendices、regions、structure、protected |
| `scripts/*.py` | 流水线复用的顶层模块：语义词汇、文本工具、模板合同与 LLM 能力注册 |
| `assets/wx_template.docx` | WX 格式模板 |
| `tests/` | 测试 |
| `docs/` | 设计文档与开发流程 |

## 环境要求

- Python 3.8 或更新版本。离线包使用标准库 `lzma`，无需 `venv`、`pip` 或 `ensurepip`。
- `python-docx`、`lxml`、`markdown-it-py`、`typing_extensions`，按解释器版本选择 `requirements.txt` 中的固定版本，离线包已包含全部运行依赖。

## 在线安装

在支持 `cmd skills add` 的宿主直接执行：

```bash
cmd skills add mh567/wx-doc-format-skill
```

也可以把仓库地址 `https://github.com/mh567/wx-doc-format-skill` 交给 agent，并要求安装 `wx-doc-format` skill。

安装后直接提出转换或审查请求，agent 调用 `scripts/run.sh`。首次调用自动在 Skill 目录创建 `.venv` 并从 PyPI 安装依赖，无需手工执行脚本。

## 离线安装包

从 Release 下载匹配平台的 zip，例如 `wx-doc-format-<VERSION>-macos-arm64.zip` 或 `wx-doc-format-<VERSION>-linux-arm64.zip`，解压到 agent 的 skills 目录，得到 `wx-doc-format/`。每个平台只有一个包，覆盖常规 CPython 3.8 至 3.14，每包小于 14 MB（14,000,000 字节）。Linux ARM64 wheel 要求 glibc 2.28 或更新版本；macOS ARM64 要求 macOS 11 或更新版本。

运行前用 `python3 --version` 确认版本。多解释器设备可设置 `WXDF_PYTHON` 为目标解释器的绝对路径。

安装后直接提出转换或审查请求，agent 调用 `scripts/run.sh`。首次调用自动从 `wheelhouse/` 解出当前 Python 对应的依赖，放入 Skill 内的 `.deps/` 并直接加载，不访问网络，不调用安装工具，也不修改系统 Python 环境。

`wheelhouse/wheels.tar.xz` 对完整 wheel 做共享压缩，首次运行只解出当前 Python 所需依赖并校验 SHA256，保留包内资源与版本元数据；后续启动复用校验通过的私有缓存。`wheelhouse/manifest.json` 记录覆盖范围、每个解释器的精确依赖清单与 wheel 摘要。Python 3.8 使用兼容依赖版本；未覆盖的小版本、PyPy 和自由线程版 Python 无对应原生 wheel，不承诺离线运行。标准库 `lzma` 被裁掉的精简 Python 会给出明确错误。

## 运行

以下命令由 agent 在 Skill 目录执行。

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
