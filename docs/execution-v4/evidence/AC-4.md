# AC-4 综合验收、独立复审和发布准备证据

## 候选身份与范围

AC-4 固定候选提交为 `2dc37c309de3c5b1177a38d651700ececd4689ec`，候选树为 `8f674f83755bd4c2256b66ed7a26dc74a7f25d53`，版本为 `0.13.0`。本次验收从该提交创建干净临时副本，工作区已有的规划文件、`_build/`、`paseo.json` 和其他用户修改均未进入候选。

AC-4 只增加本证据文件，未修改唯一状态文件 `docs/execution-v4/status.json`，未新增业务能力。

## 源码真实文件验收

使用当前仓库虚拟环境执行 native-v2 普通转换：

```text
env -u WX_DOC_FORMAT_RELEASE -u WX_DOC_LLM_ENHANCE \
  scripts/.venv/bin/python3 scripts/main.py \
  --input input/test_baseline.md \
  --output /private/tmp/wxdf-ac4-source.fs0c1q/markdown.docx \
  --template assets/wx_template.docx \
  --pipeline modular --modular-profile native-v2 --list-policy wx-default-v2 \
  --llm-enhance off --report .../markdown.report.json --report-md .../markdown.report.md
```

Markdown 结果为 `completed`，pipeline 为 `modular`，profile 为 `wx-default-v2`，诊断为空。输出 DOCX ZIP 含 22 个条目，`word/document.xml` 存在。

使用 `_build/audit_fix_validation/protocol-source.docx` 执行真实 DOCX 输入转换。结果为 `completed_with_warnings`，输出 DOCX ZIP 含 29 个条目，`word/document.xml` 存在。输入携带不可用编号定义，系统记录 5 条 `invalid_numbering_definition` 信息诊断并将对应段落降级为正文，未隐藏该输入事实。

## CLI 页面渲染与字体环境复核

使用文档渲染脚本对上述两份真实输出执行完整页面渲染：Markdown 输出 24 页，DOCX 输出 15 页。初始渲染出现中文方框。只读核验确认中文字符在 DOCX XML 和 PDF 文本层均完整存在，原因定位为当前 LibreOffice 运行时未解析模板使用的 `宋体`、`黑体` 字体别名。

在临时 Fontconfig 配置中将 `宋体` 映射到 macOS `STSong`，将 `黑体` 映射到 macOS `STHeiti` 后重新渲染。Markdown 和 DOCX 页面中的中文、目录、正文、表格、图形、分页和页码均正常显示，未发现页面越界、文字裁切或图形裁切。对照渲染目录为 `/private/tmp/wxdf-ac4-render-markdown-fontfix` 和 `/private/tmp/wxdf-ac4-render-docx-fontfix`。

该结果证明候选 DOCX 内容和页面结构可正常渲染。Word/WPS 实机字体、分页和域更新确认仍属于外部门禁，继续保留未完成状态。

## 文件协议、恢复、复核和保存后审计

通过发布入口的源码验收工具执行：

```text
scripts/.venv/bin/python3 packaging/nuitka/smoke_distribution.py  # run_runtime_contracts
```

入口使用 `packaging/nuitka/release_entrypoint.py`，证据目录为 `/private/tmp/wxdf-ac4-runtime.zZ8lLf`。验收通过：

1. 真实 Markdown 和程序生成的真实 DOCX 转换。
2. 外部 command 增强提交并检查 Heading 2。
3. 文件协议多阶段等待、响应写入、resume、审计后复核和终态幂等恢复。
4. 拒绝复核时保留既有正式输出。
5. JSON 报告、Markdown 报告脱敏和 DOCX ZIP 完整性。
6. AST 导出拒绝、legacy 模式拒绝和 legacy checkpoint 拒绝。

结果键为 `markdown`、`docx`、`command_enhancement`、`file_protocol`、`review`、`rejected_review` 和 `release_guards`，全部通过。

## macOS ARM64 编译运行时

从固定候选提交建立 `/private/tmp/wxdf-ac4-candidate.6Bvhi4` 并执行：

```text
WXDF_BOOTSTRAP_PYTHON=/opt/homebrew/bin/python3.13 \
  packaging/nuitka/build-local-macos-arm64.sh
```

结果：

1. 构建解释器全量测试：`1034 passed in 186.38s`。
2. Python `3.13.15`，Nuitka `4.1.2`，系统 `Darwin`，架构 `arm64`。
3. standalone 编译成功，完整 compilation report 已保留。
4. 运行时全部 Mach-O 为 arm64，最大 ARM64 `minos` 为 `26.0`。
5. 包内模块清单与源码模块清单均为 35 项，状态为 `matched`。
6. 真实 standalone smoke、文件协议、恢复、复核、拒绝保护和离线安装通过。
7. 归档 SHA256：`82173f5fccaadd6fd93a731148801f6d4e358c09bfd503c09f0ce6e926284af9`。

验收摘要位于 `/private/tmp/wxdf-ac4-candidate.6Bvhi4/build/nuitka/local-macos-arm64/acceptance.json`。

## Linux ARM64 编译运行时

Colima 使用 aarch64、4 CPU、8 GiB 内存和固定 manylinux2014_aarch64 镜像。由于首次运行未共享 `/private/tmp`，容器无法读取候选依赖文件。重启 Colima 时仅增加 `/private/tmp` 读写挂载后，从干净固定候选重新执行：

```text
packaging/nuitka/build-local-linux-arm64.sh
```

结果：

1. 容器内全量测试：`1034 passed in 141.14s`。
2. Python `3.13.15`，Nuitka `4.1.2`，系统 `Linux`，架构 `aarch64`。
3. manylinux2014_aarch64 standalone 编译成功，完整 compilation report 已保留。
4. 运行时为 ELF 64-bit ARM aarch64。
5. 包内模块清单与源码模块清单均为 35 项，状态为 `matched`。
6. 真实 standalone smoke、文件协议、恢复、复核、拒绝保护和离线安装通过。
7. 归档 SHA256：`7d06c10e2f0a01ff67bc5c8f5d04b0692fa67fe47d372b8b15ee2e15c5fe5f8d`。

验收摘要位于 `/private/tmp/wxdf-ac4-linux-candidate.uVaElR/build/nuitka/local-linux-arm64/acceptance.json`。

## 公开薄 Skill 验证

从上述两个归档组装 `/private/tmp/wxdf-ac4-public-final`，使用当前候选对应的构建虚拟环境执行组装。结果为版本 `0.13.0`，平台为 `macos-arm64` 和 `kylin-v10-arm64`。

验证命令及结果：

```text
python3 /Users/harris/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /private/tmp/wxdf-ac4-public-final
Skill is valid!

/bin/sh -n /private/tmp/wxdf-ac4-public-final/scripts/run.sh
find ... -name '*.py' -o -name '*.pyc' -o -name '*.pyo'
无输出
```

使用 macOS ARM64 编译包执行本地 HTTP 空缓存下载、离线安装、ASCII 路径拒绝和缓存复用：

```text
/private/tmp/wxdf-ac4-public-smoke/acceptance.json
status=passed
online_http.download_count=1
offline.status=passed
ascii_guards.installer=true
ascii_guards.launcher=true
ascii_guards.cache=true
```

公开 Skill 验收产生的 macOS 输出 DOCX SHA256 为 `7850275bc26c89d7b94fafecfdd08ab763b5ce6a3a411b5e5ea95f8d306de9ab`。

## 全量测试与差异检查

当前候选源码全量测试已由两个编译入口分别执行并通过。证据文件加入后的仓库全量命令结果为 `1033 passed, 1 failed`，唯一失败为本地沙箱禁止 `127.0.0.1` HTTP 监听的安装验收单测。授权环境重跑该单测为 `1 passed, 101 deselected`。该失败未进入代码路径，属于运行环境权限限制。

```text
scripts/.venv/bin/python3 -m pytest tests/ -q
git diff --check
scripts/.venv/bin/python3 -m py_compile scripts/wxdf/*.py scripts/main.py
```

最终 `git diff --check` 和 Python 编译检查通过。

## 独立复审

AC-3 实现已经完成 Standards、Spec 双轨独立审查，并在修复事务校验、真实职责迁移和兼容边界问题后完成定向回归。AC-4 再次请求双轨复审时，当前多 Agent 配额返回 `agent thread limit reached`，未产生新的子 Agent 报告。主控已对固定基线 `646dbb0` 到候选提交执行差异检查，AC-4 本身未引入源码变更。该环境限制保持记录，不能将其表述为新的独立复审已完成。

## 外部门禁与发布状态

以下门禁未在本地完成，候选不标记为正式发布完成：

1. Kylin V10 ARM64 真机安装和转换验收。Linux ARM64 容器结果不能替代真机结果。
2. Word/WPS 页面视觉、分页和域更新确认。DOCX ZIP、OOXML 结构和本地报告不能替代桌面应用确认。
3. 远程推送、标签、GitHub Release、远程安装和发布后真实转换。项目规则明确要求单独授权，本工单未执行。
