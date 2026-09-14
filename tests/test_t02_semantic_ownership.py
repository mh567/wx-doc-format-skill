"""T02 focused contracts: one semantic owner per concept and no recompute.

Every test names behaviour the T02 ticket moves: a single live ``*_module()``
factory arm, one owner interface returning ``{result, candidates}``, downstream
consumers that read the published ``*_result`` instead of recomputing it, and a
real consumer for the ``list_item_map`` capability.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

from docx import Document


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf import semantic_modules
from wxdf.semantic_modules import native_semantic_modules
from wxdf.parsers import parse
from wxdf.semantics import captions as caption_semantics_module
from wxdf.semantics import paragraphs as paragraph_semantics_module
from wxdf.semantics.appendices import (
    collect_source_appendix_semantics,
    prepare_native_appendix_semantics,
    recognize_appendix_semantics,
)
from wxdf.semantics.captions import (
    collect_source_caption_semantics,
    recognize_caption_semantics,
)
from wxdf.semantics.lists import recognize_list_semantics
from wxdf.semantics.source_ast import build_source_ast
from wxdf.semantics.source_projection import project_source_facts
from wxdf.semantics.tables import (
    collect_source_table_semantics,
    recognize_table_semantics,
)


FACTORIES = (
    semantic_modules.source_facts_module,
    semantic_modules.document_structure_module,
    semantic_modules.list_semantics_module,
    semantic_modules.table_semantics_module,
    semantic_modules.caption_semantics_module,
    semantic_modules.appendix_semantics_module,
)

RESULT_AND_CANDIDATE_MODULES = (
    "list-semantics",
    "table-semantics",
    "caption-semantics",
    "appendix-semantics",
)


def _write_caption_docx(destination: Path) -> Path:
    document = Document()
    document.add_paragraph("正文段落说明。")
    document.add_paragraph("表1 数据示例", style="Caption")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "信任评估"
    table.cell(1, 1).text = "行为分析"
    document.save(destination)
    return destination


def _write_appendix_list_docx(destination: Path) -> Path:
    document = Document()
    document.add_heading("系统概览", level=1)
    document.add_paragraph("正文段落。")
    document.add_heading("附录 A", level=1)
    document.add_paragraph("附录引言。")
    document.add_paragraph("第一项", style="List Number")
    document.add_paragraph("第二项", style="List Number")
    document.add_paragraph("结束语。")
    document.save(destination)
    return destination


def _write_owner_markdown(destination: Path) -> Path:
    destination.write_text(
        "\n".join(
            [
                "# 系统概览",
                "",
                "正文段落说明。",
                "",
                "1. 第一项",
                "2. 第二项",
                "",
                "| 项目 | 说明 |",
                "| --- | --- |",
                "| 信任评估 | 行为分析 |",
                "",
                "# 附录A 补充材料",
                "",
                "### A.1 术语表",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination


# ---------------------------------------------------------------------------
# One live factory arm per concept.
# ---------------------------------------------------------------------------


def test_semantic_factories_expose_a_single_descriptor_shape() -> None:
    for factory in FACTORIES:
        parameters = inspect.signature(factory).parameters
        assert "native" not in parameters, factory.__name__
        assert "protect_semantics" not in parameters, factory.__name__
        assert factory().descriptor == factory().descriptor


def test_native_semantic_modules_are_the_collapsed_factories() -> None:
    described = {
        module.descriptor.module_id: module.descriptor
        for module in native_semantic_modules()
    }
    for factory in FACTORIES:
        descriptor = factory().descriptor
        assert described[descriptor.module_id] == descriptor

    for module_id in RESULT_AND_CANDIDATE_MODULES:
        provides = described[module_id].provides
        assert any(name.endswith("_result") for name in provides), module_id
        assert any(name.endswith("_candidates") for name in provides), module_id


# ---------------------------------------------------------------------------
# One owner interface per concept returning {result, candidates}.
# ---------------------------------------------------------------------------


def test_each_concept_owner_returns_result_and_candidates(tmp_path: Path) -> None:
    facts = parse(_write_owner_markdown(tmp_path / "owner.md"))
    source_ast = build_source_ast(facts)

    lists = recognize_list_semantics(facts, source_ast)
    assert {"result", "candidates", "item_map"} <= set(lists)

    tables = recognize_table_semantics(facts, source_ast)
    assert {"result", "candidates"} <= set(tables)

    captions = recognize_caption_semantics(facts, source_ast, tables["result"])
    assert {"result", "candidates"} <= set(captions)

    appendices = recognize_appendix_semantics(facts, source_ast, lists["item_map"])
    assert {"result", "candidates"} <= set(appendices)


# ---------------------------------------------------------------------------
# Downstream consumers read the published result instead of recomputing it.
# ---------------------------------------------------------------------------


def test_paragraph_candidates_read_the_published_caption_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    facts = parse(_write_caption_docx(tmp_path / "paragraphs.docx"))
    source_ast = build_source_ast(facts)
    published = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )
    baseline = paragraph_semantics_module.collect_paragraph_candidates(facts, source_ast)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("paragraph semantics recomputed caption semantics")

    monkeypatch.setattr(
        caption_semantics_module,
        "collect_source_caption_semantics",
        forbidden,
    )
    candidates = paragraph_semantics_module.collect_paragraph_candidates(
        facts,
        source_ast,
        caption_semantics_result=published,
    )

    assert candidates == baseline


def test_caption_candidates_read_the_published_caption_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    facts = parse(_write_caption_docx(tmp_path / "captions.docx"))
    source_ast = build_source_ast(facts)
    published = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )
    baseline = caption_semantics_module.collect_caption_candidates(source_ast, facts)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("caption candidates recomputed caption semantics")

    monkeypatch.setattr(
        caption_semantics_module,
        "collect_source_caption_semantics",
        forbidden,
    )
    candidates = caption_semantics_module.collect_caption_candidates(
        source_ast,
        facts,
        caption_semantics=published,
    )

    assert candidates == baseline
    assert candidates


def test_appendix_module_requires_the_list_item_map_capability() -> None:
    described = {
        module.descriptor.module_id: module.descriptor
        for module in native_semantic_modules()
    }

    assert "list_item_map" in described["appendix-semantics"].requires
    assert "list_item_map" in described["list-semantics"].provides


def test_appendix_reads_the_published_list_item_map(tmp_path: Path) -> None:
    facts = parse(_write_appendix_list_docx(tmp_path / "appendices.docx"))
    source_ast = build_source_ast(facts)
    item_map = recognize_list_semantics(facts, source_ast)["item_map"]
    assert item_map, "the fixture contributes no list items"

    semantics = collect_source_appendix_semantics(facts)
    prepared = prepare_native_appendix_semantics(facts, source_ast, semantics, item_map)
    unmapped = prepare_native_appendix_semantics(facts, source_ast, semantics, None)

    mapped_nodes = [list(range_["root_node_ids"]) for range_ in prepared["ranges"]]
    unmapped_nodes = [list(range_["root_node_ids"]) for range_ in unmapped["ranges"]]

    assert mapped_nodes != unmapped_nodes
    assert any(set(node_ids) & set(item_map.values()) for node_ids in mapped_nodes)
