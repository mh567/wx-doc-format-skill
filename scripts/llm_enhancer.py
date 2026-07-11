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
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


# ── Constants ──────────────────────────────────────────────────────────

# These are populated by _refresh_phase_exports().
# Direct assignments are kept for backward-compatible import during
# module initialisation; they are overwritten once the registry is ready.
ALLOWED_OPS_BY_PHASE: dict[str, frozenset[str]] = {}

VALID_BLOCK_TYPES = frozenset({
    "heading", "body", "list_item", "caption", "table", "image",
    "appendix", "unknown",
})

PATCH_SCHEMA_VERSION = "1.0"
PHASE_NAMES = frozenset({"A", "B"})

AUTO_TRIGGER_THRESHOLD = 0.15
LOW_CONFIDENCE_THRESHOLD = 0.70

LLM_CALL_TIMEOUT = 30  # seconds (base — dynamic in _resolve_llm_call)

PHASE_B_SECTION_BATCH_SIZE = 20
PHASE_B_CAPTION_BATCH_SIZE = 15

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
# 0c.  Capability Registry (Pluggable Architecture)
# ═══════════════════════════════════════════════════════════════════════

BatchStrategy = Literal["single", "by_targets", "by_sections"]


@dataclass(frozen=True)
class CapabilityConfig:
    """Descriptor for an LLM enhancement capability.

    Attributes
    ----------
    name:
        Primary capability name (e.g. ``"list_detect"``, ``"caption_gen"``).
    allowed_ops:
        Set of operation strings this capability may emit.
    prompt_builder:
        Callable(model, hint=..., **kwargs) → prompt string.
    collector:
        Callable(model, report) → list of item dicts for batching.
        Each item carries whatever ``prompt_builder`` needs.
    batching:
        Strategy name — determines how items are grouped.
    batch_size:
        Max items per batch (ignored for ``"single"``).
    empty_status:
        Status string in metrics when collector returns no items.
    prevalidate:
        Whether to run ``_prevalidate_patch_schema`` before validation.
    prompt_preview_text:
        Text recorded in ``prompts`` list when no batches exist.
    after_phase:
        Optional callback (model, report, enh_dict) after all batches.
    """
    name: str
    allowed_ops: frozenset[str] = field(default_factory=frozenset)
    prompt_builder: Callable = field(default=lambda *a, **kw: "")
    collector: Callable = field(default=lambda m, r: [])
    batching: BatchStrategy = "single"
    batch_size: int | None = None
    empty_status: str = "no_targets"
    prevalidate: bool = False
    prompt_preview_text: str | None = None
    after_phase: Callable | None = None
    prompt_args_builder: Callable | None = None


CAPABILITY_REGISTRY: dict[str, CapabilityConfig] = {}

# Legacy phase name → capability name (e.g. "A" → "list_detect")
_PHASE_TO_CAPABILITY: dict[str, str] = {}
# Capability name → legacy phase name (e.g. "list_detect" → "A")
_LEGACY_NAMES: dict[str, str] = {}


def register_capability(config: CapabilityConfig) -> CapabilityConfig:
    """Register a capability (mutated in place if ``name`` already exists)."""
    if config.name in CAPABILITY_REGISTRY:
        raise ValueError(f"Duplicate capability {config.name!r}")
    CAPABILITY_REGISTRY[config.name] = config
    return config


def _register_phase_alias(legacy_name: str, capability_name: str) -> None:
    """Map a legacy phase name (``"A"``, ``"B"``) to a registry entry."""
    if legacy_name in _PHASE_TO_CAPABILITY:
        raise ValueError(f"Duplicate phase alias {legacy_name!r}")
    if capability_name not in CAPABILITY_REGISTRY:
        raise ValueError(f"Unknown capability {capability_name!r}")
    _PHASE_TO_CAPABILITY[legacy_name] = capability_name
    _LEGACY_NAMES[capability_name] = legacy_name


def _resolve_capability(name: str) -> str | None:
    """Resolve a phase or capability name to a registry key.

    Returns ``None`` when neither an alias nor a registry key matches.
    """
    if name in CAPABILITY_REGISTRY:
        return name
    cap = _PHASE_TO_CAPABILITY.get(name)
    if cap in CAPABILITY_REGISTRY:
        return cap
    return None


def _resolve_legacy_phase(name: str) -> str:
    """Map any input to a legacy phase name (A/B) for ``should_enhance``.

    ``"list_detect"`` → ``"A"``, ``"A"`` → ``"A"``, unknowns pass through.
    """
    if name in _LEGACY_NAMES:
        return _LEGACY_NAMES[name]
    if name in _PHASE_TO_CAPABILITY:
        return name  # already a legacy name
    return name


def _refresh_phase_exports() -> None:
    """Derive backward-compatible module-level constants from the registry."""
    global ALLOWED_OPS_BY_PHASE, _BUILD_PROMPT
    ALLOWED_OPS_BY_PHASE = {}
    _BUILD_PROMPT = {}
    for legacy_name, cap_name in _PHASE_TO_CAPABILITY.items():
        desc = CAPABILITY_REGISTRY.get(cap_name)
        if desc is None:
            continue
        ALLOWED_OPS_BY_PHASE[legacy_name] = frozenset(desc.allowed_ops)
        _BUILD_PROMPT[legacy_name] = desc.prompt_builder


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
        One of ``"A"`` or ``"B"``.
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
# 0b.  Batch helpers
# ═══════════════════════════════════════════════════════════════════════


def _iter_batches(items: list, size: int) -> list[list]:
    """Split *items* into batches of at most *size*."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _make_phase_batches(desc: CapabilityConfig, items: list) -> list[list]:
    """Group *items* into batches according to *desc.batching*.

    ``"single"`` → one batch containing all items (wrapped in a list).
    ``"by_targets"`` / ``"by_sections"`` → split by ``batch_size``.
    """
    if desc.batching == "single":
        return [items]
    if desc.batching in {"by_targets", "by_sections"}:
        if not items:
            return []
        return _iter_batches(items, desc.batch_size or len(items))
    raise ValueError(f"Unknown batching strategy {desc.batching!r}")


def _prompt_args_for_list_detect(
    desc: CapabilityConfig,
    model: dict,
    hint: str | None,
    batch: list,
    batch_idx: int,
    batch_count: int,
) -> dict:
    """Build keyword arguments for the list_detect prompt_builder."""
    payload = batch[0] if batch else {}
    return dict(model=model, hint=hint,
                blocks_override=payload.get("blocks_override"))


def _prompt_args_for_caption_gen(
    desc: CapabilityConfig,
    model: dict,
    hint: str | None,
    batch: list,
    batch_idx: int,
    batch_count: int,
) -> dict:
    """Build keyword arguments for the caption_gen prompt_builder."""
    targets = [item["targets"] for item in batch]
    return dict(model=model, hint=hint,
                targets_override=targets,
                batch_meta={"batch_index": batch_idx,
                            "batch_count": batch_count})


def _build_prompt_for_batch(
    desc: CapabilityConfig,
    model: dict,
    hint: str | None,
    batch: list,
    batch_idx: int,
    batch_count: int,
) -> str:
    """Build a prompt for one batch, adapting arguments per capability.

    When ``desc.prompt_args_builder`` is set, delegates argument assembly
    to that builder, then calls ``desc.prompt_builder`` with the result.
    Falls back to the legacy hard-coded dispatch for backward compat.
    """
    if desc.prompt_args_builder is not None:
        kwargs = desc.prompt_args_builder(
            desc, model, hint, batch, batch_idx, batch_count,
        )
        return desc.prompt_builder(**kwargs)

    # Legacy fallback: by_targets strategy
    if desc.batching == "by_targets":
        targets = [item["targets"] for item in batch]
        return desc.prompt_builder(
            model,
            hint=hint,
            targets_override=targets,
            batch_meta={"batch_index": batch_idx, "batch_count": batch_count},
        )

    # Fallback — pass items directly
    return desc.prompt_builder(model, hint=hint, batch_items=batch)


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


def _collect_caption_targets(model: dict) -> list[dict]:
    """Collect auto-generated empty captions that need caption text.

    For each such caption, gathers surrounding context:
    - nearest preceding heading (section title)
    - preceding body / list_item texts
    - table preview (first 3 rows of the associated table)
    - following body / list_item texts

    Returns a list of target dicts with keys:
    ``block_id``, ``block``, ``caption_type``, ``heading_text``,
    ``preceding_texts``, ``table_preview``, ``following_texts``.
    Returns an empty list when no auto-generated empty captions exist.
    """
    blocks = model.get("document", {}).get("blocks", [])
    targets: list[dict] = []
    current_heading_text = ""
    preceding_texts: list[str] = []

    for i, block in enumerate(blocks):
        btype = block.get("block_type")

        if btype == "heading":
            current_heading_text = (block.get("text") or "").strip()
            preceding_texts = []

        elif btype in ("body", "list_item"):
            text = (block.get("text") or "").strip()
            if text:
                preceding_texts.append(text[:200])
            if len(preceding_texts) > 5:
                preceding_texts.pop(0)

        elif btype == "table":
            preceding_texts = []

        elif btype == "caption":
            auto_gen = block.get("_auto_generated", False)
            text = (block.get("text") or "").strip()
            if auto_gen and not text:
                # Look ahead for the associated table block.
                table_preview = ""
                following_texts: list[str] = []
                skip = False
                for j in range(i + 1, min(i + 5, len(blocks))):
                    nb = blocks[j]
                    nbtype = nb.get("block_type")
                    if nbtype == "table":
                        tt = nb.get("table_type", "data")
                        if tt in ("layout", "code_sample"):
                            skip = True
                            break
                        rows = nb.get("rows", [])
                        preview_parts = []
                        for row in rows[:3]:
                            cells = [c.get("text", "")[:40] for c in row]
                            preview_parts.append(" | ".join(cells))
                        table_preview = "\n".join(preview_parts)
                        break
                    elif nbtype in ("body", "list_item"):
                        t = (nb.get("text") or "").strip()
                        if t:
                            following_texts.append(t[:200])
                if skip:
                    continue

                targets.append({
                    "block_id": block.get("id"),
                    "block": block,
                    "caption_type": block.get("caption_type", "table"),
                    "heading_text": current_heading_text,
                    "preceding_texts": preceding_texts[-3:],
                    "table_preview": table_preview,
                    "following_texts": following_texts[:2],
                })

            preceding_texts = []

        else:
            preceding_texts = []

    return targets


def _collect_list_detect_batches(model: dict, report: dict) -> list[dict]:
    """Collector for the ``list_detect`` capability.

    Returns a single-item list with ``{"blocks_override": ...}`` so the
    batch system produces exactly one call.  When fewer than 3 blocks
    are suspicious, ``blocks_override`` is ``None`` (full model shown).
    """
    sections = _collect_suspicious_sections(model, report)
    filtered_blocks = []
    for sec in sections:
        filtered_blocks.extend(sec.get("blocks", []))
    blocks_override = filtered_blocks if len(filtered_blocks) >= 3 else None
    return [{"blocks_override": blocks_override}]


def _collect_caption_gen_batches(model: dict, report: dict) -> list[dict]:
    """Collector for the ``caption_gen`` capability.

    Returns one item per caption target, each wrapped as
    ``{"targets": <target_dict>}``.
    """
    targets = _collect_caption_targets(model)
    return [{"targets": t} for t in targets]


def _record_phase_a_applied_rate(
    model: dict, report: dict, enh: dict,
) -> None:
    """After-phase hook for list_detect: compute applied rate."""
    _applied = enh.get("applied", [])
    _skipped = enh.get("skipped", [])
    _total = len(_applied) + len(_skipped)
    if _total > 0:
        enh["phase_a_applied_rate"] = len(_applied) / _total


def _prevalidate_patch_schema(
    patch: dict,
    phase: str,
    allowed_ops: frozenset[str],
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

def validate_patch(patch: dict, model: dict, allowed_ops: frozenset[str]) -> list[dict]:
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
    # block scan forward for the associated table block (same direction as
    # _collect_caption_targets) and record its type.
    # This correctly handles normalized caption-before-table order.
    all_blocks = model.get("document", {}).get("blocks", [])
    table_type_for_caption: dict[str, str | None] = {}
    for i, b in enumerate(all_blocks):
        if b.get("block_type") != "caption":
            continue
        bid = b.get("id", "")
        if not bid:
            continue
        assoc_table_type: str | None = None
        for j in range(i + 1, min(i + 5, len(all_blocks))):
            nb = all_blocks[j]
            nb_type = nb.get("block_type")
            if nb_type == "table":
                assoc_table_type = nb.get("table_type", "data")
                break
            elif nb_type in ("body", "list_item"):
                continue  # intervening text, keep looking
            else:
                break  # heading or other block — stop
        table_type_for_caption[bid] = assoc_table_type

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
    """Build the Phase A prompt — rule-result reviewer.

    The LLM acts as a *rule-result reviewer* (规则结果审查器): it is shown
    what the deterministic rules decided and only flags cases where the
    rule judgement is clearly wrong.

    When *blocks_override* is provided only those blocks are listed in
    the prompt; otherwise all blocks from *model* are used (incremental
    enhancement path).
    """
    blocks = (
        blocks_override
        if blocks_override is not None
        else model.get("document", {}).get("blocks", [])
    )

    # Build section-by-id map from full model (not only displayed blocks)
    # so every block shows the nearest preceding heading text.
    all_blocks = model.get("document", {}).get("blocks", [])
    section_by_id: dict[str, str] = {}
    current_section = ""
    for b in all_blocks:
        bid = b.get("id", "")
        if b.get("block_type") == "heading":
            current_section = (b.get("text") or "")[:80].replace("\n", " ")
        if bid:
            section_by_id[bid] = current_section

    lines = [
        "你是规则结果审查器。默认规则判断正确，只修正明显误判。",
        "每个 block 展示所属章节、源 DOCX 样式、源语义角色"
        "和规则的 block_type、role、level、list_type、caption_type。",
        "只返回 JSON 对象，不要额外文字。",
        "",
        f"文档共有 {len(blocks)} 个 block：",
    ]
    for b in blocks:
        bid = b.get("id", "?")
        btype = b.get("block_type", "?")
        role = b.get("role", "?")
        level = b.get("level", "-")
        list_type = b.get("list_type", "-")
        cap_type = b.get("caption_type", "-")
        text = (b.get("text") or "")[:200].replace("\n", " ")
        section = section_by_id.get(bid, "")
        source = b.get("source", {})
        source_style = source.get("style", "-")
        source_role = source.get("inferred_role", "-")
        lines.append(
            f"  [{bid}] section={section} source_style={source_style}"
            f" source_role={source_role} type={btype} role={role} level={level}"
            f" list={list_type} cap={cap_type} | {text}"
        )

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
        "",
        "约束（必须遵守）:",
        "默认规则判断正确；省略某个 block 表示接受规则结果。",
        "decisions 只包含需要修改的项；空 decisions 表示规则全部正确。",
        "不要对代码块、JSON 示例、普通说明句、引导句生成 list_item 或 caption。",
        "",
        "source_style 与 source_role 辅助判断规则：",
        "源 DOCX 样式为 Heading 的 block 必须保持 heading，除非它明显是封面日期、版本号等元信息。",
        "源 DOCX 样式为 List 或带编号信息的短句可优先判断为列表；源样式为 Normal 的长说明段落应优先保持 body。",
        "",
        "说明体保持 body：形如“术语：说明文字”“属性名：说明文字”“原则名：长说明”的段落按正文处理。",
        "长段落保持 body：超过 100 字且没有 a) 1) 1. • 等列表标记时，不要转为 list_item。",
        "列表体才转 list_item：连续短句、平行结构、无冒号前缀，通常以 a) 1) 1. • 或短横线开头。",
        "",
        "重点审查以下场景：",
        "  - 连续 body 功能点列表（应转为 list_item）",
        "  - 封面元信息误判为 heading（如日期、版本号）",
        "  - 题注（caption）误判为 body 或 heading",
        "  - 正文误判为 heading（短句标题样式残留）",
    ])

    if hint:
        lines.append(f"\n用户提示: \"{hint}\"")
    return "\n".join(lines)


def _build_phase_b_prompt(
    model: dict,
    hint: str | None = None,
    *,
    targets_override: list[dict] | None = None,
    batch_meta: dict | None = None,
) -> str:
    """Build the Phase B prompt — context-aware caption text generation.

    The LLM acts as a *technical-document caption generator* (技术文档题注生成器):
    given context around each auto-generated empty caption, it produces concise
    caption text.

    Parameters
    ----------
    targets_override:
        When provided, only these caption targets are included in the prompt.
        Each target should have keys from ``_collect_caption_targets``.
    batch_meta:
        Optional dict with ``batch_index`` and ``batch_count`` for cross-batch
        context.
    """
    if targets_override is not None:
        targets = targets_override
    else:
        targets = _collect_caption_targets(model)

    lines = [
        "你是技术文档题注生成器。以下列出需要生成题注的表格。",
        "每个条目包含章节标题、前文、表格预览和后文内容。",
        "",
        "规则：",
        "1. 题注不超过30字，简洁准确",
        "2. 不要重复表/图前缀（框架会自动添加）",
        "3. 不改动已有题注（即有文字的caption）",
        "4. 如果信息不足以生成有意义的题注，设为空字符串",
        "5. 不允许生成set_table_type、set_header_rows、set_caption_type操作",
        "6. 只返回JSON对象，不要额外文字",
        "",
    ]
    if batch_meta:
        batch_idx = batch_meta.get("batch_index", 0)
        batch_cnt = batch_meta.get("batch_count", 1)
        lines.append(f"批处理: 第 {batch_idx + 1}/{batch_cnt} 批")
    lines.append("")

    if not targets:
        lines.append("（无需生成题注的表格）")
    else:
        for ti, tgt in enumerate(targets, 1):
            lines.append(f"--- target {ti} ---")
            lines.append(f"  block_id: {tgt.get('block_id', '?')}")
            htext = tgt.get("heading_text", "") or "(无章节标题)"
            lines.append(f"  章节标题: \"{htext}\"")
            pre = tgt.get("preceding_texts", [])
            if pre:
                for pt in pre:
                    lines.append(f"  前文: {pt[:120]}")
            preview = tgt.get("table_preview", "")
            if preview:
                lines.append(f"  表格预览:")
                for prow in preview.split("\n"):
                    lines.append(f"    | {prow}")
            fol = tgt.get("following_texts", [])
            if fol:
                for ft in fol:
                    lines.append(f"  后文: {ft[:120]}")
            lines.append("")

    example = _format_patch_example({
        "schema_version": "1.0",
        "phase": "B",
        "decisions": [
            {
                "block_id": "b0012",
                "operation": "set_caption_text",
                "to": {"text": "全球部署架构"},
                "confidence": 0.85,
                "reason": "context_caption_generated",
            },
        ],
    })
    lines.extend([
        "返回 JSON 格式（只允许 set_caption_text 操作）：",
        example,
    ])

    if hint:
        lines.append(f"\n用户提示: \"{hint}\"")
    return "\n".join(lines)


_BUILD_PROMPT: dict[str, Any] = {
    # Populated by _refresh_phase_exports() after registration below.
}


# ═══════════════════════════════════════════════════════════════════════
# 5.  Capability Registration
# ═══════════════════════════════════════════════════════════════════════

register_capability(CapabilityConfig(
    name="list_detect",
    allowed_ops=frozenset({"retype"}),
    prompt_builder=_build_phase_a_prompt,
    collector=_collect_list_detect_batches,
    batching="single",
    prevalidate=True,
    after_phase=_record_phase_a_applied_rate,
    prompt_args_builder=_prompt_args_for_list_detect,
))

register_capability(CapabilityConfig(
    name="caption_gen",
    allowed_ops=frozenset({"set_caption_text"}),
    prompt_builder=_build_phase_b_prompt,
    collector=_collect_caption_gen_batches,
    batching="by_targets",
    batch_size=PHASE_B_CAPTION_BATCH_SIZE,
    empty_status="no_targets",
    prevalidate=True,
    prompt_preview_text="(no caption targets)",
    prompt_args_builder=_prompt_args_for_caption_gen,
))

_register_phase_alias("A", "list_detect")
_register_phase_alias("B", "caption_gen")

_refresh_phase_exports()


# ═══════════════════════════════════════════════════════════════════════
# 6.  Core Enhancement Entry Point  (registry-based dispatch)
# ═══════════════════════════════════════════════════════════════════════

def _make_metric(
    phase: str,
    batch_idx: int,
    batch_count: int,
    prompt_chars: int,
    start: float,
    status: str,
    applied: int = 0,
    skipped: int = 0,
    batch_err: int = 0,
) -> dict:
    return {
        "phase": phase,
        "batch_index": batch_idx,
        "batch_count": batch_count,
        "prompt_chars": prompt_chars,
        "estimated_tokens": math.ceil(prompt_chars / 4),
        "wall_time_sec": round(time.perf_counter() - start, 3),
        "status": status,
        "applied_count": applied,
        "skipped_count": skipped,
        "error_count": batch_err,
    }


def enhance_document_model(
    model: dict,
    report: dict,
    *,
    phase: str,
    llm_call: Callable[[str], str] | None = None,
    hint: str | None = None,
) -> dict:
    """Run LLM enhancement for *phase* on document *model*.

    Unified dispatcher: resolves *phase* via ``CAPABILITY_REGISTRY``
    and runs the common collect → batch → call → validate → apply loop
    using the registered ``CapabilityConfig``.

    Parameters
    ----------
    model:
        Document AST dict (modified in-place when a patch is applied).
    report:
        Mutable report dict.  Enhancement activity is written to
        ``report['llm_enhancer']``.
    phase:
        Capability name (``"list_detect"``, ``"caption_gen"``) or
        legacy phase name (``"A"``, ``"B"``).
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

    # ── Resolve capability from registry ──────────────────────────
    cap_name = _resolve_capability(phase)
    desc = CAPABILITY_REGISTRY.get(cap_name) if cap_name else None

    if desc is None:
        enh["errors"].append({
            "phase": phase,
            "message": f"Unknown phase/capability {phase!r}",
        })
        return model

    allowed_ops = desc.allowed_ops

    # ── Collect items and build batches ───────────────────────────
    items = desc.collector(model, report)
    batches = _make_phase_batches(desc, items)

    if not batches:
        enh.setdefault("prompts", []).append(
            {"phase": phase, "text": desc.prompt_preview_text or "(no targets)"},
        )
        _append_phase_summary(enh, phase)
        enh.setdefault("phase_metrics", []).append({
            "phase": phase, "batch_index": 0,
            "batch_count": 1, "status": desc.empty_status,
            "applied_count": 0, "skipped_count": 0, "error_count": 0,
        })
        return model

    block_count = len(model.get("document", {}).get("blocks", []))
    timed_call = _resolve_llm_call(
        phase, llm_call, block_count=block_count, report_enh=enh,
    )

    batch_count = len(batches)

    for batch_idx, batch in enumerate(batches):
        prompt = _build_prompt_for_batch(
            desc, model, hint, batch, batch_idx, batch_count,
        )
        prompt = _apply_token_budget(prompt)
        enh.setdefault("prompts", []).append(
            {"phase": phase, "batch": batch_idx, "text": prompt[:500]},
        )

        prompt_chars = len(prompt)
        start = time.perf_counter()

        # ── Call LLM ──────────────────────────────────────────────
        try:
            raw = timed_call(prompt)
        except Exception as exc:
            enh["errors"].append({
                "phase": phase, "batch": batch_idx,
                "message": f"LLM call failed: {exc}",
            })
            _record_phase_metric(
                enh, _make_metric(phase, batch_idx, batch_count,
                                  prompt_chars, start, "error", batch_err=1),
            )
            continue

        if not raw:
            enh["errors"].append({
                "phase": phase, "batch": batch_idx,
                "message": "Empty LLM response",
            })
            _record_phase_metric(
                enh, _make_metric(phase, batch_idx, batch_count,
                                  prompt_chars, start, "empty_response",
                                  batch_err=1),
            )
            continue

        # ── Extract JSON patch ────────────────────────────────────
        patch = extract_json_object(raw)
        if patch is None:
            enh["errors"].append({
                "phase": phase, "batch": batch_idx,
                "message": "Failed to parse JSON from LLM response",
                "raw_preview": raw[:500],
            })
            _record_phase_metric(
                enh, _make_metric(phase, batch_idx, batch_count,
                                  prompt_chars, start, "parse_error",
                                  batch_err=1),
            )
            continue

        # ── Pre-validation (capability-specific) ──────────────────
        if desc.prevalidate:
            # Pass legacy phase name because LLM patches always use
            # legacy phase identifiers ("A" / "B") in their patch JSON.
            patch_phase = _resolve_legacy_phase(phase)
            pre_errors = _prevalidate_patch_schema(
                patch, patch_phase, allowed_ops,
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
                    enh, _make_metric(phase, batch_idx, batch_count,
                                      prompt_chars, start,
                                      "pre_validation_error", batch_err=1),
                )
                continue

        # ── Validation ───────────────────────────────────────────
        validation_errors = validate_patch(patch, model, allowed_ops)
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
                enh, _make_metric(phase, batch_idx, batch_count,
                                  prompt_chars, start, "validation_error",
                                  batch_err=1),
            )
            continue

        # ── Apply ────────────────────────────────────────────────
        before_applied = len(enh.get("applied", []))
        before_skipped = len(enh.get("skipped", []))
        before_errors = len(enh.get("errors", []))

        apply_patch_to_model(model, patch, report)

        _record_phase_metric(enh, _make_metric(
            phase, batch_idx, batch_count, prompt_chars, start,
            "ok",
            applied=len(enh.get("applied", [])) - before_applied,
            skipped=len(enh.get("skipped", [])) - before_skipped,
            batch_err=len(enh.get("errors", [])) - before_errors,
        ))

    _append_phase_summary(enh, phase)

    # ── After-phase hook ─────────────────────────────────────────
    if desc.after_phase:
        desc.after_phase(model, report, enh)

    return model

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
# 7b.  Mode Normalization
# ═══════════════════════════════════════════════════════════════════════

_ENHANCE_MODE_ALIASES: dict[str, str] = {
    "list_detect": "a",
    "caption_gen": "b",
    "all": "ab",
}


def normalize_mode(mode: str) -> str:
    """Normalize a user-supplied LLM enhancement mode to legacy values.

    Accepts ``"list_detect"``, ``"caption_gen"``, ``"all"`` (new names),
    as well as ``"a"``, ``"b"``, ``"ab"``, ``"abc"`` (legacy names),
    plus their ``"force-"`` variants, and ``"auto"`` / ``"off"``.

    Unknown modes are returned as-is.
    """
    return _ENHANCE_MODE_ALIASES.get(mode, mode)


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
        phase ``"A"``.
    ``"a"`` / ``"b"`` / ``"ab"``
        Force-enable the named phases (bypass suspicion check).
        ``"abc"`` is accepted as an alias for ``"ab"`` (backward compat).
    ``"force-a"`` / ``"force-b"`` / ``"force-ab"``
        Same as the non-*force* variants — reserved for future
        modification-rate gate override.
        ``"force-abc"`` is accepted as an alias for ``"force-ab"``.
    """
    # Resolve capability names (list_detect, caption_gen) to legacy phase
    # names (A, B) for internal phase-comparison logic.
    phase = _resolve_legacy_phase(phase)

    if mode == "off":
        return False

    # ── Manual / force modes ──
    force_map = {
        "a": {"A"},
        "b": {"B"},
        "ab": {"A", "B"},
        "abc": {"A", "B"},  # backward compat
        "force-a": {"A"},
        "force-b": {"B"},
        "force-ab": {"A", "B"},
        "force-abc": {"A", "B"},  # backward compat
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
        elif phase == "B":
            # Phase B: gate on Phase A's applied modification rate.
            llm_enhancer = report.get("llm_enhancer", {})
            applied_rate = llm_enhancer.get("phase_a_applied_rate", 0.0)
            return applied_rate >= 0.05
        else:
            # Unknown phase — never run.
            return False

    return False

