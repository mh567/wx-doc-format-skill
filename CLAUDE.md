# CLAUDE.md — wx-doc-format-skill

## 项目

wx-doc-format v0.11.1：Markdown/DOCX 转 WX 模板格式。三段式流水线 + LLM Capability 插拔增强。

## 架构

```
解析 → AST → LLM Capability(可选) → 规范化 → 渲染
              ↓
    list_detect / caption_gen
```

## 关键文件

| 文件 | 用途 |
|------|------|
| scripts/main.py | CLI 入口，--llm-enhance list_detect|caption_gen|all |
| scripts/llm_enhancer.py | LLM 增强核心，Capability 插拔架构 |
| scripts/model_normalization.py | AST 规范化 |
| scripts/docx_render.py | DOCX 渲染 |
| scripts/template_finalizer.py | 格式收口、全框线、附录合并 |
| tests/test_llm_enhancer.py | LLM 增强测试 |

## 注意

- 不修改 main.py 和 document_model.py 的公开接口
- venv: scripts/.venv/
- 测试: cd scripts && .venv/bin/python3 -m pytest ../tests/ -q
- AGENTS.md 有编码偏好和关键决策记录
