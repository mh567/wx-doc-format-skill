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

- 运行时只依赖 `python-docx`、`lxml`、`markdown-it-py`，版本固定在 `requirements.txt`。
- 开发依赖包含 `pytest`，声明在 `requirements-dev.txt`。
- `scripts/setup.sh` 在仓库根创建 `.venv` 并安装依赖；`--dev` 同时安装开发依赖。
- `scripts/run.sh` 依次尝试 `WXDF_PYTHON`、`$SKILL_ROOT/.venv`、`$SKILL_ROOT/scripts/.venv`、`python3`、`python3.14` 到 `python3.10`、`/usr/bin/python3`。
- 仓库不携带任何原生二进制、wheel、加密载荷或制品清单。

## 4. 发布

1. 更新 `VERSION`。
2. 在干净提交上跑全量测试与至少一次真实转换。
3. 确认仓库内原生二进制为零：

```bash
git ls-files | grep -E '\.(so|dylib|pyd|pyc|pyo|whl)$'
```

4. 构建按平台区分的离线 zip：

```bash
scripts/.venv/bin/python3 scripts/build_offline_package.py
```

5. 在当前提交上创建 `v<VERSION>` annotated tag。
6. 推送提交与 tag。
7. 将 `dist/wx-doc-format-skill-<VERSION>-<platform>.zip` 上传到 Release。

离线 zip 内包含 `wheelhouse/`，首次运行只从包内安装依赖。Release 说明写明平台与 Python 覆盖范围。

完成条件：远程 `main` 与 tag 指向预期提交，Release 附件齐全，离线解压后可直接运行。

## 5. 回滚和异常处理

- 源码或依赖缺陷通过新的提交修复。
- 已发布版本存在缺陷时优先发布新版本。
- 不移动已发布 tag，不替换 Release 资产，除非用户明确授权。
