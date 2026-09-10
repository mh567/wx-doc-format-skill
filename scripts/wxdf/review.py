"""Candidate evaluation and mode gates for modular semantic review."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .normalization import presentation_equal, semantic_equal


MODE_MANIFEST: dict[str, dict[str, Any]] = {
    "modular_pure": {"status": "enabled", "entrypoint": "ConversionKernel.convert", "contract": "T09"},
    "modular_online": {"status": "enabled", "entrypoint": "EnhancementExecutor", "contract": "T03"},
    "modular_file": {"status": "enabled", "entrypoint": "CheckpointStore", "contract": "T04"},
    "modular_review": {"status": "enabled", "entrypoint": "ReviewEvaluator", "contract": "T05"},
    "legacy_online": {"status": "enabled", "entrypoint": "legacy CLI", "contract": "legacy"},
    "legacy_file": {"status": "enabled", "entrypoint": "legacy CLI", "contract": "1.0"},
    "legacy_review": {"status": "enabled", "entrypoint": "legacy CLI", "contract": "legacy"},
}


@dataclass(frozen=True)
class AcceptanceDecision:
    status: str
    reason: str
    accepted: bool
    semantic_changed: bool
    presentation_changed: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "accepted": self.accepted,
            "semantic_changed": self.semantic_changed,
            "presentation_changed": self.presentation_changed,
            "details": dict(self.details),
        }


def mode_manifest() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(MODE_MANIFEST)


def _normalized(bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    value = bundle.get("normalized_ast") or bundle.get("normalized") or bundle
    return value if isinstance(value, Mapping) else {}


def _content_surface(bundle: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalized(bundle)
    source_ast = normalized.get("source_ast") or bundle.get("source_ast") or {}
    document = source_ast.get("document", {}) if isinstance(source_ast, Mapping) else {}
    semantic_fields = {
        "role",
        "level",
        "semantic_role",
        "semantic_source_operation",
        "semantic_adapter",
        "semantic_paragraph_id",
        "semantic_list_parent_id",
        "appendix_id",
        "appendix_role",
        "appendix_relative_level",
        "semantic_scope_id",
        "source_heading_level",
        "page_break_before",
        "source_number_preserved",
    }
    blocks = {
        str(node_id): {
            key: copy.deepcopy(value)
            for key, value in block.items()
            if key not in semantic_fields
        }
        for node_id, block in (document.get("blocks") or {}).items()
        if isinstance(block, Mapping)
    }
    return {
        "root_id": document.get("root_id"),
        "children": copy.deepcopy(document.get("children") or {}),
        "containers": copy.deepcopy(document.get("containers") or {}),
        "associations": copy.deepcopy(document.get("associations") or []),
        "resources": copy.deepcopy(document.get("resources") or {}),
        "blocks": blocks,
        "overlay": copy.deepcopy(normalized.get("overlay") or {}),
    }


def _content_digest(bundle: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _content_surface(bundle),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _authorized_hierarchy_repair(
    baseline_bundle: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]] | None,
) -> bool:
    baseline = _content_surface(baseline_bundle)
    candidate = _content_surface(candidate_bundle)
    baseline_children = baseline.pop("children")
    candidate_children = candidate.pop("children")
    if baseline != candidate or baseline_children == candidate_children:
        return False
    move_targets = {
        str(target)
        for decision in decisions or ()
        if decision.get("operation") == "move_node"
        for target in decision.get("target_node_ids") or []
    }
    if not move_targets:
        return False

    def positions(children: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
        return {
            str(child_id): (str(parent_id), index)
            for parent_id, child_ids in children.items()
            for index, child_id in enumerate(child_ids)
        }

    before_positions = positions(baseline_children)
    after_positions = positions(candidate_children)
    changed = {
        node_id
        for node_id in set(before_positions) | set(after_positions)
        if before_positions.get(node_id) != after_positions.get(node_id)
    }
    return bool(changed) and changed.issubset(move_targets)


def _finding_score(findings: Iterable[Mapping[str, Any]]) -> tuple[int, int, int, int, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "invariant": 0}
    for finding in findings:
        severity = str(finding.get("severity") or "low")
        if str(finding.get("audit_id") or "").startswith("invariant:"):
            counts["invariant"] += 1
        elif severity in counts:
            counts[severity] += 1
        else:
            counts["low"] += 1
    return tuple(counts[name] for name in ("invariant", "critical", "high", "medium", "low"))


def _findings_pair(baseline: Mapping[str, Any], candidate: Mapping[str, Any], findings: Any) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if isinstance(findings, Mapping):
        before = findings.get("baseline", findings.get("before", []))
        after = findings.get("candidate", findings.get("after", []))
    else:
        before = baseline.get("findings", [])
        after = candidate.get("findings", [])
    return list(before or []), list(after or [])


def _decision_targets(decisions: Iterable[Mapping[str, Any]] | None) -> set[str]:
    targets: set[str] = set()
    for decision in decisions or ():
        for key in ("target_node_ids", "target_ids"):
            values = decision.get(key, [])
            if isinstance(values, str):
                values = [values]
            if isinstance(values, Iterable):
                targets.update(str(value) for value in values if value)
        for key in ("block_id", "target_id"):
            if decision.get(key):
                targets.add(str(decision[key]))
    return targets


def build_review_packet(
    baseline_bundle: Mapping[str, Any],
    findings: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a bounded post-audit view with stable AST targets."""

    normalized = _normalized(baseline_bundle)
    source_ast = normalized.get("source_ast") or baseline_bundle.get("source_ast") or {}
    document = source_ast.get("document", {}) if isinstance(source_ast, Mapping) else {}
    nodes = {
        **dict(document.get("blocks") or {}),
        **dict(document.get("containers") or {}),
    }
    finding_list = [copy.deepcopy(dict(item)) for item in findings if isinstance(item, Mapping)]
    targets: list[dict[str, Any]] = []
    for finding in finding_list:
        node_id = str(finding.get("block_id") or "")
        node = nodes.get(node_id)
        if finding.get("repairable") is not True or not isinstance(node, Mapping):
            continue
        targets.append(
            {
                **finding,
                "block_id": node_id,
                "source_ref": copy.deepcopy(dict(node.get("source_ref") or {})),
                "current": {
                    key: copy.deepcopy(node[key])
                    for key in (
                        "block_type",
                        "kind",
                        "role",
                        "level",
                        "semantic_role",
                        "table_type",
                        "caption_type",
                    )
                    if key in node
                },
            }
        )
    artifacts = baseline_bundle.get("artifacts") or {}
    return {
        "schema_version": "2.0",
        "baseline_revision": source_ast.get("revision"),
        "baseline_semantic_digest": normalized.get("semantic_digest"),
        "baseline_presentation_digest": normalized.get("presentation_digest"),
        "baseline_artifact": artifacts.get("docx") if isinstance(artifacts, Mapping) else None,
        "finding_count": len(finding_list),
        "repairable_count": len(targets),
        "targets": targets,
    }


def _high_risk_finding_ids(findings: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        str(finding.get("audit_id") or "")
        for finding in findings
        if str(finding.get("severity") or "low") in {"critical", "high"}
    }


def evaluate_candidate(
    baseline_bundle: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
    findings: Any = None,
    *,
    decisions: Iterable[Mapping[str, Any]] | None = None,
) -> AcceptanceDecision:
    """Evaluate semantic and presentation changes with protection gates."""

    baseline_normalized = _normalized(baseline_bundle)
    candidate_normalized = _normalized(candidate_bundle)
    semantic_changed = not semantic_equal(baseline_normalized, candidate_normalized)
    presentation_changed = not presentation_equal(baseline_normalized, candidate_normalized)
    details: dict[str, Any] = {
        "baseline_semantic_digest": baseline_normalized.get("semantic_digest"),
        "candidate_semantic_digest": candidate_normalized.get("semantic_digest"),
        "baseline_presentation_digest": baseline_normalized.get("presentation_digest"),
        "candidate_presentation_digest": candidate_normalized.get("presentation_digest"),
    }
    audit_exception = candidate_bundle.get("audit_exception")
    if audit_exception:
        details["audit_exception"] = copy.deepcopy(audit_exception)
        return AcceptanceDecision("rejected", "audit_exception", False, semantic_changed, presentation_changed, details)
    if not semantic_changed and not presentation_changed:
        return AcceptanceDecision("no_effect", "no_effect", False, False, False, details)

    baseline_round = baseline_bundle.get("round")
    candidate_round = candidate_bundle.get("round")
    if isinstance(baseline_round, int) and isinstance(candidate_round, int) and candidate_round > baseline_round + 1:
        return AcceptanceDecision("rejected", "review_round_exceeded", False, semantic_changed, presentation_changed, details)

    if (
        _content_digest(baseline_bundle) != _content_digest(candidate_bundle)
        and not _authorized_hierarchy_repair(baseline_bundle, candidate_bundle, decisions)
    ):
        return AcceptanceDecision("rejected", "content_changed", False, semantic_changed, presentation_changed, details)

    before, after = _findings_pair(baseline_bundle, candidate_bundle, findings)
    before_score = _finding_score(before)
    after_score = _finding_score(after)
    details["before_score"] = before_score
    details["after_score"] = after_score
    new_high_risk = sorted(_high_risk_finding_ids(after) - _high_risk_finding_ids(before))
    details["new_high_risk_findings"] = new_high_risk
    if new_high_risk:
        return AcceptanceDecision("rejected", "new_high_risk_findings", False, semantic_changed, presentation_changed, details)
    if after_score[:3] > before_score[:3]:
        return AcceptanceDecision("rejected", "protection_audit_regressed", False, semantic_changed, presentation_changed, details)

    target_ids = _decision_targets(decisions)
    before_target_ids = {str(item.get("audit_id")) for item in before if str(item.get("block_id") or "") in target_ids}
    after_target_ids = {str(item.get("audit_id")) for item in after if str(item.get("block_id") or "") in target_ids}
    details["unresolved_target_findings"] = sorted(before_target_ids & after_target_ids)
    if details["unresolved_target_findings"]:
        return AcceptanceDecision("rejected", "target_findings_not_resolved", False, semantic_changed, presentation_changed, details)
    if after_score >= before_score:
        return AcceptanceDecision("rejected", "audit_score_did_not_improve", False, semantic_changed, presentation_changed, details)
    return AcceptanceDecision("accepted", "target_audit_improved", True, semantic_changed, presentation_changed, details)


def accept_candidate(
    baseline_bundle: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
    findings: Any = None,
    *,
    decisions: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[AcceptanceDecision, dict[str, Any]]:
    """Return the accepted candidate bundle or an unchanged baseline copy."""

    decision_list = list(decisions or [])
    decision = evaluate_candidate(baseline_bundle, candidate_bundle, findings, decisions=decision_list)
    if not decision.accepted:
        return decision, copy.deepcopy(dict(baseline_bundle))
    accepted = copy.deepcopy(dict(candidate_bundle))
    if decision_list and isinstance(accepted.get("decision_ledger"), Mapping):
        from .decision_ledger import accept_decisions

        accepted["decision_ledger"] = accept_decisions(accepted["decision_ledger"], decision_list)
    accepted["acceptance"] = decision.to_dict()
    return decision, accepted


__all__ = [
    "AcceptanceDecision",
    "MODE_MANIFEST",
    "accept_candidate",
    "build_review_packet",
    "evaluate_candidate",
    "mode_manifest",
]
