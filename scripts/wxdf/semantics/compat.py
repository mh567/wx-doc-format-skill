"""Pure candidate adapters around existing deterministic semantic helpers."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from appendix_semantics import collect_appendix_candidates
from front_matter import front_matter_source_positions
from note_semantics import source_note_role
from toc_detector import selected_source_positions


def _source_ref(block: Mapping[str, Any]) -> dict[str, Any]:
    source = block.get("source") or {}
    return copy.deepcopy(source.get("source_ref") or {})


def _protection_candidates(
    blocks: list[Mapping[str, Any]],
    positions: set[int],
    *,
    module_id: str,
    reason: str,
) -> list[dict[str, Any]]:
    result = []
    for index, block in enumerate(blocks):
        source_position = (block.get("source") or {}).get("source_position")
        if source_position not in positions:
            continue
        block_id = str(block.get("id") or f"legacy-{index}")
        result.append(
            {
                "candidate_id": f"{module_id}-{block_id}",
                "module_id": module_id,
                "module_version": "1.0",
                "target_source_refs": [_source_ref(block)] if _source_ref(block) else [],
                "target_node_ids": [block_id],
                "proposal": {"operation": "update_overlay", "property_scope": ["overlay"], "value": {"protected": True, "reason": reason}},
                "positive_evidence": [{"kind": "format_contract", "source": module_id}],
                "negative_evidence": [],
                "scope": {"stages": ["scope"]},
                "constraints": [],
                "rationale": reason,
            }
        )
    return result


def collect_compat_candidates(
    model: Mapping[str, Any],
    *,
    stage: str = "role",
    front_matter_context: Mapping[str, Any] | None = None,
    toc_context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect candidates without mutating the legacy model."""

    blocks = list((model.get("document") or {}).get("blocks") or [])
    candidates: list[dict[str, Any]] = []
    if front_matter_context:
        candidates.extend(
            _protection_candidates(
                blocks,
                front_matter_source_positions(dict(front_matter_context)),
                module_id="front_matter",
                reason="front matter is excluded from semantic body scope",
            )
        )
    if toc_context:
        candidates.extend(
            _protection_candidates(
                blocks,
                selected_source_positions(dict(toc_context)),
                module_id="toc_detector",
                reason="source table of contents is excluded from semantic body scope",
            )
        )
    for index, block in enumerate(blocks):
        block_id = str(block.get("id") or f"legacy-{index}")
        source = block.get("source") or {}
        role = source_note_role(source.get("style"), source.get("numbering"), source.get("raw_text") or block.get("text"))
        if role:
            candidates.append(
                {
                    "candidate_id": f"note-{block_id}",
                    "module_id": "note_semantics",
                    "module_version": "1.0",
                    "target_source_refs": [_source_ref(block)] if _source_ref(block) else [],
                    "target_node_ids": [block_id],
                    "proposal": {"operation": "update_role", "property_scope": ["role"], "value": {"role": role}},
                    "positive_evidence": [{"kind": "format_contract", "source": "note_semantics"}],
                    "negative_evidence": [],
                    "scope": {"stages": [stage]},
                    "constraints": [],
                    "rationale": "legacy note semantic adapter",
                }
            )

    appendix_model = copy.deepcopy(dict(model))
    appendix_candidates = collect_appendix_candidates(appendix_model)
    for candidate in appendix_candidates:
        if not candidate.get("accepted"):
            continue
        block_id = str(candidate.get("block_id"))
        candidates.append(
            {
                "candidate_id": f"appendix-{block_id}",
                "module_id": "appendix_semantics",
                "module_version": "1.0",
                "target_source_refs": [],
                "target_node_ids": [block_id],
                "proposal": {"operation": "update_role", "property_scope": ["role"], "value": {"role": "heading"}},
                "positive_evidence": [{"kind": "structural", "source": "appendix_semantics", "evidence": candidate.get("evidence", [])}],
                "negative_evidence": [{"kind": value} for value in candidate.get("negative_evidence", [])],
                "scope": {"stages": ["scope", "role"]},
                "constraints": [],
                "rationale": "legacy appendix semantic adapter",
            }
        )
    return sorted(candidates, key=lambda item: (item["module_id"], item["candidate_id"]))
