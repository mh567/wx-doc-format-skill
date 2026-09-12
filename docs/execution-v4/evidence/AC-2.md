# AC-2 证据：能力合同和执行调度

## 范围

- 工单：AC-2
- 目标：为关键运行时能力提供版本和载荷合同，显式验证 Module 依赖，固定同阶段调度顺序，并审计运行时服务与选项访问。
- 实施提交：`49de8ba`（`feat: enforce modular capability contracts`）、`23b9cbf`（`fix: preserve native module ordering contracts`）
- 固定基线：`201968e`
- 未改动：公开 CLI 参数、legacy 诊断入口、`_build/`、`paseo.json` 及用户既有规划文件。

## 实施摘要

1. `CapabilityContract` 为关键能力登记版本和顶层载荷形状，`ExecutionPlan` 保存冻结后的能力合同版本。
2. `ModuleDescriptor` 支持 `requires_versions`、`provides_versions`、`runtime_services` 和 `option_keys`，计划构建阶段检查版本、缺失依赖、重复提供和阶段顺序。
3. 同阶段 Module 使用依赖就绪后的模块标识和版本进行确定性排序，循环依赖直接阻断计划生成。
4. `ModuleExecutionContext` 观测能力、服务和选项访问，执行结束时由注册表验证声明合同。
5. `provide` 和 `require` 对能力顶层载荷执行合同校验，阻止非法载荷进入运行时能力总线。

## 红灯

实施前新增 `tests/test_modular_ac2.py`，用于证明 AC-2 的合同缺口。

命令：

```text
scripts/.venv/bin/python3 -m pytest tests/test_modular_ac2.py -q
```

结果：`5 failed, 3 passed`。失败覆盖版本声明缺失、循环依赖诊断、同阶段顺序漂移、非法载荷、未声明服务和选项访问。

## 六类负向证据

| 类别 | 测试 | 结果 |
| --- | --- | --- |
| 能力版本不匹配 | `test_plan_rejects_capability_version_mismatch` | 通过 |
| 缺失依赖 | `test_plan_rejects_missing_dependency` | 通过 |
| 循环依赖 | `test_plan_rejects_circular_dependency` | 通过 |
| 同阶段顺序变化 | `test_plan_orders_same_stage_modules_deterministically` | 通过 |
| 未声明读写 | `test_runtime_rejects_undeclared_capability_access[read]`、`[write]` | 通过 |
| 非法载荷 | `test_runtime_rejects_illegal_capability_payload` | 通过 |

运行时通道额外验证：`test_runtime_rejects_undeclared_service_and_option_access` 通过，确认 `request.options` 和 `services` 的未声明访问被阻断。

## 定向回归

```text
scripts/.venv/bin/python3 -m pytest tests/test_modular_ac2.py -q
```

结果：`8 passed`。

```text
scripts/.venv/bin/python3 -m pytest tests/test_modular_kernel.py tests/test_modular_file_protocol.py tests/test_modular_native_review.py -q
```

结果：`91 passed`。

## 全量回归

```text
scripts/.venv/bin/python3 -m pytest tests/ -q
```

沙箱结果：`1027 passed, 1 failed`。唯一失败为 `test_installation_acceptance_uses_real_offline_and_http_launchers`，失败点是沙箱禁止绑定 `127.0.0.1`，错误为 `PermissionError: Operation not permitted`。

授权环境复跑：

```text
scripts/.venv/bin/python3 -m pytest tests/test_t09_compiled_acceptance.py -k 'installation_acceptance_uses_real_offline_and_http_launchers' -q
```

结果：`1 passed, 101 deselected`。

## 审查

- Standards：未发现项目规则违例。
- Spec：六类负向证据和运行时服务、选项访问合同均已覆盖，未发现范围外实现。
- 复核范围：固定基线 `201968e` 到实施提交 `23b9cbf`，包含 `49de8ba` 的合同实现。
- 外部门禁：真实 Word/WPS 页面视觉、Kylin 真机和远程发布不属于 AC-2。
