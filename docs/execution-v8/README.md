# 题注序列标识符与域形态改为模板拥有（execution-v8 执行包）

本包处理一个用户可见缺陷：转换后的文档里插入交叉引用时，引用类型选 图 或 表，引用编号项列表为空，而文档里明明有「图 x」「表 x」题注。

## 问题

实测对象：`/Users/harris/Documents/zero项目/input/技术文件格式及书写要求.docx`（151 KB）用 `assets/wx_template.docx` 转换，输出 `wx_output/wx-spec-0.14.3/技术文件格式及书写要求-WX-0.14.3.docx`（版本 0.14.3）。

**三方对比（题注段落的域）**

| | 标签 run | 域形态 | 标识符 |
|---|---------|--------|--------|
| 模板 `assets/wx_template.docx` | `表`（独立 run，紧接域） | 复杂域 | `SEQ 表 \* ARABIC` |
| 源文档 | `表`（同上） | 复杂域 | `SEQ 表 \* ARABIC` |
| 本包输出 | `表 `（多一个空格） | 简单域 `w:fldSimple` | `SEQ Table \* ARABIC` |

模板的题注 run 序列（逐 run）：

```
t='表'
fldChar[begin]
instrText=' SEQ 表 \* ARABIC '
fldChar[separate]
t='1'
fldChar[end]
t=' 小四'…（正文）
```

本包输出的 run 序列：

```
t='表 '
w:fldSimple（缓存结果 '1'）
t=' 小四黑体不加粗，单倍行距，前后各空0.5行'
```

**标识符统计**

| 标识符 | 源文档 | 本包输出 |
|--------|--------|----------|
| `SEQ 表` | 1 | 0 |
| `SEQ 图` | 3 | 0 |
| `SEQ 公式` | 3 | 3 |
| `SEQ Table` | 0 | 1 |
| `SEQ Figure` | 0 | 3 |

**根因**

1. `scripts/wxdf/rendering.py:324` 的 `_CAPTION_SEQUENCE = {"table": ("表 ", "Table"), "figure": ("图 ", "Figure")}` 把英文标识符写死，并且标签带上尾随空格；`:369` 用 `w:fldSimple` 写简单域。
2. `scripts/wxdf/template.py:125` 的呈现契约同样写死 `{"table_sequence": "Table", "figure_sequence": "Figure"}`，从未从模板读取。
3. Word 的「插入交叉引用」对话框按引用类型匹配 SEQ 标识符来列编号项。中文 Word 的 图/表 对应标识符 `图`/`表`，输出写的是 `Table`/`Figure`，匹配不到任何项，列表为空。引用类型本身仍会出现，因为 图/表/公式 是中文 Word 的内置题注标签，与文档内容无关。

**影响面**

- 受影响的是**被重建的图表题注**：本样例 1 个表题注加 3 个图题注。
- 公式编号不受影响：源文档的 `SEQ 公式` 域按决策 9 作为行内原子按引用复制，标识符本来就保住（3 到 3）。

**与治理文档的冲突**

`AGENTS.md` 关键决策 3 字面写的是「SEQ Table 和 SEQ Figure 分开编号」，代码照字面实现。模板用的是 `SEQ 表` / `SEQ 图`。按既定的「模板是格式值的唯一事实源」，应以模板为准，决策 3 需要按本包裁定改写。

## 用户裁定

2026-09-16 用户同意按模板为准修复（「可以」），本包据此编制。

## 解决方案

**一、模板读取（新增）**

在 `scripts/wxdf/template.py` 增加题注序列读取，与既有的目录片段读取（`extract_template_toc_fragment`）同一位置与同一性质：读模板 `word/document.xml` 中题注样式的段落，取出该段的 SEQ 标识符，写入呈现契约的 `captions` 键。

种类判定按下列优先次序，实施后经实测修正（见 `evidence/T01.md` 的「规格与实现的偏差」）：

1. 题注自身的段首标签（`表` / `图`）判为对应种类。标签是题注自己声明它标注什么，是最可靠的证据。
2. 标签不属于 WX 约定的词表时，才看相邻块：下一兄弟块是表格判为表题注，前一兄弟块含图形判为图题注。

模板解析不出题注样式时直接回落，不扫描正文：样式标识符为空会把每个没有 `w:pStyle` 的段落都当成题注段落。

契约键形状不变，取值改为模板实际值：

```json
"captions": {"table_sequence": "表", "figure_sequence": "图", "source": "template"}
```

模板没有对应种类的题注域时回落 `表` / `图`，`source` 记录是否两种都来自模板。

**二、渲染按模板形态写出（修改）**

`scripts/wxdf/rendering.py::_write_caption` 改为：

- 标识符从呈现契约读取，不再用硬编码表；
- 按模板的 run 序列写复杂域：标签 run（不带尾随空格）、`fldChar[begin]`、`instrText`、`fldChar[separate]`、缓存编号、`fldChar[end]`、正文 run（前导一个空格）；
- 可见文本由 `{标签}{编号} {正文}` 组成，与保存后文档的逐 `w:t` 文本一致，保证回读审计的文本比对仍然成立。

缓存编号保留当前值（`1`），由 Word 打开时按域刷新重算。附录范围内保留源编号的题注不变（决策 7）。

**三、消费方跟随（修改）**

- `scripts/caption_placement.py:275,277` 的 `_rendered_caption_type` 改为读共享的序列标识符词表与读取函数，标签文本回落分支保留。
- `tests/test_md_captions.py` 不改：该用例驱动 legacy 链路（`md_pipeline` / `word_model_renderer`），与「不在范围」的 legacy 写入端同属一类，其中的英文标识符断言在 legacy 链路上仍然成立。
- 测试断言更新：`tests/test_native_semantic_ownership.py`、`tests/test_modular_fidelity.py`、`tests/test_modular_conversion.py`、`tests/test_modular_caption_consumption.py`、`tests/test_t05_mixed_content.py` 共 13 处。

**四、共享词表与读取函数**

SEQ 标识符的读取与「标识符到种类」的词表归 `scripts/text_utils.py` 单一所有者（`sequence_identifier` 与 `CAPTION_SEQUENCE_KINDS` / `CAPTION_SEQUENCE_FALLBACK`）：模板读取与渲染后审计两处消费都走它，两边的拼写不会分叉。与 `strip_heading_marker_prefix` 在 v7 的处理一致。

**五、治理文档同步**

改写 `AGENTS.md` 决策 3：题注编号标识符与域形态取自模板自身的题注域（本模板为 `SEQ 表` / `SEQ 图`），不写死英文标识符。

## 归属

| 项 | 归属 | 说明 |
|----|------|------|
| 模板题注序列读取 | `scripts/wxdf/template.py` | 模板能力的读取归模板模块，与目录片段同处 |
| 契约键 | `scripts/wxdf/template.py`（`captions`） | 键形状不变，取值来源改变，新增 `source` |
| 题注写出 | `scripts/wxdf/rendering.py::_write_caption` | 渲染按契约写域，不自行判定标识符 |
| SEQ 标识符读取与词表 | `scripts/text_utils.py` | `sequence_identifier` 与 `CAPTION_SEQUENCE_KINDS` / `CAPTION_SEQUENCE_FALLBACK` 单一所有者 |
| 题注种类读取（审计） | `scripts/caption_placement.py` | 走共享读取函数 |
| 公式编号 | 不改 | 保留域原子，标识符本就正确 |
| legacy 写入端 | 不在范围 | `docx_render.py`、`word_model_renderer.py` 只服务 legacy 诊断链路，见「不在范围」 |

## 字段级定义

| 项 | 形状 | 说明 |
|----|------|------|
| 契约 | `presentation.template_contract.captions = {"table_sequence": <str>, "figure_sequence": <str>, "source": "template"\|"fallback"}` | 键形状沿用，新增 `source` 供核对 |
| 表题注域 | `SEQ <table_sequence> \* ARABIC` | 复杂域，`instrText` 携带 |
| 图题注域 | `SEQ <figure_sequence> \* ARABIC` | 同上 |
| 标签 run | 纯标签文本，不带尾随空格 | 模板形态 |
| 正文 run | `" " + 正文` | 模板形态 |

## 不变量

1. 输出中每个被重建的图表题注的 SEQ 标识符与模板对应种类的标识符相同。
2. 输出中不出现模板未使用的 SEQ 标识符（本模板下即不出现 `SEQ Table` / `SEQ Figure`）。
3. 题注段落的可见文本与保存后文档的逐 `w:t` 文本一致，回读审计的文本比对不退化。
4. 公式编号域按引用复制，数量与源一致（本样例 3 到 3）。
5. 附录范围内保留源编号的题注不注入域（决策 7）。
6. 审计位不变：三项审计 `passed`，`unexpected_styles` 为空，普通文档 `risk_warnings` 为空。

## 测试决策

红灯先行，新增 `tests/test_modular_caption_sequence.py`（11 项；文件名命中既有 `.gitignore` 放行规则 `!tests/test_modular_*.py`，仍需 `git check-ignore -v` 复核可见）。覆盖：

1. 契约层：加载 `assets/wx_template.docx` 得到的表/图标识符为 `表` / `图`，`source` 为 `template`。
2. 契约层：无题注域的模板回落到 `表` / `图`，`source` 为 `fallback`。
3. 契约层：标签为 `图` 的题注紧跟表格时仍判为图，不污染 `table_sequence`。
4. 契约层：解析不出题注样式的模板不读无样式段落里的域。
5. 写出层：run 序列为标签、`fldChar[begin]`、`instrText`、`fldChar[separate]`、缓存编号、`fldChar[end]`、正文，`instrText` 为 `SEQ 表 \* ARABIC` 且带 `xml:space="preserve"`。
6. 写出层：题注可见文本为 `表1 正文`（标签与编号之间无空格），且不残留简单域。
7. 端到端 DOCX：输出题注标识符为 `图` / `表`，三项审计通过。
8. 端到端 DOCX：输出不含 `SEQ Table` / `SEQ Figure`。
9. 端到端 DOCX：公式编号域按引用复制，数量 1 到 1（用模板同形的复杂域构造）。
10. 回归：`input/test_baseline.md` 仍 `completed`，审计位不变。
11. 回归：`input/benchmark_report.md` 仍 `completed`，审计位不变。

真实文档 `技术文件格式及书写要求.docx` 的标识符统计（`表` 1、`图` 3、`公式` 3）不在测试集内：该文件位于仓库之外，测试只消费仓库内被放行的输入。该统计在 `evidence/T01.md` 的「真实文档实测」一节按实际命令输出记录。

## 不在范围

- 不改模板文件与样式表。
- 不改题注的位置语义（表题注在表格上方、图题注在图片下方）与题注识别规则。
- 不改公式编号的保留策略。
- legacy 诊断链路（`scripts/docx_render.py`、`scripts/word_model_renderer.py` 的写入端）不改，其英文标识符行为记录为残留。
- 不改 CLI 参数与公开报告合同，不重命名稳定接口。
- 不移动或删除 `_build/`、`paseo.json`、`.commandcode/`。
- 不执行远程 push、标签或 Release。

## 补充说明

- 路径声明（架构定案 / 实施实现 / 独立审查）：
  - 架构定案：`scripts/wxdf/template.py`、`scripts/wxdf/rendering.py`、`AGENTS.md` 决策 3 改写、`docs/execution-v8/README.md`。
  - 实施实现：`scripts/caption_placement.py`、`tests/test_modular_caption_sequence.py`、四个既有测试断言、`docs/execution-v8/evidence/T01.md`、`.gitignore` 放行。
  - 独立审查：由另一智能体对 diff 执行 Standards + Spec 双轴审查，复审确认。
- 本包状态唯一源：本目录 `status.json`。
- 外部门禁：`word_cross_reference_dialog_manual_check`。本机没有 Word/WPS，对话框行为无法自动验证，只能验证输出的域与模板逐字段一致；人工确认由用户在不同的 Word/WPS 环境执行。
