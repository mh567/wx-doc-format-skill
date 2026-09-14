"""Protected-content candidates with explicit opaque boundaries."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..contracts import overlay_property_scope
from note_semantics import source_note_role, strip_source_note_marker
from text_utils import clean_note_prefix

from ..model import inline_text_projection
from ..validation import validate_source_facts


_API_RE = re.compile(r"^(?:API|接口)(?:\s*[:：]|\s+|$)", re.IGNORECASE)
_HTTP_API_RE = re.compile(r"^(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/", re.IGNORECASE)
_DATE_RE = re.compile(r"^(?:(?:发布日期|日期|时间)\s*[:：]?\s*)?(?:\d{4}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?|二〇?二\S+年)")
_VERSION_RE = re.compile(r"^(?:(?:版本(?:号)?|version|release|build)\s*[:：]?\s*)?(?:v?\d+(?:\.\d+){1,}|版本\s*\d+)", re.IGNORECASE)
_MANUAL_HEADING_RE = re.compile(r"^(?:第[一二三四五六七八九十百千万0-9]+[章节]|\d+(?:\.\d+)+)[\s、.．:：]")
_CODE_STYLE_RE = re.compile(r"^(?:code|codeblock|代码|代码块|代码段)$", re.IGNORECASE)
_VERSION_CONTEXT_RE = re.compile(r"^(?:v?\d+(?:\.\d+){1,})\s*(?:版本|版本号|release|build|发布)", re.IGNORECASE)
_API_LABELS = {
    "请求参数", "请求示例", "返回参数", "返回示例", "接口说明", "请求体",
    "响应体", "成功响应", "失败响应",
}
_FORMULA_RE = re.compile(r"(?:[=＋+−\-*/^]|\\frac|\\sum|\d\s*[A-Za-z])")
_NOTE_PREFIXES = ("备注：", "编写提示：", "【备注提示】", "【编写样例】")


def protected_code_style(style: str) -> bool:
    """Return whether a style name marks the unit as an opaque code block."""

    return _CODE_STYLE_RE.match(str(style).strip()) is not None


def protected_text_reason(text: str, evidence: Mapping[str, Any]) -> str | None:
    """Single owner of the text and numbering protection policy.

    Consumes the date, version, API and manual-heading rules together with the
    source numbering evidence and returns the protection reason, or ``None``
    when the unit carries no protection signal. When a visible marker is
    present the decision is final: the manual-heading and version fallbacks do
    not run.
    """

    normalized = text.strip()
    if _API_RE.match(normalized) or _HTTP_API_RE.match(normalized) or normalized.rstrip("：:") in _API_LABELS:
        return "api_label"
    if _DATE_RE.match(normalized) or _VERSION_CONTEXT_RE.match(normalized):
        return "date_or_version"
    visible_marker = evidence.get("visible_marker")
    if isinstance(visible_marker, Mapping) and evidence.get("marker_kind"):
        marker_text = str(visible_marker.get("marker_text") or "")
        marker_match = re.match(r"^[ \t]*" + re.escape(marker_text) + r"[ \t]+", normalized)
        remainder = normalized[marker_match.end():].strip() if marker_match else ""
        if _DATE_RE.match(remainder) or _VERSION_CONTEXT_RE.match(remainder):
            return "date_or_version"
        if remainder.casefold().startswith(("版本", "version", "release", "build")):
            return "date_or_version"
        if evidence.get("source_list_type") == "decimal_chain":
            segments = re.split(r"[.．-]", marker_text.rstrip(".)"))
            if len([segment for segment in segments if segment]) < 3:
                return "manual_heading_marker"
        return None
    if _MANUAL_HEADING_RE.match(normalized):
        return "manual_heading_marker"
    if _VERSION_RE.match(normalized):
        return "date_or_version"
    return None


def protected_unit_semantics(unit: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one protected-role and canonical-text contract for a source unit."""

    kind = str(unit.get("kind") or "")
    text = inline_text_projection(unit.get("inline_content", []))
    if kind in {"code_block", "opaque"}:
        return {
            "reason": "opaque_code" if kind == "code_block" else "opaque_source",
            "role": None,
            "text": text,
        }
    style = str(unit.get("style") or evidence.get("style") or "").strip()
    if unit.get("role") in {"code", "code_block"} or evidence.get("semantic_role") in {"code", "code_block"} or protected_code_style(style):
        return {"reason": "opaque_code", "role": "code_block", "text": text}
    role = source_note_role(
        unit.get("style") or evidence.get("style"),
        evidence.get("numbering"),
        unit.get("raw_text") or text,
    )
    if role is None and text.strip().startswith(_NOTE_PREFIXES):
        role = "note"
    if role:
        canonical = clean_note_prefix(strip_source_note_marker(text, role))
        return {"reason": "note", "role": role, "text": canonical}
    normalized_text = text.strip()
    reason = protected_text_reason(normalized_text, evidence)
    if reason is not None:
        return {"reason": reason, "role": None, "text": text}
    visible_marker = evidence.get("visible_marker")
    if isinstance(visible_marker, Mapping) and evidence.get("marker_kind"):
        return None
    if _FORMULA_RE.search(text) and evidence.get("formula"):
        return {"reason": "formula_opaque", "role": "formula", "text": text}
    return None


def _candidate(node_id: str, unit: Mapping[str, Any], reason: str, *, role: str | None = None) -> dict[str, Any]:
    source_ref = unit.get("source_ref")
    value: dict[str, Any] = {"protected": True, "reason": reason}
    if role:
        value["protected_role"] = role
    return {
        "candidate_id": f"protected-{reason}-{node_id}",
        "module_id": "protected_content",
        "module_version": "1.0",
        "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
        "target_node_ids": [node_id],
        "proposal": {
            "operation": "update_overlay",
            "property_scope": overlay_property_scope(value),
            "value": value,
        },
        "positive_evidence": [{"kind": "format_contract", "source": "protected_content", "reason": reason}],
        "negative_evidence": [],
        "scope": {"protected": True},
        "constraints": [{"kind": "opaque_or_role_protection"}],
        "rationale": f"preserve {reason} source semantics without heuristic retyping",
        "confidence": None,
    }


def collect_protected_candidates(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Identify protected units and containers from public SourceFacts."""

    validate_source_facts(facts)
    result: list[dict[str, Any]] = []
    table_descendants: set[str] = set()
    pending = [
        str(node_id)
        for node_id, container in facts["containers"].items()
        if container.get("kind") == "table"
    ]
    while pending:
        parent_id = pending.pop()
        for child_id in facts["children"].get(parent_id, []):
            child_key = str(child_id)
            if child_key not in table_descendants:
                table_descendants.add(child_key)
                pending.append(child_key)
    for node_id, unit in facts["units"].items():
        if str(node_id) in table_descendants:
            continue
        evidence = facts["evidence"].get(node_id, {})
        semantic = protected_unit_semantics(unit, evidence)
        if semantic is not None:
            result.append(_candidate(
                str(node_id),
                unit,
                str(semantic["reason"]),
                role=str(semantic["role"]) if semantic.get("role") else None,
            ))
    for node_id, container in facts["containers"].items():
        if container.get("kind") == "quote":
            result.append(_candidate(str(node_id), container, "quote"))
    return sorted(result, key=lambda item: (str(item["module_id"]), str(item["candidate_id"])))


__all__ = [
    "collect_protected_candidates",
    "protected_code_style",
    "protected_text_reason",
    "protected_unit_semantics",
]
