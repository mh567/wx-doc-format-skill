from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REVIEW_CONFIDENCE_THRESHOLD = 0.85
SUGGESTION_CONFIDENCE_THRESHOLD = 0.70

SEVERITY_WEIGHT = {
    "critical": 1000,
    "high": 100,
    "medium": 10,
    "low": 1,
}

SEMANTIC_ALLOWED_OPS = frozenset({
    "retype",
    "adjust_level",
    "set_restart",
    "set_table_type",
    "set_caption_type",
    "set_header_rows",
})


@dataclass(frozen=True)
class AuditScore:
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    invariant_failures: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "critical": self.critical,
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
            "invariant_failures": self.invariant_failures,
            "weighted": (
                self.critical * SEVERITY_WEIGHT["critical"]
                + self.high * SEVERITY_WEIGHT["high"]
                + self.medium * SEVERITY_WEIGHT["medium"]
                + self.low * SEVERITY_WEIGHT["low"]
                + self.invariant_failures * SEVERITY_WEIGHT["critical"]
            ),
        }

    def ordering_key(self) -> tuple[int, int, int, int, int]:
        return (
            self.invariant_failures,
            self.critical,
            self.high,
            self.medium,
            self.low,
        )


def _blocks_by_id(model: dict | None) -> dict[str, dict]:
    return {
        str(block["id"]): block
        for block in (model or {}).get("document", {}).get("blocks", [])
        if block.get("id")
    }


def _finding(
    audit_id: str,
    issue: dict,
    *,
    severity: str,
    category: str,
    repairable: bool,
    block_id: str | None = None,
    allowed_ops: Iterable[str] = (),
) -> dict[str, Any]:
    result = {
        "audit_id": audit_id,
        "severity": severity,
        "category": category,
        "repairable": bool(repairable and block_id),
        "message": str(issue.get("message") or issue.get("type") or audit_id),
        "evidence": issue,
        "allowed_ops": sorted(set(allowed_ops)) if repairable and block_id else [],
    }
    if block_id:
        result["block_id"] = str(block_id)
    return result


def collect_audit_findings(report: dict, model: dict | None) -> list[dict[str, Any]]:
    """Normalize heterogeneous audit output into a stable finding contract."""
    blocks = _blocks_by_id(model)
    ordered_blocks = (model or {}).get("document", {}).get("blocks", [])
    findings: list[dict[str, Any]] = []

    model_issue_ops = {
        "heading_missing_level": {"adjust_level"},
        "list_item_unknown_type": {"retype"},
        "table_unknown_type": {"set_table_type"},
        "code_sample_has_header_rows": {"set_header_rows"},
    }
    for index, issue in enumerate(report.get("document_model_issues", [])):
        block_index = int(issue.get("block") or 0) - 1
        block_id = None
        if 0 <= block_index < len(ordered_blocks):
            block_id = str(ordered_blocks[block_index].get("id") or "") or None
        issue_type = str(issue.get("type") or "document_model_issue")
        ops = model_issue_ops.get(issue_type, set())
        findings.append(_finding(
            f"document_model:{index}:{issue_type}", issue,
            severity="high",
            category="semantic" if ops else "deterministic_format",
            repairable=bool(ops) and block_id in blocks,
            block_id=block_id,
            allowed_ops=ops,
        ))

    table_semantics = report.get("table_semantics_audit", {})
    for index, issue in enumerate(table_semantics.get("issues", [])):
        block_id = str(issue.get("block_id") or "") or None
        issue_type = issue.get("type")
        repairable = issue_type == "unknown_table_semantics"
        ops = {"set_table_type"} if repairable else set()
        findings.append(_finding(
            f"table_semantics:{index}:{issue_type}", issue,
            severity="high" if issue_type == "unknown_table_semantics" else "medium",
            category="semantic",
            repairable=repairable and block_id in blocks,
            block_id=block_id,
            allowed_ops=ops,
        ))

    caption_model = report.get("caption_placement_model_audit", {})
    for index, issue in enumerate(caption_model.get("issues", [])):
        block_id = str(issue.get("caption_id") or issue.get("object_id") or "") or None
        findings.append(_finding(
            f"caption_model:{index}:{issue.get('type')}", issue,
            severity="high",
            category="semantic",
            repairable=False,
            block_id=block_id,
        ))

    list_audit = report.get("list_preservation_audit", {})
    list_fields = {
        "source_list_body_residue": ("high", {"retype"}),
        "list_level_jumps": ("medium", {"adjust_level"}),
        "isolated_ast_list_items": ("medium", {"retype"}),
        "protected_role_conflicts": ("high", {"retype"}),
    }
    for field, (severity, ops) in list_fields.items():
        for index, raw_issue in enumerate(list_audit.get(field, [])):
            issue = raw_issue if isinstance(raw_issue, dict) else {"block_id": raw_issue, "type": field}
            block_id = str(issue.get("block_id") or "") or None
            findings.append(_finding(
                f"list:{field}:{index}", issue,
                severity=severity,
                category="semantic",
                repairable=block_id in blocks,
                block_id=block_id,
                allowed_ops=ops,
            ))

    semantic_groups = report.get("parse_report", {}).get("semantic_list_groups", [])
    for group_index, group in enumerate(semantic_groups):
        if group.get("status") != "review":
            continue
        for item_index, raw_block_id in enumerate(group.get("item_block_ids", [])):
            block_id = str(raw_block_id or "") or None
            block = blocks.get(block_id or "", {})
            if block.get("block_type") != "body":
                continue
            issue = {
                "type": "unresolved_parallel_group",
                "message": "并列内容组需要语义复核。",
                **group,
            }
            findings.append(_finding(
                f"semantic_list_group:{group_index}:{item_index}", issue,
                severity="medium",
                category="semantic",
                repairable=True,
                block_id=block_id,
                allowed_ops={"retype"},
            ))

    audit = report.get("audit", {})
    heading_text_ids: dict[str, list[str]] = {}
    for block in ordered_blocks:
        if block.get("block_type") == "heading" and block.get("id"):
            text = str(block.get("text") or "").strip()
            heading_text_ids.setdefault(text, []).append(str(block["id"]))
    for index, issue in enumerate(audit.get("heading_hierarchy_warnings", [])):
        matches = heading_text_ids.get(str(issue.get("text") or "").strip(), [])
        block_id = matches[0] if len(matches) == 1 else None
        findings.append(_finding(
            f"output:heading_hierarchy_warnings:{index}", issue,
            severity="medium",
            category="semantic",
            repairable=block_id in blocks,
            block_id=block_id,
            allowed_ops={"adjust_level"},
        ))

    deterministic_fields = {
        "heading_paragraphs_without_numbering": "high",
        "heading_text_still_has_manual_number": "high",
        "ordered_list_nums_without_restart": "high",
        "table_paragraphs_not_table_body": "high",
        "table_rows_bad_height": "high",
        "markdown_residue": "medium",
    }
    for field, severity in deterministic_fields.items():
        for index, issue in enumerate(audit.get(field, [])):
            evidence = issue if isinstance(issue, dict) else {"value": issue}
            findings.append(_finding(
                f"output:{field}:{index}", evidence,
                severity=severity,
                category="deterministic_format",
                repairable=False,
            ))

    invariant_audits = {
        "output_structure_audit": "critical",
        "toc_replacement_audit": "critical",
        "caption_placement_audit": "high",
    }
    for key, severity in invariant_audits.items():
        value = report.get(key, {})
        if value and value.get("passed") is False:
            issues = value.get("issues") or [{"type": key, "message": f"{key} failed"}]
            for index, issue in enumerate(issues):
                findings.append(_finding(
                    f"invariant:{key}:{index}", issue,
                    severity=severity,
                    category="deterministic_format",
                    repairable=False,
                ))

    table_contract = audit.get("table_format_contract", {})
    if table_contract and table_contract.get("passed") is False:
        findings.append(_finding(
            "invariant:table_format_contract", {"type": "table_format_contract"},
            severity="critical",
            category="deterministic_format",
            repairable=False,
        ))

    unexpected = report.get("template_finalizer", {}).get("style_audit", {}).get("unexpected_styles", [])
    if unexpected:
        findings.append(_finding(
            "invariant:unexpected_styles", {"type": "unexpected_styles", "styles": unexpected},
            severity="critical",
            category="deterministic_format",
            repairable=False,
        ))

    return findings


def audit_score(findings: Iterable[dict[str, Any]]) -> AuditScore:
    counts = {name: 0 for name in SEVERITY_WEIGHT}
    invariant_failures = 0
    for finding in findings:
        severity = str(finding.get("severity") or "low")
        counts[severity if severity in counts else "low"] += 1
        if str(finding.get("audit_id") or "").startswith("invariant:"):
            invariant_failures += 1
    return AuditScore(invariant_failures=invariant_failures, **counts)


def is_strict_improvement(before: AuditScore, after: AuditScore) -> bool:
    return after.ordering_key() < before.ordering_key()


def build_review_packet(report: dict, model: dict, *, context_radius: int = 2) -> dict[str, Any]:
    findings = collect_audit_findings(report, model)
    repairable = [item for item in findings if item.get("repairable")]
    blocks = model.get("document", {}).get("blocks", [])
    indexes = {str(block.get("id")): index for index, block in enumerate(blocks) if block.get("id")}
    targets = []
    for finding in repairable:
        block_id = str(finding["block_id"])
        index = indexes[block_id]
        start = max(0, index - context_radius)
        end = min(len(blocks), index + context_radius + 1)
        context = []
        for block in blocks[start:end]:
            context.append({
                key: block.get(key)
                for key in ("id", "block_type", "role", "level", "list_type", "table_type", "text", "source")
                if block.get(key) not in (None, "", {})
            })
        targets.append({**finding, "context": context})
    return {
        "schema_version": "1.0",
        "skill_version": report.get("skill_version", "unknown"),
        "baseline_score": audit_score(findings).as_dict(),
        "finding_count": len(findings),
        "repairable_count": len(repairable),
        "targets": targets,
    }


def validate_review_patch(patch: dict, model: dict, report: dict) -> list[dict[str, Any]]:
    packet = build_review_packet(report, model)
    targets: dict[str, list[dict[str, Any]]] = {}
    for item in packet["targets"]:
        targets.setdefault(str(item["block_id"]), []).append(item)
    blocks = _blocks_by_id(model)
    errors: list[dict[str, Any]] = []
    seen_blocks: set[str] = set()
    for index, decision in enumerate(patch.get("decisions", [])):
        block_id = str(decision.get("block_id") or "")
        block_targets = targets.get(block_id, [])
        if not block_targets:
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": "document_review may only modify blocks referenced by repairable audit findings",
            })
            continue
        if block_id in seen_blocks:
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": "document_review allows at most one decision per block in one round",
            })
            continue
        seen_blocks.add(block_id)
        operation = decision.get("operation")
        matching_targets = [
            target for target in block_targets
            if operation in target.get("allowed_ops", [])
        ]
        if not matching_targets:
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": f"operation {operation!r} is not allowed for this audit finding",
            })
            continue
        block = blocks.get(block_id, {})
        expected_types = {
            "set_restart": "list_item",
            "set_table_type": "table",
            "set_header_rows": "table",
            "set_caption_type": "caption",
        }
        expected_type = expected_types.get(str(operation))
        if expected_type and block.get("block_type") != expected_type:
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": f"{operation} requires a {expected_type} block",
            })
        if operation == "adjust_level" and block.get("block_type") not in {"heading", "list_item"}:
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": "adjust_level requires a heading or list_item block",
            })
        before = decision.get("from")
        required_from_key = {
            "retype": "block_type",
            "adjust_level": "level",
            "set_restart": "restart",
            "set_table_type": "table_type",
            "set_header_rows": "header_rows",
            "set_caption_type": "caption_type",
        }.get(str(operation))
        if not isinstance(before, dict) or required_from_key not in before:
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": f"document_review requires from.{required_from_key}",
            })
            before = {}
        for key, value in before.items():
            if block.get(key) != value:
                errors.append({
                    "decision_index": index,
                    "block_id": block_id,
                    "message": f"stale from value for {key!r}",
                })
        issue_types = {
            str(target.get("evidence", {}).get("type") or "")
            for target in matching_targets
        }
        to_value = decision.get("to") if isinstance(decision.get("to"), dict) else {}
        if operation == "set_table_type":
            destination = to_value.get("table_type")
            if not issue_types & {"unknown_table_semantics", "table_unknown_type"}:
                errors.append({
                    "decision_index": index,
                    "block_id": block_id,
                    "message": "set_table_type is limited to unknown table semantics",
                })
            elif block.get("table_type") != "unknown" or destination == "unknown":
                errors.append({
                    "decision_index": index,
                    "block_id": block_id,
                    "message": "unknown tables must be classified into a concrete table type",
                })
        elif operation == "set_header_rows" and to_value.get("header_rows") != 0:
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": "code_sample_has_header_rows may only be repaired by setting header_rows to 0",
            })
        elif operation == "retype":
            destination = to_value.get("block_type")
            allowed_destinations: set[str] = set()
            for issue_type in issue_types:
                allowed_destinations.update({
                    "source_list_body_residue": {"list_item"},
                    "list_item_unknown_type": {"list_item"},
                    "isolated_ast_list_items": {"body", "heading"},
                    "protected_role_conflicts": {"body", "heading", "caption"},
                    "unresolved_parallel_group": {"list_item"},
                }.get(issue_type, set()))
            if destination not in allowed_destinations:
                errors.append({
                    "decision_index": index,
                    "block_id": block_id,
                    "message": f"retype target {destination!r} is not valid for this finding",
                })
        confidence = decision.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or confidence < REVIEW_CONFIDENCE_THRESHOLD
        ):
            errors.append({
                "decision_index": index,
                "block_id": block_id,
                "message": f"document_review mutation requires confidence >= {REVIEW_CONFIDENCE_THRESHOLD}",
            })
    return errors


def _finding_identity(finding: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(finding.get("block_id") or ""),
        str(finding.get("category") or ""),
        str(finding.get("evidence", {}).get("type") or finding.get("audit_id") or ""),
    )


def _semantic_signature(block: dict[str, Any]) -> str:
    fields = {
        key: block.get(key)
        for key in (
            "block_type", "role", "level", "list_type", "restart",
            "table_type", "header_rows", "caption_type", "association",
            "caption_id", "numbering",
        )
        if key in block
    }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_fingerprint(model: dict, *, allow_generated_caption_text: bool = False) -> str:
    records = []
    for block in model.get("document", {}).get("blocks", []):
        if block.get("_auto_generated") and not str(block.get("text") or ""):
            continue
        row_content = []
        for row in block.get("rows", []) or []:
            row_content.append([
                cell.get("text") if isinstance(cell, dict) else str(cell)
                for cell in row
            ])
        record: dict[str, Any] = {
            "id": block.get("id"),
            "title": block.get("title"),
            "asset_id": block.get("asset_id"),
            "rows": row_content or None,
        }
        if not (allow_generated_caption_text and block.get("_auto_generated")):
            record["text"] = block.get("text")
        records.append(record)
    raw = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def accepted_candidate(
    baseline_report: dict,
    candidate_report: dict,
    baseline_model: dict,
    candidate_model: dict,
    decisions: Iterable[dict[str, Any]] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    before_findings = collect_audit_findings(baseline_report, baseline_model)
    after_findings = collect_audit_findings(candidate_report, candidate_model)
    before_score = audit_score(before_findings)
    after_score = audit_score(after_findings)
    details = {
        "before_score": before_score.as_dict(),
        "after_score": after_score.as_dict(),
        "before_finding_ids": [item["audit_id"] for item in before_findings],
        "after_finding_ids": [item["audit_id"] for item in after_findings],
    }
    if content_fingerprint(baseline_model, allow_generated_caption_text=True) != content_fingerprint(
        candidate_model, allow_generated_caption_text=True,
    ):
        return False, "content_fingerprint_changed", details
    if decisions is not None:
        target_ids = {
            str(decision.get("block_id") or "")
            for decision in decisions
            if decision.get("block_id")
        }
        before_target_findings = {
            _finding_identity(item)
            for item in before_findings
            if item.get("repairable") and str(item.get("block_id") or "") in target_ids
        }
        after_identities = {_finding_identity(item) for item in after_findings}
        unresolved_targets = sorted(before_target_findings & after_identities)
        details["unresolved_target_findings"] = [list(item) for item in unresolved_targets]
        if unresolved_targets:
            return False, "target_findings_not_resolved", details

        baseline_blocks = _blocks_by_id(baseline_model)
        candidate_blocks = _blocks_by_id(candidate_model)
        unrelated_changes = sorted(
            block_id
            for block_id, block in baseline_blocks.items()
            if block_id not in target_ids
            and (
                block_id not in candidate_blocks
                or _semantic_signature(block) != _semantic_signature(candidate_blocks[block_id])
            )
        )
        details["unrelated_semantic_changes"] = unrelated_changes
        if unrelated_changes:
            return False, "unrelated_semantic_fields_changed", details
    if after_score.critical > before_score.critical or after_score.high > before_score.high:
        return False, "new_or_increased_high_risk_findings", details
    if not is_strict_improvement(before_score, after_score):
        return False, "audit_score_did_not_improve", details
    return True, "audit_score_improved", details


def unknown_pattern_packet(report: dict, model: dict, *, input_hash: str | None = None) -> dict[str, Any]:
    packet = build_review_packet(report, model)
    unresolved = collect_audit_findings(report, model)
    return {
        "schema_version": "1.0",
        "skill_version": report.get("skill_version", "unknown"),
        "input_hash": input_hash,
        "unresolved_findings": unresolved,
        "review_summary": {
            "finding_count": packet["finding_count"],
            "repairable_count": packet["repairable_count"],
            "score": packet["baseline_score"],
        },
    }


def write_unknown_pattern_packet(path: Path, packet: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
