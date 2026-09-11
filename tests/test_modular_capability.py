from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_ast import _ast
from wxdf.enhancement import EnhancementExecutor, EnhancementRequest, build_native_request


def test_enhancement_executor_supports_stub_responses_and_budgeted_waiting() -> None:
    request = EnhancementRequest(capability="list_detect", phase="A", items=({"id": "p0"},), budget=1)
    executor = EnhancementExecutor()

    waiting = executor.execute(request)
    completed = executor.execute(request, responses=({"decision": "keep"},))

    assert waiting.status == "waiting_external"
    assert completed.status == "completed"
    assert completed.responses == ({"decision": "keep"},)


def test_enhancement_executor_covers_command_failure_timeout_and_digest_metadata() -> None:
    request = EnhancementRequest(capability="list_detect", phase="A", items=({"id": "p0"},), budget=1, request_id="req-1", model_revision=4, view_sha256="sha256:" + "b" * 64, timeout_seconds=1.0)
    success = EnhancementExecutor(command=[sys.executable, "-c", "import json; print(json.dumps({'decision':'keep'}))"]).execute(request)
    assert success.status == "completed"
    assert success.request_summary["model_revision"] == 4
    failed = EnhancementExecutor(command=[sys.executable, "-c", "import sys; sys.exit(3)"]).execute(request)
    assert failed.status == "failed"
    assert failed.diagnostics[0]["code"] == "ENHANCEMENT_CALL_FAILED"
    timed_request = EnhancementRequest(**{**request.__dict__, "timeout_seconds": 0.02})
    timed = EnhancementExecutor().execute(timed_request, call=lambda _payload: time.sleep(0.2))
    assert timed.status == "failed"
    assert timed.diagnostics[0]["code"] == "ENHANCEMENT_TIMEOUT"


def _native_candidate_set(ast: dict, *, target_id: str = "p0", base_revision: int | None = None) -> dict:
    source_ref = copy.deepcopy(ast["document"]["blocks"]["p0"]["source_ref"])
    return {
        "candidate_set_version": "2.0",
        "base_revision": ast["revision"] if base_revision is None else base_revision,
        "candidates": [
            {
                "candidate_id": "native-list-detect-p0",
                "module_id": "enhancement-list_detect",
                "module_version": "2.0",
                "target_source_refs": [source_ref],
                "target_node_ids": [target_id],
                "proposal": {
                    "operation": "update_node_semantics",
                    "property_scope": ["semantics"],
                    "value": {"semantic_role": "llm-classified"},
                },
                "positive_evidence": [{"kind": "llm"}],
                "negative_evidence": [],
                "scope": {"stages": ["role"]},
                "constraints": [],
                "rationale": "native capability classification",
                "confidence": 0.8,
            }
        ],
    }


def _native_patch_with_decision_target(ast: dict, target_id: str) -> dict:
    candidate_set = _native_candidate_set(ast)
    return {
        "patch_schema_version": "2.0",
        "patch_id": "native-patch",
        "base_revision": ast["revision"],
        "phase": "role",
        "candidates": candidate_set["candidates"],
        "decisions": [
            {
                "decision_id": "native-decision",
                "candidate_id": "native-list-detect-p0",
                "operation": "update_node_semantics",
                "target_node_ids": [target_id],
                "expected_revision": ast["revision"],
                "property_scope": ["semantics"],
                "value": {"semantic_role": "llm-classified"},
                "preconditions": [],
                "evidence": [{"kind": "llm"}],
                "invalidates": ["role"],
            }
        ],
    }


def test_native_enhancement_uses_an_immutable_versioned_view_and_accepts_candidates() -> None:
    ast = _ast(1)
    request = build_native_request("list_detect", ast, budget=1, request_id="native-1")
    mutation_errors: list[str] = []

    def adapter(payload: dict) -> dict:
        for label, mutate in (
            ("payload", lambda: payload.__setitem__("revision", 9)),
            ("payload_merge", lambda: payload.__ior__({"revision": 9})),
            ("view", lambda: payload["items"][0].__setitem__("revision", 9)),
            ("ast", lambda: payload["items"][0]["source_ast"]["document"]["blocks"]["p0"].__setitem__("role", "note")),
        ):
            try:
                mutate()
            except TypeError:
                mutation_errors.append(label)
        return _native_candidate_set(ast)

    result = EnhancementExecutor().execute(request, call=adapter)

    assert request.items[0]["view_schema_version"] == "2.0"
    assert request.items[0]["ast_schema_version"] == "2.0"
    assert mutation_errors == ["payload", "payload_merge", "view", "ast"]
    assert result.status == "completed"
    assert result.candidates[0]["candidate_id"] == "native-list-detect-p0"
    assert ast["document"]["blocks"]["p0"]["role"] == "body"


@pytest.mark.parametrize(
    ("case", "request_factory", "response_factory", "expected_status", "expected_code"),
    [
        (
            "no response",
            lambda ast: build_native_request("list_detect", ast, budget=1),
            lambda ast: "",
            "no_response",
            "NO_RESPONSE",
        ),
        (
            "invalid patch",
            lambda ast: build_native_request("list_detect", ast, budget=1),
            lambda ast: {"patch_schema_version": "1.0"},
            "invalid_patch",
            "INVALID_PATCH",
        ),
        (
            "stale revision",
            lambda ast: build_native_request("list_detect", ast, budget=1),
            lambda ast: _native_candidate_set(ast, base_revision=ast["revision"] - 1),
            "stale_target",
            "STALE_TARGET",
        ),
        (
            "missing target",
            lambda ast: build_native_request("list_detect", ast, budget=1),
            lambda ast: _native_candidate_set(ast, target_id="removed-node"),
            "stale_target",
            "STALE_TARGET",
        ),
        (
            "stale Patch decision target",
            lambda ast: build_native_request("list_detect", ast, budget=1),
            lambda ast: _native_patch_with_decision_target(ast, "removed-node"),
            "stale_target",
            "STALE_TARGET",
        ),
        (
            "budget exhausted",
            lambda ast: build_native_request("list_detect", ast, budget=0),
            lambda ast: _native_candidate_set(ast),
            "budget_exhausted",
            "ENHANCEMENT_BUDGET_EXCEEDED",
        ),
        (
            "missing capability",
            lambda ast: build_native_request("unregistered-capability", ast, budget=1),
            lambda ast: _native_candidate_set(ast),
            "missing_capability",
            "CAPABILITY_UNSUPPORTED",
        ),
    ],
)
def test_native_enhancement_returns_stable_statuses(
    case: str,
    request_factory,
    response_factory,
    expected_status: str,
    expected_code: str,
) -> None:
    ast = _ast(1)
    request = request_factory(ast)

    result = EnhancementExecutor().execute(request, call=lambda _payload: response_factory(ast))

    assert result.status == expected_status, case
    assert result.diagnostics[0]["code"] == expected_code


def test_native_enhancement_timeout_has_a_stable_status() -> None:
    ast = _ast(1)
    request = build_native_request("list_detect", ast, budget=1, timeout_seconds=0.01)

    result = EnhancementExecutor().execute(request, call=lambda _payload: time.sleep(0.1))

    assert result.status == "timeout"
    assert result.diagnostics[0]["code"] == "ENHANCEMENT_TIMEOUT"
