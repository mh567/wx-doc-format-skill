"""Shadow comparison between native semantic candidates and migrated source AST."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from ..model import inline_text_projection
from ..validation import validate_ast, validate_source_facts
from text_utils import strip_heading_marker


def _ordered_ids(children: Mapping[str, list[str]], root_id: str) -> list[str]:
    result: list[str] = []

    def walk(node_id: str) -> None:
        for child_id in children.get(node_id, []):
            result.append(child_id)
            walk(child_id)

    walk(root_id)
    return result


def _candidate_roles(candidates: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        value = candidate.get("proposal", {}).get("value", {})
        for node_id in candidate.get("target_node_ids", []):
            result[str(node_id)] = value
    return result


def _records_from_facts(
    facts: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidate_roles = _candidate_roles(candidates)
    records: list[dict[str, Any]] = []
    for node_id in _ordered_ids(facts["children"], "root"):
        unit = facts["units"].get(node_id)
        proposal = candidate_roles.get(node_id)
        if not isinstance(unit, Mapping) or not isinstance(proposal, Mapping):
            continue
        text = inline_text_projection(unit.get("inline_content", [])).strip()
        if not text:
            continue
        records.append({
            "node_id": node_id,
            "text": text,
            "role": proposal.get("role"),
            "level": proposal.get("level"),
        })
    return records


def _records_from_ast(ast: Mapping[str, Any]) -> list[dict[str, Any]]:
    document = ast["document"]
    records: list[dict[str, Any]] = []

    def walk(parent_id: str, *, in_appendix: bool = False) -> None:
        for node_id in document["children"].get(parent_id, []):
            container = document["containers"].get(node_id)
            child_in_appendix = in_appendix or (
                isinstance(container, Mapping) and container.get("kind") == "appendix"
            )
            block = document["blocks"].get(node_id)
            if isinstance(block, Mapping) and block.get("block_type") == "paragraph" and not block.get("generated_by"):
                text = inline_text_projection(block.get("inline_content", [])).strip()
                if text:
                    records.append({
                        "node_id": node_id,
                        "text": text,
                        "role": block.get("role"),
                        "level": block.get("level"),
                        "semantic_scope": "appendix" if in_appendix else "body",
                    })
            walk(node_id, in_appendix=child_in_appendix)

    walk(ast["document"]["root_id"])
    return records


def _occurrence_records(records: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for record in records:
        text = record["text"]
        match_text = (
            strip_heading_marker(text).strip()
            if record.get("role") in {"title", "heading"}
            else text
        )
        counts[match_text] += 1
        result[(match_text, counts[match_text])] = record
    return result


def compare_structure_equivalence(
    facts: Mapping[str, Any],
    regions: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
    legacy_ast: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare observable paragraph decisions by text occurrence and source order."""

    validate_source_facts(facts)
    validate_ast(legacy_ast)
    native_records = _records_from_facts(facts, candidates)
    legacy_records = _records_from_ast(legacy_ast)
    native_by_key = _occurrence_records(native_records)
    legacy_by_key = _occurrence_records(legacy_records)
    owned_roles = {"title", "heading", "body"}
    issues: list[dict[str, Any]] = []
    matched = 0
    compared = 0
    ignored_other_domain = 0

    for key, native in native_by_key.items():
        legacy = legacy_by_key.get(key)
        if legacy is not None and legacy.get("semantic_scope") == "appendix":
            ignored_other_domain += 1
            continue
        if legacy is not None and legacy["role"] not in owned_roles:
            ignored_other_domain += 1
            continue
        if legacy is None:
            if native["role"] == "body":
                ignored_other_domain += 1
                continue
            compared += 1
            issues.append({
                "type": "legacy_node_missing",
                "node_id": native["node_id"],
                "text": native["text"],
            })
            continue
        compared += 1
        matched += 1
        if native["role"] != legacy["role"]:
            issues.append({
                "type": "role_mismatch",
                "node_id": native["node_id"],
                "legacy_node_id": legacy["node_id"],
                "text": native["text"],
                "native": native["role"],
                "legacy": legacy["role"],
            })
        elif native["role"] == "heading" and native["level"] != legacy["level"]:
            issues.append({
                "type": "level_mismatch",
                "node_id": native["node_id"],
                "legacy_node_id": legacy["node_id"],
                "text": native["text"],
                "native": native["level"],
                "legacy": legacy["level"],
            })

    for key, legacy in legacy_by_key.items():
        if (
            legacy.get("semantic_scope") != "appendix"
            and legacy["role"] in {"title", "heading"}
            and key not in native_by_key
        ):
            issues.append({
                "type": "native_candidate_missing",
                "legacy_node_id": legacy["node_id"],
                "text": legacy["text"],
            })

    excluded_node_ids = {
        str(node_id)
        for region in regions.get("regions", [])
        if region.get("semantic_scope") == "excluded"
        for node_id in region.get("node_ids", [])
    }
    excluded_texts = {
        inline_text_projection(facts["units"][node_id].get("inline_content", [])).strip()
        for node_id in excluded_node_ids
        if node_id in facts["units"]
    }
    for text in sorted(excluded_texts - {""}):
        if any(record["text"] == text for record in legacy_records):
            issues.append({"type": "excluded_region_retained", "text": text})

    return {
        "schema_version": "1.0",
        "status": "equivalent" if not issues else "mismatch",
        "passed": not issues,
        "compared_node_count": compared,
        "matched_node_count": matched,
        "legacy_node_count": sum(
            record["role"] in owned_roles and record.get("semantic_scope") != "appendix"
            for record in legacy_records
        ),
        "ignored_other_domain_count": ignored_other_domain,
        "excluded_node_count": len(excluded_node_ids),
        "issues": issues,
    }


__all__ = ["compare_structure_equivalence"]
