"""LLM semantic enhancement core module.

Provides JSON extraction, schema validation, patch application,
suspicion scoring, phase-based enhancement decisions, and a
compatibility wrapper for the legacy ``list_semantic_enhancer``
interface.

All public functions are pure (or nearly pure) — the sole side-effect
dependency is *llm_call*, injected by the caller.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import time
from typing import Any, Callable


# ── Constants ──────────────────────────────────────────────────────────

ALLOWED_OPS_BY_PHASE: dict[str, set[str]] = {
    "A": {"retype"},
    "B": {"retype", "adjust_level", "set_restart"},
    "C": {"retype", "set_table_type", "set_header_rows", "set_caption_type",
          "set_caption_text"},
}

VALID_BLOCK_TYPES = frozenset({
    "heading", "body", "list_item", "caption", "table", "image",
    "appendix", "unknown",
})

PATCH_SCHEMA_VERSION = "1.0"
PHASE_NAMES = frozenset({"A", "B", "C"})

AUTO_TRIGGER_THRESHOLD = 0.15
LOW_CONFIDENCE_THRESHOLD = 0.70

LLM_CALL_TIMEOUT = 30  # seconds (base — dynamic in _resolve_llm_call)

PHASE_B_SECTION_BATCH_SIZE = 20
PHASE_C_TABLE_BATCH_SIZE = 10

# Weights for the 7-dimension suspicion score (must sum to 1.0).
_SUSPICION_WEIGHTS: list[tuple[str, float]] = [
    ("suspect_visual_headings", 0.25),
    ("ambiguous_short_paragraphs", 0.20),
    ("inferred_headings", 0.15),
    ("inferred_lists", 0.10),
    ("unstyled_paragraphs", 0.15),
    ("table_density", 0.10),
    ("image_density", 0.05),
]

# Module-level thread-pool for time-bounded LLM calls.
_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)


# ═══════════════════════════════════════════════════════════════════════
# 0.  Token budget helpers
# ═══════════════════════════════════════════════════════════════════════

def _apply_token_budget(prompt: str, max_chars: int = 12000) -> str:
    """Truncate *prompt* to fit within *max_chars*.

    Keeps the instruction / prefix (front) and the JSON example section
    (back), collapsing the middle block-listing area with a ``…`` marker
    when the budget is exceeded.
    """
    if len(prompt) <= max_chars:
        return prompt
    # Keep ~40 % from the front and ~40 % from the back.
    front_len = int(max_chars * 0.40)
    back_len = max_chars - front_len - 3  # 3 chars for the '…'
    return prompt[:front_len] + "\n…\n" + prompt[-back_len:]


def _resolve_llm_call(
    phase: str,
    llm_call: Callable[[str], str] | None,
    *,
    block_count: int = 0,
    report_enh: dict | None = None,
) -> Callable[[str], str] | None:
    """Wrap *llm_call* with a dynamic per-phase timeout and model-selection hint.

    Timeout is computed as ``max(30, block_count / 20)`` so large documents
    (1000+ blocks) get proportionally more time.

    Parameters
    ----------
    phase:
        One of ``"A"``, ``"B"``, ``"C"``.
    llm_call:
        The raw LLM callable.
    block_count:
        Number of blocks in the document model — used to compute timeout.
    report_enh:
        The ``report['llm_enhancer']`` dict (if provided the timeout value
        is recorded for diagnostics).
    """
    if llm_call is None:
        return None

    # Read environment variables for model selection metadata.
    _model_env = os.environ.get(
        "LLM_MODEL_PHASE_A" if phase == "A" else "LLM_MODEL_PHASE_BC",
        os.environ.get("LLM_MODEL", ""),
    )

    # Dynamic timeout: max(60, block_count * 2) seconds
    timeout = max(60, block_count * 2) if block_count > 0 else LLM_CALL_TIMEOUT
    if report_enh is not None:
        report_enh.setdefault("llm_call_timeout", timeout)
        report_enh.setdefault("llm_call_block_count", block_count)

    def _timed_call(prompt: str) -> str:
        future = _LLM_EXECUTOR.submit(llm_call, prompt)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"LLM call timed out after {timeout}s "
                f"(phase={phase}, block_count={block_count})"
            )

    return _timed_call


# ═══════════════════════════════════════════════════════════════════════
# 0b.  Batch helpers for Phase B / C
# ═══════════════════════════════════════════════════════════════════════


def _iter_batches(items: list, size: int) -> list[list]:
    """Split *items* into batches of at most *size*."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _collect_phase_b_sections(model: dict) -> list[dict]:
    """Split model blocks into sections by heading boundaries.

    Returns a list of section dicts with keys ``heading_text`` and
    ``blocks``.  Content before the first heading is labelled with an
    empty ``heading_text``.
    """
    blocks = model.get("document", {}).get("blocks", [])
    sections: list[dict] = []
    current: dict = {"heading_text": "", "blocks": []}

    for b in blocks:
        if b.get("block_type") == "heading":
            if current["blocks"]:
                sections.append(current)
            current = {"heading_text": b.get("text", ""), "blocks": [b]}
        else:
            current["blocks"].append(b)
    if current["blocks"] or not sections:
        sections.append(current)
    return sections


def _collect_phase_c_table_groups(model: dict) -> list[dict]:
    """Collect table groups for Phase C batching.

    Each group contains a table block, its nearest preceding heading,
    and adjacent caption blocks.  Returns a list of dicts with keys
    ``table_id`` and ``blocks``.
    """
    blocks = model.get("document", {}).get("blocks", [])
    groups: list[dict] = []
    current_heading = None
    for i, b in enumerate(blocks):
        if b.get("block_type") == "heading":
            current_heading = b
        if b.get("block_type") == "table":
            related: list[dict] = []
            if current_heading is not None:
                related.append(current_heading)
            if i > 0 and blocks[i - 1].get("block_type") == "caption":
                related.append(blocks[i - 1])
            related.append(b)
            if i + 1 < len(blocks) and blocks[i + 1].get("block_type") == "caption":
                related.append(blocks[i + 1])
            groups.append({"table_id": b.get("id"), "blocks": related})
    return groups


def _prevalidate_patch_schema(
    patch: dict,
    phase: str,
    allowed_ops: set[str],
) -> list[dict]:
    """Structural pre-validation of a patch before ``validate_patch``.

    Checks top-level fields and decision-level structure only — field
    values and model references are left to ``validate_patch``.
    """
    errors: list[dict] = []
    if not isinstance(patch, dict):
        return [{"field": "$", "message": "patch must be object"}]
    if patch.get("schema_version") != PATCH_SCHEMA_VERSION:
        errors.append({
            "field": "schema_version",
            "message": f"Expected {PATCH_SCHEMA_VERSION!r}",
        })
    if patch.get("phase") != phase:
        errors.append({
            "field": "phase",
            "message": f"Expected {phase!r}",
        })
    decisions = patch.get("decisions")
    if not isinstance(decisions, list):
        errors.append({"field": "decisions", "message": "must be list"})
        return errors
    for idx, dec in enumerate(decisions):
        if not isinstance(dec, dict):
            errors.append({
                "decision_index": idx,
                "message": "decision must be object",
            })
            continue
        if dec.get("operation") not in allowed_ops:
            errors.append({
                "decision_index": idx,
                "operation": dec.get("operation"),
                "message": "operation not in allowed_ops",
            })
        if not isinstance(dec.get("to", {}), dict):
            errors.append({
                "decision_index": idx,
                "message": "'to' must be object",
            })
    return errors


def _record_phase_metric(enh: dict, metric: dict) -> None:
    """Append a per-batch metric entry to *enh['phase_metrics']*."""
    enh.setdefault("phase_metrics", []).append(metric)


def _append_phase_summary(enh: dict, phase: str) -> None:
    """Append a differential phase summary entry.

    Computes the diff for this call from tracked previous counts.
    """
    enh.setdefault("phase_summaries", [])
    prev_applied = enh.setdefault("_prev_applied", 0)
    prev_skipped = enh.setdefault("_prev_skipped", 0)
    prev_errors = enh.setdefault("_prev_errors", 0)

    cur_applied = len(enh.get("applied", []))
    cur_skipped = len(enh.get("skipped", []))
    cur_errors = len(enh.get("errors", []))

    enh["phase_summaries"].append({
        "phase": phase,
        "applied_count": cur_applied - prev_applied,
        "skipped_count": cur_skipped - prev_skipped,
        "error_count": cur_errors - prev_errors,
    })
    enh["_prev_applied"] = cur_applied
    enh["_prev_skipped"] = cur_skipped
    enh["_prev_errors"] = cur_errors


# ═══════════════════════════════════════════════════════════════════════
# 0a.  Incremental enhancement — collect suspicious sections for Phase A
# ═══════════════════════════════════════════════════════════════════════

def _get_count(field: str, parse_report: dict) -> int:
    """Read *field* from *parse_report*, treating lists as counts."""
    val = parse_report.get(field, 0)
    if isinstance(val, (list, tuple)):
        return len(val)
    if isinstance(val, int):
        return val
    return 0


def _collect_suspicious_sections(
    model: dict,
    report: dict,
) -> list[dict]:
    """Collect sections whose blocks carry signals of structural ambiguity.

    Only sections containing at least one block related to the following
    parse-report signals are returned:

    * ``ambiguous_short_paragraphs``
    * ``suspect_visual_headings``
    * ``inferred_headings``

    Parameters
    ----------
    model:
        Document AST whose top-level ``document.blocks`` are grouped by
        heading into sections.
    report:
        Mutable report dict.  ``report['parse_report']`` is consulted for
        suspicion-signal counts.

    Returns
    -------
    A list of section dicts, each with keys ``heading_text`` and ``blocks``.
    When the document is clean (no signals) the full block list is returned
    as a single section.
    """
    parse_report = report.get("parse_report", {})
    blocks = model.get("document", {}).get("blocks", [])

    if not blocks:
        return []

    # ── Group blocks into sections ──
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"heading_text": "", "blocks": []}

    for b in blocks:
        if b.get("block_type") == "heading":
            if current["blocks"]:
                sections.append(current)
            current = {"heading_text": b.get("text", ""), "blocks": []}
            # Include heading block itself so the LLM can retype cover
            # metadata, version markers, etc. to body.
            current["blocks"].append(b)
        else:
            current["blocks"].append(b)
    if current["blocks"] or not sections:
        sections.append(current)

    # ── Quick exit — no suspicious signals ──
    total_suspicious = (
        _get_count("suspect_visual_headings", parse_report)
        + _get_count("ambiguous_short_paragraphs", parse_report)
        + _get_count("inferred_headings", parse_report)
    )
    if total_suspicious == 0:
        return sections

    # ── Identify suspicious block IDs via model metadata & heuristics ──
    suspicious_ids: set[str] = set()

    for b in blocks:
        bid = b.get("id", "")
        btype = b.get("block_type", "")
        text = b.get("text", "")

        if not bid:
            continue

        # Short body paragraphs (ambiguous_short_paragraphs signal)
        if btype == "body" and text and 3 < len(text) <= 40:
            suspicious_ids.add(bid)

        # Body that looks like a heading (suspect_visual_headings /
        # inferred_headings signal)
        source = b.get("source", {})
        if source.get("is_compact_heading") or b.get("_inferred"):
            suspicious_ids.add(bid)

        # Heading-level metadata signalled as inferred
        if btype == "heading" and b.get("_inferred"):
            suspicious_ids.add(bid)

    # If signals exist but no block-level markers, fallback to text-based
    # heuristics on the sections themselves.
    if not suspicious_ids and total_suspicious > 0:
        for sec in sections:
            for b in sec.get("blocks", []):
                text = b.get("text", "")
                if b.get("block_type") == "body" and text and 3 < len(text) <= 40:
                    suspicious_ids.add(b.get("id", ""))

    # ── Filter sections ──
    if not suspicious_ids:
        return sections

    result = []
    for sec in sections:
        sec_bids = {b.get("id") for b in sec["blocks"] if b.get("id")}
        if sec_bids & suspicious_ids:
            result.append(sec)

    return result if result else sections


# ═══════════════════════════════════════════════════════════════════════
# 1.  JSON Extraction
# ═══════════════════════════════════════════════════════════════════════

def extract_json_object(raw: str) -> dict | None:
    """Extract the first JSON object from *raw* LLM output text.

    Finds the outermost ``{`` / ``}`` pair by tracking brace depth and
    attempts ``json.loads``.  Returns ``None`` when no valid object can
    be found (including when *raw* is empty).
    """
    if not raw:
        return None
    start = raw.find("{")
    if start < 0:
        return None

    depth = 0
    for i in range(start, len(raw)):
        ch = raw[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


# ═══════════════════════════════════════════════════════════════════════
# 2.  Patch Validation
# ═══════════════════════════════════════════════════════════════════════

def validate_patch(patch: dict, model: dict, allowed_ops: set[str]) -> list[dict]:
    r"""Validate a *patch* object against *model*.

    Returns a **list of error dicts**; an empty list means the patch is
    valid.

    Checks performed (in order)
    ---------------------------
    1. ``schema_version`` must equal ``"1.0"``.
    2. ``phase`` must be a non-empty string.
    3. ``decisions`` must be a list.
    4. For each decision:

       - ``block_id`` must be present and exist in *model*.
       - ``operation`` must be in *allowed_ops*.
       - When ``operation == "retype"``, target ``block_type`` must be
         a member of ``VALID_BLOCK_TYPES``.
       - ``confidence`` (if present) must be numeric.
    """
    errors: list[dict[str, Any]] = []

    # ── Top-level fields ──
    if patch.get("schema_version") != PATCH_SCHEMA_VERSION:
        errors.append({
            "field": "schema_version",
            "message": f"Expected {PATCH_SCHEMA_VERSION!r}, got {patch.get('schema_version')!r}",
        })

    phase = patch.get("phase")
    if not phase or not isinstance(phase, str):
        errors.append({"field": "phase", "message": "Missing or invalid 'phase'"})

    # ── Decisions array ──
    decisions = patch.get("decisions")
    if not isinstance(decisions, list):
        errors.append({"field": "decisions", "message": "'decisions' must be a list"})
        return errors  # nothing further to check

    if not decisions:
        return errors  # empty list is valid

    # Build block-id index from the model
    model_ids: set[str] = set()
    for b in model.get("document", {}).get("blocks", []):
        if "id" in b:
            model_ids.add(b["id"])

    # Build block-id → block lookup for operation-specific validation
    blocks_by_id: dict[str, dict] = {
        b["id"]: b
        for b in model.get("document", {}).get("blocks", [])
        if "id" in b
    }

    # Pre-compute table types for caption validation: for each caption
    # block find the preceding table block (if any) and record its type.
    table_type_for_caption: dict[str, str | None] = {}
    last_table_type: str | None = None
    for b in model.get("document", {}).get("blocks", []):
        if b.get("block_type") == "table":
            last_table_type = b.get("table_type", "data")
        elif b.get("block_type") == "caption":
            bid = b.get("id", "")
            if bid:
                table_type_for_caption[bid] = last_table_type
        else:
            last_table_type = None  # non-table resets

    # ── Per-decision checks ──
    for idx, dec in enumerate(decisions):
        if not isinstance(dec, dict):
            errors.append({"decision_index": idx, "message": "Decision is not a dict"})
            continue

        bid = dec.get("block_id")
        if not bid or not isinstance(bid, str):
            errors.append({"decision_index": idx, "message": "Missing or invalid 'block_id'"})
            continue
        if bid not in model_ids:
            errors.append({
                "decision_index": idx,
                "block_id": bid,
                "message": f"block_id {bid!r} not found in model",
            })
            continue

        op = dec.get("operation")
        if not op or not isinstance(op, str):
            errors.append({
                "decision_index": idx, "block_id": bid,
                "message": "Missing or invalid 'operation'",
            })
            continue
        if op not in allowed_ops:
            errors.append({
                "decision_index": idx, "block_id": bid,
                "message": f"Operation {op!r} not in allowed_ops: {sorted(allowed_ops)}",
            })
            continue

        # retype → validate target block_type
        if op == "retype":
            to_ = dec.get("to", {})
            to_type = to_.get("block_type")
            if not to_type or to_type not in VALID_BLOCK_TYPES:
                errors.append({
                    "decision_index": idx, "block_id": bid,
                    "message": f"Invalid target block_type {to_type!r}",
                })

        # confidence must be numeric when present
        conf = dec.get("confidence")
        if conf is not None and not isinstance(conf, (int, float)):
            errors.append({
                "decision_index": idx, "block_id": bid,
                "message": "'confidence' must be a number",
            })

        # ── Operation-specific validation ──
        if op == "set_caption_text":
            target = blocks_by_id.get(bid)
            if target:
                if not target.get("_auto_generated"):
                    errors.append({
                        "decision_index": idx, "block_id": bid,
                        "message": "set_caption_text only allowed on _auto_generated captions",
                    })
                if target.get("text", ""):
                    errors.append({
                        "decision_index": idx, "block_id": bid,
                        "message": "set_caption_text only allowed on empty-text captions",
                    })
                # Check associated table type — layout/code_sample tables
                # should not receive generated captions.
                assoc_tt = table_type_for_caption.get(bid)
                if assoc_tt in ("layout", "code_sample"):
                    errors.append({
                        "decision_index": idx, "block_id": bid,
                        "message": f"set_caption_text not allowed for {assoc_tt} tables",
                    })

    return errors


# ═══════════════════════════════════════════════════════════════════════
# 3.  Patch Application
# ═══════════════════════════════════════════════════════════════════════

def _apply_retype(block: dict, decision: dict) -> None:
    """Retype *block* to the type described in *decision['to']*."""
    to_ = decision.get("to", {})
    new_type = to_.get("block_type", block.get("block_type"))

    # Snapshot the original type on first mutation
    if "_original_block_type" not in block:
        block["_original_block_type"] = block.get("block_type")

    block["block_type"] = new_type

    # Scrub keys that may conflict with the new type
    for key in ("level", "list_type", "restart", "numbering",
                "caption_type", "header_rows", "table_type"):
        block.pop(key, None)

    if new_type == "heading":
        block["role"] = "heading"
        block["level"] = to_.get("level", 1)
        block["numbering"] = {"mode": "auto"}
    elif new_type == "list_item":
        block["role"] = "list_item"
        block["level"] = to_.get("level", 0)
        block["list_type"] = to_.get("list_type", "lower_letter_paren")
        block["restart"] = False
    elif new_type == "body":
        block["role"] = "body"
    elif new_type == "caption":
        block["role"] = "caption"
        block["caption_type"] = to_.get("caption_type", "table")
        block["numbering"] = {
            "mode": "auto",
            "label": to_.get("label", "表"),
        }


def _apply_operation(block: dict, decision: dict) -> None:
    """Apply a single operation to *block* in-place.

    Supported operations
    --------------------
    ``retype``
        Change ``block_type`` (body ↔ heading ↔ list_item ↔ caption).
    ``adjust_level``
        Change heading ``level``.
    ``set_restart``
        Toggle ``restart`` on a list_item.
    ``set_table_type``
        Change table ``table_type``.
    ``set_header_rows``
        Change table ``header_rows``.
    ``set_caption_type``
        Change caption ``caption_type``.
    ``set_caption_text``
        Change caption ``text``.
    """
    op = decision["operation"]
    to_ = decision.get("to", {})

    if op == "retype":
        _apply_retype(block, decision)
    elif op == "adjust_level":
        block["level"] = to_.get("level", block.get("level", 1))
        if "numbering" not in block:
            block["numbering"] = {"mode": "auto"}
    elif op == "set_restart":
        block["restart"] = to_.get("restart", False)
    elif op == "set_table_type":
        block["table_type"] = to_.get("table_type", "data")
    elif op == "set_header_rows":
        block["header_rows"] = to_.get("header_rows", 1)
    elif op == "set_caption_type":
        block["caption_type"] = to_.get("caption_type", "table")
    elif op == "set_caption_text":
        block["text"] = to_.get("text", "")


def _ensure_enhancer_report(report: dict) -> dict:
    """Create or return the ``report['llm_enhancer']`` sub-dict."""
    if "llm_enhancer" not in report:
        report["llm_enhancer"] = {"applied": [], "skipped": [], "errors": []}
    enh = report["llm_enhancer"]
    enh.setdefault("applied", [])
    enh.setdefault("skipped", [])
    enh.setdefault("errors", [])
    return enh


def apply_patch_to_model(model: dict, patch: dict, report: dict) -> dict:
    """Apply a validated *patch* to the document *model* in-place.

    Writes ``applied``, ``skipped``, and ``errors`` entries under
    ``report['llm_enhancer']``.

    Rules
    -----
    - ``confidence < 0.70`` → decision is **skipped** (recorded, not
      applied).
    - Unknown ``block_id``  → recorded as an error; the decision is
      discarded.
    - Valid decisions are applied; exceptions during application are
      caught and recorded.
    """
    enh = _ensure_enhancer_report(report)
    decisions = patch.get("decisions", [])
    blocks_by_id: dict[str, dict] = {
        b["id"]: b
        for b in model.get("document", {}).get("blocks", [])
        if "id" in b
    }

    for idx, dec in enumerate(decisions):
        if not isinstance(dec, dict):
            enh["errors"].append({
                "decision_index": idx,
                "message": "Decision is not a dict — skipped",
            })
            continue

        bid = dec.get("block_id", "")
        op = dec.get("operation", "")
        confidence = dec.get("confidence", 0.0)
        reason = dec.get("reason", "")

        # ── Low confidence → skip ──
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            enh["skipped"].append({
                "decision_index": idx,
                "block_id": bid,
                "operation": op,
                "confidence": confidence,
                "reason": reason,
                "skip_reason": "low_confidence",
            })
            continue

        # ── Block not found → error ──
        block = blocks_by_id.get(bid)
        if block is None:
            enh["errors"].append({
                "decision_index": idx,
                "block_id": bid,
                "message": f"Block {bid!r} not found in model",
            })
            continue

        # ── Apply ──
        try:
            _apply_operation(block, dec)
            enh["applied"].append({
                "block_id": bid,
                "operation": op,
                "from": dec.get("from"),
                "to": dec.get("to"),
                "confidence": confidence,
                "reason": reason,
            })
        except Exception as exc:
            enh["errors"].append({
                "decision_index": idx,
                "block_id": bid,
                "message": f"Apply failed: {exc}",
            })

    return model


# ═══════════════════════════════════════════════════════════════════════
# 4.  Prompt Builders  (phase-specific)
# ═══════════════════════════════════════════════════════════════════════

def _format_patch_example(patch: dict) -> str:
    """Pretty-print a small patch example."""
    return json.dumps(patch, ensure_ascii=False, indent=2)


def _build_phase_a_prompt(
    model: dict,
    hint: str | None = None,
    *,
    blocks_override: list[dict] | None = None,
) -> str:
    """Build the Phase A prompt for block role review.

    When *blocks_override* is provided only those blocks are listed in
    the prompt; otherwise all blocks from *model* are used (incremental
    enhancement path).
    """
    blocks = (
        blocks_override
        if blocks_override is not None
        else model.get("document", {}).get("blocks", [])
    )
    lines = [
        "你是文档结构审查器。请根据上下文判断段落角色。",
        "只返回 JSON 对象，不要额外文字。",
        "禁止新增、删除、合并段落。",
        "优先保留规则判断，仅在上下文强烈支持时修改。",
        "",
        f"文档共有 {len(blocks)} 个 block：",
    ]
    for b in blocks:
        bid = b.get("id", "?")
        btype = b.get("block_type", "?")
        text = (b.get("text") or "")[:200].replace("\n", " ")
        lines.append(f"  [{bid}] type={btype} | {text}")

    example = _format_patch_example({
        "schema_version": "1.0",
        "phase": "A",
        "decisions": [{
            "block_id": "b0007",
            "operation": "retype",
            "from": {"block_type": "body"},
            "to": {"block_type": "list_item", "level": 0,
                    "list_type": "lower_letter_paren"},
            "confidence": 0.85,
            "reason": "consecutive_functional_points",
        }],
    })
    lines.extend([
        "",
        "返回 JSON 格式：",
        example,
        "",
        "允许角色: heading, body, list_item, caption",
        "列表统一为 level=0, list_type=lower_letter_paren",
        "标题层级必须在 1 到 6。",
    ])

    if hint:
        lines.append(f"\n用户提示: \"{hint}\"")
    return "\n".join(lines)


def _build_phase_b_prompt(
    model: dict,
    hint: str | None = None,
    *,
    sections_override: list[dict] | None = None,
    batch_meta: dict | None = None,
) -> str:
    """Build the Phase B prompt for section structure review.

    Parameters
    ----------
    sections_override:
        When provided, only blocks from these sections are listed.
    batch_meta:
        Optional dict with ``batch_index``, ``batch_count``, and
        ``heading_path_summary`` for cross-batch context.
    """
    if sections_override is not None:
        blocks: list[dict] = []
        for sec in sections_override:
            blocks.extend(sec.get("blocks", []))
    else:
        blocks = model.get("document", {}).get("blocks", [])

    lines = [
        "你是技术文件目录结构审查器。请检查标题层级是否连贯。",
        "注意：连续同级标题（如 H2→H2→H2）是合法结构，不要认为缺少中间层级。",
        "如果文档从文档标题直接过渡到某个层级（如 H2），该层级就是顶层章节标题，不需要提升。",
        "只修正真实的层级错误（如 H2→H4 跳跃）和封面/元信息误判为标题。",
        "输出 JSON patch。不要根据文字好坏改写内容。不要创建新标题。",
        "如果封面、日期、版本、目录文字被识别为标题，应降级为 body。",
    ]
    if batch_meta:
        batch_idx = batch_meta.get("batch_index", 0)
        batch_cnt = batch_meta.get("batch_count", 1)
        hpath = batch_meta.get("heading_path_summary", "")
        lines.append(f"批处理: 第 {batch_idx + 1}/{batch_cnt} 批")
        if hpath:
            lines.append(f"前序章节: {hpath}")
    lines.extend([
        "",
        f"文档共有 {len(blocks)} 个 block：",
    ])
    for b in blocks:
        bid = b.get("id", "?")
        btype = b.get("block_type", "?")
        text = (b.get("text") or "")[:200].replace("\n", " ")
        if btype == "heading":
            level = b.get("level", "?")
            lines.append(f"  [{bid}] H{level} | {text}")
        elif btype == "list_item":
            lines.append(f"  [{bid}] list  | {text}")

    example = _format_patch_example({
        "schema_version": "1.0",
        "phase": "B",
        "decisions": [{
            "block_id": "b0012",
            "operation": "retype",
            "from": {"block_type": "heading", "level": 2},
            "to": {"block_type": "body"},
            "confidence": 0.90,
            "reason": "cover_metadata",
        }],
    })
    lines.extend([
        "",
        "返回 JSON 对象，格式参考：",
        example,
        "",
        "允许操作: retype (body↔heading), adjust_level, set_restart",
    ])

    if hint:
        lines.append(f"\n用户提示: \"{hint}\"")
    return "\n".join(lines)


def _build_phase_c_prompt(
    model: dict,
    hint: str | None = None,
    *,
    groups_override: list[dict] | None = None,
) -> str:
    """Build the merged Phase C + C1 prompt for table / caption review.

    In a single LLM call the model is asked to determine table types,
    header rows, caption types, **and** generate caption text for
    ``_auto_generated`` empty captions.

    When *groups_override* is provided only those table groups are
    included in the prompt.
    """
    if groups_override is not None:
        seen: set[str] = set()
        blocks: list[dict] = []
        for grp in groups_override:
            for b in grp.get("blocks", []):
                bid = b.get("id")
                if bid and bid not in seen:
                    seen.add(bid)
                    blocks.append(b)
    else:
        blocks = model.get("document", {}).get("blocks", [])
    lines = [
        "你是技术文件表格语义审查器。判断表格类型和题注。",
        "只返回 JSON 对象，不要额外文字。",
        "",
        "相关 block 列表：",
    ]
    has_auto_caption = False
    for b in blocks:
        bid = b.get("id", "?")
        if b.get("block_type") == "table":
            ttype = b.get("table_type", "?")
            hrows = b.get("header_rows", 0)
            rows = b.get("rows", [])
            nrows = len(rows)
            ncols = len(rows[0]) if rows else 0
            preview = "\n".join(
                " | ".join(c.get("text", "")[:30] for c in row)
                for row in rows[:3]
            )
            lines.append(f"  [{bid}] table type={ttype} rows={nrows} cols={ncols} "
                         f"header_rows={hrows}")
            lines.append(f"    {preview}")
        elif b.get("block_type") == "caption":
            text = (b.get("text") or "")[:200].replace("\n", " ")
            auto_gen = b.get("_auto_generated", False)
            if auto_gen:
                has_auto_caption = True
                lines.append(f"  [{bid}] caption (auto-generated) | text={text!r}")
            else:
                lines.append(f"  [{bid}] caption | {text}")
        elif b.get("block_type") == "heading":
            text = (b.get("text") or "")[:200].replace("\n", " ")
            lines.append(f"  [{bid}] heading | {text}")

    # Examples covering both table-type ops and caption-text ops.
    example = _format_patch_example({
        "schema_version": "1.0",
        "phase": "C",
        "decisions": [
            {
                "block_id": "b0020",
                "operation": "set_table_type",
                "to": {"table_type": "data"},
                "confidence": 0.95,
                "reason": "data_table_headers",
            },
            {
                "block_id": "b0021",
                "operation": "set_caption_text",
                "to": {"text": "功能模块定义表"},
                "confidence": 0.85,
                "reason": "caption_text_generated",
            },
        ],
    })
    lines.extend([
        "",
        "表格类型: data, code_sample, layout, unknown",
        "data 表通常有表头，header_rows 通常为 1。",
        "API 示例表通常是单格 JSON/HTTP/XML/Plain Text → code_sample。",
        "layout 表用于排版，不应自动插入表题注。",
    ])

    if has_auto_caption:
        lines.extend([
            "",
            "以下 caption 标记了 _auto_generated，需要为其生成题注文字：",
            "题注要求简洁、准确，不超过 30 字。",
            "不要重复'表'/'图'前缀（框架会自动添加）。",
            "如果信息不足无法判断，对于 data 表返回空字符串。",
        ])

    lines.extend([
        "",
        "返回 JSON：",
        example,
    ])

    if hint:
        lines.append(f"\n用户提示: \"{hint}\"")
    return "\n".join(lines)


_BUILD_PROMPT: dict[str, Any] = {
    "A": _build_phase_a_prompt,
    "B": _build_phase_b_prompt,
    "C": _build_phase_c_prompt,
}


# ═══════════════════════════════════════════════════════════════════════
# 5.  Core Enhancement Entry Point
# ═══════════════════════════════════════════════════════════════════════

def enhance_document_model(
    model: dict,
    report: dict,
    *,
    phase: str,
    llm_call: Callable[[str], str] | None = None,
    hint: str | None = None,
) -> dict:
    """Run LLM enhancement for *phase* on document *model*.

    Parameters
    ----------
    model:
        Document AST dict (modified in-place when a patch is applied).
    report:
        Mutable report dict.  Enhancement activity is written to
        ``report['llm_enhancer']``.
    phase:
        One of ``"A"``, ``"B"``, ``"C"``.
    llm_call:
        A callable that accepts a prompt string and returns the LLM raw
        text.  When *None*, *model* is returned unchanged.
    hint:
        Optional natural-language hint injected into the phase prompt.

    Returns
    -------
    The (potentially modified) *model*.
    """
    enh = _ensure_enhancer_report(report)

    # ── Sanitize user hint ─────────────────────────────────────────
    if hint:
        raw_hint = hint
        # Limit to 500 characters
        if len(hint) > 500:
            hint = hint[:500]
            enh["hint_truncated"] = True
        # Strip control characters (keep Chinese, English, digits,
        # common CJK/ASCII punctuation, whitespace)
        import re
        hint = re.sub(
            r'[^一-鿿　-〿＀-￯'
            r'\w\s,.!?;:\'\"()（）\-+=\[\]]',
            '', hint,
        )
        hint = re.sub(r'\s+', ' ', hint).strip()
        if hint != raw_hint[:500] if len(raw_hint) > 500 else hint != raw_hint:
            enh["hint_sanitized"] = True
        enh["original_hint"] = raw_hint
        enh["sanitized_hint"] = hint

    if llm_call is None:
        return model

    if phase not in PHASE_NAMES:
        enh["errors"].append({"phase": phase,
                               "message": f"Unknown phase {phase!r}"})
        return model

    allowed_ops = ALLOWED_OPS_BY_PHASE.get(phase, set())
    build_prompt = _BUILD_PROMPT.get(phase)

    if build_prompt is None:
        enh["errors"].append({"phase": phase,
                               "message": f"No prompt builder for phase {phase!r}"})
        return model

    # ── Phase A: incremental enhancement, filtered sections ──
    if phase == "A":
        sections = _collect_suspicious_sections(model, report)
        filtered_blocks: list[dict] = []
        for sec in sections:
            filtered_blocks.extend(sec.get("blocks", []))
        blocks_override = filtered_blocks if len(filtered_blocks) >= 3 else None
        prompt = _build_phase_a_prompt(
            model, hint=hint, blocks_override=blocks_override,
        )

        prompt = _apply_token_budget(prompt)
        enh.setdefault("prompts", []).append(
            {"phase": phase, "text": prompt[:500]},
        )

        block_count = len(model.get("document", {}).get("blocks", []))
        timed_call = _resolve_llm_call(
            phase, llm_call, block_count=block_count, report_enh=enh,
        )

        prompt_chars = len(prompt)
        start = time.perf_counter()

        def _metric(status, applied=0, skipped=0, batch_err=0):
            return {
                "phase": phase,
                "batch_index": 0,
                "batch_count": 1,
                "prompt_chars": prompt_chars,
                "estimated_tokens": math.ceil(prompt_chars / 4),
                "wall_time_sec": round(
                    time.perf_counter() - start, 3,
                ),
                "status": status,
                "applied_count": applied,
                "skipped_count": skipped,
                "error_count": batch_err,
            }

        try:
            raw = timed_call(prompt)
        except Exception as exc:
            enh["errors"].append({
                "phase": phase, "message": f"LLM call failed: {exc}",
            })
            _record_phase_metric(enh, _metric("error", batch_err=1))
            return model

        if not raw:
            enh["errors"].append({
                "phase": phase, "message": "Empty LLM response",
            })
            _record_phase_metric(
                enh, _metric("empty_response", batch_err=1),
            )
            return model

        patch = extract_json_object(raw)
        if patch is None:
            enh["errors"].append({
                "phase": phase,
                "message": "Failed to parse JSON from LLM response",
                "raw_preview": raw[:500],
            })
            _record_phase_metric(
                enh, _metric("parse_error", batch_err=1),
            )
            return model

        validation_errors = validate_patch(patch, model, allowed_ops)
        if validation_errors:
            enh["errors"].append({
                "phase": phase,
                "message": (
                    f"Patch validation failed"
                    f" ({len(validation_errors)} error(s))"
                ),
                "validation_errors": validation_errors,
            })
            _record_phase_metric(
                enh, _metric("validation_error", batch_err=1),
            )
            return model

        before_applied = len(enh.get("applied", []))
        before_skipped = len(enh.get("skipped", []))
        before_errors = len(enh.get("errors", []))

        apply_patch_to_model(model, patch, report)
        _append_phase_summary(enh, phase)

        _record_phase_metric(enh, _metric(
            "ok",
            applied=len(enh.get("applied", [])) - before_applied,
            skipped=len(enh.get("skipped", [])) - before_skipped,
            batch_err=len(enh.get("errors", [])) - before_errors,
        ))

        # Phase A applied rate (for auto-mode B/C gating)
        _applied = enh.get("applied", [])
        _skipped = enh.get("skipped", [])
        _total_phase_a = len(_applied) + len(_skipped)
        if _total_phase_a > 0:
            enh["phase_a_applied_rate"] = len(_applied) / _total_phase_a

        return model

    # ── Phase B: batched section processing ──
    if phase == "B":
        sections = _collect_phase_b_sections(model)
        batches = _iter_batches(sections, PHASE_B_SECTION_BATCH_SIZE)
        batch_count = len(batches)

        block_count = len(model.get("document", {}).get("blocks", []))
        timed_call = _resolve_llm_call(
            phase, llm_call, block_count=block_count, report_enh=enh,
        )

        # Track live heading state across batches (updated after each batch
        # so the heading summary reflects post-application model state).
        _live_heading_texts: list[str] = []

        def _heading_path_summary(batch_idx: int) -> str:
            if batch_idx == 0:
                return ""
            paths = _live_heading_texts[-10:]
            return " → ".join(paths) if paths else ""

        for batch_idx, batch_secs in enumerate(batches):
            batch_meta_dict = {
                "batch_index": batch_idx,
                "batch_count": batch_count,
                "heading_path_summary": _heading_path_summary(batch_idx),
            }
            prompt = _build_phase_b_prompt(
                model, hint=hint,
                sections_override=batch_secs,
                batch_meta=batch_meta_dict,
            )
            prompt = _apply_token_budget(prompt)
            enh.setdefault("prompts", []).append(
                {"phase": phase, "batch": batch_idx,
                 "text": prompt[:500]},
            )

            prompt_chars = len(prompt)
            start = time.perf_counter()

            def _metric(status, applied=0, skipped=0, batch_err=0):
                return {
                    "phase": phase,
                    "batch_index": batch_idx,
                    "batch_count": batch_count,
                    "prompt_chars": prompt_chars,
                    "estimated_tokens": math.ceil(prompt_chars / 4),
                    "wall_time_sec": round(
                        time.perf_counter() - start, 3,
                    ),
                    "status": status,
                    "applied_count": applied,
                    "skipped_count": skipped,
                    "error_count": batch_err,
                }

            try:
                raw = timed_call(prompt)
            except Exception as exc:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": f"LLM call failed: {exc}",
                })
                _record_phase_metric(enh, _metric("error", batch_err=1))
                continue

            if not raw:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": "Empty LLM response",
                })
                _record_phase_metric(
                    enh, _metric("empty_response", batch_err=1),
                )
                continue

            patch = extract_json_object(raw)
            if patch is None:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": "Failed to parse JSON",
                    "raw_preview": raw[:500],
                })
                _record_phase_metric(
                    enh, _metric("parse_error", batch_err=1),
                )
                continue

            pre_errors = _prevalidate_patch_schema(
                patch, phase, allowed_ops,
            )
            if pre_errors:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": (
                        f"Patch pre-validation failed"
                        f" ({len(pre_errors)} error(s))"
                    ),
                    "pre_validation_errors": pre_errors,
                })
                _record_phase_metric(
                    enh, _metric("pre_validation_error", batch_err=1),
                )
                continue

            validation_errors = validate_patch(
                patch, model, allowed_ops,
            )
            if validation_errors:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": (
                        f"Patch validation failed"
                        f" ({len(validation_errors)} error(s))"
                    ),
                    "validation_errors": validation_errors,
                })
                _record_phase_metric(
                    enh, _metric("validation_error", batch_err=1),
                )
                continue

            before_applied = len(enh.get("applied", []))
            before_skipped = len(enh.get("skipped", []))
            before_errors = len(enh.get("errors", []))

            apply_patch_to_model(model, patch, report)

            # Rebuild live heading tracker from current model after each batch.
            _live_heading_texts.clear()
            for _b in model.get("document", {}).get("blocks", []):
                if _b.get("block_type") == "heading":
                    _ht = (_b.get("text") or "")[:60]
                    if _ht:
                        _live_heading_texts.append(_ht)

            _record_phase_metric(enh, _metric(
                "ok",
                applied=len(enh.get("applied", [])) - before_applied,
                skipped=len(enh.get("skipped", [])) - before_skipped,
                batch_err=len(enh.get("errors", [])) - before_errors,
            ))

        _append_phase_summary(enh, phase)
        return model

    # ── Phase C: batched table group processing ──
    if phase == "C":
        table_groups = _collect_phase_c_table_groups(model)

        # Fallback when no table groups: single full-model call
        # (handles standalone captions without tables).
        if not table_groups:
            batches = [None]  # one batch → groups_override=None triggers full-model call
        else:
            batches = _iter_batches(table_groups, PHASE_C_TABLE_BATCH_SIZE)
        batch_count = len(batches)

        block_count = len(model.get("document", {}).get("blocks", []))
        timed_call = _resolve_llm_call(
            phase, llm_call, block_count=block_count, report_enh=enh,
        )

        for batch_idx, batch_groups in enumerate(batches):
            prompt = _build_phase_c_prompt(
                model, hint=hint,
                groups_override=batch_groups,
            )
            prompt = _apply_token_budget(prompt)
            enh.setdefault("prompts", []).append(
                {"phase": phase, "batch": batch_idx,
                 "text": prompt[:500]},
            )

            prompt_chars = len(prompt)
            start = time.perf_counter()

            def _metric(status, applied=0, skipped=0, batch_err=0):
                return {
                    "phase": phase,
                    "batch_index": batch_idx,
                    "batch_count": batch_count,
                    "prompt_chars": prompt_chars,
                    "estimated_tokens": math.ceil(prompt_chars / 4),
                    "wall_time_sec": round(
                        time.perf_counter() - start, 3,
                    ),
                    "status": status,
                    "applied_count": applied,
                    "skipped_count": skipped,
                    "error_count": batch_err,
                }

            try:
                raw = timed_call(prompt)
            except Exception as exc:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": f"LLM call failed: {exc}",
                })
                _record_phase_metric(enh, _metric("error", batch_err=1))
                continue

            if not raw:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": "Empty LLM response",
                })
                _record_phase_metric(
                    enh, _metric("empty_response", batch_err=1),
                )
                continue

            patch = extract_json_object(raw)
            if patch is None:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": "Failed to parse JSON",
                    "raw_preview": raw[:500],
                })
                _record_phase_metric(
                    enh, _metric("parse_error", batch_err=1),
                )
                continue

            pre_errors = _prevalidate_patch_schema(
                patch, phase, allowed_ops,
            )
            if pre_errors:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": (
                        f"Patch pre-validation failed"
                        f" ({len(pre_errors)} error(s))"
                    ),
                    "pre_validation_errors": pre_errors,
                })
                _record_phase_metric(
                    enh, _metric("pre_validation_error", batch_err=1),
                )
                continue

            validation_errors = validate_patch(
                patch, model, allowed_ops,
            )
            if validation_errors:
                enh["errors"].append({
                    "phase": phase, "batch": batch_idx,
                    "message": (
                        f"Patch validation failed"
                        f" ({len(validation_errors)} error(s))"
                    ),
                    "validation_errors": validation_errors,
                })
                _record_phase_metric(
                    enh, _metric("validation_error", batch_err=1),
                )
                continue

            before_applied = len(enh.get("applied", []))
            before_skipped = len(enh.get("skipped", []))
            before_errors = len(enh.get("errors", []))

            apply_patch_to_model(model, patch, report)

            _record_phase_metric(enh, _metric(
                "ok",
                applied=len(enh.get("applied", [])) - before_applied,
                skipped=len(enh.get("skipped", [])) - before_skipped,
                batch_err=len(enh.get("errors", [])) - before_errors,
            ))

        _append_phase_summary(enh, phase)
        return model

    # ── Fallback (should not reach here) ──
    enh["errors"].append({
        "phase": phase,
        "message": f"No handler for phase {phase!r}",
    })
    return model


# ═══════════════════════════════════════════════════════════════════════
# 6.  Suspicion Score
# ═══════════════════════════════════════════════════════════════════════

def compute_suspicion_score(report: dict) -> float:
    """Compute a 0–1 *suspicion score* from the parse report.

    Higher values suggest the source document has structural ambiguity
    that would benefit from LLM enhancement.

    Data sources (queried in order)
    --------------------------------
    - ``report['source_document_model_summary']``
    - ``report.get('parse_report', {})``
    """
    summary = report.get("source_document_model_summary", {})
    parse_report = report.get("parse_report", {})
    block_counts = summary.get("block_counts", {})
    total_blocks = summary.get("block_count", 0) or 1

    # Safe ratio helper
    def _ratio(n: float, d: float) -> float:
        return n / d if d > 0 else 0.0

    n_body = block_counts.get("body", 0) or 1
    n_heading = block_counts.get("heading", 0) or 1
    n_list = block_counts.get("list_item", 0) or 1

    # Read signals — values may be int (DOCX) or list (MD); normalize to int.
    def _signal(v):
        return len(v) if isinstance(v, list) else (v if isinstance(v, (int, float)) else 0)

    suspect_visual_headings = _signal(parse_report.get("suspect_visual_headings", 0))
    ambiguous_short_paragraphs = _signal(parse_report.get("ambiguous_short_paragraphs", 0))
    inferred_headings = _signal(parse_report.get("inferred_headings", 0))
    inferred_lists = _signal(parse_report.get("inferred_lists", 0))
    unstyled_paragraphs = _signal(parse_report.get("unstyled_paragraphs", 0))

    n_table = block_counts.get("table", 0)
    n_image = block_counts.get("image", 0)

    # 7-dimension weighted sum (weights must sum to 1.0)
    raw = (
        0.25 * min(_ratio(suspect_visual_headings, n_body), 1.0)
        + 0.20 * min(_ratio(ambiguous_short_paragraphs, n_body), 1.0)
        + 0.15 * min(_ratio(inferred_headings, n_heading), 1.0)
        + 0.10 * min(_ratio(inferred_lists, n_list), 1.0)
        + 0.15 * min(_ratio(unstyled_paragraphs, n_body), 1.0)
        + 0.10 * min(_ratio(n_table, total_blocks), 1.0)
        + 0.05 * min(_ratio(n_image, total_blocks), 1.0)
    )

    return min(raw, 1.0)


# ═══════════════════════════════════════════════════════════════════════
# 7.  Should-Enhance Decision
# ═══════════════════════════════════════════════════════════════════════

def should_enhance(report: dict, phase: str, mode: str) -> bool:
    """Decide whether LLM enhancement should run for *phase* in *mode*.

    *mode* values
    -------------
    ``"off"``
        Never enhance — returns ``False`` for every phase.
    ``"auto"``
        Only enhance if ``compute_suspicion_score(report) >= 0.15`` for
        phase ``"A"``.  (Future tasks will add modification-rate gating
        for B/C.)
    ``"a"`` / ``"ab"`` / ``"abc"``
        Force-enable the named phases (bypass suspicion check).
    ``"force-a"`` / ``"force-ab"`` / ``"force-abc"``
        Same as the non-*force* variants — reserved for future
        modification-rate gate override.
    """
    if mode == "off":
        return False

    # ── Manual / force modes ──
    force_map = {
        "a": {"A"},
        "ab": {"A", "B"},
        "abc": {"A", "B", "C"},
        "force-a": {"A"},
        "force-ab": {"A", "B"},
        "force-abc": {"A", "B", "C"},
    }
    if mode in force_map:
        return phase in force_map[mode]

    # ── Auto mode ──
    if mode == "auto":
        summary = report.get("source_document_model_summary", {})
        block_count = summary.get("block_count", 0) or 0

        if phase == "A":
            # Short documents with few blocks are likely clean.
            if block_count < 5:
                return False
            # Without a template there are no heading/list styles to
            # override — skip auto enhancement.
            if not report.get("template_profile"):
                return False
            return compute_suspicion_score(report) >= AUTO_TRIGGER_THRESHOLD
        else:
            # Phase B / C: gate on Phase A's applied modification rate.
            llm_enhancer = report.get("llm_enhancer", {})
            applied_rate = llm_enhancer.get("phase_a_applied_rate", 0.0)
            return applied_rate >= 0.05

    return False


# ═══════════════════════════════════════════════════════════════════════
# 8.  Compatibility Wrapper  (list_semantic_enhancer bridge)
# ═══════════════════════════════════════════════════════════════════════

def build_role_overrides_from_docx(
    src_doc,
    strict_normalize: bool,
    *,
    llm_call: Callable[[str], str] | None = None,
) -> dict[int, str]:
    """Compatibility wrapper matching ``list_semantic_enhancer`` interface.

    Iterates through a python-docx ``Document``, collects paragraphs per
    section, and runs Phase A LLM enhancement to produce
    ``{para_index: new_role}`` overrides.

    Returns an empty dict when *llm_call* is ``None``.
    """
    if llm_call is None:
        return {}

    # Lazy imports (python-docx may not be installed in all environments)
    from docx.oxml.ns import qn as _qn
    from docx.text.paragraph import Paragraph

    # ── Collect sections ──
    sections: list[dict[str, Any]] = []
    current_sec: dict[str, Any] = {"heading_text": "", "blocks": []}
    had_heading = False
    global_index = 0

    for child in src_doc.element.body.iterchildren():
        tag = child.tag

        # Skip section-properties and other non-content elements
        if tag.endswith(("}w:sectPr", "}sectPr")):
            continue

        if tag.endswith(("}w:tbl", "}tbl")):
            current_sec["blocks"].append(("", "table", child, global_index))
            global_index += 1
            continue

        if tag.endswith(("}w:p", "}p")):
            para = Paragraph(child, src_doc)
            text = para.text.strip()

            # Detect image-only paragraphs (empty text with graphics)
            if not text:
                ns_qn = _qn
                drawings = child.findall('.//' + ns_qn('w:drawing'))
                if drawings:
                    # Image-only paragraph: count for index alignment
                    global_index += 1
                continue

            style_name = (para.style.name or "").lower() if para.style else ""
            if "heading" in style_name or style_name.startswith("h"):
                global_index += 1
                if had_heading and current_sec["blocks"]:
                    sections.append(current_sec)
                current_sec = {"heading_text": text, "blocks": []}
                had_heading = True
                continue

            role = "body"
            current_sec["blocks"].append((text, role, child, global_index))
            global_index += 1

    if had_heading:
        sections.append(current_sec)

    # ── Per-section Phase A enhancement ──
    overrides: dict[int, str] = {}

    for sec in sections:
        heading_text = sec["heading_text"] or "(no heading)"
        paras = [{"text": t, "role": r, "global_index": gi}
                 for t, r, _, gi in sec["blocks"]]
        if len(paras) <= 1:
            continue

        lines = [
            f"章节标题: \"{heading_text}\"",
            "请重新判断每个段落的语义角色。",
            "允许角色: heading, body, list_item, caption",
            "列表统一为 level=0, list_type=lower_letter_paren",
            "标题层级必须在 1 到 6。",
            "",
            "段落列表:",
        ]
        for idx, p in enumerate(paras):
            lines.append(f"  [{idx}] role={p['role']} | {(p.get('text') or '')[:200]}")

        example_patch = {
            "schema_version": "1.0",
            "phase": "A",
            "decisions": [{
                "block_id": str(idx),
                "operation": "retype",
                "from": {"block_type": "body"},
                "to": {"block_type": "list_item", "level": 0,
                        "list_type": "lower_letter_paren"},
                "confidence": 0.85,
                "reason": "consecutive_functional_points",
            } for idx in range(min(len(paras), 3))],
        }
        lines.extend([
            "",
            "返回 JSON 对象，格式：",
            json.dumps(example_patch, ensure_ascii=False, indent=2),
        ])

        try:
            raw = llm_call("\n".join(lines))
        except Exception:
            continue

        patch = extract_json_object(raw)
        if patch is None:
            continue

        for dec in patch.get("decisions", []):
            bid = dec.get("block_id")
            conf = dec.get("confidence", 0.0)
            if conf < LOW_CONFIDENCE_THRESHOLD:
                continue
            try:
                idx_int = int(bid)
            except (ValueError, TypeError):
                continue
            if 0 <= idx_int < len(paras):
                new_role = dec.get("to", {}).get("block_type")
                if new_role in ("heading", "body", "list_item", "caption"):
                    # Use global_index, not section-local idx_int, so
                    # the override key matches the source-paragraph
                    # position used by render_docx_direct.
                    global_para_idx = paras[idx_int]["global_index"]
                    overrides[global_para_idx] = new_role

    return overrides
