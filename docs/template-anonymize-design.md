**推荐方案**

采用 `docx zip 原地改文案` 的方式，只改可见文本节点，保留包内结构和关键 XML 部件。

1. 复制原模板为匿名模板，例如 `assets/wx_template_anonymized.docx`。
2. 解压 docx 后只处理这些部件里的 `w:t` 文本：
   1. `word/document.xml`
   2. `word/header*.xml`
   3. `word/footer*.xml`
   4. `word/footnotes.xml`
   5. `word/endnotes.xml`
   6. `docProps/core.xml`
   7. `docProps/app.xml`
3. 严格不改这些部件：
   1. `word/styles.xml`
   2. `word/numbering.xml`
   3. `word/settings.xml`
   4. `word/theme/theme*.xml`
   5. `word/fontTable.xml`
   6. `word/_rels/*`
   7. `[Content_Types].xml`
4. 对 `word/document.xml` 中每个段落保留原 `w:pPr`、`w:pStyle`、`w:numPr`、`w:spacing`、`w:ind`、`w:rPr`。只替换 `w:t` 的内容。
5. 匿名文本按段落样式生成，例如：
   1. `文档标题`：`示例技术文件`
   2. `heading 1`：`范围`
   3. `heading 2`：`术语和定义`
   4. `Normal`：`本段为格式示例文本，用于验证正文样式、缩进和行距。`
   5. `1.1一级列项-编号`：`第一类示例条目`
   6. `2.1二级列项-有编号`：`第二级示例条目`
   7. `表正文`：`示例字段`、`示例说明`
6. 保留段落数量和每个段落的 style。表格也只改单元格段落文本，保留表格结构、行高、边框、单元格属性。

**为什么这样设计**

当前代码从模板读取样式和编号定义，`template_profile.py` 会解析 `styles.xml` 和 `numbering.xml`，并从样式绑定中拿编号信息：[template_profile.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/template_profile.py:85)、[template_profile.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/template_profile.py:122)。

渲染时模板会被加载，然后清空正文，只保留 section 信息：[main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:80)。所以匿名模板的正文主要用于人工查看和直接渲染路径，真正影响转换结果的是样式、编号和段落样式映射。

`unexpected_styles=0` 的判定来自最终文档中使用的段落样式是否落在模板解析出的允许集合里：[template_finalizer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/template_finalizer.py:284)。因此匿名化不能改 style name、style id 和编号绑定。

**验证清单**

1. 比较关键部件哈希，必须完全一致：

```bash
unzip -p assets/技术文件格式及书写要求.docx word/styles.xml | shasum -a 256
unzip -p assets/wx_template_anonymized.docx word/styles.xml | shasum -a 256

unzip -p assets/技术文件格式及书写要求.docx word/numbering.xml | shasum -a 256
unzip -p assets/wx_template_anonymized.docx word/numbering.xml | shasum -a 256
```

2. 检查段落样式序列一致：

```bash
python3 scripts/check_template_anonymization.py \
  assets/技术文件格式及书写要求.docx \
  assets/wx_template_anonymized.docx
```

这个检查脚本应验证：

1. `styles.xml` 字节一致
2. `numbering.xml` 字节一致
3. 段落数量一致
4. 每个段落的 `w:pStyle` 一致
5. 每个表格单元格段落的 `w:pStyle` 一致
6. 原敏感关键词在匿名模板中不存在
7. 用匿名模板跑一次转换后 `unexpected_styles` 数量为 0

**不建议做的事**

1. 不用 Word 手工另存匿名版，Word 可能重写 `styles.xml`、`numbering.xml` 或关系 ID。
2. 不用 `python-docx` 重建模板正文，容易丢失低层 OOXML 属性。
3. 不删除段落，删除会改变样式样本覆盖面，也可能影响直接渲染路径的回归判断。
4. 不重命名样式，`template_profile.py` 依赖当前样式名和别名解析。