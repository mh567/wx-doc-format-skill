# 开发与发布流程

## 1. 开发模式

项目只维护一个公开仓库 `mh567/wx-doc-format-skill`。源码、测试、模板与文档同仓，公开仓库就是唯一事实源。

- 本地 `dev` 推送到公开仓库的 `main`。
- 不保留私有仓库、生成树或制品发布链路。
- 历史重写与强制推送需要用户明确授权。

## 2. 日常开发流程

### 2.1 预检

```bash
git switch dev
git status --short
git branch -vv
git remote -v
```

完成条件：当前分支为 `dev`，未跟踪的 `_build/` 与 `paseo.json` 保持原状。

### 2.2 实现

1. 从真实输入、报告、OOXML 或红灯测试确认问题。
2. 语义修复优先进入解析和 source AST 生成阶段。
3. 保持稳定 CLI、AST 和模板接口。
4. 新增或修改 `.py` 前，按 `AGENTS.md` 声明设计、实现和审查路径。
5. 使用本目录虚拟环境执行相关测试和真实转换。

### 2.3 验证

```bash
./scripts/setup.sh --dev
.venv/bin/python3 -m pytest tests/ -q
git diff --check
```

DOCX 功能变更还需执行真实文件转换，检查 CLI 返回码、JSON 报告、ZIP 完整性和专项审计。页面级视觉质量未验证时需明确记录。

完成条件：相关测试、全量测试和真实样例均通过，`unexpected_styles` 为空，无未解释高风险项。

### 2.4 提交

```bash
git add <verified-files>
git diff --cached --check
git commit -m "<type>: <summary>"
git push origin dev:main
```

完成条件：本地 `dev` 与公开仓库 `main` 指向同一提交，未授权文件未进入提交。

## 3. 依赖与运行环境

- 运行时要求 Python 3.8 或更新版本，依赖 `python-docx`、`lxml`、`markdown-it-py`、`typing_extensions`，按解释器版本在 `requirements.txt` 固定版本。Python 3.8 使用兼容版本，较新解释器保留现有依赖。
- 开发依赖包含 `pytest`，声明在 `requirements-dev.txt`。
- 源码安装的 `scripts/setup.sh` 在仓库根创建 `.venv` 并安装依赖；`--dev` 同时安装开发依赖。离线包由启动入口直接准备并加载 `.deps/` 私有依赖，不依赖 venv、pip 或 ensurepip。
- `scripts/run.sh` 依次尝试 `WXDF_PYTHON`、`$SKILL_ROOT/.venv`、`$SKILL_ROOT/scripts/.venv`、`python3`、`python3.14` 到 `python3.8`、`/usr/bin/python3`，并把选中的解释器传给安装脚本。
- 仓库不携带任何原生二进制、wheel、加密载荷或制品清单。

## 4. 发布

1. 更新 `VERSION`。
2. 在干净提交上跑全量测试与至少一次真实转换。
3. 确认仓库内原生二进制为零：

```bash
git ls-files | grep -E '\.(so|dylib|pyd|pyc|pyo|whl)$'
```

4. 构建每个平台一个的统一离线 zip：

```bash
scripts/.venv/bin/python3 scripts/build_offline_package.py \
  --macos-cp38-wheel /absolute/path/lxml-5.4.0-cp38-cp38-macosx_11_0_arm64.whl
```

macOS ARM64 的 CPython 3.8 wheel 需在构建机用原生 Python 3.8 和官方 lxml 5.4.0 源码预先构建；官方没有提供该 wheel。使用系统 XML/XSLT，避免引入目标机不存在的 Homebrew 动态依赖：

```bash
MACOSX_DEPLOYMENT_TARGET=11.0 \
XML2_CONFIG=/usr/bin/xml2-config XSLT_CONFIG=/usr/bin/xslt-config \
python3.8 -m pip wheel --no-index --no-deps --no-build-isolation \
  --wheel-dir /absolute/path/wheels /absolute/path/lxml-5.4.0.tar.gz
```

核验官方源码摘要、生成 wheel 的 ARM64 架构、最低系统版本和 `otool -L`，仅允许目标系统自带动态库。wheel 保留在构建临时目录或离线包内，禁止提交。构建 Linux 包可单独传 `--platform linux-arm64`，无需 macOS wheel。

5. 在当前提交上创建 `v<VERSION>` annotated tag。
6. 推送提交与 tag。
7. 将 `dist/wx-doc-format-<VERSION>-<platform>.zip` 上传到 Release。

离线 zip 内包含 `wheelhouse/manifest.json` 与 `wheels.tar.xz`，覆盖常规 CPython 3.8 至 3.14。wheel 内部成员内容完整保留，以 ZIP_STORED 重包后整体 LZMA 压缩。构建机用 pip 内置的 packaging 解析 `requirements.txt`，把每个 CPython tag 的精确依赖写入 `environments`；目标机只需标准系统 Python 及其自带 lzma。首次运行按清单解出 wheel 并校验摘要，完整展开包树到 Skill 私有 `.deps/`，保留资源与版本元数据，直接加入加载路径；后续复用校验通过的缓存。离线链不创建 venv、不调用 pip 或 ensurepip、不访问网络。每包必须小于 14,000,000 字节，超限时构建失败并移除该包。Release 说明写明 macOS 11+ ARM64、glibc 2.28+ Linux ARM64 与 Python 覆盖范围。

完成条件：远程 `main` 与 tag 指向预期提交，Release 附件齐全，离线解压后可直接运行。

## 5. 回滚和异常处理

- 源码或依赖缺陷通过新的提交修复。
- 已发布版本存在缺陷时优先发布新版本。
- 不移动已发布 tag，不替换 Release 资产，除非用户明确授权。
