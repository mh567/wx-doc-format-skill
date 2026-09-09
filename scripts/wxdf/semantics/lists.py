"""List semantics over the public SourceFacts contract."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..validation import validate_source_facts


_DATE_RE = re.compile(r"^(?:(?:发布日期|日期|时间)\s*[:：]?\s*)?(?:\d{4}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?|二〇?二\S+年)")
_VERSION_RE = re.compile(r"^(?:(?:版本(?:号)?|version|release|build)\s*[:：]?\s*)?(?:v?\d+(?:\.\d+){1,}|版本\s*\d+)", re.IGNORECASE)
_API_RE = re.compile(r"^(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/|^(?:API|接口)(?:\s*[:：]|\s+|$)", re.IGNORECASE)
_MANUAL_HEADING_RE = re.compile(r"^(?:第[一二三四五六七八九十百千万0-9]+[章节]|\d+(?:\.\d+)+)[\s、.．:：]")
_CODE_STYLE_RE = re.compile(r"^(?:code|codeblock|代码|代码块|代码段)$", re.IGNORECASE)
_VERSION_CONTEXT_RE = re.compile(r"^(?:v?\d+(?:\.\d+){1,})\s*(?:版本|版本号|release|build|发布)", re.IGNORECASE)
_API_LABELS = {
    "请求参数", "请求示例", "返回参数", "返回示例", "接口说明", "请求体",
    "响应体", "成功响应", "失败响应",
}


def _node_kind(facts: Mapping[str, Any], node_id: str) -> str:
    if node_id in facts["units"]:
        return str(facts["units"][node_id].get("kind") or "")
    return str(facts["containers"].get(node_id, {}).get("kind") or "")


def _parent_map(facts: Mapping[str, Any]) -> dict[str, str]:
    parent: dict[str, str] = {}
    for parent_id, child_ids in facts["children"].items():
        for child_id in child_ids:
            parent[child_id] = parent_id
    return parent


def _ancestors(node_id: str, parents: Mapping[str, str]) -> list[str]:
    result: list[str] = []
    current = node_id
    while current in parents:
        current = parents[current]
        result.append(current)
    result.reverse()
    return result


def _unit_text(unit: Mapping[str, Any]) -> str:
    visible: list[str] = []
    for item in unit.get("inline_content", []):
        kind = item.get("kind")
        if kind == "text":
            visible.append(str(item.get("text") or ""))
        elif kind == "break":
            visible.append("\n")
        elif kind == "tab":
            visible.append("\t")
        elif kind == "link":
            visible.append(_inline_text(item.get("children", [])))
    return "".join(visible)


def _inline_text(items: list[Mapping[str, Any]]) -> str:
    return _unit_text({"inline_content": items})


def _is_protected(unit: Mapping[str, Any], evidence: Mapping[str, Any]) -> tuple[bool, str | None]:
    if unit.get("kind") != "paragraph":
        return True, "non_paragraph_unit"
    role = str(unit.get("role") or evidence.get("role") or "")
    style = str(unit.get("style") or evidence.get("style") or "")
    text = _unit_text(unit).strip()
    if role in {"heading", "note", "numbered_note", "caption", "title", "code", "code_block"}:
        return True, f"protected_role:{role}"
    if _CODE_STYLE_RE.match(style.strip()):
        return True, "opaque_code"
    if evidence.get("protected") is True:
        return True, "source_protected"
    if evidence.get("numbering_status") == "detected" and evidence.get("marker_kind"):
        return False, None
    normalized = style.casefold().replace(" ", "")
    if normalized.startswith(("heading", "toc")) or "目录" in style or "题注" in style or "附录标题" in style:
        return True, "protected_style"
    if _API_RE.match(text) or text.rstrip("：:") in _API_LABELS:
        return True, "api_label"
    if _DATE_RE.match(text) or _VERSION_CONTEXT_RE.match(text):
        return True, "date_or_version"
    if _MANUAL_HEADING_RE.match(text):
        return True, "manual_heading_marker"
    if _VERSION_RE.match(text):
        return True, "date_or_version"
    if evidence.get("semantic_role") in {"note", "numbered_note"}:
        return True, "note_evidence"
    return False, None


def _scope_id(ancestors: list[str], facts: Mapping[str, Any]) -> str:
    for node_id in reversed(ancestors):
        if _node_kind(facts, node_id) in {"table_cell", "quote"}:
            return f"scope:{node_id}"
    return "scope:document"


def _list_family(evidence: Mapping[str, Any], container: Mapping[str, Any] | None = None) -> str | None:
    marker_kind = evidence.get("marker_kind")
    if marker_kind in {"unordered", "bullet"}:
        return "unordered"
    if marker_kind in {"ordered", "decimal", "lower_letter", "upper_letter"}:
        return "ordered"
    if container and container.get("list_kind") in {"ordered", "unordered"}:
        return str(container["list_kind"])
    return None


def _source_numbering_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "numbering_status",
        "numbering_definition_ref",
        "num_fmt",
        "lvl_text",
        "marker_kind",
        "marker_text",
        "source_list_type",
        "numbering_evidence",
        "ordinal",
        "explicit_depth",
        "indent_value",
        "indent_unit",
        "restart_hint",
        "blank_before",
        "continuation_hint",
        "source_position",
    )
    return {
        field: copy.deepcopy(evidence[field])
        for field in fields
        if field in evidence
    }


def _anchor_info(item_id: str, facts: Mapping[str, Any]) -> tuple[bool, str]:
    if item_id in facts["units"]:
        unit = facts["units"][item_id]
        if unit.get("kind") == "paragraph":
            has_inline_image = any(item.get("kind") == "image" for item in unit.get("inline_content", []))
            if _unit_text(unit).strip() or has_inline_image:
                return False, "paragraph_first"
            return True, "empty_first_paragraph"
        return True, f"unit_{unit.get('kind') or 'unknown'}"
    children = facts["children"].get(item_id, [])
    if not children:
        return True, "empty_list_item"
    first = children[0]
    kind = _node_kind(facts, first)
    if kind in {"table", "image", "list", "quote"}:
        return True, f"first_child_{kind}"
    if first in facts["units"] and facts["units"][first].get("kind") == "paragraph":
        unit = facts["units"][first]
        has_inline_image = any(item.get("kind") == "image" for item in unit.get("inline_content", []))
        if not _unit_text(unit).strip() and not has_inline_image:
            return True, "empty_first_paragraph"
    return False, "paragraph_first"


def _emit_group(
    result: dict[str, Any],
    facts: Mapping[str, Any],
    *,
    list_id: str,
    list_kind: str,
    source_sequence_id: str,
    source_start: int,
    item_records: list[dict[str, Any]],
    scope_id: str,
    parent_item_id: str | None = None,
    explicit: bool,
    group_boundary: Mapping[str, Any] | None = None,
) -> None:
    group = {
        "list_id": list_id,
        "list_kind": list_kind,
        "source_sequence_id": source_sequence_id,
        "source_start": source_start,
        "scope_id": scope_id,
        "parent_item_id": parent_item_id,
        "explicit": explicit,
    }
    if group_boundary is not None:
        group["group_boundary"] = copy.deepcopy(dict(group_boundary))
    result["lists"].append(group)
    last_by_depth: dict[int, str] = {}
    for index, item in enumerate(item_records):
        item_id = item["item_id"]
        evidence = item["evidence"]
        if explicit:
            depth = 0
            parent_id = parent_item_id
        else:
            declared_depth = evidence.get("explicit_depth")
            depth = int(declared_depth) if isinstance(declared_depth, int) and declared_depth >= 0 else 0
            parent_id = item.get("parent_item_id")
            if depth > 0 and parent_id is None:
                result["diagnostics"].append(
                    {
                        "code": "unresolved_list_parent",
                        "severity": "warning",
                        "message": f"list item {item_id} has no evidenced parent at depth {depth}",
                        "node_id": item_id,
                    }
                )
            if parent_id is None and depth > 0:
                parent_id = last_by_depth.get(depth - 1)
        for old_depth in [key for key in last_by_depth if key >= depth]:
            last_by_depth.pop(old_depth, None)
        last_by_depth[depth] = item_id
        target_ordinal = None
        generated_anchor, anchor_reason = _anchor_info(item_id, facts)
        relation = {
            "item_id": item_id,
            "list_id": list_id,
            "parent_item_id": parent_id,
            "scope_id": scope_id,
            "source_marker": facts["containers"].get(item_id, {}).get("source_marker") or evidence.get("marker_text"),
            "source_ordinal": facts["containers"].get(item_id, {}).get("source_ordinal") or evidence.get("ordinal"),
            "source_sequence_id": source_sequence_id,
            "list_kind": list_kind,
            "target_ordinal": target_ordinal,
            "generated_anchor_required": generated_anchor,
            "anchor_reason": anchor_reason,
            "explicit": explicit,
        }
        result["items"].append(relation)
        result["candidates"].append(
            {
                "candidate_id": f"list-relation-{item_id}",
                "module_id": "list_semantics",
                "module_version": "1.0",
                "target_source_refs": [copy.deepcopy(facts["units"].get(item_id, facts["containers"].get(item_id, {})).get("source_ref"))] if facts["units"].get(item_id, facts["containers"].get(item_id, {})).get("source_ref") else [],
                "target_node_ids": [item_id],
                "proposal": {
                    "operation": "move_node",
                    "property_scope": ["children"],
                    "value": {"new_parent_id": list_id, "list_id": list_id, "parent_item_id": parent_id},
                },
                "positive_evidence": [{"kind": "structural", "source": "source_facts_list" if explicit else "bounded_list_run"}],
                "negative_evidence": [],
                "scope": {"stages": ["relation"], "scope_id": scope_id},
                "constraints": [],
                "rationale": "list ownership derived from source order and bounded depth",
            }
        )
        children = facts["children"].get(item_id, [])
        for continuation_id in children[1:]:
            if continuation_id in facts["units"] and _node_kind(facts, continuation_id) == "paragraph":
                continuation_evidence = facts["evidence"].get(continuation_id, {})
                if not continuation_evidence.get("marker_kind"):
                    result["continuations"].append(
                        {"node_id": continuation_id, "owner_item_id": item_id, "list_id": list_id, "scope_id": scope_id}
                    )


def analyze_lists(facts: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Generate list groups and relation candidates from SourceFacts."""

    validate_source_facts(facts)
    parents = _parent_map(facts)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "completed",
        "lists": [],
        "items": [],
        "continuations": [],
        "candidates": [],
        "diagnostics": [],
        "downgrades": [],
        "context": copy.deepcopy(dict(context or {})),
    }

    for node_id, evidence in facts["evidence"].items():
        if evidence.get("numbering_status") != "ignored":
            continue
        numbering_evidence = set(evidence.get("numbering_evidence") or [])
        if "invalid_numbering_definition" not in numbering_evidence:
            continue
        definition_ref = evidence.get("numbering_definition_ref")
        missing_definition = not isinstance(definition_ref, Mapping) or definition_ref.get("abstract_num_id") is None
        code = "missing_numbering_definition" if missing_definition else "invalid_numbering_definition"
        diagnostic = {
            "code": code,
            "severity": "info",
            "message": f"list-like paragraph {node_id} was downgraded to a body paragraph because its numbering definition is unavailable",
            "node_id": node_id,
            "numbering_status": "ignored",
            "numbering_definition_ref": copy.deepcopy(dict(definition_ref)) if isinstance(definition_ref, Mapping) else None,
            "source_evidence": _source_numbering_evidence(evidence),
            "fallback": {
                "kind": "body_paragraph",
                "preserve_source_content": True,
            },
        }
        result["diagnostics"].append(diagnostic)
        result["downgrades"].append(copy.deepcopy(diagnostic))

    def ordered_list_ids() -> list[str]:
        ordered: list[str] = []

        def walk(node_id: str) -> None:
            for child_id in facts["children"].get(node_id, []):
                kind = facts["containers"].get(child_id, {}).get("kind")
                if kind == "list":
                    ordered.append(child_id)
                if child_id in facts["containers"]:
                    walk(child_id)

        walk("root")
        return ordered

    for container_id in ordered_list_ids():
        container = facts["containers"][container_id]
        if container.get("kind") != "list":
            continue
        direct_items = [child for child in facts["children"].get(container_id, []) if facts["containers"].get(child, {}).get("kind") == "list_item"]
        ancestor_ids = _ancestors(container_id, parents)
        parent_item_id = next((node_id for node_id in reversed(ancestor_ids) if facts["containers"].get(node_id, {}).get("kind") == "list_item"), None)
        item_records = [{"item_id": item_id, "evidence": facts["evidence"].get(item_id, {})} for item_id in direct_items]
        list_kind = _list_family({}, container)
        if list_kind is None:
            diagnostic = {
                "code": "unknown_list_kind",
                "severity": "info",
                "message": f"explicit list container {container_id} has no supported list kind; its source content was preserved",
                "node_id": container_id,
                "fallback": {"kind": "source_container", "preserve_source_content": True},
            }
            result["diagnostics"].append(diagnostic)
            result["downgrades"].append(copy.deepcopy(diagnostic))
            continue
        _emit_group(
            result,
            facts,
            list_id=container_id,
            list_kind=list_kind,
            source_sequence_id=str(container.get("source_sequence_id") or container_id),
            source_start=max(1, int(container.get("source_start") or 1)),
            item_records=item_records,
            scope_id=_scope_id(ancestor_ids, facts),
            parent_item_id=parent_item_id,
            explicit=True,
        )

    for parent_id in sorted(facts["children"]):
        if _node_kind(facts, parent_id) in {"list", "list_item"}:
            continue
        direct_units = [child for child in facts["children"].get(parent_id, []) if child in facts["units"]]
        runs: list[list[str]] = []
        current: list[str] = []
        for unit_id in direct_units:
            unit = facts["units"][unit_id]
            evidence = facts["evidence"].get(unit_id, {})
            protected, reason = _is_protected(unit, evidence)
            marker_kind = evidence.get("marker_kind")
            if marker_kind and not protected:
                current.append(unit_id)
                continue
            if marker_kind and protected:
                result["diagnostics"].append(
                    {"code": "protected_list_like_text", "severity": "info", "message": reason or "protected content", "node_id": unit_id}
                )
            if current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)
        for run in runs:
            group_specs: list[dict[str, Any]] = []
            active_items: dict[int, str] = {}
            active_groups: dict[int, dict[str, Any]] = {}
            for unit_id in run:
                evidence = facts["evidence"].get(unit_id, {})
                depth = evidence.get("explicit_depth") if isinstance(evidence.get("explicit_depth"), int) else 0
                parent_item_id = active_items.get(depth - 1) if depth > 0 else None
                if depth > 0 and parent_item_id is None:
                    result["diagnostics"].append(
                        {
                            "code": "unresolved_list_parent",
                            "severity": "warning",
                            "message": f"list item {unit_id} has no evidenced parent at depth {depth}",
                            "node_id": unit_id,
                        }
                    )
                family = _list_family(evidence)
                if family is None:
                    for old_depth in [key for key in active_items if key >= depth]:
                        active_items.pop(old_depth, None)
                        active_groups.pop(old_depth, None)
                    diagnostic = {
                        "code": "unknown_list_marker_kind",
                        "severity": "info",
                        "message": f"list-like paragraph {unit_id} has an unsupported marker kind; its source content was preserved",
                        "node_id": unit_id,
                        "source_evidence": _source_numbering_evidence(evidence),
                        "fallback": {"kind": "body_paragraph", "preserve_source_content": True},
                    }
                    result["diagnostics"].append(diagnostic)
                    result["downgrades"].append(copy.deepcopy(diagnostic))
                    continue
                current = active_groups.get(depth)
                if current is None or current["list_kind"] != family or current.get("parent_item_id") != parent_item_id:
                    sequence_ref = evidence.get("numbering_definition_ref")
                    if current is None:
                        group_boundary = {"kind": "run_start", "depth": depth}
                    elif current["list_kind"] != family:
                        group_boundary = {
                            "kind": "same_level_list_kind_switch",
                            "depth": depth,
                            "from": current["list_kind"],
                            "to": family,
                            "previous_list_id": current["list_id"],
                        }
                    else:
                        group_boundary = {
                            "kind": "parent_item_switch",
                            "depth": depth,
                            "previous_list_id": current["list_id"],
                            "previous_parent_item_id": current.get("parent_item_id"),
                            "parent_item_id": parent_item_id,
                        }
                    current = {
                        "list_id": f"implicit-list-{unit_id}-{len(group_specs)}",
                        "list_kind": family,
                        "source_sequence_id": json_sequence_id(sequence_ref, unit_id),
                        "source_start": max(1, int(evidence.get("ordinal") or 1)),
                        "scope_id": _scope_id([*_ancestors(parent_id, parents), parent_id], facts),
                        "parent_item_id": parent_item_id,
                        "items": [],
                        "group_boundary": group_boundary,
                    }
                    group_specs.append(current)
                    active_groups[depth] = current
                current["items"].append({"item_id": unit_id, "evidence": evidence, "parent_item_id": parent_item_id})
                active_items[depth] = unit_id
                for old_depth in [key for key in active_items if key > depth]:
                    active_items.pop(old_depth, None)
                    active_groups.pop(old_depth, None)
            for group in group_specs:
                _emit_group(
                    result,
                    facts,
                    list_id=group["list_id"],
                    list_kind=group["list_kind"],
                    source_sequence_id=group["source_sequence_id"],
                    source_start=group["source_start"],
                    item_records=group["items"],
                    scope_id=group["scope_id"],
                    parent_item_id=group["parent_item_id"],
                    explicit=False,
                    group_boundary=group.get("group_boundary"),
                )

    relation_by_id = {item["item_id"]: item for item in result["items"]}
    counters: dict[str, int] = {}
    visited: set[str] = set()

    def assign_in_reading_order(node_id: str) -> None:
        if node_id in relation_by_id and node_id not in visited:
            relation = relation_by_id[node_id]
            visited.add(node_id)
            if relation["list_kind"] == "ordered":
                scope_id = relation["scope_id"]
                counters[scope_id] = counters.get(scope_id, 0) + 1
                relation["target_ordinal"] = counters[scope_id]
        for child_id in facts["children"].get(node_id, []):
            assign_in_reading_order(child_id)

    assign_in_reading_order("root")
    result["diagnostics"].sort(key=lambda item: (item.get("code", ""), item.get("node_id", "")))
    if result["diagnostics"]:
        result["status"] = "unresolved"
    return result


def json_sequence_id(numbering_definition_ref: Any, fallback: str) -> str:
    if isinstance(numbering_definition_ref, Mapping):
        values = [str(numbering_definition_ref.get(key)) for key in ("num_id", "abstract_num_id") if numbering_definition_ref.get(key) is not None]
        if values:
            return "docx-sequence-" + "-".join(values)
    return f"source-sequence-{fallback}"


def analyze(facts: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return analyze_lists(facts, context=context)


def build_list_candidates(facts: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return analyze_lists(facts, context=context)
