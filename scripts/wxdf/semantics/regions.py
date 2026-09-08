"""Source scope and protection regions derived from SourceFacts."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from ..model import inline_text_projection
from ..validation import validate_source_facts


def _source_ref(node: Mapping[str, Any]) -> dict[str, Any]:
    value = node.get("source_ref")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _node_text(unit: Mapping[str, Any]) -> str:
    return inline_text_projection(unit.get("inline_content", []))


def _candidate(node_id: str, unit: Mapping[str, Any], *, reason: str, scope: str = "excluded") -> dict[str, Any]:
    source_ref = _source_ref(unit)
    return {
        "candidate_id": f"scope-{reason}-{node_id}",
        "module_id": "source_regions",
        "module_version": "1.0",
        "target_source_refs": [source_ref] if source_ref else [],
        "target_node_ids": [node_id],
        "proposal": {"operation": "update_overlay", "property_scope": ["overlay"], "value": {"protected": True, "reason": reason}},
        "positive_evidence": [{"kind": "format_contract", "source": "source_regions", "reason": reason}],
        "negative_evidence": [],
        "scope": {"semantic_scope": scope},
        "constraints": [{"kind": "preserve_source_fact"}],
        "rationale": f"source region {reason} is excluded from body semantics",
        "confidence": None,
    }


def classify_regions(
    facts: Mapping[str, Any],
    *,
    front_matter_positions: set[int] | None = None,
    toc_positions: set[int] | None = None,
    canonical_title_position: int | None = None,
) -> dict[str, Any]:
    """Classify source units while retaining every original source reference."""

    validate_source_facts(facts)
    front_positions = {int(value) for value in (front_matter_positions or set())}
    toc_positions_set = {int(value) for value in (toc_positions or set())}
    root_node_ids = {str(node_id) for node_id in facts["children"].get("root", [])}
    regions: list[dict[str, Any]] = []
    node_scope: dict[str, str] = {}
    for node_id, unit in facts["units"].items():
        evidence = facts["evidence"].get(node_id, {})
        position = evidence.get("source_position")
        line_start = evidence.get("line_start")
        probe_position = position if isinstance(position, int) else line_start
        text = _node_text(unit)
        if isinstance(probe_position, int) and probe_position == canonical_title_position:
            kind, scope, reason = "front_matter", "canonical_title", "document_title"
        elif isinstance(probe_position, int) and probe_position in front_positions:
            kind, scope, reason = "front_matter", "excluded", "front_matter"
        elif isinstance(probe_position, int) and probe_position in toc_positions_set:
            kind, scope, reason = "toc", "excluded", "source_toc"
        elif (
            facts["source"].get("source_type") == "markdown"
            and str(node_id) in root_node_ids
            and unit.get("role") != "heading"
            and text.strip() in {"目录", "目次", "目  次", "目  录"}
        ):
            kind, scope, reason = "toc", "excluded", "source_toc_title"
        else:
            kind, scope, reason = "body", "body", "body"
        node_scope[node_id] = scope
        regions.append({
            "region_id": f"region-{node_id}",
            "kind": kind,
            "semantic_scope": scope,
            "node_ids": [node_id],
            "source_ref": _source_ref(unit),
            "reason": reason,
        })
    for container_id, container in facts["containers"].items():
        if container_id == "root":
            continue
        evidence = facts["evidence"].get(container_id, {})
        position = evidence.get("source_position")
        if isinstance(position, int) and position in front_positions:
            kind, scope, reason = "front_matter", "excluded", "front_matter"
        elif isinstance(position, int) and position in toc_positions_set:
            kind, scope, reason = "toc", "excluded", "source_toc"
        else:
            kind = str(container.get("kind") or "container")
            scope, reason = "body", "structural_container"
        regions.append({
            "region_id": f"region-{container_id}",
            "kind": kind,
            "semantic_scope": scope,
            "node_ids": [container_id],
            "source_ref": _source_ref(container),
            "reason": reason,
        })
    return {"schema_version": "1.0", "regions": regions, "node_scope": node_scope}


def collect_scope_candidates(facts: Mapping[str, Any], regions: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Turn excluded regions into protected candidates without changing facts."""

    validate_source_facts(facts)
    regions = regions or classify_regions(facts)
    result: list[dict[str, Any]] = []
    for region in regions.get("regions", []):
        if region.get("semantic_scope") != "excluded":
            continue
        for node_id in region.get("node_ids", []):
            unit = facts["units"].get(node_id) or facts["containers"].get(node_id)
            if isinstance(unit, Mapping):
                result.append(_candidate(str(node_id), unit, reason=str(region.get("reason") or region.get("kind"))))
    return sorted(result, key=lambda item: (str(item["module_id"]), str(item["candidate_id"])))


def resolve_source_regions(source_path: Path | str, facts: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve source scope once and return all contexts needed by downstream adapters."""

    validate_source_facts(facts)
    source_type = facts["source"].get("source_type")
    report: dict[str, Any] = {}
    toc_context: dict[str, Any] | None = None
    front_matter_context: dict[str, Any] | None = None
    front_positions: set[int] = set()
    toc_positions: set[int] = set()
    if source_type == "docx":
        from docx import Document
        from front_matter import analyze_front_matter, front_matter_source_positions
        from toc_detector import detect_toc_regions, selected_source_positions

        document = Document(str(source_path))
        toc_context = detect_toc_regions(document, report)
        front_matter_context = analyze_front_matter(
            document,
            toc_context,
            Path(source_path),
            report,
        )
        front_positions = front_matter_source_positions(front_matter_context)
        toc_positions = selected_source_positions(toc_context)
        title_position = front_matter_context.get("title", {}).get("source_position")
        regions = classify_regions(
            facts,
            front_matter_positions=front_positions,
            toc_positions=toc_positions,
            canonical_title_position=title_position if isinstance(title_position, int) else None,
        )
        excluded_positions = sorted(front_positions | toc_positions)
    else:
        regions = classify_regions(facts)
        excluded_ids = {
            str(node_id)
            for region in regions["regions"]
            if region.get("semantic_scope") == "excluded"
            for node_id in region.get("node_ids", [])
        }
        excluded_positions = sorted({
            int(position)
            for node_id in excluded_ids
            for position in [facts["evidence"].get(node_id, {}).get("line_start")]
            if isinstance(position, int)
        })

    candidates = collect_scope_candidates(facts, regions)
    excluded_node_count = sum(
        len(region.get("node_ids", []))
        for region in regions["regions"]
        if region.get("semantic_scope") == "excluded"
    )
    evidence = {
        "region_count": len(regions["regions"]),
        "excluded_node_count": excluded_node_count,
        "candidate_count": len(candidates),
        "excluded_position_count": len(excluded_positions),
    }
    return {
        "schema_version": "1.0",
        "source_type": source_type,
        "regions": regions,
        "candidates": candidates,
        "excluded_source_positions": excluded_positions,
        "toc_context": toc_context,
        "front_matter_context": front_matter_context,
        "report": report,
        "evidence": evidence,
    }


__all__ = ["classify_regions", "collect_scope_candidates", "resolve_source_regions"]
