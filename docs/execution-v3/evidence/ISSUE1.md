# Issue #1 实施证据

## 基线与范围

基线提交：`4b595c0`。

实现提交：`b44de5f`，后续修正待提交。

改动范围：native-v2 模块链、候选裁决、单批事务、列表 adapter、模块契约、CLI 受控入口和回归测试。legacy 默认入口、LLM 文件协议、Kylin 与 Windows 范围保持不变。

## 测试证据

红灯命令：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py -q`

预期失败：原生入口未接入，事务不接受 presentation override，ModuleDescriptor 没有新增契约字段。

绿灯命令：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py -q`

结果：`3 passed`。

聚焦回归：

`scripts/.venv/bin/python3 -m pytest tests/test_modular_kernel.py tests/test_modular_conversion.py tests/test_modular_lists.py tests/test_modular_list_consumption.py tests/test_modular_capability.py tests/test_modular_resolver.py tests/test_modular_ast.py -q`

结果：`50 passed`。

完整回归：

`scripts/.venv/bin/python3 -m pytest tests/ -q`

结果：`405 passed`。提交前最后一轮修正后需再次执行完整回归。

## 真实转换证据

Markdown 嵌套列表通过 `scripts/main.py --pipeline modular --list-policy wx-default-v2 --llm-enhance off` 转换成功。报告显示 native-v2 模块链、事务状态 `committed`、列表容器 2 个、列表项 3 个、嵌套列表 1 个，列表能力在模型审计和保存 DOCX 审计通过后标记为 `consumed`。

无列表 Markdown 通过显式 `--modular-profile native-v2` 进入同一 native-v2 链，报告显示 `execution_plan.profile=wx_full_rules-native-v2`，无诊断，列表能力按无候选状态标记为 `delivered`。

DOCX 列表夹具通过 `ConversionKernel` native-v2 profile 转换成功，报告无诊断，事务状态为 `committed`，列表能力标记为 `consumed`。

## 审查与残留项

双轴审查固定点为 `4b595c0`，实现提交为 `b44de5f`。审查提出入口路由仍采用受控 native-v2 门、原生函数职责较多和 L01 至 L16 覆盖尚未完整扩展。当前修正补充了 native module preflight、逐模块候选消费状态和事务审计证据字段。完整矩阵覆盖与无列表样例的全面 native-v2 切换仍需后续独立任务验证。
