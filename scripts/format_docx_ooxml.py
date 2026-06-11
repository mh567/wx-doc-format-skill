#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}

ET.register_namespace("w", NS["w"])


def w(tag: str) -> str:
    return f"{{{NS['w']}}}{tag}"


def get_attr(element: ET.Element, name: str) -> str | None:
    return element.get(w(name))


def set_attr(element: ET.Element, name: str, value: str | int) -> None:
    element.set(w(name), str(value))


def child(parent: ET.Element, tag: str) -> ET.Element | None:
    return parent.find(w(tag))


def ensure_child(parent: ET.Element, tag: str, first: bool = False) -> ET.Element:
    existing = child(parent, tag)
    if existing is not None:
        return existing
    element = ET.Element(w(tag))
    if first:
        parent.insert(0, element)
    else:
        parent.append(element)
    return element


def clear_attrs(element: ET.Element, names: list[str]) -> None:
    for name in names:
        element.attrib.pop(w(name), None)


def set_spacing(p_pr: ET.Element, line: int, line_rule: str = "auto", **attrs: int) -> None:
    spacing = ensure_child(p_pr, "spacing")
    clear_attrs(
        spacing,
        [
            "before",
            "beforeLines",
            "beforeAutospacing",
            "after",
            "afterLines",
            "afterAutospacing",
            "line",
            "lineRule",
        ],
    )
    set_attr(spacing, "line", line)
    set_attr(spacing, "lineRule", line_rule)
    for key, value in attrs.items():
        set_attr(spacing, key, value)


def set_standard_spacing(p_pr: ET.Element) -> None:
    set_spacing(
        p_pr,
        300,
        "auto",
        before=0,
        beforeLines=0,
        beforeAutospacing=0,
        after=0,
        afterLines=0,
        afterAutospacing=0,
    )


def set_heading_spacing(p_pr: ET.Element, level: int) -> None:
    if level == 1:
        set_spacing(
            p_pr,
            300,
            "auto",
            before=50,
            beforeLines=50,
            beforeAutospacing=0,
            after=50,
            afterLines=50,
            afterAutospacing=0,
        )
    else:
        set_spacing(
            p_pr,
            300,
            "auto",
            beforeLines=0,
            beforeAutospacing=0,
            afterLines=0,
            afterAutospacing=0,
        )


def set_caption_spacing(p_pr: ET.Element) -> None:
    set_spacing(
        p_pr,
        240,
        "auto",
        before=50,
        beforeLines=50,
        beforeAutospacing=0,
        after=50,
        afterLines=50,
        afterAutospacing=0,
    )


def set_table_body_spacing(p_pr: ET.Element) -> None:
    set_spacing(
        p_pr,
        0,
        "atLeast",
        before=0,
        beforeLines=0,
        beforeAutospacing=0,
        after=0,
        afterLines=0,
        afterAutospacing=0,
    )


def set_note_spacing(p_pr: ET.Element) -> None:
    set_spacing(
        p_pr,
        300,
        "auto",
        before=448,
        beforeAutospacing=0,
        after=0,
        afterLines=0,
        afterAutospacing=0,
    )


def set_numbered_note_spacing(p_pr: ET.Element) -> None:
    set_spacing(
        p_pr,
        300,
        "auto",
        before=448,
        beforeAutospacing=0,
        after=0,
        afterLines=0,
        afterAutospacing=0,
    )


def set_ind(p_pr: ET.Element, **attrs: int) -> None:
    ind = ensure_child(p_pr, "ind")
    for key, value in attrs.items():
        set_attr(ind, key, value)


def set_jc(p_pr: ET.Element, value: str) -> None:
    jc = ensure_child(p_pr, "jc")
    set_attr(jc, "val", value)


def set_fonts(r_pr: ET.Element, east_asia: str, size_half_points: int, ascii_font: str = "Times New Roman") -> None:
    fonts = ensure_child(r_pr, "rFonts", first=True)
    set_attr(fonts, "eastAsia", east_asia)
    set_attr(fonts, "ascii", ascii_font)
    set_attr(fonts, "hAnsi", ascii_font)
    size = ensure_child(r_pr, "sz")
    set_attr(size, "val", size_half_points)
    size_cs = ensure_child(r_pr, "szCs")
    set_attr(size_cs, "val", size_half_points)
    color = ensure_child(r_pr, "color")
    set_attr(color, "val", "000000")


def style_name(style: ET.Element) -> str:
    name = child(style, "name")
    return get_attr(name, "val") if name is not None else ""


def style_id(style: ET.Element) -> str:
    return get_attr(style, "styleId") or ""


def find_style(styles_root: ET.Element, names: set[str], ids: set[str]) -> ET.Element | None:
    for style in styles_root.findall(w("style")):
        if style_name(style) in names or style_id(style) in ids:
            return style
    return None


def ensure_style(styles_root: ET.Element, name: str, style_id_value: str) -> ET.Element:
    existing = find_style(styles_root, {name}, {style_id_value})
    if existing is not None:
        return existing
    style = ET.Element(w("style"))
    set_attr(style, "type", "paragraph")
    set_attr(style, "styleId", style_id_value)
    name_el = ET.SubElement(style, w("name"))
    set_attr(name_el, "val", name)
    styles_root.append(style)
    return style


def configure_style(style: ET.Element, kind: str, level: int | None = None) -> None:
    p_pr = ensure_child(style, "pPr", first=False)
    r_pr = ensure_child(style, "rPr", first=False)
    if kind == "normal":
        set_fonts(r_pr, "宋体", 24)
        set_ind(p_pr, firstLine=640, firstLineChars=200)
        set_jc(p_pr, "both")
        set_standard_spacing(p_pr)
    elif kind == "title":
        set_fonts(r_pr, "黑体", 28)
        set_ind(p_pr, left=0, firstLine=0, firstLineChars=0)
        set_jc(p_pr, "center")
        set_standard_spacing(p_pr)
    elif kind == "heading":
        heading_level = int(level or 1)
        lefts = {1: 432, 2: 575, 3: 720, 4: 864, 5: 1008, 6: 1152}
        left = lefts.get(heading_level, 1008)
        set_fonts(r_pr, "黑体", 24)
        set_ind(p_pr, left=left, hanging=left, firstLineChars=0)
        set_heading_spacing(p_pr, heading_level)
    elif kind == "caption":
        set_fonts(r_pr, "黑体", 24)
        set_ind(p_pr, firstLine=0, firstLineChars=0)
        set_jc(p_pr, "center")
        set_caption_spacing(p_pr)
    elif kind == "table":
        set_fonts(r_pr, "宋体", 21)
        set_ind(p_pr, firstLine=0, firstLineChars=0)
        set_table_body_spacing(p_pr)
    elif kind == "note":
        set_fonts(r_pr, "宋体", 21)
        set_ind(p_pr, left=867, hanging=419, firstLineChars=0)
        set_note_spacing(p_pr)
    elif kind == "numbered_note":
        set_fonts(r_pr, "宋体", 21)
        set_ind(p_pr, left=1026, hanging=578, firstLineChars=0)
        set_numbered_note_spacing(p_pr)
    elif kind == "formula":
        set_fonts(r_pr, "宋体", 24)
        set_ind(p_pr, firstLine=0, firstLineChars=0)
        set_jc(p_pr, "center")
        set_standard_spacing(p_pr)
    elif kind == "appendix_title":
        set_fonts(r_pr, "黑体", 28)
        set_ind(p_pr, left=0, firstLine=0, firstLineChars=0)
        set_jc(p_pr, "center")
        set_standard_spacing(p_pr)
    elif kind == "list1":
        set_fonts(r_pr, "宋体", 24)
        set_ind(p_pr, left=934, hanging=454, firstLineChars=0)
        set_standard_spacing(p_pr)
    elif kind == "list2":
        set_fonts(r_pr, "宋体", 24)
        set_ind(p_pr, left=1385, hanging=425, firstLineChars=0)
        set_standard_spacing(p_pr)
    elif kind == "toc":
        toc_level = int(level or 1)
        set_fonts(r_pr, "宋体", 24)
        set_ind(p_pr, left=(toc_level - 1) * 480, firstLine=0, firstLineChars=0)
        set_standard_spacing(p_pr)


def style_map(styles_root: ET.Element) -> dict[str, str]:
    mapping = {}
    for style in styles_root.findall(w("style")):
        sid = style_id(style)
        name = style_name(style)
        if sid:
            mapping[sid] = name
    return mapping


def patch_styles(xml_text: bytes) -> tuple[bytes, dict[str, str]]:
    root = ET.fromstring(xml_text)
    normal = find_style(root, {"Normal"}, {"Normal", "1"})
    if normal is not None:
        configure_style(normal, "normal")
    configure_style(ensure_style(root, "文档标题", "wxTitle"), "title")
    configure_style(ensure_style(root, "Caption", "Caption"), "caption")
    configure_style(ensure_style(root, "表正文", "wxTableBody"), "table")
    configure_style(ensure_style(root, "3.1注-无编号注", "wxNote"), "note")
    configure_style(ensure_style(root, "3.2注-有编号注", "wxNumberedNote"), "numbered_note")
    configure_style(ensure_style(root, "公式", "wxFormula"), "formula")
    configure_style(ensure_style(root, "附录标题", "wxAppendixTitle"), "appendix_title")
    configure_style(ensure_style(root, "附录一级标题", "wxAppendixHeading1"), "heading", 1)
    configure_style(ensure_style(root, "附录二级标题", "wxAppendixHeading2"), "heading", 2)
    configure_style(ensure_style(root, "附录三级标题", "wxAppendixHeading3"), "heading", 3)
    configure_style(ensure_style(root, "1.1一级列项-编号", "wxList1"), "list1")
    configure_style(ensure_style(root, "2.1二级列项-有编号", "wxList2"), "list2")
    configure_style(ensure_style(root, "1.2一级列项-无编号", "wxListDash"), "list1")
    configure_style(ensure_style(root, "2.2二级列项-无编号", "wxListBullet2"), "list2")
    for level in range(1, 7):
        style = find_style(root, {f"heading {level}", f"Heading {level}"}, {f"Heading{level}", str(level + 1)})
        if style is not None:
            configure_style(style, "heading", level)
    for level in range(1, 5):
        configure_style(ensure_style(root, f"TOC {level}", f"TOC{level}"), "toc", level)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), style_map(root)


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()


def content_warnings_from_texts(texts: list[str]) -> list[dict]:
    joined = "\n".join(texts)
    warnings = []
    if any(text in {"目次", "目录"} for text in texts):
        warnings.append({"type": "toc", "message": "发现目录或目次，应确认目录层级缩进、页码和域更新状态。"})
    if "引用文件" in joined or "依据文件" in joined:
        warnings.append({"type": "references", "message": "发现引用文件或依据文件，应人工核对排序、标准号空格、正文引用对应关系。"})
    if "术语" in joined or "缩略语" in joined:
        warnings.append({"type": "terms", "message": "发现术语或缩略语章节，应人工核对术语定义和缩略语排序。"})
    if "公式" in joined or any(text.strip("()").isdigit() for text in texts):
        warnings.append({"type": "formula", "message": "发现公式或公式编号，应人工核对公式居中、编号右对齐和全文连续编号。"})
    if any(text.startswith("附录") or text in {"（资料性）", "（规范性）", "资料性", "规范性"} for text in texts):
        warnings.append({"type": "appendix", "message": "发现附录内容，应人工核对附录标题、附录编号、附录目录显示和页码。"})
    if any("图" in text and "注" in text for text in texts) or any("脚注" in text for text in texts):
        warnings.append({"type": "figure_table_notes", "message": "发现图注、表注或脚注相关内容，应人工核对其位置和注样式。"})
    return warnings


def paragraph_style_id(paragraph: ET.Element) -> str | None:
    p_pr = child(paragraph, "pPr")
    if p_pr is None:
        return None
    p_style = child(p_pr, "pStyle")
    return get_attr(p_style, "val") if p_style is not None else None


def patch_document_xml(xml_text: bytes, styles: dict[str, str]) -> tuple[bytes, list[dict]]:
    root = ET.fromstring(xml_text)
    texts = []
    for paragraph in root.findall(".//w:p", NS):
        text = paragraph_text(paragraph)
        if text:
            texts.append(text)
        p_pr = ensure_child(paragraph, "pPr", first=True)
        sid = paragraph_style_id(paragraph)
        name = styles.get(sid or "", "")
        if name.startswith("heading ") or name.startswith("Heading "):
            try:
                level = int(name.split()[-1])
            except ValueError:
                level = 1
            set_heading_spacing(p_pr, level)
        elif name in {"Caption", "图表标题"} or text.startswith(("图 ", "图：", "表 ", "表：")):
            set_caption_spacing(p_pr)
        elif name == "表正文":
            set_table_body_spacing(p_pr)
        elif "3.2" in name or "有编号注" in name:
            set_numbered_note_spacing(p_pr)
        elif "注" in name and name != "Normal":
            set_note_spacing(p_pr)
        elif name == "公式":
            set_standard_spacing(p_pr)
            set_jc(p_pr, "center")
        elif name.startswith("TOC"):
            set_standard_spacing(p_pr)
        elif "列项" in name or name == "List Paragraph":
            set_standard_spacing(p_pr)
        else:
            set_standard_spacing(p_pr)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), content_warnings_from_texts(texts)


def convert_docx_ooxml(input_path: Path, output_path: Path, report_path: Path | None = None, report_md_path: Path | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "stdlib-ooxml",
        "input": str(input_path),
        "output": str(output_path),
        "patched": [],
        "warnings": [],
        "content_warnings": [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(input_path, "r") as zin:
            zin.extractall(tmp_path)
        styles_file = tmp_path / "word" / "styles.xml"
        document_file = tmp_path / "word" / "document.xml"
        styles = {}
        if styles_file.exists():
            patched, styles = patch_styles(styles_file.read_bytes())
            styles_file.write_bytes(patched)
            report["patched"].append("word/styles.xml")
        else:
            report["warnings"].append("word/styles.xml not found")
        if document_file.exists():
            patched_document, content_warnings = patch_document_xml(document_file.read_bytes(), styles)
            document_file.write_bytes(patched_document)
            report["content_warnings"] = content_warnings
            report["patched"].append("word/document.xml")
        else:
            report["warnings"].append("word/document.xml not found")
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for file in tmp_path.rglob("*"):
                if file.is_file():
                    zout.write(file, file.relative_to(tmp_path).as_posix())
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_md_path is not None:
        report_md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# WX OOXML 格式转换报告", "", f"- 模式：{report['mode']}", f"- 输出：{report['output']}", ""]
        lines.append("## 已修改")
        lines.extend(f"- {item}" for item in report["patched"])
        if report["warnings"]:
            lines.append("")
            lines.append("## 警告")
            lines.extend(f"- {item}" for item in report["warnings"])
        if report.get("content_warnings"):
            lines.append("")
            lines.append("## 内容型复核提示")
            lines.extend(f"- {item}" for item in report["content_warnings"])
        report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply WX formatting to DOCX with stdlib OOXML editing.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--report-md", type=Path, default=None)
    args = parser.parse_args()
    convert_docx_ooxml(args.input, args.output, args.report, args.report_md)
    print(args.output)


if __name__ == "__main__":
    main()
