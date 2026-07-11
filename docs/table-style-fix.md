已定位，根因在 DOCX 直通渲染路径的历史兼容逻辑。

**根因**

1. DOCX 输入不会用 normalized AST 重新渲染表格内容  
   [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:256) 先生成并 normalize model，但 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:285) 随后调用 `render_docx_direct()` 克隆源 DOCX 表格。normalized 里的 `cell_role`、`header_rows`、caption repairs 只部分转成 override，单元格样式没有从 normalized model 回灌到实际 DOCX 表格。

2. API 示例表被显式跳过 `表正文`  
   [scripts/docx_render.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/docx_render.py:439) 对 `is_api_example` 调用：
   ```python
   _set_template_table_properties(..., table_body_style=None)
   ```
   这会保留源表格段落样式，直接导致 audit 统计 `table_paragraphs_not_table_body`。

3. finalizer 又二次跳过 API 示例表  
   [scripts/template_finalizer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/template_finalizer.py:207) 只有 `not is_api_example` 时才执行 `set_style(..., table_body_style)`。所以渲染阶段漏掉后，finalizer 也不会补救。

4. finalizer 对表格内注样式也会保留  
   [scripts/template_finalizer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/template_finalizer.py:203) 遇到 `"注-"` 会设为注样式。但 audit 在 [scripts/audit.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/audit.py:162) 对所有非空表格段落硬性要求 `paragraph.style.name == "表正文"`，没有给注样式例外。

**修复方案**

1. 在 `scripts/docx_render.py` 中，克隆表格后始终传入 `table_body_style`。`is_api_example` 只影响左对齐和 caption 删除逻辑，不能影响段落样式。
   ```python
   _table_body_style = style_from_profile(template_profile, "table_body", "表正文")
   _set_template_table_properties(
       new_table,
       row_height_cm,
       row_height_rule,
       table_body_style=_table_body_style,
   )
   ```

2. 在 `scripts/template_finalizer.py` 中移除 `if not is_api_example` 样式跳过。所有表格单元格非空段落都执行：
   ```python
   set_style(paragraph, table_body_style, corrections, "table_cell_style_normalized", text)
   ```
   对齐仍保留现有逻辑：`code_sample` 和 `api_example` 左对齐，普通数据表居中。

3. 同步取消表格内 `"注-"` 样式保留，或者让 audit 明确允许表内注样式。按当前 skill 不变量和 audit 口径，建议统一收口为 `表正文`。

4. 补回归测试：
   - DOCX 单格 JSON/API 示例表转换后，单元格段落样式为 `表正文`，对齐为左对齐。
   - 普通数据表转换后，所有非空单元格段落样式为 `表正文`。
   - 表格内原始 `Normal`、`正文`、`3.1注-无编号注` 都被 finalizer 收口为 `表正文`。
   - `audit.table_paragraphs_not_table_body == []`。

当前环境是只读，我没有直接改文件或运行写文件测试。