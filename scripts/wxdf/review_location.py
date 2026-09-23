"""Human-readable locations for review findings.

A review finding names a problem, and a reader needs to know *where* in the
document it sits.  DOCX stores no pagination, so a page number cannot be read
out of the file; what the document does carry is its order: which section a block
belongs to and which paragraph, table or figure it is within that section.  This
module is the single owner of that translation, and of the small vocabulary the
report and the issue merger share (a finding's locations, its occurrence count
and their one-line summary).

The locator is built once from the same source model the checks read
(``SourceFacts``) plus the python-docx document, so a fact node and a
python-docx paragraph resolve to the same position.  Every position is stated as
"section path + object within the section", for example
``第2章 系统设计 > 2.3 部署要求 · 第5段`` or ``第3章 … · 第2张表格``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TypedDict

from docx import Document

from wxdf.model import inline_text_projection

_SECTION_JOIN = " > "
_HEADING_TEXT_LIMIT = 24
_OBJECT_TEXT_LIMIT = 20
FIRST_SECTION = "文档开头"
# How many distinct places one finding prints before it is summarised instead.
LOCATION_LIMIT = 10


class Finding(TypedDict, total=False):
    """A review finding, the shape the checks and the report both speak.

    The fields travel together through every stage (produce, merge, print), so
    the shape is named once here rather than repeated as a loose dict.
    """

    code: str
    level: str
    role: str
    title: str
    suggestion: str
    location: str
    locations: list[str]
    count: int


@dataclass(frozen=True)
class Position:
    """Where one document object sits, in terms a reader can navigate."""

    section: str
    kind: str
    ordinal: int | None = None
    text: str = ""

    def label(self) -> str:
        section = self.section or FIRST_SECTION
        if self.kind == "document":
            return f"全文 · {self.text}" if self.text else "全文"
        if self.kind == "section":
            return f"第{self.ordinal}节"
        if self.kind == "toc":
            return "文档目录"
        if self.kind == "heading":
            return f"{section} · 标题「{self.text}」" if self.text else f"{section} · 标题"
        if self.ordinal is None:
            # A section-level fallback: the object could not be paired, so the
            # section is as precise as the position gets.
            return f"{section} · {self.text}" if self.text else section
        if self.kind == "table":
            label = f"{section} · 第{self.ordinal}张表格"
        elif self.kind == "figure":
            label = f"{section} · 第{self.ordinal}张图片"
        else:
            label = f"{section} · 第{self.ordinal}段"
        return f"{label}「{self.text}」" if self.text else label


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit]


def _section_label(stack: list[tuple[int, str]]) -> str:
    if not stack:
        return FIRST_SECTION
    return _SECTION_JOIN.join(_short(text, _HEADING_TEXT_LIMIT) for _, text in stack)


class DocumentLocator:
    """Resolve fact nodes and python-docx objects to a printable position."""

    def __init__(self, facts: Mapping[str, Any], doc: Document) -> None:
        self._by_node: dict[str, Position] = {}
        self._by_paragraph_index: dict[int, Position] = {}
        self._by_table_index: dict[int, Position] = {}
        self._build(facts, doc)

    # -- public lookups ----------------------------------------------------

    def node(self, node_id: str | None) -> Position | None:
        return self._by_node.get(str(node_id)) if node_id else None

    def paragraph(self, index: int | None) -> Position | None:
        return self._by_paragraph_index.get(int(index)) if index else None

    def table(self, index: int | None) -> Position | None:
        return self._by_table_index.get(int(index)) if index else None

    def document(self, text: str = "") -> Position:
        return Position(section="", kind="document", text=text)

    def section(self, index: int) -> Position:
        return Position(section="", kind="section", ordinal=int(index))

    def toc(self) -> Position:
        return Position(section="", kind="toc")

    # -- construction ------------------------------------------------------

    def _build(self, facts: Mapping[str, Any], doc: Document) -> None:
        headings = self._heading_map(facts)
        children = facts.get("children", {}) or {}
        units = facts.get("units", {}) or {}
        containers = facts.get("containers", {}) or {}

        stack: list[tuple[int, str]] = []
        paragraph_ordinal = 0
        table_ordinal = 0
        figure_ordinal = 0
        paragraph_nodes: list[str] = []
        table_nodes: list[str] = []

        for raw_node in children.get("root", []):
            node_id = str(raw_node)
            if node_id in headings:
                level, text = headings[node_id]
                while stack and stack[-1][0] >= level:
                    stack.pop()
                parent = _section_label(stack)
                stack.append((level, text))
                paragraph_ordinal = 0
                table_ordinal = 0
                figure_ordinal = 0
                # A heading is a body paragraph too, so it joins the paragraph
                # order the python-docx document is paired against; it just does
                # not advance the within-section paragraph counter.
                paragraph_nodes.append(node_id)
                self._by_node[node_id] = Position(
                    section=parent, kind="heading", text=_short(text, _HEADING_TEXT_LIMIT)
                )
                continue
            container = containers.get(node_id, {})
            if container.get("kind") == "table":
                table_ordinal += 1
                table_nodes.append(node_id)
                self._by_node[node_id] = Position(
                    section=_section_label(stack), kind="table", ordinal=table_ordinal
                )
                continue
            unit = units.get(node_id)
            if not isinstance(unit, Mapping) or unit.get("kind") != "paragraph":
                continue
            paragraph_nodes.append(node_id)
            inline = unit.get("inline_content") or []
            has_image = any(item.get("kind") == "image" for item in inline)
            text = _short(inline_text_projection(inline), _OBJECT_TEXT_LIMIT)
            paragraph_ordinal += 1
            if has_image:
                figure_ordinal += 1
                position = Position(section=_section_label(stack), kind="figure", ordinal=figure_ordinal, text=text)
            else:
                position = Position(section=_section_label(stack), kind="paragraph", ordinal=paragraph_ordinal, text=text)
            self._by_node[node_id] = position

        self._pair_paragraphs(paragraph_nodes, doc)
        self._pair_tables(table_nodes, doc)

    @staticmethod
    def _heading_map(facts: Mapping[str, Any]) -> dict[str, tuple[int, str]]:
        from wxdf.review_checks import heading_records

        return {
            str(record["node_id"]): (int(record["level"]), str(record.get("text") or ""))
            for record in heading_records(facts)
            if record.get("node_id")
        }

    def _pair_paragraphs(self, node_ids: list[str], doc: Document) -> None:
        paragraphs = list(doc.paragraphs)
        if len(paragraphs) == len(node_ids):
            for index, node_id in enumerate(node_ids, 1):
                self._by_paragraph_index[index] = self._by_node[node_id]
            return
        # The two orders can diverge when content controls or text boxes wrap
        # body content.  Match what can be matched by text; a paragraph no probe
        # matches falls back to its nearest preceding section rather than to no
        # position at all.
        cursor = 0
        section = FIRST_SECTION
        for index, paragraph in enumerate(paragraphs, 1):
            text = _short(paragraph.text, _OBJECT_TEXT_LIMIT)
            matched = None
            for probe in range(cursor, len(node_ids)):
                if self._by_node[node_ids[probe]].text == text:
                    matched = self._by_node[node_ids[probe]]
                    cursor = probe + 1
                    break
            if matched is None:
                matched = Position(section=section, kind="paragraph", ordinal=None, text=text)
            else:
                section = matched.section
            self._by_paragraph_index[index] = matched

    def _pair_tables(self, node_ids: list[str], doc: Document) -> None:
        tables = list(doc.tables)
        for index, node_id in enumerate(node_ids, 1):
            if index <= len(tables):
                self._by_table_index[index] = self._by_node[node_id]


def issue_locations(issue: Finding) -> list[str]:
    """Return a finding's printable places, from ``locations`` or ``location``."""

    locations = issue.get("locations")
    if isinstance(locations, list) and locations:
        return [str(item) for item in locations if item]
    location = issue.get("location")
    return [str(location)] if location else []


def issue_count(issue: Finding, locations: list[str] | None = None) -> int:
    """Return how many places a finding covers (at least one, at least its list)."""

    if locations is None:
        locations = issue_locations(issue)
    try:
        count = int(issue.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    return max(count, len(locations), 1)


def location_summary(locations: list[str], count: int) -> str:
    """Return one line naming a place, or the first place "等 N 处"."""

    if not locations:
        return ""
    if count <= 1:
        return locations[0]
    return f"{locations[0]} 等 {count} 处"


def location_view(issue: Finding) -> tuple[int, list[str], int]:
    """Return ``(count, shown locations, omitted)`` for one finding.

    Both report writers render the same three numbers, so they read them from
    here instead of recomputing the cap arithmetic two ways.
    """

    locations = issue_locations(issue)
    count = issue_count(issue, locations)
    return count, locations, max(count - len(locations), 0)


def location_fields(
    locator: DocumentLocator | None,
    positions: list[Position | None],
    *,
    count: int | None = None,
    limit: int = LOCATION_LIMIT,
) -> dict[str, Any]:
    """Return the ``locations`` / ``location`` keys for one finding.

    ``locations`` holds the printable positions (capped so a report stays
    readable); ``location`` keeps the single-string summary other consumers
    already read.  An empty result leaves the finding's own ``location`` in
    place, which is what a caller without a locator falls back to.
    """

    if locator is None:
        return {}
    labels = _unique([position.label() for position in positions if position is not None])
    if not labels:
        return {}
    total = count if count is not None else len(labels)
    return {
        "locations": labels[:limit],
        "location": location_summary(labels, total),
    }
