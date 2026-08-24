# Markdown Document Recognition

本上下文定义 Markdown 输入进入统一文档语义模型时使用的领域语言，确保解析、规范化、渲染、审计和测试采用一致概念。

## Language

**Source Document**:
一次待识别的 Markdown 文档及其来源身份。
_Avoid_: Markdown file, raw text

**Syntax Token**:
Markdown 语法解析器确认的块级或行内语法事实。
_Avoid_: line match, regex hit

**Container Context**:
包围当前语法内容的引用、列表、表格或其他嵌套语义环境。
_Avoid_: parser state, nesting flag

**Semantic Block**:
统一文档模型中的有序块级语义对象，例如标题、正文、列项、表格、图片、题注或分隔符。
_Avoid_: paragraph token, node

**Inline Span**:
Semantic Block 内具有连续文本和一致行内语义的最小片段。
_Avoid_: run, fragment

**Recognition Evidence**:
支持某项语义裁决的可追溯语法事实和来源位置。
_Avoid_: heuristic score, parser hint

**Recognition Decision**:
依据 Syntax Token、Container Context 和 Recognition Evidence 形成的明确语义结论。
_Avoid_: inference result, guess

**Fallback Representation**:
无法完整识别的语法内容所采用的可读、可审计语义表达。
_Avoid_: unsupported text, ignore

**Recognition Diagnostic**:
记录降级、歧义、缺失资源或违反识别合同情况的结构化信息。
_Avoid_: warning string, log message

**Preservation Contract**:
Source Document、Semantic Block 和最终 DOCX 之间必须保持的语义及顺序不变量。
_Avoid_: count check, smoke check
