# Markdown AST 识别规范

## 1. 目标与范围

本规范定义 Markdown Source Document 如何转换为统一 DocumentModel。识别结果是后续规范化、模板渲染和守恒审计的唯一语义来源。

识别 module 只处理 Markdown 语法事实、Container Context、Semantic Block、Inline Span、Recognition Evidence、Recognition Diagnostic 和 Preservation Contract。DOCX 取证、模板样式、Word 渲染、LLM 增强及最终审计不进入该 module。

## 2. Seam 与稳定 interface

Markdown adapter seam 位于已读取的 Source Document 与统一 DocumentModel 之间。核心 interface 为纯计算：

```python
recognize_markdown(
    source: MarkdownSource,
    *,
    template_version: str,
) -> RecognitionResult
```

`MarkdownSource` 至少包含来源路径和完整文本。`RecognitionResult` 至少包含：

```text
document_model
source_audit
parse_observations
diagnostics
title_decision
preservation_contract
```

同一 Source Document、同一 template version 和同一识别规范版本必须产生确定性相同的 RecognitionResult。

现有 `parse_md_to_model(src, report, *, skill_version)` 保持稳定，作为兼容 Adapter 负责读取输入、调用核心 interface，并将结构化结果投影到旧报告字段。

## 3. 识别阶段

识别必须按以下顺序执行：

1. 建立 Source Document 身份和源码行映射。
2. 使用固定 Markdown 方言生成 Syntax Token。
3. 统计 token inventory，并为 token 建立来源范围。
4. 维护 Container Context，包含引用、列表、列项和表格上下文。
5. 按块级 token dispatch 生成 Recognition Decision。
6. 将 inline token 转换为有序 Inline Span。
7. 对纯文本段落执行受控领域语义裁决。
8. 装配有序 Semantic Block，并生成稳定 block id。
9. 裁决唯一文档标题。
10. 对未知或部分支持语法生成 Fallback Representation 和 Recognition Diagnostic。
11. 校验 Recognition Contract 后返回结果。

任何阶段均不得静默丢弃可见内容。

## 4. 识别优先级

Recognition Decision 按以下优先级形成：

1. 明确的 Markdown 块级语法。
2. Container Context 施加的语义约束。
3. 明确的 WX 领域语义，包括题注、附录、注和公式。
4. 受控的纯文本候选识别。
5. body Fallback Representation。

高优先级结论不得被低优先级启发式覆盖。代码、引用、显式列项、表格、图片和分隔符禁止进入正文标题或列表推断。

## 5. 块级语法映射

| Markdown rule | Syntax Token | Semantic Block | 关键合同 |
|---|---|---|---|
| heading, lheading | heading open, inline, close | heading | 首个语义块为 H1 时 role 为 title，其余保持 heading |
| paragraph | paragraph open, inline, close | body 或受控领域块 | 保留可见文本与 Inline Span |
| blockquote | blockquote open, close | 内部块携带 quote Container Context | 普通引用内容 role 为 quote |
| list | list open, item open, close | list_item | 保存 family、level、restart、marker 和 parent id |
| table | table, row, cell token | table | 保存完整矩阵、表头行数、cell role 和 cell Inline Span |
| fence, code | fence 或 code block | body, role code_block | 原样保留空白、换行和 language |
| hr | hr | separator | 无可见占位文本 |
| image | inline image | image 或可读 Fallback Representation | 本地资源保存规范路径与 hash，远程资源产生 Diagnostic |
| reference | reference definition | 无独立可见块 | 链接目标进入 Inline Span href |
| html block | html text fallback | body | 过滤标签后保留可读文本并产生 Diagnostic |

每增加一种块级 token，必须同时补充本表、dispatch、Fallback Representation、Diagnostic 和测试类别。

## 6. 行内语法映射

| Markdown rule | Inline Span 语义 | 可见文本合同 |
|---|---|---|
| text | plain | 保留 tokenizer 输出文本 |
| backticks | code | 保留代码内部空格 |
| emphasis | strong 或 emphasis | 支持合法嵌套组合 |
| link, autolink | href | 锚文本与链接目标分别保存 |
| image | image candidate | 独立图片生成 image block，混排图片生成 Fallback Representation |
| newline | soft break 或 hard break | soft break 投影为空格，hard break 投影为换行 |
| escape | plain | 保存转义后的可见字符 |
| entity | plain | 保存解码后的可见字符 |
| html inline | plain fallback | 保留可读文本并产生 Diagnostic |

所有 Inline Span 按源码顺序排列。每个块和表格单元格必须满足：

```text
visible_text == concat(inline_span.text)
```

链接 href 不参与文本拼接。链接目标是否追加为可见 URL 属于投影策略，必须集中定义，禁止在 token close 分支临时拼接。

## 7. Container Context

Container Context 是识别期间的不可变语义快照，至少包含：

```text
quote_depth
list_depth
list_family
list_marker
list_item_identity
table_region
```

每个 Semantic Block 保存与其创建时一致的 Container Context。离开容器后不得继续读取已关闭容器的状态。

嵌套列表父项必须来自当前活动 lineage。后出现项、同级项和更深层级项不能成为父项。

## 8. 标题裁决

文档只允许一个 title：

1. 首个 Semantic Block 为显式 H1 时，该块成为 title。
2. 任何语义内容出现在首个 H1 前时，使用文件名生成 title。
3. 后置 H1 按正文 heading 处理。
4. 引用或列项内 H1 不参与 title 裁决。
5. 文件名生成 title 时产生 `markdown_title_generated` Diagnostic。

## 9. Recognition Diagnostic

Diagnostic 使用稳定 code，不使用自由文本承担程序合同。至少包含：

```text
code
severity
line_start
token_type
details
```

首版稳定 code 包括：

```text
markdown_title_generated
remote_image
mixed_remote_image
missing_local_image
html_block
html_inline
task_list_state
list_item_heading
quoted_heading
unknown_block_token
unknown_inline_token
recognition_contract_violation
```

可恢复问题生成 Fallback Representation 和 Diagnostic。依赖缺失、输入不可读和 Recognition Contract 违反属于识别错误。

## 10. Recognition Contract

核心识别完成前必须校验：

1. Semantic Block 顺序与 Source Document 一致。
2. block id 唯一且稳定递增。
3. title 数量等于一。
4. Inline Span 文本拼接等于所属可见文本。
5. 显式列表 lineage 完整。
6. 表格行列和 cell role 完整。
7. 代码块空白和换行完整。
8. separator 保持无文本语义。
9. 本地资源身份具有可审计证据。
10. 未完整支持的可见内容均具有 Fallback Representation 和 Diagnostic。

Recognition Contract 通过后才能进入规范化阶段。

## 11. Preservation Contract

Preservation Contract 从 Recognition Result 编译，至少锁定：

```text
token inventory
有序 Semantic Block 序列
显式 role
标题文本与层级
正文、代码和引用文本
列表 family、level、restart 和 parent id
表格矩阵、表头和 cell role
Inline Span 文本、marks 和 href
图片资源身份
题注和附录字段
separator 位置
Diagnostic code 清单
```

规范化 AST 和保存后 DOCX 回读模型必须分别满足该合同。finalizer 不修补识别阶段的语义错误。

## 12. 测试规范

测试分为五层：

1. 原子语法：每个启用 block rule 和 inline rule 至少一个正例。
2. 容器组合：引用、列表、表格、代码和图片的合法嵌套组合。
3. 顺序组合：不同块出现在 H1 前后、表格前后和列表边界的排列。
4. 降级与反例：未知 token、远程资源、HTML、任务状态、合同破坏和保存后格式删除。
5. 真实文件：至少一份无起始 H1 的复杂文档和一份规范化正式文档。

新增语法支持时必须提交：

```text
规范映射
AST 正例
容器组合例
降级反例
Preservation Contract 反例
Desktop 入口回归
```

测试断言以识别 interface 的可观察结果为主，避免依赖内部 token state 或 handler 名称。

## 13. 扩展规则

首版固定 CommonMark 加 table 方言和单一识别策略。内部 dispatch 可以扩展，外部不开放全局注册表。

只有出现第二个真实方言或独立语法提供者时，才评估 DialectAdapter 或 RecognitionPlugin seam。新增 seam 前必须有两个真实 Adapter 和完整合同测试。
