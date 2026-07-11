# 基准对比测试方案

## 测试矩阵：6 文档 × 3 模式 = 18 次转换

### 测试文档

| # | 路径 | 类别 | 大小 |
|---|------|------|------|
| 1 | input/接口类/接口控制文件2.docx | 接口 | 77K |
| 2 | input/接口类/接口文档0708.docx | 接口 | 608K |
| 3 | input/接口类/身份接口策略下发部分.docx | 接口 | 70K |
| 4 | input/方案类/IAM全球部署高可用.docx | 方案 | 298K |
| 5 | input/方案类/生物特征认证系统集成方案.docx | 方案 | 180K |
| 6 | input/方案类/身份对象模型.docx | 方案 | 23K |

### 测试模式

| Mode | CLI 参数 | 说明 |
|------|----------|------|
| off | `--llm-enhance off` | 纯规则基准 |
| auto | `--llm-enhance auto` | 智能触发 |
| abc | `--llm-enhance abc` | 全量 LLM 增强 |

## 采集指标

### 转换性能

| 指标 | 来源 | 类型 |
|------|------|------|
| wall_time_sec | Python time.perf_counter() | float |
| output_size_kb | os.path.getsize(output) / 1024 | float |

### 规范性指标（纯规则检查）

| 指标 | 来源路径 | 期望 |
|------|----------|------|
| unexpected_styles | `template_finalizer.style_audit.unexpected_styles` | [] |
| heading_hierarchy_warnings | `audit.heading_hierarchy_warnings` | [] |
| ordered_list_nums_without_restart | `audit.ordered_list_nums_without_restart` | [] |
| markdown_residue | `audit.markdown_residue` | [] |
| table_paragraphs_not_table_body | `audit.table_paragraphs_not_table_body` | [] |
| code_sample_table_alignment_issues | `audit.code_sample_table_alignment_issues` | [] |
| risk_warnings_count | `len(report.risk_warnings)` | 0 |

### Stage 产出

| 指标 | 来源 |
|------|------|
| source_block_count | `source_document_model_summary.blocks` |
| source_heading_count | `source_document_model_summary.heading` |
| source_body_count | `source_document_model_summary.body` |
| source_table_count | `source_document_model_summary.table` |
| normalized_block_count | `document_model_summary.blocks` |
| diff_block_delta | `document_model_diff.block_count_delta` |

### LLM 增强效果

| 指标 | 来源 | 说明 |
|------|------|------|
| suspicion_score | 复算（见实现注意点） | auto 触发判断 |
| triggered_auto | suspicion >= 0.15 | auto 是否触发 |
| phase_a_applied | `llm_enhancer.applied` 条数 | Phase A 修改数 |
| phase_a_skipped | `llm_enhancer.skipped` 条数 | 低置信丢弃数 |
| phase_b_applied | Phase B 应用数 | |
| phase_c_applied | Phase C 应用数 | |
| llm_errors | `llm_enhancer.errors` 条数 | |
| phase_a_applied_rate | applied / (applied + skipped) | B/C 门控依据 |
| llm_call_count | prompt 记录数 | |
| top_reasons | applied 中 reason 分布 | 最常见修复类型 |

## 执行脚本：scripts/benchmark.py

### CLI

```bash
.venv/bin/python3 scripts/benchmark.py \
  --input-dir input/ \
  --output-dir wx_output/benchmark/ \
  --template assets/技术文件格式及书写要求.docx \
  --results wx_output/benchmark_results.json \
  --timeout 300
```

### 核心逻辑

```python
MODES = ["off", "auto", "abc"]

def run_benchmark(input_dir, output_dir, template, timeout):
    results = []
    for doc in discover_docs(input_dir):
        for mode in MODES:
            doc_name = doc.stem
            output = output_dir / f"{doc_name}_{mode}.docx"
            report = output_dir / f"{doc_name}_{mode}.json"
            start = time.perf_counter()
            try:
                subprocess.run([
                    sys.executable, "-m", "main",
                    "--input", str(doc),
                    "--output", str(output),
                    "--template", str(template),
                    "--report", str(report),
                    "--llm-enhance", mode,
                ], timeout=timeout, check=True)
                wall = time.perf_counter() - start
                metrics = collect_metrics(report, wall, output)
            except subprocess.TimeoutExpired:
                metrics = {"status": "timeout", "wall_time_sec": timeout}
            except subprocess.CalledProcessError as e:
                metrics = {"status": "error", "exit_code": e.returncode}
            metrics.update({
                "document": doc_name,
                "category": doc.parent.name,
                "mode": mode,
                "input_size_kb": doc.stat().st_size / 1024,
            })
            results.append(metrics)
    return results
```

### 失败处理

- 超时（300s）：记录 `status=timeout`，继续下一个
- 进程错误：记录 `status=error` + exit_code
- 文件不存在：记录 `status=missing_input`，跳过

## 统一结果 Schema

```json
{
  "benchmark_meta": {
    "skill_version": "0.8.5",
    "python_version": "3.14",
    "template": "assets/技术文件格式及书写要求.docx",
    "run_at": "ISO8601"
  },
  "results": [
    {
      "document": "IAM全球部署高可用",
      "category": "方案类",
      "mode": "abc",
      "input_size_kb": 298.0,
      "status": "ok",
      "wall_time_sec": 45.2,
      "output_size_kb": 312.0,
      "suspicion_score": 0.35,
      "triggered_auto": true,
      "source": {"blocks": 150, "heading": 30, "body": 100, "table": 20},
      "normalized": {"blocks": 150, "heading": 30, "body": 100, "table": 20},
      "diff": {"block_count_delta": 0},
      "compliance": {
        "unexpected_styles": [],
        "heading_hierarchy_warnings": 0,
        "ordered_list_nums_without_restart": 0,
        "markdown_residue": 0,
        "risk_warnings_count": 0
      },
      "llm_enhancement": {
        "phase_a_applied": 3,
        "phase_a_skipped": 0,
        "phase_b_applied": 1,
        "phase_c_applied": 2,
        "llm_errors": 0,
        "phase_a_applied_rate": 1.0,
        "top_reasons": {"cover_metadata": 1, "consecutive_functional_points": 2}
      }
    }
  ]
}
```

## 报告模板

### 汇总表：规范性指标对比

| 文档 | 模式 | unexpected_styles | hierarchy_warnings | list_restart | risk |
|------|------|-------------------|---------------------|--------------|------|
| IAM | off | 0 | 0 | 0 | 1 |
| IAM | auto | 0 | 0 | 0 | 0 |
| IAM | abc | 0 | 0 | 0 | 0 |
| ... | ... | ... | ... | ... | ... |

### 汇总表：LLM 增强效果

| 文档 | 模式 | suspicion | triggered | applied | skipped | errors | rate |
|------|------|-----------|-----------|---------|---------|--------|------|
| IAM | off | 0.35 | - | - | - | - | - |
| IAM | auto | 0.35 | Y | 3 | 0 | 0 | 1.0 |
| IAM | abc | 0.35 | Y | 5 | 1 | 0 | 0.83 |
| ... | ... | ... | ... | ... | ... | ... | ... |

### 图表建议

- 柱状图：各文档 × 各模式的 suspicion_score（Mermaid bar chart 或文字描述）
- 散点/表：input_size_kb × wall_time_sec（分析 LLM 开销与文档大小的关系）
- 堆叠柱状：applied/skipped/errors per document per mode

## auto 模式门控验证

验证两个门控逻辑是否按设计工作：

1. `suspicion_score >= 0.15` → Phase A 触发
2. `phase_a_applied_rate >= 0.05` → Phase B/C 触发

如果 suspicion < 0.15 但 Phase A 仍然触发了 → BUG。
如果 applied_rate < 0.05 但 Phase B/C 仍然触发了 → BUG。

## 预期假设

1. `off` 模式下全部文档的 `unexpected_styles` 应为空（已有测试保证）
2. `abc` 模式不应引入新的规范性问题
3. `abc` 模式的 applied 数量应 >= `auto` 模式（abc 强制全跑）
4. 接口类文档（含 API 示例表格）的 Phase C 修正数量应 > 方案类
5. 方案类文档（含复杂标题层级）的 Phase B 修正数量应 > 接口类
6. LLM 增强调用次数：`abc` 模式下每文档 ≤ 2 次（Phase A + B/C合并）

## 实现注意点

1. **suspicion_score 复算**：`compute_suspicion_score()` 读取 `report["parse_report"]` 子字段，但现有报告将信号（如 `suspect_visual_headings`）放在顶层。benchmark 脚本需构造 `{"parse_report": {signals...}}` 传入。
2. **输入路径**：使用 `Path(input_dir)` 递归扫描 `.docx`，排除 template 文件。
3. **超时设置**：大文档（608K）的 LLM 增强可能需要更长时间，设置 300s 超时。
4. **产出隔离**：output 和 report 写入 `wx_output/benchmark/` 子目录，不覆盖现有输出。
