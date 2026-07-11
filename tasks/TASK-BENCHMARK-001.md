# TASK-BENCHMARK-001：实现并执行基准测试

## 状态

READY

## 目标

1. 创建 `scripts/benchmark.py`
2. 对 6 个测试文档执行 3 种模式（off/auto/abc）共 18 次转换
3. 采集所有指标写入 `wx_output/benchmark_results.json`

## 输入

- `docs/benchmark-plan.md`：测试方案
- `scripts/main.py`：CLI 入口
- `scripts/llm_enhancer.py`：report 结构
- `input/接口类/*.docx` + `input/方案类/*.docx`：6 个测试文档

## 输出

- 新建 `scripts/benchmark.py`
- 生成 `wx_output/benchmark_results.json`
- `wx_output/benchmark/` 目录下 18 个产物（.docx + .json report）

## 实现要求

### benchmark.py 功能

1. 递归扫描 `input/` 目录下的 `.docx`（排除 `技术文件格式及书写要求.docx`）
2. 对每个文档 × 3 模式执行转换
3. 采集 metric（按 plan 中的 schema）
4. 写入统一 JSON 结果文件

### 关键实现细节

1. **suspicion_score 复算**：从 report JSON 提取信号后构造 `{"parse_report": {...}}` 传入 `compute_suspicion_score()`
2. **超时**：每次转换 300s
3. **Phase A applied_rate 提取**：从 `report["llm_enhancer"]` 中读取
4. **reason 分布统计**：收集 applied decisions 的 reason 字段做 top-N

### CLI

```bash
cd scripts && .venv/bin/python3 benchmark.py
```

或无参数自动运行（使用默认路径）。

### 注意事项

- 只运行 `off` 和 `abc` 两个模式。`auto` 模式目前对 MD 输入有效但对 DOCX 的 suspicion 计算依赖 parse_report（见 plan 中的实现注意点1），如果 auto 无法触发则跳过。
- template 路径：`../assets/技术文件格式及书写要求.docx`
- output 目录：`../wx_output/benchmark/`

## 验收标准

- AC-01: `benchmark.py` 可无参数运行
- AC-02: 生成 `wx_output/benchmark_results.json`
- AC-03: results 包含 6 个文档 × 至少 2 个模式（off + abc）的条目
- AC-04: 每个条目的 metrics 字段完整（wall_time、compliance、llm_enhancement 等）
- AC-05: `wx_output/benchmark/` 下有对应的输出 .docx 文件
- AC-06: 现有 79 tests 不受影响

## 验证命令

```bash
cd /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts
.venv/bin/python3 benchmark.py
# 检查结果
python3 -c "import json; r=json.load(open('../wx_output/benchmark_results.json')); print(f'{len(r[\"results\"])} results')"
# 确认原有测试
.venv/bin/python3 -m pytest ../tests/ -q
```