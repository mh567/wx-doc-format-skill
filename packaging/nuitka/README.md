# Nuitka 本地构建

本目录当前交付 macOS ARM64 和 Linux ARM64 两个平台。这两个发布入口会安装锁定依赖、运行测试、编译 standalone 运行时、组装离线包并执行真实转换冒烟。历史 Windows x86_64 入口只编译 standalone 运行时。

## 冻结候选源码

先更新 `VERSION`、完成源码测试并提交候选源码 C，再从干净的同一 C 构建两个平台。构建入口记录 version、commit、tree，以及 Python、Nuitka、操作系统、架构和构建镜像。编译前后及组包后重新检查工作树，源码漂移直接失败。`--skip-compile` 也必须提供属于当前 C 的 runtime。

直接依赖版本来自 `requirements-runtime.txt` 和 `requirements-build.txt`。每个平台另存 `dependency-versions.txt` 记录实际解析的完整环境。全量测试使用构建解释器，结果保存在对应 `build/nuitka/local-*/tests.log`。只纳入 `input/test_baseline.md` 和 `input/benchmark_report.md` 两个 Markdown 基线；真实 DOCX 和协议输入由验收程序在临时证据目录中生成。

公开版本 `0.12.19`、`0.16.0`、`0.16.1`、`0.17.0`、`0.17.1`、`0.17.2` 和 `0.17.3` 已有标签。`0.13.0` 已从候选 `85c73b4` 构建但未发布，不再复用；`0.14.0` 至 `0.15.2` 均未构建。`0.17.0` 的审查入口在发布包内不可用（编译运行时的 stdout 无 locale 时为 ASCII，打印中文摘要抛异常），保留其标签与资产但不要使用，改用 `0.17.3`。已发布版本的标签移动及资产替换仍需单独授权。若真实构建暴露代码问题，修复后创建新 C，并重建两个平台。构建完成后的证据提交仅允许修改文档，不与候选源码身份混用。

## macOS ARM64

需要 macOS ARM64、Xcode Command Line Tools 和 Python 3.13。

```bash
packaging/nuitka/build-local-macos-arm64.sh
```

可以通过 `WXDF_BOOTSTRAP_PYTHON` 指定 Python 3.13 可执行文件。冒烟会检查 runtime 中全部 Mach-O 的架构和 ARM64 切片最低系统版本，以其中最大值作为实测 `minos`。本地编译结果会受当前 macOS SDK 和 Python 工具链影响。

```bash
python3.13 -c 'import json; print(json.load(open("build/nuitka/local-macos-arm64/acceptance.json"))["binary"]["macos_min_version"])'
```

## Linux ARM64

需要 ARM64 Docker 环境。macOS ARM64 可使用 Colima：

```bash
colima start --arch aarch64 --cpu 4 --memory 8 --disk 20
docker context use colima
packaging/nuitka/build-local-linux-arm64.sh
```

默认镜像按 SHA256 摘要固定。如需使用内网镜像仓库，设置 `WXDF_LINUX_BUILD_IMAGE`。入口从当前 C 新建可独立读取 Git 身份的本地源码 clone，并在容器中测试、编译及验收。容器不依赖宿主 `scripts/.venv` 软链。生成的 `kylin-v10-arm64` 归档是容器候选制品，交付前必须在 Kylin V10 ARM64 真机完成安装和转换冒烟。

## Windows x86_64

该入口仅作历史兼容保留，执行测试并通过 `--compile-only` 编译 standalone 运行时后停止。它不组装离线公开包，不创建归档或 manifest，也不执行公开 package smoke。

需要 Windows x86_64、Python 3.13 和 Nuitka 支持的 C 编译工具链。

```powershell
.\packaging\nuitka\build-local-windows-x86_64.ps1
```

如果未使用 `py` launcher，通过 `BootstrapPython` 传入 Python 3.13 路径。

```powershell
.\packaging\nuitka\build-local-windows-x86_64.ps1 -BootstrapPython C:\Python313\python.exe
```

完成条件：命令输出 `build\nuitka\local-windows-x86_64\compile\release_entrypoint.dist`，该目录为历史 standalone runtime，不能作为公开 Skill 或 Release 资产发布。

## 产物和验证

当前发布目标的归档统一输出到 `dist/local/`：

```text
wx-doc-format-skill-<version>-macos-arm64.tar.gz
wx-doc-format-skill-<version>-kylin-v10-arm64.tar.gz
```

上述公开包合同仅适用于 macOS ARM64 与 Kylin V10 ARM64。发布包的安装目录必须只包含可打印 ASCII 字符。安装器会验证 `SHA256SUMS.txt`、完整文件集和运行时版本。调用 `scripts/run.sh` 时也会重新检查安装路径。

完整 Nuitka XML 先经过 root、completion、standalone、模块集合、能力合同及 SHA256 检查，保留在私有构建目录。公开离线包排除该 XML；包清单及 runtime 模块清单仅保留可核验模块摘要、XML hash 和源码身份，不复制 XML 中的源码路径。

`smoke_distribution.py` 首先拒绝脚本伪装的运行时。真实入口验收包括普通 Markdown、程序生成的真实 DOCX、外部 command 增强、文件协议多阶段等待及 resume、审计后复核修复、终态恢复幂等、拒绝复核时保护原输出、报告脱敏、ZIP 完整性、AST 导出拒绝门禁、离线安装与 ASCII 路径拒绝。外部增强响应由确定性本地适配器提供，不访问模型服务。该适配器属于私有验收工具，公开 runtime 和安装启动器不依赖 Python 源文件。

构建脚本保留私有证据目录并打印其路径，摘要另存 `build/nuitka/local-*/acceptance.json`。证据包含命令、返回码、stdout/stderr、请求、checkpoint、报告和输入输出文件，不进入公开安装包。

## 组装公开 Skill

公开仓库只保留薄 Skill 和平台运行时引导脚本。组装时要求当前交付的 macOS ARM64 和 Linux ARM64 平台：

```bash
build/nuitka/local-macos-arm64/venv/bin/python3 packaging/nuitka/assemble_public_skill.py \
  --artifacts-dir dist/local \
  --output-dir dist/public-skill \
  --require-platform macos-arm64 \
  --require-platform kylin-v10-arm64 \
  --macos-min-version <measured-minos>
```

生成入口打开每个归档，验证内部 VERSION、平台、package/runtime manifest、完整文件集合、每个文件大小与 SHA256、模块与能力合同，以及两平台同版本、同 commit 和 tree。CLI 还要求归档身份等于当前干净检出 C。仅文件名匹配的旧包不能通过。

生成后先运行开发流程中的 Skill validator、shell、无 Python 文件和 SHA256 门禁，再从当前平台执行在线薄 Skill 验收：

```bash
build/nuitka/local-macos-arm64/venv/bin/python3 packaging/nuitka/smoke_distribution.py \
  dist/local/wx-doc-format-skill-0.17.1-macos-arm64 \
  --public-skill dist/public-skill \
  --evidence-dir /tmp/wxdf-t09-public-acceptance
```

包目录、`--public-skill` 目录和证据目录都必须位于可打印 ASCII 路径下：`run.sh` 拒绝非 ASCII 安装路径，而且包路径含非 ASCII 字符时编译运行时会在解释器初始化阶段中止，验收停在版本探针上。仓库路径含非 ASCII 字符时，先把包、同目录的 `.tar.gz` 和公开树复制到 ASCII 临时目录再运行，归档必须与包目录同级（在线验收的临时服务只应答 `/<归档文件名>`）。

证据目录必须为新的 ASCII 路径。上述在线测试通过临时 `127.0.0.1` HTTP 服务验证薄 Skill 空缓存下载、归档校验、安装和缓存复用，结束后关闭监听。Linux 平台使用对应候选目录在 ARM64 容器内执行。该本地验证不替代远程 GitHub 下载、真实用户环境或 Kylin 真机验收。

`dist/public-skill/` 用于替换公开仓库当前文件。`dist/local/` 中列入 `artifacts/SHA256SUMS.txt` 的归档作为同版本 GitHub Release 资产上传。

`dist/local/` 中的每个平台归档都是完整离线 Skill 安装包，内含 `SKILL.md`、模板、启动脚本和本机运行时。内网可直接分发对应平台归档，由 Agent 解压到自身 skills 目录。公开 Git 仓库只提交薄 Skill，平台安装包保持在 GitHub Release 中。
