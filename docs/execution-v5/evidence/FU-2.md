# FU-2 证据：T01/T02 审查遗留项收口

## 范围

- 工单：FU-2
- 目标：收口 T01/T02 两轴审查遗留的五项归属与声明问题。
- 固定基线：`d3d8e67`（FU-3 完成提交）。
- 实施方式：前台直接实现（后台智能体两次因 provider 中断未落盘），主 Agent 收口。

## 实施摘要

1. **`build_native_run_result` 归属（含 registry 职责分离）**：把 `registry.build_native_run_result` 整体迁入 `scripts/wxdf/run_result.py`（`RunResult` 的归属 module）。`registry` 回到纯 plan 与报告 helper。`kernel` 改从 `run_result` 导入该函数。回归测试 `test_run_result_owns_the_run_result_translation` 改从 `run_result` 断言。
2. **`source_projection` 死参数（第 2 项）**：核查后，`project_source_facts` 的 `caption_semantics`、`appendix_semantics` 可选参数没有任何仓库内生产调用方，仅被 `tests/test_t02_semantic_ownership.py` 传入。按“无生产调用方则移除推测性钩子”的处理移除之，并删除断言该行为的过时测试 `test_source_projection_reads_published_caption_semantics`。
3. **`list_item_map` 语义声明（第 3 项）**：在 `contracts.py` 的 `list_item_map` 能力合同处补注释，声明其形状为“源列表项标识 -> 所属 native list container”。
4. **`ledger_replay_report` 归属（第 5 项）**：`decision_ledger.ledger_replay_report(context)` 改为显式参数 `ledger_replay_report(ledger_replay, decision_set=None)`，不再直接读 `context.artifacts`（消除 Feature Envy）。`kernel` 与 `run_result` 调用点改为传入值。`test_t01_kernel_static_relocation.py` 的对应测试按新签名更新。

## 红灯

核查阶段确认：`project_source_facts` 的 caption/appendix 参数无生产调用方（grep 仅命中测试与证据文档）。

## 绿灯 / 全量

聚焦回归：`tests/test_t01_kernel_static_relocation.py`、`tests/test_t02_semantic_ownership.py`、`tests/test_modular_kernel.py`、`tests/test_modular_source_projection.py`、`tests/test_modular_resolver.py`、`tests/test_modular_ledger.py` → 51 passed。

全量测试：`scripts/.venv/bin/python3 -m pytest tests/ -q` → `1128 passed in 132.59s`。

## 审查

本工单为前台直接实现，未另跑独立智能体审查；改动由主 Agent 按 `AGENTS.md` 编码偏好核对（无破折号、无「不是…而是」）。

## 开放项

- 本次未触碰 `checkpoint_store.py`、`response_store.py`、`request_append.py`、`errors.py`、`response_identity.py`、`digest.py`、`transaction.py`、`native_stages.py`（均在 FU-2 声明的限制清单内）。
- 未提交、未改动 `docs/execution-v5/status.json`（由主 Agent 负责）。