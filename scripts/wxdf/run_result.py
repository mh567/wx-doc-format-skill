"""Conversion lifecycle data types."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .registry import capability_migration_report, execution_plan_report


@dataclass(frozen=True)
class ConversionRequest:
    input_path: Path
    output_path: Path
    template_path: Path | None = None
    pipeline: str = "modular"
    list_policy: str = "wx-default-v2"
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_path", Path(self.input_path))
        object.__setattr__(self, "output_path", Path(self.output_path))
        if self.template_path is not None:
            object.__setattr__(self, "template_path", Path(self.template_path))
        if self.pipeline != "modular":
            raise ValueError(f"unsupported pipeline {self.pipeline!r}")
        if self.list_policy != "wx-default-v2":
            raise ValueError(f"unsupported list policy {self.list_policy!r}")


@dataclass
class RunResult:
    status: str
    exit_code: int
    report: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    # Internal lifecycle control; never serialize into reports or public results.
    terminal_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "report": self.report,
            "artifacts": self.artifacts,
            "diagnostics": self.diagnostics,
            "metrics": self.metrics,
        }

    def as_report(self) -> dict[str, Any]:
        payload = dict(self.report)
        payload.update({
            "status": self.status,
            "exit_code": self.exit_code,
            "artifacts": dict(self.artifacts),
            "diagnostics": list(self.diagnostics),
            "metrics": dict(self.metrics),
        })
        return payload


def heading_outline_report(normalized: Mapping[str, Any]) -> dict[str, Any]:
    """Report the body headings normalization mapped onto the outline contract.

    The entry records the source semantic level next to the level the template
    renders, so a reader can tell a heading that kept its source level from one
    that was pulled up to keep the outline descending one level at a time.  The
    records live in the presentation outline, which the presentation digest
    covers.
    """

    presentation = normalized.get("presentation")
    outline = presentation.get("outline") if isinstance(presentation, Mapping) else None
    entries = [
        {
            "node_id": str(node_id),
            "source_level": record.get("source_level"),
            "outline_level": record.get("outline_level"),
        }
        for node_id, record in (outline or {}).items()
        if isinstance(record, Mapping) and isinstance(record.get("outline_level"), int)
    ]
    return {"normalized_count": len(entries), "entries": entries}


def build_native_run_result(
    request: Any,
    options: dict[str, Any],
    report: dict[str, Any],
    execution_plan: Any,
    context: Any,
    metrics: Any,
    *,
    profile: str,
) -> tuple[Any, dict[Any, bytes] | None]:
    """Translate committed native artifacts into the lifecycle result contract.

    Returns the translated ``RunResult`` together with the file payloads the
    kernel publication trigger must write.  ``None`` payloads mark a failed
    result that must not publish.
    """

    from .auditing import is_advisory_audit_issue
    from .decision_ledger import ledger_replay_report

    candidates = list(context.require("candidate_set"))
    enhancement = context.require("enhancement_result")
    decision_set = context.require("decision_set")
    list_candidates = list(context.require("list_semantics_candidates"))
    appendix_candidates = list(context.require("appendix_semantics_candidates"))
    appendix_semantics = context.require("appendix_semantics")
    list_result = context.require("list_semantics_result")
    review_result = context.require("review_result")
    committed = review_result["transaction_commit"]
    snapshot = committed.snapshot
    normalized = review_result["normalized"]
    selected_audits = review_result["audits"]
    model_findings = selected_audits["model"]
    saved_findings = selected_audits["saved_output"]
    appendix_audit = selected_audits["appendix"]
    published = context.require("published_output")
    integrity = context.require("source_facts_integrity")
    source_facts_digest = context.require("source_facts_digest")
    source_facts_digest_after = str(integrity["digest_after"])
    source_facts_immutable = bool(integrity["immutable"])
    ledger_replay = ledger_replay_report(context.require("ledger_replay"), decision_set)
    review_decision_set = review_result.get("decision_set") or {}
    transaction_decision_set = (
        review_decision_set
        if review_result.get("status") == "accepted"
        else decision_set
    )
    native_review = {
        key: copy.deepcopy(review_result.get(key))
        for key in (
            "status",
            "enabled",
            "triggered",
            "acceptance",
            "baseline_artifact",
            "candidate_artifact",
            "selected_artifact",
            "enhancement",
            "decision_set",
            "audit_exception",
            "reprocess_plan",
            "reprocess_module_evidence",
        )
        if review_result.get(key) is not None
    }

    appendix_range_candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in appendix_candidates
        if str(candidate.get("candidate_id", "")).startswith("appendix-range-")
    }
    appendix_heading_candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in appendix_candidates
        if str(candidate.get("candidate_id", "")).startswith("appendix-heading-")
    }
    appendix_decision_ids = {
        str(candidate_id)
        for decision in decision_set.get("decisions", [])
        for candidate_id in decision.get("candidate_ids", [])
    }
    appendix_consumption = {
        "candidate_count": len(appendix_candidates),
        "range_candidate_count": len(appendix_range_candidate_ids),
        "range_consumed_count": len(appendix_range_candidate_ids & appendix_decision_ids),
        "heading_candidate_count": len(appendix_heading_candidate_ids),
        "heading_consumed_count": len(appendix_heading_candidate_ids & appendix_decision_ids),
        "unmatched_count": len(appendix_range_candidate_ids | appendix_heading_candidate_ids) - len((appendix_range_candidate_ids | appendix_heading_candidate_ids) & appendix_decision_ids),
        "ambiguous_count": len(decision_set.get("unresolved") or []),
    }

    diagnostics = list(list_result.get("diagnostics") or [])
    if not source_facts_immutable:
        diagnostics.append({
            "code": "SOURCE_FACTS_MUTATED",
            "severity": "error",
            "message": "SourceFacts changed during native pipeline execution",
        })
    diagnostics.extend(decision_set.get("unresolved") or [])
    diagnostics.extend(model_findings.get("issues") or [])
    diagnostics.extend(saved_findings.get("issues") or [])
    diagnostics.extend(appendix_audit.get("issues") or [])
    audits_passed = (
        model_findings.get("passed") is True
        and saved_findings.get("passed") is True
        and appendix_audit.get("passed") is True
    )
    blocking_diagnostics = [
        item
        for item in diagnostics
        if item in (decision_set.get("unresolved") or [])
        or item in (saved_findings.get("issues") or [])
        or item in (appendix_audit.get("issues") or [])
        or (
            item.get("severity") in {"error", "warning"}
            # A finding whose defect the delivered document already resolved is
            # reported and repairable without blocking the delivery, so the
            # severity alone cannot decide when the code is advisory.
            and not is_advisory_audit_issue(item)
        )
        or (
            item in (model_findings.get("issues") or [])
            # A model finding whose defect the normalization already resolved in
            # the rendered model is reported and repairable without blocking the
            # delivery: the document carries the contract-conforming level
            # either way.
            and not is_advisory_audit_issue(item)
        )
    ]
    if published is None or not audits_passed or blocking_diagnostics:
        report.update({
            "status": "failed",
            "source_ast_revision": snapshot.ast["revision"],
            "source_facts_digest": source_facts_digest,
            "source_facts_digest_after": source_facts_digest_after,
            "source_facts_immutable": source_facts_immutable,
            "decision_set": decision_set,
            "enhancement": enhancement,
            "decision_ledger_replay": ledger_replay,
            "model_audit": model_findings,
            "saved_output_audit": saved_findings,
            "review_packet": context.require("review_packet"),
            "native_review": native_review,
            "appendix_semantics": appendix_semantics,
            "appendix_semantics_consumption": appendix_consumption,
            "appendix_preservation_audit": appendix_audit,
            "execution_plan": {
                "profile": profile,
                "modules": execution_plan_report(execution_plan),
            },
        })
        return RunResult(
            status="failed",
            exit_code=1,
            report=report,
            diagnostics=diagnostics,
            metrics=metrics.snapshot(),
        ), None
    capability_migration = capability_migration_report(candidates, decision_set, committed, audits_passed)
    item_candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in list_candidates
        if str(candidate.get("candidate_id", "")).startswith("list-relation-")
    }
    continuation_candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in list_candidates
        if ((candidate.get("proposal") or {}).get("value") or {}).get("association", {}).get("kind") == "sequence_continuation"
    }
    anchor_candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in list_candidates
        if str(candidate.get("candidate_id", "")).startswith("list-anchor-")
    }
    item_consumed_count = sum(
        1
        for decision in decision_set.get("decisions", [])
        if item_candidate_ids.intersection(str(item) for item in decision.get("candidate_ids", []))
    )
    continuation_consumed_count = sum(
        1
        for decision in decision_set.get("decisions", [])
        if continuation_candidate_ids.intersection(str(item) for item in decision.get("candidate_ids", []))
    )
    anchor_consumed_count = sum(
        1
        for decision in decision_set.get("decisions", [])
        if anchor_candidate_ids.intersection(str(item) for item in decision.get("candidate_ids", []))
    )
    generated_title = None
    structure_evidence = (context.evidence or {}).get("document-structure")
    if isinstance(structure_evidence, Mapping):
        generated_title = structure_evidence.get("generated_title")
    if generated_title:
        # The title came from the file name, not from the source, so the reader
        # is told to check it.
        report["risk_warnings"] = [
            *list(report.get("risk_warnings") or []),
            {
                "type": "markdown_title_generated",
                "message": "Markdown had no leading H1; filename title was generated.",
                "title": str(generated_title),
            },
        ]
    status = "completed" if not diagnostics else "completed_with_warnings"
    report.update({
        "status": status,
        "source_facts_summary": context.require("source_facts_summary"),
        "source_facts_digest": source_facts_digest,
        "source_facts_digest_after": source_facts_digest_after,
        "source_facts_immutable": source_facts_immutable,
        "source_ast_revision": snapshot.ast["revision"],
        "semantic_digest": normalized["semantic_digest"],
        "presentation_digest": normalized["presentation_digest"],
        "heading_outline": heading_outline_report(normalized),
        "source_ast_hierarchy": context.require("source_ast_hierarchy"),
        "list_semantics": {
            "list_count": len(list_result["lists"]),
            "item_count": len(list_result["items"]),
            "continuation_count": len(list_result["continuations"]),
            "diagnostics": list(list_result["diagnostics"]),
            "downgrades": list(list_result.get("downgrades") or []),
            "groups": list(list_result["lists"]),
        },
        "list_semantics_consumption": {
            "candidate_count": len(list_candidates),
            "item_candidate_count": len(list_result["items"]),
            "item_consumed_count": item_consumed_count,
            "continuation_candidate_count": len(list_result["continuations"]),
            "continuation_consumed_count": continuation_consumed_count,
            "anchor_candidate_count": len(anchor_candidate_ids),
            "anchor_consumed_count": anchor_consumed_count,
            "unmatched_count": 0,
            "ambiguous_count": len(decision_set.get("unresolved") or []),
        },
        "table_semantics": dict(context.require("table_semantics")),
        "appendix_semantics": appendix_semantics,
        "appendix_semantics_consumption": appendix_consumption,
        "appendix_preservation_audit": appendix_audit,
        "native_transaction": {
            "status": committed.status,
            "transaction_id": committed.transaction_id,
            "decision_count": len(transaction_decision_set["decisions"]),
            "unresolved_count": len(transaction_decision_set.get("unresolved") or []),
            "expected_revision": transaction_decision_set["expected_revision"],
            "committed_revision": snapshot.ast["revision"],
            "metrics": committed.metrics,
            "overlays": snapshot.overlays,
            "presentation_overrides": snapshot.presentation_overrides,
        },
        "decision_set": decision_set,
        "review_packet": context.require("review_packet"),
        "native_review": native_review,
        "enhancement": enhancement,
        "decision_ledger_replay": ledger_replay,
        "execution_plan": {
            "profile": profile,
            "modules": execution_plan_report(execution_plan),
        },
        "native_module_evidence": copy.deepcopy(context.evidence),
        "model_audit": model_findings,
        "saved_output_audit": saved_findings,
        "capability_migration": {
            **capability_migration,
        },
    })
    artifacts = {"docx": str(request.output_path)}
    publication_payloads: dict[Any, bytes] = {}
    for option_key, artifact_key, payload in (
        ("source_ast_path", "source_ast", snapshot.ast),
        ("normalized_ast_path", "normalized_ast", normalized),
    ):
        path = options.get(option_key)
        if path is not None and not options.get("release"):
            path = Path(path)
            publication_payloads[path] = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            artifacts[artifact_key] = str(path)
    report_path = options.get("report_path")
    if report_path is not None:
        report_path = Path(report_path)
        artifacts["report"] = str(report_path)
    report["artifacts"] = artifacts
    result = RunResult(status=status, exit_code=0, report=report, artifacts=artifacts, diagnostics=diagnostics, metrics=metrics.snapshot())
    if report_path is not None:
        report_payload = result.as_report()
        report_transform = options.get("report_transform")
        if callable(report_transform):
            report_payload = report_transform(report_payload)
        publication_payloads[report_path] = json.dumps(
            report_payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    return result, publication_payloads
