from __future__ import annotations

import re
from collections import defaultdict, deque


_NOTE_STYLE_TOKEN = "注-无编号注"
_NUMBERED_NOTE_STYLE_TOKEN = "注-有编号注"
_ORDERED_FORMATS = {
    "decimal", "decimalZero", "lowerLetter", "upperLetter",
    "lowerRoman", "upperRoman",
}


def _normalized(value: str | None) -> str:
    return (value or "").casefold().replace(" ", "")


def source_note_role(
    style_name: str | None,
    numbering: dict | None = None,
    text: str | None = None,
) -> str | None:
    """Resolve note semantics from source style, OOXML numbering, or text."""
    normalized_style = _normalized(style_name)
    if _NUMBERED_NOTE_STYLE_TOKEN in normalized_style:
        return "numbered_note"
    if _NOTE_STYLE_TOKEN in normalized_style:
        return "note"

    numbering = numbering or {}
    level_text = _normalized(str(numbering.get("lvl_text") or ""))
    num_fmt = str(numbering.get("num_fmt") or "")
    if re.fullmatch(r"注[：:]", level_text) and num_fmt == "none":
        return "note"
    if re.fullmatch(r"注%\d+[：:]", level_text) and num_fmt in _ORDERED_FORMATS:
        return "numbered_note"

    source_text = (text or "").strip()
    if re.match(r"^注\s*\d+\s*[：:]", source_text):
        return "numbered_note"
    if re.match(r"^注\s*[：:]", source_text):
        return "note"
    return None


def strip_source_note_marker(text: str, role: str | None) -> str:
    """Remove a literal note marker when the target style will render it."""
    if role == "numbered_note":
        return re.sub(r"^注\s*\d+\s*[：:]\s*", "", text, count=1).strip()
    if role == "note":
        return re.sub(r"^注\s*[：:]\s*", "", text, count=1).strip()
    return text


def audit_note_preservation(doc, model: dict | None, profile: dict | None = None) -> dict:
    """Check that source note roles survive AST construction and rendering."""
    profile = profile or {}
    expected_styles = {
        "note": profile.get("resolved_styles", {}).get("note", "3.1注-无编号注"),
        "numbered_note": profile.get("resolved_styles", {}).get(
            "numbered_note", "3.2注-有编号注",
        ),
    }
    rendered_by_text: dict[str, deque] = defaultdict(deque)
    for index, paragraph in enumerate(doc.paragraphs, 1):
        text = paragraph.text.strip()
        if text:
            rendered_by_text[text].append((index, paragraph.style.name))

    notes = []
    issues = []
    for block in (model or {}).get("document", {}).get("blocks", []):
        source = block.get("source", {})
        expected_role = source_note_role(
            source.get("style"), source.get("numbering"), source.get("raw_text"),
        )
        if expected_role is None:
            continue
        text = str(block.get("text") or "").strip()
        actual_role = block.get("role") or source.get("role")
        record = {
            "block_id": block.get("id"),
            "text": text[:120],
            "expected_role": expected_role,
            "actual_role": actual_role,
            "expected_style": expected_styles[expected_role],
        }
        if block.get("block_type") != "body" or actual_role != expected_role:
            issues.append({"type": "note_ast_role_mismatch", **record})

        rendered = rendered_by_text.get(text)
        if not rendered:
            issues.append({"type": "note_missing_from_output", **record})
            notes.append(record)
            continue
        paragraph_index, rendered_style = rendered.popleft()
        record.update({
            "paragraph": paragraph_index,
            "rendered_style": rendered_style,
        })
        if _normalized(rendered_style) != _normalized(expected_styles[expected_role]):
            issues.append({"type": "note_rendered_style_mismatch", **record})
        notes.append(record)

    return {
        "source_note_count": len(notes),
        "note_count": sum(item["expected_role"] == "note" for item in notes),
        "numbered_note_count": sum(
            item["expected_role"] == "numbered_note" for item in notes
        ),
        "notes": notes,
        "issues": issues,
        "passed": not issues,
    }
