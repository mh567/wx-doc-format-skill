"""T09 readback-audit and ``list_detect`` gating tests.

These tests pin the two T09 work items:

  * work item 1: after saving, the output document is re-parsed with the same
    SourceFacts pipeline, the list tree is rebuilt and compared with the
    semantic list, and every README section-9 invariant is checked explicitly
    (every block preserved once, deep items have a parent, continuation
    paragraphs have an ``owner_item_id``, the numbering sequence agrees, the
    AST and the readback tree agree, low-confidence decisions have diagnostics,
    normal mode never silently accepts ambiguous structure).  Under the T08
    two-level model every list item emits ``w:ilvl 0``, so the level lives in
    the instance and the style: the audit re-derives the render-level parent
    chain from the saved style role instead of a ``w:ilvl`` chain that no
    longer carries information;
  * work item 2: the ``list_detect`` LLM capability acts only on fragments
    that already have multiple candidates, stays inside the Resolver's global
    constraints, and never rewrites the whole list structure.

Fixture naming follows the T01 convention ``t09_<scenario>_<variant>``.
"""
from __future__ import annotations

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


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": WORD_NS}

_INVARIANT_KEYS = {
    "list_items_preserved_once",
    "deep_items_have_parent",
    "continuations_have_owner",
    "numbering_sequence_agrees",
    "ast_readback_tree_agrees",
    "low_confidence_has_diagnostics",
    "no_silent_ambiguity",
}

_SEMANTIC_INVARIANT_KEYS = {
    "deep_items_have_parent",
    "continuations_have_owner",
    "low_confidence_has_diagnostics",
    "no_silent_ambiguity",
}


# ---------------------------------------------------------------------------
# Conversion + DOCX fixtures
# ---------------------------------------------------------------------------

def _convert_markdown(tmp_path: Path, text: str) -> tuple[dict, Path, object]:
    import json

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
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    return normalized, output, result


def _rewrite_part(path: Path, part: str, transform) -> None:
    temporary = path.with_suffix(".tmp.docx")
    with ZipFile(path) as archive, ZipFile(temporary, "w", ZIP_DEFLATED) as rewritten:
        for item in archive.infolist():
            data = archive.read(item.filename)
            if item.filename == part:
                root = ElementTree.fromstring(data)
                transform(root)
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            rewritten.writestr(item, data)
    os.replace(temporary, path)


def _stylesheet_name_index(path: Path) -> dict[str, str]:
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/styles.xml"))
    index: dict[str, str] = {}
    for style in root.findall("w:style", _NS):
        name = style.find("w:name", _NS)
        if name is not None:
            index[name.get(f"{{{WORD_NS}}}val") or ""] = style.get(f"{{{WORD_NS}}}styleId")
    return index


def _paragraph_text(element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{WORD_NS}}}t"))


# ---------------------------------------------------------------------------
# Work item 1: the readback audit covers every invariant end to end
# ---------------------------------------------------------------------------

def test_t09_readback_audit_covers_every_invariant_end_to_end(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 两级列表\n\n1. 一级一\n   1. 二级一\n   2. 二级二\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]
    invariants = comparison["invariants"]
    assert _INVARIANT_KEYS <= set(invariants), sorted(invariants)
    assert all(invariants[key] is True for key in _INVARIANT_KEYS), invariants


@pytest.mark.parametrize(
    "marker,kind_style",
    [("1.", "ordered"), ("-", "unordered")],
)
def test_t09_hard_break_list_continuation_stays_completed(
    tmp_path: Path, marker: str, kind_style: str,
) -> None:
    """A list item whose text carries a break must not fail the conversion.

    Rendering a hard break emits ``<w:t/><w:br/><w:t/>`` in one paragraph.  The
    readback text must render that break as ``"\\n"`` exactly as the AST inline
    projection does, otherwise an ordinary list continuation mismatches and the
    whole conversion is marked failed.  A soft break stays a space, so the
    fixture asks for a hard break with the trailing backslash.
    """
    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        f"# 文档\n\n## 列表\n\n{marker} 第一项\\\n   续接正文一\n{marker} 第二项\n",
    )
    assert result.status == "completed", result.diagnostics
    audit = audit_list_readback(output, normalized)
    assert audit["passed"] is True, audit["issues"]
    assert audit["invariants"]["list_items_preserved_once"] is True
    # The first item rendered the continuation inside one paragraph past a hard
    # break, so the readback text is the concatenation of both runs with the
    # break between them rather than a separate list item.
    with ZipFile(output) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    item = next(
        paragraph
        for paragraph in root.iter(f"{{{WORD_NS}}}p")
        if _paragraph_text(paragraph) in {"第一项续接正文一", "第一项\n续接正文一"}
    )
    assert any(child.tag == f"{{{WORD_NS}}}br" for child in item.iter())


def test_t09_readback_render_level_chain_replaces_the_flat_ilvl_chain(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output, _result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 两级列表\n\n1. 一级一\n   1. 二级一\n   2. 二级二\n2. 一级二\n",
    )
    comparison = audit_list_readback(output, normalized)
    # The saved levels are all zero, so the ilvl parent chain is uninformative;
    # the render-level chain recovered from the saved style role carries the
    # nesting and must agree with the AST projection.
    assert comparison["expected_render_parent_chain"] == [None, 0, 0, None], comparison["expected_render_parent_chain"]
    assert comparison["actual_render_parent_chain"] == [None, 0, 0, None], comparison["actual_render_parent_chain"]


def test_t09_readback_detects_a_saved_style_level_drift(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output, _result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 两级列表\n\n1. 一级一\n   1. 二级一\n   2. 二级二\n2. 一级二\n",
    )
    assert audit_list_readback(output, normalized)["passed"] is True
    first_level_style_id = _stylesheet_name_index(output)["1.1一级列项-编号"]

    def swap_second_level_style(root):
        for paragraph in root.iter(f"{{{WORD_NS}}}p"):
            if _paragraph_text(paragraph) == "二级一":
                style = paragraph.find("w:pPr/w:pStyle", _NS)
                style.set(f"{{{WORD_NS}}}val", first_level_style_id)

    _rewrite_part(output, "word/document.xml", swap_second_level_style)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    codes = [issue["code"] for issue in comparison["issues"]]
    assert "list_render_level_mismatch" in codes, codes


def test_t09_readback_detects_a_tampered_numbering_sequence(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output, _result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 功能列表\n\n1. 第一项功能\n2. 第二项功能\n3. 第三项功能\n",
    )
    assert audit_list_readback(output, normalized)["passed"] is True

    def add_start_override(numbering):
        for num in numbering.findall("w:num", _NS):
            if num.get(f"{{{WORD_NS}}}numId") != "3":
                continue
            override = ElementTree.SubElement(num, f"{{{WORD_NS}}}lvlOverride")
            override.set(f"{{{WORD_NS}}}ilvl", "0")
            start = ElementTree.SubElement(override, f"{{{WORD_NS}}}startOverride")
            start.set(f"{{{WORD_NS}}}val", "5")

    _rewrite_part(output, "word/numbering.xml", add_start_override)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    codes = [issue["code"] for issue in comparison["issues"]]
    assert "list_numbering_sequence_mismatch" in codes, codes
    assert comparison["invariants"]["numbering_sequence_agrees"] is False


def test_t09_readback_detects_a_duplicated_list_item(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output, _result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 功能列表\n\n1. 第一项功能\n2. 第二项功能\n",
    )
    assert audit_list_readback(output, normalized)["passed"] is True

    def duplicate_first_list_item(root):
        for index, paragraph in enumerate(list(root.iter(f"{{{WORD_NS}}}p"))):
            if _paragraph_text(paragraph) == "第一项功能":
                parent = next(node for node in root.iter() if paragraph in list(node))
                parent.append(ElementTree.fromstring(ElementTree.tostring(paragraph)))
                break

    _rewrite_part(output, "word/document.xml", duplicate_first_list_item)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    codes = [issue["code"] for issue in comparison["issues"]]
    assert "list_item_preservation_mismatch" in codes, codes
    assert comparison["invariants"]["list_items_preserved_once"] is False


def test_t09_semantic_rebuild_invariants_flag_a_deep_item_without_parent() -> None:
    from wxdf.auditing import _semantic_rebuild_invariants

    rebuild = {
        "items": [
            {"item_id": "a", "semantic_level": 0, "parent_item_id": None},
            {"item_id": "b", "semantic_level": 1, "parent_item_id": None},
        ],
        "continuations": [],
        "diagnostics": [],
        "downgrades": [],
        "state_unresolved": [],
        "status": "completed",
    }
    invariants, issues = _semantic_rebuild_invariants(rebuild)
    assert invariants["deep_items_have_parent"] is False
    assert any(issue["code"] == "list_deep_item_without_parent" for issue in issues), issues


def test_t09_semantic_rebuild_invariants_flag_a_continuation_without_owner() -> None:
    from wxdf.auditing import _semantic_rebuild_invariants

    rebuild = {
        "items": [],
        "continuations": [{"node_id": "p1b", "owner_item_id": None}],
        "diagnostics": [],
        "downgrades": [],
        "state_unresolved": [],
        "status": "completed",
    }
    invariants, issues = _semantic_rebuild_invariants(rebuild)
    assert invariants["continuations_have_owner"] is False
    assert any(issue["code"] == "list_continuation_without_owner" for issue in issues), issues


def test_t09_semantic_rebuild_invariants_flag_silent_ambiguity() -> None:
    from wxdf.auditing import _semantic_rebuild_invariants

    rebuild = {
        "items": [],
        "continuations": [],
        "diagnostics": [],
        "downgrades": [],
        "state_unresolved": [{"candidate_ids": ["x", "y"], "code": "equal_strength"}],
        "status": "completed",
    }
    invariants, issues = _semantic_rebuild_invariants(rebuild)
    assert invariants["no_silent_ambiguity"] is False
    codes = {issue["code"] for issue in issues}
    assert "list_silent_ambiguity" in codes, codes


def test_t09_semantic_rebuild_invariants_flag_an_unrecorded_low_confidence_block() -> None:
    from wxdf.auditing import _semantic_rebuild_invariants

    rebuild = {
        "items": [],
        "continuations": [],
        "diagnostics": [],
        "downgrades": [],
        "state_unresolved": [],
        "status": "completed",
    }
    facts = {"evidence": {"b1": {"numbering_status": "ambiguous"}}}
    invariants, issues = _semantic_rebuild_invariants(rebuild, facts)
    assert invariants["low_confidence_has_diagnostics"] is False
    codes = {issue["code"] for issue in issues}
    assert "list_low_confidence_without_diagnostic" in codes, codes


def test_t09_semantic_rebuild_invariants_reject_a_phantom_low_confidence_record() -> None:
    """A low-confidence record that cites a node the evidence lacks must fail."""

    from wxdf.auditing import _semantic_rebuild_invariants

    rebuild = {
        "items": [],
        "continuations": [],
        "diagnostics": [],
        "downgrades": [{"code": "low_confidence_list_marker", "node_id": "ghost"}],
        "state_unresolved": [],
        "status": "completed",
    }
    facts = {"evidence": {"b1": {"numbering_status": "detected"}}}
    invariants, issues = _semantic_rebuild_invariants(rebuild, facts)
    assert invariants["low_confidence_has_diagnostics"] is False
    issue = next(item for item in issues if item["code"] == "list_low_confidence_without_diagnostic")
    assert issue["phantom_records"] == ["ghost"], issue


def test_t09_semantic_rebuild_invariants_reject_completed_with_a_recorded_ambiguity() -> None:
    """A recorded ambiguity must not coexist with a ``completed`` status."""

    from wxdf.auditing import _semantic_rebuild_invariants

    rebuild = {
        "items": [],
        "continuations": [],
        "downgrades": [],
        "diagnostics": [
            {"code": "ambiguous_list_state", "candidate_ids": ["x", "y"]}
        ],
        "state_unresolved": [{"candidate_ids": ["x", "y"], "code": "equal_strength"}],
        "status": "completed",
    }
    invariants, issues = _semantic_rebuild_invariants(rebuild)
    assert invariants["no_silent_ambiguity"] is False
    assert any(issue["code"] == "list_silent_ambiguity" for issue in issues), issues


def test_t09_semantic_rebuild_invariants_accept_a_recorded_unresolved_ambiguity() -> None:
    """A recorded ambiguity with an ``unresolved`` status satisfies the invariant."""

    from wxdf.auditing import _semantic_rebuild_invariants

    rebuild = {
        "items": [],
        "continuations": [],
        "downgrades": [],
        "diagnostics": [
            {"code": "ambiguous_list_state", "candidate_ids": ["x", "y"]}
        ],
        "state_unresolved": [{"candidate_ids": ["x", "y"], "code": "equal_strength"}],
        "status": "unresolved",
    }
    invariants, issues = _semantic_rebuild_invariants(rebuild)
    assert invariants["no_silent_ambiguity"] is True, issues


def test_t09_readback_marks_skipped_semantic_invariants_when_the_rebuild_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild failure must report the semantic invariants as skipped (None)."""

    from wxdf import auditing
    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 列表\n\n1. 第一项\n2. 第二项\n",
    )
    assert result.status == "completed", result.diagnostics
    monkeypatch.setattr(
        auditing,
        "_rebuild_readback_lists",
        lambda path: (
            None,
            None,
            {"code": "list_readback_rebuild_skipped", "severity": "info"},
        ),
    )
    comparison = audit_list_readback(output, normalized)
    assert comparison["semantic_rebuild_checked"] is False
    assert comparison["rebuild"] == {
        "code": "list_readback_rebuild_skipped",
        "severity": "info",
    }
    assert all(
        comparison["invariants"][key] is None for key in _SEMANTIC_INVARIANT_KEYS
    ), comparison["invariants"]
    # A skipped invariant is not a passing one, but it is also not a list defect
    # the author can act on, so it does not block the document.
    assert comparison["passed"] is True, comparison["issues"]


def test_t09_readback_reports_an_unmapped_style_instead_of_disabling_the_chain(
    tmp_path: Path,
) -> None:
    """A permitted style change must not be able to turn the chain off.

    Dressing a list item in an allowed no-role style (``Normal``) used to make
    ``_actual_render_levels`` return "unavailable", which turned
    ``ast_readback_tree_agrees`` into a pass; combined with a level swap that
    kept ``numId`` intact, the wrong output went through.  An unmapped saved
    style is now its own issue.
    """

    from wxdf.auditing import audit_list_readback

    normalized, output, _result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 两级列表\n\n1. 一级一\n   1. 二级一\n   2. 二级二\n2. 一级二\n",
    )
    assert audit_list_readback(output, normalized)["passed"] is True
    styles = _stylesheet_name_index(output)
    first_level_style_id = styles["1.1一级列项-编号"]
    normal_style_id = styles["Normal"]

    def tamper(root):
        for paragraph in root.iter(f"{{{WORD_NS}}}p"):
            style = paragraph.find("w:pPr/w:pStyle", _NS)
            if style is None:
                continue
            text = _paragraph_text(paragraph)
            if text == "一级一":
                style.set(f"{{{WORD_NS}}}val", normal_style_id)
            elif text == "二级一":
                style.set(f"{{{WORD_NS}}}val", first_level_style_id)

    _rewrite_part(output, "word/document.xml", tamper)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    codes = [issue["code"] for issue in comparison["issues"]]
    assert "list_render_style_unmapped" in codes, codes
    assert comparison["invariants"]["ast_readback_tree_agrees"] is False


def test_t09_ordinary_document_stays_completed_with_empty_risk_warnings(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 章节\n\n### 一节\n\n普通正文段落。\n\n1.2 需求分析\n",
    )
    report = result.report
    assert result.status == "completed", result.diagnostics
    assert (report.get("risk_warnings") or []) == [], report.get("risk_warnings")
    comparison = audit_list_readback(output, normalized)
    assert all(comparison["invariants"][key] is True for key in _INVARIANT_KEYS), comparison["invariants"]
    assert [
        item
        for item in report["list_semantics"]["diagnostics"]
        if item.get("code") == "ambiguous_list_state"
    ] == [], report["list_semantics"]["diagnostics"]


# ---------------------------------------------------------------------------
# T10 F1: the semantic rebuild must run on the real staging artifact
# ---------------------------------------------------------------------------

def test_t09_readback_semantic_rebuild_runs_in_production(tmp_path: Path) -> None:
    """A real conversion must actually run the semantic readback rebuild.

    The kernel stages the output as ``.<name>.staging``.  A
    suffix-dispatched re-parse read that DOCX as UTF-8, failed, and reported every
    semantic invariant as ``None`` (skipped) behind a passing readback, so the
    invariants were unenforced in every conversion.  The production report now
    shows ``semantic_rebuild_checked`` true with the four semantic invariants
    evaluated rather than ``null``.
    """
    _normalized, _output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 两级列表\n\n1. 一级一\n   1. 二级一\n   2. 二级二\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics
    readback = result.report["native_module_evidence"]["native-auditor"]["downstream"]["list_readback"]
    assert readback["semantic_rebuild_checked"] is True, readback.get("rebuild")
    for key in _SEMANTIC_INVARIANT_KEYS:
        assert readback["invariants"][key] is True, readback["invariants"]


def test_t09_readback_rebuild_still_flags_a_tampered_staging_document(
    tmp_path: Path,
) -> None:
    """The semantic rebuild must still be able to fail on a tampered staging DOCX.

    ``deep_items_have_parent`` is not hardwired true: pointing a child at a
    valid multi-level instance at ``w:ilvl 2`` while no level-1 item exists makes
    the rebuild report a deep item without a parent.  The check runs against the
    real ``.<name>.staging`` artifact, so it fails on the tree where the
    rebuild never ran.
    """
    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 两级列表\n\n1. 一级一\n   1. 二级一\n   2. 二级二\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics
    staging = output.with_name(f".{output.name}.staging")
    os.replace(output, staging)

    def push_a_child_to_depth_two(root):
        for paragraph in root.iter(f"{{{WORD_NS}}}p"):
            if _paragraph_text(paragraph) == "二级一":
                num_pr = paragraph.find("w:pPr/w:numPr", _NS)
                num_pr.find("w:numId", _NS).set(f"{{{WORD_NS}}}val", "2")
                ilvl = num_pr.find("w:ilvl", _NS)
                if ilvl is None:
                    ilvl = ElementTree.SubElement(num_pr, f"{{{WORD_NS}}}ilvl")
                ilvl.set(f"{{{WORD_NS}}}val", "2")

    _rewrite_part(staging, "word/document.xml", push_a_child_to_depth_two)
    comparison = audit_list_readback(staging, normalized)
    assert comparison["semantic_rebuild_checked"] is True, comparison.get("rebuild")
    assert comparison["invariants"]["deep_items_have_parent"] is False
    codes = [issue["code"] for issue in comparison["issues"]]
    assert "list_deep_item_without_parent" in codes, codes


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
