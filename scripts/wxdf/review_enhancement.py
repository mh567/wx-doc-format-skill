"""LLM review enhancement: requests, response validation and the two transports.

The review does not modify the document, so it cannot share the conversion's
enhancement contract: that one carries an immutable AST view, a revision and
patch candidates, none of which a finding needs.  This module keeps the same
envelope and the same integrity principle in a smaller shape:

* a request names a capability and the exact items the capability may judge, and
  carries a ``view_sha256`` over those items so a response can be tied to what
  was shown;
* a response carries findings, and every finding's code must be one the
  capability declares, its level must be a known severity and its location must
  be non-empty.  A response that breaks any of that produces a diagnostic rather
  than a fabricated finding;
* the offline transport writes ``review_requests.jsonl`` and ``review_run.json``
  and reads ``review_responses.jsonl`` back, verifying each response against the
  request it answers before it is trusted.

Nothing here reaches the network; the direct transport calls a command the
caller supplies, exactly as the conversion's ``--llm-command`` does.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .capabilities import REVIEW_CAPABILITIES, normalize_mode, should_enhance
from .digest import digest
from .model import inline_text_projection
from .semantics.structure import collect_structure_candidates

REVIEW_PROTOCOL_VERSION = "1.0"
MAX_ITEMS_PER_REQUEST = 200
LEVELS = ("critical", "high", "medium", "low")

# The findings a capability may report.  A code outside its own set is rejected:
# the set is what keeps a model's answer inside the requirement it was asked
# about instead of turning into free-form commentary.
ALLOWED_FINDING_CODES: dict[str, frozenset[str]] = {
    "citation_order_review": frozenset({
        "citation_order_wrong",
        "citation_format_wrong",
        "citation_reference_missing",
    }),
    "glossary_review": frozenset({
        "glossary_order_wrong",
        "glossary_definition_in_abbreviation",
        "glossary_entry_missing_definition",
    }),
    "list_prose_review": frozenset({
        "list_used_as_heading",
        "list_item_is_prose",
    }),
}

CAPABILITY_TASKS: dict[str, str] = {
    "citation_order_review": (
        "输入的 items 含两类条目：kind 为 citation_entry 的是引用文件／依据文件条目，"
        "kind 为 body_reference 的是正文中提到标准的段落。请判断引用文件与依据文件是否按"
        "「国标、国军标、行业标、团体标、企标、国际标、规章制度、其他技术文件」的顺序排列；"
        "标准号（如 GB、ISO）与数字之间是否有空格；以及引用文件是否与正文引用一一对应"
        "（正文引用的标准应在引用文件中有条目，引用文件中的条目也应在正文中被引用）。"
    ),
    "glossary_review": (
        "判断术语是否按条排列且中文优先，缩略语是否按数字、字母顺序排列，"
        "以及缩略语一章是否误写了定义（缩略语一章不应书写定义）。"
    ),
    "list_prose_review": (
        "判断列项是否被当作标题使用，以及列项内是否出现句号（超过一句话的内容"
        "不适合作为列项，应改用段落或条标题）。"
    ),
}

_CITATION_HEADINGS = ("引用文件", "依据文件")
_GLOSSARY_HEADINGS = ("术语", "缩略语", "符号")
# A standard designation: letters, an optional part suffix, then a number — "GB
# 1234", "ISO 9001", "GB/T 1234".  Used only to pick body paragraphs that cite a
# standard, so a loose shape is enough.
_STANDARD_TOKEN = re.compile(r"[A-Za-z]{1,8}(?:/[A-Za-z]{1,6})?\s*\d+")
_MAX_REFERENCE_ITEMS = 80

REVIEW_PHASE_BY_CAPABILITY: dict[str, str] = {
    "citation_order_review": "D",
    "glossary_review": "E",
    "list_prose_review": "F",
}


class ReviewProtocolError(RuntimeError):
    """Raised when a review request or response breaks the protocol."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------------
# Payload extraction


def _headings(facts: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for candidate in collect_structure_candidates(facts):
        proposal = candidate.get("proposal") if isinstance(candidate, Mapping) else None
        value = proposal.get("value") if isinstance(proposal, Mapping) else None
        if isinstance(value, Mapping) and value.get("role") == "heading":
            for node_id in candidate.get("target_node_ids") or []:
                ids.add(str(node_id))
    return ids


def _unit_text(facts: Mapping[str, Any], node_id: str) -> str:
    unit = facts.get("units", {}).get(node_id) or {}
    return inline_text_projection(unit.get("inline_content") or []).strip()


def _ordered_root_nodes(facts: Mapping[str, Any]) -> list[str]:
    return [str(node_id) for node_id in facts.get("children", {}).get("root", [])]


def _section_entries(facts: Mapping[str, Any], keywords: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return ``(node_id, heading)`` for the paragraphs under a matching heading."""

    heading_ids = _headings(facts)
    entries: list[tuple[str, str]] = []
    heading = ""
    for node_id in _ordered_root_nodes(facts):
        text = _unit_text(facts, node_id)
        if node_id in heading_ids:
            heading = text
            continue
        if text and any(keyword in heading for keyword in keywords):
            entries.append((node_id, heading))
    return entries


def _section_items(facts: Mapping[str, Any], keywords: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return the paragraphs that sit under a heading naming one of *keywords*."""

    return [
        {"location": heading or "章节", "text": _unit_text(facts, node_id)}
        for node_id, heading in _section_entries(facts, keywords)
    ]


def _list_items(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node_id in _ordered_root_nodes(facts):
        evidence = facts.get("evidence", {}).get(node_id) or {}
        if not evidence.get("marker_kind"):
            continue
        text = _unit_text(facts, node_id)
        if text:
            items.append({"location": f"列项 {evidence.get('marker_text') or ''}".strip(), "text": text})
    return items


def _citation_items(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the citation entries plus the body places that name a standard.

    The spec requires the citation list to correspond one-to-one with the
    references made in the body, so the capability needs both sides.  The body
    side is bounded to the paragraphs that actually name a standard, which keeps
    the request small and the judgement focused.
    """

    entries = _section_entries(facts, _CITATION_HEADINGS)
    in_sections = {node_id for node_id, _ in entries}
    heading_ids = _headings(facts)
    references: list[dict[str, Any]] = []
    for node_id in _ordered_root_nodes(facts):
        if node_id in in_sections or node_id in heading_ids:
            continue
        text = _unit_text(facts, node_id)
        if not text or _STANDARD_TOKEN.search(text) is None:
            continue
        references.append({"kind": "body_reference", "location": "正文引用", "text": text})

    citation_items: list[dict[str, Any]] = [
        {"kind": "citation_entry", "location": heading or "引用文件／依据文件", "text": _unit_text(facts, node_id)}
        for node_id, heading in entries
    ]
    # The entries come last so a truncated request still carries the whole list to
    # judge the order of, which is the capability's primary question.
    return references[:_MAX_REFERENCE_ITEMS] + citation_items


def _items_for(capability: str, facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    if capability == "citation_order_review":
        return _citation_items(facts)
    if capability == "glossary_review":
        return _section_items(facts, _GLOSSARY_HEADINGS)
    if capability == "list_prose_review":
        return _list_items(facts)
    return []


# ---------------------------------------------------------------------------
# Requests


def _selected(mode: str, phase: str) -> bool:
    """Return whether *phase* runs in *mode*.

    ``auto`` has no suspicion gate for the review capabilities, so it means "run
    them all"; every other mode goes through the shared phase selection.
    """

    if mode == "auto":
        return True
    return should_enhance({}, phase, mode)


def _build_request(capability: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    phase = REVIEW_PHASE_BY_CAPABILITY[capability]
    bounded = items[:MAX_ITEMS_PER_REQUEST]
    # The identity is derived from what the request shows, not from a random
    # value: the offline transport builds the requests once to write them out and
    # again to resume, and the two must agree or no response would ever match.
    view = digest({"capability": capability, "items": bounded})
    request_id = f"{capability}-{str(view).split(':', 1)[-1][:16]}"
    request: dict[str, Any] = {
        "review_protocol_version": REVIEW_PROTOCOL_VERSION,
        "request_id": request_id,
        "capability": capability,
        "phase": phase,
        "task": CAPABILITY_TASKS[capability],
        "allowed_codes": sorted(ALLOWED_FINDING_CODES[capability]),
        "items": bounded,
        "view_sha256": view,
        "contract_versions": {"review_request": REVIEW_PROTOCOL_VERSION},
    }
    request["request_sha256"] = digest(request)
    return request


def build_review_requests(facts: Mapping[str, Any], mode: str) -> list[dict[str, Any]]:
    """Return one request per selected review capability that has material.

    The mode is normalized here so a caller may pass either the compact form or a
    capability name, which is what the CLI's ``--llm-enhance`` accepts.
    """

    normalized = normalize_mode(str(mode))
    requests: list[dict[str, Any]] = []
    for capability in REVIEW_CAPABILITIES:
        phase = REVIEW_PHASE_BY_CAPABILITY[capability]
        if not _selected(normalized, phase):
            continue
        items = _items_for(capability, facts)
        if not items:
            continue
        requests.append(_build_request(capability, items))
    return requests


# ---------------------------------------------------------------------------
# Responses


def _response_diagnostics(request: Mapping[str, Any], code: str, message: str) -> list[dict[str, Any]]:
    return [{
        "code": "llm_response_invalid",
        "level": "low",
        "detail": code,
        "capability": request.get("capability"),
        "message": message,
        "severity": "low",
    }]


def collect_findings(
    requests: Iterable[Mapping[str, Any]],
    responses: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate each response against its request and return findings.

    A response that cannot be tied to its request, or whose findings fall outside
    what the capability declares, yields a diagnostic.  No finding is ever
    inferred from a malformed answer.
    """

    by_id: dict[str, Mapping[str, Any]] = {}
    for response in responses:
        if isinstance(response, Mapping) and isinstance(response.get("request_id"), str):
            by_id[str(response["request_id"])] = response

    findings: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for request in requests:
        request_id = str(request.get("request_id"))
        response = by_id.get(request_id)
        if response is None:
            diagnostics.append({
                "code": "llm_response_missing",
                "level": "low",
                "capability": request.get("capability"),
                "message": "该能力没有对应的响应",
                "severity": "low",
            })
            continue
        if response.get("view_sha256") != request.get("view_sha256"):
            diagnostics.extend(_response_diagnostics(request, "view_mismatch", "响应的视图摘要与请求不一致"))
            continue
        raw = response.get("findings")
        if not isinstance(raw, list):
            diagnostics.extend(_response_diagnostics(request, "findings_missing", "响应缺少 findings 列表"))
            continue
        allowed = ALLOWED_FINDING_CODES.get(str(request.get("capability")), frozenset())
        capability = str(request.get("capability"))
        for entry in raw:
            if not isinstance(entry, Mapping):
                diagnostics.extend(_response_diagnostics(request, "finding_not_object", "findings 条目不是对象"))
                continue
            code = str(entry.get("code") or "")
            level = str(entry.get("level") or "")
            location = str(entry.get("location") or "")
            if code not in allowed:
                diagnostics.extend(_response_diagnostics(request, "code_not_allowed", f"未知的问题代码 {code!r}"))
                continue
            if level not in LEVELS:
                diagnostics.extend(_response_diagnostics(request, "level_not_allowed", f"未知的等级 {level!r}"))
                continue
            if not location:
                diagnostics.extend(_response_diagnostics(request, "location_missing", "问题缺少位置"))
                continue
            findings.append({
                "code": code,
                "level": level,
                "location": location,
                "evidence": str(entry.get("evidence") or ""),
                "suggestion": str(entry.get("suggestion") or "") or _fallback_suggestion(code),
                "source": "llm",
                "capability": capability,
            })
    return findings, diagnostics


def _fallback_suggestion(code: str) -> str:
    """Return the standard remediation text when a response omits its own."""

    from .review_suggestions import get_suggestion

    return str(get_suggestion(code).get("suggestion") or "")


# ---------------------------------------------------------------------------
# Transports


@dataclass
class ReviewEnhancement:
    """How the review obtains its AI findings.

    ``call`` is the direct transport: it receives the serialised request and
    returns the response text.  ``responses`` is the offline transport: the
    responses already collected from ``review_responses.jsonl``.
    """

    mode: str = "off"
    call: Callable[[str], str] | None = None
    responses: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def enabled(self) -> bool:
        return self.mode not in {"", "off"}


def call_direct(requests: list[dict[str, Any]], call: Callable[[str], str]) -> list[dict[str, Any]]:
    """Send each request through *call* and parse the response text.

    A call that raises, or an answer that is not a JSON object, is dropped: the
    capability is then reported as unanswered rather than failing the review.
    """

    responses: list[dict[str, Any]] = []
    for request in requests:
        try:
            text = call(json.dumps(request, ensure_ascii=False))
            parsed = json.loads(text)
        except Exception:
            continue
        if isinstance(parsed, Mapping):
            responses.append(dict(parsed))
    return responses


def write_protocol(
    directory: Path | str,
    requests: list[dict[str, Any]],
    *,
    input_path: Path | str,
    template_path: Path | str,
    mode: str,
) -> Path:
    """Write the offline request batch and return the run path to resume from."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "review_requests.jsonl").write_text(
        "\n".join(json.dumps(request, ensure_ascii=False) for request in requests) + "\n",
        encoding="utf-8",
    )
    run = {
        "review_protocol_version": REVIEW_PROTOCOL_VERSION,
        "run_id": uuid.uuid4().hex,
        "mode": mode,
        "input": {"path": str(input_path), "sha256": _file_digest(input_path)},
        "template": {"path": str(template_path), "sha256": _file_digest(template_path)},
        "contract_versions": {"review_request": REVIEW_PROTOCOL_VERSION},
        "requests": [
            {
                "request_id": request["request_id"],
                "capability": request["capability"],
                "phase": request["phase"],
                "view_sha256": request["view_sha256"],
                "request_sha256": request["request_sha256"],
            }
            for request in requests
        ],
    }
    run_path = directory / "review_run.json"
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_path


def _file_digest(path: Path | str) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_protocol_sources(run: Mapping[str, Any], input_path: Path | str, template_path: Path | str) -> None:
    """Fail when the document changed under the responses that answer it.

    A run records the digest of the input and the template it was built from.  If
    either differs now, the responses describe a different document, so they must
    not be applied.
    """

    for key, path in (("input", input_path), ("template", template_path)):
        recorded = run.get(key) if isinstance(run, Mapping) else None
        expected = recorded.get("sha256") if isinstance(recorded, Mapping) else None
        if not isinstance(expected, str):
            raise ReviewProtocolError("REVIEW_RUN_INVALID", f"run file has no {key} digest")
        actual = _file_digest(path)
        if actual != expected:
            raise ReviewProtocolError(
                "REVIEW_SOURCE_CHANGED",
                f"{key} 与生成请求时不一致，响应不能套用到这份文档上",
            )


def load_protocol(run_path: Path | str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read and verify ``review_responses.jsonl`` against its run file.

    A response whose identity or view digest does not match the request it claims
    to answer is a protocol failure, not something to trust.
    """

    run_path = Path(run_path)
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewProtocolError("REVIEW_RUN_UNREADABLE", str(exc)) from exc
    if not isinstance(run, Mapping) or run.get("review_protocol_version") != REVIEW_PROTOCOL_VERSION:
        raise ReviewProtocolError("REVIEW_RUN_UNSUPPORTED", "run file is not a review protocol 1.0 document")
    requests = {
        str(item["request_id"]): item
        for item in run.get("requests") or []
        if isinstance(item, Mapping) and item.get("request_id")
    }
    responses_path = run_path.parent / "review_responses.jsonl"
    responses: list[dict[str, Any]] = []
    try:
        lines = responses_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReviewProtocolError("REVIEW_RESPONSES_UNREADABLE", str(exc)) from exc
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            response = json.loads(line)
        except ValueError as exc:
            raise ReviewProtocolError("REVIEW_RESPONSE_INVALID", str(exc)) from exc
        if not isinstance(response, Mapping):
            raise ReviewProtocolError("REVIEW_RESPONSE_INVALID", "response is not an object")
        request = requests.get(str(response.get("request_id")))
        if request is None:
            raise ReviewProtocolError("REVIEW_RESPONSE_UNKNOWN_REQUEST", str(response.get("request_id")))
        if response.get("view_sha256") != request.get("view_sha256"):
            raise ReviewProtocolError(
                "REVIEW_RESPONSE_VIEW_MISMATCH",
                f"{request['request_id']} 的视图摘要与请求不一致",
            )
        responses.append(dict(response))
    return dict(run), responses
