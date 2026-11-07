# 解释型发布

本目录把 Skill 构建成“加密源码载荷 + 纯 Python loader + 随包依赖”，不再编译原生可执行文件。制品里没有第一方 Mach-O，因此 macOS 上不触发代码签名与公证约束。

## 组成

| 文件 | 作用 |
|---|---|
| `boot.py` | 随包发送的 loader：解密载荷、注册 import 查找器、执行入口模块 |
| `build_payload.py` | 收集 `scripts/` 与 `scripts/wxdf/`，逐模块压缩加密，产出 `payload.bin` 与 `payload.json` |
| `fetch_vendor.py` | 用 `pip download` 取预编译 wheel 并解压：`common` 放纯 Python 依赖，`native` 放某个 CPython 小版本的原生扩展 |
| `fetch_python.py` | 为 Linux 包取可重定位 CPython 并剪枝 |
| `requirements-vendor.txt` | 共享的纯 Python 依赖 |
| `requirements-vendor-native.txt` | 按小版本编译的原生依赖 |
| `run.sh` | 随包启动器：平台与清单校验、解释器探测、依赖选择、执行 loader |
| `install.sh` | 离线安装器：SHA256 与完整文件集校验、原子替换 |
| `build_package.py` | 组装离线包目录、写 `manifest.json` 与 `SHA256SUMS.txt`、生成归档 |
| `assemble_public_skill.py` | 从归档生成薄公开 Skill 树 |
| `smoke_distribution.py` | 验收：校验、版本探测、真实转换、审查、离线安装、Python 门禁 |

## 载荷格式

`payload.bin` 由明文头、模块表和密文体组成。模块表用模块名的 SHA-256 前 8 字节做索引，磁盘上不出现模块名。密文体是 zlib 压缩后的源码，用 SHA-256 计数器模式流加密，每个模块独立 nonce。`payload.json` 记录格式版本、模块数量、载荷摘要与模块摘要，供发布清单引用。

loader 给每个模块设置虚拟 `__file__`，例如 `<SKILL_ROOT>/scripts/main.py`。现有代码只有 `scripts/main.py` 一处使用 `__file__` 定位 `VERSION`，因此业务代码无需改动。

## 依赖策略

只有 `lxml` 是原生扩展，其余依赖是纯 Python。目录分两类：

```
vendor/
  common/    纯 Python 依赖，所有解释器小版本共用
  cp312/     只放 lxml，按 CPython 小版本编译
  cp313/
  cp314/
```

`manifest.json` 的 `runtime.python_tags` 列出包内实际覆盖的小版本，`python_requires` 由这些标签推导，启动器按解释器版本选择对应目录。默认覆盖 macOS 的 3.12 到 3.14，可通过 `WXDF_VENDOR_TAGS` 调整。目标机不联网、不装包，也不读取系统 site-packages。

`pillow` 已从运行时依赖中移除：全仓没有 `import PIL`，`python-docx` 不依赖它。

## 构建

在干净提交上执行，`--allow-dirty` 仅用于本地验证。

```bash
# 1. 共享纯 Python 依赖
scripts/.venv/bin/python3 packaging/interpreted/fetch_vendor.py \
  --kind common --output dist/vendor/macos-arm64/common

# 2. 按小版本取原生扩展
for version in 3.12 3.13 3.14; do
  tag="cp$(printf '%s' "$version" | tr -d '.')"
  scripts/.venv/bin/python3 packaging/interpreted/fetch_vendor.py \
    --kind native --platform macosx_11_0_arm64 \
    --python-version "${tag#cp}" --abi "$tag" --python-tag "$tag" \
    --output "dist/vendor/macos-arm64/$tag"
done

# 3. 离线包与归档
scripts/.venv/bin/python3 packaging/interpreted/build_package.py \
  --platform macos-arm64 \
  --vendor-root dist/vendor/macos-arm64 \
  --output-dir dist/local

# 4. 验收
scripts/.venv/bin/python3 packaging/interpreted/smoke_distribution.py \
  dist/local/wx-doc-format-skill-<version>-macos-arm64 \
  --evidence-dir /tmp/wxdf-smoke

# 5. 薄公开 Skill
scripts/.venv/bin/python3 packaging/interpreted/assemble_public_skill.py \
  --artifacts-dir dist/local \
  --output-dir dist/public-skill \
  --require-platform macos-arm64 \
  --require-platform kylin-v10-arm64 \
  --macos-min-version 11.0
```

macOS 与 Linux 的完整流程分别封装在 `build-local-macos-arm64.sh` 与 `build-local-linux-arm64.sh`。Kylin 包在 ARM64 Linux 上构建，默认随包携带 CPython 3.13，并把 vendor 目录固定为对应小版本；`WXDF_BUNDLE_PYTHON=0` 可关闭随包解释器。

## 错误码

| 退出码 | 输出 | 含义 |
|---|---|---|
| 3 | `WXDF-E-PLATFORM-UNSUPPORTED` | 平台不在发布矩阵 |
| 3 | `WXDF-E-MANIFEST-MISSING` / `WXDF-E-MANIFEST-INVALID` | 清单缺失、含 NUL 或不是合法 JSON |
| 3 | `WXDF-E-RUNTIME-UNAVAILABLE` | 清单未声明当前平台或状态为 unavailable |
| 3 | `WXDF-E-PYTHON-UNAVAILABLE` | 未找到 Python 3.10 或更新版本 |
| 3 | `WXDF-E-VENDOR-UNAVAILABLE` | 包内没有匹配解释器小版本的依赖 |
| 3 | `WXDF-E-PAYLOAD-MISSING` | 载荷文件缺失 |
| 4 | `WXDF-E-PAYLOAD` | 载荷损坏、模块缺失或完整性校验失败 |

## 边界

- 加密只保护静态制品。键随包发送，能运行包的人就能取出明文，因此这是提高阅读门槛，强度低于编译方案。
- `vendor/` 内的 `lxml` 是第三方原生扩展，由 wheel 构建方签名。它不受本项目签名策略控制，但普通 `pip install` 环境可正常加载。
- 目标机解释器小版本必须落在 `runtime.python_tags` 内，不匹配时按稳定错误码退出，不做自动降级。
- 启动器以 `-I -B` 运行，忽略 `PYTHONPATH`、`PYTHONHOME` 与用户 site-packages，依赖只来自包内 `vendor/`。
- 公开 Skill 仓库仍然不含任何 `.py`，业务源码只存在于 `payload.bin` 中。
