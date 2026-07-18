from __future__ import annotations

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


def classify_docx_table(
    table,
    *,
    multi_cell_code_sample: bool = False,
) -> TableSemantics:
    cells = unique_table_cells(table)
    visual_cell_count = len(cells)
    if visual_cell_count != 1:
        table_type = "code_sample" if multi_cell_code_sample else "data"
        return TableSemantics(
            table_type=table_type,
            header_rows=0 if table_type == "code_sample" else 1,
            visual_cell_count=visual_cell_count,
            caption_eligible=table_caption_eligible(table_type),
            confidence=0.9 if table_type == "code_sample" else 1.0,
            evidence=(
                "multiple_visual_cells",
                "legacy_code_sample_evidence" if table_type == "code_sample" else "relational_table_shape",
            ),
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
        for candidate_index in (index - 1, index + 1):
            if 0 <= candidate_index < len(blocks):
                candidate = blocks[candidate_index]
                if candidate.get("block_type") == "caption":
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
