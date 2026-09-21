"""Caption associations, placement and validated content overlays."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from text_utils import caption_parts

from ..contracts import overlay_property_scope
from ..digest import digest
from ..model import inline_text_projection
from ..validation import validate_ast
from ..validation import validate_source_facts


_RAW_NUMBER_RE = re.compile(r"^\s*(?:表|图)\s*(?P<number>(?:[A-Z]\s*[.．-]\s*)?\d+(?:\s*[.．-]\s*\d+)*)")


def _reading_order(ast: Mapping[str, Any]) -> list[str]:
    document = ast["document"]
    result: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            result.append(child_id)
            walk(child_id)

    walk(document["root_id"])
    return result


def _target_digest(ast: Mapping[str, Any], target_id: str) -> str:
    block = ast["document"]["blocks"].get(target_id)
    if block is None:
        raise ValueError(f"unknown caption target {target_id}")
    return digest({"node_id": target_id, "block": block})


def _association_digest(ast: Mapping[str, Any], target_id: str) -> str:
    associations = [
        association
        for association in ast["document"].get("associations", [])
        if association.get("kind") == "caption_target" and association.get("target_id") == target_id
    ]
    return digest(sorted(associations, key=lambda item: (str(item.get("source_id")), str(item.get("target_id")))))


def build_caption_overlay(ast: Mapping[str, Any], target_id: str, text: str, *, capability_version: str = "caption_gen@1.0") -> dict[str, Any]:
    """Build a validated overlay without changing the Source AST."""

    validate_ast(ast)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("caption text must be non-empty")
    target = ast["document"]["blocks"].get(target_id)
    if not target or target.get("block_type") not in {"table", "image"}:
        raise ValueError("caption target must be a table or image")
    return {
        "schema_version": "1.0",
        "kind": "caption_text",
        "target_node_id": target_id,
        "text": text,
        "target_content_sha256": _target_digest(ast, target_id),
        "association_sha256": _association_digest(ast, target_id),
        "association_revision": ast["revision"],
        "capability_version": capability_version,
        "validated": True,
    }


def invalidate_overlay(overlay: Mapping[str, Any], ast: Mapping[str, Any]) -> bool:
    """Return whether target content or association revision invalidates overlay."""

    target_id = overlay.get("target_node_id")
    try:
        digest_changed = overlay.get("target_content_sha256") != _target_digest(ast, str(target_id))
    except ValueError:
        return True
    association_changed = overlay.get("association_sha256") not in {None, _association_digest(ast, str(target_id))}
    return bool(digest_changed or association_changed)


def audit_caption_placement(ast: Mapping[str, Any]) -> dict[str, Any]:
    validate_ast(ast)
    document = ast["document"]
    order = _reading_order(ast)
    indexes = {node_id: index for index, node_id in enumerate(order)}
    associations: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for caption_id, caption in document["blocks"].items():
        if caption.get("role") != "caption":
            continue
        target_id = str(caption.get("target_id") or "")
        target = document["blocks"].get(target_id)
        if target is None:
            issues.append({"code": "caption_target_missing", "caption_id": caption_id})
            continue
        expected = "before" if target.get("block_type") == "table" else "after"
        actual = "before" if indexes.get(caption_id, -1) < indexes.get(target_id, -1) else "after"
        if actual != expected:
            issues.append({"code": "caption_placement_violation", "caption_id": caption_id, "target_id": target_id, "expected": expected, "actual": actual})
        associations.append({"caption_id": caption_id, "target_id": target_id, "caption_type": caption.get("caption_type"), "placement": actual})
    return {"passed": not issues, "issues": issues, "associations": associations}


def _caption_block(parent_id: str, index: int, caption_id: str, target_id: str, caption_type: str) -> dict[str, Any]:
    """Build the generated caption node for one eligible target.

    ``parent_id`` / ``index`` record where the target sits in the revision the
    candidate reads.  The proposal also carries ``anchor_node_id`` (the target)
    and ``anchor_position``, so the transaction resolves the caption's final
    parent and slot against the target's position when it applies the decision.
    A target that is moved in the same batch (a data table owned by a list item)
    therefore still gets its caption directly above it.
    """

    return {
        "operation": "create_node",
        "property_scope": ["children"],
        "value": {
            "node_id": caption_id,
            "parent_id": parent_id,
            "index": index,
            "anchor_node_id": target_id,
            "anchor_position": "after" if caption_type == "figure" else "before",
            "node": {
                "block_type": "paragraph",
                "role": "caption",
                "caption_type": caption_type,
                "target_id": target_id,
                "inline_content": [],
                "generated_by": "caption_semantics.sequence_caption",
                "generation_basis": [target_id],
            },
        },
    }


def _generated_caption_candidates(
    ast: Mapping[str, Any],
    target_id: str,
    caption_type: str,
    *,
    evidence_source: str,
    constraint: str,
) -> list[dict[str, Any]]:
    """Create one caption node and its caption_target association.

    A caption eligible target without an authored caption still needs a
    caption node so that sequence numbering and caption placement stay
    explicit in the Source AST.  The node is inserted immediately before a
    table and immediately after a figure.
    """

    document = ast["document"]
    blocks = document["blocks"]
    containers = document["containers"]
    children = document["children"]
    parent_id = next(
        (
            candidate_parent
            for candidate_parent, child_ids in children.items()
            if target_id in child_ids
        ),
        document["root_id"],
    )
    sibling_ids = list(children.get(parent_id, []))
    position = sibling_ids.index(target_id) if target_id in sibling_ids else len(sibling_ids)
    # A table caption sits above its table, a figure caption sits below.
    insert_at = position + 1 if caption_type == "figure" else position
    caption_id = f"native-caption-{target_id}"
    suffix = 2
    while caption_id in blocks or caption_id in containers:
        caption_id = f"native-caption-{target_id}-{suffix}"
        suffix += 1
    target_ref = blocks[target_id].get("source_ref")
    source_refs = [copy.deepcopy(dict(target_ref))] if isinstance(target_ref, Mapping) else []
    return [
        {
            "candidate_id": f"caption-node-{caption_id}",
            "module_id": "caption_semantics",
            "module_version": "1.0",
            "target_source_refs": source_refs,
            "target_node_ids": [caption_id],
            "proposal": _caption_block(
                parent_id,
                insert_at,
                caption_id,
                target_id,
                caption_type,
            ),
            "positive_evidence": [{"kind": "format_contract", "source": evidence_source}],
            "negative_evidence": [],
            "scope": {"stage": "relation"},
            "constraints": [{"kind": constraint}],
            "rationale": "caption node generated for a caption eligible target",
            "confidence": None,
        },
        {
            "candidate_id": f"caption-association-{caption_id}",
            "module_id": "caption_semantics",
            "module_version": "1.0",
            # The association binds the caption to its target, so the
            # precondition carries the target reference.  A generated caption
            # node has no source reference of its own.
            "target_source_refs": source_refs,
            "target_node_ids": [target_id],
            "proposal": {
                "operation": "update_association",
                "property_scope": ["association"],
                "value": {
                    "association": {
                        "kind": "caption_target",
                        "source_id": caption_id,
                        "target_id": target_id,
                    }
                },
            },
            "positive_evidence": [{"kind": "format_contract", "source": evidence_source}],
            "negative_evidence": [],
            "scope": {"stage": "relation"},
            "constraints": [{"kind": constraint}],
            "rationale": "caption target association derived from caption eligibility",
            "confidence": None,
        },
    ]


def _authored_caption_role_candidates(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Assert the caption role for captions materialized in the Source AST.

    Structure recognition proposes a default role for every paragraph, so an
    authored caption restates its role here.  Its ``caption_type`` and
    ``target_id`` come from the Source AST build.
    """

    result: list[dict[str, Any]] = []
    for node_id, block in ast["document"]["blocks"].items():
        if block.get("role") != "caption":
            continue
        if not block.get("target_id"):
            continue
        source_ref = block.get("source_ref")
        result.append({
            "candidate_id": f"caption-role-{node_id}",
            "module_id": "caption_semantics",
            "module_version": "1.0",
            "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
            "target_node_ids": [node_id],
            "proposal": {
                "operation": "update_role",
                "property_scope": ["role"],
                "value": {"role": "caption"},
            },
            "positive_evidence": [{"kind": "format_contract", "source": "authored_caption_role"}],
            "negative_evidence": [],
            "scope": {"stage": "relation"},
            "constraints": [{"kind": "preserve_caption_target"}],
            "rationale": "authored caption keeps its caption role against the default paragraph role",
            "confidence": None,
        })
    return result


def _without_images(inline_content: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep the authored caption inlines without images or its own numbering field.

    The caption owns its numbering, so the run that produced the source's label
    and number is dropped here and written back as this module's own sequence
    field at render time.  Any other retained field, a cross reference for
    example, stays with the caption text.
    """

    result: list[dict[str, Any]] = []
    for item in inline_content:
        kind = item.get("kind")
        if kind == "image":
            continue
        if kind == "field" and str(item.get("instruction") or "").upper().startswith("SEQ "):
            continue
        copied = copy.deepcopy(dict(item))
        if copied.get("kind") == "link":
            children = _without_images(copied.get("children", []))
            if not children:
                continue
            copied["children"] = children
        result.append(copied)
    return result


def _caption_target_block_id(
    ast: Mapping[str, Any],
    source_target_id: str,
    caption_type: str,
) -> str | None:
    blocks = ast["document"]["blocks"]
    if caption_type == "table" and source_target_id in blocks:
        return source_target_id
    image_id = f"{source_target_id}-image"
    if image_id in blocks and blocks[image_id].get("block_type") == "image":
        return image_id
    for node_id, block in blocks.items():
        if block.get("block_type") != "image":
            continue
        basis = block.get("generation_basis") or []
        if basis and str(basis[0]) == source_target_id:
            return str(node_id)
    return None


def _authored_caption_candidates(
    ast: Mapping[str, Any],
    facts: Mapping[str, Any],
    semantics: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Publish authored caption role, content, association and image moves.

    ``semantics`` is the published caption result owned by
    ``collect_source_caption_semantics``.  This function reads it instead of
    recomputing it, so one recognizer owns the canonical caption semantics.
    """

    blocks = ast["document"]["blocks"]
    root_children = list(ast["document"]["children"].get(ast["document"]["root_id"], []))
    result: list[dict[str, Any]] = []
    captioned_targets: set[str] = set()
    existing_associations = {
        (str(item.get("kind")), str(item.get("source_id")), str(item.get("target_id")))
        for item in ast["document"].get("associations", [])
        if isinstance(item, Mapping)
    }
    for caption in semantics.get("captions", []):
        if caption.get("status") != "associated":
            continue
        caption_id = str(caption.get("node_id") or "")
        source_target_id = str(caption.get("target_node_id") or "")
        caption_type = str(caption.get("caption_type") or "")
        if caption_id not in blocks or blocks[caption_id].get("block_type") != "paragraph":
            continue
        target_id = _caption_target_block_id(ast, source_target_id, caption_type)
        if target_id is None:
            continue
        captioned_targets.add(target_id)
        source_ref = facts["units"].get(caption_id, {}).get("source_ref")
        refs = [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else []
        result.append({
            "candidate_id": f"caption-role-{caption_id}",
            "module_id": "caption_semantics",
            "module_version": "1.0",
            "target_source_refs": refs,
            "target_node_ids": [caption_id],
            "proposal": {
                "operation": "update_role",
                "property_scope": ["role", "caption_type", "target_id"],
                "value": {"role": "caption", "caption_type": caption_type, "target_id": target_id},
            },
            "positive_evidence": [{"kind": "format_contract", "source": "authored_caption_role"}],
            "negative_evidence": [],
            "scope": {"stages": ["relation"]},
            "constraints": [{"kind": "preserve_caption_target"}],
            "rationale": "authored caption role and target are derived from SourceFacts caption semantics",
            "confidence": None,
        })

        source_unit = facts["units"].get(caption_id, {})
        source_inline = list(source_unit.get("inline_content") or []) if isinstance(source_unit, Mapping) else []
        image_ids = [
            str(node_id)
            for node_id, block in blocks.items()
            if block.get("block_type") == "image"
            and str((block.get("generation_basis") or [""])[0]) == caption_id
        ]
        wrapped = len(inline_text_projection(source_inline).strip()) >= 2 and (
            inline_text_projection(source_inline).strip()[0],
            inline_text_projection(source_inline).strip()[-1],
        ) in {("(", ")"), ("（", "）")}
        cleaned = (
            [{"kind": "text", "text": str(caption.get("text") or "").strip(), "marks": []}]
            if wrapped
            else _without_images(source_inline)
        )
        if not wrapped:
            from .paragraphs import canonicalize_docx_paragraph_inline_content

            cleaned = canonicalize_docx_paragraph_inline_content(
                cleaned,
                facts.get("evidence", {}).get(caption_id, {}),
                eligible=True,
                role="body",
                style=None,
            )
        if cleaned != blocks[caption_id].get("inline_content", []):
            result.append({
                "candidate_id": f"caption-content-{caption_id}",
                "module_id": "caption_semantics",
                "module_version": "1.0",
                "target_source_refs": refs,
                "target_node_ids": [caption_id],
                "proposal": {
                    "operation": "update_node_content",
                    "property_scope": ["inline_content"],
                    "value": {"inline_content": copy.deepcopy(cleaned)},
                },
                "positive_evidence": [{"kind": "format_contract", "source": "authored_caption_content"}],
                "negative_evidence": [],
                "scope": {"stages": ["relation"]},
                "constraints": [{"kind": "preserve_caption_text"}],
                "rationale": "caption content is normalized after caption ownership is established",
                "confidence": None,
            })

        if image_ids:
            caption_position = root_children.index(caption_id) if caption_id in root_children else len(root_children)
            for image_index, image_id in enumerate(image_ids):
                image_ref = blocks[image_id].get("source_ref")
                result.append({
                    "candidate_id": f"caption-image-mode-{image_id}",
                    "module_id": "caption_semantics",
                    "module_version": "1.0",
                    "target_source_refs": [copy.deepcopy(dict(image_ref))] if isinstance(image_ref, Mapping) else [],
                    "target_node_ids": [image_id],
                    "proposal": {
                        "operation": "update_node_semantics",
                        "property_scope": ["semantics", "materialization_mode"],
                        "value": {"semantic_role": "image", "materialization_mode": "standalone"},
                    },
                    "positive_evidence": [{"kind": "format_contract", "source": "mixed_figure_caption"}],
                    "negative_evidence": [],
                    "scope": {"stages": ["relation"]},
                    "constraints": [{"kind": "standalone_figure_image"}],
                    "rationale": "mixed figure caption extracts its image as a standalone source block",
                    "confidence": None,
                })
                result.append({
                    "candidate_id": f"caption-image-move-{image_id}",
                    "module_id": "caption_semantics",
                    "module_version": "1.0",
                    "target_source_refs": [copy.deepcopy(dict(image_ref))] if isinstance(image_ref, Mapping) else [],
                    "target_node_ids": [image_id],
                    "proposal": {
                        "operation": "move_node",
                        "property_scope": ["children"],
                        "value": {"new_parent_id": ast["document"]["root_id"], "index": caption_position + image_index},
                    },
                    "positive_evidence": [{"kind": "structural", "source": "mixed_figure_caption"}],
                    "negative_evidence": [],
                    "scope": {"stages": ["relation"]},
                    "constraints": [{"kind": "figure_before_caption"}],
                    "rationale": "mixed figure image is placed before its authored caption",
                    "confidence": None,
                })

        association = {"kind": "caption_target", "source_id": caption_id, "target_id": target_id}
        if (association["kind"], caption_id, target_id) not in existing_associations:
            result.append({
                "candidate_id": f"caption-association-{caption_id}",
                "module_id": "caption_semantics",
                "module_version": "1.0",
                "target_source_refs": refs,
                "target_node_ids": [caption_id],
                "proposal": {
                    "operation": "update_association",
                    "property_scope": ["association"],
                    "value": {"association": association},
                },
                "positive_evidence": [{"kind": "format_contract", "source": "authored_caption_target"}],
                "negative_evidence": [],
                "scope": {"stages": ["relation"]},
                "constraints": [{"kind": "caption_target_valid"}],
                "rationale": "caption target association is derived from SourceFacts",
                "confidence": None,
            })
    return result, captioned_targets


def _captioned_targets(ast: Mapping[str, Any]) -> set[str]:
    return {
        str(block.get("target_id"))
        for block in ast["document"]["blocks"].values()
        if block.get("role") == "caption" and block.get("target_id")
    }


def collect_caption_candidates(
    ast: Mapping[str, Any],
    facts: Mapping[str, Any] | None = None,
    *,
    caption_semantics: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    validate_ast(ast)
    if facts is None:
        result = _authored_caption_role_candidates(ast)
        captioned = _captioned_targets(ast)
    else:
        validate_source_facts(facts)
        if caption_semantics is None:
            from .tables import collect_source_table_semantics

            caption_semantics = collect_source_caption_semantics(
                facts,
                collect_source_table_semantics(facts),
            )
        result, captioned = _authored_caption_candidates(ast, facts, caption_semantics)
    for node_id, block in ast["document"]["blocks"].items():
        if block.get("block_type") == "table" and block.get("table_type") == "data":
            value = {"caption_type": "table", "eligible": True}
            result.append({"candidate_id": f"caption-target-{node_id}", "module_id": "caption_semantics", "module_version": "1.0", "target_source_refs": [copy.deepcopy(dict(block["source_ref"]))] if isinstance(block.get("source_ref"), Mapping) else [], "target_node_ids": [node_id], "proposal": {"operation": "create_overlay", "property_scope": overlay_property_scope(value), "value": value}, "positive_evidence": [{"kind": "format_contract", "source": "data_table_caption_eligibility"}], "negative_evidence": [], "scope": {"stage": "relation"}, "constraints": [{"kind": "caption_above_table"}], "rationale": "data table is caption eligible", "confidence": None})
            if node_id not in captioned:
                result.extend(_generated_caption_candidates(
                    ast,
                    node_id,
                    "table",
                    evidence_source="data_table_caption_eligibility",
                    constraint="caption_above_table",
                ))
        elif block.get("block_type") == "image":
            value = {"caption_type": "figure", "eligible": True}
            result.append({"candidate_id": f"caption-target-{node_id}", "module_id": "caption_semantics", "module_version": "1.0", "target_source_refs": [copy.deepcopy(dict(block["source_ref"]))] if isinstance(block.get("source_ref"), Mapping) else [], "target_node_ids": [node_id], "proposal": {"operation": "create_overlay", "property_scope": overlay_property_scope(value), "value": value}, "positive_evidence": [{"kind": "format_contract", "source": "image_caption_eligibility"}], "negative_evidence": [], "scope": {"stage": "relation"}, "constraints": [{"kind": "caption_below_figure"}], "rationale": "image is figure caption eligible", "confidence": None})
            if node_id not in captioned:
                result.extend(_generated_caption_candidates(
                    ast,
                    node_id,
                    "figure",
                    evidence_source="image_caption_eligibility",
                    constraint="caption_below_figure",
                ))
    return result


def _source_location(facts: Mapping[str, Any], node_id: str) -> dict[str, Any] | None:
    evidence = facts.get("evidence", {}).get(node_id, {})
    for key in ("source_position", "line_start"):
        value = evidence.get(key)
        if isinstance(value, int) and value >= 0:
            return {"kind": key, "value": value}
    return None


def _raw_number(text: str) -> str | None:
    match = _RAW_NUMBER_RE.match(text)
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group("number")).replace("．", ".")


def _source_caption_parts(text: str) -> tuple[str, str | None, str | None, str]:
    """Parse normal and parenthesized authored caption text."""

    stripped = text.strip()
    candidates = [stripped]
    if len(stripped) >= 2 and ((stripped[0], stripped[-1]) in {("(", ")"), ("（", "）")}):
        candidates.append(stripped[1:-1].strip())
    for candidate in candidates:
        caption_type, label, _, caption_text = caption_parts(candidate)
        if caption_type in {"table", "figure"}:
            return caption_type, label, _raw_number(candidate), caption_text
    return "unknown", None, None, stripped


def _has_terminal_sentence_punctuation(text: str) -> bool:
    return text.rstrip().endswith(("。", "；", ";", "，", ",", ":", "："))


def _is_centered_docx_paragraph(unit: Mapping[str, Any]) -> bool:
    paragraph_format = unit.get("source_paragraph_format")
    return isinstance(paragraph_format, Mapping) and paragraph_format.get("alignment") == "center"


def _is_list_scoped_docx_paragraph(evidence: Mapping[str, Any]) -> bool:
    """Keep list content out of standalone figure and table title recognition."""

    return bool(
        evidence.get("marker_kind")
        or evidence.get("visible_marker")
        or evidence.get("numbering_status") == "detected"
    )


def _is_unlabeled_figure_title(unit: Mapping[str, Any], text: str) -> bool:
    """Recognize an authored figure title that has no 图 label or number."""

    if not text or len(text) > 80 or _has_terminal_sentence_punctuation(text):
        return False
    if not text.endswith("图"):
        return False
    style = str(unit.get("style") or "").casefold()
    return _is_centered_docx_paragraph(unit) or style in {"caption", "题注"} or "图" in text


def _is_unlabeled_table_title(unit: Mapping[str, Any], text: str) -> bool:
    """Recognize a short table title immediately above a source table."""

    if not text or len(text) > 120 or _has_terminal_sentence_punctuation(text):
        return False
    style = str(unit.get("style") or "").casefold()
    if style.startswith("heading") or style.startswith("标题") or style in {"title", "主标题"}:
        return False
    return bool(
        _is_centered_docx_paragraph(unit)
        or style in {"caption", "题注", "表格标注"}
        or re.search(r"^(?:表|table)\s*\d", text, re.IGNORECASE)
        or re.search(r"表\s+", text)
        or text.endswith("表")
    )


def collect_source_caption_semantics(
    facts: Mapping[str, Any],
    table_semantics: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve authored caption roles and target relationships from SourceFacts."""

    validate_source_facts(facts)
    tables = {str(item.get("node_id")): item for item in table_semantics}
    root_children = list(facts["children"].get("root", []))
    objects: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    source_type = str(facts.get("source", {}).get("source_type") or "")
    for order, node_id in enumerate(root_children):
        unit = facts["units"].get(node_id)
        container = facts["containers"].get(node_id, {})
        if unit is not None and unit.get("kind") == "paragraph":
            evidence = facts.get("evidence", {}).get(node_id, {})
            inline = list(unit.get("inline_content") or [])
            image_count = sum(item.get("kind") == "image" for item in inline)
            if image_count:
                objects.append({
                    "node_id": node_id,
                    "kind": "figure",
                    "order": order,
                    "location": _source_location(facts, node_id),
                })
            source_text = inline_text_projection(inline).strip()
            caption_type, label, raw_number, text = _source_caption_parts(source_text)
            if caption_type in {"table", "figure"}:
                captions.append({
                    "node_id": node_id,
                    "order": order,
                    "location": _source_location(facts, node_id),
                    "caption_type": caption_type,
                    "label": label,
                    "raw_number": raw_number,
                    "text": text,
                })
            elif (
                source_type == "docx"
                and not _is_list_scoped_docx_paragraph(evidence)
                and _is_unlabeled_figure_title(unit, source_text)
            ):
                previous_id = root_children[order - 1] if order > 0 else None
                previous_unit = facts["units"].get(previous_id) if previous_id else None
                previous_inline = list(previous_unit.get("inline_content") or []) if isinstance(previous_unit, Mapping) else []
                if any(item.get("kind") == "image" for item in previous_inline):
                    captions.append({
                        "node_id": node_id,
                        "order": order,
                        "location": _source_location(facts, node_id),
                        "caption_type": "figure",
                        "label": "图",
                        "raw_number": None,
                        "text": source_text,
                    })
            elif (
                source_type == "docx"
                and not _is_list_scoped_docx_paragraph(evidence)
                and _is_unlabeled_table_title(unit, source_text)
            ):
                next_id = root_children[order + 1] if order + 1 < len(root_children) else None
                next_container = facts["containers"].get(next_id, {}) if next_id else {}
                next_semantic = tables.get(str(next_id), {})
                if next_container.get("kind") == "table" and next_semantic.get("caption_eligible"):
                    captions.append({
                        "node_id": node_id,
                        "order": order,
                        "location": _source_location(facts, node_id),
                        "caption_type": "table",
                        "label": "表",
                        "raw_number": None,
                        "text": source_text,
                    })
        elif container.get("kind") == "table":
            semantic = tables.get(str(node_id), {})
            if semantic.get("caption_eligible"):
                objects.append({
                    "node_id": node_id,
                    "kind": "table",
                    "order": order,
                    "location": _source_location(facts, node_id),
                })

    assigned: set[str] = set()
    issues: list[dict[str, Any]] = []
    for caption in captions:
        caption_type = str(caption["caption_type"])
        if caption_type == "table":
            candidates = [item for item in objects if item["kind"] == "table" and item["order"] > caption["order"]]
            candidates.sort(key=lambda item: item["order"])
            required = "before"
        else:
            mixed_figure = next(
                (
                    item for item in objects
                    if item["kind"] == "figure"
                    and item["node_id"] == caption["node_id"]
                ),
                None,
            )
            candidates = [mixed_figure] if mixed_figure is not None else [
                item for item in objects
                if item["kind"] == "figure" and item["order"] < caption["order"]
            ]
            candidates.sort(key=lambda item: item["order"], reverse=True)
            required = "after"
        target = next((item for item in candidates if item["node_id"] not in assigned), None)
        if target is None:
            caption.update({"target_location": None, "required_placement": required, "source_placement": None, "status": "orphan"})
            issues.append({"code": "caption_target_missing", "node_id": caption["node_id"], "caption_type": caption_type})
            continue
        assigned.add(str(target["node_id"]))
        caption.update({
            "target_node_id": target["node_id"],
            "target_location": copy.deepcopy(target["location"]),
            "required_placement": required,
            "source_placement": "before" if caption["order"] < target["order"] else "after",
            "status": "associated",
        })
    for caption in captions:
        caption.pop("order", None)
    # The inventory the captions were resolved against is part of the published
    # result: a consumer that checks whether an object carries a caption needs
    # the objects, not only the captions, or an object nobody captioned and a
    # measurement it never made look the same.
    return {"captions": captions, "issues": issues, "objects": objects}


def recognize_caption_semantics(
    facts: Mapping[str, Any],
    source_ast: Mapping[str, Any],
    table_semantics_result: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Own the whole caption concept: facts result plus AST candidates."""

    result = collect_source_caption_semantics(facts, table_semantics_result)
    candidates = collect_caption_candidates(
        source_ast,
        facts,
        caption_semantics=result,
    )
    return {"result": result, "candidates": candidates}


__all__ = ["audit_caption_placement", "build_caption_overlay", "collect_caption_candidates", "collect_source_caption_semantics", "invalidate_overlay", "recognize_caption_semantics"]
