"""Independent model and saved-artifact audits for the modular renderer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

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
    try:
        with ZipFile(output) as archive:
            bad = archive.testzip()
            if bad:
                issues.append({"code": "zip_corrupt", "part": bad})
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                issues.append({"code": "missing_document_xml"})
    except (OSError, BadZipFile) as exc:
        return {"passed": False, "issues": [{"code": "invalid_docx", "message": str(exc)}]}

    try:
        document = Document(output)
    except Exception as exc:
        return {"passed": False, "issues": [{"code": "readback_failed", "message": str(exc)}]}
    actual_texts = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    expected_texts = [text for text in _expected_paragraph_texts(expected, render_map) if text]
    if actual_texts != expected_texts:
        issues.append({"code": "paragraph_order_changed", "expected": expected_texts, "actual": actual_texts})
    expected_tables = expected.get("expected_table_count")
    if expected_tables is None:
        expected_tables = sum(1 for item in render_map.values() if item.get("kind") == "table")
    if len(document.tables) != expected_tables:
        issues.append({"code": "table_count_changed", "expected": expected_tables, "actual": len(document.tables)})
    expected_numbered = any(item.get("numbered") for item in render_map.values())
    if expected_numbered and not any(item.style.name in {"List Number", "List Bullet"} or "列项" in item.style.name for item in document.paragraphs):
        issues.append({"code": "list_numbering_missing"})
    return {"passed": not issues, "issues": issues, "paragraph_texts": actual_texts, "table_count": len(document.tables)}
