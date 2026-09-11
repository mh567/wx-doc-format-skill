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
}


_REPAIR_AUTHORITIES: dict[str, dict[str, Any]] = {
    "heading_level_jump": {
        "allowed_operations": ("update_role",),
        "property_scope": ("role", "level"),
        "required_value_fields": ("role", "level"),
        "allowed_values": {
            "role": ("heading",),
            "level": "expected_level",
        },
    },
}

_PROPERTY_AST_FIELDS: dict[str, tuple[str, ...]] = {
    "role": ("role",),
    "level": ("level",),
    "semantic_role": ("semantic_role",),
    "source_operation": ("semantic_source_operation",),
    "adapter_id": ("semantic_adapter",),
    "paragraph_id": ("semantic_paragraph_id",),
    "list_parent_id": ("semantic_list_parent_id",),
    "appendix_id": ("appendix_id",),
    "appendix_role": ("appendix_role",),
    "appendix_relative_level": ("appendix_relative_level",),
    "semantic_scope_id": ("semantic_scope_id",),
    "source_heading_level": ("source_heading_level",),
    "page_break_before": ("page_break_before",),
    "source_number_preserved": ("source_number_preserved",),
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

    if not isinstance(baseline_children, Mapping) or not isinstance(candidate_children, Mapping):
        return False

    def positions(children: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
        return {
            str(child_id): (str(parent_id), index)
            for parent_id, child_ids in children.items()
            for index, child_id in enumerate(child_ids)
        }

    before_positions = positions(baseline_children)
    after_positions = positions(candidate_children)
    if not move_targets.issubset(before_positions) or not move_targets.issubset(after_positions):
        return False
    passive_nodes = (set(before_positions) | set(after_positions)) - move_targets
    if any(
        before_positions.get(node_id, (None,))[0]
        != after_positions.get(node_id, (None,))[0]
        for node_id in passive_nodes
    ):
        return False
    for parent_id in set(baseline_children) | set(candidate_children):
        before_passive = [
            str(node_id)
            for node_id in baseline_children.get(parent_id, [])
            if str(node_id) not in move_targets
        ]
        after_passive = [
            str(node_id)
            for node_id in candidate_children.get(parent_id, [])
            if str(node_id) not in move_targets
        ]
        if before_passive != after_passive:
            return False
    return any(
        before_positions[target_id] != after_positions[target_id]
        for target_id in move_targets
    )


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
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), Mapping) else {}
        authority = _REPAIR_AUTHORITIES.get(str(evidence.get("code") or ""))
        if authority is None:
            raw_operations = finding.get("allowed_operations")
            raw_scope = finding.get("property_scope")
            raw_values = finding.get("allowed_values")
            if (
                isinstance(raw_operations, list)
                and raw_operations
                and all(isinstance(value, str) and value for value in raw_operations)
                and isinstance(raw_scope, list)
                and raw_scope
                and all(isinstance(value, str) and value for value in raw_scope)
                and isinstance(raw_values, Mapping)
                and set(raw_scope).issubset(raw_values)
                and all(
                    isinstance(values, list) and values
                    for values in raw_values.values()
                )
            ):
                authority = {
                    "allowed_operations": tuple(raw_operations),
                    "property_scope": tuple(raw_scope),
                    "allowed_values": copy.deepcopy(dict(raw_values)),
                }
        if (
            finding.get("repairable") is not True
            or not isinstance(node, Mapping)
            or authority is None
        ):
            continue
        allowed_values: dict[str, list[Any]] = {}
        for property_name, configured in authority["allowed_values"].items():
            if isinstance(configured, str):
                resolved = evidence.get(configured)
                if resolved is None:
                    allowed_values = {}
                    break
                allowed_values[str(property_name)] = [copy.deepcopy(resolved)]
            else:
                allowed_values[str(property_name)] = copy.deepcopy(list(configured))
        if set(authority["property_scope"]) != set(allowed_values):
            continue
        targets.append(
            {
                **finding,
                "block_id": node_id,
                "source_ref": copy.deepcopy(dict(node.get("source_ref") or {})),
                "allowed_operations": list(authority["allowed_operations"]),
                "property_scope": list(authority["property_scope"]),
                "allowed_values": allowed_values,
                "required_value_fields": list(authority.get("required_value_fields", ())),
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


def validate_review_decisions(
    review_packet: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return a structured violation when a review decision exceeds its packet."""

    authorities = {
        str(target.get("block_id")): target
        for target in review_packet.get("targets", [])
        if isinstance(target, Mapping) and target.get("block_id")
    }
    for index, decision in enumerate(decisions):
        targets = [str(value) for value in decision.get("target_node_ids") or []]
        operation = str(decision.get("operation") or "")
        scope = {str(value) for value in decision.get("property_scope") or []}
        if not targets or any(target not in authorities for target in targets):
            return {
                "code": "review_scope_violation",
                "kind": "target",
                "decision_index": index,
                "target_node_ids": targets,
            }
        for target_id in targets:
            authority = authorities[target_id]
            allowed_operations = {
                str(value) for value in authority.get("allowed_operations") or []
            }
            allowed_scope = {
                str(value) for value in authority.get("property_scope") or []
            }
            if operation not in allowed_operations:
                return {
                    "code": "review_scope_violation",
                    "kind": "operation",
                    "decision_index": index,
                    "operation": operation,
                }
            if not scope or not scope.issubset(allowed_scope):
                return {
                    "code": "review_scope_violation",
                    "kind": "property_scope",
                    "decision_index": index,
                    "property_scope": sorted(scope),
                }
            allowed_values = authority.get("allowed_values")
            value = decision.get("value")
            if not isinstance(allowed_values, Mapping) or not isinstance(value, Mapping):
                return {
                    "code": "review_scope_violation",
                    "kind": "value",
                    "decision_index": index,
                }
            if not set(authority.get("required_value_fields") or []).issubset(value):
                return {
                    "code": "review_scope_violation",
                    "kind": "value",
                    "decision_index": index,
                }
            for property_name, proposed in value.items():
                permitted = allowed_values.get(str(property_name))
                if not isinstance(permitted, list) or proposed not in permitted:
                    return {
                        "code": "review_scope_violation",
                        "kind": "value",
                        "decision_index": index,
                        "property": str(property_name),
                    }
        value = decision.get("value")
        if operation == "update_role" and isinstance(value, Mapping):
            if not set(value).issubset(scope):
                return {
                    "code": "review_scope_violation",
                    "kind": "value_scope",
                    "decision_index": index,
                    "value_fields": sorted(value),
                }
    return None


def _out_of_scope_semantic_change(
    baseline_bundle: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    baseline_normalized = _normalized(baseline_bundle)
    candidate_normalized = _normalized(candidate_bundle)
    baseline_ast = baseline_normalized.get("source_ast") or baseline_bundle.get("source_ast") or {}
    candidate_ast = candidate_normalized.get("source_ast") or candidate_bundle.get("source_ast") or {}
    baseline_document = copy.deepcopy(dict(baseline_ast.get("document") or {}))
    candidate_document = copy.deepcopy(dict(candidate_ast.get("document") or {}))
    for decision in decisions:
        operation = str(decision.get("operation") or "")
        if operation == "move_node":
            baseline_document.pop("children", None)
            candidate_document.pop("children", None)
            continue
        fields = {
            ast_field
            for property_name in decision.get("property_scope") or []
            for ast_field in _PROPERTY_AST_FIELDS.get(str(property_name), ())
        }
        for collection_name in ("blocks", "containers"):
            before_nodes = baseline_document.get(collection_name) or {}
            after_nodes = candidate_document.get(collection_name) or {}
            for target_id in decision.get("target_node_ids") or []:
                before_node = before_nodes.get(str(target_id))
                after_node = after_nodes.get(str(target_id))
                if isinstance(before_node, dict) and isinstance(after_node, dict):
                    for field_name in fields:
                        before_node.pop(field_name, None)
                        after_node.pop(field_name, None)
    if baseline_document != candidate_document:
        return {"code": "out_of_scope_semantic_change"}
    return None


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
    review_packet: Mapping[str, Any] | None = None,
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
    decision_list = list(decisions or [])
    if review_packet is not None:
        violation = validate_review_decisions(review_packet, decision_list)
        if violation is None:
            violation = _out_of_scope_semantic_change(
                baseline_bundle,
                candidate_bundle,
                decision_list,
            )
        if violation is not None:
            details["review_scope_violation"] = violation
            return AcceptanceDecision(
                "rejected",
                "review_scope_violation",
                False,
                semantic_changed,
                presentation_changed,
                details,
            )
    if not semantic_changed and not presentation_changed:
        return AcceptanceDecision("no_effect", "no_effect", False, False, False, details)

    baseline_round = baseline_bundle.get("round")
    candidate_round = candidate_bundle.get("round")
    if isinstance(baseline_round, int) and isinstance(candidate_round, int) and candidate_round > baseline_round + 1:
        return AcceptanceDecision("rejected", "review_round_exceeded", False, semantic_changed, presentation_changed, details)

    if (
        _content_digest(baseline_bundle) != _content_digest(candidate_bundle)
        and not _authorized_hierarchy_repair(baseline_bundle, candidate_bundle, decision_list)
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

    target_ids = _decision_targets(decision_list)
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
    review_packet: Mapping[str, Any] | None = None,
) -> tuple[AcceptanceDecision, dict[str, Any]]:
    """Return the accepted candidate bundle or an unchanged baseline copy."""

    decision_list = list(decisions or [])
    decision = evaluate_candidate(
        baseline_bundle,
        candidate_bundle,
        findings,
        decisions=decision_list,
        review_packet=review_packet,
    )
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
    "validate_review_decisions",
]
