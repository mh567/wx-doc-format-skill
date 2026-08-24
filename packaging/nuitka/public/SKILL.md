---
name: wx-doc-format
description: Convert Markdown or DOCX documents into WX template-formatted DOCX files with an offline native runtime.
metadata:
  short-description: Convert MD or DOCX to WX formatted DOCX
---

# WX 文档格式

当用户要求转换 Markdown 或 DOCX 文档为 WX 模板格式时使用本 Skill。

## 运行模式

1. 普通模式。默认使用本地规则转换。
2. LLM 增强模式。可使用内网本地命令，也可使用请求和响应文件。发布包不自动访问互联网模型服务。

## 调用

POSIX 系统：

```bash
scripts/run.sh --input source.docx --output output.docx --template assets/wx_template.docx --report report.json
```

Windows PowerShell：

```powershell
.\scripts\run.ps1 --input source.docx --output output.docx --template assets\wx_template.docx --report report.json
```

使用内网本地 LLM 命令时，增加 `--llm-enhance all --llm-command "<command>"`。

## LLM 文件协议

先生成请求文件：

```bash
scripts/run.sh --input source.docx --output output.docx --template assets/wx_template.docx --llm-enhance all --generate-requests request-dir
```

处理 `llm_requests.jsonl` 后，将响应写入同目录的 `llm_responses.jsonl`，再继续运行：
每个响应占一行 JSON，包含请求中的 `protocol_version`、`request_id`和 `input_hash`，并将模型原始返回文本写入 `raw_response`。

```bash
scripts/run.sh --resume request-dir/run.json
```

## 输出验证

交付前检查 CLI 返回码、JSON 报告和以下不变量：

1. `unexpected_styles` 为空。
2. 标题文本不含手工编号。
3. 普通语义表格符合表格样式要求。
4. 目录只包含规定的标题层级。
5. 列表在每个章节内按规则重启。

## 已知边界

1. 目录页码和复杂域可能需要在 Word 或 WPS 中更新。
2. 复杂嵌入对象需要根据报告执行人工复核。
3. 发布包不提供完整中间结构导出。
