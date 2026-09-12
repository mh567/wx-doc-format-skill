# AC-3 代码职责与入口边界收敛证据

## 范围

本工单对应 `docs/execution-v4/architecture-convergence.md` 中的 AC-3，目标是收敛语义模块、原生阶段、检查点与响应存储的职责边界，同时保持稳定 CLI 和现有开发测试兼容面。

固定基线：`646dbb0`。

## 实现结果

已完成以下职责拆分：

- `scripts/wxdf/semantic_modules.py` 承担语义模块实现和执行上下文。
- `scripts/wxdf/native_stages.py` 承担原生流水线阶段执行器。
- `scripts/wxdf/checkpoint_store.py` 承担检查点、事务账本和候选基线存储。
- `scripts/wxdf/response_store.py` 承担响应摘要、请求摘要和内容摘要。
- `scripts/wxdf/builtin_modules.py` 保留兼容导出面。
- `scripts/wxdf/run_store.py` 保留稳定导入和测试替换面，并转发到新的存储实现。
- `scripts/wxdf/registry.py` 直接绑定新的语义模块和原生阶段实现，并提供 `implementation_module()` 诊断入口。
- `scripts/main.py` 的稳定 `--help` 和 `--version` 行为保持不变。

开发兼容钩子仅在显式替换旧兼容模块符号时生效，生产默认路径直接使用新的职责模块。

## 红灯与绿灯

AC-3 专项测试先加入，再实现代码。初始运行因新模块、实现归属入口和发布清单约束缺失而失败。实现完成后：

```text
scripts/.venv/bin/python3 -m pytest tests/test_modular_ac3.py -q
6 passed
```

## 定向验证

```text
scripts/.venv/bin/python3 -m pytest \
  tests/test_modular_kernel.py \
  tests/test_modular_file_protocol.py \
  tests/test_native_semantic_ownership.py
105 passed

scripts/.venv/bin/python3 -m pytest tests/test_modular_resume.py -q
6 passed

scripts/.venv/bin/python3 -m pytest tests/test_t08_legacy_cutover.py -q
16 passed

scripts/.venv/bin/python3 -m pytest tests/test_modular_distribution.py \
  -k 'manifest or module_set or compilation_report or retired' -q
37 passed, 8 deselected
```

## 全量验证

```text
scripts/.venv/bin/python3 -m pytest tests/ -q
1033 passed, 1 failed
```

唯一失败为 `tests/test_t09_compiled_acceptance.py::test_installation_acceptance_uses_real_offline_and_http_launchers`。失败发生在测试环境绑定 `127.0.0.1` 监听端口时，异常为 `PermissionError: [Errno 1] Operation not permitted`。在允许本地监听的授权环境中重跑同一测试：

```text
scripts/.venv/bin/python3 -m pytest \
  tests/test_t09_compiled_acceptance.py \
  -k 'installation_acceptance_uses_real_offline_and_http_launchers' -q
1 passed, 101 deselected
```

辅助检查通过：

```text
git diff --check
scripts/.venv/bin/python3 -m py_compile \
  scripts/wxdf/*.py scripts/main.py tests/test_modular_ac3.py
```

## 审查结论

已完成 Standards 和 Spec 两个维度的独立审查。首轮发现实现仍停留在浅兼容包装层，已改为真实职责迁移并删除重复实现。后续审查发现响应完成路径缺少已应用事务校验，已恢复 `applied_transactions` 和摘要一致性检查。兼容钩子、兼容 facade 以及旧测试替换面属于明确的开发期稳定边界，生产默认绑定保持在新模块。

## 未包含的外部门禁

本工单未执行远端推送、发布标签、跨平台构建、Kylin ARM64 验收、WPS 实机验证或正式 Release。工作区中 AC-3 之外已有的用户修改保持未提交。
