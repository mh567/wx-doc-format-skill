# CLAUDE.md — wx-doc-format-skill

## 项目

wx-doc-format：将 Markdown/DOCX 转换为 WX 模板格式的 .docx。三段式流水线（parse → normalize → render）+ LLM 语义增强。

## 关键文件

| 文件 | 用途 |
|------|------|
| `scripts/main.py` | CLI 入口 |
| `scripts/llm_enhancer.py` | LLM 增强核心（Phase A/B/C + 分片） |
| `scripts/model_normalization.py` | AST 规范化 + 列项推断（F4） |
| `scripts/docx_render.py` | DOCX 路径渲染（表格/图片克隆） |
| `scripts/text_utils.py` | 文本解析工具（标题/列项/题注） |
| `scripts/benchmark.py` | 基准测试 |
| `tests/test_llm_enhancer.py` | LLM 增强测试（102 tests） |

## 注意

- 不修改 `scripts/main.py` 和 `scripts/document_model.py` 的公开接口
- Python 3.14, venv 在 `scripts/.venv/`
- 测试：`cd scripts && .venv/bin/python3 -m pytest ../tests/ -q`
- AGENTS.md 有编码偏好和关键决策记录，每次任务前参考
