"""Migration between the legacy document model and AST 2.0."""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .model import inline_text_projection
from .validation import validate_ast


class MigrationError(ValueError):
    """Raised when a representation cannot be projected without loss."""


@dataclass(frozen=True)
class MigrationResult:
    ast: dict[str, Any]
    diagnostics: list[dict[str, Any]]


_ZERO_DIGEST = "sha256:" + "0" * 64
_MARKER_RE = re.compile(r"^\s*(?P<marker>(?:[A-Za-z]+|\d+)[.)]|[-*•])\s+")


def _source_ref(block: Mapping[str, Any], *, suffix: str = "") -> dict[str, Any]:
    source = block.get("source") or {}
    supplied = source.get("source_ref")
    if isinstance(supplied, Mapping) and supplied.get("source_id") and supplied.get("part") and supplied.get("content_sha256"):
        return copy.deepcopy(dict(supplied))
    source_id = str(source.get("source_id") or source.get("path") or "legacy-source")
    part = str(source.get("part") or source.get("path") or "legacy-document")
    digest = source.get("content_sha256")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        digest = _ZERO_DIGEST
    position = source.get("source_position")
    if isinstance(position, int) and position >= 0:
        text = str(block.get("text") or block.get("title") or "")
        span = {"start": position, "end": position + len(text), "unit": "unicode_code_point"}
    else:
        span = {"start": 0, "end": 0, "unit": "unicode_code_point"}
    if suffix:
        part = f"{part}#{suffix}"
    return {
        "source_id": source_id,
        "part": part,
        "content_sha256": digest,
        "span": span,
    }


def _inline_content(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    runs = block.get("inline_runs")
    if isinstance(runs, list):
        result: list[dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, Mapping):
                raise MigrationError("legacy inline run is not an object")
            text = str(run.get("text") or "")
            marks = [mark for mark in run.get("marks", []) if mark in {"strong", "emphasis", "code"}]
            if run.get("href"):
                result.append(
                    {
                        "kind": "link",
                        "target": str(run["href"]),
                        "children": [{"kind": "text", "text": text, "marks": marks}],
                    }
                )
            else:
                result.append({"kind": "text", "text": text, "marks": marks})
        return result
    return [{"kind": "text", "text": str(block.get("text") or ""), "marks": []}]


def _paragraph(
    block: Mapping[str, Any],
    *,
    node_id: str,
    role: str = "body",
    level: int | None = None,
    source_suffix: str = "",
) -> dict[str, Any]:
    inline = _inline_content(block)
    paragraph: dict[str, Any] = {
        "block_type": "paragraph",
        "role": role if role in {"title", "body", "heading", "caption", "note", "numbered_note", "unknown"} else "body",
        "inline_content": inline,
        "source_ref": _source_ref(block, suffix=source_suffix),
    }
    if level is not None and paragraph["role"] == "heading":
        paragraph["level"] = max(1, min(6, int(level)))
    return paragraph


def _text_marker(block: Mapping[str, Any], ordinal: int) -> str:
    marker = block.get("source_marker") or block.get("marker")
    if marker:
        return str(marker)
    match = _MARKER_RE.match(str(block.get("text") or ""))
    if match:
        return match.group("marker")
    list_type = str(block.get("list_type") or "")
    if list_type in {"lower_letter_paren", "letter"}:
        return f"{chr(96 + max(1, ordinal))})"
    return f"{ordinal}."


def _list_kind(block: Mapping[str, Any]) -> str:
    list_type = str(block.get("list_type") or "")
    return "unordered" if list_type in {"bullet_dot", "dash", "bullet", "unordered"} else "ordered"


def _appendix_classification(
    value: Any,
    *,
    node_id: str,
    diagnostics: list[dict[str, Any]],
) -> tuple[str, str | None]:
    label = str(value).strip() if value is not None else ""
    mapping = {
        "规范性": "normative",
        "normative": "normative",
        "资料性": "informative",
        "informative": "informative",
        "unknown": "unknown",
    }
    if not label:
        return "unknown", None
    classification = mapping.get(label)
    if classification is not None:
        return classification, label
    diagnostics.append({
        "code": "legacy_appendix_classification_unmapped",
        "severity": "warning",
        "message": "legacy appendix classification is outside AST 2.0 enum",
        "node_id": node_id,
        "value": label,
    })
    return "unknown", label


def _legacy_block_id(block: Mapping[str, Any], index: int) -> str:
    value = str(block.get("id") or f"legacy-{index}")
    return value or f"legacy-{index}"


def migrate_legacy_model(model: Mapping[str, Any]) -> MigrationResult:
    """Migrate legacy blocks while retaining only relationships supported by evidence."""

    if model.get("schema_version") != "1.0":
        raise MigrationError("legacy model schema_version must be 1.0")
    document = model.get("document")
    if not isinstance(document, Mapping) or not isinstance(document.get("blocks"), list):
        raise MigrationError("legacy model requires document.blocks list")

    blocks: dict[str, dict[str, Any]] = {}
    containers: dict[str, dict[str, Any]] = {"root": {"kind": "document"}}
    children: dict[str, list[str]] = {"root": []}
    resources: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    active_lists: dict[int, str] = {}
    active_items: dict[int, str] = {}
    sequence_ordinals: dict[str, int] = {}
    appendix_containers: dict[str, str] = {}

    def add_child(parent_id: str, child_id: str) -> None:
        children.setdefault(parent_id, []).append(child_id)

    for index, legacy_block in enumerate(document["blocks"]):
        if not isinstance(legacy_block, Mapping):
            raise MigrationError(f"legacy block {index} is not an object")
        block_id = _legacy_block_id(legacy_block, index)
        block_type = str(legacy_block.get("block_type") or "body")
        legacy_context = legacy_block.get("context") or {}
        context_appendix_id = str(
            legacy_context.get("appendix_id")
            or legacy_block.get("appendix_id")
            or ""
        )
        appendix_parent_id = (
            appendix_containers.get(context_appendix_id)
            if legacy_context.get("scope") == "appendix"
            else None
        )
        if block_type == "list_item":
            level = max(0, int(legacy_block.get("level") or 0))
            list_type = str(legacy_block.get("list_type") or "decimal_paren")
            list_kind = _list_kind(legacy_block)
            while active_lists and max(active_lists) > level:
                active_lists.pop(max(active_lists))
                active_items.pop(max(active_items), None)
            parent_id = active_items.get(level - 1) if level > 0 else (appendix_parent_id or "root")
            unresolved = level > 0 and parent_id is None
            if unresolved:
                diagnostics.append(
                    {
                        "code": "unresolved_list_parent",
                        "severity": "warning",
                        "message": f"legacy list item {block_id} has no evidenced parent at level {level}",
                        "node_id": block_id,
                    }
                )
                parent_id = "root"
                active_lists.clear()
                active_items.clear()
            list_id = active_lists.get(level)
            if list_id is None or list_id not in containers or containers[list_id].get("list_kind") != list_kind:
                list_id = f"list-{block_id}"
                sequence_id = f"legacy-sequence-{list_type}-{level}"
                containers[list_id] = {
                    "kind": "list",
                    "list_kind": list_kind,
                    "source_start": max(1, int(legacy_block.get("source_start") or 1)),
                    "source_sequence_id": sequence_id,
                }
                children[list_id] = []
                add_child(parent_id, list_id)
                active_lists[level] = list_id
                for old_level in list(active_lists):
                    if old_level > level:
                        active_lists.pop(old_level, None)
                        active_items.pop(old_level, None)
            sequence_id = containers[list_id]["source_sequence_id"]
            ordinal = int(legacy_block.get("source_ordinal") or sequence_ordinals.get(sequence_id, 0) + 1)
            sequence_ordinals[sequence_id] = ordinal
            item_id = f"item-{block_id}"
            containers[item_id] = {
                "kind": "list_item",
                "legacy_id": block_id,
                "source_marker": _text_marker(legacy_block, ordinal),
                "source_ordinal": max(1, ordinal),
                "source_sequence_id": sequence_id,
                "source_ref": _source_ref(legacy_block),
            }
            children[item_id] = []
            add_child(list_id, item_id)
            paragraph_id = f"{block_id}-paragraph"
            blocks[paragraph_id] = _paragraph(legacy_block, node_id=paragraph_id, source_suffix="paragraph")
            add_child(item_id, paragraph_id)
            active_items[level] = item_id
            for old_level in list(active_items):
                if old_level > level:
                    active_items.pop(old_level, None)
            continue

        role = legacy_block.get("role")
        if block_type == "heading":
            paragraph_role = "title" if role == "title" or int(legacy_block.get("level") or 0) <= 0 else "heading"
            paragraph_level = None if paragraph_role == "title" else legacy_block.get("level") or 1
            blocks[block_id] = _paragraph(legacy_block, node_id=block_id, role=paragraph_role, level=paragraph_level)
        elif block_type == "caption":
            association = legacy_block.get("association") or {}
            target_id = str(
                legacy_block.get("target_id")
                or legacy_block.get("target_block_id")
                or association.get("object_id")
                or ""
            )
            caption_type = str(legacy_block.get("caption_type") or "figure")
            if target_id and caption_type in {"figure", "table"}:
                blocks[block_id] = _paragraph(legacy_block, node_id=block_id, role="caption")
                blocks[block_id]["caption_type"] = caption_type
                blocks[block_id]["target_id"] = target_id
                if legacy_block.get("_auto_generated"):
                    blocks[block_id].pop("source_ref", None)
                    blocks[block_id]["generated_by"] = "legacy_normalization_adapter"
                    blocks[block_id]["generation_basis"] = [target_id]
            else:
                blocks[block_id] = _paragraph(legacy_block, node_id=block_id, role="body")
        elif block_type == "image":
            resource_id = str(legacy_block.get("asset_id") or f"resource-{block_id}")
            source_ref = _source_ref(legacy_block)
            containers.setdefault("root", {"kind": "document"})
            document_resource = {
                "kind": "image",
                "content_sha256": source_ref["content_sha256"],
                "source_ref": source_ref,
            }
            resources[resource_id] = document_resource
            blocks[block_id] = {
                "block_type": "image",
                "resource_id": resource_id,
                "alt_text": str(legacy_block.get("alt_text") or "image"),
                "source_ref": source_ref,
            }
        elif block_type == "table":
            header_rows = max(0, int(legacy_block.get("header_rows") or 0))
            blocks[block_id] = {
                "block_type": "table",
                "table_type": str(legacy_block.get("table_type") or "unknown"),
                "header_rows": header_rows,
                "source_ref": _source_ref(legacy_block),
            }
            children[block_id] = []
            for row_index, row in enumerate(legacy_block.get("rows") or []):
                row_id = f"{block_id}-row-{row_index}"
                containers[row_id] = {"kind": "table_row", "source_ref": _source_ref(legacy_block, suffix=row_id)}
                children[row_id] = []
                add_child(block_id, row_id)
                for col_index, cell in enumerate(row or []):
                    cell_id = f"{row_id}-cell-{col_index}"
                    containers[cell_id] = {
                        "kind": "table_cell",
                        "row": row_index,
                        "col": col_index,
                        "row_span": max(1, int((cell or {}).get("row_span") or 1)),
                        "col_span": max(1, int((cell or {}).get("col_span") or 1)),
                        "cell_role": str((cell or {}).get("cell_role") or ("header" if row_index < header_rows else "body")),
                        "source_ref": _source_ref(legacy_block, suffix=cell_id),
                    }
                    children[cell_id] = []
                    add_child(row_id, cell_id)
                    cell = cell if isinstance(cell, Mapping) else {}
                    cell_block_id = f"{cell_id}-paragraph"
                    blocks[cell_block_id] = _paragraph(cell, node_id=cell_block_id, source_suffix=cell_id)
                    add_child(cell_id, cell_block_id)
        elif block_type == "appendix":
            title_id = f"{block_id}-title"
            blocks[title_id] = _paragraph(
                {**legacy_block, "text": legacy_block.get("title") or legacy_block.get("text") or "附录"},
                node_id=title_id,
                role="heading",
                level=1,
                source_suffix="title",
            )
            appendix_id = str(legacy_block.get("appendix_id") or block_id)
            source_classification, source_classification_label = _appendix_classification(
                legacy_block.get("classification"),
                node_id=block_id,
                diagnostics=diagnostics,
            )
            appendix_container = {
                "kind": "appendix",
                "source_classification": source_classification,
                "title_ref": title_id,
                "source_ref": _source_ref(legacy_block),
            }
            if source_classification_label is not None:
                appendix_container["source_classification_label"] = source_classification_label
            containers[block_id] = appendix_container
            children[block_id] = [title_id]
            add_child("root", block_id)
            appendix_containers[appendix_id] = block_id
            active_lists.clear()
            active_items.clear()
            continue
        elif block_type == "unknown":
            diagnostic_id = f"legacy-unknown-{block_id}"
            diagnostics.append({"code": diagnostic_id, "severity": "warning", "message": str(legacy_block.get("description") or "unknown legacy block")})
            blocks[block_id] = {
                "block_type": "opaque",
                "diagnostic_ids": [diagnostic_id],
                "source_ref": _source_ref(legacy_block),
            }
        else:
            blocks[block_id] = _paragraph(legacy_block, node_id=block_id, role=role or "body")
        add_child(appendix_parent_id or "root", block_id)
        active_lists.clear()
        active_items.clear()

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": blocks,
            "containers": containers,
            "children": children,
            "associations": [],
            "resources": resources,
        },
    }
    _repair_caption_associations(ast)
    validate_ast(ast)
    return MigrationResult(ast=ast, diagnostics=diagnostics)


def _repair_caption_associations(ast: dict[str, Any]) -> None:
    document = ast["document"]
    blocks = document["blocks"]
    for block_id, block in blocks.items():
        if block.get("role") != "caption" or block.get("target_id") not in blocks:
            continue
        target_id = block["target_id"]
        expected = "table" if block.get("caption_type") == "table" else "image"
        if blocks[target_id].get("block_type") == expected:
            document["associations"].append({"kind": "caption_target", "source_id": block_id, "target_id": target_id})


def _legacy_inline_runs(inline_content: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for item in inline_content:
        kind = item.get("kind")
        if kind == "text":
            runs.append({"text": item.get("text", ""), "marks": list(item.get("marks", []))})
        elif kind == "break":
            runs.append({"text": "\n", "marks": []})
        elif kind == "tab":
            runs.append({"text": "\t", "marks": []})
        elif kind == "link":
            for child in _legacy_inline_runs(item.get("children", [])):
                child["href"] = item.get("target", "")
                runs.append(child)
        elif kind in {"field", "bookmark", "math", "opaque_inline"}:
            raise MigrationError(f"{kind} inline node cannot be represented by the legacy view")
        elif kind == "image":
            raise MigrationError("inline image cannot be represented by the legacy view")
    return runs


def _legacy_record(block_id: str, block: Mapping[str, Any], *, owner_item: str | None = None) -> dict[str, Any]:
    inline = block.get("inline_content", [])
    runs = _legacy_inline_runs(inline)
    record: dict[str, Any] = {
        "id": block_id,
        "block_type": "heading" if block.get("role") in {"title", "heading"} else "body",
        "text": inline_text_projection(inline),
        "inline_runs": runs,
    }
    if block.get("role") == "heading":
        record["level"] = block.get("level", 1)
    elif block.get("role") == "title":
        record["role"] = "title"
        record["level"] = 0
    if owner_item:
        record["block_type"] = "list_item"
        record["owner_item_id"] = owner_item
    return record


def _legacy_table_record(block_id: str, ast: Mapping[str, Any], table_id: str) -> dict[str, Any]:
    document = ast["document"]
    rows: list[list[dict[str, Any]]] = []
    for row_id in document["children"].get(table_id, []):
        row: list[dict[str, Any]] = []
        for cell_id in document["children"].get(row_id, []):
            paragraph_ids = [child for child in document["children"].get(cell_id, []) if child in document["blocks"]]
            if len(paragraph_ids) > 1:
                raise MigrationError(f"cell {cell_id} has multiple paragraphs in the legacy view")
            paragraph = document["blocks"].get(paragraph_ids[0], {}) if paragraph_ids else {"inline_content": []}
            inline = paragraph.get("inline_content", [])
            row.append({
                "text": inline_text_projection(inline),
                "inline_runs": _legacy_inline_runs(inline),
                "cell_role": document["containers"][cell_id].get("cell_role", "body"),
            })
        rows.append(row)
    return {
        "id": block_id,
        "block_type": "table",
        "table_type": document["blocks"][table_id].get("table_type", "unknown"),
        "header_rows": document["blocks"][table_id].get("header_rows", 0),
        "rows": rows,
    }


def legacy_view(ast: Mapping[str, Any]) -> Mapping[str, Any]:
    """Project AST 2.0 to an immutable legacy read view."""

    validate_ast(ast)
    document = ast["document"]
    blocks = document["blocks"]
    containers = document["containers"]
    children = document["children"]
    records: dict[str, dict[str, Any]] = {}

    def walk(node_id: str, owner_item: str | None = None) -> None:
        if node_id in blocks:
            block = blocks[node_id]
            block_type = block.get("block_type")
            if block_type == "table":
                records[node_id] = _legacy_table_record(node_id, ast, node_id)
            elif block_type == "image":
                records[node_id] = {
                    "id": node_id,
                    "block_type": "image",
                    "asset_id": block.get("resource_id"),
                    "alt_text": block.get("alt_text", ""),
                }
            elif block_type == "opaque":
                raise MigrationError(f"opaque block {node_id} cannot be represented by the legacy view")
            else:
                records[node_id] = _legacy_record(node_id, block, owner_item=owner_item)
        container = containers.get(node_id)
        if container and container.get("kind") == "list_item":
            paragraph_ids = [child for child in children.get(node_id, []) if child in blocks and blocks[child].get("block_type") == "paragraph"]
            if len(paragraph_ids) > 1:
                raise MigrationError(f"list item {node_id} has multiple paragraphs in the legacy view")
            paragraph_id = paragraph_ids[0] if paragraph_ids else None
            if paragraph_id:
                paragraph = blocks[paragraph_id]
                records.pop(paragraph_id, None)
                record = _legacy_record(str(container.get("legacy_id") or node_id), paragraph)
                record.update(
                    {
                        "block_type": "list_item",
                        "level": 0,
                        "marker": container.get("source_marker"),
                        "source_ordinal": container.get("source_ordinal"),
                    }
                )
                records[record["id"]] = record
        next_owner = node_id if container and container.get("kind") == "list_item" else owner_item
        for child_id in children.get(node_id, []):
            if container and container.get("kind") == "list_item" and child_id in blocks and child_id in {paragraph_id}:
                continue
            walk(child_id, next_owner)

    walk(document["root_id"])
    def freeze(value: Any) -> Any:
        if isinstance(value, Mapping):
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        return value

    frozen_records = {key: freeze(value) for key, value in records.items()}
    return MappingProxyType({"blocks": MappingProxyType(frozen_records), "revision": ast["revision"]})
