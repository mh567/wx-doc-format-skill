---
name: wx-doc-format
description: Use when the user says “wx文档格式” or asks to convert Markdown, DOCX, or Word documents into a WX-style formatted .docx. Designed for any agent with Python and python-docx. Uses a fixed script for repeatable formatting, has built-in WX formatting rules, supports strict normalization for messy Word inputs, Markdown table conversion, heading and list inference, note styles, table body style, fixed table row height, JSON and Markdown audit reports, and render-based verification.
metadata:
  short-description: Convert MD or DOCX into WX Word format
---

# WX 文档格式

当用户说“wx文档格式”，或要求把 `.md`、`.docx`、Word 文件转换成 WX 文档格式并输出 `.docx` 时，使用本技能。本技能面向通用智能体设计，只要求能运行 Python 并安装 `python-docx` 与 `lxml`。

## 固化格式规则

本技能已固化 WX 技术文件常用格式规则。默认情况下直接按以下规则创建同名样式，无需提供外部样式文件：

- 文档标题：四号黑体，不加粗，居中，1.25 倍行距。
- 正文：小四宋体，不加粗，首行缩进约 1.13 厘米，英文字母和数字使用 Times New Roman，两端对齐，1.25 倍行距。
- 正文标题 1 到 6：小四黑体，不加粗，1.25 倍行距，采用悬挂缩进。标题 1 左缩进 0.762 厘米、悬挂 0.762 厘米，标题 2 左缩进 1.014 厘米、悬挂 1.014 厘米，标题 3 左缩进 1.27 厘米、悬挂 1.27 厘米，标题 4 左缩进 1.524 厘米、悬挂 1.524 厘米，标题 5 左缩进 1.778 厘米、悬挂 1.778 厘米。一级标题段前、段后各 2.5 磅。
- 图表标题：小四黑体，不加粗，单倍行距，段前、段后各 0.5 行，居中。
- 表正文：小四宋体，可按内容密度缩小，但建议不小于小五；英文字母、数字和编号使用 Times New Roman；不缩进；表格内行距按最小值处理。
- 表格：默认表格文字样式为 `表正文`，行高固定为 0.69 厘米。
- 注、图注、表注、脚注：五号宋体，字号比正文小一号；回行与注的内容文字对齐。无编号注段前空 0.79 厘米，悬挂缩进约 1.53 厘米；有编号注悬挂缩进约 1.81 厘米。
- 列项：一级列项为 `a)`、`b)`；二级列项为 `1)`、`2)`；一级无编号列项为长横线；二级无编号列项为中点。一级有编号列项左缩进约 1.647 厘米、悬挂约 0.801 厘米，二级有编号列项左缩进约 2.443 厘米、悬挂约 0.750 厘米。列项一般不超过两级，不建议把列项作为下级标题。
- 目录：目次使用小四宋体，1.25 倍行距，目录层级每级向后缩进两字符，定稿前应更新目录。
- 附录：附录标题与文档标题格式一致，附录一级、二级、三级标题参照正文相应标题格式。

## 工作流

1. 确认输入文件类型：支持 `.md`、`.markdown`、`.docx`。
2. 确认可用 Python 环境已安装依赖。可在技能目录执行 `python -m pip install -r requirements.txt`，或执行 `python -m pip install python-docx lxml`。
3. 运行 `scripts/format_document.py` 生成格式化 `.docx`。
4. 若出现 `lxml` 签名、导入或动态库错误，优先重装依赖：`python -m pip install --upgrade --force-reinstall --no-cache-dir python-docx lxml`。macOS 环境若反复报签名或隔离问题，建议更换干净 Python 环境后重新安装依赖。
5. 表格默认执行：
   - 表格文字样式使用 `表正文`
   - 表格行高固定为 `0.69厘米`
   - 可用 `--table-row-height-rule exact|at-least` 控制固定行高或最小行高
   - 表头可使用浅蓝底色，正文单元格左对齐，表头居中
6. 标题处理：
   - Markdown `#` 映射为 `文档标题`
   - Markdown `##` 到 `######` 映射为 `Heading 1` 到 `Heading 5`
   - 默认保留源文件手工编号，保证标题仍有章节号
   - DOCX 输入按原段落样式和标题文本推断标题层级
   - DOCX 输入若使用 Word 自动标题编号，应将显示编号物化到标题文本中，编号与标题文字之间使用制表符，并套用标题悬挂缩进，避免重建样式后丢失章节号
   - 标题字体颜色应统一为黑色，避免继承 Word 内置 Heading 主题色
7. 强规范化处理：
   - 默认启用 `--strict-normalize`
   - 对 Word 中混乱标题样式进行文本识别，支持 `第X章`、`第X节`、`1`、`1.1`、`1.1.1`、`一、`、`（一）` 等标题形式
   - 对未设置标题样式但明显加粗、居中或字号较大的短段落，作为疑似标题处理并写入报告
   - 识别常见列项，如 `a)`、`1)`、`（1）`、中点、长横线列项，并套用列项兼容样式
   - DOCX 输入若使用 Word 自动列表编号，应将一级列表物化为 `a)`、`b)`，二级列表物化为 `1)`、`2)`，编号与列项文字之间使用制表符，并套用对应列项样式
   - DOCX 输入会重建段落样式，减少原始缩进、字体、行距混乱对输出的影响
8. 备注处理：
   - Markdown 中 `**备注：**`、`备注：`、`**编写提示：**`、`编写提示：` 使用 `3.1注-无编号注`
   - 避免在正文里重复写“注：备注：”
9. 表格处理：
   - Markdown 标准表格会转换为 Word 表格
   - DOCX 输入尽量复制原始表格 XML 后再规范化，保留合并单元格等结构
   - 统一表格样式、表正文、行高和垂直居中
10. 生成审计报告：
   - 建议使用 `--report report.json`
   - 建议同时使用 `--report-md report.md`
   - 报告包含推断标题、疑似视觉标题、推断列项、模糊短段落、表格处理数量、非文本对象统计、风险提示和审计结果
   - 自动化批处理可增加 `--fail-on-risk`，当源文件含图片、公式、页眉页脚、目录域、批注、修订等对象或固定表格行高可能截断文字时，脚本会生成文件和报告后返回失败码
11. 如当前智能体具备 Word、LibreOffice 或文档渲染能力，应渲染页面并目视检查关键页。
12. 最终只返回生成的 `.docx` 链接，除非用户要求中间产物。

## 命令示例

```bash
python scripts/format_document.py \
  --input "/path/to/input.md" \
  --output "/path/to/output.docx" \
  --report "/path/to/report.json" \
  --report-md "/path/to/report.md"
```

关闭强规范化：

```bash
python scripts/format_document.py \
  --input "/path/to/input.docx" \
  --output "/path/to/output.docx" \
  --no-strict-normalize
```

自动化场景遇到高风险源文件时返回失败码：

```bash
python scripts/format_document.py \
  --input "/path/to/input.docx" \
  --output "/path/to/output.docx" \
  --report "/path/to/report.json" \
  --report-md "/path/to/report.md" \
  --fail-on-risk
```

## 校验要点

- 标题前不能出现两套章节编号。
- 使用 Word 自动编号的源文件，转换后标题文本应保留可见章节号，如 `1`、`2.1`、`2.1.1`。
- 不应残留 Markdown 标记，如 `**备注：**`。
- 标题字体颜色应为黑色，不能继承蓝色或主题色。
- 自动列表应转换为可见列项编号，并套用 `1.1一级列项-编号` 或 `2.1二级列项-有编号` 等列项样式。
- 表格段落样式应为 `表正文`，行高应为 `0.69厘米`。
- 查看 JSON 报告中的 `suspect_visual_headings` 和 `ambiguous_short_paragraphs`，必要时人工复核。
- `audit.table_paragraphs_not_table_body`、`audit.table_rows_bad_height`、`audit.markdown_residue` 应为空。
- 查看 `non_text_objects`，若图片、公式、文本框、目录域、页眉页脚、批注、修订等数量不为 0，应渲染复核。
- 查看 `risk_warnings`。存在风险提示时，不能只按脚本成功作为交付依据。
- 查看 `audit.table_cells_may_clip`。固定表格行高下若存在长单元格文本，应重点检查表格是否压缩或截断。
- 渲染页中不得出现文字重叠、明显截断、空白异常页。
- 若源文件含有用户禁用句式或特殊写法，按当前对话要求同步清理。
- 页眉页脚、页码分节、复杂目录域、附录自动编号等内容不由本脚本自动生成，需要人工复核或另行处理。

## 已知边界

- 文本框、形状、SmartArt、批注、修订记录、复杂域代码、页眉页脚中的正文内容不会完整重建。
- 图片和公式不作为主要转换对象处理，必要时需单独检查。
- 脚本会尽量保留 DOCX 表格结构，但极复杂嵌套表格仍需渲染复核。
- 强规范化会根据文本和格式推断标题，疑似标题会写入报告，不能完全替代人工确认。
