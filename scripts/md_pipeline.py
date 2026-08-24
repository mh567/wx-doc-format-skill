from __future__ import annotations

from typing import Callable

from markdown_recognition import MarkdownSource, canonical_semantic_sequence, recognize_markdown


def model_list_type_for_kind(kind: str) -> str:
    """Map a legacy text-marker kind onto the stable AST list type name."""
    mapping = {
        "letter": "lower_letter_paren",
        "decimal": "decimal_paren",
        "dash": "dash",
        "bullet2": "bullet_dot",
    }
    return mapping.get(kind, "lower_letter_paren")


def parse_md_to_model(src, report: dict, *, skill_version: Callable[[], str]) -> dict:
    """Compatibility adapter that projects pure recognition into legacy reports."""
    result = recognize_markdown(
        MarkdownSource.from_input(src), template_version=skill_version(),
    )
    report["markdown_source_audit"] = result.source_audit
    report["parse_report"] = result.parse_observations
    report["source_lists"] = result.source_lists
    report["tables_processed"] = result.tables_processed
    report["inferred_lists"] = list(result.parse_observations["inferred_lists"])
    report["markdown_recognition_contract"] = result.preservation_contract
    if result.title_decision.get("code") == "markdown_title_generated":
        report["markdown_title_audit"] = result.title_decision
    return result.document_model


def audit_markdown_preservation(source_model: dict | None, normalized_model: dict | None, report: dict, rendered_model: dict | None = None) -> dict:
    """Check token-derived source semantics across every later model boundary."""
    source = report.get("markdown_source_audit", {})
    source_blocks = (source_model or {}).get("document", {}).get("blocks", [])
    normalized_blocks = (normalized_model or {}).get("document", {}).get("blocks", [])
    rendered_blocks = (rendered_model or {}).get("document", {}).get("blocks", [])

    def selected(blocks, block_type=None, role=None):
        return [
            block for block in blocks
            if (block_type is None or block.get("block_type") == block_type)
            and (role is None or block.get("role") == role)
        ]

    def table_matrix(block):
        return [
            [(cell.get("text", ""), cell.get("cell_role", ""), cell.get("inline_runs")) for cell in row]
            for row in block.get("rows", [])
        ]

    def list_shape(block):
        numbering = block.get("source", {}).get("numbering", {})
        return (
            block.get("text"), block.get("level"), numbering.get("family"),
            block.get("restart"), block.get("parent_list_item_id"),
        )

    def headings(blocks):
        return [
            (block.get("role"), block.get("level"), block.get("text"))
            for block in selected(blocks, "heading")
        ]

    source_tables = selected(source_blocks, "table")
    normalized_tables = selected(normalized_blocks, "table")
    rendered_tables = selected(rendered_blocks, "table")
    table_matrix_mismatches = [
        index + 1 for index, (before, after) in enumerate(zip(source_tables, normalized_tables))
        if table_matrix(before) != table_matrix(after) or before.get("header_rows") != after.get("header_rows")
    ]
    list_shape_mismatches = [
        index + 1 for index, (before, after) in enumerate(zip(selected(source_blocks, "list_item"), selected(normalized_blocks, "list_item")))
        if list_shape(before) != list_shape(after)
    ]
    token_inventory = source.get("token_inventory", {})
    token_inventory_mismatch = {
        "list_item_open": (token_inventory.get("list_item_open", 0), len(selected(source_blocks, "list_item"))),
        "table_open": (token_inventory.get("table_open", 0), len(source_tables)),
        "code_blocks": (token_inventory.get("fence", 0) + token_inventory.get("code_block", 0), len(selected(source_blocks, role="code_block"))),
    }
    token_inventory_mismatch = {key: value for key, value in token_inventory_mismatch.items() if value[0] != value[1]}
    rendered_image_relation_failures = [
        index + 1 for index, block in enumerate(selected(rendered_blocks, "image"))
        if block.get("source", {}).get("image_relationship_valid") is not True
    ] if rendered_model is not None else []
    normalized_counts = {
        "titles": len(selected(normalized_blocks, role="title")),
        "headings": len(selected(normalized_blocks, "heading")),
        "list_items": len(selected(normalized_blocks, "list_item")),
        "tables": len(normalized_tables),
    }
    result = {
        "source": source,
        "source_ast": {
            "titles": len(selected(source_blocks, role="title")),
            "headings": len(selected(source_blocks, "heading")),
            "list_items": len(selected(source_blocks, "list_item")),
            "tables": len(source_tables),
            "table_rows": sum(len(block.get("rows", [])) for block in source_tables),
        },
        "normalized_ast": normalized_counts,
        "rendered_model": {
            "titles": len(selected(rendered_blocks, role="title")),
            "headings": len(selected(rendered_blocks, "heading")),
            "list_items": len(selected(rendered_blocks, "list_item")),
            "tables": len(rendered_tables),
        },
        "heading_text_mismatch": headings(source_blocks) != headings(normalized_blocks),
        "table_matrix_mismatches": table_matrix_mismatches,
        "table_count_mismatch": len(source_tables) != len(normalized_tables),
        "list_shape_mismatches": list_shape_mismatches,
        "list_count_mismatch": len(selected(source_blocks, "list_item")) != len(selected(normalized_blocks, "list_item")),
        "token_inventory_mismatch": token_inventory_mismatch,
        "rendered_image_relation_failures": rendered_image_relation_failures,
        "rendered_model_mismatch": {
            key: (normalized_counts[key], rendered)
            for key, rendered in {
                "titles": len(selected(rendered_blocks, role="title")),
                "headings": len(selected(rendered_blocks, "heading")),
                "list_items": len(selected(rendered_blocks, "list_item")),
                "tables": len(rendered_tables),
            }.items() if rendered_model is not None and normalized_counts[key] != rendered
        },
        "source_normalized_semantic_mismatch": canonical_semantic_sequence(source_model) != canonical_semantic_sequence(normalized_model),
        "rendered_semantic_mismatch": canonical_semantic_sequence(normalized_model) != canonical_semantic_sequence(rendered_model) if rendered_model is not None else False,
        "markdown_residue": report.get("audit", {}).get("markdown_residue", []),
        "unsupported_tokens": source.get("unsupported_tokens", []),
        "semantic_sequence": {
            "source": canonical_semantic_sequence(source_model),
            "normalized": canonical_semantic_sequence(normalized_model),
            "rendered": canonical_semantic_sequence(rendered_model),
        },
    }
    before, after = result["source_ast"], result["normalized_ast"]
    result["passed"] = (
        before["titles"] == after["titles"] == 1 and not result["heading_text_mismatch"]
        and not result["table_count_mismatch"] and not result["table_matrix_mismatches"]
        and not result["list_count_mismatch"] and not result["list_shape_mismatches"]
        and not result["token_inventory_mismatch"] and not result["rendered_model_mismatch"]
        and not result["rendered_image_relation_failures"]
        and not result["source_normalized_semantic_mismatch"] and not result["rendered_semantic_mismatch"]
        and not result["markdown_residue"] and not result["unsupported_tokens"]
    )
    return result
