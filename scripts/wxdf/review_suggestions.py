from __future__ import annotations

from typing import Any

SUGGESTION_TEMPLATES: dict[str, dict[str, str]] = {
    "missing_template_style": {
        "level": "high",
        "title": "使用了模板中不存在的样式",
        "suggestion": "请将文本样式改为模板中定义的样式：{allowed_styles}",
    },
    "heading_level_jump": {
        "level": "high",
        "title": "标题层级跳跃",
        "suggestion": "标题从 {from_level} 级直接跳到 {to_level} 级，中间缺少 {missing_level} 级标题。请在两者之间添加适当层级的标题",
    },
    "heading_level_invalid": {
        "level": "high",
        "title": "标题层级超出范围",
        "suggestion": "标题层级应在 1-6 之间，当前为 {actual_level} 级",
    },
    "list_nested_too_deep": {
        "level": "medium",
        "title": "列表嵌套层级过深",
        "suggestion": "模板最多支持两级列表，请将第 {actual_depth} 层及更深层级改为标题层级表达",
    },
    "list_numbering_broken": {
        "level": "medium",
        "title": "列表编号不连续",
        "suggestion": "列表编号在 {location} 处中断，预期 {expected} 但实际为 {actual}",
    },
    "list_numbering_not_restarted": {
        "level": "medium",
        "title": "列表未在标题后重新开始编号",
        "suggestion": "模板要求每遇到标题后列表从 a) 重新开始，此处首个列表项编号为 {actual_ordinal}，请改为从 1 开始",
    },
    "heading_manual_number": {
        "level": "low",
        "title": "标题含有手工编号",
        "suggestion": "标题「{text}」含有手工编号前缀，建议删除并交由多级编号样式自动生成",
    },
    "table_cell_style": {
        "level": "medium",
        "title": "表格单元格样式不符合模板",
        "suggestion": "普通语义表格单元格应使用「表正文」样式，当前为：{actual_styles}",
    },
    "format_value_mismatch": {
        "level": "medium",
        "title": "元素格式与模板不一致",
        "suggestion": "{role}的以下格式与模板不一致：{property}。请挂到模板样式并清除直接格式",
    },
    "format_run_direct_override": {
        "level": "medium",
        "title": "文本存在不允许的直接格式",
        "suggestion": "文本设置了模板未定义的直接格式（字号、颜色、下划线等）。强调只允许加粗或改用黑体不加粗，其余直接格式请清除",
    },
    "format_unexpected_direct": {
        "level": "low",
        "title": "段落存在多余的直接格式",
        "suggestion": "段落带有模板样式之外的直接格式（缩进、行距、对齐等），请清除并交由样式统一控制",
    },
    "page_section_count_mismatch": {
        "level": "medium",
        "title": "文档分节与模板不一致",
        "suggestion": "模板要求 {expected_count} 节（页码依次为 {expected_formats}），当前为 {actual_count} 节。请在目录之后与正文起始处分别设置分节符",
    },
    "page_numbering_mismatch": {
        "level": "medium",
        "title": "页码格式与模板不一致",
        "suggestion": "{section}的页码应为{expected}，当前为{actual}。请在「页码格式」中调整编号格式与起始页码",
    },
    "page_footer_missing": {
        "level": "low",
        "title": "分节缺少页脚",
        "suggestion": "该节没有页脚引用，页码无法显示。请为该节设置带 PAGE 域的页脚",
    },
    "formula_numbering_broken": {
        "level": "medium",
        "title": "公式编号不连续",
        "suggestion": "公式编号应为 {expected}，当前为 {actual}。请检查是否漏编、重编或被删除",
    },
    "formula_not_centered": {
        "level": "medium",
        "title": "公式未居中",
        "suggestion": "公式应居中排版，当前对齐为 {actual}。请在公式版式中设置居中",
    },
    "formula_number_not_right": {
        "level": "medium",
        "title": "公式编号未右对齐",
        "suggestion": "公式编号应右对齐，当前对齐为 {actual}。请在公式版式中设置右对齐",
    },
    "sequence_numbering_inconsistent": {
        "level": "medium",
        "title": "图表编号方案不统一",
        "suggestion": "全文图表编号须保持统一，当前混用了多种方案：{schemes}",
    },
    "citation_order_wrong": {
        "level": "medium",
        "title": "引用文件／依据文件排序不合规",
        "suggestion": "请按「国标、国军标、行业标、团体标、企标、国际标、规章制度、其他技术文件」的顺序排列",
    },
    "citation_format_wrong": {
        "level": "low",
        "title": "引用文件格式不合规",
        "suggestion": "标准号（如 GB、ISO）与数字之间应有一个空格，并按「标准号  版本号  文件名称」编排",
    },
    "citation_reference_missing": {
        "level": "medium",
        "title": "引用文件与正文引用未一一对应",
        "suggestion": "引用文件应与正文中的引用一一对应，请补充缺失的引用或删除未被引用的条目",
    },
    "glossary_order_wrong": {
        "level": "low",
        "title": "术语或缩略语排序不合规",
        "suggestion": "术语按条排列、中文优先；缩略语按数字、字母顺序排列",
    },
    "glossary_definition_in_abbreviation": {
        "level": "low",
        "title": "缩略语一章书写了定义",
        "suggestion": "缩略语一章不应书写定义，定义应放在术语一章",
    },
    "glossary_entry_missing_definition": {
        "level": "low",
        "title": "术语缺少定义",
        "suggestion": "请为术语补充定义（可在中文后附英文与英文缩略语）",
    },
    "list_used_as_heading": {
        "level": "medium",
        "title": "列项被当作标题使用",
        "suggestion": "下级标题应使用条标题（如「2.2.4 列项」）而非列项，以减少列项层级",
    },
    "list_item_is_prose": {
        "level": "medium",
        "title": "列项内使用了段落式文字",
        "suggestion": "超过一句话的内容不适合作为列项（列项内不应出现句号），请改用段落或条标题加段落",
    },
    "table_missing_caption": {
        "level": "medium",
        "title": "表格缺少题注",
        "suggestion": "请在表格上方添加题注（如「表1：表说明」）",
    },
    "figure_missing_caption": {
        "level": "medium",
        "title": "图片缺少题注",
        "suggestion": "请在图片下方添加题注（如「图1：图片说明」）",
    },
    "figure_note_below_caption": {
        "level": "medium",
        "title": "图注位于图题下方",
        "suggestion": "图注和脚注应在图题上方（顺序为图片、图注、图题），请把注移到图题之前",
    },
    "caption_target_missing": {
        "level": "medium",
        "title": "题注没有对应的表格或图片",
        "suggestion": "该题注未指向任何表格或图片，请把它放在对应对象的上方或下方，或删除多余的题注",
    },
    "toc_missing": {
        "level": "medium",
        "title": "缺少目录",
        "suggestion": "文档应在开头包含目录区域，标题为「目  次」",
    },
    "toc_level_too_deep": {
        "level": "low",
        "title": "目录层级过深",
        "suggestion": "目录域包含的标题层级为 1-{actual_level}，建议不超过 1-{expected_level}",
    },
    "toc_title_style": {
        "level": "medium",
        "title": "目录标题样式不符合模板",
        "suggestion": "目录标题应使用「{expected_style}」样式（黑体四号、不加粗、居中、1.25 倍行距），当前为「{actual_style}」",
    },
    "toc_entry_style": {
        "level": "medium",
        "title": "目录条目样式不符合模板",
        "suggestion": "目录条目应按模板的 TOC 1 至 TOC 4 样式排版（宋体小四、不加粗、1.25 倍行距、按层级缩进 0/2/4/6 字符），当前不符项：{issue_types}",
    },
    "toc_update_fields": {
        "level": "low",
        "title": "目录未设置打开时更新域",
        "suggestion": "文档未设置「打开时更新域」，目录页码可能显示为旧值；建议在 Word 的目录域属性中开启",
    },
    "appendix_structure_invalid": {
        "level": "medium",
        "title": "附录结构不规范",
        "suggestion": "附录应以「附录X」开头，使用附录标题样式，并独占一页",
    },
    "image_missing": {
        "level": "critical",
        "title": "图片缺失或无法读取",
        "suggestion": "图片 {image_id} 无法读取，请检查图片是否损坏或嵌入方式是否正确",
    },
    "style_direct_format_override": {
        "level": "low",
        "title": "存在直接格式覆盖",
        "suggestion": "段落存在直接格式（如直接设置字体、字号），建议使用样式统一管理格式",
    },
    "paragraph_alignment_inconsistent": {
        "level": "low",
        "title": "段落对齐方式不一致",
        "suggestion": "段落对齐方式与模板要求不一致，建议检查并统一对齐方式",
    },
    "heading_marker_not_stripped": {
        "level": "low",
        "title": "标题含有手工编号前缀",
        "suggestion": "标题「{text}」含有手工编号前缀，建议删除并使用自动编号",
    },
    "api_label_as_non_heading": {
        "level": "low",
        "title": "API 标签可能被误识别",
        "suggestion": "段落「{text}」含有 API 标签特征，如应为章节标题请使用标题样式",
    },
    "unexpected_break": {
        "level": "low",
        "title": "存在异常分页或分节",
        "suggestion": "文档中存在异常的分页符或分节符，建议检查文档结构",
    },
    "font_inconsistent": {
        "level": "low",
        "title": "字体使用不一致",
        "suggestion": "文本使用了模板未定义的字体 {actual_font}，建议使用模板定义的字体",
    },
    "line_spacing_inconsistent": {
        "level": "low",
        "title": "行距与模板不一致",
        "suggestion": "段落行距与模板要求不一致，建议使用样式统一控制行距",
    },
    "note_style_missing": {
        "level": "medium",
        "title": "注段落缺少专用样式",
        "suggestion": "注内容建议使用「注」或「注-X」样式，以便正确渲染",
    },
}


def get_suggestion(code: str, **kwargs: Any) -> dict[str, str]:
    template = SUGGESTION_TEMPLATES.get(code, {
        "level": "medium",
        "title": "未知问题",
        "suggestion": "请检查文档格式是否符合模板要求",
    })
    return {
        "code": code,
        "level": template["level"],
        "title": template["title"],
        "suggestion": template["suggestion"].format(**kwargs) if kwargs else template["suggestion"],
    }
