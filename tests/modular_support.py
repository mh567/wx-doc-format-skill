from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parents[1]
BASELINE_MANIFEST_PATH = ROOT / "tests" / "fixtures" / "modular_v3" / "manifest.json"
BASELINE_INPUT = ROOT / "input" / "test_baseline.md"
TEMPLATE = ROOT / "assets" / "wx_template.docx"


@dataclass(frozen=True)
class LegacyConversionResult:
    returncode: int
    stdout: str
    stderr: str
    output: Path
    report: Path
    source_ast: Path
    normalized_ast: Path


def load_baseline_manifest() -> dict:
    return json.loads(BASELINE_MANIFEST_PATH.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_numbering_definition(
    document: Document,
    *,
    abstract_id: int,
    num_id: int,
    levels: list[tuple[str, str]],
) -> None:
    numbering = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi_level = OxmlElement("w:multiLevelType")
    multi_level.set(qn("w:val"), "multilevel")
    abstract.append(multi_level)
    for level_index, (num_format, level_text) in enumerate(levels):
        level = OxmlElement("w:lvl")
        level.set(qn("w:ilvl"), str(level_index))
        start = OxmlElement("w:start")
        start.set(qn("w:val"), "1")
        level.append(start)
        fmt = OxmlElement("w:numFmt")
        fmt.set(qn("w:val"), num_format)
        level.append(fmt)
        text = OxmlElement("w:lvlText")
        text.set(qn("w:val"), level_text)
        level.append(text)
        abstract.append(level)
    numbering.append(abstract)

    numbering_instance = OxmlElement("w:num")
    numbering_instance.set(qn("w:numId"), str(num_id))
    abstract_ref = OxmlElement("w:abstractNumId")
    abstract_ref.set(qn("w:val"), str(abstract_id))
    numbering_instance.append(abstract_ref)
    numbering.append(numbering_instance)


def _set_numbering(paragraph, *, num_id: int, level: int) -> None:
    properties = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), str(level))
    num_pr.append(ilvl)
    number = OxmlElement("w:numId")
    number.set(qn("w:val"), str(num_id))
    num_pr.append(number)
    properties.append(num_pr)


def build_docx_fixture(tmp_path: Path) -> Path:
    """Build a small DOCX with explicit OOXML numbering for later tasks."""
    document = Document()
    _append_numbering_definition(
        document,
        abstract_id=901,
        num_id=901,
        levels=[("decimal", "%1."), ("decimal", "%1.%2")],
    )
    _append_numbering_definition(
        document,
        abstract_id=902,
        num_id=902,
        levels=[("bullet", "•"), ("bullet", "◦")],
    )

    document.add_paragraph("模块化基线", style="Heading 1")
    first = document.add_paragraph("一级条目")
    _set_numbering(first, num_id=901, level=0)
    child = document.add_paragraph("二级条目")
    _set_numbering(child, num_id=901, level=1)
    bullet = document.add_paragraph("无序子项")
    _set_numbering(bullet, num_id=902, level=1)
    second = document.add_paragraph("第二个一级条目")
    _set_numbering(second, num_id=901, level=0)
    document.add_paragraph("正文说明")

    output = tmp_path / "modular-baseline.docx"
    document.save(output)
    return output


def run_legacy_markdown_conversion(tmp_path: Path) -> LegacyConversionResult:
    output = tmp_path / "baseline.docx"
    report = tmp_path / "report.json"
    source_ast = tmp_path / "source.json"
    normalized_ast = tmp_path / "normalized.json"
    python = ROOT / "scripts" / ".venv" / "bin" / "python3"
    if not python.is_file():
        python = Path(sys.executable)
    env = os.environ.copy()
    env.pop("WX_DOC_FORMAT_RELEASE", None)
    env.pop("WX_DOC_LLM_ENHANCE", None)
    completed = subprocess.run(
        [
            str(python),
            str(ROOT / "scripts" / "main.py"),
            "--input",
            str(BASELINE_INPUT),
            "--output",
            str(output),
            "--template",
            str(TEMPLATE),
            "--pipeline",
            "legacy",
            "--modular-profile",
            "legacy-full-v1",
            "--list-policy",
            "legacy",
            "--llm-enhance",
            "off",
            "--report",
            str(report),
            "--source-ast",
            str(source_ast),
            "--ast",
            str(normalized_ast),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return LegacyConversionResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        output=output,
        report=report,
        source_ast=source_ast,
        normalized_ast=normalized_ast,
    )
