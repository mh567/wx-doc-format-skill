from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from docx import Document
from docx.enum.style import WD_STYLE_TYPE

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.parsers import parse
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.protected import collect_protected_candidates
from modular_support import _append_numbering_definition, _set_numbering
from test_issue1_acceptance import _run_native
from test_issue2_acceptance import _assert_native_completed


def _facts(tmp_path: Path, text: str) -> dict:
    return parse(tmp_path / "issue4.md", {"text": text})


def _unit_text(facts: dict, node_id: str) -> str:
    if node_id in facts["containers"]:
        return " ".join(
            _unit_text(facts, child_id)
            for child_id in facts["children"].get(node_id, [])
            if child_id in facts["units"] or child_id in facts["containers"]
        ).strip()
    unit = facts["units"][node_id]
    if unit.get("raw_text"):
        return str(unit["raw_text"]).strip()
    return "".join(
        item.get("text", "")
        for item in unit.get("inline_content", [])
        if item.get("kind") == "text"
    )


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


def _xml_paragraph_text(paragraph: ElementTree.Element, namespace: str) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{namespace}}}t":
            parts.append(node.text or "")
        elif node.tag == f"{{{namespace}}}br":
            parts.append("\n")
        elif node.tag == f"{{{namespace}}}tab":
            parts.append("\t")
    return "".join(parts)


def test_l11_protects_manual_number_date_version_api_and_note_like_text(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """# 文档

```text
1. 代码中的编号
```

2026.07.19 发布生产版本

发布日期：2026.07.19

v1.2.3 版本说明

版本号：v1.2.3

Version 1.2.3

1.2.3 版本号说明

GET /users

API 用户查询

请求参数

注1：说明

注1 无冒号说明

1. 真正列项
""",
    )

    protected = {
        _unit_text(facts, candidate["target_node_ids"][0]): candidate["proposal"]["value"]["reason"]
        for candidate in collect_protected_candidates(facts)
    }
    list_result = analyze_lists(facts)
    list_texts = {_unit_text(facts, item["item_id"]) for item in list_result["items"]}

    assert protected["1. 代码中的编号"] == "opaque_code"
    assert protected["2026.07.19 发布生产版本"] == "date_or_version"
    assert protected["发布日期：2026.07.19"] == "date_or_version"
    assert protected["v1.2.3 版本说明"] == "date_or_version"
    assert protected["版本号：v1.2.3"] == "date_or_version"
    assert protected["Version 1.2.3"] == "date_or_version"
    assert protected["1.2.3 版本号说明"] == "date_or_version"
    assert protected["GET /users"] == "api_label"
    assert protected["API 用户查询"] == "api_label"
    assert protected["请求参数"] == "api_label"
    assert protected["注1：说明"] == "note"
    assert protected["注1 无冒号说明"] == "note"
    assert list_texts == {"真正列项"}


def test_l11_docx_protection_uses_the_same_public_semantics(tmp_path: Path) -> None:
    source = tmp_path / "issue4.docx"
    document = Document()
    _append_numbering_definition(document, abstract_id=997, num_id=997, levels=[("decimal", "%1.")])
    code_style = document.styles.add_style("代码块", WD_STYLE_TYPE.PARAGRAPH)
    code = document.add_paragraph("1. 代码内容", style=code_style)
    _set_numbering(code, num_id=997, level=0)
    code_note = document.add_paragraph("注1：代码内容", style=code_style)
    _set_numbering(code_note, num_id=997, level=0)
    api_list = document.add_paragraph("GET /list")
    _set_numbering(api_list, num_id=997, level=0)
    for text in (
        "6.3 标准化注册",
        "2026.07.19 发布",
        "发布日期：2026.07.19",
        "v1.2.3 版本",
        "版本号：v1.2.3",
        "Version 1.2.3",
        "1.2.3 版本号说明",
        "GET /users",
        "API 用户查询",
        "请求参数",
        "注1 无冒号说明",
        "普通正文",
    ):
        document.add_paragraph(text)
    document.add_paragraph("API 用户详情", style="Heading 1")
    document.add_paragraph("注2 无冒号说明", style="Heading 1")
    document.save(source)

    facts = parse(source)
    protected = {
        _unit_text(facts, candidate["target_node_ids"][0]): candidate["proposal"]["value"]["reason"]
        for candidate in collect_protected_candidates(facts)
    }

    assert protected == {
        "1. 代码内容": "opaque_code",
        "注1：代码内容": "opaque_code",
        "GET /list": "api_label",
        "6.3 标准化注册": "manual_heading_marker",
        "2026.07.19 发布": "date_or_version",
        "发布日期：2026.07.19": "date_or_version",
        "v1.2.3 版本": "date_or_version",
        "版本号：v1.2.3": "date_or_version",
        "Version 1.2.3": "date_or_version",
        "1.2.3 版本号说明": "date_or_version",
        "GET /users": "api_label",
        "API 用户查询": "api_label",
        "请求参数": "api_label",
        "注1 无冒号说明": "note",
        "API 用户详情": "api_label",
        "注2 无冒号说明": "note",
    }
    list_texts = {_unit_text(facts, item["item_id"]) for item in analyze_lists(facts)["items"]}
    assert list_texts == {"GET /list"}

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l11-docx-native")
    assert report["status"] in {"completed", "completed_with_warnings"}
    assert output.is_file()
    assert report["source_facts_immutable"] is True
    assert report["capability_migration"]["protected_content"] == "consumed"
    assert report["model_audit"]["passed"] is True
    assert report["saved_output_audit"]["passed"] is True
    assert any(
        _ast_text(source_ast, operation["node_id"]) == "GET /list"
        and operation["kind"] == "list_item"
        for operation in report["model_audit"]["render_plan"]["operations"]
    )
    assert normalized["semantic_digest"]
    text_by_block = {
        _ast_text(source_ast, node_id): block
        for node_id, block in source_ast["document"]["blocks"].items()
        if block.get("inline_content")
    }
    assert text_by_block["API 用户详情"]["role"] == "body"
    assert text_by_block["注2 无冒号说明"]["role"] == "numbered_note"


def test_l11_native_report_and_saved_output_preserve_protected_roles(tmp_path: Path) -> None:
    source = tmp_path / "issue4-native.md"
    source.write_text(
        """# 文档

```text
1. 代码段内容
2. 第二行编号
```

1.2 代码中的编号

2026.07.19 发布生产版本

v1.2.3 版本说明

GET /users

请求参数

注1 无冒号说明

1. 真正列项
""",
        encoding="utf-8",
    )

    report, source_ast, normalized, output = _run_native(source, tmp_path, "l11-native")

    _assert_native_completed(report, output)
    assert report["capability_migration"]["protected_content"] == "consumed"
    assert report["model_audit"]["passed"] is True
    assert report["saved_output_audit"]["passed"] is True

    source_facts = parse(source)
    candidate_reasons = {
        candidate["target_node_ids"][0]: candidate["proposal"]["value"]["reason"]
        for candidate in collect_protected_candidates(source_facts)
    }
    overlays = report["native_transaction"]["overlays"]
    assert set(overlays) >= set(candidate_reasons)
    for node_id, reason in candidate_reasons.items():
        assert overlays[node_id]["protected"] is True
        assert overlays[node_id]["reason"] == reason

    blocks = source_ast["document"]["blocks"]
    text_by_node = {
        _ast_text(source_ast, node_id): (node_id, block)
        for node_id, block in blocks.items()
        if block.get("inline_content")
    }
    assert any(
        block.get("block_type") == "opaque"
        and "source_code_block" in block.get("diagnostic_ids", [])
        for block in blocks.values()
    )
    assert text_by_node["1.2 代码中的编号"][1]["role"] == "body"
    assert text_by_node["2026.07.19 发布生产版本"][1]["role"] == "body"
    assert text_by_node["v1.2.3 版本说明"][1]["role"] == "body"
    assert text_by_node["GET /users"][1]["role"] == "body"
    assert text_by_node["请求参数"][1]["role"] == "body"
    assert text_by_node["注1 无冒号说明"][1]["role"] == "numbered_note"
    operations = report["model_audit"]["render_plan"]["operations"]
    operation_by_text = {
        _ast_text(source_ast, operation["node_id"]): operation
        for operation in operations
        if operation["kind"] == "paragraph"
    }
    assert operation_by_text["1.2 代码中的编号"]["role"] == "body"
    assert operation_by_text["2026.07.19 发布生产版本"]["role"] == "body"
    assert operation_by_text["v1.2.3 版本说明"]["role"] == "body"
    assert operation_by_text["GET /users"]["role"] == "body"
    assert operation_by_text["请求参数"]["role"] == "body"
    assert operation_by_text["注1 无冒号说明"]["role"] == "numbered_note"
    with ZipFile(output) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    style_names = {
        style.get(f"{{{namespace}}}styleId"): style.find("w:name", ns).get(f"{{{namespace}}}val")
        for style in styles.findall("w:style", ns)
        if style.find("w:name", ns) is not None
    }
    saved_paragraphs = {}
    for paragraph in document.findall(".//w:p", ns):
        text = _xml_paragraph_text(paragraph, namespace)
        style = paragraph.find("w:pPr/w:pStyle", ns)
        style_id = style.get(f"{{{namespace}}}val") if style is not None else None
        if text:
            saved_paragraphs[text] = style_names.get(style_id, "Normal")
    for text in (
        "1. 代码段内容\n2. 第二行编号",
        "1.2 代码中的编号",
        "2026.07.19 发布生产版本",
        "v1.2.3 版本说明",
        "GET /users",
        "请求参数",
        "注1 无冒号说明",
    ):
        expected_style = "3.2注-有编号注" if text == "注1 无冒号说明" else "Normal"
        assert saved_paragraphs[text] == expected_style
    assert any(
        _ast_text(source_ast, child_id) == "真正列项"
        for item_id, item in source_ast["document"]["containers"].items()
        if item.get("kind") == "list_item"
        for child_id in source_ast["document"]["children"].get(item_id, [])
        if child_id in blocks
    )
    assert normalized["semantic_digest"]


def test_l11_protected_heading_like_labels_keep_their_roles_in_native_ast(tmp_path: Path) -> None:
    markdown = tmp_path / "issue4-heading-like.md"
    markdown.write_text("# 文档\n\n# API 用户查询\n\n# 注2 无冒号说明\n", encoding="utf-8")
    report, source_ast, _, output = _run_native(markdown, tmp_path, "l11-heading-like-md")

    _assert_native_completed(report, output)
    text_by_block = {
        _ast_text(source_ast, node_id): block
        for node_id, block in source_ast["document"]["blocks"].items()
        if block.get("inline_content")
    }
    assert text_by_block["API 用户查询"]["role"] == "body"
    assert text_by_block["注2 无冒号说明"]["role"] == "numbered_note"
    operations = report["model_audit"]["render_plan"]["operations"]
    operation_by_text = {
        _ast_text(source_ast, operation["node_id"]): operation
        for operation in operations
        if operation["kind"] == "paragraph"
    }
    assert operation_by_text["API 用户查询"]["role"] == "body"
    assert operation_by_text["注2 无冒号说明"]["role"] == "numbered_note"
