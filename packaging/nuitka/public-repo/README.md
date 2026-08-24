# wx-doc-format-skill

本仓库是 wx-doc-format 的公开 Skill 分发层。核心处理程序以按平台编译的离线包发布，目标机无需安装 Python。

## 安装 Skill

可以让支持 Skill 的 Agent 从 `mh567/wx-doc-format-skill` 安装，也可以直接克隆到 Agent 的 Skill 目录。

首次运行 `scripts/run.sh` 或 `scripts/run.ps1` 时，启动器根据当前操作系统和架构获取对应归档。当前发布可用平台见 `artifacts/AVAILABLE_PLATFORMS.txt`。对应平台缺失时，启动器会返回明确错误，已安装的 Skill 保持可更新。

## 离线和内网部署

从 GitHub Release 下载目标平台的完整归档，解压后可以直接作为 Skill 使用。也可以将归档和本仓库一同放入内网介质，首次运行前设置：

```bash
export WX_DOC_FORMAT_ARCHIVE_DIR=/path/to/offline-artifacts
```

Windows PowerShell 使用：

```powershell
$env:WX_DOC_FORMAT_ARCHIVE_DIR = "D:\offline-artifacts"
```

启动器会根据仓库内置的 SHA256 清单验证归档。缓存和安装路径需要只包含可打印 ASCII 字符。

## 可用性

Windows 运行时可以后续补充。当 `windows-x86_64` 未列入当前版本的可用平台时，Windows 端会保留 Skill 并返回 `WXDF-E-RUNTIME-UNAVAILABLE`。
