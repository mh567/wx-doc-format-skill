# TASK-APPENDIX-001：修复附录标题双重显示和两段式结构

## 目标

按 `docs/appendix-fix-design.md` 修改 `scripts/template_finalizer.py` 的 `finalize_appendix_structure`。

## 修改点

1. 附录段落设为 `附录标题` 后，正文内容清空（auto-numbering 负责显示"附录 A"）
2. 支持两段式：`附录A` + `（资料性）接口字段与处置命令约束`
3. 第二段删除，避免重复触发 auto-numbering

## 验证

用接口控制文件2.docx 转换确认附录不再重复。
