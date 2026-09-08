# 方案 3.0 交接说明

## 当前交付

T00 至 T16 已完成源码实现、合同测试、模块化纯规则路径、本机性能候选和 macOS ARM64 编译门禁。默认入口仍为 legacy。完整状态见 `docs/execution-v3/status.json`，T16 证据见 `docs/execution-v3/evidence/T16.md`。

本机发布包：

`dist/local/wx-doc-format-skill-0.12.19-macos-arm64.tar.gz`

该包已完成版本检查、报告脱敏检查、真实 Markdown 转换、显式 modular 转换、legacy 文件式恢复和 DOCX ZIP 完整性检查。包内模块清单显示源码和编译运行时均为 33 个 `wxdf` 模块，合同版本和模式清单已写入 `manifest.json`。

## 未决能力

- `modular_online`：closed，候选接受内核接入尚未完成。
- `modular_file`：closed，模块化 CLI 检查点恢复接入尚未完成。
- `modular_review`：closed，模块化审计后复核重渲染接入尚未完成。
- Kylin V10 ARM64：`pending_external`，需要目标 ARM64 真机安装和转换验收。
- Windows x86_64：`pending_external`，需要目标主机和 Python 3.13 工具链验收。
- 页面视觉抽检：`visual_pending`。

## 测试与性能

- 全量测试：349 passed。
- T16 发行聚焦：43 passed。
- T16 相关回归：47 passed。
- T15 基准报告：`wx_output/modular-v3/benchmark.json`。
- 最终候选资格：small、medium、large 三档均通过 5% 时间和 10% 内存门槛，`global_default_changed=false`。

## 当前默认与回退命令

默认行为保持 legacy，旧调用可继续使用：

```bash
env -u WX_DOC_FORMAT_RELEASE -u WX_DOC_LLM_ENHANCE \
  scripts/.venv/bin/python3 scripts/main.py \
  --input input/test_baseline.md \
  --output wx_output/modular-v3/handoff-legacy.docx \
  --template assets/wx_template.docx \
  --pipeline legacy \
  --list-policy legacy \
  --llm-enhance off \
  --report wx_output/modular-v3/handoff-legacy.json
```

显式试用已开放的 modular 纯规则路径：

```bash
env -u WX_DOC_FORMAT_RELEASE -u WX_DOC_LLM_ENHANCE \
  scripts/.venv/bin/python3 scripts/main.py \
  --input input/test_baseline.md \
  --output wx_output/modular-v3/handoff-modular.docx \
  --template assets/wx_template.docx \
  --pipeline modular \
  --list-policy wx-default-v2 \
  --llm-enhance off \
  --report wx_output/modular-v3/handoff-modular.json
```

发行包安装目录必须使用可打印 ASCII。工作区路径含中文时，先将归档解压到合规目录，再执行包内 `scripts/run.sh` 或 `scripts/run.ps1`。

## 平台状态

本机 macOS ARM64 构建日志：`wx_output/modular-v3/T16/macos-build.txt`。Nuitka 报告显示 standalone 二进制的 `minos 26.0`。本地发布包只代表 macOS ARM64 证据，不能替代 Kylin V10 ARM64 或 Windows x86_64 验收。

本轮未执行远程推送、标签、Release、公开树替换或安装副本同步。后续若要发布，先完成两个外部平台验收和页面视觉抽检，再按 `docs/development-workflow.md` 执行发布流程，并重新确认默认切换授权。
