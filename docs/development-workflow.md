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
| Windows x86_64 | `packaging/nuitka/build-local-windows-x86_64.ps1` | 入口已就绪，制品可暂缺 |

每个构建入口负责锁定依赖、执行测试、编译 standalone、组装平台包并执行真实转换冒烟。产物位于 `dist/local/`。

版本发布前先更新 `VERSION`，然后从同一私有提交重建所有本次提供的平台制品。制品内 `VERSION`、运行时 `--version`、归档名和发布标签必须一致。

详细命令和平台前置条件见 [`packaging/nuitka/README.md`](../packaging/nuitka/README.md)。

## 4. 生成公开 Skill

当前允许 macOS ARM64 和 Linux ARM64 先行发布，Windows 制品缺失不阻断组装。

```bash
scripts/.venv/bin/python3 packaging/nuitka/assemble_public_skill.py \
  --artifacts-dir dist/local \
  --output-dir dist/public-skill \
  --require-platform macos-arm64 \
  --require-platform kylin-v10-arm64 \
  --macos-min-version <measured-minos>
```

`<measured-minos>` 使用本次 macOS 制品的实测值，不沿用旧版本数字。

生成树必须通过：

```bash
python3 /Users/harris/.codex/skills/.system/skill-creator/scripts/quick_validate.py dist/public-skill
/bin/sh -n dist/public-skill/scripts/run.sh
find dist/public-skill -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.pyo' \) -print
```

完成条件：Skill validator 和 shell 语法通过，`find` 无输出，`artifacts/manifest.json` 只列出实际上传的平台，每个归档 SHA256 与 `dist/local/` 一致。

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
5. 将当前平台归档放入公开 Skill 的 `artifacts/` 目录，在空缓存且不设置 `WX_DOC_FORMAT_*` 变量的环境重跑首次转换，验证内网离线路由。
6. 在至少一个已发布平台执行真实 Markdown 或 DOCX 转换。

完成条件：公开仓库安装体验、线上获取、离线介质获取和真实转换均通过，公开历史不含私有文件。

## 7. 回滚和异常处理

- 私有发布工具或源码缺陷通过 `dev` 新提交修复。
- 公开 Skill 或清单缺陷通过新的公开生成提交修复。
- 已发布制品缺陷优先发布新版本。
- 旧的公开源码历史不得重新推入 `origin`。
- 任何已经持有旧公开 clone 的环境都应废弃该 clone 并重新克隆，避免旧历史回流。
