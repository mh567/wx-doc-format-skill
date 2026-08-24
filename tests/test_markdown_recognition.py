import json
from pathlib import Path
import sys

import pytest
from markdown_it.token import Token

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from markdown_recognition import (
    MarkdownSource,
    RecognitionBuilder,
    RecognitionDiagnostic,
    canonical_semantic_sequence,
    recognize_markdown,
)
from md_pipeline import parse_md_to_model


def test_recognizer_is_deterministic_and_exposes_contract(tmp_path: Path) -> None:
    source = MarkdownSource(tmp_path / "sample.md", "# 标题\n\n---\n\n- **项目**\n\n列 | 值\n--- | ---\n甲 | `码`\n")
    first = recognize_markdown(source, template_version="test")
    second = recognize_markdown(source, template_version="test")
    assert first.document_model == second.document_model
    assert first.preservation_contract["token_inventory"]["hr"] == 1
    blocks = first.document_model["document"]["blocks"]
    assert any(block["block_type"] == "separator" for block in blocks)
    assert next(block for block in blocks if block["block_type"] == "list_item")["inline_runs"][0]["marks"] == ["strong"]


def test_typed_diagnostics_and_complete_preservation_contract(tmp_path: Path) -> None:
    source = MarkdownSource(tmp_path / "no-h1.md", "正文\n\n![远程](https://example.com/image.png)\n")
    result = recognize_markdown(source, template_version="test")

    assert all(isinstance(diagnostic, RecognitionDiagnostic) for diagnostic in result.diagnostics)
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"remote_image", "markdown_title_generated"}
    assert result.source_audit["unsupported_tokens"] == [
        {"type": "remote_image", "line_start": 3, "target": "https://example.com/image.png"},
    ]
    contract = result.preservation_contract
    assert contract["schema_version"] == "1.0"
    assert contract["dialect"] == "commonmark+table"
    assert len(contract["source_sha256"]) == 64
    assert contract["token_inventory"]["paragraph_open"] == 2
    assert contract["semantic_sequence"] == canonical_semantic_sequence(result.document_model)
    assert contract["diagnostic_codes"] == ["remote_image", "markdown_title_generated"]


@pytest.mark.parametrize(
    ("markdown", "block_type", "role", "text"),
    [
        ("# ATX 标题\n", "heading", "title", "ATX 标题"),
        ("Setext 标题\n===\n", "heading", "title", "Setext 标题"),
        ("正文第一行\n第二行\n", "body", None, "正文第一行 第二行"),
        ("```python\nprint(1)\n```\n", "body", "code_block", "print(1)\n"),
        ("---\n", "separator", None, None),
    ],
)
def test_commonmark_rule_normalization_matrix(tmp_path: Path, markdown: str, block_type: str, role: str | None, text: str | None) -> None:
    result = recognize_markdown(MarkdownSource(tmp_path / "matrix.md", markdown), template_version="test")
    block = next(block for block in result.document_model["document"]["blocks"] if block["block_type"] == block_type)
    assert block.get("role") == role
    assert block.get("text") == text
    assert not [diagnostic for diagnostic in result.diagnostics if diagnostic.code == "unknown_block_token"]


def test_container_context_covers_quote_list_and_table(tmp_path: Path) -> None:
    source = MarkdownSource(
        tmp_path / "containers.md",
        "# 标题\n\n> 引用正文\n\n- 父项\n  - 子项\n\n列 | 值\n--- | ---\n甲 | 乙\n",
    )
    result = recognize_markdown(source, template_version="test")
    blocks = result.document_model["document"]["blocks"]
    quote = next(block for block in blocks if block.get("role") == "quote")
    items = [block for block in blocks if block["block_type"] == "list_item"]
    table = next(block for block in blocks if block["block_type"] == "table")

    assert quote["source"]["container_quote_depth"] == 1
    assert items[1]["parent_list_item_id"] == items[0]["id"]
    assert items[1]["level"] > items[0]["level"]
    assert table["header_rows"] == 1
    assert [[cell["cell_role"] for cell in row] for row in table["rows"]] == [["header", "header"], ["body", "body"]]


def test_manual_unknown_tokens_preserve_visible_fallback_and_diagnostics(tmp_path: Path) -> None:
    source = MarkdownSource(tmp_path / "unknown.md", "")
    block_builder = RecognitionBuilder(source, template_version="test")
    unknown_block = Token("plugin_block", "", 0)
    unknown_block.content = "未知块可见内容"
    block_builder.tokens = [unknown_block]
    block_result = block_builder.build()

    inline_builder = RecognitionBuilder(source, template_version="test")
    inline_token = Token("inline", "", 0)
    unknown_inline = Token("plugin_inline", "", 0)
    unknown_inline.content = "未知行内内容"
    inline_token.children = [unknown_inline]
    inline_builder.tokens = [inline_token]
    inline_result = inline_builder.build()

    assert any(block.get("text") == "未知块可见内容" for block in block_result.document_model["document"]["blocks"])
    assert any(diagnostic.code == "unknown_block_token" for diagnostic in block_result.diagnostics)
    assert any(block.get("text") == "未知行内内容" for block in inline_result.document_model["document"]["blocks"])
    assert any(diagnostic.code == "unknown_inline_token" for diagnostic in inline_result.diagnostics)
    unknown_diagnostic = next(diagnostic for diagnostic in inline_result.diagnostics if diagnostic.code == "unknown_inline_token")
    legacy_diagnostic = next(item for item in inline_result.source_audit["unsupported_tokens"] if item["type"] == "unknown_inline_token")
    assert unknown_diagnostic.token_type == legacy_diagnostic["token_type"] == "plugin_inline"


def test_loose_list_paragraphs_preserve_combined_inline_spans(tmp_path: Path) -> None:
    result = recognize_markdown(
        MarkdownSource(tmp_path / "loose-list.md", "# 标题\n\n- first **bold**\n\n  second *em*\n"),
        template_version="test",
    )
    item = next(block for block in result.document_model["document"]["blocks"] if block["block_type"] == "list_item")

    assert item["text"] == "first bold second em"
    assert "".join(run["text"] for run in item["inline_runs"]) == item["text"]
    assert item["inline_runs"] == [
        {"text": "first "}, {"text": "bold", "marks": ["strong"]}, {"text": " "},
        {"text": "second "}, {"text": "em", "marks": ["emphasis"]},
    ]


def test_missing_local_image_code_and_adapter_contract_are_json_serializable(tmp_path: Path) -> None:
    source = tmp_path / "contract.md"
    source.write_text("![图](missing.png)\n", encoding="utf-8")
    result = recognize_markdown(MarkdownSource.from_input(source), template_version="test")
    report: dict = {}
    parse_md_to_model(source, report, skill_version=lambda: "test")

    assert any(diagnostic.code == "missing_local_image" for diagnostic in result.diagnostics)
    assert report["markdown_recognition_contract"] == result.preservation_contract
    json.dumps(report["markdown_recognition_contract"], ensure_ascii=False)
