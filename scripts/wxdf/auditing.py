"""Independent model and saved-artifact audits for the modular renderer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from docx import Document

from .render_plan import build_render_plan
from .validation import validate_ast


def check_model(normalized: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        validate_ast(normalized["source_ast"])
    except Exception as exc:
        issues.append({"code": "ast_invalid", "message": str(exc)})
    plan = build_render_plan(normalized)
    presentation = normalized.get("presentation", {}).get("nodes", {})
    for node_id, node in presentation.items():
        if not node.get("marker_anchor"):
            issues.append({"code": "missing_marker_anchor", "node_id": node_id})
    listed = [item["node_id"] for item in plan["operations"] if item.get("kind") == "list_item"]
    if set(listed) != set(presentation):
        issues.append({"code": "render_plan_list_mismatch", "expected": sorted(presentation), "actual": sorted(listed)})
    return {"passed": not issues, "issues": issues, "render_plan": plan}


def _expected_paragraph_texts(expected: Mapping[str, Any], render_map: Mapping[str, Any]) -> list[str]:
    if expected.get("expected_texts"):
        return [str(value) for value in expected["expected_texts"]]
    return [str(item.get("text") or "") for item in render_map.values() if item.get("kind") == "paragraph"]


def check_saved_output(path: Path | str, expected: Mapping[str, Any], render_map: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    output = Path(path)
    styles_xml = None
    numbering_xml = None
    try:
        with ZipFile(output) as archive:
            bad = archive.testzip()
            if bad:
                issues.append({"code": "zip_corrupt", "part": bad})
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                issues.append({"code": "missing_document_xml"})
            if "word/styles.xml" in names:
                styles_xml = archive.read("word/styles.xml")
            if "word/numbering.xml" in names:
                numbering_xml = archive.read("word/numbering.xml")
    except (OSError, BadZipFile) as exc:
        return {"passed": False, "issues": [{"code": "invalid_docx", "message": str(exc)}]}

    try:
        document = Document(output)
    except Exception as exc:
        return {"passed": False, "issues": [{"code": "readback_failed", "message": str(exc)}]}
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    xml_paragraphs: list[Any] = []
    table_count = 0

    def collect_xml(element: Any) -> None:
        nonlocal table_count
        for child in list(element):
            if child.tag == f"{{{namespace}}}p":
                xml_paragraphs.append(child)
            elif child.tag == f"{{{namespace}}}tbl":
                table_count += 1
                collect_xml(child)
            elif child.tag == f"{{{namespace}}}sdt":
                continue
            else:
                collect_xml(child)

    collect_xml(document.element.body)
    actual_texts = [
        "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        for paragraph in xml_paragraphs
        if "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
    ]
    expected_texts = [text for text in _expected_paragraph_texts(expected, render_map) if text]
    if actual_texts != expected_texts:
        issues.append({"code": "paragraph_order_changed", "expected": expected_texts, "actual": actual_texts})
    expected_tables = expected.get("expected_table_count")
    if expected_tables is None:
        expected_tables = sum(1 for item in render_map.values() if item.get("kind") == "table")
    if table_count != expected_tables:
        issues.append({"code": "table_count_changed", "expected": expected_tables, "actual": table_count})
    expected_numbered_items = [item for item in render_map.values() if item.get("numbered")]
    expected_numbered_texts = [str(item.get("text") or "") for item in expected_numbered_items if str(item.get("text") or "")]
    expected_numbered_styles = {str(item.get("style") or "") for item in expected_numbered_items if item.get("style")}
    style_names: dict[str, str] = {}
    style_num_info: dict[str, tuple[int | None, int]] = {}
    if styles_xml is not None:
        styles_root = ElementTree.fromstring(styles_xml)
        for style in styles_root.findall("w:style", ns):
            style_id = style.get(f"{{{namespace}}}styleId")
            name = style.find("w:name", ns)
            if style_id and name is not None:
                style_names[style_id] = name.get(f"{{{namespace}}}val") or ""
            num_pr = style.find("w:pPr/w:numPr", ns)
            if style_id and num_pr is not None:
                num_id = num_pr.find("w:numId", ns)
                ilvl = num_pr.find("w:ilvl", ns)
                try:
                    parsed_num_id = int(num_id.get(f"{{{namespace}}}val")) if num_id is not None else None
                except (TypeError, ValueError):
                    parsed_num_id = None
                try:
                    parsed_ilvl = int(ilvl.get(f"{{{namespace}}}val")) if ilvl is not None else 0
                except (TypeError, ValueError):
                    parsed_ilvl = 0
                style_num_info[style_id] = (parsed_num_id, parsed_ilvl)
    expected_numbered_style_ids = {style_id for style_id, name in style_names.items() if name in expected_numbered_styles}
    valid_num_fmts = {"decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman", "bullet"}
    numbering_num_to_abstract: dict[int, int] = {}
    numbering_levels: dict[int, dict[int, str]] = {}
    numbering_overrides: dict[int, dict[int, str]] = {}
    if numbering_xml is not None:
        numbering_root = ElementTree.fromstring(numbering_xml)
        for abstract in numbering_root.findall("w:abstractNum", ns):
            try:
                abstract_id = int(abstract.get(f"{{{namespace}}}abstractNumId"))
            except (TypeError, ValueError):
                continue
            levels: dict[int, str] = {}
            for level in abstract.findall("w:lvl", ns):
                try:
                    ilvl = int(level.get(f"{{{namespace}}}ilvl", "0"))
                except (TypeError, ValueError):
                    ilvl = 0
                num_fmt = level.find("w:numFmt", ns)
                if num_fmt is not None:
                    levels[ilvl] = num_fmt.get(f"{{{namespace}}}val") or ""
            numbering_levels[abstract_id] = levels
        for num in numbering_root.findall("w:num", ns):
            try:
                num_id = int(num.get(f"{{{namespace}}}numId"))
                abstract = num.find("w:abstractNumId", ns)
                if abstract is None:
                    continue
                numbering_num_to_abstract[num_id] = int(abstract.get(f"{{{namespace}}}val"))
            except (TypeError, ValueError):
                continue
            overrides: dict[int, str] = {}
            for override in num.findall("w:lvlOverride", ns):
                try:
                    ilvl = int(override.get(f"{{{namespace}}}ilvl", "0"))
                except (TypeError, ValueError):
                    ilvl = 0
                override_level = override.find("w:lvl/w:numFmt", ns)
                if override_level is not None:
                    overrides[ilvl] = override_level.get(f"{{{namespace}}}val") or ""
            if overrides:
                numbering_overrides[num_id] = overrides
    numbered_paragraphs: list[Any] = []
    invalid_numbering: list[dict[str, Any]] = []
    for paragraph in xml_paragraphs:
        direct = paragraph.find("w:pPr/w:numPr", ns)
        direct_num_id = direct.find("w:numId", ns) if direct is not None else None
        direct_ilvl = direct.find("w:ilvl", ns) if direct is not None else None
        style = paragraph.find("w:pPr/w:pStyle", ns)
        style_id = style.get(f"{{{namespace}}}val") if style is not None else None
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        direct_numbering = direct_num_id is not None and direct_num_id.get(f"{{{namespace}}}val") not in {None, "0"}
        expected_numbering = style_id in expected_numbered_style_ids or (direct_numbering and text in expected_numbered_texts)
        if expected_numbering:
            numbered_paragraphs.append(paragraph)
            style_num_id, style_ilvl = style_num_info.get(style_id, (None, 0))
            try:
                num_id = int(direct_num_id.get(f"{{{namespace}}}val")) if direct_num_id is not None else style_num_id
            except (TypeError, ValueError):
                num_id = None
            try:
                ilvl = int(direct_ilvl.get(f"{{{namespace}}}val")) if direct_ilvl is not None else style_ilvl
            except (TypeError, ValueError):
                ilvl = style_ilvl
            abstract_id = numbering_num_to_abstract.get(num_id) if num_id is not None else None
            num_fmt = numbering_overrides.get(num_id, {}).get(ilvl) if num_id is not None else None
            if num_fmt is None and abstract_id is not None:
                num_fmt = numbering_levels.get(abstract_id, {}).get(ilvl)
            if num_id is None or abstract_id is None or num_fmt not in valid_num_fmts:
                invalid_numbering.append({"text": text, "num_id": num_id, "ilvl": ilvl, "abstract_num_id": abstract_id, "num_fmt": num_fmt})
    actual_numbered_texts = [
        "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        for paragraph in numbered_paragraphs
        if "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
    ]
    if len(numbered_paragraphs) != len(expected_numbered_items) or actual_numbered_texts != expected_numbered_texts:
        issues.append(
            {
                "code": "list_numbering_missing",
                "expected_count": len(expected_numbered_items),
                "actual_count": len(numbered_paragraphs),
                "expected_texts": expected_numbered_texts,
                "actual_texts": actual_numbered_texts,
            }
        )
    if invalid_numbering:
        issues.append({"code": "list_numbering_definition_invalid", "items": invalid_numbering})
    return {"passed": not issues, "issues": issues, "paragraph_texts": actual_texts, "table_count": table_count}
