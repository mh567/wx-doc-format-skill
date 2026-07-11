无法写入 `reviews/TASK-OPT-001-review.md`：当前沙箱是只读，`apply_patch` 被拒绝。审查内容如下，可直接作为该文件内容。

```markdown
# TASK-OPT-001 实现审查

## 结论

当前实现还不建议合并。Phase B/C 的基础分批调用和逐批应用已落地，schema 预校验方向合理，`benchmark.py` 主脚本的错误场景 `output_size_kb` 和 `phase_metrics` 采集也已补齐。但仍有几处影响验收或实际运行的问题。

## Findings

### 1. Phase C 空 table groups 回退没有按注释执行

位置：`scripts/llm_enhancer.py:1286`

当 `_collect_phase_c_table_groups()` 返回空时，代码设置 `batches = [[]]`，随后把空 list 作为 `groups_override` 传给 `_build_phase_c_prompt()`。这会生成空的相关 block 列表。注释说要做 full-model call 处理 standalone captions，实际 prompt 不包含任何模型 block。

严重程度：High

建议：空 groups 回退时传 `groups_override=None`，或显式构造包含 caption 的 fallback groups。补一个无 table 但有 caption 的测试。

### 2. Phase A 没有 phase_metrics

位置：`scripts/llm_enhancer.py:1078`

任务要求每次 LLM 调用记录 `phase_metrics`，并覆盖成功、失败、超时。当前 Phase B/C 覆盖了常见分支，但 Phase A 完全没有 `_record_phase_metric()`。`abc` 或 `auto` 模式下，benchmark 会缺少 Phase A 的耗时和 token 估算。

严重程度：High

建议：Phase A 也记录 `phase=A`、`batch_index=0`、`batch_count=1`、`prompt_chars`、`estimated_tokens`、`wall_time_sec`、`status`、`applied_count`、`skipped_count`、`error_count`。补成功、失败、空响应、解析失败、validation failed 测试。

### 3. `run_bench_standalone.py` 会直接 NameError

位置：`scripts/run_bench_standalone.py:121`

脚本调用了 `os.chdir(...)`，但顶部没有 `import os`。任务要求同步修正 standalone benchmark，当前 runner 会在第一份存在的文档执行时失败。

严重程度：High

建议：补 `import os`，并运行或静态检查 standalone runner。错误场景也建议保留 `message` 字段，和 `benchmark.py` 主脚本对齐。

### 4. Phase B 跨批 heading context 可能使用失效标题

位置：`scripts/llm_enhancer.py:1143`

Phase B 在批处理前一次性收集 `sections`。如果第一批把封面、版本、目录等 heading 降级为 body，第二批的 `前序章节` 仍会包含这些初始标题。逐批应用已发生，但跨批上下文摘要没有反映应用后的模型状态。

严重程度：Medium

建议：每批应用后基于当前 model 重建前序 heading 摘要，或只取当前仍为 heading 的 block，并携带 level。

## 已核对的正确点

1. Phase B 多 section 分批调用已实现。
2. Phase C 按 table group 分批已实现，group 包含最近 heading、相邻 caption 和 table。
3. Phase B/C 成功、LLM 异常、空响应、JSON 解析失败、schema 预校验失败、validate_patch 失败都会写入 `phase_metrics`。
4. `_prevalidate_patch_schema()` 只做结构检查，细节留给 `validate_patch()`，边界合理。
5. `scripts/benchmark.py` 主脚本错误场景已补 `output_size_kb`，正常场景已输出 `phase_metrics`。
6. `scripts/benchmark.py` 对 `phase_summaries` 的 Phase B/C 统计已按当前 per-phase summary 读取。

## 测试情况

尝试运行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python3 -m pytest -p no:cacheprovider tests/test_llm_enhancer.py -q
```

未能进入测试收集阶段。当前只读沙箱没有可用临时目录，pytest 在创建 capture 临时文件时报 `FileNotFoundError: No usable temporary directory found`。
```