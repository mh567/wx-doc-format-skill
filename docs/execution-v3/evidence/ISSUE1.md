# Issue #1 实施证据

## 基线与范围

审查基线：`4b595c0`。

原始实现：`b44de5f`。

本轮本地修复提交链：`0799c17`、`8f70c18`、`5f04ee9`、`1e91f88`、`7c8ed9a`。

改动范围：native-v2 模块链、候选裁决、单批事务、列表 adapter、模块契约、CLI 受控入口和回归测试。legacy 默认入口、LLM 文件协议、Kylin 与 Windows 范围保持不变。

## 测试证据

R03 初始红灯命令：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py -q`

结果：3 项新增测试失败，分别暴露 legacy retype 意图丢失、续段候选缺失和空列项锚点缺失。

R04 初始红灯覆盖了 native 注册表和未决决策发布阻断测试。

当前 Issue 专项命令：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py -q`

结果：`13 passed`。

聚焦回归：

`scripts/.venv/bin/python3 -m pytest tests/test_modular_kernel.py tests/test_modular_conversion.py tests/test_modular_lists.py tests/test_modular_list_consumption.py tests/test_modular_capability.py tests/test_modular_resolver.py tests/test_modular_ast.py -q`

结果：`38 passed`。

完整回归：

`scripts/.venv/bin/python3 -m pytest tests/ -q`

结果：`415 passed`。

## 真实转换证据

Markdown `input/benchmark_report.md` 通过显式 `--modular-profile native-v2` 转换成功。报告显示 `execution_plan.profile=wx_full_rules-native-v2`、事务状态 `committed`、提交 revision 为 2、模型审计和保存后审计均通过。输出执行 `unzip -t` 通过。

无列表 Markdown 通过显式 `--modular-profile native-v2` 进入同一 native-v2 链，报告显示 `execution_plan.profile=wx_full_rules-native-v2`，无诊断，列表能力按无候选状态标记为 `delivered`。

DOCX 最小列表夹具通过 `scripts/main.py --modular-profile native-v2` 转换成功。夹具包含两个 `List Number` 段落，报告显示列表 1 个、列表项 2 个、生成锚点候选 2 个且全部消费，事务状态为 `committed`，提交 revision 为 2，模型审计和保存后审计均通过。输出执行 `unzip -t` 通过。

把模板 DOCX 直接作为输入的试验因模板内图片资源缺少可解析路径失败，诊断为 `RenderError`。该试验未计入通过项。

## 审查与残留项

双轴审查固定点为 `4b595c0`，原始实现提交为 `b44de5f`。本轮已补充显式 profile 路由、列表结构单批事务、续段关系、生成锚点、legacy retype 标准语义更新、注册表预检和 failure policy 阻断。

L01 至 L16 的逐项双 parser、RenderPlan 和保存后 OOXML 矩阵尚未在本轮补齐，不能据此宣称 Issue #1 全部验收完成。Kylin V10 ARM64 真机、页面视觉和发布流程仍属于外部门禁。远程推送、标签、Release 和公开 Skill 同步均未执行。
