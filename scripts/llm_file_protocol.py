"""File-based LLM protocol: serialise prompts to JSONL and replay responses.

Supports a two-phase workflow:

1. **Generate** (``--generate-requests DIR``):
   Parse document, build all prompts, write ``llm_requests.jsonl`` + ``run.json``, stop.

2. **Resume** (``--resume RUN_JSON``):
   Read ``llm_responses.jsonl``, verify integrity (``input_hash``), validate each
   patch against the document model, apply approved patches, render final output.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any


# ── Constants ─────────────────────────────────────────────────────────

PROTOCOL_VERSION = "1.0"

# Fields included in the canonical hash — every field the agent saw.
_HASH_FIELDS = [
    "protocol_version",
    "run_id",
    "request_id",
    "phase",
    "capability",
    "prompt",
]

# Hash prefix for SHA-256.
_HASH_PREFIX = "sha256:"


# ── Run ID ────────────────────────────────────────────────────────────

def generate_run_id() -> str:
    """Return a globally unique run id (``<UTC-timestamp>-<32-bit-hex>``)."""
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    suffix = "%08x" % random.getrandbits(32)
    return f"{ts}-{suffix}"


# ── Hashing helpers ───────────────────────────────────────────────────

def _canonical_json(obj: dict) -> str:
    """Deterministic JSON with sorted keys and no extraneous whitespace."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_input_hash(request: dict) -> str:
    """SHA-256 of the canonical JSON of ``_HASH_FIELDS`` extracted from *request*.

    Returns a ``"sha256:<hex>"`` string.
    """
    payload = {k: request.get(k) for k in _HASH_FIELDS}
    canonical = _canonical_json(payload)
    return _HASH_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_source_sha256(path: Path) -> str:
    """SHA-256 of the source file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return _HASH_PREFIX + h.hexdigest()


# ── Builder helpers ───────────────────────────────────────────────────

def build_llm_request(
    run_id: str,
    request_id: str,
    phase: str,
    capability: str,
    prompt: str,
) -> dict:
    """Construct a single request entry for ``llm_requests.jsonl``."""
    req: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "request_id": request_id,
        "phase": phase,
        "capability": capability,
        "prompt": prompt,
    }
    req["input_hash"] = compute_input_hash(req)
    return req


def build_run_info(
    run_id: str,
    source_path: str,
    source_sha256: str,
    args: dict,
    work_dir: str,
) -> dict:
    """Construct the ``run.json`` metadata dict."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "args": args,
        "requests_path": str(Path(work_dir) / "llm_requests.jsonl"),
        "responses_path": str(Path(work_dir) / "llm_responses.jsonl"),
    }


# ── Batch / prompt collection ────────────────────────────────────────

def collect_phase_requests(
    model: dict,
    report: dict,
    *,
    phase: str,
    hint: str | None = None,
) -> list[dict]:
    """Build all LLM requests for *phase* without calling the LLM.

    Each returned dict contains ``request_id``, ``phase``, ``capability``,
    and ``prompt`` — ready for ``build_llm_request``.

    Raises ``ValueError`` when *phase* does not match a registered capability.
    """
    # Late import to avoid circular dependency at module level.
    from llm_enhancer import (
        _resolve_capability,
        _resolve_legacy_phase,
        CAPABILITY_REGISTRY,
        _make_phase_batches,
        _build_prompt_for_batch,
        _ensure_enhancer_report,
    )

    _ensure_enhancer_report(report)

    cap_name = _resolve_capability(phase)
    desc = CAPABILITY_REGISTRY.get(cap_name) if cap_name else None
    if desc is None:
        raise ValueError(f"Unknown phase/capability {phase!r}")

    items = desc.collector(model, report)
    batches = _make_phase_batches(desc, items)
    if not batches:
        return []

    requests: list[dict] = []
    legacy_phase = _resolve_legacy_phase(phase)
    for batch_idx, batch in enumerate(batches):
        prompt = _build_prompt_for_batch(
            desc, model, hint, batch, batch_idx, len(batches),
        )
        request_id = f"{legacy_phase}-{batch_idx:04d}"
        requests.append({
            "request_id": request_id,
            "phase": legacy_phase,
            "capability": cap_name,
            "prompt": prompt,
        })
    return requests


# ── I/O ───────────────────────────────────────────────────────────────

def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_requests_and_run(
    requests: list[dict],
    run_info: dict,
    work_dir: Path,
) -> tuple[Path, Path]:
    """Write ``llm_requests.jsonl`` and ``run.json`` under *work_dir*.

    Returns ``(requests_path, run_path)``.
    """
    _ensure_dir(work_dir)

    # Include run_id + protocol_version in each request line
    run_id = run_info["run_id"]
    full_requests = [
        build_llm_request(
            run_id=run_id,
            request_id=r["request_id"],
            phase=r["phase"],
            capability=r["capability"],
            prompt=r["prompt"],
        )
        for r in requests
    ]

    req_path = work_dir / "llm_requests.jsonl"
    with open(req_path, "w", encoding="utf-8") as f:
        for req in full_requests:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")

    run_path = work_dir / "run.json"
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(run_info, f, ensure_ascii=False, indent=2)

    return req_path, run_path


def read_run_info(path: Path) -> dict:
    """Read and validate ``run.json``."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError(
            f"Unknown protocol version {data.get('protocol_version')!r} "
            f"(expected {PROTOCOL_VERSION!r})"
        )
    return data


def read_requests(path: Path) -> list[dict]:
    """Read ``llm_requests.jsonl``; returns list of request dicts."""
    requests: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            requests.append(json.loads(line))
    return requests


def read_responses(path: Path) -> list[dict]:
    """Read ``llm_responses.jsonl``; returns list of response dicts."""
    responses: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            responses.append(json.loads(line))
    return responses


# ── Integrity validation ──────────────────────────────────────────────

class ProtocolError(Exception):
    """Raised when a protocol integrity check fails."""
    pass


def validate_response_schema(response: dict) -> None:
    """Check top-level fields of a response entry."""
    if response.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(
            f"Invalid protocol_version {response.get('protocol_version')!r}"
            f" in response (expected {PROTOCOL_VERSION!r})"
        )
    if not response.get("request_id"):
        raise ProtocolError("Missing request_id in response")
    if "raw_response" not in response:
        raise ProtocolError(
            f"Missing raw_response in response {response.get('request_id')}"
        )


def verify_response(request: dict, response: dict) -> None:
    """Verify *response* integrity against *request*.

    Checks:
    1. Top-level schema (``protocol_version``, ``request_id``, ``raw_response``).
    2. ``request_id`` matches between request and response.
    3. ``input_hash`` in the response matches the request's computed hash
       (detects tampering with the prompt the agent actually saw).
    """
    validate_response_schema(response)

    rid_resp = response.get("request_id", "")
    rid_req = request.get("request_id", "")
    if rid_resp != rid_req:
        raise ProtocolError(
            f"request_id mismatch: response has {rid_resp!r}, "
            f"request has {rid_req!r}"
        )

    # Recompute the hash that this response claims (if it carries one).
    claimed_hash = response.get("input_hash")
    expected_hash = request.get("input_hash", "")
    if claimed_hash and claimed_hash != expected_hash:
        # Tag-team verification: the response's claimed hash must equal
        # what the request recorded.  A mismatch means either the request
        # was tampered with after generation, or the response was forged.
        raise ProtocolError(
            f"input_hash mismatch for {rid_resp}: "
            f"response claims {claimed_hash}, "
            f"request has {expected_hash}"
        )


# ── Resume / Replay ───────────────────────────────────────────────────

def replay_phase_responses(
    model: dict,
    report: dict,
    *,
    phase: str,
    requests: list[dict],
    responses: list[dict],
    hint: str | None = None,
) -> dict:
    """Replay saved LLM responses for *phase* through validate + apply.

    Parameters
    ----------
    model:
        Document AST (mutated in-place when patches are applied).
    report:
        Mutable report dict — enhancement activity is recorded here.
    phase:
        Capability name (``"list_detect"``, ``"caption_gen"``) or legacy
        phase name (``"A"``, ``"B"``).
    requests:
        List of request dicts from ``llm_requests.jsonl``.
    responses:
        List of response dicts from ``llm_responses.jsonl``.
    hint:
        Optional user hint (passed through to prompt building for context).

    Returns
    -------
    The (potentially modified) *model*.
    """
    from llm_enhancer import (
        _resolve_capability,
        _resolve_legacy_phase,
        CAPABILITY_REGISTRY,
        _ensure_enhancer_report,
        _append_phase_summary,
        _record_phase_metric,
        _make_metric,
        _make_phase_batches,
        _build_prompt_for_batch,
        _prevalidate_patch_schema,
        extract_json_object,
        validate_patch,
        apply_patch_to_model,
    )

    enh = _ensure_enhancer_report(report)

    cap_name = _resolve_capability(phase)
    desc = CAPABILITY_REGISTRY.get(cap_name) if cap_name else None
    if desc is None:
        enh["errors"].append({
            "phase": phase,
            "message": f"Unknown phase/capability {phase!r}",
        })
        return model

    allowed_ops = desc.allowed_ops
    legacy_phase = _resolve_legacy_phase(phase)

    # Re-collect batches (deterministic from model).
    items = desc.collector(model, report)
    batches = _make_phase_batches(desc, items)
    if not batches:
        return model

    # Build lookups.
    resp_by_req_id: dict[str, dict] = {r["request_id"]: r for r in responses}
    req_by_id: dict[str, dict] = {r["request_id"]: r for r in requests}

    batch_count = len(batches)

    for batch_idx, batch in enumerate(batches):
        request_id = f"{legacy_phase}-{batch_idx:04d}"
        response = resp_by_req_id.get(request_id)
        request = req_by_id.get(request_id)

        # Rebuild prompt for diagnostic use (not sent to LLM).
        prompt = _build_prompt_for_batch(
            desc, model, hint, batch, batch_idx, batch_count,
        )
        prompt_chars = len(prompt)
        start = time.perf_counter()

        # ── Check response exists ──────────────────────────────────
        if response is None:
            enh["errors"].append({
                "phase": phase, "batch": batch_idx,
                "request_id": request_id,
                "message": f"No response found for {request_id}",
            })
            _record_phase_metric(
                enh, _make_metric(phase, batch_idx, batch_count,
                                  prompt_chars, start, "missing_response",
                                  batch_err=1),
            )
            continue

        # ── Integrity check ────────────────────────────────────────
        try:
            if request is None:
                raise ProtocolError(f"No request found for {request_id}")
            verify_response(request, response)
        except ProtocolError as exc:
            enh["errors"].append({
                "phase": phase, "batch": batch_idx,
                "request_id": request_id,
                "message": f"Verification failed: {exc}",
            })
            _record_phase_metric(
                enh, _make_metric(phase, batch_idx, batch_count,
                                  prompt_chars, start,
                                  "verification_error", batch_err=1),
            )
            continue

        # ── Extract JSON patch ─────────────────────────────────────
        raw = response.get("raw_response", "")
        patch = extract_json_object(raw)
        if patch is None:
            enh["errors"].append({
                "phase": phase, "batch": batch_idx,
                "request_id": request_id,
                "message": "Failed to parse JSON from raw_response",
                "raw_preview": raw[:500],
            })
            _record_phase_metric(
                enh, _make_metric(phase, batch_idx, batch_count,
                                  prompt_chars, start, "parse_error",
                                  batch_err=1),
            )
            continue

        # ── Pre-validation ─────────────────────────────────────────
        if desc.prevalidate:
            pre_errors = _prevalidate_patch_schema(
                patch, legacy_phase, allowed_ops,
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

        # ── Validation against model ───────────────────────────────
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
                                  prompt_chars, start,
                                  "validation_error", batch_err=1),
            )
            continue

        # ── Apply ─────────────────────────────────────────────────
        before_applied = len(enh.get("applied", []))
        before_skipped = len(enh.get("skipped", []))
        before_errors = len(enh.get("errors", []))

        apply_patch_to_model(model, patch, report)

        _record_phase_metric(enh, _make_metric(
            phase, batch_idx, batch_count, prompt_chars, start, "ok",
            applied=len(enh.get("applied", [])) - before_applied,
            skipped=len(enh.get("skipped", [])) - before_skipped,
            batch_err=len(enh.get("errors", [])) - before_errors,
        ))

    _append_phase_summary(enh, phase)

    # ── After-phase hook ───────────────────────────────────────────
    if desc.after_phase:
        desc.after_phase(model, report, enh)

    return model
