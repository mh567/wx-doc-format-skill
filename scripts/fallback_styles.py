from __future__ import annotations

from copy import deepcopy

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph


def set_run_fonts(run, east_asia="宋体", ascii_font="Times New Roman", size_pt=12, bold=None) -> None:
    run.font.name = ascii_font
    run.font.size = Pt(size_pt)
    run.font.color.rgb = RGBColor(0, 0, 0)
    if bold is not None:
        run.bold = bold
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), east_asia)
    fonts.set(qn("w:ascii"), ascii_font)
    fonts.set(qn("w:hAnsi"), ascii_font)


def set_style_fonts(style, east_asia="宋体", ascii_font="Times New Roman", size_pt=12, bold=None) -> None:
    style.font.name = ascii_font
    style.font.size = Pt(size_pt)
    style.font.color.rgb = RGBColor(0, 0, 0)
    if bold is not None:
        style.font.bold = bold
    rpr = style._element.get_or_add_rPr()
    fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), east_asia)
    fonts.set(qn("w:ascii"), ascii_font)
    fonts.set(qn("w:hAnsi"), ascii_font)


def ensure_paragraph_style(doc: Document, name: str):
    try:
        return doc.styles[name]
    except KeyError:
        return doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)


def set_spacing_xml(target, line: int | None = 300, line_rule: str = "auto", **attrs) -> None:
    p_pr = target._element.get_or_add_pPr() if hasattr(target, "_element") else target._p.get_or_add_pPr()
    spacing = p_pr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        p_pr.append(spacing)
    for attr in [
        "before", "beforeLines", "beforeAutospacing",
        "after", "afterLines", "afterAutospacing",
        "line", "lineRule",
    ]:
        key = qn(f"w:{attr}")
        if key in spacing.attrib:
            del spacing.attrib[key]
    if line is not None:
        spacing.set(qn("w:line"), str(line))
        spacing.set(qn("w:lineRule"), line_rule)
    for attr, value in attrs.items():
        if value is not None:
            spacing.set(qn(f"w:{attr}"), str(value))


def set_standard_spacing(target) -> None:
    set_spacing_xml(target, line=300, line_rule="auto",
                    before=0, beforeLines=0, beforeAutospacing=0,
                    after=0, afterLines=0, afterAutospacing=0)


def set_heading_spacing(target, style_name: str | None) -> None:
    if style_name == "Heading 1":
        set_spacing_xml(target, line=300, line_rule="auto",
                        before=50, beforeLines=50, beforeAutospacing=0,
                        after=50, afterLines=50, afterAutospacing=0)
    else:
        set_spacing_xml(target, line=300, line_rule="auto",
                        beforeLines=0, beforeAutospacing=0,
                        afterLines=0, afterAutospacing=0)


def set_caption_spacing(target) -> None:
    set_spacing_xml(target, line=240, line_rule="auto",
                    before=50, beforeLines=50, beforeAutospacing=0,
                    after=50, afterLines=50, afterAutospacing=0)


def set_table_body_spacing(target) -> None:
    set_spacing_xml(target, line=0, line_rule="atLeast",
                    before=0, beforeLines=0, beforeAutospacing=0,
                    after=0, afterLines=0, afterAutospacing=0)


def set_note_spacing(target) -> None:
    set_spacing_xml(target, line=300, line_rule="auto",
                    before=448, beforeAutospacing=0,
                    after=0, afterLines=0, afterAutospacing=0)


def set_numbered_note_spacing(target) -> None:
    set_spacing_xml(target, line=300, line_rule="auto",
                    before=448, beforeAutospacing=0,
                    after=0, afterLines=0, afterAutospacing=0)


def set_formula_spacing(target) -> None:
    set_standard_spacing(target)


def set_toc_spacing(target) -> None:
    set_standard_spacing(target)


def apply_page_setup(doc: Document) -> None:
    for section in doc.sections:
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        section.left_margin = Cm(3.17)
        section.right_margin = Cm(3.17)
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.header_distance = Cm(1.5)
        section.footer_distance = Cm(1.75)


def ensure_fallback_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    set_style_fonts(normal, east_asia="宋体", size_pt=12, bold=False)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.first_line_indent = Cm(1.13)
    set_standard_spacing(normal)

    title = ensure_paragraph_style(doc, "文档标题")
    set_style_fonts(title, east_asia="黑体", size_pt=14, bold=False)
    title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.left_indent = Pt(0)
    title.paragraph_format.first_line_indent = Pt(0)
    set_standard_spacing(title)

    note = ensure_paragraph_style(doc, "3.1注-无编号注")
    set_style_fonts(note, east_asia="宋体", size_pt=10.5, bold=False)
    note.paragraph_format.left_indent = Cm(1.53)
    note.paragraph_format.first_line_indent = Cm(-0.74)
    set_note_spacing(note)

    numbered_note = ensure_paragraph_style(doc, "3.2注-有编号注")
    set_style_fonts(numbered_note, east_asia="宋体", size_pt=10.5, bold=False)
    numbered_note.paragraph_format.left_indent = Cm(1.81)
    numbered_note.paragraph_format.first_line_indent = Cm(-0.93)
    set_numbered_note_spacing(numbered_note)

    formula = ensure_paragraph_style(doc, "公式")
    set_style_fonts(formula, east_asia="宋体", size_pt=12, bold=False)
    formula.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    formula.paragraph_format.first_line_indent = Pt(0)
    set_formula_spacing(formula)

    table_body = ensure_paragraph_style(doc, "表正文")
    set_style_fonts(table_body, east_asia="宋体", size_pt=10.5, bold=False)
    table_body.paragraph_format.first_line_indent = Pt(0)
    set_table_body_spacing(table_body)

    caption = ensure_paragraph_style(doc, "Caption")
    set_style_fonts(caption, east_asia="黑体", size_pt=12, bold=False)
    caption.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_caption_spacing(caption)

    heading_indents = {
        "Heading 1": 0.762, "Heading 2": 1.0142361111111111,
        "Heading 3": 1.27, "Heading 4": 1.524,
        "Heading 5": 1.778, "Heading 6": 2.032,
    }
    for name, indent_cm in heading_indents.items():
        style = ensure_paragraph_style(doc, name)
        set_style_fonts(style, east_asia="黑体", size_pt=12, bold=False)
        style.paragraph_format.left_indent = Cm(indent_cm)
        style.paragraph_format.first_line_indent = Cm(-indent_cm)
        set_heading_spacing(style, name)

    appendix_title = ensure_paragraph_style(doc, "附录标题")
    set_style_fonts(appendix_title, east_asia="黑体", size_pt=14, bold=False)
    appendix_title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    appendix_title.paragraph_format.left_indent = Pt(0)
    appendix_title.paragraph_format.first_line_indent = Pt(0)
    set_standard_spacing(appendix_title)

    for name, indent_cm in [
        ("附录一级标题", 0.762),
        ("附录二级标题", 1.0142361111111111),
        ("附录三级标题", 1.27),
    ]:
        style = ensure_paragraph_style(doc, name)
        set_style_fonts(style, east_asia="黑体", size_pt=12, bold=False)
        style.paragraph_format.left_indent = Cm(indent_cm)
        style.paragraph_format.first_line_indent = Cm(-indent_cm)
        set_heading_spacing(style, "Heading 1" if name == "附录一级标题" else "Heading 2")

    for level, indent_cm in [(1, 0), (2, 0.85), (3, 1.69), (4, 2.54)]:
        style = ensure_paragraph_style(doc, f"TOC {level}")
        set_style_fonts(style, east_asia="宋体", size_pt=12, bold=False)
        style.paragraph_format.left_indent = Cm(indent_cm)
        style.paragraph_format.first_line_indent = Pt(0)
        set_toc_spacing(style)

    for name, left_cm, hanging_cm in [
        ("1.1一级列项-编号", 1.6474722222222222, -0.8008055555555555),
        ("1.2一级列项-无编号", 1.760361111111111, -0.8766527777777777),
        ("2.1二级列项-有编号", 2.4429861111111113, -0.7496527777777777),
        ("2.2二级列项-无编号", 2.573513888888889, -0.8801805555555555),
    ]:
        style = ensure_paragraph_style(doc, name)
        set_style_fonts(style, east_asia="宋体", size_pt=12, bold=False)
        style.paragraph_format.left_indent = Cm(left_cm)
        style.paragraph_format.first_line_indent = Cm(hanging_cm)
        set_standard_spacing(style)


def next_numbering_id(numbering, tag_name: str, attr_name: str) -> int:
    max_id = 0
    for child in numbering:
        if child.tag == qn(tag_name):
            try:
                val = int(child.get(qn(attr_name)))
                if val > max_id:
                    max_id = val
            except (ValueError, TypeError):
                pass
    return max_id + 1


def append_text_child(parent, tag: str, attr: str, value: str):
    child = OxmlElement(tag)
    child.set(qn(attr), value)
    parent.append(child)


def append_level(parent, ilvl: int, num_fmt: str, lvl_text: str, left_twips: int, hanging_twips: int, style_id: str | None = None) -> None:
    lvl = OxmlElement("w:lvl")
    lvl.set(qn("w:ilvl"), str(ilvl))
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    lvl.append(start)
    append_text_child(lvl, "w:numFmt", "w:val", num_fmt)
    append_text_child(lvl, "w:lvlText", "w:val", lvl_text)
    p_pr = OxmlElement("w:pPr")
    ind = OxmlElement("w:ind")
    ind.set(qn("w:left"), str(left_twips))
    ind.set(qn("w:hanging"), str(hanging_twips))
    p_pr.append(ind)
    lvl.append(p_pr)
    if style_id is not None:
        append_text_child(lvl, "w:pStyle", "w:val", style_id)
    parent.append(lvl)


def append_num(numbering, abstract_num_id: int, num_id: int, start_override: int | None = None) -> None:
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    append_text_child(num, "w:abstractNumId", "w:val", str(abstract_num_id))
    if start_override is not None:
        start = OxmlElement("w:startOverride")
        start.set(qn("w:val"), str(start_override))
        num.append(start)
    numbering.append(num)


def ensure_auto_numbering(doc: Document) -> dict:
    numbering = doc.part.numbering_part.element
    abstract_id = next_numbering_id(numbering, "w:abstractNum", "w:abstractNumId")
    num_id = next_numbering_id(numbering, "w:num", "w:numId")

    heading_abs = OxmlElement("w:abstractNum")
    heading_abs.set(qn("w:abstractNumId"), str(abstract_id))
    append_text_child(heading_abs, "w:multiLevelType", "w:val", "multilevel")
    for ilvl, left in enumerate([432, 575, 720, 864, 1008, 1151]):
        tokens = ".".join(f"%{index}" for index in range(1, ilvl + 2))
        append_level(heading_abs, ilvl, "decimal", f"{tokens} ", left, left, f"Heading{ilvl + 1}")
    numbering.append(heading_abs)
    append_num(numbering, abstract_id, num_id)
    heading_num_id = num_id

    letter_abs_id = abstract_id + 1
    letter_num_id = num_id + 1
    letter_abs = OxmlElement("w:abstractNum")
    letter_abs.set(qn("w:abstractNumId"), str(letter_abs_id))
    append_text_child(letter_abs, "w:multiLevelType", "w:val", "singleLevel")
    append_level(letter_abs, 0, "lowerLetter", "%1)", 934, 454, None)
    numbering.append(letter_abs)
    append_num(numbering, letter_abs_id, letter_num_id)

    decimal_abs_id = abstract_id + 2
    decimal_num_id = num_id + 2
    decimal_abs = OxmlElement("w:abstractNum")
    decimal_abs.set(qn("w:abstractNumId"), str(decimal_abs_id))
    append_text_child(decimal_abs, "w:multiLevelType", "w:val", "singleLevel")
    append_level(decimal_abs, 0, "decimal", "%1)", 1385, 425, None)
    numbering.append(decimal_abs)
    append_num(numbering, decimal_abs_id, decimal_num_id)

    return {
        "heading": heading_num_id,
        "list_letter": letter_num_id,
        "list_decimal": decimal_num_id,
        "list_letter_abstract": letter_abs_id,
        "list_decimal_abstract": decimal_abs_id,
    }


def clear_document_body(doc: Document) -> None:
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def merge_template_numbering_ids(template_profile: dict | None, fallback_ids: dict) -> dict:
    if not template_profile:
        return fallback_ids
    return template_profile.get("numbering_ids", {})
