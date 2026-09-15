"""Independent model and saved-artifact audits for the modular renderer."""

from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from docx import Document

from .render_plan import build_render_plan
from .validation import validate_ast


_REPAIRABLE_AUDIT_CODES = frozenset({
    ("model", "heading_level_jump"),
})


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


def _list_kind_for_num_fmt(num_fmt: str | None) -> str | None:
    if num_fmt is None:
        return None
    if num_fmt in _LIST_NUMBERING_FORMATS:
        return "ordered"
    if num_fmt in _BULLET_NUMBERING_FORMATS:
        return "unordered"
    return None


def _expected_list_tree(
    normalized: Mapping[str, Any],
    formats: Mapping[int, Mapping[int, str]],
) -> list[dict[str, Any]]:
    """Return the AST list tree in reading order with clamped render levels.

    Each entry carries the ``(list_kind, numId, level)`` the renderer must write
    plus ``parent_index``: the index of the item that owns this item's list
    container, set only when this item's render level expresses the nesting
    (``level == owner render level + 1``).  A nesting the render level cannot
    express (a clamped level) expects no parent, matching what the renderer can
    actually save.
    """

    document = normalized.get("source_ast", {}).get("document", {})
    presentation = normalized.get("presentation", {})
    nodes = presentation.get("nodes", {}) if isinstance(presentation, Mapping) else {}
    profile = presentation.get("profile", {}) if isinstance(presentation, Mapping) else {}
    numbering_ids = profile.get("numbering_ids", {}) if isinstance(profile, Mapping) else {}
    children = document.get("children", {})
    items: list[dict[str, Any]] = []
    owners: list[tuple[int, int]] = []

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
                level = _clamp_expected_level(raw_level, num_id, formats)
                owner_index = owners[-1][0] if owners else None
                owner_level = owners[-1][1] if owners else None
                parent_index = (
                    owner_index
                    if owner_index is not None and owner_level is not None and level == owner_level + 1
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
                    }
                )
                owners.append((index, level))
                walk(child_id)
                owners.pop()
            else:
                walk(child_id)

    walk(document.get("root_id", "root"))
    return items


def _level_parent_chain(levels: list[int]) -> list[int | None]:
    """Return the implicit parent index of each level in reading order.

    The saved DOCX encodes nesting only through ``w:ilvl``, so the parent of an
    item is the nearest preceding item one level shallower; a level-0 item has
    no parent.
    """

    chain: list[int | None] = []
    last_at_level: dict[int, int] = {}
    for index, level in enumerate(levels):
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
) -> tuple[bytes | None, dict[int, dict[int, str]], dict[str, Any] | None]:
    output = Path(path)
    try:
        with ZipFile(output) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                return None, {}, {"code": "list_readback_missing_parts"}
            document_xml = archive.read("word/document.xml")
            numbering_xml = archive.read("word/numbering.xml") if "word/numbering.xml" in names else b""
    except (OSError, BadZipFile) as exc:
        return None, {}, {"code": "list_readback_invalid_docx", "message": str(exc)}
    return document_xml, _numbering_format_map(numbering_xml), None


def _saved_list_items(
    document_xml: bytes,
    formats: Mapping[int, Mapping[int, str]],
) -> list[tuple[str | None, int | None, int]]:
    """Read (list_kind, numId, ilvl) from every saved paragraph's direct numPr."""

    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    document_root = ElementTree.fromstring(document_xml)
    items: list[tuple[str | None, int | None, int]] = []
    for paragraph in document_root.findall(".//w:p", ns):
        num_pr = paragraph.find("w:pPr/w:numPr", ns)
        if num_pr is None:
            continue
        num_id_element = num_pr.find("w:numId", ns)
        ilvl_element = num_pr.find("w:ilvl", ns)
        try:
            num_id = int(num_id_element.get(f"{{{namespace}}}val")) if num_id_element is not None else None
        except (TypeError, ValueError):
            num_id = None
        try:
            ilvl = int(ilvl_element.get(f"{{{namespace}}}val")) if ilvl_element is not None else 0
        except (TypeError, ValueError):
            ilvl = 0
        if num_id is None or num_id == 0:
            continue
        levels = formats.get(num_id, {})
        items.append((_list_kind_for_num_fmt(levels.get(ilvl) if isinstance(levels, Mapping) else None), num_id, ilvl))
    return items


def _clamp_expected_level(level: int, num_id: int | None, formats: Mapping[int, Mapping[int, str]]) -> int:
    if num_id is None:
        return level
    levels = formats.get(num_id)
    if levels and level not in levels:
        return 0
    return level


def audit_list_readback(
    path: Path | str,
    normalized: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-parse the saved DOCX and compare its list numbering with the semantic list.

    Every list paragraph's direct ``w:numPr/w:numId`` and ``w:numPr/w:ilvl`` is
    read from the saved artifact and compared, in reading order, against the
    ``(list_kind, numId, ilvl)`` the semantic list and the template numbering ids
    require.  A tampered level, a tampered or remapped numbering id, or a
    removed ``numPr`` therefore diverges from the expectation and is reported;
    the AST list tree and the saved output cannot drift apart silently.

    The parent chain is compared too: the saved ``ilvl`` values imply a parent
    for every item (the nearest preceding item one level shallower), and that
    implied parent must match the item that owns the item's list container in
    the AST.  A saved document whose parent chain drifted from the AST list tree
    is reported as ``list_parent_chain_mismatch`` in addition to the flat
    mismatch.
    """

    document_xml, formats, parse_error = _read_saved_list_parts(path)
    expected_tree = _expected_list_tree(normalized, formats)
    expected_effective = [
        (item["kind"], item["num_id"], item["level"]) for item in expected_tree
    ]
    if parse_error is not None or document_xml is None:
        return {"passed": False, "issues": [parse_error or {"code": "list_readback_missing_parts"}], "expected": expected_effective, "actual": []}
    actual = _saved_list_items(document_xml, formats)
    issues: list[dict[str, Any]] = []
    if expected_effective != actual:
        issues.append({"code": "list_readback_mismatch", "expected": expected_effective, "actual": actual})
    expected_parents = [item["parent_index"] for item in expected_tree]
    actual_parents = _level_parent_chain([level for _, _, level in actual])
    if len(expected_parents) == len(actual_parents) and expected_parents != actual_parents:
        issues.append({"code": "list_parent_chain_mismatch", "expected": expected_parents, "actual": actual_parents})
    return {
        "passed": not issues,
        "issues": issues,
        "expected": expected_effective,
        "actual": actual,
        "expected_parent_chain": expected_parents,
        "actual_parent_chain": actual_parents,
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
        if not isinstance(block, Mapping) or block.get("block_type") != "paragraph":
            continue
        if block.get("role") != "heading":
            continue
        if block.get("appendix_id") or block.get("appendix_role"):
            continue
        level = block.get("level")
        if not isinstance(level, int):
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
        previous_heading_level = level
    presentation = normalized.get("presentation", {}).get("nodes", {})
    for node_id, node in presentation.items():
        if not node.get("marker_anchor"):
            issues.append({"code": "missing_marker_anchor", "node_id": node_id})
    listed = [item["node_id"] for item in plan["operations"] if item.get("kind") == "list_item"]
    if set(listed) != set(presentation):
        issues.append({"code": "render_plan_list_mismatch", "expected": sorted(presentation), "actual": sorted(listed)})
    return {"passed": not issues, "issues": issues, "render_plan": plan}


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
