# wx-doc-format

一个供 AI agent 使用的技术文档格式 Skill：将 Markdown 或 Word 文档整理成 WX 模板格式的 DOCX，也可以检查现有 Word 文档的格式，给出评分和整改建议。

## 能做什么

- **格式转换**：统一标题、正文、目录、列表、表格、图表题注和附录的格式，输出可编辑的 DOCX。
- **格式审查**：检查 DOCX 是否符合模板要求，输出百分制评分、问题位置和整改建议。
- **AI 增强**：在转换时辅助识别目录、列表和题注；在审查时增加引用文件排序、术语与缩略语、列项用法检查。

默认使用随 Skill 提供的 WX 模板。普通转换与普通审查不需要调用模型。

## 安装

### 在线安装

把下面这句话发给支持 Skill 的 agent：

```text
请从 https://github.com/mh567/wx-doc-format-skill 安装 wx-doc-format Skill。
```

支持 `cmd skills add` 的宿主也可以执行：

```bash
cmd skills add mh567/wx-doc-format-skill
```

需要 Python 3.8 或更新版本。首次使用会自动准备依赖，需要联网，无需手工运行安装脚本。

### 离线安装

从仓库的 Releases 页面下载与目标机匹配的离线包，解压到 agent 的 skills 目录，确认目录结构为 `wx-doc-format/SKILL.md`。

| 离线包 | 目标环境 |
|--------|----------|
| `wx-doc-format-<版本>-macos-arm64.zip` | Apple Silicon，macOS 11 或更新版本 |
| `wx-doc-format-<版本>-linux-arm64.zip` | Linux ARM64，glibc 2.28 或更新版本 |

两个包均包含运行依赖，支持标准 CPython 3.8 至 3.14。目标 Python 需带有标准库 `lzma`，无需 `pip`、`venv` 或 `ensurepip`。安装后直接使用，首次运行自动准备依赖，全程无需联网。

离线包暂不覆盖 Windows、x86、PyPy 和自由线程版 Python。多解释器环境可用 `WXDF_PYTHON` 指定 Python 的绝对路径。

## 怎么用

安装完成后，把文件交给 agent，或提供它可以读取的文件路径，再说明需要转换还是审查。

### 转换文档

支持 `.md` 和 `.docx` 输入，输出 `.docx`。例如：

```text
用 wx-doc-format 将这份方案转换成 WX 模板格式，输出到 outputs/方案.docx，使用普通格式转换。
```

需要 AI 辅助识别复杂结构时：

```text
用 wx-doc-format 的 AI 增强格式转换处理 input/方案.docx，输出到 outputs/方案.docx。
```

### 审查文档

审查支持 `.docx` 输入，保留原文件，输出评分和整改报告。例如：

```text
用 wx-doc-format 对 input/方案.docx 做普通格式审查，将整改报告保存到 outputs/审查报告.md。
```

需要同时检查引用文件排序、术语与缩略语、列项用法时：

```text
用 wx-doc-format 对这份 Word 文档做 AI 增强格式审查，给出评分、问题位置和整改建议。
```

AI 增强模式需要 agent 提供模型响应；离线包包含文档处理依赖，模型能力由使用它的 agent 提供。

## 会得到什么

- **转换**：整理后的 DOCX、转换报告，以及需要人工确认的事项。
- **审查**：百分制评分和整改报告；报告可保存为 Markdown、HTML 或 JSON。

agent 会根据检查结果说明文档是否可交付及仍需处理的问题。目录、交叉引用等域的最终显示，以及复杂公式和嵌入对象，建议在 Word 或 WPS 中确认。

agent 的完整执行说明见 [SKILL.md](SKILL.md)。
