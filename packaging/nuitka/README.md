# Nuitka 本地构建

本目录提供 macOS ARM64、Linux ARM64 和 Windows x86_64 三个本地构建入口。每个入口都会安装锁定依赖、运行测试、编译 standalone 运行时、组装离线包并执行真实转换冒烟。

## macOS ARM64

需要 macOS ARM64、Xcode Command Line Tools 和 Python 3.13。

```bash
packaging/nuitka/build-local-macos-arm64.sh
```

可以通过 `WXDF_BOOTSTRAP_PYTHON` 指定 Python 3.13 可执行文件。发布前必须检查 Mach-O 的 `minos`，本地编译结果会受当前 macOS SDK 和 Python 工具链影响。

```bash
otool -l dist/local/wx-doc-format-skill-*/runtime/wx-doc-format
```

## Linux ARM64

需要 ARM64 Docker 环境。macOS ARM64 可使用 Colima：

```bash
colima start --arch aarch64 --cpu 4 --memory 8 --disk 20
docker context use colima
packaging/nuitka/build-local-linux-arm64.sh
```

默认镜像按 SHA256 摘要固定。如需使用内网镜像仓库，设置 `WXDF_LINUX_BUILD_IMAGE`。生成的 `kylin-v10-arm64` 归档是候选制品，交付前必须在 Kylin V10 ARM64 真机完成安装和转换冒烟。

## Windows x86_64

需要 Windows x86_64、Python 3.13 和 Nuitka 支持的 C 编译工具链。

```powershell
.\packaging\nuitka\build-local-windows-x86_64.ps1
```

如果未使用 `py` launcher，通过 `BootstrapPython` 传入 Python 3.13 路径。

```powershell
.\packaging\nuitka\build-local-windows-x86_64.ps1 -BootstrapPython C:\Python313\python.exe
```

## 产物和验证

归档统一输出到 `dist/local/`：

```text
wx-doc-format-skill-<version>-macos-arm64.tar.gz
wx-doc-format-skill-<version>-kylin-v10-arm64.tar.gz
wx-doc-format-skill-<version>-windows-x86_64.zip
```

发布包的安装目录必须只包含可打印 ASCII 字符。安装器会验证 `SHA256SUMS.txt`、完整文件集和运行时版本。调用 `scripts/run.sh` 或 `scripts/run.ps1` 时也会重新检查安装路径。

## 组装公开 Skill

公开仓库只保留薄 Skill 和平台运行时引导脚本。组装时可以要求已经完成的平台，未生成的 Windows 制品会记录为暂不可用：

```bash
scripts/.venv/bin/python3 packaging/nuitka/assemble_public_skill.py \
  --artifacts-dir dist/local \
  --output-dir dist/public-skill \
  --require-platform macos-arm64 \
  --require-platform kylin-v10-arm64 \
  --macos-min-version 26.0
```

`dist/public-skill/` 用于替换公开仓库当前文件。`dist/local/` 中列入 `artifacts/SHA256SUMS.txt` 的归档作为同版本 GitHub Release 资产上传。
