from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from docx.oxml.ns import qn


TABLE_TYPES = {"data", "code_sample", "callout", "layout", "unknown"}
CAPTION_ELIGIBLE_TABLE_TYPES = {"data"}


@dataclass(frozen=True)
class TableSemantics:
    table_type: str
    header_rows: int
    visual_cell_count: int
    caption_eligible: bool
    confidence: float
    evidence: tuple[str, ...]

    def as_source_record(self) -> dict[str, Any]:
        return {
            "table_type": self.table_type,
            "header_rows": self.header_rows,
            "visual_cell_count": self.visual_cell_count,
            "caption_eligible": self.caption_eligible,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


def table_caption_eligible(table_type: str | None) -> bool:
    return str(table_type or "unknown") in CAPTION_ELIGIBLE_TABLE_TYPES


def unique_table_cells(table) -> list[Any]:
    cells: list[Any] = []
    seen = set()
    for row in table.rows:
        for cell in row.cells:
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            cells.append(cell)
    return cells


def _cell_has_graphics(cell) -> bool:
    element = cell._tc
    return any(
        next(element.iter(qn(tag)), None) is not None
        for tag in ("w:drawing", "w:pict", "w:object")
    )


def _cell_has_math(cell) -> bool:
    element = cell._tc
    return any(
        next(element.iter(qn(tag)), None) is not None
        for tag in ("m:oMath", "m:oMathPara")
    )


def _table_is_borderless(table) -> bool:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders")) if tbl_pr is not None else None
    if borders is None:
        return False
    edges = [
        borders.find(qn(f"w:{name}"))
        for name in ("top", "left", "bottom", "right", "insideH", "insideV")
    ]
    defined = [edge for edge in edges if edge is not None]
    return len(defined) == len(edges) and all(
        edge.get(qn("w:val")) in {"none", "nil"}
        for edge in defined
    )


def _looks_like_formula_layout(table, cells: list[Any]) -> bool:
    if len(table.rows) != 1 or len(cells) != 2:
        return False
    left, right = cells
    number = right.text.strip()
    if re.fullmatch(r"[(（]\d+(?:\.\d+)*[)）]", number) is None:
        return False
    return _cell_has_math(left) and _table_is_borderless(table)


def _looks_like_code_payload(text: str) -> bool:
    value = text.strip()
    if not value:
        return False
    if value.startswith(("{", "[", "<")):
        return True
    upper = value[:240].upper()
    markers = (
        "POST ", "GET ", "PUT ", "DELETE ", "PATCH ", "HTTP/1.",
        "<<HEADER>>", "CONTENT-TYPE:", "CONTENT-MD5", "<?XML",
        "PLAIN TEXT", "REQUEST BODY", "RESPONSE BODY",
    )
    if any(marker in upper for marker in markers):
        return True
    probe = value[:240]
    if "{" in probe and ("\"" in probe or ":" in probe):
        return True
    if "<" in probe and ">" in probe and "</" in probe:
        return True
    return False


def _multi_cell_code_payload_evidence(cells: list[Any]) -> tuple[bool, tuple[str, ...]]:
    texts = [cell.text.strip() for cell in cells if cell.text.strip()]
    if not texts:
        return False, ()
    payloads = [text for text in texts if _looks_like_code_payload(text)]
    if not payloads:
        return False, ()
    payload_ratio = len(payloads) / len(texts)
    longest_payload = max(len(text) for text in payloads)
    dominant = payload_ratio >= 0.25 or longest_payload >= 80
    evidence = (
        "multiple_visual_cells",
        "code_payload_content",
        f"payload_cells:{len(payloads)}",
        f"payload_ratio:{payload_ratio:.2f}",
    )
    return dominant, evidence


def classify_docx_table(
    table,
    *,
    multi_cell_code_sample: bool = False,
) -> TableSemantics:
    cells = unique_table_cells(table)
    visual_cell_count = len(cells)
    if visual_cell_count != 1:
        if _looks_like_formula_layout(table, cells):
            return TableSemantics(
                table_type="layout",
                header_rows=0,
                visual_cell_count=visual_cell_count,
                caption_eligible=False,
                confidence=1.0,
                evidence=(
                    "formula_number_layout",
                    "math_object",
                    "borderless_table",
                    "single_row_two_cells",
                ),
            )
        code_sample, code_evidence = _multi_cell_code_payload_evidence(cells)
        table_type = "code_sample" if code_sample else "data"
        evidence = code_evidence if code_sample else (
            "multiple_visual_cells",
            "relational_table_shape",
        )
        if multi_cell_code_sample and not code_sample:
            evidence = (*evidence, "legacy_header_signal_rejected_without_payload")
        return TableSemantics(
            table_type=table_type,
            header_rows=0 if table_type == "code_sample" else 1,
            visual_cell_count=visual_cell_count,
            caption_eligible=table_caption_eligible(table_type),
            confidence=0.95 if table_type == "code_sample" else 1.0,
            evidence=evidence,
        )

    cell = cells[0]
    text = cell.text.strip()
    has_nested_table = bool(cell.tables)
    has_graphics = _cell_has_graphics(cell)

    if has_nested_table or (not text and has_graphics) or not text:
        evidence = ["single_visual_cell"]
        if has_nested_table:
            evidence.append("nested_table")
        if has_graphics:
            evidence.append("graphic_content")
        if not text:
            evidence.append("no_text_content")
        return TableSemantics(
            table_type="layout",
            header_rows=0,
            visual_cell_count=1,
            caption_eligible=False,
            confidence=1.0,
            evidence=tuple(evidence),
        )

    if _looks_like_code_payload(text):
        return TableSemantics(
            table_type="code_sample",
            header_rows=0,
            visual_cell_count=1,
            caption_eligible=False,
            confidence=1.0,
            evidence=("single_visual_cell", "code_payload"),
        )

    return TableSemantics(
        table_type="callout",
        header_rows=0,
        visual_cell_count=1,
        caption_eligible=False,
        confidence=1.0,
        evidence=("single_visual_cell", "natural_language_content"),
    )


def audit_model_table_semantics(model: dict | None) -> dict[str, Any]:
    blocks = (model or {}).get("document", {}).get("blocks", [])
    tables: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    counts = {table_type: 0 for table_type in sorted(TABLE_TYPES)}

    for index, block in enumerate(blocks):
        if block.get("block_type") != "table":
            continue
        table_type = str(block.get("table_type") or "unknown")
        counts[table_type] = counts.get(table_type, 0) + 1
        eligible = table_caption_eligible(table_type)
        adjacent_captions = []
        if index > 0:
            candidate = blocks[index - 1]
            if (
                candidate.get("block_type") == "caption"
                and candidate.get("caption_type") in {"table", "unknown", None}
            ):
                adjacent_captions.append(candidate)
        auto_captions = [item for item in adjacent_captions if item.get("_auto_generated")]
        source_semantics = block.get("source", {}).get("table_semantics", {})
        record = {
            "block_id": block.get("id"),
            "source_position": block.get("source", {}).get("source_position"),
            "table_type": table_type,
            "caption_eligible": eligible,
            "caption_count": len(adjacent_captions),
            "auto_caption_count": len(auto_captions),
            "visual_cell_count": source_semantics.get("visual_cell_count"),
            "confidence": source_semantics.get("confidence"),
            "evidence": source_semantics.get("evidence", []),
        }
        tables.append(record)
        if auto_captions and not eligible:
            issues.append({
                "type": "auto_caption_on_ineligible_table",
                "block_id": block.get("id"),
                "table_type": table_type,
            })
        if eligible and not adjacent_captions:
            issues.append({
                "type": "data_table_missing_caption",
                "block_id": block.get("id"),
            })
        if index + 1 < len(blocks) and blocks[index + 1].get("block_type") == "caption":
            candidate = blocks[index + 1]
            if candidate.get("caption_type") in {"table", "unknown", None}:
                issues.append({
                    "type": "table_caption_below_table",
                    "block_id": block.get("id"),
                    "caption_id": candidate.get("id"),
                })
        if table_type == "unknown":
            issues.append({
                "type": "unknown_table_semantics",
                "block_id": block.get("id"),
            })

    return {
        "counts": counts,
        "tables": tables,
        "issues": issues,
        "passed": not issues,
    }
