from __future__ import annotations

from typing import Any

# Every title and suggestion here is written for a reader who does not know the
# pipeline: no internal vocabulary (table role names like ``data``, field codes,
# contract names, phase letters).  A finding says what is wrong and where to fix
# it, in the document's own terms.
SUGGESTION_TEMPLATES: dict[str, dict[str, str]] = {
    "missing_template_style": {
        "level": "high",
        "title": "使用了模板里没有的样式",
        "suggestion": "这段文字用了模板中没有的样式，请改成模板里已有的样式，例如：{allowed_styles}",
    },
    "heading_level_jump": {
        "level": "high",
        "title": "标题层级中间断档",
        "suggestion": "标题从第 {from_level} 级直接跳到第 {to_level} 级，缺少第 {missing_level} 级标题。请在两者之间补上对应层级的标题",
    },
    "heading_level_invalid": {
        "level": "high",
        "title": "标题层级超出范围",
        "suggestion": "标题层级应在第 1 到第 6 级之间，当前是第 {actual_level} 级",
    },
    "list_nested_too_deep": {
        "level": "medium",
        "title": "列表嵌套过深",
        "suggestion": "模板最多支持两级列表，当前是第 {actual_depth} 级。请把更深的层级改用标题加段落表达",
    },
    "list_numbering_broken": {
        "level": "medium",
        "title": "列表编号不连续",
        "suggestion": "列表编号在 {location} 处中断，应为 {expected}，实际是 {actual}",
    },
    "list_numbering_not_restarted": {
        "level": "medium",
        "title": "列表编号未在标题后重新开始",
        "suggestion": "每个标题之后的列表都应从 a) 重新编号，这里从 {actual_ordinal} 开始，请改为从第 1 项开始",
    },
    "heading_manual_number": {
        "level": "low",
        "title": "标题里带了手工编号",
        "suggestion": "标题「{text}」开头的手工编号多余，请删掉，交由标题样式自动编号",
    },
    "table_cell_style": {
        "level": "medium",
        "title": "表格单元格样式不符合模板",
        "suggestion": "表格单元格应使用模板的「表正文」样式，当前是：{actual_styles}",
    },
    "format_value_mismatch": {
        "level": "medium",
        "title": "格式与模板要求不一致",
        "suggestion": "{role}的{property}与模板要求不一致。请套用模板样式，并清除手动设置的格式",
    },
    "format_run_direct_override": {
        "level": "medium",
        "title": "文字上有多余的手动格式",
        "suggestion": "这段文字手动设置了字体、字号、颜色、下划线等格式。若要强调，只能加粗或改用黑体不加粗，其余手动格式请清除",
    },
    "format_unexpected_direct": {
        "level": "low",
        "title": "段落上有多余的手动格式",
        "suggestion": "这段段落手动设置了缩进、行距或对齐等格式，请清除，改为由样式统一控制",
    },
    "page_section_count_mismatch": {
        "level": "medium",
        "title": "分节与模板要求不一致",
        "suggestion": "模板要求全文分为 {expected_count} 节（页码依次为 {expected_formats}），当前是 {actual_count} 节。请在目录之后、正文开始处分别插入分节符",
    },
    "page_numbering_mismatch": {
        "level": "medium",
        "title": "页码格式与模板要求不一致",
        "suggestion": "{section}的页码应为{expected}，当前是{actual}。请在「页码格式」里调整编号格式和起始页码",
    },
    "page_footer_missing": {
        "level": "low",
        "title": "分节缺少页脚",
        "suggestion": "该节没有页脚，页码无法显示。请为该节设置带页码的页脚",
    },
    "formula_numbering_broken": {
        "level": "medium",
        "title": "公式编号不连续",
        "suggestion": "公式编号应为 {expected}，当前是 {actual}。请检查是否有漏编、重编或误删",
    },
    "formula_not_centered": {
        "level": "medium",
        "title": "公式没有居中",
        "suggestion": "公式应居中排版，当前对齐方式是 {actual}。请在公式所在段落设置居中",
    },
    "formula_number_not_right": {
        "level": "medium",
        "title": "公式编号没有右对齐",
        "suggestion": "公式编号应右对齐，当前对齐方式是 {actual}。请设置右对齐",
    },
    "sequence_numbering_inconsistent": {
        "level": "medium",
        "title": "图表编号方式不统一",
        "suggestion": "全文的图表编号方式应保持一致，当前混用了多种：{schemes}",
    },
    "citation_order_wrong": {
        "level": "medium",
        "title": "引用文件顺序不符合要求",
        "suggestion": "请按「国标、国军标、行业标、团体标、企标、国际标、规章制度、其他技术文件」的顺序排列引用文件",
    },
    "citation_format_wrong": {
        "level": "low",
        "title": "引用文件格式不规范",
        "suggestion": "标准号（如 GB、ISO）与数字之间应有一个空格，并按「标准号  版本号  文件名称」的格式书写",
    },
    "citation_reference_missing": {
        "level": "medium",
        "title": "引用文件与正文引用没有一一对应",
        "suggestion": "引用文件应与正文中出现的引用一一对应，请补上缺失的引用，或删除没有被引用的条目",
    },
    "glossary_order_wrong": {
        "level": "low",
        "title": "术语或缩略语排序不符合要求",
        "suggestion": "术语按条目排列、中文在前；缩略语按数字、字母顺序排列",
    },
    "glossary_definition_in_abbreviation": {
        "level": "low",
        "title": "缩略语部分写了定义",
        "suggestion": "缩略语部分不应写定义，定义请放在术语部分",
    },
    "glossary_entry_missing_definition": {
        "level": "low",
        "title": "术语缺少定义",
        "suggestion": "请为这个术语补充定义，可在中文后附英文和英文缩略语",
    },
    "list_used_as_heading": {
        "level": "medium",
        "title": "把列表项当成了标题",
        "suggestion": "这一项应改用条标题（例如「2.2.4 列项」）表达，不要用列表项充当标题，以减少列表层级",
    },
    "list_item_is_prose": {
        "level": "medium",
        "title": "列表项里写了成段的话",
        "suggestion": "列表项里不应出现句号，超过一句话的内容请改用段落，或条标题加段落",
    },
    "table_missing_caption": {
        "level": "medium",
        "title": "表格缺少题注",
        "suggestion": "请在表格上方添加题注，例如「表1 说明」",
    },
    "figure_missing_caption": {
        "level": "medium",
        "title": "图片缺少题注",
        "suggestion": "请在图片下方添加题注，例如「图1 图片说明」",
    },
    "figure_note_below_caption": {
        "level": "medium",
        "title": "图注放在了图题下面",
        "suggestion": "图注和脚注应放在图题上方（顺序为图片、图注、图题），请把注移到图题之前",
    },
    "caption_target_missing": {
        "level": "medium",
        "title": "题注没有对应的表格或图片",
        "suggestion": "这个题注找不到对应的表格或图片，请把它移到对应对象的上方或下方，或删除多余的题注",
    },
    "toc_missing": {
        "level": "medium",
        "title": "缺少目录",
        "suggestion": "文档开头应包含目录，标题为「目  次」",
    },
    "toc_level_too_deep": {
        "level": "low",
        "title": "目录层级过深",
        "suggestion": "目录包含了第 1 到第 {actual_level} 级标题，建议只保留到第 {expected_level} 级",
    },
    "toc_title_style": {
        "level": "medium",
        "title": "目录标题样式不符合模板",
        "suggestion": "目录标题应使用「{expected_style}」样式（黑体四号、不加粗、居中、1.25 倍行距），当前是「{actual_style}」",
    },
    "toc_entry_style": {
        "level": "medium",
        "title": "目录条目样式不符合模板",
        "suggestion": "目录条目应按模板排版：宋体小四、不加粗、1.25 倍行距，并按层级缩进 0/2/4/6 字符。当前不符的有：{issue_types}",
    },
    "toc_update_fields": {
        "level": "low",
        "title": "目录没有设置打开时更新",
        "suggestion": "文档没有开启「打开时更新域」，目录页码可能显示旧值，建议在目录属性中开启",
    },
    "appendix_structure_invalid": {
        "level": "medium",
        "title": "附录结构不规范",
        "suggestion": "附录应以「附录A」这样的编号开头，使用附录标题样式，并单独另起一页",
    },
    "image_missing": {
        "level": "critical",
        "title": "图片缺失或无法读取",
        "suggestion": "图片 {image_id} 无法读取，请检查图片是否损坏或插入方式是否正确",
    },
    "style_direct_format_override": {
        "level": "low",
        "title": "存在手动设置的格式",
        "suggestion": "这段内容手动设置了字体、字号等格式，建议改用样式统一管理",
    },
    "paragraph_alignment_inconsistent": {
        "level": "low",
        "title": "段落对齐方式不一致",
        "suggestion": "段落对齐方式与模板要求不一致，请检查并统一对齐方式",
    },
    "heading_marker_not_stripped": {
        "level": "low",
        "title": "标题里带了手工编号",
        "suggestion": "标题「{text}」开头的手工编号多余，请删掉，改用自动编号",
    },
    "api_label_as_non_heading": {
        "level": "low",
        "title": "接口标签可能被当成了正文",
        "suggestion": "段落「{text}」看起来像接口标签，如果它应是章节标题，请套用标题样式",
    },
    "unexpected_break": {
        "level": "low",
        "title": "存在异常的分页或分节",
        "suggestion": "文档里有异常的分页符或分节符，请检查文档结构",
    },
    "font_inconsistent": {
        "level": "low",
        "title": "字体不统一",
        "suggestion": "这段文字用了模板里没有的字体 {actual_font}，请改用模板规定的字体",
    },
    "line_spacing_inconsistent": {
        "level": "low",
        "title": "行距与模板要求不一致",
        "suggestion": "段落行距与模板要求不一致，建议改由样式统一控制行距",
    },
    "note_style_missing": {
        "level": "medium",
        "title": "注没有使用专用样式",
        "suggestion": "注的内容请使用模板的「注」或「注-X」样式，以便正确排版",
    },
}


def get_suggestion(code: str, **kwargs: Any) -> dict[str, str]:
    template = SUGGESTION_TEMPLATES.get(code, {
        "level": "medium",
        "title": "未知问题",
        "suggestion": "请检查这段内容的格式是否符合模板要求",
    })
    return {
        "code": code,
        "level": template["level"],
        "title": template["title"],
        "suggestion": template["suggestion"].format(**kwargs) if kwargs else template["suggestion"],
    }
