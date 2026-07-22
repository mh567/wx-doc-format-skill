from __future__ import annotations


CANONICAL_TOC_TITLE = "目  次"
TOC_TITLE_STYLE = "目录标题"
TOC_MAX_LEVEL = 4
TOC_LINE_SPACING = 300
TOC_ENTRY_INDENT_TWIPS_PER_LEVEL = 420
TOC_ENTRY_INDENT_CHARS_PER_LEVEL = 200
TOC_TITLE_FONT = "黑体"
TOC_TITLE_SIZE_HALF_POINTS = 28
TOC_ENTRY_FONT = "宋体"
TOC_ENTRY_SIZE_HALF_POINTS = 24
TOC_CUSTOM_STYLE_LEVELS = {
    "附录标题": 2,
    "附录一级标题": 2,
    "附录二级标题": 3,
    "附录三级标题": 4,
}


def toc_custom_style_switch() -> str:
    mapping = ",".join(
        f"{style_name},{level}"
        for style_name, level in TOC_CUSTOM_STYLE_LEVELS.items()
    )
    return f'\\t "{mapping}"'
