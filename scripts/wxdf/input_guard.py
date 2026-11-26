"""Input precheck for DOCX sources that carry comments or unprocessed revisions.

Conversion has no defined behavior for review marks: it cannot keep, accept,
reject or carry them.  The precheck reads the same SourceStore snapshot the
pipeline parses, looks for real records in WordprocessingML (never for the
``trackRevisions`` switch or ``rsid`` attributes, which a clean document may
carry), and reports only a category.  Comment bodies, authors and revision
content are never read into the result.
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Tuple

from lxml import etree


COMMENTS_CODE = "WXDF-E-INPUT-COMMENTS"
REVISIONS_CODE = "WXDF-E-INPUT-TRACKED-CHANGES"

_STOPPED = "本次转换已停止，未生成或覆盖输出文档。"

GUIDANCE: Dict[str, str] = {
    COMMENTS_CODE: "输入文档含有批注。请先在 Word 或 WPS 中删除全部批注，再另存为副本，用副本重新转换。" + _STOPPED,
    REVISIONS_CODE: "输入文档含有未处理的修订记录。请先在 Word 或 WPS 中接受或拒绝全部修订，再另存为副本，用副本重新转换。" + _STOPPED,
}

_CATEGORY = {COMMENTS_CODE: "comments", REVISIONS_CODE: "tracked_changes"}

_WORDPROCESSING_NAMESPACES = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
)

_COMMENT_ELEMENTS = frozenset({
    "comment", "commentRangeStart", "commentRangeEnd", "commentReference",
})

_REVISION_ELEMENTS = frozenset({
    "ins", "del", "moveFrom", "moveTo",
    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
    "rPrChange", "pPrChange", "sectPrChange", "numberingChange",
    "tblPrChange", "tblPrExChange", "tblGridChange", "trPrChange", "tcPrChange",
    "cellIns", "cellDel", "cellMerge",
    "customXmlInsRangeStart", "customXmlInsRangeEnd",
    "customXmlDelRangeStart", "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
})

# The body, every header, footer, footnote and endnote story, the comment part
# and the glossary documents.  Styles and numbering definitions are not stories
# and the converter replaces them with the template's own.
_SCANNED_PARTS = re.compile(
    r"word/(?:document|comments|footnotes|endnotes|(?:header|footer)[^/]*)\.xml"
    r"|word/glossary/[^/]+\.xml"
)
_CONTENT_TYPES_PART = "[Content_Types].xml"
_CONTENT_TYPE_OVERRIDE = "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
_STORY_CONTENT_TYPES = frozenset(
    f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"
    for kind in ("document.main", "header", "footer", "footnotes", "endnotes", "comments", "document.glossary")
)


def _declared_story_parts(payload: bytes) -> set[str]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError:
        return set()
    return {
        element.get("PartName", "").lstrip("/")
        for element in root.iterchildren(_CONTENT_TYPE_OVERRIDE)
        if element.get("ContentType") in _STORY_CONTENT_TYPES
    }


def _scan_part(payload: bytes, found: set) -> None:
    try:
        events = etree.iterparse(
            io.BytesIO(payload),
            events=("end",),
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        )
        for _event, element in events:
            tag = element.tag
            if isinstance(tag, str) and tag.startswith("{"):
                namespace, _, local = tag[1:].partition("}")
                if namespace in _WORDPROCESSING_NAMESPACES:
                    if local in _COMMENT_ELEMENTS:
                        found.add(COMMENTS_CODE)
                    elif local in _REVISION_ELEMENTS:
                        found.add(REVISIONS_CODE)
            element.clear()
            parent = element.getparent()
            while parent is not None and element.getprevious() is not None:
                del parent[0]
            if len(found) == len(GUIDANCE):
                return
    except etree.XMLSyntaxError:
        # A part that is not well formed is the parser's finding to report.
        return


def detect_input_marks(store: Any) -> List[str]:
    """Return the stable codes of every mark category the source snapshot carries."""

    handle = store.handle
    found: set[str] = set()
    names = store.part_names()
    declared_stories = set()
    if _CONTENT_TYPES_PART in names:
        manifest = store.resolve({"source_id": handle.source_id, "part": _CONTENT_TYPES_PART})
        declared_stories = _declared_story_parts(manifest)
    for name in names:
        if name not in declared_stories and not _SCANNED_PARTS.fullmatch(name):
            continue
        payload = store.resolve({"source_id": handle.source_id, "part": name})
        if isinstance(payload, bytes):
            _scan_part(payload, found)
        if len(found) == len(GUIDANCE):
            break
    return [code for code in (COMMENTS_CODE, REVISIONS_CODE) if code in found]


def precheck_failure(codes: List[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return the report fields and diagnostics for a blocked input."""

    fields = {
        "status": "failed",
        "delivery_status": "blocked",
        "input_precheck": {
            "status": "blocked",
            "categories": [{"code": code, "category": _CATEGORY[code]} for code in codes],
        },
    }
    diagnostics = [
        {
            "code": code,
            "severity": "error",
            "category": "input_precheck",
            "message": GUIDANCE[code],
        }
        for code in codes
    ]
    return fields, diagnostics


def blocked_codes(report: Any) -> List[str]:
    """Return the known precheck codes a report records, in report order."""

    precheck = report.get("input_precheck") if isinstance(report, dict) else None
    if not isinstance(precheck, dict) or precheck.get("status") != "blocked":
        return []
    return [
        item["code"]
        for item in precheck.get("categories") or []
        if isinstance(item, dict) and item.get("code") in GUIDANCE
    ]
