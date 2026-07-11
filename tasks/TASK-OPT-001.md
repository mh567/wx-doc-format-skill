# TASK-OPT-001：实现优化项

## 状态

READY

## 目标

按照 `docs/optimization-plan.md` 实现 4 项优化。

## 实现步骤

### 1. Phase B 按章节分片调用

在 `scripts/llm_enhancer.py`：
- 新增 `PHASE_B_SECTION_BATCH_SIZE = 20`
- 新增 `_iter_batches(items, size)` 通用分片函数
- 新增 `_collect_phase_b_sections(model)` 按标题切 section
- 修改 `_build_phase_b_prompt()` 支持 `sections_override` + `batch_meta`
- 在 `enhance_document_model()` Phase B 走批处理：分片 → 每片调用 LLM → 合并 patch 应用
- 每批 prompt 带前序标题路径摘要（跨章节上下文）

### 2. Phase C 按表格批次调用 + JSON schema 预校验

在 `scripts/llm_enhancer.py`：
- 新增 `PHASE_C_TABLE_BATCH_SIZE = 10`
- 新增 `_collect_phase_c_table_groups(model)`：每个 group 含表格、邻近 caption、最近 heading
- 新增 `_prevalidate_patch_schema(patch, phase, allowed_ops)`：结构校验（别于现有 validate_patch）
- 修改 `_build_phase_c_prompt()` 支持 `groups_override`
- Phase C 走批处理，逐批应用

### 3. per-phase 耗时和 token 估算记录

在 `scripts/llm_enhancer.py`：
- 新增 `_record_phase_metric(enh, metric)` 
- 在每次 LLM 调用前后记录 `time.perf_counter()`
- `phase_metrics` 字段：phase, batch_index, batch_count, prompt_chars, estimated_tokens(ceil/4), wall_time_sec, status, applied_count, skipped_count, error_count
- 失败批次也要有 phase_metrics

### 4. benchmark.py 补充完善

在 `scripts/benchmark.py`：
- 错误场景补充 `output_size_kb`（不存在时 0.0）
- `llm_enhancement` 增加 `phase_metrics` 摘要
- 修正 `run_bench_standalone.py` 的 Phase B/C 统计减差逻辑

### 5. 测试

- Phase B 超过 20 section 时多次调用
- Phase C 超过 10 table group 时多次调用
- schema 预校验拒绝错误结构
- phase_metrics 在成功/失败时均存在

## 允许修改

- `scripts/llm_enhancer.py`
- `scripts/benchmark.py`
- `scripts/run_bench_standalone.py`（如存在）
- `tests/test_llm_enhancer.py`

## 禁止修改

- `scripts/main.py`
- `scripts/model_normalization.py`
- 其他 .py 文件

## 验证

```bash
cd /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts
.venv/bin/python3 -m pytest ../tests/ -q
```