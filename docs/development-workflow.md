# 私有仓库开发与发布流程

## 1. 开发模式

项目采用私有源码仓库和公开薄 Skill 仓库分离模式。

| 用途 | GitHub 仓库 | 远程名 | 本地分支 | 内容 |
|---|---|---|---|---|
| 开发 | `mh567/wx-doc-format-skill-dev` | `dev` | `dev` | 全部源码、测试、构建工具和开发文档 |
| 分发 | `mh567/wx-doc-format-skill` | `origin` | `main` | 公开 Skill、模板、平台清单和启动器 |

私有 `dev/main` 是源码和构建规则的唯一事实源。公开 `origin/main` 是由 `packaging/nuitka/assemble_public_skill.py` 生成的分发结果。

以下边界始终有效：

- 所有功能、修复、测试和构建工具变更只在本地 `dev` 开发并推送到 `dev/main`。
- 本地 `main` 只跟踪公开 `origin/main`，不承担源码开发。
- 公开发布只复制已验证的 `dist/public-skill/` 生成树。
- 私有提交不进入公开 Git 历史。公开分支禁止 merge 或 cherry-pick 私有分支。
- 推送时使用完整远程名和 refspec，避免本地分支 `dev` 与远程名 `dev` 混淆。

## 2. 日常开发流程

### 2.1 预检

```bash
git switch dev
git status --short
git branch -vv
git remote -v
```

完成条件：当前分支为 `dev`，私有远程为 `dev`，公开远程为 `origin`，未跟踪的 `_build/` 和 `paseo.json` 保持原状。

### 2.2 实现

1. 从真实输入、报告、OOXML 或红灯测试确认问题。
2. 语义修复优先进入解析和 source AST 生成阶段。
3. 保持稳定 CLI、AST 和模板接口。
4. 新增或修改 `.py` 前，按 `AGENTS.md` 声明设计、实现和审查路径。
5. 使用项目虚拟环境执行相关测试和真实转换。

### 2.3 验证

```bash
scripts/.venv/bin/python3 -m pytest tests/ -q
git diff --check
```

DOCX 功能变更还需执行真实文件转换，检查 CLI 返回码、JSON 报告、ZIP 完整性和专项审计。页面级视觉质量未验证时需明确记录。

完成条件：相关测试、全量测试和真实样例均通过，`unexpected_styles` 为空，无未解释高风险项。

### 2.4 提交私有仓库

```bash
git add <verified-files>
git diff --cached --check
git commit -m "<type>: <summary>"
git push dev dev:main
```

完成条件：本地 `dev` 与远程 `dev/main` 指向同一提交，未授权文件未进入提交。

## 3. 本地制品构建

GitHub Actions 额度受限期间，发布制品使用本地构建。

| 平台 | 构建入口 | 当前状态 |
|---|---|---|
| macOS ARM64 | `packaging/nuitka/build-local-macos-arm64.sh` | 可构建，发布前检查 Mach-O `minos` |
| Linux ARM64 | `packaging/nuitka/build-local-linux-arm64.sh` | 可构建，Kylin V10 保持真机验收门禁 |
| Windows x86_64 | `packaging/nuitka/build-local-windows-x86_64.ps1` | 历史兼容入口，仅编译 standalone runtime |

macOS ARM64 与 Linux ARM64 发布入口负责锁定依赖、执行测试、编译 standalone、组装平台包并执行真实转换冒烟，公开包产物位于 `dist/local/`。Windows 历史入口执行测试和 standalone 编译后停止，runtime 位于 `build/nuitka/local-windows-x86_64/compile/release_entrypoint.dist`，不进入公开包组装、归档、package smoke 或发布流程。

以下公开包合同仅适用于当前发布目标。版本发布前先更新 `VERSION`，然后从同一私有提交重建所有本次提供的平台制品。制品内 `VERSION`、运行时 `--version`、归档名和发布标签必须一致。

候选源码身份的冻结顺序：

1. 在隔离私有工作树完成 RED、实现、全量测试及源码审查，提交候选源码 C。
2. 从干净 C 构建 macOS 和 Linux ARM64。构建解释器执行全量测试；Linux 容器使用 C 的独立源码 clone，不读取宿主虚拟环境软链。
3. 编译前后及组包后检查 version、commit、tree 和工具链身份。清单绑定该身份及经过验证的完整 Nuitka XML hash。完整 XML 保留在私有构建证据中，公开 runtime 不携带该 XML 或其中的源码路径。
4. 如果构建暴露代码问题，修复并创建新 C，两平台全部重建。完成后的证据记录通过仅文档提交追加，已有制品继续指向原候选 C。
5. 已存在公开标签的版本不用于新制品替换。已构建未发布的 `0.13.0` 不用于新制品替换。本轮候选版本为 `0.14.2`，不移动 `v0.12.19`。

每个平台保留全量测试日志、实际依赖版本、编译日志与 XML、包内清单、归档 SHA256 和真实 standalone 验收报告。macOS 记录全部 Mach-O 架构及 ARM64 切片最大 `minos`；Linux 容器通过后仍维持候选标识。普通 Markdown/DOCX、command 增强、文件协议和恢复、复核提交与拒绝保护、报告脱敏、ZIP、离线安装、ASCII 门禁必须通过。页面级视觉排版不由 ZIP 或结构审计证明。

详细命令和平台前置条件见 [`packaging/nuitka/README.md`](../packaging/nuitka/README.md)。

## 4. 生成公开 Skill

当前发布目标仅包含 macOS ARM64 和 Linux ARM64。Windows x86_64 不再进入打包、验收或发布门禁。

```bash
build/nuitka/local-macos-arm64/venv/bin/python3 packaging/nuitka/assemble_public_skill.py \
  --artifacts-dir dist/local \
  --output-dir dist/public-skill \
  --require-platform macos-arm64 \
  --require-platform kylin-v10-arm64 \
  --macos-min-version <measured-minos>
```

`<measured-minos>` 使用本次 macOS 制品的实测值，不沿用旧版本数字。

在冻结 C 的干净工作树生成公开 Skill。生成入口读取归档内部元数据并校验完整文件集和 hash，要求两个平台及当前检出的 version、commit 和 tree 相同。公开清单保留平台各自的工具链身份。候选源码之后的证据提交不作为公开生成的源码身份。

生成树必须通过：

```bash
python3 /Users/harris/.codex/skills/.system/skill-creator/scripts/quick_validate.py dist/public-skill
/bin/sh -n dist/public-skill/scripts/run.sh
find dist/public-skill -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.pyo' \) -print
```

完成条件：Skill validator 和 shell 语法通过，`find` 无输出，`artifacts/manifest.json` 只列出实际上传的平台，每个归档 SHA256 与 `dist/local/` 一致。

发布授权前可使用 `smoke_distribution.py --public-skill dist/public-skill` 验证本机 HTTP 空缓存下载、安装和缓存复用。该步骤需要临时本地监听权限，不访问 GitHub。Kylin V10 ARM64 真机、远程 GitHub 安装、公开同步、push、tag 和 Release 是独立门禁，未授权时保持待办。

## 5. 发布公开仓库

### 5.1 公开树同步

1. 从 `origin/main` 创建全新的隔离 clone 或 worktree。
2. 确认隔离目录无未跟踪文件和字节码缓存。
3. 使用 `dist/public-skill/` 完整替换公开工作树，保留 `.git`。
4. 检查暂存树只包含公开 Skill 文件。
5. 提交并推送到 `origin/main`。

公开历史从 `a28f347` 开始。后续版本在该公开生成历史上正常追加提交。历史重写、强制推送、同版本标签移动或 Release 资产替换都需要用户明确授权。

### 5.2 公开历史门禁

在推送前检查当前树和所有可达历史：

```bash
git ls-files | rg '\.(py|pyc|pyo)$'
git log --all --name-only --format= | rg '\.(py|pyc|pyo)$'
```

两条命令都必须无输出。如果任何私有文件进入公开索引或历史，立即停止推送并重建隔离工作树。

### 5.3 标签和 Release

1. 在公开提交上创建 `v<VERSION>` annotated tag。
2. 上传本次可用平台归档和 `SHA256SUMS.txt`。
3. 未构建平台不进入 Release 资产和公开 SHA256 清单。
4. 核对 Release 为非草稿、非预发布状态，资产状态为 uploaded，GitHub digest 与本地 SHA256 一致。

## 6. 发布后验证

1. 查询远程 `main`、tag 和 Release，确认全部指向预期公开提交。
2. 从 GitHub 新鲜克隆公开仓库。
3. 重跑 Skill validator 和公开历史门禁。
4. 使用空缓存运行 `scripts/run.sh --version`，验证 GitHub Release 下载、归档 SHA256、包内完整性、安装和版本输出。
5. 将当前平台安装包解压到空的临时 skills 目录，不设置 `WX_DOC_FORMAT_*` 变量，直接执行版本检查和真实转换，验证标准内网离线安装路由。
6. 在至少一个已发布平台执行真实 Markdown 或 DOCX 转换。

完成条件：公开仓库安装体验、线上获取、离线介质获取和真实转换均通过，公开历史不含私有文件。

## 7. 回滚和异常处理

- 私有发布工具或源码缺陷通过 `dev` 新提交修复。
- 公开 Skill 或清单缺陷通过新的公开生成提交修复。
- 已发布制品缺陷优先发布新版本。
- 旧的公开源码历史不得重新推入 `origin`。
- 任何已经持有旧公开 clone 的环境都应废弃该 clone 并重新克隆，避免旧历史回流。
