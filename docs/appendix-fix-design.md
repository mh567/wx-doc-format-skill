当前环境是只读 sandbox，`apply_patch` 被拒绝了，所以我不能直接修改文件。下面是可直接应用的补丁，修复点集中在 `scripts/template_finalizer.py`。

```diff
*** Begin Patch
*** Update File: scripts/template_finalizer.py
@@
 def set_paragraph_lines(paragraph, lines: list[str]) -> None:
     paragraph.text = ""
     for index, line in enumerate(lines):
         if index == 0:
             paragraph.add_run(line)
         else:
             paragraph.add_run().add_break()
             paragraph.add_run(line)
 
 
+_APPENDIX_MARK_RE = re.compile(r"^附\s*录\s*([A-Z])\s*$")
+_APPENDIX_CLASSIFICATION_RE = re.compile(r"^[（(](规范性|资料性)[)）]$")
+_APPENDIX_CLASSIFICATION_TITLE_RE = re.compile(r"^[（(](规范性|资料性)[)）]\s*.+$")
+
+
 def finalize_appendix_structure(doc, profile: dict) -> list[dict]:
@@
-        match = re.match(r"^附\s*录\s*([A-Z])\s*$", text)
+        match = _APPENDIX_MARK_RE.match(text)
@@
-        if re.match(r"^[（(](规范性|资料性)[)）]$", next_one_text) and next_two_text:
-            set_paragraph_lines(paragraph, [f"附  录  {letter}", next_one_text, next_two_text])
+        if _APPENDIX_CLASSIFICATION_RE.match(next_one_text) and next_two_text:
+            set_paragraph_lines(paragraph, ["", next_one_text, next_two_text])
@@
             index += 1
             continue
-        canonical = f"附  录  {letter}"
-        if text != canonical:
-            paragraph.text = canonical
-            corrections.append({"type": "appendix_title_spacing_normalized", "from": text, "to": canonical})
+        if _APPENDIX_CLASSIFICATION_TITLE_RE.match(next_one_text):
+            set_paragraph_lines(paragraph, ["", next_one_text])
+            set_style(paragraph, appendix_style, corrections, "appendix_title_style_normalized", paragraph.text)
+            delete_paragraph(next_one)
+            corrections.append(
+                {
+                    "type": "appendix_two_paragraph_header_merged",
+                    "letter": letter,
+                    "title": next_one_text[:120],
+                }
+            )
+            index += 1
+            continue
+        paragraph.text = ""
+        corrections.append({"type": "appendix_title_prefix_removed", "from": text, "to": ""})
         set_style(paragraph, appendix_style, corrections, "appendix_title_style_normalized", paragraph.text)
*** End Patch
```

关键行为：

- `附录A` 段落设为 `附录标题` 后，正文内容清空，由模板 auto-numbering 显示“附录 A”。
- 三段式变为：自动编号行、`（资料性）`、标题。
- 两段式 `附录A` + `（资料性）接口字段与处置命令约束` 变为：自动编号行、`（资料性）接口字段与处置命令约束`。
- 第二个源段落会被删除，避免它单独套用 `附录标题` 后再触发一次自动编号。

我没法在当前权限下运行转换并写出新的 `接口控制文件2.docx`。权限恢复后建议跑：

```bash
cd scripts && .venv/bin/python3 -m main \
  --input ../input/接口控制文件2.docx \
  --output ../wx_output/api_doc2/接口控制文件2.docx \
  --template ../assets/技术文件格式及书写要求.docx \
  --report ../wx_output/api_doc2/接口控制文件2.json
```