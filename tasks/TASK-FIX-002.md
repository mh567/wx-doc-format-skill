# TASK-FIX-002：修复 LLM 增强版 DOCX 渲染路径

## 背景

LLM 增强（Phase A）正确识别了 15 个 body→list_item 转换和 4 个 heading，但 DOCX 渲染管线处理后结构崩溃：

| 块类型 | LLM 增强后 | 渲染后 | 丢失 |
|--------|:---------:|:-----:|:---:|
| heading | 4 | 1 | -3 |
| list_item | 15 | 0 | -15 |
| body | 5 | 22 | +17 |
| caption | 3 | 6 | +3 |
| image | 0 | 4 | +4 |

## 问题

### 1. headings 丢失
DOCX 路径通过 `role_overrides` 修正段落角色，但渲染时只处理了部分 heading。需要排查 `docx_render.py` 和 `docx_pipeline.py` 中 role_overrides 对 heading 的处理。

### 2. list_item 全变成 body
15 个 list_item 在渲染后全部回退为 body。DOCX 路径对 list_item 的渲染支持不完整。

### 3. caption 翻倍（非表格加了表题注）
从 3→6，说明渲染路径自动插入了不必要的题注（API 类表格、图片被误判加表题注）。

### 4. image 块拆出
body 段落中的嵌入图片被拆成独立 image 块，可能和 list_item 失败的连锁反应有关。

## 定位方向

- `scripts/docx_render.py`：`render_docx_direct` 函数中 role_overrides 如何影响标题和列项渲染
- `scripts/docx_pipeline.py`：`infer_docx_role` 和 role_overrides 的整合方式
- 测试用例：`tests/test_llm_enhancer.py` 中的 `TestEnhanceDocumentModel`

## 验证

```bash
cd scripts && .venv/bin/python3 -m main \
  --input ../input/方案类/IAM全球部署高可用.docx \
  --output ../wx_output/iam_test_v3.docx \
  --template ../assets/技术文件格式及书写要求.docx \
  --report ../wx_output/iam_test_v3_report.json \
  --llm-enhance abc
```

要求：渲染后 `document_model_diff` 中 heading 和 list_item 无丢失，caption 数量正确。

## 需要参考的文件

- `scripts/docx_render.py`
- `scripts/docx_pipeline.py`
- `scripts/main.py`（role_overrides 传递路径）
- `scripts/llm_enhancer.py`（`build_role_overrides_from_docx` 返回格式）
- 上次修复的 `scripts/model_normalization.py`（表题注位置修正）
- `wx_output/iam_test_off_report.json`（基准版，参考正确输出）
- `wx_output/iam_test_abc_v2_report.json`（问题版）
