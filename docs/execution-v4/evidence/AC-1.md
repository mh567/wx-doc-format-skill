# AC-1 证据：统一复核候选重处理链

## 范围

- 工单：AC-1
- 目标：复核候选通过已注册 Module 子计划完成 Resolver、Transaction、Normalization、Render 和 Audit。
- 实施提交：`5180d58`（`feat: route review candidates through native child plans`）
- 固定基线：`4ec6642a4a50bf0ed9eb5f33c9728d794a2f1db8`
- 未改动：公开 CLI 参数、legacy 诊断入口、`_build/`、`paseo.json` 及用户既有规划文件。

## 实施摘要

1. `ModuleRegistry` 支持按 Module ID 构造冻结子计划，并声明由父执行上下文提供的初始能力。
2. 新增 `native-review-candidate-set` 注册 Module，将已校验复核候选接入统一 CandidateSet 和 Resolver 接缝。
3. `native-reviewer` 仅负责复核请求、范围校验、候选接受和回滚，候选 Resolver 与后续处理通过两个连续子计划执行。
4. 候选 Transaction Module 使用隔离的 `stage_candidate_revision`，候选失败时保留基线快照。
5. 复核报告记录 `reprocess_plan` 和各 Module 的运行期证据。

## 红灯

实施前新增测试
`test_native_review_reprocesses_candidate_through_registered_child_plan`。

命令：

```text
scripts/.venv/bin/python3 -m pytest tests/test_modular_native_review.py -k 'reprocesses_candidate_through_registered_child_plan' -q
```

结果：`1 failed`。原实现只执行一次主计划，没有复核候选子计划。

## 四类回归证据

| 场景 | 测试 | 结果 |
| --- | --- | --- |
| 接受候选 | `test_native_review_uses_independent_artifacts_and_publishes_accepted_revision` | 通过 |
| 拒绝候选并保留基线 | `test_rejected_native_review_preserves_existing_formal_output` | 通过 |
| 候选事务冲突 | `test_candidate_transaction_exception_returns_explicit_rollback_with_baseline_evidence` | 通过 |
| 候选保存后审计失败 | `test_candidate_audit_exception_rolls_back_and_preserves_formal_output` | 通过 |

新增子计划测试确认执行顺序为：

```text
native-review-candidate-set
native-resolver
native-transaction
native-normalizer
native-renderer
native-auditor
```

## 验证命令

```text
git diff --check
scripts/.venv/bin/python3 -m py_compile scripts/wxdf/registry.py scripts/wxdf/builtin_modules.py scripts/wxdf/kernel.py tests/test_modular_native_review.py
scripts/.venv/bin/python3 -m pytest tests/test_modular_native_review.py tests/test_modular_kernel.py -q
```

结果：静态检查通过，`27 passed`。

完整回归：

```text
scripts/.venv/bin/python3 -m pytest tests/ -q
```

结果：`1019 passed, 1 failed`。唯一失败为 T09 HTTP 安装验收在沙箱内绑定 `127.0.0.1` 时被环境拒绝，错误为 `PermissionError: Operation not permitted`。同一测试在授权环境复跑通过：`1 passed, 101 deselected`。

## 审查

- Standards：未发现项目规则违例。
- Spec：AC-1 目标与四类回归证据均已覆盖，未发现范围外实现。
- 复核范围：固定基线 `4ec6642a4a50bf0ed9eb5f33c9728d794a2f1db8` 到实施提交 `5180d58`。
- 外部门禁：Kylin 真机、Word/WPS 页面视觉和远程发布不属于 AC-1。
