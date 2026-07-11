# TASK-LLM-ENHANCE-001：实现 llm_enhancer.py 核心模块

## 状态

READY

## 目标

创建 `scripts/llm_enhancer.py`，实现 LLM 语义增强的核心基础能力：JSON 提取、schema 校验、patch 应用、report 记录。

## 输入

- `docs/llm-enhancer-design.md`：完整设计方案
- `scripts/document_model.py`：AST 模型定义和 validate_document_model
- `scripts/list_semantic_enhancer.py`：现有 LLM 模块（参考 llm_call 回调和 build_role_overrides_from_docx 结构）
- `scripts/text_utils.py`：文本工具函数

## 输出

新增文件：`scripts/llm_enhancer.py`

## 实现要求

### 1. 核心函数签名

```python
def extract_json_object(raw: str) -> dict | None
    """从 LLM 输出文本中提取 JSON 对象。从第一个 { 到最后一个 } 截取并解析。"""

def validate_patch(patch: dict, model: dict, allowed_ops: set[str]) -> list[dict]
    """校验 LLM 返回的 patch 对象。
    返回错误列表。空列表表示通过。
    检查：schema_version、phase、decisions 数组、block_id 存在性、operation 合法性。
    """

def apply_patch_to_model(model: dict, patch: dict, report: dict) -> dict
    """将校验通过的 patch 应用到 model。
    返回修改后的 model（直接修改传入对象）。
    将 applied/skipped/errors 写入 report["llm_enhancer"]。
    """

def enhance_document_model(
    model: dict, report: dict, *, phase: str, llm_call=None, hint: str = None
) -> dict
    """核心入口：根据 phase 调用 LLM 增强 AST。
    llm_call 为 None 时直接返回原 model。
    phase: "A" | "B" | "C" | "C1"
    hint: 用户提示词，注入到 prompt
    """

def compute_suspicion_score(report: dict) -> float
    """基于 Step 1 解析报告计算文档可疑度分数 0-1。"""

def should_enhance(report: dict, phase: str, mode: str) -> bool
    """判断是否应该触发 LLM 增强。
    mode: "off" | "auto" | "a" | "ab" | "abc" | "force-a" | "force-ab" | "force-abc"
    """

def build_role_overrides_from_docx(
    src_doc, strict_normalize: bool, *, llm_call=None
) -> dict[int, str]
    """兼容现有 list_semantic_enhancer 接口，内部转调 enhance_document_model phase A。"""
```

### 2. Patch Schema 和约束

按照设计文档中的 Patch Schema 实现：
- `confidence < 0.70` 不应用，写入 report.skipped
- `block_id` 不存在 → 写入 report.errors，丢弃该条
- `operation` 不在 `allowed_ops` → 写入 report.errors，丢弃该条
- 应用后不调用 `validate_document_model()`（由调用方决定是否校验）

### 3. 允许的操作集（per phase）

- Phase A: {"retype"} — 只允许 body↔heading↔list_item↔caption 转换
- Phase B: {"retype", "adjust_level", "set_restart"}
- Phase C: {"retype", "set_table_type", "set_header_rows", "set_caption_type"}
- Phase C-1: {"set_caption_text"} — 只允许修改 caption block 的 text

### 4. 错误处理

- LLM 调用异常 → 捕获，写入 `report["llm_enhancer"]["errors"]`，返回原 model
- JSON 解析失败 → 记录 raw 前 500 字符到 errors，返回原 model
- Schema 校验失败 → 整批 patch 丢弃，记录到 errors
- 单条 decision 不合法 → 丢弃该条，保留其他合法条

### 5. suspicion_score 实现

按设计文档中的 7 维加权评分表实现。从 `report["source_document_model_summary"]` 和 `report.get("parse_report", {})` 中提取信号。

### 6. 与 list_semantic_enhancer.py 的关系

不修改 `list_semantic_enhancer.py`（任务 2 再做迁移）。新模块独立存在。

## 允许修改

- `scripts/llm_enhancer.py`（新建）

## 禁止修改

- 所有现有 .py 文件
- `docs/` 目录
- `tests/` 目录

## 测试要求

- 不要求写测试（测试在任务 9 统一添加）
- 确保导入无错误：`.venv/bin/python3 -c "from llm_enhancer import enhance_document_model, compute_suspicion_score"`

## 验收标准

- AC-01: 所有 7 个函数签名与上述一致
- AC-02: `extract_json_object` 能从带 markdown 包裹的文本中提取 JSON
- AC-03: `validate_patch` 对合法 patch 返回空列表，对非法 patch 返回错误列表
- AC-04: `apply_patch_to_model` 正确应用 retype 操作到 AST
- AC-05: `confidence < 0.70` 的 decision 被跳过并记录
- AC-06: `enhance_document_model` 在 llm_call=None 时直接返回原 model
- AC-07: `compute_suspicion_score` 返回 0-1 之间的浮点数
- AC-08: `should_enhance` 对 "off" 返回 False，对 "abc" 返回 True
- AC-09: 导入无错误

## 验证命令

```bash
cd /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts
.venv/bin/python3 -c "
from llm_enhancer import (
    enhance_document_model, compute_suspicion_score, should_enhance,
    extract_json_object, validate_patch, apply_patch_to_model,
    build_role_overrides_from_docx
)
print('All imports OK')
"
```