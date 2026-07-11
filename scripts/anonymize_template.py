#!/usr/bin/env python3
"""Anonymize a WX template: replace visible text while preserving all styles and numbering."""

import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# Replacement text by paragraph style
STYLE_REPLACEMENTS = {
    "文档标题": "示例技术文件",
    "1 一级标题": "范围",
    "2 二级标题": "术语和定义",
    "3 三级标题": "技术要求",
    "4 四级标题": "测试方法",
    "5 五级标题": "检验规则",
    "6 六级标题": "包装运输",
    "1.1一级列项-编号": "第一类示例条目",
    "1.2一级列项-无编号": "第二类示例条目",
    "2.1二级列项-有编号": "第一细分类目",
    "2.2二级列项-无编号": "第二细分类目",
    "3.1三级列项-有编号": "更细分项示例",
    "附录标题": "附录示例",
}

DEFAULT_REPLACEMENTS = {
    "Normal": "本段为格式示例文本，用于验证正文样式、缩进和行距配置。",
    "表正文": "示例字段",
    "Caption": "示例题注文本",
}


def replace_paragraph_text(xml_path: Path, replacements: dict):
    """Replace w:t text in paragraphs, matching by w:pStyle first, fallback to generic."""
    ET.register_namespace("w", NS["w"])
    tree = ET.parse(xml_path)
    root = tree.getroot()

    for p in root.findall(".//w:p", NS):
        style_el = p.find(".//w:pPr/w:pStyle", NS)
        style_id = style_el.get(f"{{{NS['w']}}}val") if style_el is not None else None

        new_text = None
        if style_id in replacements:
            items = replacements[style_id]
            new_text = items if isinstance(items, str) else items[0]

        for t in p.findall(".//w:t", NS):
            if t.text and len(t.text.strip()) > 3:
                t.text = new_text if new_text else "格式示例文本。"

    tree.write(xml_path, xml_declaration=True, encoding="UTF-8")


def replace_text_in_xml(xml_path: Path, replacements: dict):
    """Replace all w:t text in an XML file."""
    ET.register_namespace("w", NS["w"])
    if not xml_path.exists():
        return
    tree = ET.parse(xml_path)
    root = tree.getroot()
    changed = False

    for t in root.findall(".//w:t", NS):
        if t.text:
            # Keep short labels (menus, field codes) as-is
            if len(t.text.strip()) <= 5:
                continue
            t.text = replacements.get(
                t.text.strip(),
                "本段为格式示例文本。"
            )
            changed = True

    if changed:
        tree.write(xml_path, xml_declaration=True, encoding="UTF-8")


def anonymize_template(input_path: str, output_path: str):
    """Create anonymized copy of template docx."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Unzip
        with ZipFile(input_path, "r") as zf:
            zf.extractall(tmp)

        # Prepare per-style replacements with lists for rotation
        style_reps = {}
        for k, v in STYLE_REPLACEMENTS.items():
            style_reps[k] = v

        # Replace in document body
        doc_xml = tmp / "word" / "document.xml"
        if doc_xml.exists():
            replace_paragraph_text(doc_xml, style_reps)

        # Replace in headers/footers
        for part in ["header1.xml", "header2.xml", "header3.xml",
                     "footer1.xml", "footer2.xml", "footer3.xml"]:
            p = tmp / "word" / part
            if p.exists():
                replace_text_in_xml(p, DEFAULT_REPLACEMENTS)

        # Replace in footnotes/endnotes
        for part in ["footnotes.xml", "endnotes.xml"]:
            p = tmp / "word" / part
            if p.exists():
                replace_text_in_xml(p, DEFAULT_REPLACEMENTS)

        # Replace docProps core.xml — only dc:title and dc:description
        core = tmp / "docProps" / "core.xml"
        if core.exists():
            core_tree = ET.parse(core)
            cr = core_tree.getroot()
            for child in cr:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag in ("title", "description"):
                    child.text = "示例技术文件模板"
            core_tree.write(core, xml_declaration=True, encoding="UTF-8")

        # Clean temp files
        for junk in tmp.glob("docProps/thumbnail*"):
            junk.unlink()

        # Repack
        shutil.make_archive(
            str(Path(output_path).with_suffix("")),
            "zip", tmp,
        )
        Path(output_path).with_suffix(".zip").rename(output_path)

    print(f"Anonymized: {output_path}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: anonymize_template.py <input.docx> <output.docx>")
        sys.exit(1)
    anonymize_template(sys.argv[1], sys.argv[2])
