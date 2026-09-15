"""T01 failing-sample library for the list semantic compiler.

Each fixture captures a current broken behaviour of the list handling
pipeline and asserts the EXPECTED post-fix outcome. Every test in this
file MUST fail against the current implementation (red) and pass once
T02+ lands the field-level evidence, restart expression, and nesting
fixes described in docs/execution-v6/README.md.

Fixture naming convention for subsequent tickets:
  t01_<scenario>_<variant>  (e.g. t01_nested_ordered_new_numid_ilvl0)
"""
from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.semantics.lists import analyze_lists


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


# Complete list of evidence fields required by the spec (docs/execution-v6/README.md).
_T01_SPEC_EVIDENCE_FIELDS = [
    "source_ilvl",
    "effective_ilvl",
    "semantic_level",
    "render_level",
    "start_value",
    "sequence",
    "restart_scope",
    "num_id",
    "abstract_num_id",
    "num_fmt",
    "lvl_text",
    "w:ind",
    "suffix",
    "p_style_ref",
]


def _assert_spec_fields_present(result: dict) -> None:
    """Assert every list item carries the full spec evidence field set.

    The current analyze_lists implementation produces items with only a
    narrow field set (item_id, list_id, parent_item_id, source_marker,
    source_ordinal, source_sequence_id, list_kind, target_ordinal, ...).
    After T02+, items must also carry the full evidence field list.
    """
    for item in result["items"]:
        missing = [f for f in _T01_SPEC_EVIDENCE_FIELDS if f not in item]
        assert not missing, (
            f"list item {item.get('item_id')!r} is missing spec evidence fields: {missing}; "
            f"present fields: {sorted(item.keys())}"
        )


# ---------------------------------------------------------------------------
# Fixture 1: heading misidentified as list item
# ---------------------------------------------------------------------------

def test_t01_heading_not_list_item() -> None:
    """Current: a heading paragraph with numbering is emitted as a list item.

    Expected: the heading is excluded from list items and the paragraph
    keeps its heading role. The list relation set must not contain the
    heading node id. Additionally, the remaining list item must carry the
    full spec evidence field set.
    """
    units = {
        "h1": _paragraph("h1", 0, "6.3 标准化注册时序流程", style="章标题"),
        "b1": _paragraph("b1", 1, "终端用户负责发起认证请求"),
    }
    children = {"root": ["h1", "b1"]}
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
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": "章标题",
        },
        "b1": {
            "marker_kind": "ordered",
            "marker_text": "a)",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "lower_letter",
            "lvl_text": "%1)",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    item_ids = {item["item_id"] for item in result["items"]}

    assert "h1" not in item_ids, (
        f"current behaviour promotes heading h1 to list item; "
        f"expected h1 to be excluded from list items but got item_ids={item_ids}"
    )
    _assert_spec_fields_present(result)


# ---------------------------------------------------------------------------
# Fixture 2: consecutive body paragraphs misidentified as a list
# ---------------------------------------------------------------------------

def test_t01_body_paragraphs_not_list() -> None:
    """Current: consecutive body paragraphs that share a left indent and a
    numeric-looking prefix are grouped as a list by the parser, and
    analyze_lists has no safeguard to reject them.

    Expected: body paragraphs without a real numbering definition or a
    visible list marker stay as body paragraphs and are not emitted as
    list items. When the parser incorrectly assigns marker_kind to such
    paragraphs, analyze_lists must respect an explicit protected or
    numbering_status:"ignored" signal and exclude them.
    """
    units = {
        "b1": _paragraph("b1", 0, "第一段描述了系统当前的运行情况。"),
        "b2": _paragraph("b1", 1, "第二段继续说明系统当前的配置情况。"),
        "b3": _paragraph("b1", 2, "第三段补充系统后续的演进方向。"),
    }
    children = {"root": ["b1", "b2", "b3"]}
    containers = {"root": {"kind": "document"}}
    # Simulate the parser incorrectly detecting marker_kind on plain body text.
    evidence = {
        "b1": {
            "marker_kind": "ordered",
            "marker_text": "第一段",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": None,
            "num_fmt": None,
            "numbering_status": "ignored",
            "p_style_ref": None,
        },
        "b2": {
            "marker_kind": "ordered",
            "marker_text": "第二段",
            "ordinal": 2,
            "explicit_depth": 0,
            "numbering_definition_ref": None,
            "num_fmt": None,
            "numbering_status": "ignored",
            "p_style_ref": None,
        },
        "b3": {
            "marker_kind": "ordered",
            "marker_text": "第三段",
            "ordinal": 3,
            "explicit_depth": 0,
            "numbering_definition_ref": None,
            "num_fmt": None,
            "numbering_status": "ignored",
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    item_ids = {item["item_id"] for item in result["items"]}

    assert item_ids == set(), (
        f"current behaviour promotes plain body paragraphs to list items; "
        f"expected no list items but got item_ids={item_ids}"
    )


# ---------------------------------------------------------------------------
# Fixture 3: ordered vs unordered picked wrong
# ---------------------------------------------------------------------------

def test_t01_ordered_unordered_category() -> None:
    """Current: when marker_kind and num_fmt disagree, the current code
    picks list_kind from marker_kind alone, ignoring the more reliable
    OOXML num_fmt signal.

    Expected: num_fmt takes precedence over marker_kind for category
    selection. A marker_kind of "ordered" with num_fmt="bullet" must be
    classified as unordered.
    """
    units = {
        "o1": _paragraph("o1", 1, "有序第一项"),
        "conflict": _paragraph("conflict", 2, "冲突项"),
    }
    children = {"root": ["o1", "conflict"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "o1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "conflict": {
            "marker_kind": "ordered",
            "marker_text": "2.",
            "ordinal": 2,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "bullet",
            "lvl_text": "•",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert by_id["o1"]["list_kind"] == "ordered", (
        f"current behaviour picks wrong category for decimal num_fmt; "
        f"expected ordered but got {by_id['o1']['list_kind']!r}"
    )
    assert by_id["conflict"]["list_kind"] == "unordered", (
        f"current behaviour ignores num_fmt when marker_kind disagrees; "
        f"expected unordered (num_fmt=bullet) but got {by_id['conflict']['list_kind']!r}"
    )
    _assert_spec_fields_present(result)


# ---------------------------------------------------------------------------
# Fixture 4: level-1 vs level-2 priority wrong
# ---------------------------------------------------------------------------

def test_t01_level_priority_source_ilvl() -> None:
    """Current: source_ilvl is ignored and effective_ilvl is guessed from
    indent or ordinal, so a level-2 item (ilvl=1) is treated as level-1.

    Expected: source_ilvl is honoured. An item with source_ilvl=1 is
    emitted with effective_ilvl=1 and parented to the preceding
    effective_ilvl=0 item. The items must also carry spec evidence fields.
    """
    units = {
        "l0": _paragraph("l0", 0, "一级条目"),
        "l1": _paragraph("l1", 1, "二级条目"),
    }
    children = {"root": ["l0", "l1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "l0": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "l1": {
            "marker_kind": "ordered",
            "marker_text": "a)",
            "ordinal": 1,
            "explicit_depth": 1,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "lower_letter",
            "lvl_text": "%2)",
            "restart_hint": False,
            "source_ilvl": 1,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert by_id["l1"]["parent_item_id"] == "l0", (
        f"current behaviour flattens level-2 to level-1 or orphans it; "
        f"expected l1.parent_item_id=='l0' but got {by_id['l1']['parent_item_id']!r}"
    )
    _assert_spec_fields_present(result)


# ---------------------------------------------------------------------------
# Fixture 5: nested ordered / unordered / mixed / interleaved with body
# ---------------------------------------------------------------------------

def test_t01_nested_ordered_unordered_mixed() -> None:
    """Current: nested lists with mixed ordered/unordered children or body
    paragraphs interleaved between list items lose their parent or get
    attached to the wrong list.

    Expected: the nested structure is preserved. The unordered child is
    parented to the ordered parent, and the interleaved body paragraph
    is NOT absorbed into the list. All items carry spec evidence fields.
    """
    units = {
        "o1": _paragraph("o1", 0, "有序父项"),
        "u1": _paragraph("u1", 1, "无序子项一"),
        "u2": _paragraph("u2", 2, "无序子项二"),
        "body": _paragraph("body", 3, "穿插正文段落"),
        "o2": _paragraph("o2", 4, "有序父项二"),
    }
    children = {"root": ["o1", "u1", "u2", "body", "o2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "o1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "u1": {
            "marker_kind": "unordered",
            "marker_text": "•",
            "ordinal": None,
            "explicit_depth": 1,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "bullet",
            "lvl_text": "•",
            "restart_hint": True,
            "source_ilvl": 1,
            "p_style_ref": None,
        },
        "u2": {
            "marker_kind": "unordered",
            "marker_text": "•",
            "ordinal": None,
            "explicit_depth": 1,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "bullet",
            "lvl_text": "•",
            "restart_hint": False,
            "source_ilvl": 1,
            "p_style_ref": None,
        },
        "body": {
            "marker_kind": None,
            "explicit_depth": 0,
            "numbering_definition_ref": None,
            "num_fmt": None,
            "p_style_ref": None,
        },
        "o2": {
            "marker_kind": "ordered",
            "marker_text": "2.",
            "ordinal": 2,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": False,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    by_id = {item["item_id"]: item for item in result["items"]}
    item_ids = set(by_id)

    assert "body" not in item_ids, (
        f"current behaviour absorbs interleaved body paragraph into list; "
        f"expected body to stay outside but got item_ids={item_ids}"
    )
    assert by_id["u1"]["parent_item_id"] == "o1", (
        f"current behaviour misparents unordered child; "
        f"expected u1.parent_item_id=='o1' but got {by_id['u1']['parent_item_id']!r}"
    )
    assert by_id["u2"]["parent_item_id"] == "o1", (
        f"current behaviour misparents second unordered child; "
        f"expected u2.parent_item_id=='o1' but got {by_id['u2']['parent_item_id']!r}"
    )
    assert by_id["o2"]["parent_item_id"] is None, (
        f"current behaviour wrongly parents top-level ordered item; "
        f"expected o2.parent_item_id is None but got {by_id['o2']['parent_item_id']!r}"
    )
    _assert_spec_fields_present(result)


# ---------------------------------------------------------------------------
# Fixture 6: sub-list with new numId but ilvl=0
# ---------------------------------------------------------------------------

def test_t01_sublist_new_numid_ilvl0() -> None:
    """Current: a sub-list that starts a new numId at ilvl=0 is merged
    into the parent list or flattened, losing the sub-list boundary.

    Expected: the new numId at ilvl=0 starts a fresh sub-list nested
    under the preceding parent item. The sub-list items are parented to
    the parent item, not to the document root.
    """
    units = {
        "p1": _paragraph("p1", 0, "父项"),
        "c1": _paragraph("c1", 1, "子项一"),
        "c2": _paragraph("c2", 2, "子项二"),
    }
    children = {"root": ["p1", "c1", "c2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "p1": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "c1": {
            "marker_kind": "ordered",
            "marker_text": "a)",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "lower_letter",
            "lvl_text": "%1)",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "c2": {
            "marker_kind": "ordered",
            "marker_text": "b)",
            "ordinal": 2,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 2, "abstract_num_id": 2},
            "num_fmt": "lower_letter",
            "lvl_text": "%1)",
            "restart_hint": False,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert by_id["c1"]["parent_item_id"] == "p1", (
        f"current behaviour merges new-numId ilvl=0 sub-list into parent; "
        f"expected c1.parent_item_id=='p1' but got {by_id['c1']['parent_item_id']!r}"
    )
    assert by_id["c2"]["parent_item_id"] == "p1", (
        f"current behaviour merges new-numId ilvl=0 sub-list into parent; "
        f"expected c2.parent_item_id=='p1' but got {by_id['c2']['parent_item_id']!r}"
    )


# ---------------------------------------------------------------------------
# Fixture 7: list item with continuation paragraph
# ---------------------------------------------------------------------------

def test_t01_list_item_continuation() -> None:
    """Current: a continuation paragraph (no marker, same list item) is
    either dropped or promoted to a standalone list item.

    Expected: the continuation paragraph is recorded in
    ``result["continuations"]`` with the correct ``owner_item_id``.
    All list items carry spec evidence fields.
    """
    units = {
        "p1": _paragraph("p1", 0, "第一项"),
        "p1b": _paragraph("p1b", 1, "第一项的续接正文"),
    }
    containers = {
        "root": {"kind": "document"},
        "list": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s"},
        "item": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "s"},
    }
    children = {"root": ["list"], "list": ["item"], "item": ["p1", "p1b"]}
    evidence = {
        "item": {
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "p1b": {"continuation_hint": True},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    continuations = result["continuations"]
    continuation_ids = {c["node_id"] for c in continuations}

    assert "p1b" in continuation_ids, (
        f"current behaviour drops or promotes continuation paragraph; "
        f"expected p1b in continuations but got {continuation_ids!r}"
    )
    owner = next(c["owner_item_id"] for c in continuations if c["node_id"] == "p1b")
    assert owner == "item", (
        f"current behaviour assigns wrong owner to continuation; "
        f"expected owner=='item' but got {owner!r}"
    )
    _assert_spec_fields_present(result)


# ---------------------------------------------------------------------------
# Fixture 8: missing / damaged numbering
# ---------------------------------------------------------------------------

def test_t01_missing_numbering_definition() -> None:
    """Current: a paragraph whose numbering definition is missing or
    damaged but whose marker_kind is set (parser detected a marker) is
    still promoted to a list item, because the implicit-list loop does
    not honour numbering_status:"ignored".

    Expected: the paragraph is downgraded to a body paragraph, a
    diagnostic with code ``missing_numbering_definition`` or
    ``invalid_numbering_definition`` is recorded, and the source content
    is preserved.
    """
    units = {
        "b1": _paragraph("b1", 0, "生物特征认证系统与IAM之间的逻辑接口如表1所示。"),
    }
    children = {"root": ["b1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "b1": {
            "numbering_status": "ignored",
            "numbering_definition_ref": None,
            "numbering_evidence": ["invalid_numbering_definition"],
            "marker_kind": "ordered",
            "marker_text": "1.",
            "ordinal": 1,
            "explicit_depth": 0,
            "num_fmt": None,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    item_ids = {item["item_id"] for item in result["items"]}
    diagnostic_codes = {d["code"] for d in result["diagnostics"]}

    assert "b1" not in item_ids, (
        f"current behaviour promotes damaged-numbering paragraph to list item "
        f"even when numbering_status is ignored; "
        f"expected b1 to be excluded but got item_ids={item_ids}"
    )
    assert diagnostic_codes & {"invalid_numbering_definition", "missing_numbering_definition"}, (
        f"current behaviour silently drops damaged numbering; "
        f"expected a numbering diagnostic but got {diagnostic_codes!r}"
    )


# ---------------------------------------------------------------------------
# Fixture 9: evidence field completeness (spec fields present on items)
# ---------------------------------------------------------------------------

def test_t01_evidence_field_completeness() -> None:
    """Current: list items carry only a narrow set of fields
    (item_id, list_id, parent_item_id, source_marker, source_ordinal,
    source_sequence_id, list_kind, target_ordinal, ...).

    Expected: each item also exposes the full evidence field list from
    the spec: source_ilvl, effective_ilvl, semantic_level, render_level,
    start_value, sequence, restart_scope, num_id, abstract_num_id,
    num_fmt, lvl_text, w:ind, suffix, p_style_ref.
    """
    units = {
        "u1": _paragraph("u1", 0, "步骤一"),
    }
    children = {"root": ["u1"]}
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
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    assert result["items"], "expected at least one list item"
    _assert_spec_fields_present(result)


# ---------------------------------------------------------------------------
# Fixture 10: restart is an expression, not a boolean
# ---------------------------------------------------------------------------

def test_t01_restart_is_expression() -> None:
    """Current: restart is a boolean (restart_hint) that cannot express
    start_value, sequence, or restart_scope.

    Expected: restart information is an expression object carrying
    start_value, sequence, and restart_scope, so a list that resumes at
    a non-1 ordinal is represented precisely.
    """
    units = {
        "u1": _paragraph("u1", 0, "步骤3"),
        "u2": _paragraph("u2", 1, "步骤4"),
    }
    children = {"root": ["u1", "u2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "u1": {
            "marker_kind": "ordered",
            "marker_text": "3.",
            "ordinal": 3,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": True,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
        "u2": {
            "marker_kind": "ordered",
            "marker_text": "4.",
            "ordinal": 4,
            "explicit_depth": 0,
            "numbering_definition_ref": {"num_id": 1, "abstract_num_id": 1},
            "num_fmt": "decimal",
            "lvl_text": "%1.",
            "restart_hint": False,
            "source_ilvl": 0,
            "p_style_ref": None,
        },
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    list_entry = result["lists"][0]

    assert "start_value" in list_entry, (
        f"current behaviour uses boolean restart_hint; "
        f"expected list entry to carry start_value but got keys={sorted(list_entry.keys())}"
    )
    assert list_entry["start_value"] == 3, (
        f"current behaviour loses the real start ordinal; "
        f"expected start_value==3 but got {list_entry.get('start_value')!r}"
    )
    assert "sequence" in list_entry, (
        f"current behaviour has no sequence identifier on list entry; "
        f"expected 'sequence' key but got keys={sorted(list_entry.keys())}"
    )
    assert "restart_scope" in list_entry, (
        f"current behaviour has no restart_scope on list entry; "
        f"expected 'restart_scope' key but got keys={sorted(list_entry.keys())}"
    )


# ---------------------------------------------------------------------------
# Aggregate red test: all fixtures satisfy expected list semantics
# ---------------------------------------------------------------------------

def test_t01_failing_samples_satisfy_expected_semantics() -> None:
    """Run every fixture scenario and assert the EXPECTED list semantics.

    This is the single aggregate red test for T01. It calls each fixture
    test in-process and fails if any of them fails. After T02+ lands the
    fixes, this test should turn green.
    """
    test_t01_heading_not_list_item()
    test_t01_body_paragraphs_not_list()
    test_t01_ordered_unordered_category()
    test_t01_level_priority_source_ilvl()
    test_t01_nested_ordered_unordered_mixed()
    test_t01_sublist_new_numid_ilvl0()
    test_t01_list_item_continuation()
    test_t01_missing_numbering_definition()
    test_t01_evidence_field_completeness()
    test_t01_restart_is_expression()
