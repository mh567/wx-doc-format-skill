"""Versioned enhancement execution boundary and legacy capability aliases."""

from __future__ import annotations

import copy
import hashlib
import json
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from llm_enhancer import (  # noqa: F401  # re-exported compatibility surface
    CAPABILITY_REGISTRY,
    CapabilityConfig,
    _resolve_capability,
    _resolve_legacy_phase,
)

from .contracts import AST_SCHEMA_VERSION, ContractError, PATCH_SCHEMA_VERSION
from .validation import validate_ast, validate_patch


CAPABILITY_INPUT_SCHEMA_VERSION = "2.0"
LEGACY_PATCH_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class EnhancementRequest:
    """A serialisable request batch shared by online and replay adapters."""

    capability: str = ""
    phase: str | None = None
    items: tuple[Mapping[str, Any], ...] = ()
    budget: int | None = None
    timeout_seconds: float = 30.0
    request_id: str | None = None
    view_sha256: str | None = None
    model_revision: int | None = None
    source_refs: tuple[Mapping[str, Any], ...] = ()
    contract_versions: Mapping[str, str] = field(default_factory=lambda: {"capability_input": CAPABILITY_INPUT_SCHEMA_VERSION, "legacy_patch": LEGACY_PATCH_SCHEMA_VERSION})


@dataclass(frozen=True)
class EnhancementResult:
    status: str
    responses: tuple[Mapping[str, Any], ...] = ()
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    request_id: str | None = None
    consumed: int = 0
    request_summary: Mapping[str, Any] = field(default_factory=dict)
    candidates: tuple[Mapping[str, Any], ...] = ()

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "responses": [dict(item) for item in self.responses],
            "candidates": [dict(item) for item in self.candidates],
            "diagnostics": [dict(item) for item in self.diagnostics],
            "request_id": self.request_id,
            "consumed": self.consumed,
            "request_summary": dict(self.request_summary),
        }


def _digest(value: Any) -> str:
    payload = json.dumps(_thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _FrozenDict(dict):
    """JSON compatible mapping used for external read only views."""

    @staticmethod
    def _reject(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("mapping is read-only")

    __setitem__ = _reject
    __delitem__ = _reject
    clear = _reject
    pop = _reject
    popitem = _reject
    setdefault = _reject
    update = _reject
    __ior__ = _reject

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        result = {copy.deepcopy(key, memo): copy.deepcopy(value, memo) for key, value in self.items()}
        memo[id(self)] = result
        return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen = _FrozenDict()
        dict.__init__(frozen, {key: _freeze(item) for key, item in value.items()})
        return frozen
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return copy.deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def build_native_request(
    capability: str,
    source_ast: Mapping[str, Any],
    *,
    budget: int | None = None,
    timeout_seconds: float = 30.0,
    request_id: str | None = None,
) -> EnhancementRequest:
    """Build one revision bound immutable AST view for a native capability."""

    ast = copy.deepcopy(dict(source_ast))
    validate_ast(ast)
    view_sha256 = _digest(ast)
    view = _freeze(
        {
            "view_schema_version": CAPABILITY_INPUT_SCHEMA_VERSION,
            "ast_schema_version": ast["schema_version"],
            "revision": ast["revision"],
            "view_sha256": view_sha256,
            "source_ast": ast,
        }
    )
    return EnhancementRequest(
        capability=capability,
        phase=capability,
        items=(view,),
        budget=budget,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
        view_sha256=view_sha256,
        model_revision=int(ast["revision"]),
        contract_versions={
            "capability_input": CAPABILITY_INPUT_SCHEMA_VERSION,
            "ast": AST_SCHEMA_VERSION,
            "patch": PATCH_SCHEMA_VERSION,
        },
    )


def _coerce_request(request: EnhancementRequest | Mapping[str, Any]) -> EnhancementRequest:
    if isinstance(request, EnhancementRequest):
        return request
    if not isinstance(request, Mapping):
        raise TypeError("enhancement request must be an EnhancementRequest or mapping")
    raw_items = request.get("items", request.get("targets", ()))
    if isinstance(raw_items, Mapping):
        raw_items = (raw_items,)
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise TypeError("enhancement request items must be a sequence")
    raw_refs = request.get("source_refs", ())
    if isinstance(raw_refs, Mapping):
        raw_refs = (raw_refs,)
    return EnhancementRequest(
        capability=str(request.get("capability") or request.get("phase") or ""),
        phase=str(request["phase"]) if request.get("phase") is not None else None,
        items=tuple(dict(item) for item in raw_items if isinstance(item, Mapping)),
        budget=request.get("budget"),
        timeout_seconds=float(request.get("timeout_seconds", 30.0)),
        request_id=str(request["request_id"]) if request.get("request_id") is not None else None,
        view_sha256=request.get("view_sha256") or request.get("view_digest"),
        model_revision=request.get("model_revision"),
        source_refs=tuple(dict(ref) for ref in raw_refs if isinstance(ref, Mapping)),
        contract_versions=dict(request.get("contract_versions") or {"capability_input": CAPABILITY_INPUT_SCHEMA_VERSION, "legacy_patch": LEGACY_PATCH_SCHEMA_VERSION}),
    )


def _invoke_call(call: Callable[..., Any], payload: Mapping[str, Any]) -> Any:
    if hasattr(call, "execute") and callable(call.execute):
        return call.execute(payload)
    if hasattr(call, "invoke") and callable(call.invoke):
        return call.invoke(payload)
    return call(payload)


def _run_with_timeout(call: Callable[..., Any], payload: Mapping[str, Any], timeout: float) -> tuple[bool, Any, BaseException | None]:
    result: dict[str, Any] = {}
    error: list[BaseException] = []
    finished = threading.Event()

    def runner() -> None:
        try:
            result["value"] = _invoke_call(call, payload)
        except BaseException as exc:  # returned as a structured execution failure
            error.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=runner, name="wxdf-enhancement", daemon=True)
    thread.start()
    if not finished.wait(timeout=max(0.0, timeout)):
        return False, None, TimeoutError(f"enhancement call timed out after {timeout}s")
    if error:
        return True, None, error[0]
    return True, result.get("value"), None


def _normalise_responses(raw: Any, expected: int) -> tuple[Mapping[str, Any], ...]:
    if isinstance(raw, Mapping) and "responses" in raw:
        raw = raw["responses"]
    if isinstance(raw, Mapping):
        values = [raw]
    elif isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = list(raw)
    else:
        values = [{"raw": raw}]
    if len(values) != expected:
        raise ValueError(f"response count {len(values)} does not match item count {expected}")
    result: list[Mapping[str, Any]] = []
    for value in values:
        if isinstance(value, Mapping):
            result.append(dict(value))
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = {"raw": value}
            result.append(decoded if isinstance(decoded, Mapping) else {"raw": decoded})
        else:
            result.append({"raw": value})
    return tuple(result)


def _native_ast(request: EnhancementRequest) -> dict[str, Any] | None:
    if len(request.items) != 1:
        return None
    view = request.items[0]
    if not isinstance(view, Mapping) or view.get("view_schema_version") != CAPABILITY_INPUT_SCHEMA_VERSION:
        return None
    ast = view.get("source_ast")
    if not isinstance(ast, Mapping):
        return None
    return _thaw(ast)


def _no_response(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str) and not raw.strip():
        return True
    if isinstance(raw, Mapping) and "responses" in raw:
        responses = raw.get("responses")
        return isinstance(responses, Sequence) and not isinstance(responses, (str, bytes)) and len(responses) == 0
    return isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 0


def _source_ref_matches(expected: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    for field in ("source_id", "part", "content_sha256", "span", "node_path", "parser_version"):
        if field in expected or field in current:
            if expected.get(field) != current.get(field):
                return False
    return True


def _validate_candidate_targets(candidates: Sequence[Mapping[str, Any]], ast: Mapping[str, Any]) -> None:
    document = ast["document"]
    nodes = {**document["blocks"], **document["containers"]}
    for candidate in candidates:
        targets = [str(value) for value in candidate.get("target_node_ids") or []]
        if not targets or any(target not in nodes for target in targets):
            raise LookupError("candidate target is absent from the current AST revision")
        current_refs = [
            nodes[target].get("source_ref")
            for target in targets
            if isinstance(nodes[target].get("source_ref"), Mapping)
        ]
        expected_refs = [
            ref for ref in candidate.get("target_source_refs") or [] if isinstance(ref, Mapping)
        ]
        if not expected_refs or not current_refs:
            raise LookupError("candidate source reference is absent from the current AST revision")
        if not all(any(_source_ref_matches(expected, current) for current in current_refs) for expected in expected_refs):
            raise LookupError("candidate source reference changed after the request")


def _extract_native_candidates(
    response: Mapping[str, Any],
    ast: Mapping[str, Any],
    view_sha256: str | None,
) -> tuple[Mapping[str, Any], ...]:
    current_revision = int(ast["revision"])
    payload = _thaw(response)
    if "patch_schema_version" in payload:
        validate_patch(payload)
        if payload["base_revision"] != current_revision:
            raise LookupError("Patch base revision is stale")
        for decision in payload["decisions"]:
            if decision["expected_revision"] != current_revision:
                raise LookupError("Patch decision revision is stale")
            if any(
                str(target) not in ast["document"]["blocks"]
                and str(target) not in ast["document"]["containers"]
                for target in decision["target_node_ids"]
            ):
                raise LookupError("Patch decision target is absent from the current AST revision")
        candidates = payload["candidates"]
    elif "candidate_set_version" in payload:
        if payload.get("candidate_set_version") != PATCH_SCHEMA_VERSION:
            raise ContractError("unsupported_version", "candidate_set_version", "unsupported CandidateSet version", contract="CandidateSet 2.0")
        if payload.get("base_revision") != current_revision:
            raise LookupError("CandidateSet base revision is stale")
        response_view_sha256 = payload.get("view_sha256")
        if response_view_sha256 is not None and response_view_sha256 != view_sha256:
            raise LookupError("CandidateSet view digest is stale")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise ContractError("invalid_type", "candidates", "CandidateSet candidates must be a list", contract="CandidateSet 2.0")
        validate_patch(
            {
                "patch_schema_version": PATCH_SCHEMA_VERSION,
                "patch_id": "candidate-set-validation",
                "base_revision": current_revision,
                "phase": "relation",
                "candidates": candidates,
                "decisions": [],
            }
        )
    else:
        raise ContractError("missing_required", "$", "response must be Patch 2.0 or CandidateSet 2.0", contract="native enhancement")
    _validate_candidate_targets(candidates, ast)
    return tuple(_freeze(candidate) for candidate in candidates)


class EnhancementExecutor:
    """Run an enhancement batch through an injected external adapter.

    No network or paid model call is created by this class.  A callable, a
    command adapter, or a replayed response must be provided by the caller.
    """

    def __init__(self, registry: Mapping[str, CapabilityConfig] | None = None, command: Sequence[str] | str | None = None) -> None:
        self.registry = registry if registry is not None else CAPABILITY_REGISTRY
        if isinstance(command, str):
            self.command = tuple(shlex.split(command))
        elif command is None:
            self.command = None
        else:
            self.command = tuple(str(value) for value in command)

    def execute(
        self,
        request: EnhancementRequest | Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
        *,
        responses: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        call: Callable[..., Any] | Any | None = None,
        llm_call: Callable[..., Any] | Any | None = None,
    ) -> EnhancementResult:
        request = _coerce_request(request)
        context = dict(context or {})
        native_ast = _native_ast(request)
        native = native_ast is not None
        phase = request.phase or request.capability
        capability = _resolve_capability(request.capability or phase)
        summary = {
            "request_id": request.request_id,
            "capability": capability or request.capability,
            "phase": _resolve_legacy_phase(phase),
            "model_revision": request.model_revision if request.model_revision is not None else context.get("model_revision"),
            "view_sha256": request.view_sha256 or context.get("view_sha256"),
            "source_refs": [dict(ref) for ref in request.source_refs],
            "contracts": dict(request.contract_versions),
            "items_sha256": _digest([dict(item) for item in request.items]),
        }
        if capability is None or capability not in self.registry:
            status = "missing_capability" if native else "unsupported"
            return EnhancementResult(status, diagnostics=({"code": "CAPABILITY_UNSUPPORTED", "capability": request.capability},), request_id=request.request_id, request_summary=summary)
        if not request.items:
            return EnhancementResult("completed", diagnostics=({"code": "NO_TARGETS"},), request_id=request.request_id, request_summary=summary)
        if request.budget is not None and (not isinstance(request.budget, int) or request.budget < 0):
            return EnhancementResult("failed", diagnostics=({"code": "INVALID_BUDGET"},), request_id=request.request_id, request_summary=summary)
        if request.budget is not None and len(request.items) > request.budget:
            status = "budget_exhausted" if native else "failed"
            return EnhancementResult(status, diagnostics=({"code": "ENHANCEMENT_BUDGET_EXCEEDED", "required": len(request.items), "remaining": request.budget},), request_id=request.request_id, consumed=0, request_summary=summary)

        if responses is not None:
            if native and _no_response(responses):
                return EnhancementResult("no_response", diagnostics=({"code": "NO_RESPONSE"},), request_id=request.request_id, request_summary=summary)
            try:
                normalised = _normalise_responses(responses, len(request.items))
            except ValueError as exc:
                status = "invalid_patch" if native else "failed"
                return EnhancementResult(status, diagnostics=({"code": "RESPONSE_COUNT_MISMATCH", "message": str(exc)},), request_id=request.request_id, request_summary=summary)
            identity_error = self._validate_response_identity(request, normalised)
            if identity_error is not None:
                status = "stale_target" if native and identity_error.get("code") == "STALE_RESPONSE" else "invalid_patch" if native else "failed"
                return EnhancementResult(status, diagnostics=(identity_error,), request_id=request.request_id, request_summary=summary)
            if native_ast is not None:
                return self._native_result(request, summary, normalised, native_ast, consumed=0)
            return EnhancementResult("completed", responses=normalised, request_id=request.request_id, consumed=0, request_summary=summary)

        adapter = call if call is not None else llm_call
        if adapter is None and self.command:
            def adapter(payload: Mapping[str, Any]) -> str:
                try:
                    completed_process = subprocess.run(
                        list(self.command),
                        input=json.dumps(payload, ensure_ascii=False),
                        text=True,
                        capture_output=True,
                        timeout=max(0.0, request.timeout_seconds),
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise TimeoutError(f"enhancement command timed out after {request.timeout_seconds}s") from exc
                if completed_process.returncode != 0:
                    raise RuntimeError(f"enhancement command exited {completed_process.returncode}: {completed_process.stderr[:200]}")
                return completed_process.stdout
        if adapter is None:
            return EnhancementResult("waiting_external", diagnostics=({"code": "WAITING_EXTERNAL", "request": summary},), request_id=request.request_id, request_summary=summary)
        payload = {
            **summary,
            "items": [dict(item) for item in request.items],
            "context": context,
            "prompt": context.get("prompt"),
        }
        completed, raw, error = _run_with_timeout(adapter, _freeze(payload), request.timeout_seconds)
        consumed = len(request.items)
        if isinstance(error, TimeoutError):
            status = "timeout" if native else "failed"
            return EnhancementResult(status, diagnostics=({"code": "ENHANCEMENT_TIMEOUT", "message": str(error)},), request_id=request.request_id, consumed=consumed, request_summary=summary)
        if error is not None:
            return EnhancementResult("failed", diagnostics=({"code": "ENHANCEMENT_CALL_FAILED", "message": str(error)},), request_id=request.request_id, consumed=consumed, request_summary=summary)
        if native and _no_response(raw):
            return EnhancementResult("no_response", diagnostics=({"code": "NO_RESPONSE"},), request_id=request.request_id, consumed=consumed, request_summary=summary)
        try:
            normalised = _normalise_responses(raw, len(request.items))
        except ValueError as exc:
            status = "invalid_patch" if native else "failed"
            return EnhancementResult(status, diagnostics=({"code": "RESPONSE_COUNT_MISMATCH", "message": str(exc)},), request_id=request.request_id, consumed=consumed, request_summary=summary)
        identity_error = self._validate_response_identity(request, normalised)
        if identity_error is not None:
            status = "stale_target" if native and identity_error.get("code") == "STALE_RESPONSE" else "invalid_patch" if native else "failed"
            return EnhancementResult(status, diagnostics=(identity_error,), request_id=request.request_id, consumed=consumed, request_summary=summary)
        if native_ast is not None:
            return self._native_result(request, summary, normalised, native_ast, consumed=consumed)
        return EnhancementResult("completed", responses=normalised, request_id=request.request_id, consumed=consumed, request_summary=summary)

    @staticmethod
    def _native_result(
        request: EnhancementRequest,
        summary: Mapping[str, Any],
        responses: Sequence[Mapping[str, Any]],
        ast: Mapping[str, Any],
        *,
        consumed: int,
    ) -> EnhancementResult:
        candidates: list[Mapping[str, Any]] = []
        try:
            for response in responses:
                candidates.extend(_extract_native_candidates(response, ast, request.view_sha256))
        except LookupError as exc:
            return EnhancementResult(
                "stale_target",
                responses=tuple(responses),
                diagnostics=({"code": "STALE_TARGET", "message": str(exc)},),
                request_id=request.request_id,
                consumed=consumed,
                request_summary=summary,
            )
        except (ContractError, TypeError, ValueError, KeyError) as exc:
            return EnhancementResult(
                "invalid_patch",
                responses=tuple(responses),
                diagnostics=({"code": "INVALID_PATCH", "message": str(exc)},),
                request_id=request.request_id,
                consumed=consumed,
                request_summary=summary,
            )
        return EnhancementResult(
            "completed",
            responses=tuple(responses),
            candidates=tuple(candidates),
            request_id=request.request_id,
            consumed=consumed,
            request_summary=summary,
        )

    @staticmethod
    def _validate_response_identity(request: EnhancementRequest, responses: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        response_ids = [response.get("response_id") for response in responses if response.get("response_id") is not None]
        if len(response_ids) != len(set(response_ids)):
            return {"code": "REUSED_RESPONSE_ID"}
        if request.request_id:
            mismatched = [response.get("request_id") for response in responses if response.get("request_id") is not None and response.get("request_id") != request.request_id]
            if mismatched:
                return {"code": "STALE_RESPONSE", "request_id": request.request_id}
        return None


__all__ = [
    "CAPABILITY_INPUT_SCHEMA_VERSION",
    "CapabilityConfig",
    "EnhancementExecutor",
    "EnhancementRequest",
    "EnhancementResult",
    "CAPABILITY_REGISTRY",
    "build_native_request",
]
