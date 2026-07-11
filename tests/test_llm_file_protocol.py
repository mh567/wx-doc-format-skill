"""Tests for llm_file_protocol.py — the file-based LLM protocol.

Covers request collection, I/O, integrity validation, and response replay.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# ruff: noqa: E402 — imports after sys.path manipulation

from document_model import new_document_model
from llm_enhancer import PATCH_SCHEMA_VERSION
from llm_file_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    build_llm_request,
    build_run_info,
    collect_phase_requests,
    compute_input_hash,
    compute_source_sha256,
    generate_run_id,
    read_requests,
    read_responses,
    read_run_info,
    replay_phase_responses,
    validate_response_schema,
    verify_response,
    write_requests_and_run,
)


# ═════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════

def _block(bid: str, block_type: str = "body", **kwargs):
    """Build a minimal block dict."""
    b = {"id": bid, "block_type": block_type, "text": kwargs.pop("text", "test content")}
    b.update(kwargs)
    return b


def make_minimal_model(blocks=None):
    """Return a minimal document model, optionally with *blocks*."""
    model = new_document_model("test.md", "md", "test")
    if blocks is not None:
        model["document"]["blocks"] = blocks
    return model


def make_empty_patch(phase: str = "A") -> str:
    """Return a valid empty-decisions patch JSON string."""
    return json.dumps({
        "schema_version": PATCH_SCHEMA_VERSION,
        "phase": phase,
        "decisions": [],
    }, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════════
#  run_id
# ═════════════════════════════════════════════════════════════════════

class TestGenerateRunId:
    def test_format(self):
        rid = generate_run_id()
        # Format: YYYYMMDDTHHMMSSZ-8hex
        assert len(rid) == 25, f"Expected 25 chars, got {len(rid)}: {rid}"
        assert rid[8] == "T"
        assert rid[15] == "Z"
        assert rid[16] == "-"
        assert all(c in "0123456789abcdef" for c in rid[17:])

    def test_unique(self):
        ids = {generate_run_id() for _ in range(10)}
        assert len(ids) == 10


# ═════════════════════════════════════════════════════════════════════
#  compute_input_hash
# ═════════════════════════════════════════════════════════════════════

class TestComputeInputHash:
    def test_hash_format(self):
        req = build_llm_request(
            run_id="20260101T000000Z-00000001",
            request_id="A-0000",
            phase="A",
            capability="list_detect",
            prompt="test prompt",
        )
        h = compute_input_hash(req)
        assert h.startswith("sha256:")
        assert len(h) == 64 + 7  # "sha256:" + 64 hex chars

    def test_deterministic(self):
        req1 = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="hello",
        )
        req2 = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="hello",
        )
        assert req1["input_hash"] == req2["input_hash"]

    def test_changes_with_prompt(self):
        req1 = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="hello",
        )
        req2 = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="world",
        )
        assert req1["input_hash"] != req2["input_hash"]


# ═════════════════════════════════════════════════════════════════════
#  build_llm_request
# ═════════════════════════════════════════════════════════════════════

class TestBuildLlmRequest:
    def test_minimal(self):
        req = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="hello",
        )
        assert req["protocol_version"] == PROTOCOL_VERSION
        assert req["run_id"] == "R1"
        assert req["request_id"] == "A-0000"
        assert req["phase"] == "A"
        assert req["capability"] == "list_detect"
        assert req["prompt"] == "hello"
        assert req["input_hash"].startswith("sha256:")


# ═════════════════════════════════════════════════════════════════════
#  build_run_info
# ═════════════════════════════════════════════════════════════════════

class TestBuildRunInfo:
    def test_minimal(self):
        info = build_run_info(
            run_id="R1",
            source_path="/tmp/test.docx",
            source_sha256="sha256:abc",
            args={"input": "/tmp/test.docx", "llm_enhance": "ab"},
            work_dir="/tmp/work",
        )
        assert info["protocol_version"] == PROTOCOL_VERSION
        assert info["run_id"] == "R1"
        assert info["args"]["llm_enhance"] == "ab"
        assert "llm_requests.jsonl" in info["requests_path"]


# ═════════════════════════════════════════════════════════════════════
#  collect_phase_requests
# ═════════════════════════════════════════════════════════════════════

class TestCollectPhaseRequests:
    def test_phase_a_returns_one_request(self):
        """Phase A (list_detect) with suspicious blocks should produce one request."""
        blocks = [
            _block("b0001", "body", text="Short para"),
            _block("b0002", "body", text="Another short"),
        ]
        model = make_minimal_model(blocks)
        report = {
            "parse_report": {
                "ambiguous_short_paragraphs": 2,
            },
        }
        reqs = collect_phase_requests(model, report, phase="A")
        assert len(reqs) == 1
        assert reqs[0]["phase"] == "A"
        assert reqs[0]["capability"] == "list_detect"
        assert "prompt" in reqs[0]

    def test_phase_a_clean_document_returns_one_request(self):
        """Even a clean document produces one batch for list_detect."""
        blocks = [_block("b0001", "heading", text="Clean doc")]
        model = make_minimal_model(blocks)
        report = {"parse_report": {}}
        reqs = collect_phase_requests(model, report, phase="A")
        assert len(reqs) == 1

    def test_caption_gen_no_targets_returns_empty(self):
        """Phase B with no auto-generated captions returns empty."""
        blocks = [_block("b0001", "body", text="No tables here")]
        model = make_minimal_model(blocks)
        report = {}
        reqs = collect_phase_requests(model, report, phase="caption_gen")
        assert reqs == []

    def test_phase_b_with_captions_returns_requests(self):
        """Phase B with auto-generated captions should return batch requests."""
        blocks = [
            _block("c0001", "caption", text="", _auto_generated=True, caption_type="table"),
            _block("t0001", "table", table_type="data", rows=[[{"text": "cell1"}]]),
        ]
        model = make_minimal_model(blocks)
        report = {}
        reqs = collect_phase_requests(model, report, phase="B")
        assert len(reqs) >= 1
        assert reqs[0]["phase"] == "B"
        assert reqs[0]["capability"] == "caption_gen"

    def test_unknown_phase_raises_value_error(self):
        model = make_minimal_model()
        report = {}
        try:
            collect_phase_requests(model, report, phase="X")
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_request_id_format(self):
        blocks = [_block("b0001", "body", text="Short")]
        model = make_minimal_model(blocks)
        report = {"parse_report": {"ambiguous_short_paragraphs": 1}}
        reqs = collect_phase_requests(model, report, phase="A")
        assert len(reqs) == 1
        rid = reqs[0]["request_id"]
        assert rid.startswith("A-")
        assert len(rid) == 6  # "A-0000"


# ═════════════════════════════════════════════════════════════════════
#  File I/O (write + read back)
# ═════════════════════════════════════════════════════════════════════

class TestFileIO:
    def test_write_and_read_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / ".wx-doc-format"
            run_id = generate_run_id()

            requests = [
                build_llm_request(run_id, "A-0000", "A", "list_detect", "prompt 1"),
                build_llm_request(run_id, "B-0000", "B", "caption_gen", "prompt 2"),
            ]
            run_info = build_run_info(
                run_id=run_id,
                source_path="/tmp/test.docx",
                source_sha256="sha256:abc",
                args={"input": "/tmp/test.docx", "llm_enhance": "ab"},
                work_dir=str(work),
            )

            write_requests_and_run(requests, run_info, work)

            # Verify files exist
            assert (work / "llm_requests.jsonl").exists()
            assert (work / "run.json").exists()

            # Read back requests
            read_reqs = read_requests(work / "llm_requests.jsonl")
            assert len(read_reqs) == 2
            assert read_reqs[0]["request_id"] == "A-0000"
            assert read_reqs[1]["request_id"] == "B-0000"
            assert read_reqs[0]["protocol_version"] == PROTOCOL_VERSION

            # Read back run.json
            read_info = read_run_info(work / "run.json")
            assert read_info["run_id"] == run_id
            assert read_info["args"]["llm_enhance"] == "ab"

    def test_skip_empty_lines_in_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"
            # Write a JSONL with an empty line
            with open(path, "w") as f:
                f.write('{"a": 1}\n')
                f.write("\n")
                f.write('{"b": 2}\n')

            data = read_requests(path)
            assert len(data) == 2

    def test_read_run_info_invalid_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            with open(path, "w") as f:
                json.dump({"protocol_version": "0.0"}, f)
            try:
                read_run_info(path)
                assert False, "Expected ValueError"
            except ValueError:
                pass

    def test_write_requests_via_collect_then_read(self):
        """End-to-end: collect → write → read back with hashes filled in."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / ".wx-doc-format"
            run_id = generate_run_id()

            blocks = [
                _block("b0001", "body", text="Short para"),
                _block("b0002", "body", text="Another short"),
            ]
            model = make_minimal_model(blocks)
            report = {"parse_report": {"ambiguous_short_paragraphs": 2}}
            reqs = collect_phase_requests(model, report, phase="A")

            run_info = build_run_info(
                run_id=run_id,
                source_path="/tmp/test.md",
                source_sha256="sha256:abc",
                args={"input": "/tmp/test.md", "llm_enhance": "a"},
                work_dir=str(work),
            )

            write_requests_and_run(reqs, run_info, work)

            read_reqs = read_requests(work / "llm_requests.jsonl")
            assert len(read_reqs) == 1
            assert "input_hash" in read_reqs[0]
            assert read_reqs[0]["protocol_version"] == PROTOCOL_VERSION


# ═════════════════════════════════════════════════════════════════════
#  validate_response_schema
# ═════════════════════════════════════════════════════════════════════

class TestValidateResponseSchema:
    def test_valid_response(self):
        resp = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "A-0000",
            "raw_response": '{"schema_version":"1.0"}',
        }
        # Should not raise
        validate_response_schema(resp)

    def test_missing_protocol_version(self):
        resp = {"request_id": "A-0000", "raw_response": "{}"}
        try:
            validate_response_schema(resp)
            assert False, "Expected ProtocolError"
        except ProtocolError:
            pass

    def test_wrong_protocol_version(self):
        resp = {
            "protocol_version": "0.0",
            "request_id": "A-0000",
            "raw_response": "{}",
        }
        try:
            validate_response_schema(resp)
            assert False, "Expected ProtocolError"
        except ProtocolError:
            pass

    def test_missing_request_id(self):
        resp = {
            "protocol_version": PROTOCOL_VERSION,
            "raw_response": "{}",
        }
        try:
            validate_response_schema(resp)
            assert False, "Expected ProtocolError"
        except ProtocolError:
            pass

    def test_missing_raw_response(self):
        resp = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "A-0000",
        }
        try:
            validate_response_schema(resp)
            assert False, "Expected ProtocolError"
        except ProtocolError:
            pass


# ═════════════════════════════════════════════════════════════════════
#  verify_response  (integrity check)
# ═════════════════════════════════════════════════════════════════════

class TestVerifyResponse:
    def test_valid_response_passes(self):
        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="test",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "A-0000",
            "raw_response": '{"schema_version":"1.0","decisions":[]}',
            "input_hash": request["input_hash"],
        }
        # Should not raise
        verify_response(request, response)

    def test_request_id_mismatch(self):
        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="test",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "B-0000",  # different
            "raw_response": "{}",
        }
        try:
            verify_response(request, response)
            assert False, "Expected ProtocolError"
        except ProtocolError as e:
            assert "request_id mismatch" in str(e)

    def test_input_hash_tampered(self):
        """Simulate tampering: response carries a different input_hash."""
        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="original prompt",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "A-0000",
            "raw_response": "{}",
            "input_hash": "sha256:" + "0" * 64,  # fake hash
        }
        try:
            verify_response(request, response)
            assert False, "Expected ProtocolError"
        except ProtocolError as e:
            assert "input_hash mismatch" in str(e)

    def test_input_hash_absent_in_response_still_works(self):
        """When the response lacks input_hash, verification passes (backward compat)."""
        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="test",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "A-0000",
            "raw_response": "{}",
            # no input_hash
        }
        # Should not raise (input_hash is optional in response for backward compat)
        verify_response(request, response)


# ═════════════════════════════════════════════════════════════════════
#  compute_source_sha256
# ═════════════════════════════════════════════════════════════════════

class TestComputeSourceSha256:
    def test_compute_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.txt"
            path.write_text("hello world", encoding="utf-8")
            h = compute_source_sha256(path)
            assert h.startswith("sha256:")
            assert len(h) == 64 + 7

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.txt"
            path.write_text("same content", encoding="utf-8")
            h1 = compute_source_sha256(path)
            h2 = compute_source_sha256(path)
            assert h1 == h2

    def test_different_files_different_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.txt"
            b = Path(tmp) / "b.txt"
            a.write_text("content A", encoding="utf-8")
            b.write_text("content B", encoding="utf-8")
            assert compute_source_sha256(a) != compute_source_sha256(b)


# ═════════════════════════════════════════════════════════════════════
#  replay_phase_responses
# ═════════════════════════════════════════════════════════════════════

class TestReplayPhaseResponses:
    def test_replay_applies_valid_patch(self):
        """Resume mode should validate and apply patches from saved responses."""
        blocks = [
            _block("b0001", "body", text="First item"),
            _block("b0002", "body", text="Second item"),
        ]
        model = make_minimal_model(blocks)
        report: dict = {}
        patch = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "phase": "A",
            "decisions": [{
                "block_id": "b0001",
                "operation": "retype",
                "from": {"block_type": "body"},
                "to": {"block_type": "list_item", "level": 0,
                        "list_type": "lower_letter_paren"},
                "confidence": 0.85,
                "reason": "consecutive_functional_points",
            }],
        }

        run_id = generate_run_id()
        request = build_llm_request(
            run_id=run_id, request_id="A-0000",
            phase="A", capability="list_detect",
            prompt="test prompt (non-empty)",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": run_id,
            "request_id": "A-0000",
            "raw_response": json.dumps(patch, ensure_ascii=False),
            "input_hash": request["input_hash"],
        }

        result = replay_phase_responses(
            model, report, phase="A",
            requests=[request],
            responses=[response],
        )
        assert result["document"]["blocks"][0]["block_type"] == "list_item"
        assert len(report["llm_enhancer"]["applied"]) == 1

    def test_replay_missing_response_is_error(self):
        """When a response is missing, an error should be recorded."""
        blocks = [_block("b0001", "body")]
        model = make_minimal_model(blocks)
        report: dict = {}

        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="test",
        )

        replay_phase_responses(
            model, report, phase="A",
            requests=[request],
            responses=[],  # no responses
        )
        assert len(report["llm_enhancer"]["errors"]) >= 1

    def test_replay_input_hash_tampered_is_error(self):
        """Tampered input_hash should cause verification error."""
        blocks = [_block("b0001", "body")]
        model = make_minimal_model(blocks)
        report: dict = {}

        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="original",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "A-0000",
            "raw_response": "{}",
            "input_hash": "sha256:" + "f" * 64,  # wrong hash
        }

        replay_phase_responses(
            model, report, phase="A",
            requests=[request],
            responses=[response],
        )
        assert len(report["llm_enhancer"]["errors"]) >= 1

    def test_replay_bad_json_is_error(self):
        """Invalid raw_response JSON should be an error."""
        blocks = [_block("b0001", "body")]
        model = make_minimal_model(blocks)
        report: dict = {}

        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="test",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "A-0000",
            "raw_response": "not valid json",
            "input_hash": request["input_hash"],
        }

        replay_phase_responses(
            model, report, phase="A",
            requests=[request],
            responses=[response],
        )
        assert len(report["llm_enhancer"]["errors"]) >= 1

    def test_replay_still_validates_patch_against_model(self):
        """Even from a response, the patch is validated against the model."""
        blocks = [_block("b0001", "body")]
        model = make_minimal_model(blocks)
        report: dict = {}
        patch = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "phase": "A",
            "decisions": [{
                "block_id": "b9999",  # nonexistent
                "operation": "retype",
                "to": {"block_type": "list_item"},
                "confidence": 0.95,
            }],
        }

        request = build_llm_request(
            run_id="R1", request_id="A-0000",
            phase="A", capability="list_detect", prompt="test",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "A-0000",
            "raw_response": json.dumps(patch, ensure_ascii=False),
            "input_hash": request["input_hash"],
        }

        replay_phase_responses(
            model, report, phase="A",
            requests=[request],
            responses=[response],
        )
        # The patch is validated → error because b9999 not found
        assert len(report["llm_enhancer"]["errors"]) >= 1
        assert not report["llm_enhancer"]["applied"]

    def test_replay_caption_gen_applies_caption_text(self):
        """Phase B replay should apply set_caption_text."""
        blocks = [
            _block("c0001", "caption", text="", _auto_generated=True, caption_type="table"),
            _block("t0001", "table", table_type="data", rows=[[{"text": "cell1"}]]),
        ]
        model = make_minimal_model(blocks)
        report: dict = {}
        patch = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "phase": "B",
            "decisions": [{
                "block_id": "c0001",
                "operation": "set_caption_text",
                "to": {"text": "系统功能表"},
                "confidence": 0.85,
                "reason": "caption_text_generated",
            }],
        }

        request = build_llm_request(
            run_id="R1", request_id="B-0000",
            phase="B", capability="caption_gen", prompt="test",
        )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": "R1",
            "request_id": "B-0000",
            "raw_response": json.dumps(patch, ensure_ascii=False),
            "input_hash": request["input_hash"],
        }

        result = replay_phase_responses(
            model, report, phase="B",
            requests=[request],
            responses=[response],
        )
        assert result["document"]["blocks"][0]["text"] == "系统功能表"
        assert len(report["llm_enhancer"]["applied"]) == 1

    def test_unknown_phase_is_error(self):
        model = make_minimal_model()
        report: dict = {}
        replay_phase_responses(
            model, report, phase="X",
            requests=[], responses=[],
        )
        assert len(report["llm_enhancer"]["errors"]) >= 1


# ═════════════════════════════════════════════════════════════════════
#  End-to-end: collect → write → read → replay
# ═════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_collect_write_read_replay(self):
        """Full round-trip: collect prompts, write files, read back, replay."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / ".wx-doc-format"
            run_id = generate_run_id()

            # ── Collect Phase A request ────────────────────────────
            blocks = [
                _block("b0001", "body", text="Short ambiguous"),
                _block("b0002", "body", text="Another short"),
            ]
            model = make_minimal_model(blocks)
            report: dict = {"parse_report": {"ambiguous_short_paragraphs": 2}}
            reqs = collect_phase_requests(model, report, phase="A")
            assert len(reqs) == 1

            # ── Write ──────────────────────────────────────────────
            run_info = build_run_info(
                run_id=run_id,
                source_path="/tmp/test.md",
                source_sha256="sha256:abc123",
                args={"input": "/tmp/test.md", "llm_enhance": "a"},
                work_dir=str(work),
            )
            write_requests_and_run(reqs, run_info, work)

            # ── Read back ──────────────────────────────────────────
            read_reqs = read_requests(work / "llm_requests.jsonl")
            assert len(read_reqs) == 1
            read_info = read_run_info(work / "run.json")
            assert read_info["run_id"] == run_id

            # ── Build response (simulate Agent processing) ─────────
            empty_patch = {
                "schema_version": PATCH_SCHEMA_VERSION,
                "phase": "A",
                "decisions": [],
            }
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "run_id": run_id,
                "request_id": "A-0000",
                "raw_response": json.dumps(empty_patch, ensure_ascii=False),
                "input_hash": read_reqs[0]["input_hash"],
            }
            responses = [response]

            # ── Build fresh model (simulate resume) ────────────────
            model2 = make_minimal_model(blocks)
            report2: dict = {"parse_report": {"ambiguous_short_paragraphs": 2}}

            result = replay_phase_responses(
                model2, report2, phase="A",
                requests=read_reqs,
                responses=responses,
            )
            # No decisions applied (empty patch) → model unchanged
            assert result is model2
            assert len(report2["llm_enhancer"]["applied"]) == 0
