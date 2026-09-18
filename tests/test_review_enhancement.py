"""Review AI-enhancement tests.

The review's AI layer is separate from the conversion's: it produces findings
rather than patch candidates, so it has its own light request/response contract.
These tests pin:

  * a request per selected capability, with a stable identity and view digest;
  * the direct transport and the offline file protocol producing the same
    findings;
  * a response that breaks the contract producing a diagnostic, never a finding;
  * a response whose view digest or source document disagrees with its request
    being refused rather than trusted;
  * a plain review keeping exactly the output it had before AI existed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.input_reviewer import plan_review_requests, review_input_document
from wxdf.parsers import parse
from wxdf.review_enhancement import (
    ReviewEnhancement,
    ReviewProtocolError,
    build_review_requests,
    collect_findings,
    load_protocol,
    verify_protocol_sources,
    write_protocol,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"

_FINDING_BY_CAPABILITY = {
    "citation_order_review": {
        "code": "citation_order_wrong",
        "level": "medium",
        "location": "引用文件",
        "evidence": "企标排在国标之前",
    },
    "glossary_review": {
        "code": "glossary_order_wrong",
        "level": "low",
        "location": "术语、符号和缩略语",
        "evidence": "缩略语未按字母顺序",
    },
    "list_prose_review": {
        "code": "list_item_is_prose",
        "level": "medium",
        "location": "列项 二",
        "evidence": "列项内出现句号",
    },
}


def _source(tmp_path: Path) -> Path:
    document = Document()
    document.add_paragraph("引用文件", style="Heading 1")
    document.add_paragraph("GB 1234 某国家标准")
    document.add_paragraph("Q/ABC 1 某企业标准")
    document.add_paragraph("正文", style="Heading 1")
    document.add_paragraph("本系统的接口按 GB 1234 的规定实现。")
    document.add_paragraph("术语、符号和缩略语", style="Heading 1")
    document.add_paragraph("术语：指某概念")
    document.add_paragraph("AAA：某缩略语")
    document.add_paragraph("列项", style="Heading 1")
    document.add_paragraph("列项 一", style="List Number")
    document.add_paragraph("列项 二，这里出现了句号。所以其实是段落文字。", style="List Number")
    source = tmp_path / "review-source.docx"
    document.save(source)
    return source


def _answer(request: dict, finding: dict | None = None) -> dict:
    entry = finding if finding is not None else _FINDING_BY_CAPABILITY[request["capability"]]
    return {
        "request_id": request["request_id"],
        "response_id": "response-1",
        "view_sha256": request["view_sha256"],
        "findings": [entry],
    }


def _call(request_text: str) -> str:
    request = json.loads(request_text)
    return json.dumps(_answer(request), ensure_ascii=False)


def test_requests_are_built_per_capability_with_a_stable_identity(tmp_path: Path) -> None:
    facts = parse(_source(tmp_path), {"source_store": None, "source_type": "docx"})

    first = build_review_requests(facts, "def")
    second = build_review_requests(facts, "def")

    assert [request["capability"] for request in first] == [
        "citation_order_review",
        "glossary_review",
        "list_prose_review",
    ]
    assert [request["request_id"] for request in first] == [request["request_id"] for request in second]
    assert [request["view_sha256"] for request in first] == [request["view_sha256"] for request in second]
    # The payload stays inside the capability: the citation request never sees the
    # list items, and it carries both sides of the one-to-one requirement — the
    # entries and the body places that name a standard.
    citation = first[0]
    assert all("列项" not in item["text"] for item in citation["items"])
    kinds = {item["kind"] for item in citation["items"]}
    assert kinds == {"citation_entry", "body_reference"}
    assert all(item["kind"] in kinds for item in citation["items"])
    assert citation["allowed_codes"] == sorted(citation["allowed_codes"])


def test_a_mode_selects_only_its_own_capabilities(tmp_path: Path) -> None:
    facts = parse(_source(tmp_path), {"source_store": None, "source_type": "docx"})

    assert [request["capability"] for request in build_review_requests(facts, "d")] == ["citation_order_review"]
    assert build_review_requests(facts, "off") == []


def test_the_direct_transport_merges_findings(tmp_path: Path) -> None:
    source = _source(tmp_path)

    result = review_input_document(source, TEMPLATE, enhancement=ReviewEnhancement(mode="def", call=_call))

    llm = [issue for issue in result["issues"] if issue.get("source") == "llm"]
    assert {issue["code"] for issue in llm} == {
        "citation_order_wrong",
        "glossary_order_wrong",
        "list_item_is_prose",
    }
    assert result["ai_review"]["status"] == "completed"
    assert all(info["status"] == "completed" for info in result["ai_review"]["capabilities"].values())
    assert "ai_review" in result["dimension_scores"]


def test_the_offline_protocol_reproduces_the_direct_result(tmp_path: Path) -> None:
    source = _source(tmp_path)
    directory = tmp_path / "review-requests"
    requests = plan_review_requests(source, TEMPLATE, "def")
    run_path = write_protocol(
        directory,
        requests,
        input_path=source,
        template_path=TEMPLATE,
        mode="def",
    )
    (directory / "review_responses.jsonl").write_text(
        "\n".join(json.dumps(_answer(request), ensure_ascii=False) for request in requests) + "\n",
        encoding="utf-8",
    )

    run, responses = load_protocol(run_path)
    verify_protocol_sources(run, source, TEMPLATE)
    offline = review_input_document(
        source, TEMPLATE, enhancement=ReviewEnhancement(mode=str(run["mode"]), responses=tuple(responses))
    )
    direct = review_input_document(source, TEMPLATE, enhancement=ReviewEnhancement(mode="def", call=_call))

    assert [issue["code"] for issue in offline["issues"] if issue.get("source") == "llm"] == [
        issue["code"] for issue in direct["issues"] if issue.get("source") == "llm"
    ]
    assert offline["score"] == direct["score"]


def test_an_out_of_contract_response_yields_a_diagnostic_not_a_finding(tmp_path: Path) -> None:
    facts = parse(_source(tmp_path), {"source_store": None, "source_type": "docx"})
    requests = build_review_requests(facts, "d")
    request = requests[0]

    bad_code = dict(_answer(request))
    bad_code["findings"] = [{"code": "invented_code", "level": "medium", "location": "x"}]
    bad_level = dict(_answer(request))
    bad_level["findings"] = [{"code": "citation_order_wrong", "level": "urgent", "location": "x"}]
    no_location = dict(_answer(request))
    no_location["findings"] = [{"code": "citation_order_wrong", "level": "medium", "location": ""}]

    for response in (bad_code, bad_level, no_location):
        findings, diagnostics = collect_findings([request], [response])
        assert findings == []
        assert [item["code"] for item in diagnostics] == ["llm_response_invalid"]
        assert diagnostics[0]["detail"] in {"code_not_allowed", "level_not_allowed", "location_missing"}


def test_a_response_answering_a_different_view_is_refused(tmp_path: Path) -> None:
    facts = parse(_source(tmp_path), {"source_store": None, "source_type": "docx"})
    request = build_review_requests(facts, "d")[0]

    findings, diagnostics = collect_findings([request], [{**_answer(request), "view_sha256": "sha256:" + "0" * 64}])

    assert findings == []
    assert diagnostics[0]["detail"] == "view_mismatch"


def test_a_missing_response_is_reported_rather_than_guessed(tmp_path: Path) -> None:
    facts = parse(_source(tmp_path), {"source_store": None, "source_type": "docx"})
    requests = build_review_requests(facts, "def")

    findings, diagnostics = collect_findings(requests, [_answer(requests[0])])

    assert [issue["code"] for issue in findings] == ["citation_order_wrong"]
    assert {item["code"] for item in diagnostics} == {"llm_response_missing"}


def test_the_run_file_refuses_a_changed_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    directory = tmp_path / "protocol"
    requests = plan_review_requests(source, TEMPLATE, "d")
    run_path = write_protocol(directory, requests, input_path=source, template_path=TEMPLATE, mode="d")
    run = json.loads(run_path.read_text(encoding="utf-8"))

    # The document the run was built from still verifies.
    verify_protocol_sources(run, source, TEMPLATE)

    # A different document must not inherit the answers written for that one.
    other = tmp_path / "other.docx"
    Document().save(other)
    with pytest.raises(ReviewProtocolError) as refused:
        verify_protocol_sources(run, other, TEMPLATE)
    assert refused.value.code == "REVIEW_SOURCE_CHANGED"


def test_a_plain_review_does_not_change_with_ai_available(tmp_path: Path) -> None:
    source = _source(tmp_path)

    plain = review_input_document(source, TEMPLATE)

    assert plain["ai_review"] == {"status": "not_requested", "capabilities": {}}
    assert "ai_review" not in plain["dimension_scores"]
    assert not [issue for issue in plain["issues"] if issue.get("source") == "llm"]
