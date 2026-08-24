# wx-doc-format-skill

## 核心能力

将 Markdown 或 DOCX 文档转换为 WX 模板格式的 DOCX，自动处理标题、目录、列表、表格、题注和附录，并生成审计报告。

运行时以本机编译包分发，目标机无需安装 Python。当前提供 macOS ARM64 和 Linux ARM64 制品，Windows x86_64 运行时待后续补充。

## 安装方法

让支持 Skill 的 Agent 执行：

> 从 GitHub 仓库 mh567/wx-doc-format-skill 安装 wx-doc-format skill

也可以将仓库克隆到 Agent 的 Skill 目录。首次运行会自动获取当前平台制品并验证 SHA256。

内网环境可以直接解压 GitHub Release 中对应平台的完整包，或设置本地制品目录：

```bash
export WX_DOC_FORMAT_ARCHIVE_DIR=/path/to/offline-artifacts
```

## 使用方法

macOS 和 Linux：

```bash
scripts/run.sh \
  --input source.docx \
  --output output.docx \
  --template assets/wx_template.docx \
  --report report.json
```

Windows PowerShell：

```powershell
.\scripts\run.ps1 `
  --input source.docx `
  --output output.docx `
  --template assets\wx_template.docx `
  --report report.json
```
