# Desktop 交付策略验收记录

日期：2026-10-14。

## 结果

源码已在 Skill 与配对 Desktop 仓库实现。Desktop 显式传入 desktop 策略，审计发现和检查异常保留为复核项；有效 DOCX 可交付。Skill 默认 strict，保留正式交付门禁。独立审查与复审无待修问题。

本次未构建 Nuitka、安装包或公开 Skill，未提交、推送、创建标签或 Release，已安装的 Desktop 未替换。版本文件保持原值。本记录不证明已发布安装包包含修改，也不证明 Word/WPS 页面视觉验收。

## 设计与修改

方案见 [desktop-delivery-policy.md](desktop-delivery-policy.md)。

- `scripts/wxdf/delivery.py`：统一交付决策、独立 DOCX 可读性检查、发现项与状态合同。
- `scripts/wxdf/native_stages.py`：审计异常隔离、发布策略接线、受限复核拒绝后选用有效基线。
- `scripts/wxdf/run_result.py`：消费统一决策，分开转换、审计、交付和能力迁移状态。
- `scripts/wxdf/auditing.py`：单项检查异常标为未完成，继续其余检查；格式偏差保留为提示。
- `scripts/wxdf/contracts.py` 与 `registry.py`：交付决策的注册合同。
- `scripts/main.py`：CLI 参数、检查点配置和旧严格检查点兼容，公开摘要固定文案与定位脱敏。
- `scripts/wxdf/kernel.py`：保存失败仍保留已有审计证据，交付状态标为阻断。
- 配对 Desktop 的 `native_runtime.py`、`service.py`：显式策略、结构化失败报告、有效 DOCX 校验、三份结果的进程内保存事务与回滚、同文件路径和硬链接保护。

## 测试

核心最终全量：

```text
scripts/.venv/bin/python3 -m pytest tests/ -q
1293 passed, 3 skipped in 179.29s
```

配对 Desktop 最终全量：

```text
PYTHONPATH=src <Skill 项目虚拟环境 Python> -m pytest tests/ -o addopts='' -q
54 passed in 3.58s
```

新策略与 CLI 定向回归：11 passed。两仓库 `git diff --check` 均通过。Desktop 写回的四个文件与已测试临时副本逐字节一致。

覆盖审计失败的两种策略、审计异常、必需审计缺项、空发现的失败审计、损坏 DOCX、SourceFacts 变更、公开摘要原始字符串过滤、检查点策略冻结、失败诊断保留、原有文件保存回滚、回滚失败备份保留、源路径和硬链接碰撞、无效运行时报告与无效输出。

首轮全量发现两项实现回归：格式发现影响能力迁移 consumed 判定，advisory False 影响 bundle 门禁。两项均建立红灯后修复，原有测试通过，最终全量无失败。初次本地 HTTP 测试受沙箱监听限制，授权本机监听后单项及最终全量通过，不存在剩余权限阻塞。

## 真实用户手册

输入为用户提供的 `副本1管理平台用户手册-v10.docx`，使用 `assets/wx_template.docx`。

| 策略 | CLI 退出码 | 转换状态 | 审计状态 | 交付状态 |
|---|---:|---|---|---|
| strict | 1 | completed | issues | blocked |
| desktop | 0 | completed | issues | published |

Desktop service 通过实际子进程调用源码 CLI，并在最终工作区生成 DOCX、JSON 与 HTML。临时 source adapter 仅用于验证调用协议，来源元数据属于测试夹具，未使用重新编译的原生二进制。

生成文件位于 `wx_output/desktop-delivery-validation/管理平台用户手册_WX格式.docx`，报告位于同目录。应用结果为 `review`，报告保留以下三处行内差异：

- `u-50f8e7694e82ec21`，渲染映射第 178 段。
- `u-db3eddab0bda9525`，渲染映射第 607 段。
- `u-f771e07d949a5b65`，渲染映射第 649 段。

输出通过 ZIP 检查与 python-docx 读取，模板外样式数量为 0。输出含 755 个顶层段落与 3 张表格。DOCX SHA256 为 `c205ebd83cae49dbd834309db1430e36341a9c38f7c234d105c36a5e3db881ab`。

原题注误报仍保留，未修改解析或题注生成逻辑。本次验证交付策略，并未把这些差异改写为审计通过。
