# 0.19.0 发布验收

## 发布身份

- 版本：`0.19.0`
- 冻结源码：`a67ab14b2aac0dd9e2e8a1b3e453fdeba8b1c2bb`
- 源码树：`aa3208c1ea098a449c91d54c3b10006d8fd01c4f`
- 分支：私有 `dev`，两个平台制品均从该干净提交构建，`manifest.json` 的 `source_identity` 与之一致
- 公开提交：`60623ab68871f5fa9949837687c25e6ec34c69b6`
- 公开树：`4caa7916c95bbcdf3102ef87bc3dec3c92b8243a`
- Release：https://github.com/mh567/wx-doc-format-skill/releases/tag/v0.19.0

## 本次变更

发布包由 Nuitka standalone 改为 `lib/boot.py` + `lib/payload.bin` + `vendor/`，由目标机 Python 解释执行。制品内没有第一方 Mach-O，因此不再触发 macOS 代码签名与公证校验。依赖随包携带，目标机不联网、不装包，启动器以 `-I -B` 运行并忽略目标机的 `PYTHONPATH`、`PYTHONHOME` 与用户 site-packages。`pillow` 从运行时依赖移除，全仓没有 `import PIL`。

依赖布局分为共享的 `vendor/common`（纯 Python）与按小版本的 `vendor/cp3xx`（只放 lxml）。`manifest.json` 的 `runtime.python_tags` 列出实际覆盖的小版本，`python_requires` 由标签推导。

## 构建与验证

- macOS ARM64 在本机 macOS 27.2 ARM64 构建，Python 3.14.4，覆盖 `cp312`、`cp313`、`cp314`，不随包携带解释器。
- Kylin V10 ARM64 在 ARM64 容器 `python:3.13-slim`（glibc 2.36）构建，随包携带 CPython 3.13.18（python-build-standalone `20261026`，stripped），vendor 固定 `cp313`，wheel 取 `manylinux_2_28_aarch64`。
- 随包解释器剪枝 30 项：重复入口、头文件、pip、ensurepip、idlelib、tkinter、turtledemo、pydoc_data、lib2to3、config、Tcl/Tk 库与重复的 `libpython3*.so` 软链。解释器目录从 141 MB 降到 55 MB。
- 载荷：65 个模块，354093 字节，两平台 `module_digest` 均为 `9cf7f986…80af4`。`payload.bin` 因每次构建使用随机 nonce，字节摘要按包记录。

## 验收结果

| 项目 | macOS ARM64 | Kylin V10 ARM64 |
|---|---|---|
| 包内 SHA256 | 通过 | 通过 |
| 运行时 `--version` | `0.19.0` | `0.19.0` |
| 第一方 Mach-O | 0 个 | 0 个 |
| 真实转换 | completed | completed |
| 格式审查 | 通过 | 通过 |
| 离线安装 | 通过 | 通过 |
| 安装后版本 | `0.19.0` | `0.19.0` |
| 非 ASCII 安装路径 | 通过 | 通过 |
| 解释器校验 | 3.9 被拒，退出码 3 | 仅用包内解释器启动成功 |
| 运行后包完整性 | 未变化 | 未变化 |
| 验收项 | 12/12 | 12/12 |

全量测试：`1326 passed, 3 skipped`。

薄 Skill：由两个平台归档组装，`artifacts/manifest.json` 声明 macOS `accepted`、Kylin `candidate`，`macos_min_version` 为 `11.0`。本机 HTTP 空缓存下载、SHA256 校验、离线安装、版本探测与缓存复用通过。公开树不含 `.py`。

## 制品摘要

| 制品 | 大小 | SHA256 |
|---|---|---|
| macOS ARM64 | 25.1 MB | `7249a7715d4f5125d6cd05bae0e8ce2ec0203b4f05ca4d49004b2f76ab31c3c2` |
| Kylin V10 ARM64 | 24.9 MB | `d73cc979bfe6416ab98f76dda088d8b3a69219bef53efd47b45bf3b0bf21093b` |

Kylin 包内解释器摘要：`578d9da1983463ffb0c8c98ab0da2f18177f44069328eb0af847860c1eb05c3e`。

## 验证边界

- 未在 Kylin V10 ARM64 真机安装与转换。容器基线为 glibc 2.36，目标机为 2.28，真机仍需复核。
- 未执行 Word/WPS 页面视觉验收。
- macOS 只覆盖 3.12 到 3.14。目标机解释器不在该范围时按 `WXDF-E-VENDOR-UNAVAILABLE` 退出，需要扩充 `WXDF_VENDOR_TAGS` 后重建。
- 随包解释器来自 `python-build-standalone`，其许可与来源需随发布说明一并记录。
- `pip download` 未启用 `--require-hashes`，`vendor.json` 中的摘要证明的是本次下载内容一致，不构成上游来源证明。

## 发布后验证

- 全新克隆公开仓库，HEAD 为 `60623ab`，当前树与全部可达历史均无 `.py`、`.pyc`、`.pyo`。
- Skill validator 通过，启动脚本 shell 语法通过。
- 空缓存运行薄 Skill，从 GitHub Release 下载、校验 SHA256、安装并输出 `0.19.0`。
- 薄 Skill 真实转换：`status=completed`，DOCX ZIP 完整。
- 离线包解压到空目录：版本 `0.19.0`，真实转换 `status=completed`，DOCX ZIP 完整。

## 本地证据

- macOS 验收：`dist/evidence/macos/acceptance.json`
- Kylin 验收：`dist/evidence/kylin/acceptance.json`
- 离线包：`dist/local/wx-doc-format-skill-0.19.0-macos-arm64`、`dist/local/wx-doc-format-skill-0.19.0-kylin-v10-arm64`
- 薄 Skill：`dist/public-skill-interpreted/`
- 随包解释器：`dist/runtime-python/kylin-v10-arm64/python`
- vendor：`dist/vendor/macos-arm64`、`dist/vendor/kylin-v10-arm64`
