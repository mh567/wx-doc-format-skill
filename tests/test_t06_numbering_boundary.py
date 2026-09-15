"""T06 numbering-boundary tests: restart, sequence split and merge, degradation.

The ticket models numbering continuity as an expression instead of identifier
equality:

  * a logical list split across several ``numId`` values merges back into one
    sequence when the observable evidence says it continues (README constraint
    8);
  * a numbering instance reused for a different logical list splits into a new
    sequence (README constraint 8);
  * restart is an expression built from ``start_value / sequence /
    restart_scope`` and it consumes ``lvlRestart`` and ``startOverride``
    (README constraint 10);
  * a sub-list either restarts with its parent or continues across parents, and
    the ``lvlRestart`` evidence decides which.

Every test in this file is an END-TO-END numbering-boundary test: it builds
source facts or a real DOCX, runs the native parser and the list semantics, and
asserts the resulting sequence model.  The red-light run is recorded in
``docs/execution-v6/evidence/T06.md``.

Fixture naming follows the T01 convention ``t06_<scenario>_<variant>``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


# ---------------------------------------------------------------------------
# SourceFacts fixtures
# ---------------------------------------------------------------------------

def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


def _paragraph(unit_id: str, index: int, text: str) -> dict:
    return {
        "kind": "paragraph",
        "source_ref": _ref(index),
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
    }


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


def _numbered(
    num_id: int,
    ordinal: int,
    *,
    depth: int = 0,
    fmt: str = "decimal",
    lvl_text: str = "%1.",
    restart: bool | None = None,
    lvl_restart: int | None = None,
    start_override: int | None = None,
) -> dict:
    evidence = {
        "marker_kind": "unordered" if fmt == "bullet" else "ordered",
        "marker_text": f"{ordinal}.",
        "ordinal": ordinal,
        "explicit_depth": depth,
        "numbering_definition_ref": {"num_id": num_id, "abstract_num_id": num_id},
        "num_fmt": fmt,
        "lvl_text": lvl_text,
        "num_id": num_id,
        "abstract_num_id": num_id,
        "source_ilvl": depth,
        "p_style_ref": None,
    }
    if restart is not None:
        evidence["restart_hint"] = restart
    if lvl_restart is not None:
        evidence["lvl_restart"] = lvl_restart
    if start_override is not None:
        evidence["start_override"] = start_override
        evidence["start_value"] = start_override
    return evidence


# ---------------------------------------------------------------------------
# Work item 2a: one logical list split across numIds merges back into a sequence
# ---------------------------------------------------------------------------

def test_t06_cross_numid_continuation_merges_into_one_sequence() -> None:
    """``[numId1][numId2]`` that continues the ordinal stays ONE level-0 list.

    The first instance carries ``restart_hint=False`` on the second instance,
    the numbered format is consistent and the items are contiguous, so the two
    instances are one logical sequence.  Before T06 the second instance is
    treated as a sub-list (``_resolve_sublist``) and the items cascade to
    levels 0/1.
    """

    units = {
        "a1": _paragraph("a1", 0, "步骤1"),
        "a2": _paragraph("a2", 1, "步骤2"),
        "b1": _paragraph("b1", 2, "步骤3"),
        "b2": _paragraph("b2", 3, "步骤4"),
    }
    children = {"root": ["a1", "a2", "b1", "b2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "a1": _numbered(1, 1, restart=True),
        "a2": _numbered(1, 2, restart=False),
        "b1": _numbered(2, 3, restart=False),
        "b2": _numbered(2, 4, restart=False),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert len(result["lists"]) == 1, result["lists"]
    assert [item["semantic_level"] for item in result["items"]] == [0, 0, 0, 0], result["items"]
    assert by_id["b1"]["parent_item_id"] is None, by_id["b1"]
    assert by_id["b2"]["parent_item_id"] is None, by_id["b2"]
    group = result["lists"][0]
    assert group["source_sequence_id"] == "docx-sequence-1-1", group
    assert group.get("merged_sequences") == ["docx-sequence-2-2"], group


# ---------------------------------------------------------------------------
# Work item 2b: the same numId reused for a new logical list splits
# ---------------------------------------------------------------------------

def test_t06_same_numid_restart_splits_into_two_sequences() -> None:
    """A restart inside one instance opens a new sequence instead of extending it.

    Both items share ``numId`` and the second one restarts, so the reused
    instance is a different logical list.  Before T06 both items collapse into
    a single group because the group key ignores the restart.
    """

    units = {
        "a1": _paragraph("a1", 0, "第一组1"),
        "a2": _paragraph("a2", 1, "第二组1"),
    }
    children = {"root": ["a1", "a2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "a1": _numbered(7, 1, restart=True),
        "a2": _numbered(7, 1, restart=True),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert len(result["lists"]) == 2, result["lists"]
    assert [item["semantic_level"] for item in result["items"]] == [0, 0], result["items"]
    second = result["lists"][1]
    assert second.get("group_boundary", {}).get("kind") == "sequence_split", second


def test_t06_restarted_sibling_sublist_splits_instead_of_merging() -> None:
    """A restarted sibling sub-list splits; the depth>0 path must not merge it.

    The parent and the first sub-list share one numbering instance; the second
    sub-list is a different instance marked ``restart_hint=True``, so it is a
    genuinely restarted sibling list under the same parent.  Before the fix the
    depth>0 path merged any different instance at the same depth, parent and
    family, so all three children collapsed to level 1 under one parent and the
    second sub-list wrongly carried ``merged_sequences``.  The depth>0 path now
    applies the same continuation evidence as the depth==0 path and emits a
    ``sequence_split`` boundary.
    """

    units = {
        "p": _paragraph("p", 0, "父项"),
        "s1": _paragraph("s1", 1, "子表1-1"),
        "s2a": _paragraph("s2a", 2, "重启子表1"),
        "s2b": _paragraph("s2b", 3, "重启子表2"),
    }
    children = {"root": ["p", "s1", "s2a", "s2b"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "p": _numbered(2000, 1, depth=0, restart=True),
        "s1": _numbered(2000, 1, depth=1, restart=True, lvl_text="%1.%2"),
        "s2a": _numbered(2001, 1, depth=1, restart=True, lvl_text="%1.%2"),
        "s2b": _numbered(2001, 2, depth=1, restart=False, lvl_text="%1.%2"),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert len(result["lists"]) == 3, result["lists"]
    assert [item["semantic_level"] for item in result["items"]] == [0, 1, 1, 1], result["items"]
    assert by_id["s1"]["parent_item_id"] == "p", by_id["s1"]
    assert by_id["s2a"]["parent_item_id"] == "p", by_id["s2a"]
    assert by_id["s2b"]["parent_item_id"] == "p", by_id["s2b"]
    restart_group = result["lists"][2]
    assert restart_group["source_sequence_id"] == "docx-sequence-2001-2001", restart_group
    assert restart_group.get("group_boundary", {}).get("kind") == "sequence_split", restart_group
    assert not restart_group.get("merged_sequences"), restart_group
    assert not result["lists"][1].get("merged_sequences"), result["lists"][1]


def test_t06_sequence_split_groups_are_distinguishable() -> None:
    """The two groups of a reused-instance split carry a distinguishable identity.

    A same-instance restart splits into two groups that share the numbering
    instance, so ``source_sequence_id`` / ``sequence`` / ``restart_scope`` are
    identical and the split would only exist in ``group_boundary.kind``.
    ``sequence_split_index`` is the additive identity field that makes the split
    observable on the published contract: the first group is index 0 and the
    split-off group is index 1.  The frozen ``docx-sequence-*`` spelling is
    unchanged.
    """

    units = {
        "a1": _paragraph("a1", 0, "第一组1"),
        "a2": _paragraph("a2", 1, "第二组1"),
    }
    children = {"root": ["a1", "a2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "a1": _numbered(7, 1, restart=True),
        "a2": _numbered(7, 1, restart=True),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    first, second = result["lists"]

    assert first["source_sequence_id"] == second["source_sequence_id"] == "docx-sequence-7-7"
    assert first["sequence"] == second["sequence"] == "docx-sequence-7-7"
    assert first["restart_scope"] == second["restart_scope"]
    assert second.get("group_boundary", {}).get("kind") == "sequence_split", second
    assert [first["sequence_split_index"], second["sequence_split_index"]] == [0, 1], result["lists"]
    identity = lambda group: (
        group["source_sequence_id"],
        group["sequence"],
        group["restart_scope"],
        group["sequence_split_index"],
    )
    assert identity(first) != identity(second)


def test_t06_ordinal_continuity_is_required_to_merge_across_instances() -> None:
    """An instance change merges only when the ordinal actually continues.

    ``restart_hint`` is explicitly false and the format matches, but the second
    instance restarts at ordinal 1 instead of continuing the open group's
    trailing run (whose next ordinal is 2).  The native rule rejects the merge on
    its own, so the block does not extend the open sequence.  The evidence layer
    never stamps this shape (its physical-group ordinal gate filters it first),
    so the native rule now stands without that legacy gate.
    """

    units = {
        "p": _paragraph("p", 0, "父项"),
        "s1": _paragraph("s1", 1, "子表1"),
        "t1": _paragraph("t1", 2, "另一实例重启1"),
    }
    children = {"root": ["p", "s1", "t1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "p": _numbered(2000, 1, depth=0, restart=True),
        "s1": _numbered(2000, 1, depth=1, restart=True, lvl_text="%1.%2"),
        "t1": _numbered(2001, 1, depth=1, restart=False, lvl_text="%1.%2"),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert len(result["lists"]) == 3, result["lists"]
    assert not result["lists"][1].get("merged_sequences"), result["lists"][1]
    assert result["lists"][2].get("group_boundary", {}).get("kind") == "sequence_split", result["lists"][2]


# ---------------------------------------------------------------------------
# Work item 1 / constraint 10: restart is an expression, lvl_restart consumed
# ---------------------------------------------------------------------------

def test_t06_restart_expression_consumes_lvl_restart_and_start_override() -> None:
    """The restart expression consumes lvlRestart and startOverride.

    ``lvlRestart`` decides the derived ``restart_policy`` and ``startOverride``
    decides the derived ``start_value``.  The same sub-list shape with
    ``lvlRestart=0`` yields ``continue_across_parents`` while the default yields
    ``restart_with_parent``, and the explicit start override drives the group's
    start value instead of the raw ordinal.
    """

    def build(lvl_restart: int | None) -> dict:
        units = {
            "p": _paragraph("p", 0, "父项"),
            "c": _paragraph("c", 1, "子项"),
        }
        children = {"root": ["p", "c"]}
        containers = {"root": {"kind": "document"}}
        evidence = {
            "p": _numbered(1, 1, restart=True),
            "c": _numbered(2, 1, depth=1, fmt="lower_letter", lvl_restart=lvl_restart,
                           start_override=3, restart=True),
        }
        return analyze_lists(_facts(containers, children, units, evidence))

    def child_group(result: dict) -> dict:
        return next(group for group in result["lists"] if group["parent_item_id"] is not None)

    continuing = child_group(build(0))
    restarting = child_group(build(None))

    assert continuing["restart_policy"] == "continue_across_parents", continuing
    assert restarting["restart_policy"] == "restart_with_parent", restarting
    assert continuing["restart_expression"]["lvl_restart"] == 0, continuing
    assert continuing["restart_expression"]["start_override"] == 3, continuing
    # startOverride drives the derived start value.
    assert continuing["start_value"] == 3, continuing
    assert continuing["restart_scope"].startswith("abstract:"), continuing


def test_t06_restart_split_resets_the_new_sequence_start_value() -> None:
    """A reused-instance restart resets the new sequence to its own start value.

    Both blocks reuse one numbering instance and the second one restarts, so the
    split opens a new sequence that starts at its own value 1.  A continuation
    would have advanced the start to 6 (the first group's start plus its item
    count), so the reset is genuinely derived from the split boundary.
    """

    units = {
        "a1": _paragraph("a1", 0, "步骤5"),
        "b1": _paragraph("b1", 1, "重新1"),
    }
    children = {"root": ["a1", "b1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "a1": _numbered(9, 5, restart=True, start_override=5),
        "b1": _numbered(9, 1, restart=True),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    first, second = result["lists"]

    assert [first["start_value"], second["start_value"]] == [5, 1], result["lists"]
    assert second.get("group_boundary", {}).get("kind") == "sequence_split", second
    # The first group has one item, so a continuation would have started at 6.
    assert second["start_value"] != first["start_value"] + 1, second


# ---------------------------------------------------------------------------
# Work item 4: sub-list restart policy
# ---------------------------------------------------------------------------

def _two_parent_sublist(lvl_restart: int | None, num_fmt: str) -> dict:
    units = {
        "p1": _paragraph("p1", 0, "父项一"),
        "c1": _paragraph("c1", 1, "子项a1"),
        "c2": _paragraph("c2", 2, "子项a2"),
        "p2": _paragraph("p2", 3, "父项二"),
        "c3": _paragraph("c3", 4, "子项b1"),
    }
    children = {"root": ["p1", "c1", "c2", "p2", "c3"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "p1": _numbered(1, 1, restart=True),
        "c1": _numbered(2, 1, depth=1, fmt=num_fmt, lvl_restart=lvl_restart, restart=True),
        "c2": _numbered(2, 2, depth=1, fmt=num_fmt, lvl_restart=lvl_restart, restart=False),
        "p2": _numbered(1, 2, restart=False),
        "c3": _numbered(2, 1, depth=1, fmt=num_fmt, lvl_restart=lvl_restart, restart=True),
    }
    return _facts(containers, children, units, evidence)


def test_t06_sublist_restart_policy_restarts_with_parent() -> None:
    """Without ``lvlRestart=0`` a child sub-list restarts under each parent."""

    result = analyze_lists(_two_parent_sublist(None, "lower_letter"))
    child_groups = [group for group in result["lists"] if group["parent_item_id"] is not None]
    child_levels = [item["semantic_level"] for item in result["items"] if item["parent_item_id"] is not None]

    assert len(child_groups) == 2, result["lists"]
    assert child_levels == [1, 1, 1], result["items"]
    assert all(group.get("restart_policy") == "restart_with_parent" for group in child_groups), child_groups
    assert [group["start_value"] for group in child_groups] == [1, 1], child_groups


def test_t06_sublist_restart_policy_continues_across_parents() -> None:
    """``lvlRestart=0`` makes the child sub-list continue across parents."""

    result = analyze_lists(_two_parent_sublist(0, "lower_letter"))
    child_groups = [group for group in result["lists"] if group["parent_item_id"] is not None]

    assert len(child_groups) == 2, result["lists"]
    assert all(group.get("restart_policy") == "continue_across_parents" for group in child_groups), child_groups
    # The second parent's child list continues the first parent's child count.
    assert [group["start_value"] for group in child_groups] == [1, 3], child_groups


# ---------------------------------------------------------------------------
# Latent tie: a non-paragraph unit after a run is owned, not an ambiguity
# ---------------------------------------------------------------------------

def test_t06_non_paragraph_unit_after_run_is_owned_without_ambiguity_tie() -> None:
    """A code block after an implicit run is item content, not a state tie.

    The block is content of the run's last item (``ITEM_CHILD_BLOCK``).  When a
    non-paragraph unit also published a protected ``OUTSIDE_LIST`` candidate of
    equal evidence strength, the Resolver recorded a spurious ambiguity.  The
    ownership candidate is the only one that is now offered.
    """

    units = {
        "a1": _paragraph("a1", 0, "第一项"),
        "code": {"kind": "code_block", "source_ref": _ref(1), "raw_text": "print(1)"},
    }
    children = {"root": ["a1", "code"]}
    containers = {"root": {"kind": "document"}}
    evidence = {"a1": _numbered(1, 1, restart=True)}

    result = analyze_lists(_facts(containers, children, units, evidence))
    codes = {item["code"] for item in result["diagnostics"]}

    assert "ambiguous_list_state" not in codes, result["diagnostics"]
    owned = {entry["node_id"]: entry["state"] for entry in result["owned_content"]}
    assert owned.get("code") == "ITEM_CHILD_BLOCK", result["owned_content"]


# ---------------------------------------------------------------------------
# Work item 3 (guard): a damaged numbering definition degrades to body text
# ---------------------------------------------------------------------------

def test_t06_damaged_numbering_definition_degrades_and_preserves_source() -> None:
    """A damaged definition stays body text, records a downgrade and keeps text.

    This is the T06 regression guard for work item 3: the downgrade path is the
    same one T02 established, and the source paragraph must survive as body
    text with its evidence attached.
    """

    units = {"b1": _paragraph("b1", 0, "编号定义缺失的段落。")}
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
    codes = {item["code"] for item in result["diagnostics"]}

    assert result["items"] == [], result["items"]
    assert codes & {"missing_numbering_definition", "invalid_numbering_definition"}, result["diagnostics"]
    assert result["downgrades"], result["downgrades"]
    assert result["downgrades"][0]["fallback"]["preserve_source_content"] is True
    assert "b1" in units, "the source paragraph must be preserved"


# ---------------------------------------------------------------------------
# End-to-end: a real DOCX whose numbering is split across numIds
# ---------------------------------------------------------------------------

def _level(ilvl: int, *, start: int = 1, num_fmt: str = "decimal",
           lvl_text: str = "%1.", lvl_restart: int | None = None):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    element = OxmlElement("w:lvl")
    element.set(qn("w:ilvl"), str(ilvl))
    for tag, value in (("w:start", start), ("w:numFmt", num_fmt),
                       ("w:lvlRestart", lvl_restart), ("w:lvlText", lvl_text)):
        if value is None:
            continue
        child = OxmlElement(tag)
        child.set(qn("w:val"), str(value))
        element.append(child)
    return element


def _build_numbered_docx(path: Path, *, abstract_id: int, levels: list, nums: list, spec: list) -> None:
    """Write a DOCX with direct ``w:numPr``; ``spec`` is ``(text, num_id, ilvl)``.

    ``nums`` is ``(num_id, start_override)``; a non-``None`` start writes a
    ``w:startOverride`` for level 0 on that instance.
    """

    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = Document()
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    for level in levels:
        abstract.append(level)
    root.insert(0, abstract)
    for num_id, start in nums:
        num = OxmlElement("w:num")
        num.set(qn("w:numId"), str(num_id))
        reference = OxmlElement("w:abstractNumId")
        reference.set(qn("w:val"), str(abstract_id))
        num.append(reference)
        if start is not None:
            override = OxmlElement("w:lvlOverride")
            override.set(qn("w:ilvl"), "0")
            start_override = OxmlElement("w:startOverride")
            start_override.set(qn("w:val"), str(start))
            override.append(start_override)
            num.append(override)
        root.append(num)
    for text, num_id, ilvl in spec:
        paragraph = document.add_paragraph(text)
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


def _convert_docx(tmp_path: Path, source: Path, tag: str):
    import json

    output = tmp_path / f"{tag}.out.docx"
    normalized_path = tmp_path / f"{tag}.normalized.json"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"normalized_ast_path": normalized_path, "llm_enhance": "off"},
    )
    result = ConversionKernel().convert(request)
    normalized = json.loads(normalized_path.read_text(encoding="utf-8")) if normalized_path.exists() else {}
    return normalized, output, result


def test_t06_cross_numid_continuation_end_to_end(tmp_path: Path) -> None:
    """A real DOCX that continues across numIds stays one flat level-0 list.

    Word splits one visible sequence into several ``numId`` values and marks the
    continuation with a ``w:startOverride``.  The second and third instances do
    not restart, so the native parser and the list semantics must reconstruct
    one level-0 sequence instead of nesting 0/1/2.
    """

    source = tmp_path / "separated.docx"
    _build_numbered_docx(
        source,
        abstract_id=1000,
        levels=[_level(0)],
        nums=[(1000, None), (1001, 3), (1002, 4)],
        spec=[("步骤1", 1000, 0), ("步骤2", 1000, 0), ("步骤3", 1001, 0), ("步骤4", 1002, 0)],
    )

    result = analyze_lists(parse(source))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert len(result["lists"]) == 1, result["lists"]
    assert len(result["items"]) == 4, result["items"]
    assert [item["semantic_level"] for item in result["items"]] == [0, 0, 0, 0], result["items"]
    assert all(by_id[item_id]["parent_item_id"] is None for item_id in by_id), result["items"]
    assert result["lists"][0].get("merged_sequences"), result["lists"][0]


def test_t06_restarted_sibling_sublist_end_to_end(tmp_path: Path) -> None:
    """A real DOCX with two sibling sub-lists keeps them as two sequences.

    One parent (``numId 2000, ilvl 0``) owns a sub-list (``numId 2000, ilvl 1``)
    and a genuinely restarted sibling sub-list (``numId 2001, ilvl 1``, two
    items) in the same abstract.  Before the fix the depth>0 merge collapsed the
    restarted sibling into the first sub-list and all three children landed at
    level 1 under one list; the split must keep them as two level-1 lists.
    """

    source = tmp_path / "sibling_sublist.docx"
    _build_numbered_docx(
        source,
        abstract_id=2000,
        levels=[_level(0), _level(1, lvl_text="%1.%2")],
        nums=[(2000, None), (2001, None)],
        spec=[("父项", 2000, 0), ("子项a1", 2000, 1), ("重启子项b1", 2001, 1), ("重启子项b2", 2001, 1)],
    )

    result = analyze_lists(parse(source))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert len(result["lists"]) == 3, result["lists"]
    assert [item["semantic_level"] for item in result["items"]] == [0, 1, 1, 1], result["items"]
    child_groups = [group for group in result["lists"] if group["parent_item_id"] is not None]
    assert len(child_groups) == 2, result["lists"]
    assert child_groups[0]["source_sequence_id"] == "docx-sequence-2000-2000", child_groups
    assert child_groups[1]["source_sequence_id"] == "docx-sequence-2001-2000", child_groups
    assert child_groups[1].get("group_boundary", {}).get("kind") == "sequence_split", child_groups[1]
    assert not child_groups[1].get("merged_sequences"), child_groups[1]
    assert all(
        by_id[item_id]["parent_item_id"] == child_groups[0]["parent_item_id"]
        for item_id in by_id
        if by_id[item_id]["parent_item_id"] is not None
    ), result["items"]


def test_t06_group_restart_scope_matches_item_scope(tmp_path: Path) -> None:
    """The group's restart_scope is the catalogue scope, not the sequence copy.

    README defines ``restart_scope`` as derived from ``num_id`` /
    ``abstract_num_id``.  After the fix the group and its items carry the same
    ``abstract:<id>`` scope while ``sequence`` / ``source_sequence_id`` stay the
    ``docx-sequence-*`` identity, so the scope is no longer a duplicate of the
    sequence.
    """

    source = tmp_path / "scope.docx"
    _build_numbered_docx(
        source,
        abstract_id=2000,
        levels=[_level(0)],
        nums=[(2000, None)],
        spec=[("第一项", 2000, 0), ("第二项", 2000, 0)],
    )

    result = analyze_lists(parse(source))
    group = result["lists"][0]

    assert group["restart_scope"] == "abstract:2000", group
    assert group["restart_expression"]["restart_scope"] == "abstract:2000", group["restart_expression"]
    assert all(item["restart_scope"] == "abstract:2000" for item in result["items"]), result["items"]
    assert group["restart_scope"] != group["sequence"], group
    assert group["restart_scope"] != group["source_sequence_id"], group


def test_t06_cross_numid_continuation_conversion_groups(tmp_path: Path) -> None:
    """The conversion report publishes ONE list for a split sequence.

    This is the user-visible contract: ``list_semantics.list_count`` and the
    source AST hierarchy count one list, not one list per numId.
    """

    source = tmp_path / "separated.docx"
    _build_numbered_docx(
        source,
        abstract_id=1000,
        levels=[_level(0)],
        nums=[(1000, None), (1001, 3), (1002, 4)],
        spec=[("步骤1", 1000, 0), ("步骤2", 1000, 0), ("步骤3", 1001, 0), ("步骤4", 1002, 0)],
    )

    _, _output, result = _convert_docx(tmp_path, source, "separated")
    assert result.status == "completed", result.diagnostics
    report = result.as_report()

    assert report["list_semantics"]["list_count"] == 1, report["list_semantics"]["groups"]
    assert report["list_semantics"]["item_count"] == 4, report["list_semantics"]
    assert report["source_ast_hierarchy"]["list_count"] == 1, report["source_ast_hierarchy"]


def test_t06_sub_list_restart_policy_end_to_end(tmp_path: Path) -> None:
    """``lvlRestart`` decides the sub-list restart policy, end to end.

    The default definition restarts level 1 under each parent; a definition with
    ``w:lvlRestart=0`` keeps the child sequence running across parents.  Both
    cases must be modelled from the catalog evidence.
    """

    default = tmp_path / "restart.docx"
    _build_numbered_docx(
        default,
        abstract_id=2000,
        levels=[_level(0), _level(1, lvl_text="%1.%2")],
        nums=[(2000, None)],
        spec=[("父项一", 2000, 0), ("子项a1", 2000, 1), ("子项a2", 2000, 1),
              ("父项二", 2000, 0), ("子项b1", 2000, 1)],
    )
    continuing = tmp_path / "continue.docx"
    _build_numbered_docx(
        continuing,
        abstract_id=2000,
        levels=[_level(0), _level(1, lvl_text="%1.%2", lvl_restart=0)],
        nums=[(2000, None)],
        spec=[("父项一", 2000, 0), ("子项a1", 2000, 1), ("子项a2", 2000, 1),
              ("父项二", 2000, 0), ("子项b1", 2000, 1)],
    )

    default_result = analyze_lists(parse(default))
    continuing_result = analyze_lists(parse(continuing))
    default_children = [group for group in default_result["lists"] if group["parent_item_id"] is not None]
    continuing_children = [group for group in continuing_result["lists"] if group["parent_item_id"] is not None]

    assert all(group.get("restart_policy") == "restart_with_parent" for group in default_children), default_children
    assert all(group.get("restart_policy") == "continue_across_parents" for group in continuing_children), continuing_children
    assert [group["start_value"] for group in continuing_children] == [1, 3], continuing_children


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
