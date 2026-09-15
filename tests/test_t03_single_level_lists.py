"""T03 end-to-end tests for single-level ordered/unordered lists.

These tests exercise the native-v2 chain (raw evidence -> candidate states ->
Resolver arbitration -> constraint validation -> materialization ->
normalization -> real numbering output -> saved-output readback) for
single-level lists.  They pin the behaviour the reviewer demanded after the
first review round:

  * candidate states are derived from raw evidence, publish genuine competing
    alternatives and are arbitrated by ``wxdf.resolver.resolve``;
  * README constraints 3 (deep items need a parent) and 4 (stepwise level
    increase) are validated over the arbitrated decisions, and constraint 7
    (low confidence into the ambiguity report) is reachable;
  * a level that the numbering instance cannot render is reported rather than
    clamped silently;
  * the saved-output readback reads ``w:numPr/w:numId`` and ``w:numPr/w:ilvl``
    and fails on a tampered level, a tampered numbering id and a removed
    ``numPr``.

Fixture naming follows the T01 convention ``t03_<scenario>_<variant>``.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import LIST_STATE_ENUM, analyze_lists

from test_modular_normalization import _ast, _profile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": WORD_NS}


def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


def _paragraph(unit_id: str, index: int, text: str, *, style: str | None = None) -> dict:
    unit = {
        "kind": "paragraph",
        "source_ref": _ref(index),
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
    }
    if style is not None:
        unit["style"] = style
    return unit


def _facts(containers: dict, children: dict, units: dict, evidence: dict) -> dict:
    return {
        "facts_schema_version": "1.0",
        "source": {"source_id": "source", "source_type": "markdown", "content_sha256": "sha256:" + "a" * 64},
        "parser": {"id": "test", "version": "1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": units,
        "containers": containers,
        "children": children,
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def _convert_markdown_result(tmp_path: Path, text: str) -> tuple[dict, Path, object]:
    source = tmp_path / "input.md"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "output.docx"
    normalized_path = tmp_path / "normalized.json"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"normalized_ast_path": normalized_path, "llm_enhance": "off"},
    )
    result = ConversionKernel().convert(request)
    assert result.status == "completed", result.diagnostics
    import json

    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    return normalized, output, result


def _convert_markdown(tmp_path: Path, text: str) -> tuple[dict, Path]:
    normalized, output, _result = _convert_markdown_result(tmp_path, text)
    return normalized, output


def _direct_numbering(path: Path) -> list[tuple[str, str | None, str | None]]:
    """Return (text, numId, ilvl) for every paragraph carrying a direct numPr."""

    rows: list[tuple[str, str | None, str | None]] = []
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    for paragraph in root.iter(f"{{{WORD_NS}}}p"):
        num_pr = paragraph.find("w:pPr/w:numPr", _NS)
        if num_pr is None:
            continue
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
        num_id = num_pr.find("w:numId", _NS)
        ilvl = num_pr.find("w:ilvl", _NS)
        rows.append((
            text,
            num_id.get(f"{{{WORD_NS}}}val") if num_id is not None else None,
            ilvl.get(f"{{{WORD_NS}}}val") if ilvl is not None else None,
        ))
    return rows


def _semantic_list_sequence(normalized: dict) -> list[tuple[str, int | None]]:
    """Return (list_kind, render_level) for list items in reading order."""

    nodes = normalized.get("presentation", {}).get("nodes", {})
    children = normalized["source_ast"]["document"]["children"]
    sequence: list[tuple[str, int | None]] = []

    def walk(node_id: str) -> None:
        for child_id in children.get(node_id, []):
            node = nodes.get(child_id)
            if node is not None:
                sequence.append((str(node.get("marker_kind")), node.get("target_level")))
            walk(child_id)

    walk(normalized["source_ast"]["document"]["root_id"])
    return sequence


def _rewrite_document_xml(path: Path, transform) -> None:
    """Rewrite ``word/document.xml`` inside the DOCX with ``transform(root)``."""

    temporary = path.with_suffix(".tmp.docx")
    with ZipFile(path) as archive, ZipFile(temporary, "w", ZIP_DEFLATED) as rewritten:
        for item in archive.infolist():
            data = archive.read(item.filename)
            if item.filename == "word/document.xml":
                root = ElementTree.fromstring(data)
                transform(root)
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            rewritten.writestr(item, data)
    os.replace(temporary, path)


def _level_aware_profile() -> dict:
    # ``list_decimal`` points at numbering instance 2 of the real template,
    # whose abstract definition carries nine levels, so level 1 is expressible.
    return {
        "resolved_styles": dict(_profile()["resolved_styles"]),
        "numbering_ids": {"list_letter": 3, "list_decimal": 2, "list_dash": 5, "list_bullet": 4},
    }


def _two_level_ordered_ast() -> dict:
    ast = copy.deepcopy(_ast())
    ast["document"]["containers"]["l2"]["list_kind"] = "ordered"
    ast["document"]["containers"]["i2"]["source_marker"] = "1."
    return ast


# ---------------------------------------------------------------------------
# Work item 1: scored candidate states with genuine competing alternatives
# ---------------------------------------------------------------------------

def test_t03_state_candidates_encode_competing_states_from_raw_evidence() -> None:
    units = {
        "h1": _paragraph("h1", 0, "6.3 标准化注册时序流程", style="章标题"),
        "i1": _paragraph("i1", 1, "第一项功能"),
        "i2": _paragraph("i2", 2, "第二项功能"),
        "body": _paragraph("body", 3, "普通正文段落。"),
    }
    children = {"root": ["h1", "i1", "i2", "body"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "h1": {
            "marker_kind": "ordered",
            "marker_text": "6.3",
            "ordinal": 6,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "source_ilvl": 0,
            "p_style_ref": "章标题",
        },
        "i1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "source_ilvl": 0,
        },
        "i2": {
            "marker_kind": "ordered",
            "marker_text": "2.",
            "ordinal": 2,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "source_ilvl": 0,
        },
        "body": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert "state_candidates" in result, (
        "analyze_lists must emit a scored set of candidate states; "
        f"present keys={sorted(result.keys())}"
    )
    assert all(candidate["state"] in LIST_STATE_ENUM for candidate in result["state_candidates"])
    by_node: dict[str, list[dict]] = {}
    for candidate in result["state_candidates"]:
        by_node.setdefault(str(candidate["node_id"]), []).append(candidate)

    # A protected heading offers OUTSIDE_LIST strongly and LIST_ITEM weakly; the
    # weak alternative carries the protection as negative evidence.
    heading_states = {candidate["state"] for candidate in by_node["h1"]}
    assert heading_states == {"OUTSIDE_LIST", "LIST_ITEM"}, heading_states
    heading_weak = next(candidate for candidate in by_node["h1"] if candidate["state"] == "LIST_ITEM")
    assert heading_weak["negative_evidence"], "the weak LIST_ITEM alternative must carry negative evidence"
    assert any(
        entry.get("kind") in {"protected_content", "structure"}
        for entry in heading_weak["negative_evidence"]
    )

    # A marker-bearing block offers LIST_ITEM and a lower OUTSIDE_LIST
    # alternative, so the Resolver has to arbitrate rather than re-confirm.
    assert {candidate["state"] for candidate in by_node["i1"]} == {"LIST_ITEM", "OUTSIDE_LIST"}
    # A body paragraph that follows a list run is a continuation candidate (T05),
    # so it offers OUTSIDE_LIST and ITEM_CONTINUATION and the Resolver decides.
    assert {candidate["state"] for candidate in by_node["body"]} == {"OUTSIDE_LIST", "ITEM_CONTINUATION"}

    decided = {
        decision["target_node_ids"][0]: decision["value"]["state"]
        for decision in result["state_decisions"]
    }
    assert decided == {
        "h1": "OUTSIDE_LIST",
        "i1": "LIST_ITEM",
        "i2": "LIST_ITEM",
        "body": "ITEM_CONTINUATION",
    }, decided


# ---------------------------------------------------------------------------
# Work item 2: the Resolver arbitrates, and the materialized list follows it
# ---------------------------------------------------------------------------

def test_t03_state_candidates_are_arbitrated_by_resolver() -> None:
    units = {
        "i1": _paragraph("i1", 0, "第一项"),
        "bad": _paragraph("bad", 1, "编号损坏的段落。"),
        "body": _paragraph("body", 2, "普通正文。"),
    }
    children = {"root": ["i1", "bad", "body"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "i1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "source_ilvl": 0,
        },
        "bad": {
            "numbering_status": "ignored",
            "numbering_definition_ref": None,
            "numbering_evidence": ["invalid_numbering_definition"],
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "num_fmt": None,
        },
        "body": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    decisions = result.get("state_decisions")
    assert isinstance(decisions, list) and decisions, (
        "the existing resolver must arbitrate the state candidate set; "
        f"state_decisions={decisions!r}"
    )
    decided = {decision["target_node_ids"][0]: decision["value"]["state"] for decision in decisions}
    assert decided["i1"] == "LIST_ITEM"
    assert decided["bad"] == "OUTSIDE_LIST"

    # Observable behaviour: the materialized items are exactly the blocks the
    # Resolver decided are list items, so the decisions genuinely drive output.
    item_ids = {item["item_id"] for item in result["items"]}
    decided_items = {node_id for node_id, state in decided.items() if state == "LIST_ITEM"}
    assert item_ids == decided_items, (item_ids, decided_items)
    assert "bad" not in item_ids
    assert "body" not in item_ids


# ---------------------------------------------------------------------------
# Constraint 7: a genuine equal-strength state tie reaches the ambiguity report
# ---------------------------------------------------------------------------

def test_t03_genuine_equal_strength_tie_is_unresolved() -> None:
    """An equal-strength state conflict is the only ``unresolved`` list state.

    ``AMBIGUOUS`` and ``OUTSIDE_LIST`` carry the same evidence strength here, so
    the Resolver returns ``candidate_conflict`` and the block enters the
    ambiguity report.  This is the path reserved for a genuine Resolver tie; a
    merely low-confidence parser verdict stays body text (see the FIX 1a tests).
    """
    units = {
        "i1": _paragraph("i1", 0, "正常列表项"),
        "suspect": _paragraph("suspect", 1, "疑似列表项"),
    }
    children = {"root": ["i1", "suspect"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "i1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
        },
        # A marker whose numbering definition reference does not resolve and
        # whose status is neither "detected" nor "ignored": the marker is seen
        # but no definition confirms it, so AMBIGUOUS and OUTSIDE_LIST compete
        # at equal strength.
        "suspect": {
            "marker_kind": "ordered",
            "marker_text": "2.",
            "ordinal": 2,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": None, "abstract_num_id": None},
            "numbering_evidence": ["unresolved_numbering_definition"],
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    ambiguous = [candidate for candidate in result["state_candidates"] if candidate["node_id"] == "suspect"]
    assert {candidate["state"] for candidate in ambiguous} == {"AMBIGUOUS", "OUTSIDE_LIST"}, ambiguous
    assert result["state_unresolved"], "an unresolved state conflict must be recorded"
    assert any(entry.get("target_node_ids") == ["suspect"] for entry in result["state_unresolved"])
    diagnostic_codes = [item["code"] for item in result["diagnostics"]]
    assert "ambiguous_list_state" in diagnostic_codes, diagnostic_codes
    assert result["status"] == "unresolved"
    # The ambiguous block is not silently promoted to a list item.
    assert "suspect" not in {item["item_id"] for item in result["items"]}


# ---------------------------------------------------------------------------
# Constraints 3 and 4: validated over the arbitrated decisions
# ---------------------------------------------------------------------------

def _constraint_codes(result: dict) -> list[tuple[int, str]]:
    return [(int(item["constraint"]), str(item["code"])) for item in result["state_constraint_findings"]]


def test_t03_deep_item_without_parent_reports_constraint_3() -> None:
    units = {
        "u1": _paragraph("u1", 0, "孤立深层项"),
    }
    children = {"root": ["u1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "u1": {
            "marker_kind": "ordered",
            "marker_text": "a)",
            "ordinal": 1,
            "explicit_depth": 1,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "lower_letter",
            "lvl_text": "%2)",
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert (3, "list_constraint_violation") in _constraint_codes(result), _constraint_codes(result)
    finding = next(item for item in result["state_constraint_findings"] if item["constraint"] == 3)
    assert finding["node_id"] == "u1"
    assert finding["declared_level"] == 1
    # A deep item with no parent is never silently accepted.
    assert result["status"] == "unresolved"


def test_t03_level_jump_reports_constraint_4() -> None:
    units = {
        "u1": _paragraph("u1", 0, "第一项"),
        "u2": _paragraph("u2", 1, "跳级项"),
    }
    children = {"root": ["u1", "u2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "u1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
        },
        "u2": {
            "marker_kind": "ordered",
            "marker_text": "2.",
            "ordinal": 2,
            "explicit_depth": 2,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.%2.%3",
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert (4, "list_constraint_violation") in _constraint_codes(result), _constraint_codes(result)
    finding = next(item for item in result["state_constraint_findings"] if item["constraint"] == 4)
    assert finding["node_id"] == "u2"
    assert finding["declared_level"] == 2
    assert result["status"] == "unresolved"


# ---------------------------------------------------------------------------
# Work item 3: end-to-end single-level ordered list, tree + numbering + readback
# ---------------------------------------------------------------------------

def test_t03_single_level_ordered_end_to_end(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output = _convert_markdown(
        tmp_path,
        "# 测试文档\n\n## 功能列表\n\n1. 第一项功能\n2. 第二项功能\n3. 第三项功能\n",
    )

    sequence = _semantic_list_sequence(normalized)
    assert sequence == [("ordered", 0), ("ordered", 0), ("ordered", 0)], sequence

    numbering = _direct_numbering(output)
    assert len(numbering) == 3, (
        f"every ordered list item must carry an explicit w:numPr with numId+ilvl; got {numbering!r}"
    )
    assert all(num_id is not None and ilvl is not None for _, num_id, ilvl in numbering), numbering
    # Under wx-default-v2 the ordered single-level projection is explicitly 0.
    assert [ilvl for _, _, ilvl in numbering] == ["0", "0", "0"], numbering

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]
    assert comparison["actual"] == comparison["expected"]


# ---------------------------------------------------------------------------
# Work item 3/4: end-to-end single-level unordered list, tree + numbering + readback
# ---------------------------------------------------------------------------

def test_t03_single_level_unordered_end_to_end(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output = _convert_markdown(
        tmp_path,
        "# 测试文档\n\n## 无序列表\n\n- 无序项一\n- 无序项二\n",
    )

    sequence = _semantic_list_sequence(normalized)
    assert sequence == [("unordered", 0), ("unordered", 0)], sequence

    numbering = _direct_numbering(output)
    assert len(numbering) == 2, (
        f"every unordered list item must carry an explicit w:numPr with numId+ilvl; got {numbering!r}"
    )
    assert all(num_id is not None and ilvl == "0" for _, num_id, ilvl in numbering), numbering

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]


# ---------------------------------------------------------------------------
# Acceptance: ordered lists must not write every w:ilvl as zero
# ---------------------------------------------------------------------------

def test_t03_ordered_numbering_is_level_aware_against_real_template(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback
    from wxdf.normalization import normalize
    from wxdf.rendering import render

    normalized = normalize(_two_level_ordered_ast(), _level_aware_profile(), strategy="legacy")
    assert normalized["presentation"]["nodes"]["i2"]["target_level"] == 1
    output = tmp_path / "level-aware.docx"

    result = render(normalized, output, template_path=TEMPLATE)

    numbering = _direct_numbering(output)
    ilvls = [ilvl for _, _, ilvl in numbering]
    assert ilvls == ["0", "1", "0"], (
        f"ordered w:ilvl must follow the render level, not be hardcoded to zero; got {numbering!r}"
    )
    num_ids = [num_id for _, num_id, _ in numbering]
    assert num_ids == ["3", "2", "3"], numbering
    assert not [item for item in result["diagnostics"] if item.get("code") == "list_render_level_clamped"]
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]


def test_t03_single_level_instance_emits_ilvl_zero_without_a_clamp_diagnostic(tmp_path: Path) -> None:
    from wxdf.normalization import normalize
    from wxdf.rendering import render

    # ``list_decimal`` points at numbering instance 8 of the real template, which
    # defines only level 0.  Under the two-level model (AGENTS.md decision 1) a
    # second-level item bound to that single-level instance legitimately emits
    # ``w:ilvl 0`` and is told apart from the first level by the style and the
    # instance, so no clamp diagnostic is produced.  The pre-ruling code reported
    # a clamp here because it read the emitted level as a real level.
    profile = {
        "resolved_styles": dict(_profile()["resolved_styles"]),
        "numbering_ids": {"list_letter": 3, "list_decimal": 8, "list_dash": 5, "list_bullet": 4},
    }
    normalized = normalize(_two_level_ordered_ast(), profile, strategy="legacy")
    output = tmp_path / "clamped.docx"

    result = render(normalized, output, template_path=TEMPLATE)

    clamps = [item for item in result["diagnostics"] if item.get("code") == "list_render_level_clamped"]
    assert clamps == [], (
        f"a single-level instance is the designed second level, not a clamp; diagnostics={result['diagnostics']!r}"
    )
    assert [ilvl for _, _, ilvl in _direct_numbering(output)] == ["0", "0", "0"]


# ---------------------------------------------------------------------------
# Work item 4: saved-output readback detects tampering
# ---------------------------------------------------------------------------

def _tamper(output: Path, transform) -> None:
    _rewrite_document_xml(output, transform)


def test_t03_readback_detects_tampered_ilvl(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output = _convert_markdown(
        tmp_path,
        "# 测试文档\n\n## 功能列表\n\n1. 第一项功能\n2. 第二项功能\n",
    )
    assert audit_list_readback(output, normalized)["passed"] is True

    def mangle(root):
        for ilvl in root.iter(f"{{{WORD_NS}}}ilvl"):
            ilvl.set(f"{{{WORD_NS}}}val", "77")

    _tamper(output, mangle)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    assert comparison["issues"][0]["code"] == "list_readback_mismatch"


def test_t03_readback_detects_tampered_num_id(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output = _convert_markdown(
        tmp_path,
        "# 测试文档\n\n## 功能列表\n\n1. 第一项功能\n2. 第二项功能\n",
    )
    assert audit_list_readback(output, normalized)["passed"] is True

    def mangle(root):
        for num_id in root.iter(f"{{{WORD_NS}}}numId"):
            num_id.set(f"{{{WORD_NS}}}val", "999")

    _tamper(output, mangle)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    assert comparison["issues"][0]["code"] == "list_readback_mismatch"


def test_t03_readback_detects_removed_num_pr(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output = _convert_markdown(
        tmp_path,
        "# 测试文档\n\n## 功能列表\n\n1. 第一项功能\n2. 第二项功能\n",
    )
    assert audit_list_readback(output, normalized)["passed"] is True

    def strip(root):
        for properties in root.iter(f"{{{WORD_NS}}}pPr"):
            for num_pr in list(properties.findall(f"{{{WORD_NS}}}numPr")):
                properties.remove(num_pr)

    _tamper(output, strip)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    assert comparison["issues"][0]["code"] == "list_readback_mismatch"


def test_t03_readback_failure_is_a_visible_audit_finding(tmp_path: Path) -> None:
    from wxdf.auditing import audit_bundle
    from wxdf.normalization import normalize
    from wxdf.rendering import render

    normalized = normalize(_two_level_ordered_ast(), _level_aware_profile(), strategy="legacy")
    output = tmp_path / "bundle.docx"
    result = render(normalized, output, template_path=TEMPLATE)
    bundle = audit_bundle(output, normalized, result["render_map"])
    assert bundle["list_readback"]["passed"] is True

    def mangle(root):
        for ilvl in root.iter(f"{{{WORD_NS}}}ilvl"):
            ilvl.set(f"{{{WORD_NS}}}val", "5")

    _tamper(output, mangle)
    tampered = audit_bundle(output, normalized, result["render_map"])
    assert tampered["list_readback"]["passed"] is False
    assert tampered["passed"] is False
    categories = {(finding["category"], finding["evidence"].get("code")) for finding in tampered["findings"]}
    assert ("list_readback", "list_readback_mismatch") in categories, categories


# ---------------------------------------------------------------------------
# Report observability of the readback audit and the renderer clamp
# ---------------------------------------------------------------------------

def test_t03_real_conversion_surfaces_readback_and_second_level_instance(tmp_path: Path) -> None:
    _normalized, _output, result = _convert_markdown_result(
        tmp_path,
        "# 测试文档\n\n## 功能列表\n\n1. 第一项功能\n2. 第二项功能\n\n- 一级无序\n  - 嵌套无序\n",
    )
    report = result.report
    assert report["saved_output_audit"]["passed"] is True

    downstream = report["native_module_evidence"]["native-auditor"]["downstream"]
    assert downstream["list_readback"]["passed"] is True
    assert downstream["list_readback"]["expected"] == downstream["list_readback"]["actual"]
    # The nested unordered item asked for level 1, which the template renders with
    # its own second-level unordered instance under the two-level model, so there
    # is no clamp to report.
    clamps = [item for item in downstream["render_diagnostics"] if item.get("code") == "list_render_level_clamped"]
    assert clamps == [], downstream["render_diagnostics"]

    # The two repository baseline inputs stay completed and clean: the
    # low-confidence gate must not downgrade an ordinary document.
    for name in ("test_baseline.md", "benchmark_report.md"):
        baseline = ConversionKernel().convert(
            ConversionRequest(
                input_path=ROOT / "input" / name,
                output_path=tmp_path / f"{name}.docx",
                template_path=TEMPLATE,
                pipeline="modular",
                list_policy="wx-default-v2",
                options={"llm_enhance": "off"},
            )
        )
        assert baseline.status == "completed", (name, baseline.diagnostics)
        assert (baseline.report.get("risk_warnings") or []) == [], (
            name,
            baseline.report.get("risk_warnings"),
        )
        assert [
            item
            for item in baseline.report["list_semantics"]["diagnostics"]
            if item.get("code") == "ambiguous_list_state"
        ] == [], name


# ---------------------------------------------------------------------------
# F1: list constraint violations are observable in the published report
# ---------------------------------------------------------------------------

def _build_numbered_docx(path: Path, spec: list[tuple[str, int | None, int]]) -> None:
    """Write a real DOCX whose paragraphs carry a direct ``w:numPr``.

    ``spec`` is a list of ``(text, num_id, ilvl)``; ``num_id`` ``None`` writes a
    plain body paragraph.
    """

    from docx import Document as _Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = _Document(str(TEMPLATE))
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    for text, num_id, ilvl in spec:
        paragraph = document.add_paragraph(text)
        if num_id is None:
            continue
        properties = paragraph._p.get_or_add_pPr()
        num_pr = OxmlElement("w:numPr")
        level = OxmlElement("w:ilvl")
        level.set(qn("w:val"), str(ilvl))
        identifier = OxmlElement("w:numId")
        identifier.set(qn("w:val"), str(num_id))
        num_pr.append(level)
        num_pr.append(identifier)
        properties.append(num_pr)
    document.save(str(path))


def _convert_docx(tmp_path: Path, source: Path, tag: str) -> object:
    output = tmp_path / f"{tag}.out.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return ConversionKernel().convert(request)


def test_t03_level_jump_constraint_reaches_published_report(tmp_path: Path) -> None:
    """A real DOCX with direct ``numPr`` ilvl 0 then ilvl 2 must publish the
    list-constraint violation, not merely an internal field."""

    source = tmp_path / "level-jump.docx"
    _build_numbered_docx(source, [("第一项", 1, 0), ("第三层项", 1, 2)])

    result = _convert_docx(tmp_path, source, "level-jump")
    published = result.as_report()
    codes = [item.get("code") for item in published["diagnostics"]]

    assert "list_constraint_violation" in codes, codes
    assert result.status == "failed"


def test_t03_constraint_validation_agrees_with_materialized_flattening() -> None:
    """The validator resets level state per run, matching the materializer.

    ``[level-0 item][body][level-1 item]`` is two materialization runs, so the
    level-1 item has no parent and the materializer flattens it.  The validator
    must report the same constraint-3 violation in ``diagnostics``.
    """

    units = {
        "u0": _paragraph("u0", 0, "第一项"),
        "body": _paragraph("body", 1, "中间正文段落。"),
        "u1": _paragraph("u1", 2, "第二层项"),
    }
    children = {"root": ["u0", "body", "u1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "u0": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
        },
        "body": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
        "u1": {
            "marker_kind": "ordered",
            "marker_text": "a)",
            "ordinal": 1,
            "explicit_depth": 1,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    codes = [item["code"] for item in result["diagnostics"]]

    assert (3, "list_constraint_violation") in _constraint_codes(result), _constraint_codes(result)
    assert "list_constraint_violation" in codes, codes
    assert "orphan_list_depth_flattened" in codes, codes
    assert result["status"] == "unresolved"


# ---------------------------------------------------------------------------
# F2: an ordinary document is not downgraded by the low-confidence gate
# ---------------------------------------------------------------------------

def test_t03_ambiguous_numbering_reaches_published_report(tmp_path: Path) -> None:
    """A DOCX the parser marks ``numbering_status=ambiguous`` stays body text.

    ``list_detector`` could not confirm the marker as a list item, so it keeps
    the paragraph as body text.  The Resolver must not treat that as a genuine
    competing-state conflict: the block stays body text and the decision is
    published as a ``low_confidence_list_marker`` downgrade rather than an
    ``ambiguous_list_state``.  The same holds for both numbered paragraphs of
    ``[numbered item][body][numbered item]``, since the body paragraph splits
    them into two isolated single-item groups.
    """

    source = tmp_path / "ambiguous.docx"
    _build_numbered_docx(source, [("这是一段普通文字内容", 1, 0)])

    from wxdf.parsers import parse

    facts = parse(source, {"source_store": None})
    statuses = {item.get("numbering_status") for item in facts["evidence"].values()}
    assert "ambiguous" in statuses, statuses

    result = _convert_docx(tmp_path, source, "ambiguous")
    published = result.as_report()
    list_semantics = published["list_semantics"]
    codes = [item.get("code") for item in list_semantics["diagnostics"]]

    assert "ambiguous_list_state" not in codes, codes
    records = [
        item for item in list_semantics["downgrades"] if item.get("code") == "low_confidence_list_marker"
    ]
    assert records, list_semantics["downgrades"]
    assert records[0]["severity"] == "info"
    # The block stays body text and the document completes cleanly.
    assert list_semantics["item_count"] == 0
    assert published["status"] == "completed"
    evidence = published["native_module_evidence"]["list-semantics"]
    assert evidence["state_conflict_count"] == 0
    assert evidence["state_decision_count"] == 1
    assert evidence["outside_state_count"] == 1

    # ``[numbered item][body][numbered item]`` is two isolated single-item
    # groups, so both numbered paragraphs are low confidence and stay body text.
    split = tmp_path / "split-numbered.docx"
    _build_numbered_docx(split, [("第一项", 1, 0), ("中间正文段落。", None, 0), ("第二项", 1, 0)])
    split_result = _convert_docx(tmp_path, split, "split-numbered")
    split_semantics = split_result.as_report()["list_semantics"]
    assert split_result.status == "completed", split_result.diagnostics
    assert [
        item for item in split_semantics["diagnostics"] if item.get("code") == "ambiguous_list_state"
    ] == [], split_semantics["diagnostics"]
    split_records = [
        item for item in split_semantics["downgrades"] if item.get("code") == "low_confidence_list_marker"
    ]
    assert len(split_records) == 2, split_semantics["downgrades"]
    assert split_semantics["item_count"] == 0


# ---------------------------------------------------------------------------
# F3: the saved-output audit honours the direct numbering before the style
# ---------------------------------------------------------------------------

def test_t03_saved_output_honours_direct_numbering(tmp_path: Path) -> None:
    from wxdf.auditing import audit_bundle
    from wxdf.normalization import normalize
    from wxdf.rendering import render

    normalized = normalize(_two_level_ordered_ast(), _level_aware_profile(), strategy="legacy")
    output = tmp_path / "saved-output.docx"
    result = render(normalized, output, template_path=TEMPLATE)

    bundle = audit_bundle(output, normalized, result["render_map"])
    assert bundle["saved_output"]["passed"] is True, bundle["saved_output"]["issues"]
    assert bundle["list_readback"]["passed"] is True
    assert bundle["passed"] is True

    def mangle(root):
        for ilvl in root.iter(f"{{{WORD_NS}}}ilvl"):
            ilvl.set(f"{{{WORD_NS}}}val", "5")

    _tamper(output, mangle)
    tampered = audit_bundle(output, normalized, result["render_map"])
    assert tampered["saved_output"]["passed"] is False


# ---------------------------------------------------------------------------
# F6: the direct numbering element is inserted in CT_PPr order
# ---------------------------------------------------------------------------

def test_t03_direct_numbering_is_inserted_in_pPr_order() -> None:
    from docx import Document as _Document
    from docx.oxml import OxmlElement

    from wxdf.rendering import _write_direct_numbering

    document = _Document()
    paragraph = document.add_paragraph("x")
    properties = paragraph._p.get_or_add_pPr()
    for name in ("w:ind", "w:jc", "w:rPr"):
        properties.append(OxmlElement(name))

    _write_direct_numbering(paragraph, 7, 2)

    names = [child.tag.split("}")[-1] for child in properties]
    assert names.index("numPr") < names.index("ind"), names
    assert names.index("numPr") < names.index("jc"), names
    assert names.index("numPr") < names.index("rPr"), names


# ---------------------------------------------------------------------------
# F7: a missing continuation decision is not a continuation
# ---------------------------------------------------------------------------

def test_t03_missing_continuation_decision_is_not_a_continuation() -> None:
    from wxdf.semantics.lists import _emit_group

    units = {
        "p1": _paragraph("p1", 0, "第一项"),
        "p1b": _paragraph("p1b", 1, "续段"),
    }
    containers = {
        "root": {"kind": "document"},
        "list": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s"},
        "item": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "s"},
    }
    children = {"root": ["list"], "list": ["item"], "item": ["p1", "p1b"]}
    item_record = {"item_id": "item", "evidence": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0}}

    def emit(p1b_evidence: dict, decided_states: dict) -> list[dict]:
        result: dict = {"lists": [], "items": [], "continuations": [], "candidates": []}
        _emit_group(
            result,
            _facts(containers, children, units, {"item": item_record["evidence"], "p1b": p1b_evidence}),
            list_id="list",
            list_kind="ordered",
            source_sequence_id="s",
            source_start=1,
            item_records=[item_record],
            scope_id="scope:document",
            parent_item_id=None,
            explicit=True,
            decided_states=decided_states,
        )
        return result["continuations"]

    # A missing decision with no positive evidence must not become a continuation.
    assert emit({}, {}) == []
    # The same missing decision is a continuation only when the evidence supports it.
    assert [item["node_id"] for item in emit({"continuation_hint": True}, {})] == ["p1b"]
    # An explicit continuation decision is honoured.
    assert [item["node_id"] for item in emit({}, {"p1b": "ITEM_CONTINUATION"})] == ["p1b"]
    # A decision that is not a continuation is respected even with the hint.
    assert emit({"continuation_hint": True}, {"p1b": "OUTSIDE_LIST"}) == []


# ---------------------------------------------------------------------------
# F4/F5: readback findings reach the merged saved-output audit findings
# ---------------------------------------------------------------------------

def test_t03_readback_findings_are_merged_into_saved_output_findings(tmp_path: Path) -> None:
    import contextlib

    from wxdf import native_stages
    from wxdf.normalization import normalize
    from wxdf.rendering import render

    normalized = normalize(_two_level_ordered_ast(), _level_aware_profile(), strategy="legacy")
    staging = tmp_path / "staging.docx"
    render_result = render(normalized, staging, template_path=TEMPLATE)

    def mangle(root):
        for ilvl in root.iter(f"{{{WORD_NS}}}ilvl"):
            ilvl.set(f"{{{WORD_NS}}}val", "9")

    _tamper(staging, mangle)

    class _Context:
        def __init__(self) -> None:
            self.artifacts = {
                "normalized": normalized,
                "render_result": render_result,
                "appendix_semantics": None,
                "source_facts": _facts({"root": {"kind": "document"}}, {"root": []}, {}, {}),
                "source_facts_digest": "sha256:" + "0" * 64,
            }
            self.services = {"staging_path": str(staging)}
            self.evidence: dict = {}

        def require(self, key):
            return self.artifacts[key]

        def provide(self, key, value):
            self.artifacts[key] = value

        def stage(self, name):
            return contextlib.nullcontext()

    context = _Context()
    native_stages._native_auditor(context)
    categories = {
        (finding["category"], finding["evidence"].get("code"))
        for finding in context.artifacts["audit_findings"]
    }

    assert ("list_readback", "list_readback_mismatch") in categories, categories
    assert ("saved_output", "list_readback_mismatch") in categories, categories


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
