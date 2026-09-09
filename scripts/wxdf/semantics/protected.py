"""Protected-content candidates with explicit opaque boundaries."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from note_semantics import source_note_role, strip_source_note_marker
from text_utils import clean_note_prefix

from ..model import inline_text_projection
from ..validation import validate_source_facts


_API_RE = re.compile(r"^(?:API|接口)\s*[:：]", re.IGNORECASE)
_FORMULA_RE = re.compile(r"(?:[=＋+−\-*/^]|\\frac|\\sum|\d\s*[A-Za-z])")
_NOTE_PREFIXES = ("备注：", "编写提示：", "【备注提示】", "【编写样例】")


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
    if _API_RE.match(text.strip()):
        return {"reason": "api_label", "role": None, "text": text}
    if _FORMULA_RE.search(text) and evidence.get("formula"):
        return {"reason": "formula_opaque", "role": "formula", "text": text}
    return None


def _candidate(node_id: str, unit: Mapping[str, Any], reason: str, *, role: str | None = None) -> dict[str, Any]:
    source_ref = unit.get("source_ref")
    value: dict[str, Any] = {"protected": True, "reason": reason}
    if role:
        value["role"] = role
    return {
        "candidate_id": f"protected-{reason}-{node_id}",
        "module_id": "protected_content",
        "module_version": "1.0",
        "target_source_refs": [copy.deepcopy(dict(source_ref))] if isinstance(source_ref, Mapping) else [],
        "target_node_ids": [node_id],
        "proposal": {"operation": "update_overlay", "property_scope": ["overlay"], "value": value},
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


__all__ = ["collect_protected_candidates", "protected_unit_semantics"]
