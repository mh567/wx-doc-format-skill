---
name: wx-doc-format
description: Convert Markdown or DOCX documents into WX template-formatted DOCX files with an offline native runtime.
metadata:
  short-description: Convert MD or DOCX to WX formatted DOCX
---

# WX 文档格式

当用户要求将 Markdown 或 DOCX 文档转换为 WX 模板格式时使用本 Skill。

## 平台检查

执行前读取 `artifacts/AVAILABLE_PLATFORMS.txt`。当前平台没有发布运行时时，告知用户并停止转换，保留原始输入文件。

## 运行

POSIX 系统：

```bash
scripts/run.sh --input source.docx --output output.docx --template assets/wx_template.docx --report report.json
```

Windows PowerShell：

```powershell
.\scripts\run.ps1 --input source.docx --output output.docx --template assets\wx_template.docx --report report.json
```

首次运行会下载当前平台的发布归档并验证 SHA256。内网或离线环境使用 `WX_DOC_FORMAT_ARCHIVE_DIR` 指向存放平台归档的目录。

使用内网本地 LLM 命令时，增加 `--llm-enhance all --llm-command "<command>"`。发布运行时不自动访问互联网模型服务。

## 验证

交付前检查 CLI 返回码、JSON 报告和以下不变量：

1. `unexpected_styles` 为空。
2. 标题文本不含手工编号。
3. 普通语义表格符合表格样式要求。
4. 目录只包含规定的标题层级。
5. 列表在每个章节内按规则重启。

## 已知边界

1. Skill 缓存和安装目录的完整路径必须只包含可打印 ASCII 字符。
2. 目录页码和复杂域可能需要在 Word 或 WPS 中更新。
3. Kylin V10 ARM64 制品在真机验收前保持候选状态。
4. macOS ARM64 可用系统版本取决于发布制品的 `minos`。
