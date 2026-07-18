from __future__ import annotations

from copy import deepcopy
from typing import Any

from docx.oxml.ns import qn

from document_model import caption_block
from table_semantics import table_caption_eligible


def _object_kind(block: dict[str, Any]) -> str | None:
    block_type = block.get("block_type")
    if block_type == "table":
        return "table"
    if block_type == "image" or block.get("source", {}).get("had_mixed_graphic"):
        return "figure"
    return None


def _caption_type(block: dict[str, Any]) -> str:
    caption_type = str(block.get("caption_type") or "unknown")
    if caption_type in {"table", "figure"}:
        return caption_type
    label = str(block.get("numbering", {}).get("label") or "").strip()
    if label.startswith("表"):
        return "table"
    if label.startswith("图"):
        return "figure"
    return "unknown"


def _compatible(caption_type: str, block: dict[str, Any]) -> bool:
    kind = _object_kind(block)
    return kind is not None and (caption_type == "unknown" or caption_type == kind)


def _unique_caption_id(existing: set[str], serial: int) -> tuple[str, int]:
    while True:
        candidate = f"caption-auto-{serial}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate, serial + 1
        serial += 1


def normalize_caption_placement(
    model: dict[str, Any],
    repairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Associate captions with objects and enforce table-above, figure-below order."""
    repairs = repairs if repairs is not None else []
    blocks = model.get("document", {}).get("blocks", [])
    if not blocks:
        model["caption_placement"] = {"associations": [], "issues": [], "moved": 0, "inserted": 0}
        return model

    for block in blocks:
        if _object_kind(block) is not None:
            block.pop("caption_id", None)

    object_by_id = {
        str(block.get("id")): block
        for block in blocks
        if block.get("id") and _object_kind(block) is not None
    }
    object_index = {
        str(block.get("id")): index
        for index, block in enumerate(blocks)
        if block.get("id") and _object_kind(block) is not None
    }
    assigned_object_ids: set[str] = set()
    blocked_auto_ids: set[str] = set()
    associations: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    associated_caption_ids: set[str] = set()
    caption_for_object: dict[str, dict[str, Any]] = {}
    inserted_count = 0

    for index, caption in enumerate(blocks):
        if caption.get("block_type") != "caption":
            continue
        caption_id = str(caption.get("id") or f"caption-at-{index}")
        caption_type = _caption_type(caption)
        candidates: list[dict[str, Any]] = []
        for candidate_index in (index - 1, index + 1):
            if 0 <= candidate_index < len(blocks):
                candidate = blocks[candidate_index]
                if _compatible(caption_type, candidate):
                    candidates.append(candidate)

        previous_object_id = str(caption.get("association", {}).get("object_id") or "")
        if previous_object_id and previous_object_id in object_by_id:
            previous = object_by_id[previous_object_id]
            if previous in candidates:
                candidates = [previous]

        candidate_ids = [str(candidate.get("id")) for candidate in candidates if candidate.get("id")]
        if len(candidates) != 1:
            blocked_auto_ids.update(candidate_ids)
            issue_type = "orphan_caption" if not candidates else "ambiguous_caption_association"
            issues.append({
                "type": issue_type,
                "caption_id": caption_id,
                "caption_type": caption_type,
                "candidate_object_ids": candidate_ids,
            })
            caption.pop("association", None)
            continue

        target = candidates[0]
        object_id = str(target.get("id"))
        if object_id in assigned_object_ids:
            blocked_auto_ids.add(object_id)
            issues.append({
                "type": "duplicate_caption_association",
                "caption_id": caption_id,
                "object_id": object_id,
            })
            caption.pop("association", None)
            continue

        object_kind = str(_object_kind(target))
        resolved_type = object_kind if caption_type == "unknown" else caption_type
        required_placement = "before" if object_kind == "table" else "after"
        source_placement = "before" if index < object_index[object_id] else "after"
        association = {
            "caption_id": caption_id,
            "object_id": object_id,
            "object_type": object_kind,
            "caption_type": resolved_type,
            "required_placement": required_placement,
            "source_placement": source_placement,
            "moved": source_placement != required_placement,
            "auto_generated": bool(caption.get("_auto_generated")),
        }
        caption["caption_type"] = resolved_type
        caption["association"] = deepcopy(association)
        target["caption_id"] = caption_id
        associations.append(association)
        assigned_object_ids.add(object_id)
        associated_caption_ids.add(caption_id)
        caption_for_object[object_id] = caption
        if association["moved"]:
            repairs.append({
                "type": "caption_moved_to_canonical_position",
                "caption_id": caption_id,
                "object_id": object_id,
                "from": source_placement,
                "to": required_placement,
            })

    serial = 1
    existing_ids = {str(block.get("id") or "") for block in blocks}
    for target in blocks:
        if target.get("block_type") != "table":
            continue
        object_id = str(target.get("id") or "")
        if (
            not object_id
            or object_id in assigned_object_ids
            or object_id in blocked_auto_ids
            or not table_caption_eligible(target.get("table_type"))
        ):
            continue
        caption_id, serial = _unique_caption_id(existing_ids, serial)
        generated = caption_block(caption_id, "", "table", label="表", raw_number=None)
        generated["_auto_generated"] = True
        association = {
            "caption_id": caption_id,
            "object_id": object_id,
            "object_type": "table",
            "caption_type": "table",
            "required_placement": "before",
            "source_placement": "generated",
            "moved": False,
            "auto_generated": True,
        }
        generated["association"] = deepcopy(association)
        target["caption_id"] = caption_id
        associations.append(association)
        assigned_object_ids.add(object_id)
        associated_caption_ids.add(caption_id)
        caption_for_object[object_id] = generated
        repairs.append({
            "type": "caption_auto_generated_for_table",
            "caption_id": caption_id,
            "object_id": object_id,
            "table_type": target.get("table_type", "data"),
        })
        inserted_count += 1

    rebuilt: list[dict[str, Any]] = []
    for block in blocks:
        block_id = str(block.get("id") or "")
        if block.get("block_type") == "caption" and block_id in associated_caption_ids:
            continue
        kind = _object_kind(block)
        caption = caption_for_object.get(block_id)
        if kind == "table" and caption is not None:
            rebuilt.append(caption)
        rebuilt.append(block)
        if kind == "figure" and caption is not None:
            rebuilt.append(caption)

    model["document"]["blocks"] = rebuilt
    model["caption_placement"] = {
        "associations": associations,
        "issues": issues,
        "moved": sum(1 for item in associations if item.get("moved")),
        "inserted": inserted_count,
        "passed": not issues,
    }
    return model


def audit_model_caption_placement(model: dict[str, Any] | None) -> dict[str, Any]:
    blocks = (model or {}).get("document", {}).get("blocks", [])
    indexes = {str(block.get("id")): index for index, block in enumerate(blocks) if block.get("id")}
    issues: list[dict[str, Any]] = []
    associations: list[dict[str, Any]] = []
    seen_objects: set[str] = set()

    for index, caption in enumerate(blocks):
        if caption.get("block_type") != "caption":
            continue
        caption_id = str(caption.get("id") or f"caption-at-{index}")
        association = caption.get("association") or {}
        object_id = str(association.get("object_id") or "")
        if not object_id or object_id not in indexes:
            issues.append({"type": "unassociated_caption", "caption_id": caption_id})
            continue
        if object_id in seen_objects:
            issues.append({"type": "duplicate_object_caption", "caption_id": caption_id, "object_id": object_id})
            continue
        seen_objects.add(object_id)
        target = blocks[indexes[object_id]]
        kind = _object_kind(target)
        expected_index = indexes[object_id] - 1 if kind == "table" else indexes[object_id] + 1
        if index != expected_index:
            issues.append({
                "type": "caption_position_violation",
                "caption_id": caption_id,
                "object_id": object_id,
                "object_type": kind,
                "caption_index": index,
                "object_index": indexes[object_id],
            })
        associations.append({
            "caption_id": caption_id,
            "object_id": object_id,
            "object_type": kind,
            "caption_index": index,
            "object_index": indexes[object_id],
        })

    return {"associations": associations, "issues": issues, "passed": not issues}


def _paragraph_has_graphics(paragraph) -> bool:
    element = paragraph._element
    return any(
        next(element.iter(qn(tag)), None) is not None
        for tag in ("w:drawing", "w:pict", "w:object")
    )


def _rendered_caption_type(paragraph) -> str | None:
    style_name = paragraph.style.name if paragraph.style is not None else ""
    xml = paragraph._element.xml
    if "SEQ Table" in xml:
        return "table"
    if "SEQ Figure" in xml:
        return "figure"
    if "caption" not in style_name.casefold() and "题注" not in style_name:
        return None
    text = paragraph.text.strip()
    if text.startswith("表"):
        return "table"
    if text.startswith("图"):
        return "figure"
    return "unknown"


def audit_rendered_caption_placement(doc) -> dict[str, Any]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    blocks: list[Any] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            blocks.append(Paragraph(child, doc))
        elif child.tag == qn("w:tbl"):
            blocks.append(Table(child, doc))

    issues: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, Paragraph):
            continue
        caption_type = _rendered_caption_type(block)
        if caption_type is None:
            continue
        record = {"block": index, "caption_type": caption_type, "text": block.text.strip()[:120]}
        captions.append(record)
        if caption_type == "table":
            valid = index + 1 < len(blocks) and isinstance(blocks[index + 1], Table)
        elif caption_type == "figure":
            valid = index > 0 and isinstance(blocks[index - 1], Paragraph) and _paragraph_has_graphics(blocks[index - 1])
        else:
            valid = False
        if not valid:
            issues.append({"type": "rendered_caption_position_violation", **record})

    return {"caption_count": len(captions), "captions": captions, "issues": issues, "passed": not issues}
