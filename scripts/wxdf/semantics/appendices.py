"""Appendix scope and relative heading semantics for AST 2.0."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..model import consume_leading_text, inline_text_projection
from ..validation import validate_ast, validate_source_facts
from appendix_semantics import (
    appendix_heading_level,
    appendix_role_from_style,
    parse_appendix_marker,
    parse_appendix_title,
)


_TITLE_RE = re.compile(r"^附\s*录(?:\s+([A-Z]))?(?:[：:]\s*)?(.*)$", re.IGNORECASE)
_VISIBLE_RE = re.compile(r"^([A-Z])(?:[.．]\d+){1,3}\s*(.*)$", re.IGNORECASE)
_MAIN_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)+\s+")
_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)
_CLASSIFICATION_ONLY_RE = re.compile(r"^[（(](规范性|资料性)[)）]$")


def _text(block: Mapping[str, Any]) -> str:
    if "text" in block:
        return str(block.get("text") or "")
    return inline_text_projection(block.get("inline_content", []))


def collect_appendix_candidates(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_ast(ast)
    document = ast["document"]
    appendix_containers = {node_id: node for node_id, node in document["containers"].items() if node.get("kind") == "appendix"}
    result: list[dict[str, Any]] = []
    for container_id, container in appendix_containers.items():
        title_id = container.get("title_ref")
        title = document["blocks"].get(title_id, {})
        text = _text(title)
        marker = _TITLE_RE.match(text.strip())
        result.append({"candidate_id": f"appendix-{container_id}", "container_id": container_id, "title_id": title_id, "appendix_id": (marker.group(1).upper() if marker and marker.group(1) else None), "classification": container.get("source_classification", "unknown"), "confidence": 1.0, "accepted": bool(title_id in document["blocks"]), "evidence": ["appendix_container", "title_ref"], "negative_evidence": []})
    for block_id, block in document["blocks"].items():
        text = _text(block).strip()
        marker = _TITLE_RE.match(text)
        if not marker or any(item.get("title_id") == block_id for item in result):
            continue
        negative = ["nested_main_numbered_heading"] if _MAIN_NUMBER_RE.match(text) and (block.get("level") or 0) >= 2 else []
        result.append({"candidate_id": f"appendix-title-{block_id}", "container_id": None, "title_id": block_id, "appendix_id": marker.group(1).upper() if marker.group(1) else None, "classification": "unknown", "confidence": 0.7 if not negative else 0.2, "accepted": not negative, "evidence": ["explicit_appendix_title"], "negative_evidence": negative})
    return result


def _reading_order(ast: Mapping[str, Any]) -> list[str]:
    document = ast["document"]
    result: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in document["children"].get(node_id, []):
            result.append(child_id)
            walk(child_id)

    walk(document["root_id"])
    return result


def annotate_appendix_ranges(ast: Mapping[str, Any]) -> dict[str, Any]:
    """Return a derived AST view with appendix ranges and relative levels."""

    validate_ast(ast)
    derived = copy.deepcopy(dict(ast))
    document = derived["document"]
    candidates = [item for item in collect_appendix_candidates(ast) if item.get("accepted")]
    order = _reading_order(derived)
    ranges: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates, 1):
        container_id = candidate.get("container_id")
        if container_id:
            node_ids = [node_id for node_id in order if node_id == container_id or node_id in _descendants(derived, container_id)]
        else:
            start = order.index(str(candidate["title_id"])) if candidate["title_id"] in order else 0
            node_ids = order[start:]
        appendix_id = str(candidate.get("appendix_id") or chr(64 + ordinal))
        heading_ids = [node_id for node_id in node_ids if node_id != candidate.get("title_id") and node_id in document["blocks"] and document["blocks"][node_id].get("role") == "heading"]
        source_levels = [int(document["blocks"][node_id].get("level") or 1) for node_id in heading_ids]
        base = min(source_levels) if source_levels else 1
        heading_levels = {node_id: max(1, min(3, int(document["blocks"][node_id].get("level") or base) - base + 1)) for node_id in heading_ids}
        for node_id, level in heading_levels.items():
            document["blocks"][node_id].setdefault("derived_appendix", {})["appendix_id"] = appendix_id
            document["blocks"][node_id]["derived_appendix"]["relative_level"] = level
        start_index = min((order.index(node_id) for node_id in node_ids if node_id in order), default=0)
        end_index = max((order.index(node_id) for node_id in node_ids if node_id in order), default=start_index)
        ranges.append({"appendix_id": appendix_id, "container_id": container_id, "title_id": candidate.get("title_id"), "node_ids": node_ids, "heading_levels": heading_levels, "source_number_preserved": True, "page_break_before": True, "start_order": start_index, "end_order": end_index, "negative_evidence": list(candidate.get("negative_evidence", []))})
    return {"schema_version": "1.0", "source_revision": ast["revision"], "source_ast": derived, "candidates": candidates, "ranges": ranges, "diagnostics": []}


def _descendants(ast: Mapping[str, Any], node_id: str) -> set[str]:
    children = ast["document"]["children"]
    result: set[str] = set()
    stack = list(children.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def _source_location(facts: Mapping[str, Any], node_id: str) -> dict[str, Any] | None:
    evidence = facts.get("evidence", {}).get(node_id, {})
    for key in ("source_position", "line_start"):
        value = evidence.get(key)
        if isinstance(value, int) and value >= 0:
            return {"kind": key, "value": value}
    return None


def _source_heading_level(unit: Mapping[str, Any]) -> int | None:
    style = str(unit.get("style") or "")
    appendix_level = appendix_heading_level(style)
    if appendix_level is not None:
        return appendix_level
    match = _HEADING_STYLE_RE.match(style.strip())
    if match is not None:
        return int(match.group(1))
    level = unit.get("level")
    return int(level) if isinstance(level, int) and level > 0 else None


def _visible_appendix_heading(text: str, appendix_id: str) -> tuple[int, str] | None:
    match = _VISIBLE_RE.match(text.strip())
    if match is None or match.group(1).upper() != appendix_id.upper():
        return None
    prefix = text.strip().split(maxsplit=1)[0].replace("．", ".")
    level = prefix.count(".")
    return max(1, min(3, level)), match.group(2).strip()


def collect_source_appendix_semantics(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve appendix starts, ranges and relative headings from SourceFacts."""

    validate_source_facts(facts)
    entries: list[dict[str, Any]] = []
    root_children = list(facts["children"].get("root", []))
    candidates: list[dict[str, Any]] = []
    for order, node_id in enumerate(facts["children"].get("root", [])):
        unit = facts["units"].get(node_id)
        container = facts["containers"].get(node_id, {})
        if unit is not None and unit.get("kind") == "paragraph":
            text = inline_text_projection(unit.get("inline_content", [])).strip()
            has_image = any(item.get("kind") == "image" for item in unit.get("inline_content", []))
            if text or has_image:
                entries.append({
                    "node_id": node_id,
                    "order": order,
                    "location": _source_location(facts, node_id),
                    "kind": "image" if has_image and not text else "paragraph",
                    "text": text,
                    "style": unit.get("style"),
                    "heading_level": _source_heading_level(unit),
                })
            direct_marker = parse_appendix_marker(text)
            numbered_match = _MAIN_NUMBER_RE.match(text)
            nested_marker = parse_appendix_marker(text[numbered_match.end():]) if numbered_match else None
            style_role = appendix_role_from_style(unit.get("style"))
            heading_like = (
                unit.get("role") == "heading"
                or _source_heading_level(unit) is not None
                or style_role == "appendix_title"
            )
            direct_title = str((direct_marker or {}).get("title") or "")
            direct_marker_allowed = direct_marker is not None and (
                heading_like
                or not direct_title
                or _CLASSIFICATION_ONLY_RE.fullmatch(direct_title) is not None
            )
            if not direct_marker_allowed and nested_marker is None and style_role != "appendix_title":
                continue
            source_evidence = facts.get("evidence", {}).get(node_id, {})
            nested_numbering = (
                source_evidence.get("num_fmt") == "decimal"
                and str(source_evidence.get("lvl_text") or "").count("%") >= 2
                and (_source_heading_level(unit) or 0) >= 2
            )
            negative = ["nested_main_numbered_heading"] if nested_marker is not None or nested_numbering else []
            marker = direct_marker or nested_marker
            accepted = not negative
            candidates.append({
                "candidate_id": f"appendix-source-{node_id}",
                "node_id": node_id,
                "location": _source_location(facts, node_id),
                "order": order,
                "appendix_id": (marker or {}).get("appendix_id"),
                "accepted": accepted,
                "confidence": 1.0 if style_role == "appendix_title" or direct_marker is not None else 0.2,
                "evidence": ["dedicated_appendix_semantics"] if style_role == "appendix_title" else ["explicit_appendix_title"],
                "negative_evidence": negative,
                "raw_text": text,
            })
        elif container.get("kind") == "table":
            entries.append({
                "node_id": node_id,
                "order": order,
                "location": _source_location(facts, node_id),
                "kind": "table",
                "text": "",
                "style": None,
                "heading_level": None,
            })

    accepted = [item for item in candidates if item["accepted"]]
    ranges: list[dict[str, Any]] = []
    for index, candidate in enumerate(accepted):
        end_order = accepted[index + 1]["order"] - 1 if index + 1 < len(accepted) else len(root_children) - 1
        scoped = [entry for entry in entries if candidate["order"] <= entry["order"] <= end_order]
        title_data = parse_appendix_title(candidate["raw_text"], index + 1)
        if candidate.get("appendix_id"):
            title_data["appendix_id"] = candidate["appendix_id"]
        appendix_id = str(title_data["appendix_id"])
        heading_entries = [
            entry for entry in scoped
            if entry["node_id"] != candidate["node_id"] and entry.get("heading_level") is not None
        ]
        generic_levels = [int(entry["heading_level"]) for entry in heading_entries]
        base_level = min(generic_levels) if generic_levels else None
        heading_decisions: list[dict[str, Any]] = []
        for entry in heading_entries:
            visible = _visible_appendix_heading(str(entry["text"]), appendix_id)
            if visible is not None:
                relative_level, heading_text = visible
                level_source = "visible_appendix_number"
            elif base_level is not None:
                relative_level = max(1, min(3, int(entry["heading_level"]) - base_level + 1))
                heading_text = str(entry["text"])
                level_source = "relative_heading_hierarchy"
            else:
                continue
            heading_decisions.append({
                "node_id": entry["node_id"],
                "location": copy.deepcopy(entry["location"]),
                "relative_level": relative_level,
                "text": heading_text,
                "level_source": level_source,
                "source_heading_level": entry["heading_level"],
            })
        end_entry = scoped[-1] if scoped else next(entry for entry in entries if entry["node_id"] == candidate["node_id"])
        ranges.append({
            "appendix_id": appendix_id,
            "classification": title_data.get("classification"),
            "title": title_data.get("title", ""),
            "title_lines": list(title_data.get("title_lines") or []),
            "soft_break_count": int(title_data.get("soft_break_count") or 0),
            "title_location": copy.deepcopy(candidate["location"]),
            "title_node_id": candidate["node_id"],
            "start_location": copy.deepcopy(candidate["location"]),
            "end_location": copy.deepcopy(end_entry["location"]),
            "page_break_before": True,
            "confidence": candidate["confidence"],
            "evidence": list(candidate["evidence"]),
            "negative_evidence": list(candidate["negative_evidence"]),
            "heading_decisions": heading_decisions,
            "start_order": candidate["order"],
            "end_order": end_order,
            "root_node_ids": root_children[candidate["order"]:end_order + 1],
        })
    for candidate in candidates:
        candidate.pop("order", None)
        candidate.pop("raw_text", None)
    return {"candidates": candidates, "ranges": ranges, "issues": []}


def _appendix_classification(value: Any) -> str:
    return {
        "规范性": "normative",
        "资料性": "informative",
        "normative": "normative",
        "informative": "informative",
    }.get(str(value or ""), "unknown")


def _ast_source_ref(ast: Mapping[str, Any], node_id: str) -> dict[str, Any] | None:
    document = ast["document"]
    node = document["blocks"].get(node_id) or document["containers"].get(node_id)
    source_ref = node.get("source_ref") if isinstance(node, Mapping) else None
    return copy.deepcopy(dict(source_ref)) if isinstance(source_ref, Mapping) else None


def _appendix_candidate(
    candidate_id: str,
    target_node_ids: list[str],
    target_source_refs: list[dict[str, Any]],
    proposal: dict[str, Any],
    *,
    rationale: str,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "module_id": "appendix_semantics",
        "module_version": "1.0",
        "target_source_refs": target_source_refs,
        "target_node_ids": target_node_ids,
        "proposal": proposal,
        "positive_evidence": evidence or [{"kind": "format_contract", "source": "appendix_semantics"}],
        "negative_evidence": [],
        "scope": {"stages": ["relation"]},
        "constraints": [],
        "rationale": rationale,
    }


def prepare_native_appendix_semantics(
    facts: Mapping[str, Any],
    source_ast: Mapping[str, Any],
    semantics: Mapping[str, Any],
    item_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map source-root list paragraphs to their native list containers.

    ``item_map`` is the published ``list_item_map`` capability owned by list
    semantics.  It records the native list container that owns each source list
    item, so this function reads it instead of rebuilding that map from the
    list result.
    """

    validate_source_facts(facts)
    validate_ast(source_ast)
    prepared = copy.deepcopy(dict(semantics))
    item_to_list = {
        str(item_id): str(list_id)
        for item_id, list_id in (item_map or {}).items()
        if item_id and list_id
    }
    for appendix in prepared.get("ranges", []):
        seen: set[str] = set()
        mapped: list[str] = []
        for node_id in appendix.get("root_node_ids", []):
            native_id = item_to_list.get(str(node_id), str(node_id))
            if native_id in seen:
                continue
            seen.add(native_id)
            mapped.append(native_id)
        appendix["source_root_node_ids"] = list(appendix.get("root_node_ids") or [])
        appendix["root_node_ids"] = mapped
    return prepared


def collect_native_appendix_candidates(
    facts: Mapping[str, Any],
    source_ast: Mapping[str, Any],
    semantics: Mapping[str, Any] | None = None,
    item_map: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build native AST operations for accepted SourceFacts appendix ranges.

    ``item_map`` is the published ``list_item_map`` capability.  The appendix
    reads it to resolve the source reference of an appendix list container that
    the list transaction creates.
    """

    validate_source_facts(facts)
    validate_ast(source_ast)
    semantics = semantics or collect_source_appendix_semantics(facts)
    document = source_ast["document"]
    root_children = list(document["children"].get(document["root_id"], []))
    candidates: list[dict[str, Any]] = []
    used_container_ids = set(document["containers"])
    list_first_source_id: dict[str, str] = {}
    for item_id, list_id in (item_map or {}).items():
        if item_id and list_id:
            list_first_source_id.setdefault(str(list_id), str(item_id))

    def node_source_ref(node_id: str) -> dict[str, Any] | None:
        ref = _ast_source_ref(source_ast, node_id)
        if ref is not None:
            return ref
        source_id = list_first_source_id.get(node_id)
        if source_id:
            source_node = facts.get("units", {}).get(source_id) or facts.get("containers", {}).get(source_id) or {}
            source_ref = source_node.get("source_ref") if isinstance(source_node, Mapping) else None
            if isinstance(source_ref, Mapping):
                return copy.deepcopy(dict(source_ref))
        return None

    for ordinal, appendix in enumerate(semantics.get("ranges", []), 1):
        appendix_id = str(appendix.get("appendix_id") or chr(64 + ordinal)).upper()
        title_id = str(appendix.get("title_node_id") or "")
        if not title_id:
            title_candidates = [
                candidate for candidate in semantics.get("candidates", [])
                if candidate.get("appendix_id") == appendix_id
            ]
            title_id = str(title_candidates[0].get("node_id")) if title_candidates else ""
        if not title_id or title_id not in document["blocks"]:
            continue
        title_ref = _ast_source_ref(source_ast, title_id)
        if title_ref is None:
            continue

        container_id = f"native-appendix-{appendix_id.casefold()}"
        suffix = 2
        while container_id in used_container_ids:
            container_id = f"native-appendix-{appendix_id.casefold()}-{suffix}"
            suffix += 1
        used_container_ids.add(container_id)
        semantic_scope_id = f"semantic:appendix:{appendix_id}"
        start_order = int(appendix.get("start_order") or 0)
        root_node_ids = [
            node_id for node_id in appendix.get("root_node_ids", [])
            if (node_id in root_children or node_id in list_first_source_id)
            and node_id != document["root_id"]
        ]
        if not root_node_ids:
            continue
        # The create index is a position in the parent's children list, so it is
        # read from the AST.  ``start_order`` counts SourceFacts root children and
        # the AST adds nodes of its own (a materialized inline image node, a list
        # container), so handing it to the transaction places the container ahead
        # of the blocks that precede the range.
        parent_children = document["children"].get(document["root_id"], [])
        range_index = (
            parent_children.index(root_node_ids[0])
            if root_node_ids[0] in parent_children
            else len(parent_children)
        )
        source_classification = _appendix_classification(appendix.get("classification"))
        container = {
            "kind": "appendix",
            "source_classification": source_classification,
            "title_ref": title_id,
            "source_ref": copy.deepcopy(title_ref),
            "appendix_id": appendix_id,
            "semantic_scope_id": semantic_scope_id,
            "source_range": {
                "start_order": start_order,
                "end_order": int(appendix.get("end_order") or start_order),
            },
            "source_number_preserved": True,
            "page_break_before": bool(appendix.get("page_break_before", True)),
        }
        candidates.append(_appendix_candidate(
            f"appendix-range-{appendix_id}",
            [container_id],
            [title_ref],
            {
                "operation": "create_node",
                "property_scope": ["children"],
                "value": {
                    "node_id": container_id,
                    "parent_id": document["root_id"],
                    "index": range_index,
                    "node": container,
                },
            },
            rationale="appendix range materialized from immutable SourceFacts semantics",
            evidence=[{"kind": "format_contract", "source": "appendix_range"}],
        ))

        for index, node_id in enumerate(root_node_ids):
            node_ref = node_source_ref(node_id)
            if node_ref is None:
                continue
            candidates.append(_appendix_candidate(
                f"appendix-move-{appendix_id}-{node_id}",
                [node_id],
                [node_ref],
                {
                    "operation": "move_node",
                    "property_scope": ["children", "semantics"],
                    "value": {
                        "new_parent_id": container_id,
                        "index": index,
                        "semantic_metadata": {
                            "appendix_id": appendix_id,
                            "semantic_scope_id": semantic_scope_id,
                            "source_number_preserved": True,
                        },
                    },
                },
                rationale="appendix root range assigned to its semantic container",
                evidence=[{"kind": "structural", "source": "appendix_range"}],
            ))

        def consume_marker(node_id: str, pattern: str) -> None:
            block = document["blocks"][node_id]
            content = list(block.get("inline_content") or [])
            text = inline_text_projection(content)
            match = re.match(pattern, text, re.IGNORECASE)
            if match is None:
                return
            cleaned = consume_leading_text(content, match.end())
            if inline_text_projection(cleaned) != text[match.end():]:
                return
            ref = _ast_source_ref(source_ast, node_id)
            candidates.append(_appendix_candidate(
                f"appendix-content-{node_id}", [node_id], [ref] if ref else [],
                {"operation": "update_node_content", "property_scope": ["inline_content"],
                 "value": {"inline_content": cleaned}},
                rationale="consume the authored appendix marker owned by template numbering",
            ))

        # Dedicated styles can name an appendix without a literal marker.
        # Only consume a prefix the shared marker parser actually recognizes.
        if parse_appendix_marker(_text(document["blocks"][title_id]).split("\n", 1)[0]) is not None:
            consume_marker(title_id, r"^[ \t]*附\s*录(?:[ \t]*[A-Z])?(?:[ \t]*[：:])?[ \t]*")
        title_value = {
            "semantic_role": "appendix_title",
            "appendix_role": "title",
            "appendix_id": appendix_id,
            "semantic_scope_id": semantic_scope_id,
            "page_break_before": bool(appendix.get("page_break_before", True)),
            "source_number_preserved": True,
        }
        candidates.append(_appendix_candidate(
            f"appendix-title-{title_id}",
            [title_id],
            [title_ref],
            {"operation": "update_node_semantics", "property_scope": ["semantics"], "value": title_value},
            rationale="appendix title role resolved from SourceFacts appendix semantics",
        ))

        for heading in appendix.get("heading_decisions", []):
            heading_id = str(heading.get("node_id") or "")
            heading_ref = _ast_source_ref(source_ast, heading_id)
            if not heading_id or heading_ref is None:
                continue
            consume_marker(heading_id, r"^[ \t]*" + re.escape(appendix_id) + r"(?:[.．]\d+){1,3}(?:[ \t、：:.)）-]+|$)")
            heading_value = {
                "semantic_role": "appendix_heading",
                "appendix_role": "heading",
                "appendix_id": appendix_id,
                "semantic_scope_id": semantic_scope_id,
                "appendix_relative_level": int(heading.get("relative_level") or 1),
                "source_heading_level": heading.get("source_heading_level"),
            }
            candidates.append(_appendix_candidate(
                f"appendix-heading-{heading_id}",
                [heading_id],
                [heading_ref],
                {"operation": "update_node_semantics", "property_scope": ["semantics"], "value": heading_value},
                rationale="appendix heading relative level resolved from source hierarchy",
            ))

    return candidates


def audit_native_appendix(
    source_ast: Mapping[str, Any],
    semantics: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit appendix containers, range ownership and relative heading metadata."""

    validate_ast(source_ast)
    document = source_ast["document"]
    root_children = set(document["children"].get(document["root_id"], []))
    issues: list[dict[str, Any]] = []
    checked = 0
    for appendix in semantics.get("ranges", []):
        appendix_id = str(appendix.get("appendix_id") or "").upper()
        containers = [
            (node_id, node)
            for node_id, node in document["containers"].items()
            if node.get("kind") == "appendix" and str(node.get("appendix_id") or "").upper() == appendix_id
        ]
        if len(containers) != 1:
            issues.append({"code": "appendix_container_count", "appendix_id": appendix_id, "count": len(containers)})
            continue
        checked += 1
        container_id, container = containers[0]
        expected_nodes = set(appendix.get("root_node_ids") or [])
        actual_nodes = set(document["children"].get(container_id, []))
        # The range owns every block of its subtree, not only the container's
        # direct children: list materialization folds a list item's following
        # content into the item, so a declared block may sit one or more levels
        # down.  Ownership is therefore containment, while a declared block that
        # ends up outside the subtree and a child that was never declared are both
        # still reported.
        owned = set(actual_nodes)
        frontier = list(actual_nodes)
        while frontier:
            current = frontier.pop()
            for child in document["children"].get(current, []):
                if child not in owned:
                    owned.add(child)
                    frontier.append(child)
        # A caption generated for a table or figure inside the range joins the
        # container after the range was declared, so it is not in the declared
        # roots.  A generated caption that declares this appendix and anchors to
        # a target already in the range belongs to it; a container child that
        # declares nothing is still reported.
        generated_nodes = {
            node_id
            for node_id in actual_nodes - expected_nodes
            if str((document["blocks"].get(node_id) or {}).get("appendix_id") or "").upper() == appendix_id
            and str((document["blocks"].get(node_id) or {}).get("target_id") or "") in expected_nodes
        }
        expected_nodes |= generated_nodes
        missing = expected_nodes - owned - {container_id}
        foreign = actual_nodes - expected_nodes
        if missing or foreign:
            issues.append({
                "code": "appendix_range_mismatch",
                "appendix_id": appendix_id,
                "missing": sorted(missing),
                "foreign": sorted(foreign),
            })
        if expected_nodes & root_children:
            issues.append({"code": "appendix_range_leak", "appendix_id": appendix_id, "node_ids": sorted(expected_nodes & root_children)})
        if container.get("title_ref") not in actual_nodes:
            issues.append({"code": "appendix_title_not_owned", "appendix_id": appendix_id, "title_id": container.get("title_ref")})
        if container.get("semantic_scope_id") != f"semantic:appendix:{appendix_id}":
            issues.append({"code": "appendix_semantic_scope_mismatch", "appendix_id": appendix_id})
        if container.get("page_break_before") is not True:
            issues.append({"code": "appendix_page_boundary_missing", "appendix_id": appendix_id})
        title = document["blocks"].get(container.get("title_ref"), {})
        if (
            title.get("appendix_id") != appendix_id
            or title.get("appendix_role") != "title"
            or title.get("semantic_scope_id") != f"semantic:appendix:{appendix_id}"
        ):
            issues.append({"code": "appendix_title_metadata_mismatch", "appendix_id": appendix_id, "title_id": container.get("title_ref")})
        for heading in appendix.get("heading_decisions", []):
            block = document["blocks"].get(heading.get("node_id"), {})
            if block.get("appendix_id") != appendix_id or block.get("appendix_relative_level") != heading.get("relative_level"):
                issues.append({"code": "appendix_heading_metadata_mismatch", "appendix_id": appendix_id, "node_id": heading.get("node_id")})
        for node_id in sorted(owned):
            node = document["blocks"].get(node_id) or document["containers"].get(node_id) or {}
            if node.get("appendix_id") != appendix_id or node.get("semantic_scope_id") != f"semantic:appendix:{appendix_id}":
                issues.append({"code": "appendix_scope_metadata_missing", "appendix_id": appendix_id, "node_id": node_id})

    return {"passed": not issues, "checked_appendix_count": checked, "issue_count": len(issues), "issues": issues}


def recognize_appendix_semantics(
    facts: Mapping[str, Any],
    source_ast: Mapping[str, Any],
    item_map: Mapping[str, Any],
) -> dict[str, Any]:
    """Own the whole appendix concept: facts result plus AST candidates.

    ``item_map`` is the published ``list_item_map`` capability owned by list
    semantics, which the appendix reads to map source list items onto their
    native list containers.
    """

    result = collect_source_appendix_semantics(facts)
    prepared = prepare_native_appendix_semantics(facts, source_ast, result, item_map)
    candidates = collect_native_appendix_candidates(facts, source_ast, prepared, item_map)
    return {"result": prepared, "candidates": candidates}


__all__ = [
    "annotate_appendix_ranges",
    "audit_native_appendix",
    "collect_appendix_candidates",
    "collect_native_appendix_candidates",
    "collect_source_appendix_semantics",
    "prepare_native_appendix_semantics",
    "recognize_appendix_semantics",
]
