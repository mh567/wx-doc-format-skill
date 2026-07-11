# CLAUDE.md — wx-doc-format-skill

## 项目

wx-doc-format：将 Markdown/DOCX 转换为 WX 模板格式的 .docx。三段式流水线（parse → normalize → render）+ LLM 语义增强。v0.9.0。

## 常用命令

```bash
cd scripts && .venv/bin/python3 -m main \
  --input ../input/xxx.docx \
  --output ../wx_output/xxx.docx \
  --template ../assets/技术文件格式及书写要求.docx \
  --report ../wx_output/report.json

# 测试
.venv/bin/python3 -m pytest ../tests/ -q
```

## 架构

| 阶段 | 模块 | 职责 |
|------|------|------|
| 解析 | `md_pipeline.py` / `docx_pipeline.py` | 输入转语义 AST |
| 规范化 | `model_normalization.py` + `llm_enhancer.py` | 清理编号、重定型、LLM 增强（Phase A/B/C） |
| 渲染 | `word_model_renderer.py` + `docx_render.py` → `template_finalizer.py` | 样式映射、图片/表格克隆、格式收口、审计 |

## 多 Agent 协作

- Hermes = 总控路由
- Codex = 设计/审查
- Claude Code = 编码实现
- 详见 AGENTS.md

## 编码偏好

- 不出现"不是...，而是"句式
- 不出现破折号
- 不随意重命名已稳定的接口
- 不修改 `main.py` 和 `document_model.py` 的公开接口

## 仓库管理

- 公开 `mh567/wx-doc-format-skill` — main（纯 skill）
- 私有 `mh567/wx-doc-format-skill-dev` — main（含开发文件）
- 本地 `main` → 公开，`dev` → 私有
