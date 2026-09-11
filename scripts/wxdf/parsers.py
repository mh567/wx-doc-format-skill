"""SourceFacts adapters for Markdown and DOCX inputs."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import SOURCE_FACTS_SCHEMA_VERSION
from .source_store import SourceHandle, SourceStore
from .validation import validate_source_facts


MARKDOWN_PARSER_VERSION = "1.1.0"


def _digest(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _empty_facts(handle: SourceHandle, parser_id: str, parser_config: str, *, parser_version: str = "1.0.0") -> dict[str, Any]:
    return {
        "facts_schema_version": SOURCE_FACTS_SCHEMA_VERSION,
        "source": {
            "source_id": handle.source_id,
            "source_type": handle.source_type,
            "content_sha256": handle.content_sha256,
            "encoding": handle.encoding,
        },
        "parser": {
            "id": parser_id,
            "version": parser_version,
            "dialect": parser_config,
            "config_sha256": _digest(parser_config),
        },
        "units": {},
        "containers": {"root": {"kind": "document"}},
        "children": {"root": []},
        "evidence": {},
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def _source_ref(handle: SourceHandle, part: str, *, start: int | None = None, end: int | None = None, node_path: list[str | int] | None = None, content: bytes | str | None = None, parser_version: str | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "source_id": handle.source_id,
        "part": part,
        "content_sha256": _digest(content if content is not None else handle.content_sha256),
    }
    if start is not None and end is not None:
        ref["span"] = {"start": start, "end": end, "unit": "unicode_code_point"}
    elif node_path is not None:
        ref["node_path"] = node_path
    else:
        ref["span"] = {"start": 0, "end": 0, "unit": "unicode_code_point"}
    if parser_version is not None:
        ref["parser_version"] = parser_version
    return ref


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _inline_nodes(tokens: Iterable[Any], handle: SourceHandle, part: str, resources: dict[str, Any], diagnostics: list[dict[str, Any]], source_ref: dict[str, Any], parser_version: str | None = None) -> list[dict[str, Any]]:
    tokens = list(tokens)

    def parse_range(start: int, stop_kind: str | None = None, marks: list[str] | None = None) -> tuple[list[dict[str, Any]], int]:
        result: list[dict[str, Any]] = []
        marks = list(marks or [])
        index = start
        while index < len(tokens):
            token = tokens[index]
            kind = getattr(token, "type", "")
            if stop_kind and kind == stop_kind:
                return result, index
            if kind == "text":
                result.append({"kind": "text", "text": token.content, "marks": list(marks)})
            elif kind == "code_inline":
                result.append({"kind": "text", "text": token.content, "marks": [*marks, "code"]})
            elif kind == "softbreak":
                result.append({"kind": "break"})
            elif kind == "hardbreak":
                result.append({"kind": "break"})
            elif kind in {"strong_open", "em_open"}:
                mark = "strong" if kind == "strong_open" else "emphasis"
                nested, index = parse_range(index + 1, "strong_close" if kind == "strong_open" else "em_close", [*marks, mark])
                result.extend(nested)
            elif kind in {"strong_close", "em_close"}:
                if stop_kind is None:
                    diagnostics.append({"code": "unbalanced_inline_mark", "severity": "warning", "message": kind})
                    index += 1
                    continue
                return result, index
            elif kind == "link_open":
                target = str(token.attrGet("href") or "")
                nested, index = parse_range(index + 1, "link_close", marks)
                result.append({"kind": "link", "target": target, "children": nested})
            elif kind == "image":
                target = str(token.attrGet("src") or "")
                alt_text = str(token.content or "")
                resource_id = _stable_id("res", handle.source_id, target)
                if resource_id not in resources:
                    resource_bytes: bytes | None = None
                    target_path = Path(target)
                    if not target_path.is_absolute():
                        target_path = handle.path.parent / target_path
                    try:
                        resource_bytes = target_path.read_bytes()
                    except OSError:
                        diagnostics.append({"code": "external_resource_unresolved", "severity": "warning", "message": target})
                    resource_content = resource_bytes if resource_bytes is not None else target
                    resources[resource_id] = {
                        "kind": "image",
                        "source_ref": _source_ref(handle, target, node_path=["inline", target], parser_version=parser_version),
                        "content_sha256": _digest(resource_content),
                        "dependencies": [],
                    }
                result.append({"kind": "image", "resource_id": resource_id, "alt_text": alt_text})
            elif kind in {"html_inline", "entity"}:
                result.append({"kind": "text", "text": token.content, "marks": list(marks)})
            else:
                diagnostics.append({"code": "unknown_inline_token", "severity": "warning", "message": kind})
                result.append({"kind": "opaque_inline", "source_ref": source_ref, "diagnostic": f"unsupported inline token: {kind}"})
            index += 1
        if stop_kind:
            diagnostics.append({"code": "unclosed_inline_container", "severity": "warning", "message": stop_kind})
        return result, index

    return parse_range(0)[0]


def _marker(line: str) -> tuple[str | None, int | None, int]:
    match = re.match(r"^(?P<indent>[ \t]*)(?P<marker>(?:\d+[.)]|[-+*]))[ \t]+", line)
    if not match:
        return None, None, 0
    marker = match.group("marker")
    if marker[0].isdigit():
        return "ordered", int(re.match(r"\d+", marker).group(0)), len(match.group("indent"))
    return "unordered", None, len(match.group("indent"))


@dataclass
class _MarkdownFactsBuilder:
    source_text: str
    handle: SourceHandle
    part: str

    def __post_init__(self) -> None:
        self.facts = _empty_facts(self.handle, "markdown-source-facts", "commonmark+table")
        self.lines = self.source_text.splitlines()
        self.offsets = _line_offsets(self.source_text)
        self.stack = ["root"]
        self.pending: tuple[str, Any] | None = None
        self.token_index = 0

    def _span(self, token: Any) -> tuple[int, int]:
        token_map = getattr(token, "map", None)
        if token_map is None:
            return 0, len(self.source_text)
        start_line, end_line = token_map
        start = self.offsets[min(start_line, len(self.offsets) - 1)]
        end = self.offsets[min(end_line, len(self.offsets) - 1)]
        if end_line >= len(self.offsets):
            end = len(self.source_text)
        return start, max(start, end)

    def _ref(self, token: Any) -> dict[str, Any]:
        start, end = self._span(token)
        return _source_ref(
            self.handle,
            self.part,
            start=start,
            end=end,
            content=self.source_text[start:end],
            parser_version=self.facts["parser"]["version"],
        )

    def _append(self, node_id: str) -> None:
        self.facts["children"].setdefault(self.stack[-1], []).append(node_id)

    def _new_container(self, kind: str, token: Any, **fields: Any) -> str:
        start, end = self._span(token)
        node_id = _stable_id("c", self.handle.source_id, kind, start, end, self.token_index)
        while node_id in self.facts["containers"]:
            self.token_index += 1
            node_id = _stable_id("c", self.handle.source_id, kind, start, end, self.token_index)
        self.facts["containers"][node_id] = {"kind": kind, **fields, "source_ref": self._ref(token)}
        self.facts["children"][node_id] = []
        self._append(node_id)
        return node_id

    def _new_unit(self, kind: str, token: Any, **fields: Any) -> str:
        start, end = self._span(token)
        node_id = _stable_id("u", self.handle.source_id, kind, start, end, self.token_index)
        while node_id in self.facts["units"]:
            self.token_index += 1
            node_id = _stable_id("u", self.handle.source_id, kind, start, end, self.token_index)
        self.facts["units"][node_id] = {"kind": kind, "source_ref": self._ref(token), **fields}
        self._append(node_id)
        return node_id

    def _record_list_evidence(self, node_id: str, token: Any) -> None:
        start_line = (getattr(token, "map", None) or [0])[0]
        line = self.lines[start_line] if start_line < len(self.lines) else ""
        marker_kind, ordinal, indent = _marker(line)
        if marker_kind is None:
            return
        self.facts["evidence"][node_id] = {
            "marker_kind": marker_kind,
            "marker_text": line[indent:].split(None, 1)[0] if line[indent:].split(None, 1) else None,
            "ordinal": ordinal,
            "explicit_depth": indent,
            "indent_value": indent,
            "indent_unit": "unicode_code_point",
            "numbering_definition_ref": None,
            "restart_hint": ordinal == 1 if ordinal is not None else None,
            "blank_before": start_line == 0 or not self.lines[start_line - 1].strip(),
            "continuation_hint": False,
        }

    def consume(self, tokens: Iterable[Any]) -> dict[str, Any]:
        from markdown_it import MarkdownIt

        for token in tokens:
            self.token_index += 1
            token_type = token.type
            if token_type in {"ordered_list_open", "bullet_list_open"}:
                start = 1
                if token_type == "ordered_list_open":
                    attrs = dict(token.attrs or [])
                    try:
                        start = int(attrs.get("start", 1))
                    except (TypeError, ValueError):
                        start = 1
                container_id = self._new_container(
                    "list",
                    token,
                    list_kind="ordered" if token_type == "ordered_list_open" else "unordered",
                    source_start=start,
                    source_sequence_id=_stable_id("seq", self.handle.source_id, self.token_index),
                )
                self.facts["evidence"][container_id] = {"marker_kind": "ordered" if token_type == "ordered_list_open" else "unordered", "source_token": token_type}
                self.stack.append(container_id)
            elif token_type == "list_item_open":
                item_id = self._new_container(
                    "list_item",
                    token,
                    source_marker="item",
                    source_ordinal=1,
                    source_sequence_id=_stable_id("seq", self.handle.source_id, self.stack[-1]),
                )
                self._record_list_evidence(item_id, token)
                evidence = self.facts["evidence"].get(item_id, {})
                self.facts["containers"][item_id]["source_marker"] = evidence.get("marker_text") or "item"
                self.facts["containers"][item_id]["source_ordinal"] = evidence.get("ordinal") or 1
                self.stack.append(item_id)
            elif token_type == "blockquote_open":
                self.stack.append(self._new_container("quote", token))
            elif token_type == "table_open":
                table_id = self._new_container("table", token)
                self.facts["evidence"][table_id] = {
                    "line_start": (getattr(token, "map", None) or [0])[0] + 1,
                    "source_token": token_type,
                }
                self.stack.append(table_id)
            elif token_type == "tr_open":
                table_id = self.stack[-1]
                row_index = len(self.facts["children"].get(table_id, []))
                self.stack.append(self._new_container("table_row", token, row=row_index))
            elif token_type in {"th_open", "td_open"}:
                row_id = self.stack[-1]
                row = self.facts["containers"][row_id]
                row_index = int(row["row"])
                col_index = len(self.facts["children"].get(row_id, []))
                self.stack.append(
                    self._new_container(
                        "table_cell",
                        token,
                        row=row_index,
                        col=col_index,
                        row_span=1,
                        col_span=1,
                        cell_role="header" if token_type == "th_open" else "body",
                    )
                )
                self.pending = ("body", None)
            elif token_type in {"ordered_list_close", "bullet_list_close", "list_item_close", "blockquote_close", "table_close", "tr_close", "th_close", "td_close"}:
                if len(self.stack) > 1:
                    self.stack.pop()
            elif token_type in {"heading_open", "paragraph_open"}:
                role = "heading" if token_type == "heading_open" else "body"
                level = int(token.tag[1:]) if role == "heading" and token.tag[1:].isdigit() else None
                self.pending = (role, level)
            elif token_type == "inline" and self.pending is not None:
                role, level = self.pending
                ref = self._ref(token)
                inline_content = _inline_nodes(
                    token.children or [],
                    self.handle,
                    self.part,
                    self.facts["resources"],
                    self.facts["diagnostics"],
                    ref,
                    self.facts["parser"]["version"],
                )
                fields: dict[str, Any] = {"role": role, "inline_content": inline_content}
                if level is not None:
                    fields["level"] = level
                unit_id = self._new_unit("paragraph", token, **fields)
                self.facts["evidence"][unit_id] = {"line_start": (getattr(token, "map", None) or [0])[0] + 1, "source_token": "inline", "blank_before": False}
                self.pending = None
            elif token_type in {"fence", "code_block"}:
                unit_id = self._new_unit("code_block", token, raw_text=token.content, language=(token.info or "").strip() or None)
                self.facts["evidence"][unit_id] = {"source_token": token_type}
            elif token_type == "hr":
                self._new_unit("separator", token, raw_text="")
            elif token_type == "html_block":
                unit_id = self._new_unit("opaque", token, raw_text=token.content, diagnostic_ids=["unsupported_html_block"])
                self.facts["evidence"][unit_id] = {"source_token": token_type}
                self.facts["diagnostics"].append({"code": "unsupported_html_block", "severity": "warning", "message": "HTML block is retained as opaque source content"})
            elif token.map is not None and token_type not in {"thead_open", "thead_close", "tbody_open", "tbody_close", "paragraph_close", "heading_close", "table_close"}:
                unit_id = self._new_unit("opaque", token, raw_text=token.content or "", diagnostic_ids=[f"unsupported_{token_type}"])
                self.facts["diagnostics"].append({"code": f"unsupported_{token_type}", "severity": "warning", "message": f"token {token_type} retained as opaque source content"})
        return self.facts


def _parse_markdown(source: Path | str, *, store: SourceStore, text: str | None = None) -> dict[str, Any]:
    from markdown_it import MarkdownIt

    handle = store.open_source(source, text=text)
    source_text = text if text is not None else store.resolve({"source_id": handle.source_id, "part": handle.path.name})
    if isinstance(source_text, bytes):
        source_text = source_text.decode(handle.encoding or "utf-8")
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    builder = _MarkdownFactsBuilder(str(source_text), handle, handle.path.name)
    builder.facts["parser"]["version"] = MARKDOWN_PARSER_VERSION
    facts = builder.consume(parser.parse(str(source_text)))
    validate_source_facts(facts)
    return facts


def _docx_inline_content(paragraph: Any, handle: SourceHandle, node_path: list[str | int], resources: dict[str, Any], parser_version: str) -> list[dict[str, Any]]:
    from docx.oxml.ns import qn

    content: list[dict[str, Any]] = []
    image_tags = {qn("a:blip"), "{urn:schemas-microsoft-com:vml}imagedata"}

    def run_marks(run: Any) -> list[str]:
        marks: list[str] = []
        properties = run.find(qn("w:rPr"))
        if properties is None:
            return marks
        if properties.find(qn("w:b")) is not None:
            marks.append("strong")
        if properties.find(qn("w:i")) is not None:
            marks.append("emphasis")
        return marks

    def append_image(blip: Any, path: list[str | int], target: list[dict[str, Any]]) -> None:
        relationship_id = blip.get(qn("r:embed")) or blip.get(qn("r:id"))
        if not relationship_id:
            return
        resource_id = _stable_id("res", handle.source_id, relationship_id)
        if resource_id not in resources:
            part = paragraph.part.related_parts.get(relationship_id)
            blob = getattr(part, "blob", b"")
            media_part = str(getattr(part, "partname", "word/media/unknown")).lstrip("/")
            resources[resource_id] = {
                "kind": "image",
                "source_ref": _source_ref(handle, media_part, node_path=[*path, relationship_id], parser_version=parser_version),
                "content_sha256": _digest(blob),
                "dependencies": [],
            }
        target.append({"kind": "image", "resource_id": resource_id, "alt_text": ""})

    def parse_children(parent: Any, target: list[dict[str, Any]], path: list[str | int]) -> None:
        for index, child in enumerate(list(parent)):
            tag = child.tag
            child_path = [*path, index]
            if tag == qn("w:r"):
                marks = run_marks(child)
                for run_child in list(child):
                    run_tag = run_child.tag
                    if run_tag == qn("w:rPr"):
                        continue
                    if run_tag == qn("w:t"):
                        target.append({"kind": "text", "text": run_child.text or "", "marks": list(marks)})
                    elif run_tag == qn("w:tab"):
                        target.append({"kind": "tab"})
                    elif run_tag in {qn("w:br"), qn("w:cr")}:
                        target.append({"kind": "break"})
                    elif run_tag in {qn("w:drawing"), qn("w:pict")} or any(
                        element.tag in image_tags for element in run_child.iter()
                    ):
                        for image_ref in run_child.iter():
                            if image_ref.tag in image_tags:
                                append_image(image_ref, child_path, target)
            elif tag == qn("w:hyperlink"):
                relationship_id = child.get(qn("r:id"))
                relationship = paragraph.part.rels.get(relationship_id) if relationship_id else None
                target_url = getattr(relationship, "target_ref", None) if relationship is not None else None
                if not target_url:
                    anchor = child.get(qn("w:anchor"))
                    target_url = f"#{anchor}" if anchor else None
                children: list[dict[str, Any]] = []
                parse_children(child, children, child_path)
                if target_url and children:
                    target.append({"kind": "link", "target": str(target_url), "children": children})
                else:
                    target.extend(children)
            elif tag != qn("w:pPr"):
                parse_children(child, target, child_path)

    parse_children(paragraph._p, content, node_path)
    return content


def _docx_source_paragraph_format(paragraph: Any) -> dict[str, Any]:
    """Capture the safe direct-format subset needed by layout table cells."""

    from docx.oxml.ns import qn

    properties = paragraph._p.pPr
    if properties is None:
        return {}
    result: dict[str, Any] = {}
    alignment = properties.find(qn("w:jc"))
    if alignment is not None and alignment.get(qn("w:val")) is not None:
        result["alignment"] = alignment.get(qn("w:val"))

    for contract_name, element_name, attributes in (
        (
            "spacing",
            "spacing",
            (
                "before", "beforeLines", "beforeAutospacing",
                "after", "afterLines", "afterAutospacing", "line", "lineRule",
            ),
        ),
        (
            "indentation",
            "ind",
            (
                "left", "leftChars", "right", "rightChars",
                "firstLine", "firstLineChars", "hanging", "hangingChars",
                "start", "startChars", "end", "endChars",
            ),
        ),
    ):
        element = properties.find(qn(f"w:{element_name}"))
        if element is None:
            continue
        captured = {
            attribute: element.get(qn(f"w:{attribute}"))
            for attribute in attributes
            if element.get(qn(f"w:{attribute}")) is not None
        }
        if captured:
            result[contract_name] = captured

    pagination = {}
    for element_name in ("keepNext", "keepLines", "pageBreakBefore", "widowControl"):
        element = properties.find(qn(f"w:{element_name}"))
        if element is not None:
            pagination[element_name] = element.get(qn("w:val"))
    if pagination:
        result["pagination"] = pagination
    return result


def _parse_docx(source: Path | str, *, store: SourceStore) -> dict[str, Any]:
    from docx import Document
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    from list_detector import _numbering_maps, analyze_docx_lists, describe_docx_paragraph
    from table_semantics import classify_docx_table
    from .semantics.tables import capture_docx_table_layout

    handle = store.open_source(source)
    document = Document(str(handle.path))
    facts = _empty_facts(handle, "docx-source-facts", "docx-ooxml", parser_version="1.1.0")
    parser_version = facts["parser"]["version"]
    report: dict[str, Any] = {}
    numbering_maps = _numbering_maps(document)
    if "numbering_maps" in inspect.signature(analyze_docx_lists).parameters:
        numbering = analyze_docx_lists(document, report, numbering_maps=numbering_maps)
    else:
        numbering = analyze_docx_lists(document, report)
    body = document.element.body
    position = 0
    nested_list_ordinals: dict[tuple[str, int, int, str | None], int] = {}

    def add_unit(parent_id: str, paragraph: Any, node_path: list[str | int], source_position: int, descriptor: dict[str, Any] | None = None) -> None:
        unit_id = _stable_id("u", handle.source_id, "paragraph", *node_path)
        ref = _source_ref(handle, "word/document.xml", node_path=node_path, parser_version=parser_version)
        facts["units"][unit_id] = {
            "kind": "paragraph",
            "source_ref": ref,
            "inline_content": _docx_inline_content(paragraph, handle, node_path, facts["resources"], parser_version),
            "style": paragraph.style.name if paragraph.style is not None else None,
        }
        source_paragraph_format = _docx_source_paragraph_format(paragraph)
        if source_paragraph_format:
            facts["units"][unit_id]["source_paragraph_format"] = source_paragraph_format
        facts["children"].setdefault(parent_id, []).append(unit_id)
        evidence = {"source_position": source_position, "style": paragraph.style.name if paragraph.style is not None else None}
        if descriptor is not None:
            evidence.update({
                "numbering_status": descriptor.get("status"),
                "num_fmt": descriptor.get("num_fmt"),
                "lvl_text": descriptor.get("lvl_text"),
                "source_list_type": descriptor.get("source_list_type"),
                "numbering_evidence": list(descriptor.get("evidence") or []),
                "indent_value": descriptor.get("layout", {}).get("left_twips", 0),
                "indent_unit": "twips",
                "marker_text": descriptor.get("lvl_text"),
                "ordinal": descriptor.get("start"),
                "explicit_depth": descriptor.get("ilvl"),
                "restart_hint": descriptor.get("restart"),
                "numbering_definition_ref": {
                    "num_id": descriptor.get("num_id"),
                    "abstract_num_id": descriptor.get("abstract_num_id"),
                },
                "blank_before": False,
                "continuation_hint": False,
            })
            if descriptor.get("status") == "detected":
                evidence.update({
                    "marker_kind": "ordered" if descriptor.get("num_fmt") != "bullet" else "unordered",
                })
        facts["evidence"][unit_id] = evidence

    def _grid_span(cell: Any) -> int:
        tc_pr = cell._tc.tcPr
        if tc_pr is None:
            return 1
        grid_span = tc_pr.find(qn("w:gridSpan"))
        try:
            return max(1, int(grid_span.get(qn("w:val")))) if grid_span is not None else 1
        except (TypeError, ValueError):
            return 1

    def _vertical_merge(cell: Any) -> str | None:
        tc_pr = cell._tc.tcPr
        if tc_pr is None:
            return None
        merge = tc_pr.find(qn("w:vMerge"))
        if merge is None:
            return None
        return merge.get(qn("w:val")) or "continue"

    def add_table(parent_id: str, table: Any, node_path: list[str | int]) -> None:
        nonlocal position
        table_position = position
        table_id = _stable_id("c", handle.source_id, "table", *node_path)
        table_semantics = classify_docx_table(table)
        facts["containers"][table_id] = {
            "kind": "table",
            "source_ref": _source_ref(handle, "word/document.xml", node_path=node_path, parser_version=parser_version),
            "table_semantics": table_semantics.as_source_record(),
            "layout": capture_docx_table_layout(table),
        }
        facts["children"][table_id] = []
        facts["children"].setdefault(parent_id, []).append(table_id)
        active_vertical_cells: dict[int, tuple[str, int]] = {}
        for row_index, row in enumerate(table.rows):
            row_path = [*node_path, row_index]
            row_id = _stable_id("c", handle.source_id, "row", *row_path)
            facts["containers"][row_id] = {
                "kind": "table_row",
                "source_ref": _source_ref(handle, "word/document.xml", node_path=row_path, parser_version=parser_version),
            }
            facts["children"][row_id] = []
            facts["children"][table_id].append(row_id)
            merged_cells: list[dict[str, Any]] = []
            facts["containers"][row_id]["merged_cells"] = merged_cells
            column_cursor = 0
            raw_cells = [child for child in row._tr.iterchildren() if child.tag == qn("w:tc")]
            for raw_cell in raw_cells:
                cell = _Cell(raw_cell, table)
                col_index = column_cursor
                col_span = _grid_span(cell)
                merge_state = _vertical_merge(cell)
                if merge_state == "continue" and col_index in active_vertical_cells:
                    anchor_id, start_row = active_vertical_cells[col_index]
                    anchor = facts["containers"][anchor_id]
                    anchor["row_span"] = row_index - start_row + 1
                    merged_cells.append({"cell_id": anchor_id, "col": col_index, "col_span": col_span, "continuation": True})
                    column_cursor += col_span
                    continue
                cell_path = [*node_path, row_index, col_index]
                cell_id = _stable_id("c", handle.source_id, "cell", *cell_path)
                facts["containers"][cell_id] = {
                    "kind": "table_cell",
                    "row": row_index,
                    "col": col_index,
                    "row_span": 1,
                    "col_span": col_span,
                    "source_ref": _source_ref(handle, "word/document.xml", node_path=cell_path, parser_version=parser_version),
                }
                facts["children"][cell_id] = []
                facts["children"][row_id].append(cell_id)
                if merge_state == "restart":
                    for col in range(col_index, col_index + col_span):
                        active_vertical_cells[col] = (cell_id, row_index)
                else:
                    for col in range(col_index, col_index + col_span):
                        active_vertical_cells.pop(col, None)
                paragraph_index = 0
                nested_table_index = 0
                for child in cell._tc.iterchildren():
                    if child.tag == qn("w:p"):
                        paragraph = Paragraph(child, cell)
                        descriptor = describe_docx_paragraph(document, paragraph, numbering_maps=numbering_maps)
                        if descriptor is not None:
                            key = (cell_id, int(descriptor.get("num_id") or 0), int(descriptor.get("ilvl") or 0), descriptor.get("num_fmt"))
                            ordinal = nested_list_ordinals.get(key, 0) + 1
                            nested_list_ordinals[key] = ordinal
                            descriptor = dict(descriptor)
                            descriptor["start"] = ordinal
                            descriptor["restart"] = ordinal == 1
                        child_path = [*cell_path, "w:p", paragraph_index]
                        add_unit(cell_id, paragraph, child_path, position, descriptor)
                        paragraph_index += 1
                    elif child.tag == qn("w:tbl"):
                        child_path = [*cell_path, "w:tbl", nested_table_index]
                        add_table(cell_id, Table(child, cell), child_path)
                        nested_table_index += 1
                column_cursor += col_span
        facts["evidence"][table_id] = {"source_position": table_position, "table_scope": "cell_local"}

    for child_index, child in enumerate(body.iterchildren()):
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            add_unit("root", paragraph, ["body", child_index, "w:p"], position, numbering.get(position))
            position += 1
        elif child.tag == qn("w:tbl"):
            add_table("root", Table(child, document), ["body", child_index, "w:tbl"])
            position += 1

    facts["extensions"]["docx_numbering_report"] = {"source_lists": report.get("source_lists", {})}
    validate_source_facts(facts)
    return facts


def parse(source: Path | str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    store = context.get("source_store") or SourceStore()
    owns_store = "source_store" not in context
    text = context.get("text")
    try:
        suffix = Path(source).suffix.lower()
        if suffix == ".docx":
            return _parse_docx(source, store=store)
        return _parse_markdown(source, store=store, text=text)
    finally:
        if owns_store:
            store.close()


def summarize_source_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """A public consumer that only reads the SourceFacts contract."""
    validate_source_facts(facts)
    return {
        "source_type": facts["source"]["source_type"],
        "unit_count": len(facts["units"]),
        "container_count": len(facts["containers"]),
        "resource_count": len(facts["resources"]),
        "diagnostic_codes": [item["code"] for item in facts["diagnostics"]],
        "marker_kinds": sorted({item.get("marker_kind") for item in facts["evidence"].values() if item.get("marker_kind")}),
    }


def digest_source_facts(facts: dict[str, Any]) -> str:
    """Return a stable digest for the immutable SourceFacts contract."""
    validate_source_facts(facts)
    payload = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _digest(payload)
