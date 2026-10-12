from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_issue1_acceptance import _run_native
from test_issue2_acceptance import _assert_native_completed
from modular_support import _append_numbering_definition, _set_numbering
from wxdf.parsers import parse
from wxdf.semantics.appendices import collect_source_appendix_semantics


def _ast_text(ast: dict, node_id: str) -> str:
    document = ast["document"]
    if node_id in document["containers"]:
        return " ".join(
            _ast_text(ast, child_id)
            for child_id in document["children"].get(node_id, [])
            if child_id in document["blocks"] or child_id in document["containers"]
        ).strip()
    return "".join(
        item.get("text", "")
        for item in document["blocks"][node_id].get("inline_content", [])
        if item.get("kind") == "text"
    )


def _list_items_by_text(ast: dict, normalized: dict) -> dict[str, dict]:
    document = ast["document"]
    result: dict[str, dict] = {}
    for item_id, item in document["containers"].items():
        if item.get("kind") != "list_item":
            continue
        text = " ".join(
            _ast_text(ast, child_id)
            for child_id in document["children"].get(item_id, [])
            if child_id in document["blocks"]
        ).strip()
        result[text] = normalized["presentation"]["nodes"][item_id]
    return result


def test_l12_appendix_ranges_materialize_native_scope_and_keep_numbering_scope_separate(tmp_path: Path) -> None:
    source = tmp_path / "issue5.md"
    source.write_text(
        """# 文档

1. 正文一
2. 正文二

# 附录 A

### A.1 适用范围

1. 附录 A 一
2. 附录 A 二

# 附录 B

### B.1 附录内容

1. 附录 B 一
""",
        encoding="utf-8",
    )

    facts = parse(source)
    semantics = collect_source_appendix_semantics(facts)
    assert [item["appendix_id"] for item in semantics["ranges"]] == ["A", "B"]
    assert all(item["root_node_ids"] for item in semantics["ranges"])

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l12-native")

    _assert_native_completed(report, output)
    assert report["capability_migration"]["appendix_semantics"] == "consumed"
    assert report["appendix_semantics_consumption"] == {
        "candidate_count": 16,
        "range_candidate_count": 2,
        "range_consumed_count": 2,
        "heading_candidate_count": 2,
        "heading_consumed_count": 2,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
    assert report["appendix_preservation_audit"]["passed"] is True

    document = source_ast["document"]
    appendices = {
        node_id: node
        for node_id, node in document["containers"].items()
        if node.get("kind") == "appendix"
    }
    assert {node["appendix_id"] for node in appendices.values()} == {"A", "B"}
    assert all(node["semantic_scope_id"].startswith("semantic:appendix:") for node in appendices.values())
    assert all(node["page_break_before"] is True for node in appendices.values())
    for container_id, appendix in appendices.items():
        appendix_id = appendix["appendix_id"]
        descendants = set(document["children"].get(container_id, []))
        frontier = list(descendants)
        while frontier:
            current = frontier.pop()
            for child_id in document["children"].get(current, []):
                if child_id not in descendants:
                    descendants.add(child_id)
                    frontier.append(child_id)
        assert all(
            (document["blocks"].get(node_id) or document["containers"].get(node_id))["appendix_id"] == appendix_id
            for node_id in descendants
        )

    title_by_id = {
        node["appendix_id"]: document["blocks"][node["title_ref"]]
        for node in appendices.values()
    }
    assert all(block["appendix_role"] == "title" for block in title_by_id.values())
    assert all(block["semantic_scope_id"].startswith("semantic:appendix:") for block in title_by_id.values())

    heading_blocks = {
        _ast_text(source_ast, node_id): block
        for node_id, block in document["blocks"].items()
        if block.get("appendix_role") == "heading"
    }
    assert heading_blocks["适用范围"]["appendix_relative_level"] == 1
    assert heading_blocks["附录内容"]["appendix_relative_level"] == 1

    presentations = _list_items_by_text(source_ast, normalized)
    assert [presentations[text]["target_ordinal"] for text in ("正文一", "正文二")] == [1, 2]
    assert [presentations[text]["target_ordinal"] for text in ("附录 A 一", "附录 A 二")] == [1, 2]
    assert presentations["附录 B 一"]["target_ordinal"] == 1
    for text in ("正文一", "附录 A 一", "附录 B 一"):
        presentation = presentations[text]
        assert presentation["restart"] is True
        assert presentation["numbering_scope_id"] == presentation["scope_id"]
    assert presentations["正文一"]["semantic_scope_id"] == "semantic:main"
    assert presentations["附录 A 一"]["semantic_scope_id"] == "semantic:appendix:A"
    assert presentations["附录 B 一"]["semantic_scope_id"] == "semantic:appendix:B"
    assert len({presentations[text]["semantic_scope_id"] for text in ("正文一", "附录 A 一", "附录 B 一")}) == 3
    assert normalized["presentation"]["template_contract"]["appendix"]["page_break_before"] is True
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    with ZipFile(output) as archive:
        document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
    appendix_title_paragraphs = [
        paragraph
        for paragraph in document_xml.findall(".//w:p", ns)
        if paragraph.find("w:pPr/w:outlineLvl", ns) is not None
        and paragraph.find("w:pPr/w:pageBreakBefore", ns) is not None
    ]
    assert len(appendix_title_paragraphs) == 2
    assert appendix_title_paragraphs[0].find("w:pPr/w:pageBreakBefore", ns) is not None


def test_l12_docx_heading_evidence_rejects_appendix_like_body_text(tmp_path: Path) -> None:
    source = tmp_path / "issue5.docx"
    document = Document()
    _append_numbering_definition(document, abstract_id=1205, num_id=1205, levels=[("decimal", "%1.")])
    document.add_heading("文档", level=1)
    main = document.add_paragraph("正文一")
    _set_numbering(main, num_id=1205, level=0)
    main = document.add_paragraph("正文二")
    _set_numbering(main, num_id=1205, level=0)
    document.add_heading("附录 A", level=1)
    document.add_heading("A.1 适用范围", level=3)
    appendix_item = document.add_paragraph("附录 A 一")
    _set_numbering(appendix_item, num_id=1205, level=0)
    appendix_item = document.add_paragraph("附录 A 二")
    _set_numbering(appendix_item, num_id=1205, level=0)
    document.add_heading("附录 B", level=1)
    document.add_heading("B.1 附录内容", level=3)
    appendix_b_item = document.add_paragraph("附录 B 一")
    _set_numbering(appendix_b_item, num_id=1205, level=0)
    appendix_b_item = document.add_paragraph("附录 B 二")
    _set_numbering(appendix_b_item, num_id=1205, level=0)
    document.save(source)

    facts = parse(source)
    semantics = collect_source_appendix_semantics(facts)
    assert [item["appendix_id"] for item in semantics["ranges"]] == ["A", "B"]
    assert len(semantics["candidates"]) == 2

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l12-docx")

    _assert_native_completed(report, output)
    assert report["appendix_preservation_audit"]["passed"] is True
    assert report["appendix_semantics_consumption"]["range_candidate_count"] == 2
    assert {
        block["appendix_id"]
        for block in source_ast["document"]["blocks"].values()
        if block.get("appendix_role") == "heading"
    } == {"A", "B"}
    presentations = _list_items_by_text(source_ast, normalized)
    assert presentations["正文一"]["semantic_scope_id"] == "semantic:main"
    assert presentations["附录 A 一"]["semantic_scope_id"] == "semantic:appendix:A"
    assert presentations["附录 B 一"]["semantic_scope_id"] == "semantic:appendix:B"
