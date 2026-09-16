"""Independent model and saved-artifact audits for the modular renderer."""

from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from docx import Document

from .model import effective_outline_level, is_body_outline_heading
from .render_plan import build_render_plan
from .validation import validate_ast


_REPAIRABLE_AUDIT_CODES = frozenset({
    ("model", "heading_level_jump"),
    ("model", "source_heading_level_normalized"),
})

# Findings whose defect the normalization already resolved in the rendered
# model.  They stay reported and repairable, so the review can align the source
# model with what the template renders, and they do not fail the model audit:
# the delivered document carries the level the outline allows either way.  The
# model audit is the only producer, and the code alone identifies them because
# the publication gate and the CLI diagnostic echo see the issues without an
# audit name.
ADVISORY_AUDIT_CODES = frozenset({
    "source_heading_level_normalized",
})


def is_advisory_audit_issue(issue: Mapping[str, Any]) -> bool:
    """Return whether an audit issue reports a divergence normalization resolved."""

    return str(issue.get("code") or "") in ADVISORY_AUDIT_CODES


def collect_audit_findings(
    audits: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize native audit issues into the review finding contract."""

    findings: list[dict[str, Any]] = []
    for audit_name, audit in audits.items():
        if not isinstance(audit, Mapping):
            continue
        for index, raw_issue in enumerate(audit.get("issues") or []):
            issue = dict(raw_issue) if isinstance(raw_issue, Mapping) else {"message": str(raw_issue)}
            code = str(issue.get("code") or issue.get("type") or "audit_issue")
            block_id = issue.get("block_id") or issue.get("node_id") or issue.get("target_id")
            severity = issue.get("severity")
            if severity not in {"critical", "high", "medium", "low"}:
                severity = "critical" if audit_name == "saved_output" else "high"
            repairable = bool(
                block_id
                and (
                    issue.get("repairable") is True
                    or (audit_name, code) in _REPAIRABLE_AUDIT_CODES
                )
            )
            identity = str(block_id) if block_id else str(index)
            finding: dict[str, Any] = {
                "audit_id": str(issue.get("audit_id") or f"{audit_name}:{code}:{identity}"),
                "severity": severity,
                "category": audit_name,
                "repairable": repairable,
                "evidence": issue,
            }
            if block_id:
                finding["block_id"] = str(block_id)
            for field in ("allowed_operations", "property_scope"):
                if isinstance(issue.get(field), list):
                    finding[field] = list(issue[field])
            if isinstance(issue.get("allowed_values"), Mapping):
                finding["allowed_values"] = {
                    str(name): list(values)
                    for name, values in issue["allowed_values"].items()
                    if isinstance(values, list)
                }
            findings.append(finding)
    return findings


_LIST_NUMBERING_FORMATS = frozenset({
    "decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman",
})
_BULLET_NUMBERING_FORMATS = frozenset({"bullet", "none"})

_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD_NS = {"w": _WORD_NAMESPACE}


def _list_kind_for_num_fmt(num_fmt: str | None) -> str | None:
    if num_fmt is None:
        return None
    if num_fmt in _LIST_NUMBERING_FORMATS:
        return "ordered"
    if num_fmt in _BULLET_NUMBERING_FORMATS:
        return "unordered"
    return None


def _int_attribute(element: Any, name: str, default: int | None) -> int | None:
    if element is None:
        return default
    try:
        return int(element.get(f"{{{_WORD_NAMESPACE}}}{name}"))
    except (TypeError, ValueError):
        return default


def _paragraph_text(element: Any) -> str:
    """Return the paragraph's visible text, mirroring the inline projection.

    The readback compares this against ``_inline_plain_text`` (the AST inline
    projection), so a soft break must render as ``"\\n"`` and a tab as ``"\\t"``
    exactly as the inline projection does.  Rendering only ``w:t`` would drop
    the break and make an ordinary list continuation mismatch.
    """

    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{{{_WORD_NAMESPACE}}}t":
            parts.append(node.text or "")
        elif node.tag in {f"{{{_WORD_NAMESPACE}}}br", f"{{{_WORD_NAMESPACE}}}cr"}:
            parts.append("\n")
        elif node.tag == f"{{{_WORD_NAMESPACE}}}tab":
            parts.append("\t")
    return "".join(parts)


def _inline_plain_text(items: list[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for item in items:
        kind = item.get("kind")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "break":
            parts.append("\n")
        elif kind == "tab":
            parts.append("\t")
        elif kind == "link":
            parts.append(_inline_plain_text(list(item.get("children") or [])))
    return "".join(parts)


def _node_visible_text(document: Mapping[str, Any], node_id: str) -> str:
    block = (document.get("blocks") or {}).get(node_id)
    if not isinstance(block, Mapping):
        return ""
    return _inline_plain_text(list(block.get("inline_content") or []))


def _item_own_text(document: Mapping[str, Any], item_id: str) -> str:
    """Return the visible text of a list item's own leading paragraph block."""

    for child_id in (document.get("children") or {}).get(item_id, []):
        block = (document.get("blocks") or {}).get(child_id)
        if isinstance(block, Mapping) and block.get("block_type") == "paragraph":
            return _node_visible_text(document, child_id)
    return ""


def _expected_list_tree(
    normalized: Mapping[str, Any],
    formats: Mapping[int, Mapping[int, str]],
) -> list[dict[str, Any]]:
    """Return the AST list tree in reading order with clamped render levels.

    Each entry carries the ``(list_kind, numId, level)`` the renderer must write
    plus ``parent_index``: the index of the item that owns this item's list
    container, set only when this item's emitted level expresses the nesting
    (``level == owner level + 1``).  A nesting the emitted level cannot express
    (a clamped level, or a single-level instance under the two-level model)
    expects no parent.

    Under the T08 two-level model every list item emits ``w:ilvl 0``, so the
    emitted level carries no nesting.  ``render_level`` (the projected level
    before the instance clamp) and ``style_role`` are carried too: the saved
    document expresses the level through the style role, so the readback audit
    re-derives the parent chain from the role rather than from a ``w:ilvl``
    chain that no longer carries information.
    """

    document = normalized.get("source_ast", {}).get("document", {})
    presentation = normalized.get("presentation", {})
    nodes = presentation.get("nodes", {}) if isinstance(presentation, Mapping) else {}
    profile = presentation.get("profile", {}) if isinstance(presentation, Mapping) else {}
    numbering_ids = profile.get("numbering_ids", {}) if isinstance(profile, Mapping) else {}
    children = document.get("children", {})
    items: list[dict[str, Any]] = []
    owners: list[tuple[int, int, int]] = []

    def walk(node_id: str) -> None:
        for child_id in children.get(node_id, []):
            node = nodes.get(child_id)
            if isinstance(node, Mapping):
                kind = str(node.get("marker_kind") or "ordered")
                try:
                    raw_level = max(0, int(node.get("target_level") or 0))
                except (TypeError, ValueError):
                    raw_level = 0
                style_role = node.get("style_role")
                num_id = numbering_ids.get(str(style_role)) if isinstance(numbering_ids, Mapping) and style_role else None
                # A per-chapter restart binds the item to a concrete numbering
                # instance the normalizer assigned, which is a synthesized
                # ``w:num`` for a chapter after the first.
                instance_num_id = node.get("numbering_instance")
                if isinstance(instance_num_id, int) and not isinstance(instance_num_id, bool):
                    num_id = instance_num_id
                level = _clamp_expected_level(raw_level, num_id, formats)
                owner_index = owners[-1][0] if owners else None
                owner_level = owners[-1][1] if owners else None
                parent_index = (
                    owner_index
                    if owner_index is not None and owner_level is not None and level == owner_level + 1
                    else None
                )
                render_owner_level = owners[-1][2] if owners else None
                render_parent_index = (
                    owner_index
                    if owner_index is not None and render_owner_level is not None and raw_level == render_owner_level + 1
                    else None
                )
                index = len(items)
                items.append(
                    {
                        "index": index,
                        "kind": kind,
                        "num_id": num_id,
                        "level": level,
                        "parent_index": parent_index,
                        "render_level": raw_level,
                        "render_parent_index": render_parent_index,
                        "style_role": str(style_role) if style_role else None,
                        "text": _item_own_text(document, child_id),
                    }
                )
                owners.append((index, level, raw_level))
                walk(child_id)
                owners.pop()
            else:
                walk(child_id)

    walk(document.get("root_id", "root"))
    return items


def _level_parent_chain(levels: list[int | None]) -> list[int | None]:
    """Return the implicit parent index of each level in reading order.

    The saved DOCX encodes nesting only through ``w:ilvl``, so the parent of an
    item is the nearest preceding item one level shallower; a level-0 item has
    no parent.  A paragraph with a direct ``w:numPr`` that omits ``w:ilvl``
    carries no level to chain on, so it contributes no parent and resets the
    open chain.
    """

    chain: list[int | None] = []
    last_at_level: dict[int, int] = {}
    for index, level in enumerate(levels):
        if not isinstance(level, int):
            chain.append(None)
            last_at_level.clear()
            continue
        parent = last_at_level.get(level - 1) if level > 0 else None
        chain.append(parent)
        for stale in [key for key in last_at_level if key >= level]:
            last_at_level.pop(stale, None)
        last_at_level[level] = index
    return chain



def _numbering_format_map(numbering_xml: bytes) -> dict[int, dict[int, str]]:
    """Return ``{numId: {ilvl: numFmt}}`` parsed from the saved numbering part."""

    result: dict[int, dict[int, str]] = {}
    if not numbering_xml:
        return result
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    root = ElementTree.fromstring(numbering_xml)
    abstract_levels: dict[str, dict[int, str]] = {}
    for abstract in root.findall("w:abstractNum", ns):
        abstract_id = abstract.get(f"{{{namespace}}}abstractNumId")
        levels: dict[int, str] = {}
        for level in abstract.findall("w:lvl", ns):
            try:
                ilvl = int(level.get(f"{{{namespace}}}ilvl", "0"))
            except (TypeError, ValueError):
                ilvl = 0
            num_fmt = level.find("w:numFmt", ns)
            if num_fmt is not None:
                levels[ilvl] = num_fmt.get(f"{{{namespace}}}val") or ""
        abstract_levels[str(abstract_id)] = levels
    for num in root.findall("w:num", ns):
        try:
            num_id = int(num.get(f"{{{namespace}}}numId"))
        except (TypeError, ValueError):
            continue
        reference = num.find("w:abstractNumId", ns)
        levels = (
            dict(abstract_levels.get(reference.get(f"{{{namespace}}}val"), {}))
            if reference is not None
            else {}
        )
        for override in num.findall("w:lvlOverride", ns):
            try:
                ilvl = int(override.get(f"{{{namespace}}}ilvl", "0"))
            except (TypeError, ValueError):
                ilvl = 0
            override_level = override.find("w:lvl/w:numFmt", ns)
            if override_level is not None:
                levels[ilvl] = override_level.get(f"{{{namespace}}}val") or ""
        result[num_id] = levels
    return result


def _read_saved_list_parts(
    path: Path | str,
) -> tuple[bytes | None, bytes, bytes, dict[int, dict[int, str]], dict[str, Any] | None]:
    output = Path(path)
    try:
        with ZipFile(output) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                return None, b"", b"", {}, {"code": "list_readback_missing_parts"}
            document_xml = archive.read("word/document.xml")
            numbering_xml = archive.read("word/numbering.xml") if "word/numbering.xml" in names else b""
            styles_xml = archive.read("word/styles.xml") if "word/styles.xml" in names else b""
    except (OSError, BadZipFile) as exc:
        return None, b"", b"", {}, {"code": "list_readback_invalid_docx", "message": str(exc)}
    return document_xml, numbering_xml, styles_xml, _numbering_format_map(numbering_xml), None


def _style_name_index(styles_xml: bytes) -> dict[str, str]:
    """Return ``{styleId: style_name}`` from a saved styles part."""

    if not styles_xml:
        return {}
    root = ElementTree.fromstring(styles_xml)
    index: dict[str, str] = {}
    for style in root.findall("w:style", _WORD_NS):
        style_id = style.get(f"{{{_WORD_NAMESPACE}}}styleId")
        name = style.find("w:name", _WORD_NS)
        if style_id and name is not None:
            index[style_id] = name.get(f"{{{_WORD_NAMESPACE}}}val") or ""
    return index


def _role_by_style_name(normalized: Mapping[str, Any]) -> dict[str, str]:
    """Invert the presentation ``resolved_styles`` map to ``{style_name: role}``."""

    presentation = normalized.get("presentation", {})
    profile = presentation.get("profile", {}) if isinstance(presentation, Mapping) else {}
    resolved = profile.get("resolved_styles", {}) if isinstance(profile, Mapping) else {}
    roles: dict[str, str] = {}
    if isinstance(resolved, Mapping):
        for role, name in resolved.items():
            if name and str(name) not in roles:
                roles[str(name)] = str(role)
    return roles


def _saved_list_rows(
    document_xml: bytes,
    formats: Mapping[int, Mapping[int, str]],
    style_names: Mapping[str, str],
    role_by_style: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Read every direct-numbered paragraph's kind, instance and style role.

    Under the two-level model the emitted ``w:ilvl`` is always 0, so the saved
    style role is the carrier of the rendered level; it is read here so the
    audit can recover the parent chain from the style instead of the level.
    """

    document_root = ElementTree.fromstring(document_xml)
    # A list paragraph inside a table cell legitimately carries the table's own
    # style (表正文) rather than a list role, so it is marked here and the
    # render-level chain skips it instead of treating it as a style tamper.
    table_paragraph_ids = {
        id(paragraph)
        for table in document_root.iter(f"{{{_WORD_NAMESPACE}}}tbl")
        for paragraph in table.iter(f"{{{_WORD_NAMESPACE}}}p")
    }
    rows: list[dict[str, Any]] = []
    for paragraph in document_root.findall(".//w:p", _WORD_NS):
        num_pr = paragraph.find("w:pPr/w:numPr", _WORD_NS)
        if num_pr is None:
            continue
        num_id_element = num_pr.find("w:numId", _WORD_NS)
        ilvl_element = num_pr.find("w:ilvl", _WORD_NS)
        try:
            num_id = int(num_id_element.get(f"{{{_WORD_NAMESPACE}}}val")) if num_id_element is not None else None
        except (TypeError, ValueError):
            num_id = None
        if num_id is None or num_id == 0:
            continue
        try:
            ilvl = int(ilvl_element.get(f"{{{_WORD_NAMESPACE}}}val")) if ilvl_element is not None else 0
        except (TypeError, ValueError):
            ilvl = 0
        levels = formats.get(num_id, {})
        kind = _list_kind_for_num_fmt(levels.get(ilvl) if isinstance(levels, Mapping) else None)
        style = paragraph.find("w:pPr/w:pStyle", _WORD_NS)
        style_id = style.get(f"{{{_WORD_NAMESPACE}}}val") if style is not None else None
        style_name = style_names.get(style_id, style_id)
        role = role_by_style.get(style_name) if style_name else None
        rows.append(
            {
                "text": _paragraph_text(paragraph),
                "num_id": num_id,
                "ilvl": ilvl,
                "kind": kind,
                "style_id": style_id,
                "style_name": style_name,
                "style_role": role,
                "in_table": id(paragraph) in table_paragraph_ids,
            }
        )
    return rows


def _saved_list_items(
    document_xml: bytes,
    formats: Mapping[int, Mapping[int, str]],
) -> list[tuple[str | None, int | None, int]]:
    """Read (list_kind, numId, ilvl) from every saved paragraph's direct numPr."""

    return [
        (row["kind"], row["num_id"], row["ilvl"])
        for row in _saved_list_rows(document_xml, formats, {}, {})
    ]


def _saved_number_sequence(
    document_xml: bytes,
    numbering_xml: bytes,
) -> tuple[list[int | None], list[int | None]]:
    """Simulate the saved visible counter value of every direct-numbered paragraph.

    Returns ``(actual, expected)`` aligned by ``(numId, ilvl)`` group in first
    appearance order.  ``actual`` is the counter value the saved artifact shows;
    ``expected`` is the contiguous sequence the level's own ``w:start`` implies
    (ignoring a ``w:startOverride``, whose only legitimate use is a per-list
    restart the renderer does not emit).  A ``w:startOverride`` or a ``w:lvlRestart``
    that skips or resets a counter therefore diverges from ``expected``.
    """

    if not numbering_xml or not document_xml:
        return [], []
    numbering_root = ElementTree.fromstring(numbering_xml)
    abstract_levels: dict[str, dict[int, dict[str, int | None]]] = {}
    for abstract in numbering_root.findall("w:abstractNum", _WORD_NS):
        abstract_id = abstract.get(f"{{{_WORD_NAMESPACE}}}abstractNumId")
        levels: dict[int, dict[str, int | None]] = {}
        for level in abstract.findall("w:lvl", _WORD_NS):
            ilvl = _int_attribute(level, "ilvl", 0) or 0
            levels[ilvl] = {
                "start": _int_attribute(level.find("w:start", _WORD_NS), "val", 1),
                "restart": _int_attribute(level.find("w:lvlRestart", _WORD_NS), "val", None),
            }
        abstract_levels[str(abstract_id)] = levels
    num_to_abstract: dict[str, str] = {}
    overrides: dict[tuple[int, int], int] = {}
    for num in numbering_root.findall("w:num", _WORD_NS):
        try:
            num_id = int(num.get(f"{{{_WORD_NAMESPACE}}}numId"))
        except (TypeError, ValueError):
            continue
        reference = num.find("w:abstractNumId", _WORD_NS)
        if reference is not None:
            num_to_abstract[str(num_id)] = reference.get(f"{{{_WORD_NAMESPACE}}}val") or ""
        for override in num.findall("w:lvlOverride", _WORD_NS):
            ilvl = _int_attribute(override, "ilvl", 0) or 0
            start_override = override.find("w:startOverride", _WORD_NS)
            if start_override is not None:
                overrides[(num_id, ilvl)] = _int_attribute(start_override, "val", 1) or 1
    document_root = ElementTree.fromstring(document_xml)
    counters: dict[tuple[int, int], int] = {}
    groups: dict[tuple[int, int], list[int | None]] = {}
    group_order: list[tuple[int, int]] = []
    for paragraph in document_root.findall(".//w:p", _WORD_NS):
        num_pr = paragraph.find("w:pPr/w:numPr", _WORD_NS)
        if num_pr is None:
            continue
        try:
            num_id = int(num_pr.find("w:numId", _WORD_NS).get(f"{{{_WORD_NAMESPACE}}}val"))
        except (AttributeError, TypeError, ValueError):
            continue
        if num_id == 0:
            continue
        ilvl = _int_attribute(num_pr.find("w:ilvl", _WORD_NS), "val", 0) or 0
        levels = abstract_levels.get(num_to_abstract.get(str(num_id), ""), {})
        if ilvl not in levels:
            key = (num_id, ilvl)
            groups.setdefault(key, []).append(None)
            if key not in group_order:
                group_order.append(key)
            continue
        for deeper, deeper_level in levels.items():
            if deeper <= ilvl:
                continue
            trigger = deeper_level["restart"] if deeper_level["restart"] is not None else deeper
            if trigger == 0 or trigger != ilvl + 1:
                continue
            deeper_start = overrides.get((num_id, deeper)) or deeper_level["start"] or 1
            counters[(num_id, deeper)] = deeper_start - 1
        start = overrides.get((num_id, ilvl)) or levels[ilvl]["start"] or 1
        counters[(num_id, ilvl)] = counters.get((num_id, ilvl), start - 1) + 1
        key = (num_id, ilvl)
        groups.setdefault(key, []).append(counters[(num_id, ilvl)])
        if key not in group_order:
            group_order.append(key)
    actual: list[int | None] = []
    expected: list[int | None] = []
    for key in group_order:
        values = groups[key]
        if any(value is None for value in values):
            actual.extend(values)
            expected.extend(values)
            continue
        num_id, ilvl = key
        start = abstract_levels.get(num_to_abstract.get(str(num_id), ""), {}).get(ilvl, {}).get("start", 1) or 1
        actual.extend(values)
        expected.extend(range(start, start + len(values)))
    return actual, expected


def _clamp_expected_level(level: int, num_id: int | None, formats: Mapping[int, Mapping[int, str]]) -> int:
    if num_id is None:
        return level
    levels = formats.get(num_id)
    if levels and level not in levels:
        return 0
    return level


def _semantic_rebuild_invariants(
    rebuild: Mapping[str, Any],
    facts: Mapping[str, Any] | None = None,
) -> tuple[dict[str, bool], list[dict[str, Any]]]:
    """Evaluate the four semantic invariants on a rebuilt readback list tree.

    ``rebuild`` is the ``analyze_lists`` result over the re-parsed output
    document, so it speaks the same semantic vocabulary as the source pass.
    ``facts`` is the re-parsed SourceFacts; it is what lets the low-confidence
    invariant cross-check the recorded downgrades against the evidence instead
    of trusting either side alone.

    Scope of each check, and what it cannot catch:

    * ``deep_items_have_parent`` / ``continuations_have_owner`` read the rebuilt
      output tree only; they cannot see a source block the renderer dropped.
    * ``low_confidence_has_diagnostics`` checks the re-parsed output: every
      ``low_confidence_list_marker`` downgrade must cite a node the re-parsed
      evidence has, and every re-parsed evidence node with an ``ambiguous``
      numbering status must be decided or recorded.  The source pass's own
      low-confidence set is not re-derivable from the saved DOCX, so this guards
      the output-side record consistency, not the source decision.
    * ``no_silent_ambiguity`` checks that every unresolved candidate left a
      matching ``ambiguous_list_state`` record and that the status did not report
      ``completed``; it does not re-arbitrate the conflict.
    * A duplicated or dropped non-list block is not a list-tree defect and is
      caught by ``check_saved_output.paragraph_order_changed``, not here.
    """

    issues: list[dict[str, Any]] = []
    items = list(rebuild.get("items") or [])
    deep_items_have_parent = all(
        item.get("parent_item_id") is not None
        for item in items
        if isinstance(item.get("semantic_level"), int) and item.get("semantic_level") > 0
    )
    if not deep_items_have_parent:
        missing = [
            str(item.get("item_id"))
            for item in items
            if isinstance(item.get("semantic_level"), int)
            and item.get("semantic_level") > 0
            and item.get("parent_item_id") is None
        ]
        issues.append({"code": "list_deep_item_without_parent", "items": missing})

    continuations = list(rebuild.get("continuations") or [])
    continuations_have_owner = all(entry.get("owner_item_id") for entry in continuations)
    if not continuations_have_owner:
        issues.append(
            {
                "code": "list_continuation_without_owner",
                "items": [str(entry.get("node_id")) for entry in continuations if not entry.get("owner_item_id")],
            }
        )

    # Invariant 6 (low-confidence decisions are indexable).  Two independently
    # falsifiable directions:
    #   (a) every ``low_confidence_list_marker`` downgrade the rebuild emits must
    #       cite a node the re-parsed evidence actually has, so a record cannot
    #       reference a phantom node;
    #   (b) every re-parsed evidence node whose numbering status is "ambiguous"
    #       must be decided as a list item or recorded in a diagnostic / downgrade,
    #       so an unresolved low-confidence decision cannot be silent.
    # Direction (b) is empty when the parser reads the saved output as a decided
    # list, which is the intended production shape; direction (a) still checks the
    # records against the evidence on every run.
    evidence_ids: set[str] = set()
    low_confidence_nodes: set[str] = set()
    if isinstance(facts, Mapping):
        for node_id, evidence in (facts.get("evidence") or {}).items():
            evidence_ids.add(str(node_id))
            if isinstance(evidence, Mapping) and evidence.get("numbering_status") == "ambiguous":
                low_confidence_nodes.add(str(node_id))
    recorded_nodes: set[str] = set()
    for entry in list(rebuild.get("diagnostics") or []) + list(rebuild.get("downgrades") or []):
        if isinstance(entry, Mapping) and entry.get("node_id") is not None:
            recorded_nodes.add(str(entry["node_id"]))
    decided_nodes = {str(item.get("item_id")) for item in items}
    undiagnosed = sorted(low_confidence_nodes - recorded_nodes - decided_nodes)
    phantom_records = sorted(
        str(entry.get("node_id"))
        for entry in (rebuild.get("downgrades") or [])
        if isinstance(entry, Mapping)
        and entry.get("code") == "low_confidence_list_marker"
        and entry.get("node_id") is not None
        and evidence_ids
        and str(entry.get("node_id")) not in evidence_ids
    )
    low_confidence_has_diagnostics = not undiagnosed and not phantom_records
    if not low_confidence_has_diagnostics:
        issues.append(
            {
                "code": "list_low_confidence_without_diagnostic",
                "undocumented": undiagnosed,
                "phantom_records": phantom_records,
            }
        )

    # Invariant 7 (no silent ambiguity).  ``analyze_lists`` always sets the status
    # to "unresolved" and records an ``ambiguous_list_state`` diagnostic for each
    # unresolved candidate, so ``not (unresolved and completed)`` alone is
    # unreachable.  The real check is that every unresolved candidate left a
    # matching recorded ambiguity and that the status did not report "completed".
    unresolved = list(rebuild.get("state_unresolved") or [])
    recorded_ambiguity = [
        tuple(sorted(str(candidate_id) for candidate_id in entry.get("candidate_ids") or []))
        for entry in (rebuild.get("diagnostics") or [])
        if isinstance(entry, Mapping) and entry.get("code") == "ambiguous_list_state"
    ]
    remaining_records = list(recorded_ambiguity)
    unrecorded_ambiguity: list[Any] = []
    for entry in unresolved:
        key = tuple(sorted(str(candidate_id) for candidate_id in entry.get("candidate_ids") or []))
        if key in remaining_records:
            remaining_records.remove(key)
        else:
            unrecorded_ambiguity.append(entry)
    no_silent_ambiguity = not unresolved or (
        not unrecorded_ambiguity and rebuild.get("status") != "completed"
    )
    if not no_silent_ambiguity:
        issues.append(
            {
                "code": "list_silent_ambiguity",
                "unresolved_count": len(unresolved),
                "unrecorded_count": len(unrecorded_ambiguity),
                "status": rebuild.get("status"),
            }
        )

    invariants = {
        "deep_items_have_parent": deep_items_have_parent,
        "continuations_have_owner": continuations_have_owner,
        "low_confidence_has_diagnostics": low_confidence_has_diagnostics,
        "no_silent_ambiguity": no_silent_ambiguity,
    }
    return invariants, issues


def _rebuild_readback_lists(
    path: Path | str,
) -> tuple[dict[str, Any] | None, Mapping[str, Any] | None, dict[str, Any] | None]:
    """Re-parse the saved DOCX with the SourceFacts pipeline and rebuild the list tree.

    Returns ``(result, facts, error)``.  A rebuild failure is reported as a
    non-blocking ``list_readback_rebuild_skipped`` info record so an output the
    parser cannot read back still completes: it is not a list defect the author
    can act on.

    The saved artifact is always a DOCX written to a staging path whose name
    (``.<name>.native-v2.staging``) has no ``.docx`` suffix, so the DOCX
    container is passed to ``parse`` as an explicit ``source_type`` hint.  A
    suffix-based dispatch would take the Markdown branch and read the zip as
    UTF-8, which fails and silently skips every semantic invariant.
    """

    try:
        from .parsers import parse
        from .semantics.lists import analyze_lists

        facts = parse(Path(path), {"source_store": None, "source_type": "docx"})
        return analyze_lists(facts), facts, None
    except Exception as exc:  # pragma: no cover - defensive, output-specific
        return None, None, {
            "code": "list_readback_rebuild_skipped",
            "severity": "info",
            "message": f"the saved output could not be re-parsed for the list readback: {exc}",
        }


def _expected_render_levels_and_chain(
    expected_tree: list[dict[str, Any]],
) -> tuple[list[int], list[int | None]]:
    levels = [int(item.get("render_level") or 0) for item in expected_tree]
    return levels, _level_parent_chain(levels)


def _actual_render_levels(
    expected_tree: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> tuple[list[int | None], bool, list[str]]:
    """Recover each saved item's render level from its style role.

    Returns ``(levels, available, unmapped_styles)``.  The role-to-level map is
    built from the AST projection and is only usable when the role identifies the
    level uniquely (the product two-level model gives ``list_letter`` level 0 and
    ``list_decimal``/``list_bullet`` level 1).  When a role appears at more than
    one projected level (the ``legacy`` multi-level policy reuses
    ``list_decimal`` for levels 1 and 2) the style no longer identifies the level
    and the chain is unavailable for that legitimate reason.  ``available`` is
    ``False`` only for those projection-level reasons.

    A saved list paragraph whose style does not map back to any projected role is
    handled per row instead of dropping the chain: its level is ``None`` and the
    caller compares only the mapped rows.  When such a row is outside any table
    it is a tampered or otherwise unexpected ``pStyle`` and is returned in
    ``unmapped_styles`` so the caller reports it as its own issue.  A row inside a
    table cell is not reported, because the renderer writes the table's own style
    there by design; its level is simply not recoverable from the style.
    """

    role_levels: dict[str, set[int]] = {}
    for item in expected_tree:
        role = item.get("style_role")
        if role is None:
            # An expected item without a projected role cannot be matched by
            # style, so the style-derived chain is unavailable as a whole.
            return [], False, []
        role_levels.setdefault(str(role), set()).add(int(item.get("render_level") or 0))
    if any(len(levels) > 1 for levels in role_levels.values()):
        return [], False, []
    role_level = {role: next(iter(levels)) for role, levels in role_levels.items()}
    levels: list[int | None] = []
    unmapped_styles: list[str] = []
    for row in rows:
        role = row.get("style_role")
        level = role_level.get(str(role)) if role is not None else None
        if level is None and not row.get("in_table"):
            unmapped_styles.append(str(row.get("style_name") or row.get("style_id") or "<no style>"))
        levels.append(level)
    return levels, True, unmapped_styles


def audit_list_readback(
    path: Path | str,
    normalized: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-parse the saved DOCX and compare its list tree with the semantic list.

    Every list paragraph's direct ``w:numPr/w:numId`` and ``w:numPr/w:ilvl`` is
    read from the saved artifact and compared, in reading order, against the
    ``(list_kind, numId, ilvl)`` the semantic list and the template numbering ids
    require.  A tampered level, a tampered or remapped numbering id, or a
    removed ``numPr`` therefore diverges from the expectation and is reported;
    the AST list tree and the saved output cannot drift apart silently.

    The README section-9 invariants are checked explicitly and exposed under
    ``invariants``.  Each row is scoped to what the code actually verifies:

    * ``list_items_preserved_once`` -- the saved list paragraphs' texts match the
      AST list items' texts, one each, in reading order.  This is a subset
      invariant: it covers list-item paragraphs only.  A duplicated non-list
      block is caught one layer up by
      ``check_saved_output.paragraph_order_changed``, which compares every saved
      paragraph text against the render map;
    * ``deep_items_have_parent`` -- a rebuilt readback item at a level deeper
      than zero has a ``parent_item_id``;
    * ``continuations_have_owner`` -- every rebuilt readback continuation has an
      ``owner_item_id``;
    * ``numbering_sequence_agrees`` -- the simulated visible counter of each
      ``(numId, ilvl)`` group is the contiguous sequence its ``w:start`` implies,
      so a ``w:startOverride`` or a restart cannot silently renumber a level;
    * ``ast_readback_tree_agrees`` -- the render-level parent chain recovered
      from the saved style roles matches the AST projection, in addition to the
      flat ``(kind, numId, ilvl)`` reading order.  A saved list paragraph whose
      style does not map back to a projected role fails this check with its own
      ``list_render_style_unmapped`` issue instead of disabling the chain; the
      chain is skipped only when the role genuinely cannot identify the level
      (a role reused across projected levels, or an expected item without a
      projected role);
    * ``low_confidence_has_diagnostics`` -- every ``low_confidence_list_marker``
      downgrade in the rebuild cites a node the re-parsed evidence has, and every
      re-parsed evidence node with an ambiguous numbering status is decided or
      recorded;
    * ``no_silent_ambiguity`` -- every unresolved candidate left a matching
      ``ambiguous_list_state`` record and the status did not report
      ``completed``.

    When the saved output cannot be re-parsed the four semantic invariants
    (``deep_items_have_parent``, ``continuations_have_owner``,
    ``low_confidence_has_diagnostics``, ``no_silent_ambiguity``) are reported as
    ``None`` rather than ``True``, and ``semantic_rebuild_checked`` is ``False``,
    so a consumer can tell "checked and passed" from "skipped".

    The emitted-level parent chain (``expected_parent_chain`` /
    ``actual_parent_chain``) is still compared for the policies that emit real
    levels; under the two-level model it is all-``None`` on both sides and the
    render-level chain carries the information instead.
    """

    document_xml, numbering_xml, styles_xml, formats, parse_error = _read_saved_list_parts(path)
    expected_tree = _expected_list_tree(normalized, formats)
    expected_effective = [
        (item["kind"], item["num_id"], item["level"]) for item in expected_tree
    ]
    expected_levels, expected_render_chain = _expected_render_levels_and_chain(expected_tree)
    # Nothing could be checked when the saved parts are unreadable, so every
    # invariant is reported as None (skipped) instead of True (passed).
    unreadable_invariants: dict[str, bool | None] = {
        "list_items_preserved_once": None,
        "deep_items_have_parent": None,
        "continuations_have_owner": None,
        "numbering_sequence_agrees": None,
        "ast_readback_tree_agrees": None,
        "low_confidence_has_diagnostics": None,
        "no_silent_ambiguity": None,
    }
    if parse_error is not None or document_xml is None:
        return {
            "passed": False,
            "issues": [parse_error or {"code": "list_readback_missing_parts"}],
            "expected": expected_effective,
            "actual": [],
            "expected_parent_chain": [item["parent_index"] for item in expected_tree],
            "actual_parent_chain": [],
            "expected_render_levels": expected_levels,
            "actual_render_levels": [],
            "expected_render_parent_chain": expected_render_chain,
            "actual_render_parent_chain": [],
            "invariants": unreadable_invariants,
            "semantic_rebuild_checked": False,
            "rebuild": None,
        }
    role_by_style = _role_by_style_name(normalized)
    style_names = _style_name_index(styles_xml)
    rows = _saved_list_rows(document_xml, formats, style_names, role_by_style)
    actual = [(row["kind"], row["num_id"], row["ilvl"]) for row in rows]
    issues: list[dict[str, Any]] = []

    # 1. Flat reading order: the numbering the AST requires.
    if expected_effective != actual:
        issues.append({"code": "list_readback_mismatch", "expected": expected_effective, "actual": actual})

    # 2. Emitted-level parent chain, still meaningful for real-level policies.
    expected_parents = [item["parent_index"] for item in expected_tree]
    actual_parents = _level_parent_chain([level for _, _, level in actual])
    if len(expected_parents) == len(actual_parents) and expected_parents != actual_parents:
        issues.append({"code": "list_parent_chain_mismatch", "expected": expected_parents, "actual": actual_parents})

    # 3. Render-level parent chain recovered from the saved style role.  The
    # comparison degrades per row: a row whose style carries no projected role
    # contributes no level and is skipped, so one such row (a table-cell list
    # item, whose cell style is the table's own) cannot turn the whole chain off.
    # A non-table row with no mapped role is reported as its own issue, so a
    # permitted style change cannot hide a level tamper either.
    actual_levels, style_levels_available, unmapped_styles = _actual_render_levels(expected_tree, rows)
    actual_render_chain = _level_parent_chain(actual_levels) if style_levels_available else []
    render_tree_agrees = True
    if unmapped_styles:
        render_tree_agrees = False
        issues.append(
            {
                "code": "list_render_style_unmapped",
                "styles": sorted({name for name in unmapped_styles if name}),
                "actual_render_levels": actual_levels,
                "expected_render_levels": expected_levels,
            }
        )
    elif style_levels_available and len(actual_levels) == len(expected_levels):
        mapped_indices = [index for index, level in enumerate(actual_levels) if level is not None]
        actual_mapped = [actual_levels[index] for index in mapped_indices]
        expected_mapped = [expected_levels[index] for index in mapped_indices]
        render_tree_agrees = (
            actual_mapped == expected_mapped
            and _level_parent_chain(actual_mapped) == _level_parent_chain(expected_mapped)
        )
        if not render_tree_agrees:
            issues.append(
                {
                    "code": "list_render_level_mismatch",
                    "expected": expected_levels,
                    "actual": actual_levels,
                    "expected_parent_chain": expected_render_chain,
                    "actual_parent_chain": _level_parent_chain(actual_levels),
                }
            )

    # 4. Per-level sequence values.
    sequence_actual, sequence_expected = _saved_number_sequence(document_xml, numbering_xml)
    numbering_sequence_agrees = sequence_actual == sequence_expected and len(sequence_actual) == len(sequence_expected)
    if not numbering_sequence_agrees:
        issues.append(
            {
                "code": "list_numbering_sequence_mismatch",
                "expected": sequence_expected,
                "actual": sequence_actual,
            }
        )

    # 5. Every list item preserved exactly once, in reading order.  This is a
    # subset invariant (list-item paragraphs only); a duplicated non-list block
    # is caught by ``check_saved_output.paragraph_order_changed``.
    expected_texts = [str(item.get("text") or "") for item in expected_tree]
    actual_texts = [str(row.get("text") or "") for row in rows]
    list_items_preserved_once = expected_texts == actual_texts
    if not list_items_preserved_once:
        issues.append(
            {
                "code": "list_item_preservation_mismatch",
                "expected": expected_texts,
                "actual": actual_texts,
            }
        )

    # 6. Semantic invariants on the tree rebuilt with the SourceFacts pipeline.
    # A rebuild failure is defensive and non-blocking: it is surfaced on its own
    # key instead of the blocking issue list, because an output the parser
    # cannot read back is not a list defect the author can act on.  The four
    # semantic invariants stay None (skipped) rather than True (passed) when the
    # rebuild did not run.
    rebuild, rebuild_facts, rebuild_error = _rebuild_readback_lists(path)
    semantic_invariants: dict[str, bool | None] = {
        "deep_items_have_parent": None,
        "continuations_have_owner": None,
        "low_confidence_has_diagnostics": None,
        "no_silent_ambiguity": None,
    }
    if rebuild is not None:
        semantic_invariants, rebuild_issues = _semantic_rebuild_invariants(rebuild, rebuild_facts)
        issues.extend(rebuild_issues)

    invariants = {
        "list_items_preserved_once": list_items_preserved_once,
        "deep_items_have_parent": semantic_invariants["deep_items_have_parent"],
        "continuations_have_owner": semantic_invariants["continuations_have_owner"],
        "numbering_sequence_agrees": numbering_sequence_agrees,
        "ast_readback_tree_agrees": render_tree_agrees,
        "low_confidence_has_diagnostics": semantic_invariants["low_confidence_has_diagnostics"],
        "no_silent_ambiguity": semantic_invariants["no_silent_ambiguity"],
    }
    return {
        "passed": not issues,
        "issues": issues,
        "expected": expected_effective,
        "actual": actual,
        "expected_parent_chain": expected_parents,
        "actual_parent_chain": actual_parents,
        "expected_render_levels": expected_levels,
        "actual_render_levels": actual_levels,
        "expected_render_parent_chain": expected_render_chain,
        "actual_render_parent_chain": actual_render_chain,
        "sequence_values": sequence_actual,
        "invariants": invariants,
        "semantic_rebuild_checked": rebuild is not None,
        "rebuild": rebuild_error,
    }


def audit_bundle(
    path: Path | str,
    normalized: Mapping[str, Any],
    render_map: Mapping[str, Any],
    *,
    appendix_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the complete native audit set and expose normalized findings."""

    audits = {
        "model": check_model(normalized),
        "saved_output": check_saved_output(path, normalized, render_map),
        "list_readback": audit_list_readback(path, normalized),
        "appendix": dict(appendix_audit or {"passed": True, "issues": []}),
    }
    findings = collect_audit_findings(audits)
    return {
        **audits,
        "findings": findings,
        "passed": all(audit.get("passed") is True for audit in audits.values()),
    }


def check_model(normalized: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        validate_ast(normalized["source_ast"])
    except Exception as exc:
        issues.append({"code": "ast_invalid", "message": str(exc)})
    plan = build_render_plan(normalized)
    blocks = normalized.get("source_ast", {}).get("document", {}).get("blocks", {})
    previous_heading_level = 0
    for operation in plan["operations"]:
        node_id = str(operation.get("node_id") or "")
        block = blocks.get(node_id) if isinstance(blocks, Mapping) else None
        if not isinstance(block, Mapping) or not is_body_outline_heading(block):
            continue
        # The outline contract governs what the template renders, so the check
        # reads the presentation level normalization derived and falls back to
        # the source semantic level for a block without a presentation record.
        level = effective_outline_level(block, operation.get("presentation"))
        if level is None:
            continue
        expected_level = min(6, previous_heading_level + 1)
        if level > expected_level:
            issues.append({
                "code": "heading_level_jump",
                "node_id": node_id,
                "severity": "high",
                "expected_level": expected_level,
                "actual_level": level,
            })
        source_level = block.get("level")
        if isinstance(source_level, int) and not isinstance(source_level, bool) and source_level != level:
            # The source declared a level the outline does not allow and the
            # normalization rendered it at the level it does allow, so the
            # delivered document satisfies the contract.  Report the divergence
            # so a reader sees the source declaration, and let the review align
            # the source model with the rendered level; the finding disappears
            # once it does.
            issues.append({
                "code": "source_heading_level_normalized",
                "node_id": node_id,
                "severity": "high",
                "expected_level": level,
                "actual_level": source_level,
            })
        previous_heading_level = level
    presentation = normalized.get("presentation", {}).get("nodes", {})
    for node_id, node in presentation.items():
        if not node.get("marker_anchor"):
            issues.append({"code": "missing_marker_anchor", "node_id": node_id})
    listed = [item["node_id"] for item in plan["operations"] if item.get("kind") == "list_item"]
    if set(listed) != set(presentation):
        issues.append({"code": "render_plan_list_mismatch", "expected": sorted(presentation), "actual": sorted(listed)})
    blocking = [issue for issue in issues if not is_advisory_audit_issue(issue)]
    return {"passed": not blocking, "issues": issues, "render_plan": plan}


def _expected_paragraph_texts(expected: Mapping[str, Any], render_map: Mapping[str, Any]) -> list[str]:
    if expected.get("expected_texts"):
        return [str(value) for value in expected["expected_texts"]]
    return [str(item.get("text") or "") for item in render_map.values() if item.get("kind") == "paragraph"]


def _inline_signature(inline_content: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    signature: list[dict[str, Any]] = []
    for item in inline_content:
        kind = item.get("kind")
        if kind == "text":
            signature.append({"kind": "text", "text": str(item.get("text") or "")})
        elif kind in {"break", "tab", "image"}:
            signature.append({"kind": str(kind)})
        elif kind == "link":
            signature.append(
                {
                    "kind": "link",
                    "target": str(item.get("target") or ""),
                    "children": _inline_signature(list(item.get("children") or [])),
                }
            )
    return signature


def _xml_inline_signature(element: Any, hyperlink_targets: Mapping[str, str]) -> list[dict[str, Any]]:
    signature: list[dict[str, Any]] = []
    text_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    tab_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tab"
    break_tags = {
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br",
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}cr",
    }
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name in {"pPr", "rPr"}:
            continue
        if local_name == "hyperlink":
            relation_id = child.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = hyperlink_targets.get(relation_id or "")
            if target is None:
                anchor = child.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}anchor")
                target = f"#{anchor}" if anchor else ""
            signature.append(
                {
                    "kind": "link",
                    "target": target,
                    "children": _xml_inline_signature(child, hyperlink_targets),
                }
            )
            continue
        if local_name == "r":
            for run_child in list(child):
                if run_child.tag == text_tag:
                    signature.append({"kind": "text", "text": run_child.text or ""})
                elif run_child.tag == tab_tag:
                    signature.append({"kind": "tab"})
                elif run_child.tag in break_tags:
                    signature.append({"kind": "break"})
                elif any(node.tag.rsplit("}", 1)[-1] == "blip" for node in run_child.iter()):
                    blip_count = sum(1 for node in run_child.iter() if node.tag.rsplit("}", 1)[-1] == "blip")
                    signature.extend({"kind": "image"} for _ in range(blip_count))
            continue
        signature.extend(_xml_inline_signature(child, hyperlink_targets))
    return signature


def _has_inline_contract(inline_content: list[Mapping[str, Any]]) -> bool:
    return any(item.get("kind") in {"image", "link", "break", "tab"} for item in inline_content)


def check_saved_output(path: Path | str, expected: Mapping[str, Any], render_map: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    unexpected_styles: list[str] = []
    output = Path(path)
    styles_xml = None
    numbering_xml = None
    document_xml = None
    relationships_xml = None
    try:
        with ZipFile(output) as archive:
            bad = archive.testzip()
            if bad:
                issues.append({"code": "zip_corrupt", "part": bad})
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                issues.append({"code": "missing_document_xml"})
            if "word/styles.xml" in names:
                styles_xml = archive.read("word/styles.xml")
            if "word/numbering.xml" in names:
                numbering_xml = archive.read("word/numbering.xml")
            if "word/document.xml" in names:
                document_xml = archive.read("word/document.xml")
            if "word/_rels/document.xml.rels" in names:
                relationships_xml = archive.read("word/_rels/document.xml.rels")
    except (OSError, BadZipFile) as exc:
        return {
            "passed": False,
            "issues": [{"code": "invalid_docx", "message": str(exc)}],
            "unexpected_styles": unexpected_styles,
        }

    try:
        document = Document(output)
    except Exception as exc:
        return {
            "passed": False,
            "issues": [{"code": "readback_failed", "message": str(exc)}],
            "unexpected_styles": unexpected_styles,
        }
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    xml_paragraphs: list[Any] = []
    table_count = 0

    def collect_xml(element: Any) -> None:
        nonlocal table_count
        for child in list(element):
            if child.tag == f"{{{namespace}}}p":
                xml_paragraphs.append(child)
            elif child.tag == f"{{{namespace}}}tbl":
                table_count += 1
                collect_xml(child)
            elif child.tag == f"{{{namespace}}}sdt":
                continue
            else:
                collect_xml(child)

    collect_xml(document.element.body)
    def paragraph_text(paragraph: Any) -> str:
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{{{namespace}}}t":
                parts.append(node.text or "")
            elif node.tag in {f"{{{namespace}}}br", f"{{{namespace}}}cr"}:
                parts.append("\n")
            elif node.tag == f"{{{namespace}}}tab":
                parts.append("\t")
        return "".join(parts)

    actual_texts = [
        text
        for paragraph in xml_paragraphs
        if (text := paragraph_text(paragraph))
        and (
            "\t" in text
            or any(node.text for node in paragraph.findall(".//w:t", ns))
            or any(
                node.tag in {f"{{{namespace}}}br", f"{{{namespace}}}cr"}
                and node.get(f"{{{namespace}}}type") != "page"
                for node in paragraph.iter()
            )
        )
    ]
    expected_texts = [text for text in _expected_paragraph_texts(expected, render_map) if text]
    if actual_texts != expected_texts:
        issues.append({"code": "paragraph_order_changed", "expected": expected_texts, "actual": actual_texts})
    hyperlink_targets: dict[str, str] = {}
    if relationships_xml is not None:
        relationships_root = ElementTree.fromstring(relationships_xml)
        hyperlink_targets = {
            relationship.get("Id"): relationship.get("Target") or ""
            for relationship in relationships_root
            if relationship.get("Type", "").endswith("/hyperlink")
        }
    used_paragraph_indexes: set[int] = set()
    for node_id, item in render_map.items():
        if item.get("kind") != "paragraph" or not _has_inline_contract(item.get("inline_content", [])):
            continue
        expected_signature = _inline_signature(item.get("inline_content", []))
        expected_text = str(item.get("text") or "")
        if not expected_text:
            continue
        candidate = next(
            (
                (index, paragraph)
                for index, paragraph in enumerate(xml_paragraphs)
                if index not in used_paragraph_indexes and paragraph_text(paragraph) == expected_text
            ),
            None,
        )
        if candidate is None:
            issues.append({"code": "inline_paragraph_missing", "node_id": node_id, "expected_text": expected_text})
            continue
        index, paragraph = candidate
        used_paragraph_indexes.add(index)
        actual_signature = _xml_inline_signature(paragraph, hyperlink_targets)
        if actual_signature != expected_signature:
            issues.append(
                {
                    "code": "inline_content_changed",
                    "node_id": node_id,
                    "expected": expected_signature,
                    "actual": actual_signature,
                }
            )
    expected_tables = expected.get("expected_table_count")
    if expected_tables is None:
        expected_tables = sum(1 for item in render_map.values() if item.get("kind") == "table")
    if table_count != expected_tables:
        issues.append({"code": "table_count_changed", "expected": expected_tables, "actual": table_count})
    expected_image_hashes: list[tuple[str, str]] = []
    expected_image_targets: list[str | None] = []
    expected_media_parts: set[str] | None = None
    artifact_metadata = render_map.get("__artifact__")
    if isinstance(artifact_metadata, Mapping) and artifact_metadata.get("media_parts") is not None:
        expected_media_parts = set(artifact_metadata["media_parts"])
    source_resources = expected.get("source_ast", {}).get("document", {}).get("resources", {})
    if isinstance(source_resources, Mapping):
        for item in render_map.values():
            for resource_id in item.get("image_resources", []):
                resource = source_resources.get(resource_id)
                if isinstance(resource, Mapping) and resource.get("content_sha256"):
                    expected_image_hashes.append((str(resource_id), str(resource["content_sha256"])))
                else:
                    issues.append({"code": "image_resource_missing", "resource_id": resource_id})
            if item.get("image_resources"):
                expected_image_targets.extend(item.get("image_targets", []))
                if item.get("media_parts") is not None and expected_media_parts is None:
                    expected_media_parts = set(item["media_parts"])
    if expected_image_hashes and (document_xml is None or relationships_xml is None):
        issues.append({"code": "image_relationships_missing"})
    if document_xml is not None and relationships_xml is not None:
        document_root = ElementTree.fromstring(document_xml)
        drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
        rel_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        relationship_root = ElementTree.fromstring(relationships_xml)
        relationships = {
            relationship.get("Id"): relationship.get("Target")
            for relationship in relationship_root
            if relationship.get("Type", "").endswith("/image")
        }
        actual_image_hashes: list[tuple[str | None, str | None, str | None]] = []
        with ZipFile(output) as archive:
            names = set(archive.namelist())
            for blip in document_root.findall(f".//{{{drawing_namespace}}}blip"):
                relationship_id = blip.get(f"{{{rel_namespace}}}embed")
                target = relationships.get(relationship_id)
                package_path = posixpath.normpath(posixpath.join("word", target.lstrip("/"))) if target else None
                if package_path is None or package_path not in names:
                    actual_image_hashes.append((relationship_id, package_path, None))
                    issues.append({"code": "image_relationship_missing", "relationship_id": relationship_id, "target": target})
                    continue
                actual_image_hashes.append((relationship_id, package_path, "sha256:" + hashlib.sha256(archive.read(package_path)).hexdigest()))
        if len(actual_image_hashes) != len(expected_image_hashes):
            issues.append({"code": "image_count_changed", "expected": len(expected_image_hashes), "actual": len(actual_image_hashes)})
        if expected_media_parts is not None:
            actual_media_parts = {name for name in names if name.startswith("word/media/")}
            if actual_media_parts != expected_media_parts:
                issues.append({"code": "image_media_parts_changed", "expected": sorted(expected_media_parts), "actual": sorted(actual_media_parts)})
        for index, ((resource_id, expected_hash), actual) in enumerate(zip(expected_image_hashes, actual_image_hashes)):
            _, actual_target, actual_hash = actual
            if actual_hash != expected_hash:
                issues.append({"code": "image_resource_mismatch", "index": index, "resource_id": resource_id, "expected": expected_hash, "actual": actual_hash})
            if index < len(expected_image_targets) and expected_image_targets[index] != actual_target:
                issues.append({"code": "image_relationship_target_mismatch", "index": index, "resource_id": resource_id, "expected": expected_image_targets[index], "actual": actual_target})
    expected_numbered_items = [item for item in render_map.values() if item.get("numbered")]
    expected_numbered_texts = [str(item.get("text") or "") for item in expected_numbered_items if str(item.get("text") or "")]
    expected_numbered_styles = {str(item.get("style") or "") for item in expected_numbered_items if item.get("style")}
    expected_profile = expected.get("presentation", {}).get("profile", {})
    expected_numbering_ids = expected_profile.get("numbering_ids", {}) if isinstance(expected_profile, Mapping) else {}
    style_names: dict[str, str] = {}
    style_num_info: dict[str, tuple[int | None, int]] = {}
    if styles_xml is not None:
        styles_root = ElementTree.fromstring(styles_xml)
        for style in styles_root.findall("w:style", ns):
            style_id = style.get(f"{{{namespace}}}styleId")
            name = style.find("w:name", ns)
            if style_id and name is not None:
                style_names[style_id] = name.get(f"{{{namespace}}}val") or ""
            num_pr = style.find("w:pPr/w:numPr", ns)
            if style_id and num_pr is not None:
                num_id = num_pr.find("w:numId", ns)
                ilvl = num_pr.find("w:ilvl", ns)
                try:
                    parsed_num_id = int(num_id.get(f"{{{namespace}}}val")) if num_id is not None else None
                except (TypeError, ValueError):
                    parsed_num_id = None
                try:
                    parsed_ilvl = int(ilvl.get(f"{{{namespace}}}val")) if ilvl is not None else 0
                except (TypeError, ValueError):
                    parsed_ilvl = 0
                style_num_info[style_id] = (parsed_num_id, parsed_ilvl)
    resolved_styles = expected_profile.get("resolved_styles", {}) if isinstance(expected_profile, Mapping) else {}
    allowed_styles = {"Normal"}
    if isinstance(resolved_styles, Mapping):
        allowed_styles.update(str(name) for name in resolved_styles.values() if name)
    toc_contract = expected.get("presentation", {}).get("template_contract", {}).get("toc", {})
    if isinstance(toc_contract, Mapping):
        try:
            max_toc_level = int(toc_contract.get("max_level") or 0)
        except (TypeError, ValueError):
            max_toc_level = 0
        allowed_styles.update(f"TOC {level}" for level in range(1, max_toc_level + 1))
    allowed_styles_casefold = {name.casefold() for name in allowed_styles}
    if document_xml is not None:
        document_root_for_styles = ElementTree.fromstring(document_xml)
        used_styles = set()
        for paragraph in document_root_for_styles.findall(".//w:p", ns):
            style = paragraph.find("w:pPr/w:pStyle", ns)
            style_id = style.get(f"{{{namespace}}}val") if style is not None else None
            used_styles.add(style_names.get(style_id, style_id) if style_id else "Normal")
        unexpected_styles = sorted(
            (name for name in used_styles if name and name.casefold() not in allowed_styles_casefold),
            key=str.casefold,
        )
        if unexpected_styles:
            issues.append({"code": "unexpected_styles", "styles": unexpected_styles})
    expected_numbered_style_ids = {style_id for style_id, name in style_names.items() if name in expected_numbered_styles}
    expected_style_ids_by_name = {name: style_id for style_id, name in style_names.items() if name in expected_numbered_styles}
    valid_num_fmts = {"decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman", "bullet", "none"}
    numbering_num_to_abstract: dict[int, int] = {}
    numbering_levels: dict[int, dict[int, str]] = {}
    numbering_overrides: dict[int, dict[int, str]] = {}
    if numbering_xml is not None:
        numbering_root = ElementTree.fromstring(numbering_xml)
        for abstract in numbering_root.findall("w:abstractNum", ns):
            try:
                abstract_id = int(abstract.get(f"{{{namespace}}}abstractNumId"))
            except (TypeError, ValueError):
                continue
            levels: dict[int, str] = {}
            for level in abstract.findall("w:lvl", ns):
                try:
                    ilvl = int(level.get(f"{{{namespace}}}ilvl", "0"))
                except (TypeError, ValueError):
                    ilvl = 0
                num_fmt = level.find("w:numFmt", ns)
                if num_fmt is not None:
                    levels[ilvl] = num_fmt.get(f"{{{namespace}}}val") or ""
            numbering_levels[abstract_id] = levels
        for num in numbering_root.findall("w:num", ns):
            try:
                num_id = int(num.get(f"{{{namespace}}}numId"))
                abstract = num.find("w:abstractNumId", ns)
                if abstract is None:
                    continue
                numbering_num_to_abstract[num_id] = int(abstract.get(f"{{{namespace}}}val"))
            except (TypeError, ValueError):
                continue
            overrides: dict[int, str] = {}
            for override in num.findall("w:lvlOverride", ns):
                try:
                    ilvl = int(override.get(f"{{{namespace}}}ilvl", "0"))
                except (TypeError, ValueError):
                    ilvl = 0
                override_level = override.find("w:lvl/w:numFmt", ns)
                if override_level is not None:
                    overrides[ilvl] = override_level.get(f"{{{namespace}}}val") or ""
            if overrides:
                numbering_overrides[num_id] = overrides
    numbered_paragraphs: list[Any] = []
    actual_numbering: list[dict[str, Any]] = []
    invalid_numbering: list[dict[str, Any]] = []
    for paragraph in xml_paragraphs:
        direct = paragraph.find("w:pPr/w:numPr", ns)
        direct_num_id = direct.find("w:numId", ns) if direct is not None else None
        direct_ilvl = direct.find("w:ilvl", ns) if direct is not None else None
        style = paragraph.find("w:pPr/w:pStyle", ns)
        style_id = style.get(f"{{{namespace}}}val") if style is not None else None
        text = paragraph_text(paragraph)
        direct_numbering = direct_num_id is not None and direct_num_id.get(f"{{{namespace}}}val") not in {None, "0"}
        expected_numbering = style_id in expected_numbered_style_ids or (direct_numbering and text in expected_numbered_texts)
        if expected_numbering:
            numbered_paragraphs.append(paragraph)
            style_num_id, style_ilvl = style_num_info.get(style_id, (None, 0))
            try:
                num_id = int(direct_num_id.get(f"{{{namespace}}}val")) if direct_num_id is not None else style_num_id
            except (TypeError, ValueError):
                num_id = None
            try:
                ilvl = int(direct_ilvl.get(f"{{{namespace}}}val")) if direct_ilvl is not None else style_ilvl
            except (TypeError, ValueError):
                ilvl = style_ilvl
            abstract_id = numbering_num_to_abstract.get(num_id) if num_id is not None else None
            num_fmt = numbering_overrides.get(num_id, {}).get(ilvl) if num_id is not None else None
            if num_fmt is None and abstract_id is not None:
                num_fmt = numbering_levels.get(abstract_id, {}).get(ilvl)
            if num_id is None or abstract_id is None or num_fmt not in valid_num_fmts:
                invalid_numbering.append({"text": text, "num_id": num_id, "ilvl": ilvl, "abstract_num_id": abstract_id, "num_fmt": num_fmt})
            actual_numbering.append({"text": text, "num_id": num_id, "ilvl": ilvl, "abstract_num_id": abstract_id, "num_fmt": num_fmt})
    actual_numbered_texts = [
        paragraph_text(paragraph)
        for paragraph in numbered_paragraphs
        if paragraph_text(paragraph)
    ]
    if len(numbered_paragraphs) != len(expected_numbered_items) or actual_numbered_texts != expected_numbered_texts:
        issues.append(
            {
                "code": "list_numbering_missing",
                "expected_count": len(expected_numbered_items),
                "actual_count": len(numbered_paragraphs),
                "expected_texts": expected_numbered_texts,
                "actual_texts": actual_numbered_texts,
            }
        )
    if invalid_numbering:
        issues.append({"code": "list_numbering_definition_invalid", "items": invalid_numbering})
    mapping_mismatches: list[dict[str, Any]] = []
    for index, expected_item in enumerate(expected_numbered_items):
        if index >= len(actual_numbering):
            break
        numbering = expected_item.get("numbering") or {}
        expected_kind = numbering.get("marker_kind")
        # The renderer writes a direct ``w:numPr`` with the emitted level, so a
        # multi-level ordered render is expected at the render map's level.  The
        # style-derived level is only a fallback for a numbered block that the
        # renderer carries without a direct numbering record.
        direct_level = numbering.get("num_ilvl")
        direct_num_id = numbering.get("num_id")
        expected_style_id = expected_style_ids_by_name.get(str(expected_item.get("style") or ""))
        if isinstance(direct_level, int):
            expected_level = direct_level
        else:
            expected_level = numbering.get("target_level")
            if expected_style_id in style_num_info:
                expected_level = style_num_info[expected_style_id][1]
        expected_fmt = None
        if expected_kind == "unordered":
            expected_fmt = "none" if numbering.get("style_role") == "list_dash" else "bullet"
        elif expected_kind == "ordered":
            # The ordered style role owns the number format: under the two-level
            # model the first-level letter instance and the second-level decimal
            # instance both emit ``w:ilvl 0``, so the emitted level cannot select
            # the format and the role does.
            expected_fmt = "lowerLetter" if numbering.get("style_role") == "list_letter" else "decimal"
        if isinstance(direct_num_id, int):
            expected_num_id = direct_num_id
        else:
            expected_num_id = expected_numbering_ids.get(numbering.get("style_role")) if isinstance(expected_numbering_ids, Mapping) else None
        expected_abstract_id = expected_numbering_ids.get(f"{numbering.get('style_role')}_abstract") if isinstance(expected_numbering_ids, Mapping) else None
        actual = actual_numbering[index]
        if expected_fmt is not None and actual.get("num_fmt") != expected_fmt:
            mapping_mismatches.append({"text": actual.get("text"), "field": "num_fmt", "expected": expected_fmt, "actual": actual.get("num_fmt")})
        if isinstance(expected_level, int) and actual.get("ilvl") != expected_level:
            mapping_mismatches.append({"text": actual.get("text"), "field": "ilvl", "expected": expected_level, "actual": actual.get("ilvl")})
        if isinstance(expected_num_id, int) and actual.get("num_id") != expected_num_id:
            mapping_mismatches.append({"text": actual.get("text"), "field": "num_id", "expected": expected_num_id, "actual": actual.get("num_id")})
        if isinstance(expected_abstract_id, int) and actual.get("abstract_num_id") != expected_abstract_id:
            mapping_mismatches.append({"text": actual.get("text"), "field": "abstract_num_id", "expected": expected_abstract_id, "actual": actual.get("abstract_num_id")})
    if mapping_mismatches:
        issues.append({"code": "list_numbering_mapping_mismatch", "items": mapping_mismatches})
    return {
        "passed": not issues,
        "issues": issues,
        "paragraph_texts": actual_texts,
        "table_count": table_count,
        "unexpected_styles": unexpected_styles,
    }
