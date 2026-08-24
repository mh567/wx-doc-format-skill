from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from md_pipeline import audit_markdown_preservation, parse_md_to_model
from model_normalization import normalize_document_model, summarize_source_document_model
from reporting import new_report


def _parse(tmp_path: Path, text: str) -> tuple[dict, dict]:
    source = tmp_path / "source.md"
    source.write_text(text, encoding="utf-8")
    report = new_report("test")
    return parse_md_to_model(source, report, skill_version=lambda: "test"), report


def test_markdown_tokens_preserve_lists_tables_and_title(tmp_path: Path) -> None:
    model, report = _parse(
        tmp_path,
        """# 文档标题

# 第二个一级标题
## 二级标题

1. 有序一级
   - 无序二级
     * 无序三级
2. 有序一级二

- 横线项目
* 星号项目
+ 加号项目

名称 | 值
:--- | ---:
甲 | `代码` **强调** [链接](https://example.com)
""",
    )
    blocks = model["document"]["blocks"]

    titles = [block for block in blocks if block.get("role") == "title"]
    headings = [block for block in blocks if block.get("block_type") == "heading"]
    items = [block for block in blocks if block.get("block_type") == "list_item"]
    table = next(block for block in blocks if block.get("block_type") == "table")

    assert [block["text"] for block in titles] == ["文档标题"]
    assert [block["text"] for block in headings] == ["文档标题", "第二个一级标题", "二级标题"]
    assert [block["level"] for block in items] == [0, 1, 2, 0, 0, 0, 0]
    assert [block["text"] for block in items] == ["有序一级", "无序二级", "无序三级", "有序一级二", "横线项目", "星号项目", "加号项目"]
    assert table["header_rows"] == 1
    assert [[cell["text"] for cell in row] for row in table["rows"]] == [
        ["名称", "值"],
        ["甲", "代码 强调 链接 (https://example.com)"],
    ]
    assert report["source_lists"]["detected"] == 7
    assert report["markdown_source_audit"]["table_header_rows"] == 1


def test_markdown_tokens_merge_soft_lines_and_handle_setext_quote_fence(tmp_path: Path) -> None:
    model, report = _parse(
        tmp_path,
        """标题
====

正文第一行
第二行

> 引用 *内容*

```json
{"key": "value"}
```
""",
    )
    blocks = model["document"]["blocks"]

    assert blocks[0]["block_type"] == "heading"
    assert blocks[0]["role"] == "title"
    assert blocks[0]["text"] == "标题"
    assert blocks[1]["text"] == "正文第一行 第二行"
    assert blocks[2]["role"] == "quote"
    assert blocks[2]["text"] == "引用 内容"
    assert blocks[3]["role"] == "code_block"
    assert blocks[3]["text"] == '{"key": "value"}\n'
    assert report["markdown_source_audit"]["fence_blocks"] == 1
    assert report["markdown_source_audit"]["blockquotes"] == 1


def test_markdown_preservation_audit_detects_lost_list_item(tmp_path: Path) -> None:
    source_model, report = _parse(tmp_path, "# 标题\n\n1. 第一项\n2. 第二项\n")
    normalized_model = deepcopy(source_model)
    normalized_model["document"]["blocks"].pop()
    report["audit"] = {"markdown_residue": []}

    result = audit_markdown_preservation(source_model, normalized_model, report)

    assert result["source_ast"]["list_items"] == 2
    assert result["normalized_ast"]["list_items"] == 1
    assert result["passed"] is False


def test_markdown_token_containers_and_unsupported_content_are_readable(tmp_path: Path) -> None:
    model, report = _parse(
        tmp_path,
        """> # 引用标题
> - 引用列项

    1 code line

- [x] 已完成事项
- [ ] 待办事项

<aside>HTML 内容</aside>

![远程图片](https://example.com/image.png)

前文 ![混合图片](assets/example.png) 后文

- # 列项标题
""",
    )
    blocks = model["document"]["blocks"]
    text = "\n".join(str(block.get("text") or "") for block in blocks)
    assert not [block for block in blocks if block.get("role") == "title"]
    assert any(block.get("role") == "quote" and block.get("text") == "引用标题" for block in blocks)
    assert any(block.get("role") == "code_block" and block.get("text") == "1 code line\n" for block in blocks)
    assert "https://example.com/image.png" in text
    assert "assets/example.png" in text
    assert any(block.get("source", {}).get("task_state") == "x" for block in blocks)
    assert any(item["type"] == "remote_image" for item in report["markdown_source_audit"]["unsupported_tokens"])
    assert any(item["type"] in {"html_block", "html_inline"} for item in report["markdown_source_audit"]["unsupported_tokens"])
    assert any(item["type"] == "list_item_heading" for item in report["markdown_source_audit"]["unsupported_tokens"])


def test_markdown_token_code_and_quote_roles_survive_normalization(tmp_path: Path) -> None:
    source_model, report = _parse(tmp_path, "> # 引用标题\n\n    1 code line\n")
    summarize_source_document_model(report, source_model)
    normalized_model = normalize_document_model(source_model, report)
    blocks = normalized_model["document"]["blocks"]

    assert report["source_document_model_summary"]["block_count"] == 2
    assert report["source_document_model_issues"] == []
    assert [(block.get("role"), block.get("text")) for block in blocks] == [
        ("quote", "引用标题"),
        ("code_block", "1 code line\n"),
    ]


def test_list_nested_quote_keeps_a_separate_quote_block(tmp_path: Path) -> None:
    model, report = _parse(tmp_path, "- 父列项\n  > 引用子块\n\n> - 引用中的列项\n")
    blocks = model["document"]["blocks"]
    assert [(block["block_type"], block.get("role"), block.get("text")) for block in blocks] == [
        ("list_item", None, "父列项"),
        ("body", "quote", "引用子块"),
        ("list_item", None, "引用中的列项"),
    ]
    assert blocks[1]["source"]["container_quote_depth"] == 1
    assert blocks[2]["source"]["container_quote_depth"] == 1


def test_rendered_model_semantic_mutations_fail_preservation_audit(tmp_path: Path) -> None:
    source_model, report = _parse(tmp_path, "# 标题\n\n正文\n\n1. 列项\n\n表 | 值\n--- | ---\n甲 | 乙\n\n```\ncode\n```\n\n> 引用\n")
    normalized_model = deepcopy(source_model)
    rendered_model = deepcopy(normalized_model)
    for block in rendered_model["document"]["blocks"]:
        if block["block_type"] == "heading":
            block["text"] = "篡改标题"
        elif block["block_type"] == "body":
            block["text"] = "篡改正文"
        elif block["block_type"] == "list_item":
            block["text"] = "篡改列项"
        elif block["block_type"] == "table":
            block["rows"][0][0]["text"] = "篡改表格"
    report["audit"] = {"markdown_residue": []}

    result = audit_markdown_preservation(source_model, normalized_model, report, rendered_model)

    assert result["rendered_semantic_mismatch"] is True
    assert result["passed"] is False


def test_markdown_preservation_audit_detects_table_and_list_semantic_changes(tmp_path: Path) -> None:
    source_model, report = _parse(tmp_path, "# 标题\n\n1. 项目\n   - 子项\n\n列 | 值\n--- | ---\n甲 | 乙\n")
    normalized_model = deepcopy(source_model)
    table = next(block for block in normalized_model["document"]["blocks"] if block["block_type"] == "table")
    table["rows"][0][0]["text"] = "错误表头"
    lists = [block for block in normalized_model["document"]["blocks"] if block["block_type"] == "list_item"]
    lists[1]["level"] = 0
    report["audit"] = {"markdown_residue": []}

    result = audit_markdown_preservation(source_model, normalized_model, report)

    assert result["table_matrix_mismatches"] == [1]
    assert result["list_shape_mismatches"] == [2]
    assert result["passed"] is False


def test_markdown_preservation_audit_detects_cross_type_order_swaps(tmp_path: Path) -> None:
    source_model, report = _parse(tmp_path, "# 标题\n\n正文\n\n列 | 值\n--- | ---\n甲 | 乙\n")
    normalized_model = deepcopy(source_model)
    blocks = normalized_model["document"]["blocks"]
    blocks[0], blocks[1] = blocks[1], blocks[0]
    report["audit"] = {"markdown_residue": []}

    heading_body_swap = audit_markdown_preservation(source_model, normalized_model, report)
    assert heading_body_swap["source_normalized_semantic_mismatch"] is True
    assert heading_body_swap["passed"] is False

    rendered_model = deepcopy(source_model)
    blocks = rendered_model["document"]["blocks"]
    table_index = next(index for index, block in enumerate(blocks) if block["block_type"] == "table")
    blocks[1], blocks[table_index] = blocks[table_index], blocks[1]
    paragraph_table_swap = audit_markdown_preservation(source_model, source_model, report, rendered_model)
    assert paragraph_table_swap["rendered_semantic_mismatch"] is True
    assert paragraph_table_swap["passed"] is False
