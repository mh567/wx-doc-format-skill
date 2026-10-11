"""T02 focused tests: native parsing and semantic ownership.

These tests are the working set for T02.  They pin the claim that the native
v2 chain owns SourceFacts, the Source AST, the normalized AST and the saved
artifact on its own, and that the legacy parser, normalizer, finalizer, source
projection bridge and legacy pipeline stay reachable only from legacy paths.

The frozen cross ticket contract lives in ``tests/test_native_skill_cutover.py``
and is not duplicated here.
"""

from __future__ import annotations

import base64
from pathlib import Path
import json
import os
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

TEMPLATE = ROOT / "assets" / "wx_template.docx"
BASELINE_MARKDOWN = ROOT / "input" / "test_baseline.md"
BENCHMARK_MARKDOWN = ROOT / "input" / "benchmark_report.md"

# The transitional seams T02 removes from the native call graph.  A native run
# that imports any of these has fallen back onto a legacy semantic path.
BLOCKED_LEGACY_MODULES = (
    "docx_pipeline",
    "md_pipeline",
    "model_normalization",
    "template_finalizer",
    "wxdf.legacy_pipeline",
    "wxdf.semantics.source_projection_bridge",
)

# One pixel PNG keeps the media fixture generated rather than committed.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _venv_python() -> Path:
    candidate = SCRIPTS / ".venv" / "bin" / "python3"
    return candidate if candidate.is_file() else Path(sys.executable)


def _cli_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("WX_DOC_FORMAT_RELEASE", None)
    environment.pop("WX_DOC_LLM_ENHANCE", None)
    environment["PYTHONPATH"] = str(SCRIPTS)
    return environment


def _blocker_source() -> list[str]:
    return [
        f"BLOCKED = {BLOCKED_LEGACY_MODULES!r}",
        "class _Blocker:",
        "    def find_spec(self, fullname, path=None, target=None):",
        "        if fullname in BLOCKED:",
        '            raise ImportError("WXDF-T02-BLOCKED:" + fullname)',
        "        return None",
        "sys.meta_path.insert(0, _Blocker())",
    ]


def _run_blocked_child(child_source: str, argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_venv_python()), "-c", child_source, *argv],
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
    )


def _blocked_native_cli(tmp_path: Path, source: Path) -> tuple[subprocess.CompletedProcess, dict[str, Path]]:
    """Run the public native CLI while the legacy seams are unavailable."""

    paths = {
        "output": tmp_path / "native-output.docx",
        "source_ast": tmp_path / "native-source-ast.json",
        "normalized_ast": tmp_path / "native-normalized-ast.json",
        "report": tmp_path / "native-report.json",
        "summary": tmp_path / "child-summary.json",
    }
    child = "\n".join(
        [
            "import json, sys",
            "sys.path.insert(0, sys.argv[1])",
            *_blocker_source(),
            'summary = {"import_error": None, "exit_code": None, "leaked": []}',
            "try:",
            "    import main",
            "except ImportError as exc:",
            "    summary['import_error'] = str(exc)",
            "else:",
            "    try:",
            "        main.main(sys.argv[3:])",
            "        summary['exit_code'] = 0",
            "    except SystemExit as exc:",
            "        summary['exit_code'] = exc.code if isinstance(exc.code, int) else 1",
            "    except ImportError as exc:",
            "    # a blocked import escaping the run is a native ownership failure",
            "        summary['import_error'] = str(exc)",
            "summary['leaked'] = [name for name in BLOCKED if name in sys.modules]",
            "with open(sys.argv[2], 'w', encoding='utf-8') as handle:",
            "    json.dump(summary, handle)",
        ]
    )
    completed = _run_blocked_child(
        child,
        [
            str(SCRIPTS),
            str(paths["summary"]),
            "--input", str(source),
            "--output", str(paths["output"]),
            "--template", str(TEMPLATE),
            "--llm-enhance", "off",
            "--report", str(paths["report"]),
            "--source-ast", str(paths["source_ast"]),
            "--ast", str(paths["normalized_ast"]),
        ],
    )
    assert paths["summary"].is_file(), (
        f"the blocked child wrote no summary: {completed.stdout[-300:]} {completed.stderr[-300:]}"
    )
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["import_error"] is None, (
        f"native-v2 touched a legacy seam: {summary['import_error']}"
    )
    assert summary["exit_code"] == 0, summary
    assert summary["leaked"] == [], summary["leaked"]
    return completed, paths


def _write_rich_markdown(destination: Path) -> Path:
    """Write a Markdown fixture covering text, media, list, table and appendix."""

    media = destination.parent / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "architecture.png").write_bytes(_ONE_PIXEL_PNG)
    destination.write_text(
        "\n".join(
            [
                "# 系统概览",
                "",
                "正文段落说明系统总体结构。",
                "",
                "![图1 系统架构](media/architecture.png)",
                "",
                "## 功能列表",
                "",
                "1. 身份管理",
                "2. 策略管理",
                "",
                "| 项目 | 说明 |",
                "| --- | --- |",
                "| 信任评估 | 基于行为分析 |",
                "",
                "# 附录A 补充材料",
                "",
                "### A.1 术语表",
                "",
                "补充说明内容。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination


def _write_authored_caption_markdown(destination: Path) -> Path:
    """Write Markdown with one authored table caption and one bare table."""

    destination.write_text(
        "\n".join(
            [
                "# 系统概览",
                "",
                "正文段落说明系统总体结构。",
                "",
                "表 1 服务接口",
                "",
                "| 项目 | 说明 |",
                "| --- | --- |",
                "| 信任评估 | 基于行为分析 |",
                "",
                "以下表格没有作者题注。",
                "",
                "| 参数 | 取值 |",
                "| --- | --- |",
                "| 超时 | 30 |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination


def _write_authored_figure_markdown(destination: Path) -> Path:
    """Write Markdown with an authored figure caption and a bare table."""

    media = destination.parent / "figure-media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "architecture.png").write_bytes(_ONE_PIXEL_PNG)
    destination.write_text(
        "\n".join(
            [
                "# 系统概览",
                "",
                "正文段落说明系统总体结构。",
                "",
                "![系统架构](figure-media/architecture.png)",
                "",
                "图 1 系统架构",
                "",
                "| 项目 | 说明 |",
                "| --- | --- |",
                "| 信任评估 | 基于行为分析 |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination


def _image_block_ids(ast: dict) -> list[str]:
    return [
        node_id
        for node_id, block in ast["document"]["blocks"].items()
        if block.get("block_type") == "image"
    ]


def _sorted_children(ast: dict, node_id: str) -> list[str]:
    return list(ast["document"]["children"].get(node_id, []))


def _inline_text(block: dict) -> str:
    return "".join(str(item.get("text") or "") for item in block.get("inline_content") or [])


def _write_rich_docx(destination: Path) -> Path:
    """Write a DOCX fixture covering heading, body, ordered list, table and image."""

    from docx import Document

    media = destination.parent / "docx-media"
    media.mkdir(parents=True, exist_ok=True)
    picture = media / "architecture.png"
    picture.write_bytes(_ONE_PIXEL_PNG)

    document = Document()
    document.add_paragraph("系统设计说明", style="Heading 1")
    document.add_paragraph("这是正文段落。")
    document.add_paragraph("第一条", style="List Number")
    document.add_paragraph("第二条", style="List Number")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "信任评估"
    table.cell(1, 1).text = "行为分析"
    document.add_picture(str(picture))
    document.save(destination)
    return destination


def _saved_ast(paths: dict[str, Path]) -> dict:
    return json.loads(paths["source_ast"].read_text(encoding="utf-8"))


def _normalized(paths: dict[str, Path]) -> dict:
    return json.loads(paths["normalized_ast"].read_text(encoding="utf-8"))


def _block_types(ast: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in ast["document"]["blocks"].values():
        kind = str(block.get("block_type"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _container_kinds(ast: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for container in ast["document"]["containers"].values():
        kind = str(container.get("kind"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _inline_kinds(ast: dict) -> set[str]:
    kinds: set[str] = set()
    for block in ast["document"]["blocks"].values():
        for item in block.get("inline_content") or []:
            if isinstance(item, dict) and item.get("kind"):
                kinds.add(str(item["kind"]))
    return kinds


def _associations(ast: dict) -> list[dict]:
    return list(ast["document"].get("associations") or [])


# ---------------------------------------------------------------------------
# Module level ownership: the native chain imports without the legacy seams.
# ---------------------------------------------------------------------------


def test_wxdf_template_loads_a_bundle_without_the_legacy_finalizer(tmp_path: Path) -> None:
    """``wxdf.template`` reads the template TOC OOXML on its own."""

    child = "\n".join(
        [
            "import json, sys",
            "sys.path.insert(0, sys.argv[1])",
            *_blocker_source(),
            "from wxdf.template import load_template_bundle",
            "bundle = load_template_bundle(sys.argv[2])",
            "summary = {",
            "    'fragments': len(bundle.toc_fragment_xml),",
            "    'max_level': bundle.presentation_contract['toc']['max_level'],",
            "    'source': bundle.presentation_contract['source']['sha256'],",
            "    'leaked': [name for name in BLOCKED if name in sys.modules],",
            "}",
            "sys.stdout.write(json.dumps(summary))",
        ]
    )
    completed = _run_blocked_child(child, [str(SCRIPTS), str(TEMPLATE)])
    assert completed.returncode == 0, completed.stderr[-400:]
    summary = json.loads(completed.stdout)
    assert summary["leaked"] == [], summary["leaked"]
    # The template content control plus its trailing page break.
    assert summary["fragments"] == 2, summary
    assert summary["max_level"] == 4, summary
    assert summary["source"], summary


def test_main_imports_while_the_legacy_seams_are_blocked() -> None:
    """Importing ``main`` must not require any legacy production module."""

    child = "\n".join(
        [
            "import json, sys",
            "sys.path.insert(0, sys.argv[1])",
            *_blocker_source(),
            "try:",
            "    import main",
            "    error = None",
            "except ImportError as exc:",
            "    error = str(exc)",
            "sys.stdout.write(json.dumps({",
            "    'error': error,",
            "    'leaked': [name for name in BLOCKED if name in sys.modules],",
            "}))",
        ]
    )
    completed = _run_blocked_child(child, [str(SCRIPTS)])
    assert completed.returncode == 0, completed.stderr[-400:]
    summary = json.loads(completed.stdout)
    assert summary["error"] is None, summary["error"]
    assert summary["leaked"] == [], summary["leaked"]


def test_retired_legacy_helpers_are_gone_from_the_cli() -> None:
    """The legacy entry points were deleted, not deferred."""

    import main

    for name in (
        "parse_docx_to_model",
        "parse_md_to_model",
        "audit_markdown_preservation",
        "normalize_document_model",
        "summarize_source_document_model",
        "render_docx_direct",
        "apply_template_finalizer",
        "extract_template_toc_fragment",
        "_deferred_legacy_callable",
    ):
        assert not hasattr(main, name), name


# ---------------------------------------------------------------------------
# Native ownership of SourceFacts, Source AST, normalized AST and the artifact.
# ---------------------------------------------------------------------------


def test_native_markdown_owns_text_table_and_caption_semantics(tmp_path: Path) -> None:
    _completed, paths = _blocked_native_cli(tmp_path, BENCHMARK_MARKDOWN)
    source = _saved_ast(paths)
    normalized = _normalized(paths)

    assert source["schema_version"] == "2.0"
    assert normalized["source_ast"] == source
    assert _block_types(source).get("table", 0) >= 1
    assert _block_types(source).get("paragraph", 0) >= 1
    assert "text" in _inline_kinds(source)

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["passed"] is True
    assert report["native_transaction"]["status"] in {"committed", "already_committed"}

    captions = [
        block_id
        for block_id, block in source["document"]["blocks"].items()
        if block.get("role") == "caption"
    ]
    assert captions, "the native Source AST carries no caption block for a data table"
    targets = {str(item.get("target_id")) for item in _associations(source) if item.get("kind") == "caption_target"}
    assert targets, _associations(source)
    for caption_id in captions:
        block = source["document"]["blocks"][caption_id]
        assert str(block.get("target_id")) in targets
        assert source["document"]["blocks"][str(block.get("target_id"))]["block_type"] == "table"
        assert block.get("caption_type") == "table"


def test_native_media_and_appendix_own_their_semantics(tmp_path: Path) -> None:
    source = _write_rich_markdown(tmp_path / "media-and-appendix.md")
    _completed, paths = _blocked_native_cli(tmp_path, source)
    ast = _saved_ast(paths)
    normalized = _normalized(paths)

    assert normalized["source_ast"] == ast
    assert "image" in _inline_kinds(ast), _inline_kinds(ast)
    assert _container_kinds(ast).get("list", 0) >= 1
    assert _container_kinds(ast).get("appendix", 0) >= 1
    assert _block_types(ast).get("table", 0) >= 1

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["passed"] is True


def test_native_docx_owns_its_structure(tmp_path: Path) -> None:
    source = _write_rich_docx(tmp_path / "rich-fixture.docx")
    _completed, paths = _blocked_native_cli(tmp_path, source)
    ast = _saved_ast(paths)
    normalized = _normalized(paths)

    assert normalized["source_ast"] == ast
    assert _container_kinds(ast).get("list_item", 0) >= 1
    assert _container_kinds(ast).get("table_row", 0) >= 1
    assert _block_types(ast).get("paragraph", 0) >= 1
    assert "image" in _inline_kinds(ast), _inline_kinds(ast)

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["passed"] is True


def _document_xml(path: Path) -> str:
    import zipfile

    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _caption_blocks(ast: dict) -> dict[str, dict]:
    return {
        block_id: block
        for block_id, block in ast["document"]["blocks"].items()
        if block.get("role") == "caption"
    }


def _materialized_caption_ast(source: Path) -> dict:
    from wxdf.parsers import parse
    from wxdf.resolver import resolve
    from wxdf.semantics.captions import collect_caption_candidates
    from wxdf.semantics.source_ast import build_source_ast
    from wxdf.transaction import apply_stage_batch, make_snapshot

    facts = parse(source)
    baseline = build_source_ast(facts)
    candidates = [
        candidate
        for candidate in collect_caption_candidates(baseline, facts)
        if str(candidate.get("candidate_id") or "").startswith(
            ("caption-role-", "caption-content-", "caption-image-", "caption-association-")
        )
        and not str(candidate.get("candidate_id") or "").startswith(
            "caption-association-native-caption-"
        )
    ]
    decision_set = resolve(
        candidates,
        {"revision": baseline["revision"]},
    )
    assert decision_set["unresolved"] == []
    return apply_stage_batch(
        make_snapshot(baseline),
        decision_set["decisions"],
        expected_revision=baseline["revision"],
    ).snapshot.ast


def _data_tables(ast: dict) -> list[str]:
    return [
        block_id
        for block_id, block in ast["document"]["blocks"].items()
        if block.get("block_type") == "table" and block.get("table_type") == "data"
    ]


def test_native_generates_one_sequence_caption_per_data_table(tmp_path: Path) -> None:
    """A caption eligible table gets a generated caption node and a SEQ field."""

    _completed, paths = _blocked_native_cli(tmp_path, BENCHMARK_MARKDOWN)
    source = _saved_ast(paths)
    captions = _caption_blocks(source)
    tables = _data_tables(source)

    assert tables, "the fixture declares no data table"
    assert len(captions) == len(tables), (sorted(captions), tables)
    for caption_id, block in captions.items():
        assert block["caption_type"] == "table"
        assert block["target_id"] in tables
        # A deterministic addition carries its provenance.
        assert block["generated_by"] == "caption_semantics.sequence_caption"
        assert block["generation_basis"] == [block["target_id"]]
        # The caption sits immediately above its own table.
        parent_id = next(
            parent
            for parent, child_ids in source["document"]["children"].items()
            if caption_id in child_ids
        )
        siblings = source["document"]["children"][parent_id]
        assert siblings[siblings.index(caption_id) + 1] == block["target_id"]

    xml = _document_xml(paths["output"])
    assert xml.count("SEQ 表") == len(captions), xml.count("SEQ 表")
    for caption_id in captions:
        assert f"native-caption-" in caption_id


def test_native_caption_output_matches_the_legacy_sequence_contract(tmp_path: Path) -> None:
    """The rendered caption carries the template style, prefix and SEQ field."""

    import re

    from docx import Document

    from wxdf.template import load_template_bundle

    _completed, paths = _blocked_native_cli(tmp_path, BENCHMARK_MARKDOWN)

    bundle = load_template_bundle(TEMPLATE)
    caption_style = bundle.presentation_contract["styles"]["resolved_roles"]["caption"]
    caption_style_id = Document(str(TEMPLATE)).styles[caption_style].style_id

    xml = _document_xml(paths["output"])
    paragraphs = [
        match.group(0)
        for match in re.finditer(r"<w:p\b.*?</w:p>", xml, re.S)
        if "SEQ 表" in match.group(0)
    ]
    assert paragraphs
    for paragraph in paragraphs:
        flattened = re.sub(r"\s+", " ", paragraph)
        assert f'<w:pStyle w:val="{caption_style_id}"/>' in flattened, flattened
        assert "SEQ 表 \\* ARABIC" in flattened
        assert '<w:t xml:space="preserve">表 </w:t>' in flattened
        assert "<w:t>1</w:t>" in flattened.replace(" ", "")

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["passed"] is True
    assert report["saved_output_audit"]["issues"] == []


def test_source_ast_materializes_an_authored_table_caption(tmp_path: Path) -> None:
    """An authored caption becomes an explicit caption block with a target."""

    source = _write_authored_caption_markdown(tmp_path / "authored.md")
    ast = _materialized_caption_ast(source)
    captions = _caption_blocks(ast)

    assert len(captions) == 1, sorted(captions)
    caption_id, block = next(iter(captions.items()))
    assert block["caption_type"] == "table"
    assert ast["document"]["blocks"][block["target_id"]]["block_type"] == "table"
    # The authored block keeps its source reference and its visible content.
    assert block["source_ref"], block
    assert _inline_text(block) == "表 1 服务接口", block["inline_content"]
    assert _associations(ast) == [
        {
            "kind": "caption_target",
            "source_id": caption_id,
            "target_id": block["target_id"],
        }
    ]


def test_caption_association_candidate_carries_the_target_source_refs() -> None:
    """A caption association precondition carries the target reference."""

    from wxdf.parsers import parse
    from wxdf.semantics.captions import collect_caption_candidates
    from wxdf.semantics.source_ast import build_source_ast

    ast = build_source_ast(parse(BENCHMARK_MARKDOWN))
    associations = [
        candidate
        for candidate in collect_caption_candidates(ast)
        if candidate["proposal"]["operation"] == "update_association"
    ]

    assert associations, "a bare data table produced no caption association candidate"
    for candidate in associations:
        assert candidate["target_source_refs"], candidate["candidate_id"]
        target_id = candidate["target_node_ids"][0]
        assert candidate["target_source_refs"] == [
            ast["document"]["blocks"][target_id]["source_ref"]
        ]


def test_an_authored_caption_suppresses_generation_for_its_target(tmp_path: Path) -> None:
    """A captioned target gets no second, generated caption node."""

    from wxdf.semantics.captions import collect_caption_candidates

    source = _write_authored_caption_markdown(tmp_path / "authored.md")
    ast = _materialized_caption_ast(source)
    authored = _caption_blocks(ast)
    created = [
        candidate
        for candidate in collect_caption_candidates(ast)
        if candidate["proposal"]["operation"] == "create_node"
    ]
    generated_node_ids = {candidate["proposal"]["value"]["node_id"] for candidate in created}
    generated_target_ids = {
        candidate["proposal"]["value"]["node"]["target_id"] for candidate in created
    }

    assert not (generated_node_ids & set(authored)), generated_node_ids
    assert not (generated_target_ids & {block["target_id"] for block in authored.values()})
    # The bare table in the same document still gets a generated caption.
    assert len(created) == 1, [candidate["candidate_id"] for candidate in created]
    assert not [node_id for node_id in ast["document"]["blocks"] if node_id.startswith("native-caption-")]


def test_authored_caption_text_reaches_the_saved_output(tmp_path: Path) -> None:
    """The authored caption renders once, without a duplicated label."""

    source = _write_authored_caption_markdown(tmp_path / "authored.md")
    _completed, paths = _blocked_native_cli(tmp_path, source)
    xml = _document_xml(paths["output"])

    # One authored caption and one generated caption.
    assert xml.count("SEQ 表") == 2, xml.count("SEQ 表")
    assert xml.count("服务接口") == 1, xml.count("服务接口")
    # The authored label is replaced by the label run and the sequence field.
    assert xml.count("表 1 服务接口") == 0
    assert re.search(r"<w:t>表</w:t>", xml), "the table label run is missing"

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["passed"] is True
    assert report["saved_output_audit"]["issues"] == []
    evidence = report["native_module_evidence"]["caption-semantics"]
    assert evidence["caption_count"] == 1, evidence
    assert evidence["generated_caption_count"] == 1, evidence


@pytest.mark.parametrize("source_kind", ["markdown", "docx"])
def test_inline_images_become_explicit_image_blocks(tmp_path: Path, source_kind: str) -> None:
    """An inline image gets an explicit Source AST node with its own reference."""

    from wxdf.parsers import parse
    from wxdf.semantics.source_ast import build_source_ast

    if source_kind == "markdown":
        source = _write_authored_figure_markdown(tmp_path / "figure.md")
    else:
        source = _write_rich_docx(tmp_path / "rich.docx")
    ast = build_source_ast(parse(source))
    documents = ast["document"]
    images = _image_block_ids(ast)

    assert len(images) == 1, images
    block = documents["blocks"][images[0]]
    resource = documents["resources"][block["resource_id"]]
    assert resource["kind"] == "image"
    assert str(block["alt_text"]).strip(), block
    # The image node carries the reference of the image part it came from.
    assert block["source_ref"] == resource["source_ref"], block["source_ref"]
    # It records the source paragraph and inline position it came from.
    assert block["generated_by"] == "source_ast.inline_image"
    source_id, inline_index = block["generation_basis"]
    # The source paragraph keeps its text, its inline image and its order.
    paragraph = documents["blocks"][source_id]
    assert paragraph["block_type"] == "paragraph"
    item = paragraph["inline_content"][inline_index]
    assert item["kind"] == "image"
    assert item["resource_id"] == block["resource_id"]
    # The image node follows the paragraph that carries the inline token.
    parents = [
        parent for parent, children in documents["children"].items() if images[0] in children
    ]
    assert len(parents) == 1, parents
    siblings = _sorted_children(ast, parents[0])
    assert siblings[siblings.index(source_id) + 1] == images[0], siblings


def test_authored_figure_caption_is_materialized_after_its_image(tmp_path: Path) -> None:
    """An authored figure caption binds to the image and follows it."""

    source = _write_authored_figure_markdown(tmp_path / "figure.md")
    ast = _materialized_caption_ast(source)
    images = _image_block_ids(ast)
    captions = _caption_blocks(ast)

    assert len(images) == 1, images
    assert len(captions) == 1, sorted(captions)
    caption_id, block = next(iter(captions.items()))
    assert block["caption_type"] == "figure"
    assert block["target_id"] == images[0]
    # The authored caption keeps its visible content and its source reference.
    assert block["source_ref"], block
    assert _inline_text(block) == "图 1 系统架构", block["inline_content"]
    # It sits directly after the image it captions.
    siblings = _sorted_children(ast, "root")
    assert siblings.index(caption_id) == siblings.index(images[0]) + 1, siblings
    assert _associations(ast) == [
        {"kind": "caption_target", "source_id": caption_id, "target_id": images[0]}
    ]


def test_an_authored_figure_caption_suppresses_generation_for_its_image(
    tmp_path: Path,
) -> None:
    """A captioned image gets no second, generated caption node."""

    from wxdf.semantics.captions import collect_caption_candidates

    source = _write_authored_figure_markdown(tmp_path / "figure.md")
    ast = _materialized_caption_ast(source)
    images = _image_block_ids(ast)
    created = [
        candidate
        for candidate in collect_caption_candidates(ast)
        if candidate["proposal"]["operation"] == "create_node"
    ]
    generated_targets = {
        candidate["proposal"]["value"]["node"]["target_id"] for candidate in created
    }

    assert images[0] not in generated_targets
    # The bare table in the same document still gets a generated caption.
    assert len(created) == 1, [candidate["candidate_id"] for candidate in created]
    assert not [
        node_id
        for node_id in ast["document"]["blocks"]
        if str(node_id).startswith("native-caption-")
    ]


@pytest.mark.parametrize("source_kind", ["markdown", "docx"])
def test_uncaptioned_image_gets_one_generated_figure_caption(
    tmp_path: Path, source_kind: str
) -> None:
    """An image without an authored caption gets exactly one generated caption."""

    from wxdf.parsers import parse
    from wxdf.semantics.captions import collect_caption_candidates
    from wxdf.semantics.source_ast import build_source_ast

    if source_kind == "markdown":
        source = _write_rich_markdown(tmp_path / "media-and-appendix.md")
    else:
        source = _write_rich_docx(tmp_path / "rich.docx")
    ast = build_source_ast(parse(source))
    images = _image_block_ids(ast)
    candidates = collect_caption_candidates(ast)
    figures = [
        candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "create_node"
        and candidate["proposal"]["value"]["node"]["caption_type"] == "figure"
    ]

    assert len(images) == 1, images
    assert len(figures) == 1, [candidate["candidate_id"] for candidate in candidates]
    value = figures[0]["proposal"]["value"]
    assert value["node"]["target_id"] == images[0]
    assert value["node"]["role"] == "caption"
    assert value["node"]["generated_by"] == "caption_semantics.sequence_caption"
    parents = [
        parent for parent, children in ast["document"]["children"].items() if images[0] in children
    ]
    assert value["index"] == _sorted_children(ast, parents[0]).index(images[0]) + 1, value

    associations = [
        candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "update_association"
        and candidate["target_node_ids"] == [images[0]]
    ]
    assert len(associations) == 1, [candidate["candidate_id"] for candidate in candidates]
    assert associations[0]["target_source_refs"] == [
        ast["document"]["blocks"][images[0]]["source_ref"]
    ]


def test_authored_figure_caption_renders_as_a_seq_figure_field(tmp_path: Path) -> None:
    """The authored figure caption renders once, below the image."""

    source = _write_authored_figure_markdown(tmp_path / "figure.md")
    _completed, paths = _blocked_native_cli(tmp_path, source)
    xml = _document_xml(paths["output"])

    assert xml.count("SEQ 图") == 1, xml.count("SEQ 图")
    assert xml.count("SEQ 表") == 1, xml.count("SEQ 表")
    assert xml.count("系统架构") == 1, xml.count("系统架构")
    # The authored label is replaced by the label run and the sequence field.
    assert xml.count("图 1 系统架构") == 0
    assert re.search(r"<w:t>图</w:t>", xml), "the figure label run is missing"

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["passed"] is True
    assert report["saved_output_audit"]["issues"] == []
    evidence = report["native_module_evidence"]["caption-semantics"]
    assert evidence["caption_count"] == 1, evidence
    assert evidence["generated_caption_count"] == 1, evidence


def test_uncaptioned_docx_image_renders_one_seq_figure_field(tmp_path: Path) -> None:
    """A DOCX image without an authored caption still gets one figure caption."""

    source = _write_rich_docx(tmp_path / "rich.docx")
    _completed, paths = _blocked_native_cli(tmp_path, source)
    xml = _document_xml(paths["output"])

    assert xml.count("SEQ 图") == 1, xml.count("SEQ 图")
    assert xml.count("SEQ 表") == 1, xml.count("SEQ 表")
    assert re.search(r'<w:t xml:space="preserve">图 </w:t>', xml), "the figure label run is missing"

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert report["saved_output_audit"]["passed"] is True
    assert report["saved_output_audit"]["issues"] == []


def test_repeated_native_conversion_is_byte_stable(tmp_path: Path) -> None:
    """The native chain is deterministic for identical input."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _blocked_native_cli(first, BENCHMARK_MARKDOWN)
    _blocked_native_cli(second, BENCHMARK_MARKDOWN)
    assert (
        (first / "native-source-ast.json").read_bytes()
        == (second / "native-source-ast.json").read_bytes()
    )
    assert (
        (first / "native-normalized-ast.json").read_bytes()
        == (second / "native-normalized-ast.json").read_bytes()
    )


@pytest.mark.parametrize("source_name", ["test_baseline.md", "benchmark_report.md"])
def test_native_baseline_markdown_converts_under_blocking(tmp_path: Path, source_name: str) -> None:
    source = ROOT / "input" / source_name
    completed, paths = _blocked_native_cli(tmp_path, source)
    assert completed.returncode == 0, completed.stderr[-400:]
    assert paths["output"].is_file()
    assert paths["source_ast"].is_file()
    assert paths["normalized_ast"].is_file()
    assert paths["report"].is_file()
