"""Protected-content candidates with explicit opaque boundaries."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from note_semantics import source_note_role

from ..model import inline_text_projection
from ..validation import validate_source_facts


_API_RE = re.compile(r"^(?:API|接口)\s*[:：]", re.IGNORECASE)
_FORMULA_RE = re.compile(r"(?:[=＋+−\-*/^]|\\frac|\\sum|\d\s*[A-Za-z])")


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
    for node_id, unit in facts["units"].items():
        kind = str(unit.get("kind") or "")
        text = inline_text_projection(unit.get("inline_content", []))
        evidence = facts["evidence"].get(node_id, {})
        role = source_note_role(unit.get("style") or evidence.get("style"), evidence.get("numbering"), unit.get("raw_text") or text)
        if kind in {"code_block", "opaque"}:
            result.append(_candidate(str(node_id), unit, "opaque_code" if kind == "code_block" else "opaque_source"))
        elif role:
            result.append(_candidate(str(node_id), unit, "note", role=role))
        elif _API_RE.match(text.strip()):
            result.append(_candidate(str(node_id), unit, "api_label"))
        elif _FORMULA_RE.search(text) and evidence.get("formula"):
            result.append(_candidate(str(node_id), unit, "formula_opaque"))
    for node_id, container in facts["containers"].items():
        if container.get("kind") == "quote":
            result.append(_candidate(str(node_id), container, "quote"))
    return sorted(result, key=lambda item: (str(item["module_id"]), str(item["candidate_id"])))


__all__ = ["collect_protected_candidates"]
