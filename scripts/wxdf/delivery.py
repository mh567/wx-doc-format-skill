"""Delivery policy shared by the native publisher and run result builder."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from zipfile import ZipFile


def valid_docx(path: Path | str) -> tuple[bool, str | None]:
    """Check that an artifact is a readable DOCX package."""

    candidate = Path(path)
    if not candidate.is_file():
        return False, "artifact_missing"
    try:
        with ZipFile(candidate) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                return False, "zip_member_corrupt"
        from docx import Document

        Document(candidate)
    except Exception as exc:
        return False, f"docx_unreadable:{type(exc).__name__}"
    return True, None


def audit_state(audits: Mapping[str, Any]) -> str:
    core_names = ("model", "saved_output", "appendix")
    states = [
        audits.get(name, {}).get("passed")
        if isinstance(audits.get(name), Mapping)
        else None
        for name in core_names
    ]
    states.extend(
        audit.get("passed")
        for name, audit in audits.items()
        if name not in core_names and isinstance(audit, Mapping) and "passed" in audit
    )
    states.extend(
        nested.get("passed")
        for audit in audits.values() if isinstance(audit, Mapping)
        for nested in (audit.get("advisory_audits") or {}).values()
        if isinstance(nested, Mapping) and "passed" in nested
    )
    if any(value is None for value in states):
        return "incomplete"
    if any(value is False for value in states) or any(
        isinstance(audit, Mapping) and (audit.get("issues") or audit.get("format_issues") or any(
            isinstance(nested, Mapping) and nested.get("issues")
            for nested in (audit.get("advisory_audits") or {}).values()
        ))
        for audit in audits.values()
    ):
        return "issues"
    return "passed"


def audit_findings(audits: Mapping[str, Any], render_map: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for name, audit in audits.items():
        if not isinstance(audit, Mapping) or "passed" not in audit:
            continue
        if audit.get("passed") is None and not audit.get("issues"):
            findings.append({"code": "audit_incomplete", "category": str(name), "impact": "unknown", "certainty": "incomplete", "evidence_level": "incomplete", "severity": "warning", "message": "Audit did not complete."})
        elif audit.get("passed") is False and not audit.get("issues"):
            findings.append({"category": str(name), "impact": "unknown", "certainty": "reported", "evidence_level": "reported", "severity": "warning", "code": "audit_failed", "message": "Audit found an issue without a detailed finding."})
        for raw in audit.get("issues") or []:
            item = dict(raw) if isinstance(raw, Mapping) else {"message": str(raw)}
            code = str(item.get("code") or "")
            known_audit = str(name) in {"model", "saved_output", "appendix", "document_format", "page_contract", "list_readback"}
            known_code = code == "audit_exception" or any(token in code for token in ("changed", "missing", "lost", "mismatch")) or code.startswith(("heading_", "source_heading", "list_", "unexpected_style", "format_", "page_", "toc_", "appendix_", "caption_", "table_", "ast_", "opaque_", "paragraph_", "formula_", "bookmark_"))
            item.setdefault("category", str(name) if known_audit and known_code else "audit")
            explicit_loss = item.get("loss_confirmed") is True or item.get("certainty") == "confirmed"
            if "impact" not in item:
                if explicit_loss:
                    item["impact"] = "content_loss"
                elif code == "audit_exception":
                    item["impact"] = "unknown"
                elif any(token in code for token in ("changed", "missing", "lost", "mismatch")):
                    item["impact"] = "possible_content_change"
                elif known_audit and known_code:
                    item["impact"] = "format_or_semantic"
                else:
                    item["impact"] = "unknown"
            if "certainty" not in item:
                item["certainty"] = "confirmed" if explicit_loss else "suspected" if item["impact"] == "possible_content_change" else "incomplete" if item["impact"] == "unknown" and code == "audit_exception" else "reported"
            item.setdefault("evidence_level", item["certainty"])
            item.setdefault("severity", "high" if item["impact"] == "content_loss" else "medium" if item["impact"] == "format_or_semantic" else "warning")
            if item.get("block_id") is None:
                for key in ("node_id", "target_id"):
                    if item.get(key) is not None:
                        item["block_id"] = str(item[key])
                        break
            if item.get("location") is None and item.get("block_id") is not None:
                rendered = (render_map or {}).get(str(item["block_id"]), {})
                paragraph = rendered.get("paragraph") if isinstance(rendered, Mapping) else None
                item["location"] = f"第{paragraph}段" if isinstance(paragraph, int) else str(item["block_id"])
            findings.append(item)
        for advisory_name, advisory in (audit.get("advisory_audits") or {}).items():
            if not isinstance(advisory, Mapping):
                continue
            for raw in advisory.get("issues") or []:
                item = dict(raw) if isinstance(raw, Mapping) else {"message": str(raw)}
                item.setdefault("category", str(advisory_name))
                item.setdefault("impact", "unknown" if item.get("code") == "audit_exception" else "format_or_semantic")
                item.setdefault("certainty", "incomplete" if item.get("code") == "audit_exception" else "reported")
                item.setdefault("evidence_level", item["certainty"])
                item.setdefault("severity", "warning" if item["impact"] == "unknown" else "medium")
                if item.get("block_id") is None:
                    for key in ("node_id", "target_id"):
                        if item.get(key) is not None:
                            item["block_id"] = str(item[key])
                            break
                if item.get("location") is None and item.get("block_id") is not None:
                    rendered = (render_map or {}).get(str(item["block_id"]), {})
                    paragraph = rendered.get("paragraph") if isinstance(rendered, Mapping) else None
                    item["location"] = f"第{paragraph}段" if isinstance(paragraph, int) else str(item["block_id"])
                findings.append(item)
        for raw in (audit.get("format_issues") or []) if not audit.get("advisory_audits") else []:
            item = dict(raw) if isinstance(raw, Mapping) else {"message": str(raw)}
            item.setdefault("category", "document_format")
            item.setdefault("impact", "format_or_semantic")
            item.setdefault("certainty", "reported")
            item.setdefault("evidence_level", "reported")
            item.setdefault("severity", "medium")
            if item.get("block_id") is None:
                for key in ("node_id", "target_id"):
                    if item.get(key) is not None:
                        item["block_id"] = str(item[key])
                        break
            if item.get("location") is None and item.get("block_id") is not None:
                item["location"] = str(item["block_id"])
            findings.append(item)
    return findings


def decide_delivery(
    policy: str,
    audits: Mapping[str, Any],
    *,
    artifact: Path | str | None,
    source_facts_immutable: bool,
    unresolved: list[Any],
    transaction_status: str,
    acceptance: Mapping[str, Any] | None = None,
    strict_diagnostics: list[Any] | None = None,
    render_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce the single delivery decision consumed by publication and reporting."""

    if policy not in {"strict", "desktop"}:
        policy = "strict"
    state = audit_state(audits)
    hard_blocks: list[str] = []
    selected = Path(artifact) if artifact is not None else None
    if selected is None:
        hard_blocks.append("artifact_unavailable")
    else:
        valid, reason = valid_docx(selected)
        if not valid:
            hard_blocks.append(reason or "artifact_invalid")
    if not source_facts_immutable:
        hard_blocks.append("SOURCE_FACTS_MUTATED")
    if unresolved:
        hard_blocks.append("unresolved_transaction")
    if transaction_status not in {"committed", "already_committed", "accepted"}:
        hard_blocks.append("transaction_not_committed")
    audit_gate = any(
        not isinstance(audits.get(name), Mapping)
        or audits[name].get("passed") is not True
        for name in ("model", "saved_output", "appendix")
    )
    required_audits_passed = not audit_gate
    if policy == "strict" and (audit_gate or state == "incomplete"):
        hard_blocks.append("strict_audit_gate")
    if policy == "strict" and acceptance is not None and acceptance.get("accepted") is not True:
        hard_blocks.append("review_acceptance_rejected")
    if policy == "strict" and strict_diagnostics:
        hard_blocks.append("strict_diagnostic")
    publish = not hard_blocks
    conversion_failures = {"artifact_unavailable", "artifact_missing", "zip_member_corrupt", "SOURCE_FACTS_MUTATED", "unresolved_transaction", "transaction_not_committed"}
    conversion_status = "failed" if any(item in conversion_failures or item.startswith("docx_unreadable") for item in hard_blocks) else "completed"
    return {
        "policy": policy,
        "audit_state": state,
        "required_audits_passed": required_audits_passed,
        "conversion_status": conversion_status,
        "delivery_status": "published" if publish else "blocked",
        "publish": publish,
        "selected_artifact": str(selected) if publish and selected is not None else None,
        "hard_blocks": hard_blocks,
        "findings": audit_findings(audits, render_map),
    }
