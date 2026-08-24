# wx-doc-format-skill 离线发布包

本发布包用于将 Markdown 或 DOCX 文档转换为 WX 模板格式的 DOCX 文档。运行时无需安装 Python，安装和转换过程不下载文件。

支持 Windows x86_64、macOS ARM64 和 Linux ARM64。Linux ARM64 制品以 Kylin V10 为候选兼容目标。

## 安装

POSIX 系统：

```bash
scripts/install.sh
```

Windows PowerShell：

```powershell
.\scripts\install.ps1
```

可以向安装脚本传入自定义目标目录。安装前会验证 SHA256，升级时会在目标目录旁保留上一版备份。

## 运行

```bash
scripts/run.sh --version
scripts/run.sh --input source.docx --output output.docx --template assets/wx_template.docx
```

Windows 使用 `.\scripts\run.ps1` 传入相同参数。详细模式、文件协议和验证要求见 `SKILL.md`。

## 完整性和兼容性

`SHA256SUMS.txt` 用于检查包内文件完整性。当前制品未包含签名认证。Kylin V10 制品在 manifest 中标记为 candidate，交付前需要在目标架构真机上完成冒烟测试。
