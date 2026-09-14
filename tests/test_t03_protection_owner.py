"""T03: the protection policy has exactly one owner in protected.py."""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.semantics import lists as lists_module
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.protected import (
    collect_protected_candidates,
    protected_unit_semantics,
)


# The policy constants live in protected.py. lists.py must not redefine any of
# them, otherwise the two copies can drift silently.
_POLICY_CONSTANTS = (
    "_DATE_RE",
    "_VERSION_RE",
    "_VERSION_CONTEXT_RE",
    "_API_RE",
    "_HTTP_API_RE",
    "_API_LABELS",
    "_MANUAL_HEADING_RE",
    "_CODE_STYLE_RE",
)


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


def test_lists_module_does_not_own_protection_policy() -> None:
    for name in _POLICY_CONSTANTS:
        assert not hasattr(lists_module, name), f"lists.py still defines {name}"


def test_lists_and_protected_agree_on_shared_policy() -> None:
    # (text, style, expected protection reason). Marker-kind evidence is placed
    # on every unit so the list recognizer actually evaluates protection.
    cases = {
        "date": ("2026-09-08", None, "date_or_version"),
        "version": ("版本 2.0", None, "date_or_version"),
        "version_context": ("v1.2 发布", None, "date_or_version"),
        "api_label": ("请求参数", None, "api_label"),
        "api_prefix": ("API: /v1/items", None, "api_label"),
        "manual_heading": ("1.2.3 标题", None, "manual_heading_marker"),
        "code_style": ("代码样例", "代码", "opaque_code"),
        "plain": ("普通列项内容", None, None),
    }
    units = {
        key: _paragraph(key, index, text, style=style)
        for index, (key, (text, style, _)) in enumerate(cases.items())
    }
    children = {"root": list(cases)}
    containers = {"root": {"kind": "document"}}
    evidence = {
        key: {"marker_kind": "ordered", "marker_text": "1.", "ordinal": index + 1, "explicit_depth": 0}
        for index, key in enumerate(cases)
    }
    facts = _facts(containers, children, units, evidence)

    list_result = analyze_lists(facts)
    list_reasons = {
        item["node_id"]: item["message"]
        for item in list_result["diagnostics"]
        if item["code"] == "protected_list_like_text"
    }
    protected_reasons = {
        candidate["target_node_ids"][0]: candidate["proposal"]["value"]["reason"]
        for candidate in collect_protected_candidates(facts)
        if len(candidate["target_node_ids"]) == 1 and candidate["target_node_ids"][0] in cases
    }

    for key, (_text, _style, expected) in cases.items():
        semantic = protected_unit_semantics(units[key], evidence[key])
        if expected is None:
            assert semantic is None
            assert key not in list_reasons
            assert key not in protected_reasons
            continue
        assert semantic is not None
        assert semantic["reason"] == expected
        assert list_reasons.get(key) == expected
        assert protected_reasons.get(key) == expected
