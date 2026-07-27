"""Resolve DOCX list hierarchy from numbering, layout, and parent context."""

from __future__ import annotations

from typing import Any

from list_style_mapping import normalize_wx_list_type


_UNORDERED_TYPES = frozenset({"dash", "bullet_dot"})
_INDENT_CLUSTER_TWIPS = 120


def _source_level(block: dict[str, Any]) -> tuple[int, bool]:
    numbering = block.get("source", {}).get("numbering", {})
    try:
        if "ilvl" in numbering:
            return max(0, int(numbering.get("ilvl") or 0)), True
        return max(0, int(block.get("level", 0) or 0)), False
    except (TypeError, ValueError):
        return 0, False


def _source_family(block: dict[str, Any]) -> str:
    numbering = block.get("source", {}).get("numbering", {})
    source_type = numbering.get("source_list_type")
    if source_type in _UNORDERED_TYPES:
        return "unordered"
    if block.get("list_type") in _UNORDERED_TYPES:
        return "unordered"
    return "ordered"


def _layout_levels(run: list[dict[str, Any]]) -> dict[int, int]:
    values = sorted({
        int(left)
        for block in run
        for left in [block.get("source", {}).get("layout", {}).get("left_twips")]
        if isinstance(left, int)
    })
    clusters: list[int] = []
    for value in values:
        if not clusters or value - clusters[-1] >= _INDENT_CLUSTER_TWIPS:
            clusters.append(value)
    return {
        value: index
        for index, cluster in enumerate(clusters)
        for value in values
        if abs(value - cluster) < _INDENT_CLUSTER_TWIPS
    }


def _proposed_level(
    block: dict[str, Any],
    layout_levels: dict[int, int],
    *,
    preserve_declared_levels: bool,
) -> tuple[int, list[str]]:
    source_level, has_explicit_source_level = _source_level(block)
    left = block.get("source", {}).get("layout", {}).get("left_twips")
    layout_level = layout_levels.get(left) if isinstance(left, int) else None
    evidence = ["source_ilvl"]
    level = source_level
    if layout_level is not None and not has_explicit_source_level:
        if preserve_declared_levels and source_level > layout_level:
            evidence = ["declared_ast_level"]
        else:
            level = layout_level
            evidence = ["layout_indent"]
    elif layout_level is not None and layout_level > level:
        level = layout_level
        evidence.append("layout_indent")
    elif layout_level is not None:
        evidence.append("layout_compatible")
    return level, evidence


def _resolve_run(
    run: list[dict[str, Any]],
    run_id: int,
    repairs: list[dict[str, Any]],
    *,
    preserve_declared_levels: bool,
) -> None:
    if not run:
        return
    layout_levels = _layout_levels(run)
    proposed = [
        _proposed_level(
            block,
            layout_levels,
            preserve_declared_levels=preserve_declared_levels,
        )
        for block in run
    ]
    minimum = min((level for level, _ in proposed), default=0)
    _, first_has_explicit_source_level = _source_level(run[0])
    if preserve_declared_levels and not first_has_explicit_source_level:
        minimum = 0
    if proposed and proposed[0][0] > 0 and first_has_explicit_source_level:
        minimum = min(minimum, proposed[0][0])

    active_parents: dict[int, str] = {}
    seen_sequences: set[tuple[str | None, int, str]] = set()
    previous_level: int | None = None
    for block, (raw_level, evidence) in zip(run, proposed):
        level = max(0, raw_level - minimum)
        if previous_level is None:
            if first_has_explicit_source_level or not preserve_declared_levels:
                level = 0
        elif level > previous_level + 1:
            level = previous_level + 1
            evidence.append("level_jump_clamped")

        family = _source_family(block)
        list_type = normalize_wx_list_type(
            "dash" if family == "unordered" else "lower_letter_paren",
            level,
        )
        parent_id = active_parents.get(level - 1) if level else None
        sequence_key = (parent_id, level, list_type)
        source_numbering = block.setdefault("source", {}).setdefault("numbering", {})
        restart = bool(source_numbering.get("restart")) or sequence_key not in seen_sequences
        seen_sequences.add(sequence_key)

        old_level = int(block.get("level") or 0)
        old_type = block.get("list_type")
        block["level"] = level
        block["list_type"] = list_type
        block["restart"] = restart
        block["list_run_id"] = f"list_run_{run_id}"
        if parent_id:
            block["parent_list_item_id"] = parent_id
        else:
            block.pop("parent_list_item_id", None)

        source_numbering["resolved_level"] = level
        source_numbering["hierarchy_evidence"] = evidence
        if list_type in _UNORDERED_TYPES:
            candidate = block.get("source", {}).get("unordered_candidate", {})
            marker = str(candidate.get("marker") or "")
            text = str(block.get("text") or "")
            if marker and text.lstrip().startswith(marker):
                block["text"] = text.lstrip()[len(marker):].lstrip()
                repairs.append({
                    "block_id": block.get("id"),
                    "type": "unordered_source_marker_removed",
                })
        if old_level != level or old_type != list_type:
            repairs.append({
                "block_id": block.get("id"),
                "type": "list_hierarchy_resolved",
                "from": {"level": old_level, "list_type": old_type},
                "to": {"level": level, "list_type": list_type},
                "evidence": evidence,
            })

        active_parents[level] = str(block.get("id") or "")
        for deeper_level in [key for key in active_parents if key > level]:
            active_parents.pop(deeper_level, None)
        previous_level = level


def resolve_list_hierarchy(
    model: dict[str, Any],
    repairs: list[dict[str, Any]],
    *,
    preserve_declared_levels: bool = False,
) -> None:
    """Resolve every consecutive list run into a stable AST hierarchy.

    ``ilvl`` remains high-confidence source evidence.  Indentation only raises
    a level when it demonstrates a deeper stable layout cluster, which supports
    Word documents that create child lists with a fresh ``numId`` and ``ilvl=0``.
    """
    run: list[dict[str, Any]] = []
    run_id = 0

    def flush() -> None:
        nonlocal run_id
        if run:
            run_id += 1
            _resolve_run(
                run,
                run_id,
                repairs,
                preserve_declared_levels=preserve_declared_levels,
            )
            run.clear()

    for block in model.get("document", {}).get("blocks", []):
        if block.get("block_type") == "list_item":
            run.append(block)
        else:
            flush()
    flush()
