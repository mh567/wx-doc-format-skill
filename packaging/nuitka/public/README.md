# wx-doc-format-skill 离线发布包

本发布包用于将 Markdown 或 DOCX 文档转换为 WX 模板格式的 DOCX 文档。运行时无需安装 Python，安装和转换过程不下载文件。

支持 Windows x86_64、macOS ARM64 和 Linux ARM64。Linux ARM64 制品以 Kylin V10 为候选兼容目标。

## 安装

将本安装包交给 Agent：

> 安装这个 wx-doc-format 离线 Skill 包

Agent 解压后将包含 `SKILL.md` 的完整目录安装到自身 skills 目录，建议目录名使用 `wx-doc-format`。安装完成后可直接通过对话触发。

也可以执行包内安装脚本，并将目标 Agent 的 Skill 路径作为安装目录传入：

POSIX 系统：

```bash
scripts/install.sh /path/to/agent/skills/wx-doc-format
```

Windows PowerShell：

```powershell
.\scripts\install.ps1 -Destination C:\path\to\agent\skills\wx-doc-format
```

安装前会验证 SHA256，升级时会在目标目录旁保留上一版备份。
安装目录的完整路径必须只包含可打印 ASCII 字符。如果用户主目录含有中文等字符，请显式指定类似 `/opt/wx-doc-format` 或 `C:\wx-doc-format` 的目录。安装脚本和启动脚本会在运行前检查该条件。

## 使用

上传 Markdown 或 DOCX 文档，在对话中说：

> wx文档格式，请把附件转换成标准格式。

Agent 会请用户选择普通模式或 LLM 增强模式。用户已指定模式时会直接执行。

以下命令仅用于手工检查：

```bash
scripts/run.sh --version
scripts/run.sh --input source.docx --output output.docx --template assets/wx_template.docx
```

Windows 使用 `.\scripts\run.ps1` 传入相同参数。详细模式、文件协议和验证要求见 `SKILL.md`。

## 完整性和兼容性

`SHA256SUMS.txt` 用于检查包内文件完整性。当前制品未包含签名认证。Kylin V10 制品在 manifest 中标记为 candidate，交付前需要在目标架构真机上完成冒烟测试。
