"""AST graph indexes and read-only semantic projections."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .validation import validate_ast


@dataclass(frozen=True)
class AstIndexes:
    """Indexes derived from the structural children graph for one revision."""

    revision: int
    parent_by_id: Mapping[str, str]
    owner_item_by_id: Mapping[str, str | None]
    section_by_id: Mapping[str, str | None]
    semantic_depth: Mapping[str, int]


def _nodes(ast: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, list[str]]]:
    document = ast["document"]
    return document["blocks"], document["containers"], document["children"]


def build_indexes(ast: Mapping[str, Any], *, validate: bool = True) -> AstIndexes:
    """Build all stage indexes in one linear walk of the children graph."""

    if validate:
        validate_ast(ast)
    blocks, containers, children = _nodes(ast)
    node_ids = set(blocks) | set(containers)
    parent: dict[str, str] = {}
    owner_item: dict[str, str | None] = {node_id: None for node_id in node_ids}
    section: dict[str, str | None] = {node_id: None for node_id in node_ids}
    depth: dict[str, int] = {}

    def walk(node_id: str, current_item: str | None, current_section: str | None, current_depth: int) -> None:
        depth[node_id] = current_depth
        owner_item[node_id] = current_item
        container = containers.get(node_id)
        next_item = node_id if container and container.get("kind") == "list_item" else current_item
        next_section = node_id if container and container.get("kind") in {"section", "appendix"} else current_section
        section[node_id] = next_section
        for child_id in children.get(node_id, []):
            parent[child_id] = node_id
            walk(child_id, next_item, next_section, current_depth + 1)

    walk(ast["document"]["root_id"], None, None, 0)
    return AstIndexes(
        revision=ast["revision"],
        parent_by_id=MappingProxyType(parent),
        owner_item_by_id=MappingProxyType(owner_item),
        section_by_id=MappingProxyType(section),
        semantic_depth=MappingProxyType(depth),
    )


def is_body_outline_heading(block: Mapping[str, Any]) -> bool:
    """Return whether a block takes part in the body outline contract.

    Appendix headings render through their own relative level and are owned by
    appendix semantics, so they stay out of the body outline.  The same predicate
    decides which headings the normalization derives a level for and which ones
    the model audit checks, so the two rules cannot drift apart.
    """

    if block.get("block_type") != "paragraph" or block.get("role") != "heading":
        return False
    return not (block.get("appendix_id") or block.get("appendix_role"))


def effective_outline_level(
    block: Mapping[str, Any],
    presentation: Mapping[str, Any] | None = None,
) -> int | None:
    """Return the outline level a heading renders at.

    ``block["level"]`` carries the source semantic level, which a source
    document may declare freely.  When the caller also has the presentation
    record, its derived ``outline_level`` is the level normalization mapped onto
    the WX outline contract, so the renderer writes it and the auditor checks
    it.  A block without either value carries no outline level.
    """

    if isinstance(presentation, Mapping):
        derived = presentation.get("outline_level")
        if isinstance(derived, int) and not isinstance(derived, bool):
            return derived
    level = block.get("level")
    if isinstance(level, int) and not isinstance(level, bool):
        return level
    return None


def inline_text_projection(inline_content: list[Mapping[str, Any]]) -> str:
    """Return the visible text projection of inline content."""

    visible: list[str] = []
    for item in inline_content:
        kind = item.get("kind")
        if kind == "text":
            visible.append(item.get("text", ""))
        elif kind == "break":
            visible.append("\n")
        elif kind == "tab":
            visible.append("\t")
        elif kind == "link":
            visible.append(inline_text_projection(item.get("children", [])))
        elif kind in {"field", "opaque_inline"}:
            # A retained field shows the result it last carried, so its cached
            # text is visible text like any other.
            cached = item.get("cached_result")
            if isinstance(cached, str):
                visible.append(cached)
    return "".join(visible)
