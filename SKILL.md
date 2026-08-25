---
name: wx-doc-format
description: 将 Markdown 或 DOCX 转换为模板化技术文档 DOCX。当用户说“技术文档格式”“转换为技术文档格式”“wx文档格式”“WX 格式转换”，或要求按技术文档模板整理标题、目录、列表、表格、题注和附录时使用。
metadata:
  short-description: 转换为模板化技术文档 DOCX
---

# WX 文档格式

当用户上传 Markdown 或 DOCX 并说“技术文档格式”“转换为技术文档格式”“按技术文档格式排版”“整理成技术文档”“wx文档格式”或“WX 格式转换”时使用本 Skill。

## 交互

1. 确认用户已提供 Markdown 或 DOCX 输入文件。
2. 用户未指定运行模式时，请其选择“普通模式”或“LLM 增强模式”。
3. 用户已选定模式时直接执行，无需再次确认。
4. 完成后交付 DOCX，同时说明审计是否通过、`unexpected_styles` 是否为空以及人工复核项。

运行模式：

1. 普通模式：使用本地规则完成转换。
2. LLM 增强模式：增加模糊目录复核、语义列表识别、符合条件的题注生成和审计后受限复核。

## 运行时准备

执行前读取 `artifacts/AVAILABLE_PLATFORMS.txt`。当前平台没有发布运行时时，告知用户并保留原始文件。

从 GitHub 仓库安装时，首次运行会从当前版本的 GitHub Release 下载平台运行时，验证 SHA256 后自动安装。

使用离线 Skill 安装包时，Agent 将解压后包含 `SKILL.md` 的完整目录放入自身 skills 目录。离线包已包含平台运行时，可直接执行转换。

联网仓库安装和离线安装包均无需配置环境变量。`artifacts/` 只保存薄 Skill 用于校验下载的平台清单、版本信息和 SHA256，不作为用户安装入口。

## 执行

将 `SKILL_ROOT` 解析为包含本 `SKILL.md` 的目录，并对输入、输出、模板和报告使用绝对路径。

POSIX 系统：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --input "$INPUT_FILE" \
  --output "$OUTPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report "$REPORT_FILE"
```

Windows PowerShell：

```powershell
& "$SkillRoot\scripts\run.ps1" `
  --input $InputFile `
  --output $OutputFile `
  --template "$SkillRoot\assets\wx_template.docx" `
  --report $ReportFile
```

## LLM 增强模式

选择 LLM 增强模式时，使用文件协议：

1. 在转换命令中增加 `--llm-enhance all --generate-requests "$REQUEST_DIR"`。
2. 读取 `llm_requests.jsonl`，逐行处理请求，将模型原始 JSON 返回文本写入对应响应的 `raw_response`。
3. 将响应写入同目录的 `llm_responses.jsonl`。每行保留请求中的 `protocol_version`、`request_id` 和 `input_hash`。
4. 执行 `scripts/run.sh --resume "$REQUEST_DIR/run.json"`。如果生成新请求，继续处理，直到转换完成或协议验证失败。

发布运行时不会自动访问互联网模型服务。

## 验证

交付前检查 CLI 返回码、JSON 报告和以下不变量：

1. `unexpected_styles` 为空。
2. 标题文本不含手工编号。
3. 普通语义表格符合表格样式要求。
4. 目录只包含规定的标题层级。
5. 列表在每个章节内按规则重启。

## 已知边界

1. 目录页码和复杂域可能需要在 Word 或 WPS 中更新。
2. Kylin V10 ARM64 制品在真机验收前保持候选状态。
3. macOS ARM64 可用系统版本取决于发布制品的 `minos`。
