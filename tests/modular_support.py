from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parents[1]
BASELINE_MANIFEST_PATH = ROOT / "tests" / "fixtures" / "modular_v3" / "manifest.json"
BASELINE_INPUT = ROOT / "input" / "test_baseline.md"
TEMPLATE = ROOT / "assets" / "wx_template.docx"

_NUMBERING_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NUMBERING_NS = {"w": _NUMBERING_NAMESPACE}


@dataclass(frozen=True)
class NativeConversionResult:
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


_ROMAN_VALUES = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)


def _letters(value: int) -> str:
    text = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        text = chr(ord("a") + remainder) + text
    return text


def _roman(value: int) -> str:
    text = ""
    for amount, symbol in _ROMAN_VALUES:
        while value >= amount:
            text += symbol
            value -= amount
    return text


def _format_number(value: int, num_fmt: str | None) -> str:
    """Format one counter value the way ``w:numFmt`` renders it."""

    if num_fmt in {"lowerLetter", "upperLetter"}:
        text = _letters(value)
        return text.upper() if num_fmt == "upperLetter" else text
    if num_fmt in {"lowerRoman", "upperRoman"}:
        text = _roman(value)
        return text.upper() if num_fmt == "upperRoman" else text
    if num_fmt == "decimalZero":
        return f"{value:02d}"
    if num_fmt in {"bullet", "none"}:
        return ""
    return str(value)


def _int_attribute(element, name: str, default: int) -> int:
    if element is None:
        return default
    try:
        return int(element.get(f"{{{_NUMBERING_NAMESPACE}}}{name}"))
    except (TypeError, ValueError):
        return default


def simulate_saved_numbering(path: Path) -> list[dict]:
    """Return the visible numbering the saved DOCX produces, paragraph by paragraph.

    A paragraph is numbered by its own direct ``w:numPr`` or, when it carries
    none, by the ``w:numPr`` of its paragraph style, which is how the template
    numbers its headings.  Counters are kept per ``w:num`` instance, so two
    instances that reference the same abstract definition never share a counter.
    A level restarts when an instance of the level its ``w:lvlRestart`` names
    appears in the same instance (the value is a one-based index, ``0`` means the
    level never restarts, and an omitted ``w:lvlRestart`` restarts the level when
    the level above it is used, per ECMA-376 17.9.11).

    Every returned row carries the paragraph text, the numbering ``num_id`` /
    ``ilvl`` actually used, the paragraph style id, and ``marker``: the
    ``w:lvlText`` with its ``%n`` placeholders substituted (the level's marker
    suffix is not appended).  ``marker`` is ``None`` for a paragraph with no
    numbering, and is ``""`` for a bullet or ``none`` level, whose ``lvlText``
    carries the visible symbol itself.
    """

    with ZipFile(path) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))

    abstract_levels: dict[str, dict[int, dict]] = {}
    for abstract in numbering.findall("w:abstractNum", _NUMBERING_NS):
        abstract_id = abstract.get(f"{{{_NUMBERING_NAMESPACE}}}abstractNumId")
        levels: dict[int, dict] = {}
        for level in abstract.findall("w:lvl", _NUMBERING_NS):
            ilvl = _int_attribute(level, "ilvl", 0)
            num_fmt = level.find("w:numFmt", _NUMBERING_NS)
            lvl_text = level.find("w:lvlText", _NUMBERING_NS)
            restart = level.find("w:lvlRestart", _NUMBERING_NS)
            start = level.find("w:start", _NUMBERING_NS)
            levels[ilvl] = {
                "num_fmt": num_fmt.get(f"{{{_NUMBERING_NAMESPACE}}}val") if num_fmt is not None else None,
                "lvl_text": lvl_text.get(f"{{{_NUMBERING_NAMESPACE}}}val") if lvl_text is not None else None,
                "restart": (
                    _int_attribute(restart, "val", 0) if restart is not None else None
                ),
                "start": _int_attribute(start, "val", 1),
            }
        abstract_levels[str(abstract_id)] = levels

    num_to_abstract: dict[str, str] = {}
    for instance in numbering.findall("w:num", _NUMBERING_NS):
        reference = instance.find("w:abstractNumId", _NUMBERING_NS)
        if reference is None:
            continue
        num_to_abstract[instance.get(f"{{{_NUMBERING_NAMESPACE}}}numId")] = reference.get(
            f"{{{_NUMBERING_NAMESPACE}}}val"
        )

    style_numbering: dict[str, tuple[str, int]] = {}
    for style in styles.findall("w:style", _NUMBERING_NS):
        style_id = style.get(f"{{{_NUMBERING_NAMESPACE}}}styleId")
        num_pr = style.find("w:pPr/w:numPr", _NUMBERING_NS)
        if style_id is None or num_pr is None:
            continue
        num_id_element = num_pr.find("w:numId", _NUMBERING_NS)
        if num_id_element is None:
            continue
        style_numbering[style_id] = (
            num_id_element.get(f"{{{_NUMBERING_NAMESPACE}}}val"),
            _int_attribute(num_pr.find("w:ilvl", _NUMBERING_NS), "val", 0),
        )

    counters: dict[tuple[str, int], int] = {}
    rows: list[dict] = []
    for paragraph in document.findall(".//w:p", _NUMBERING_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", _NUMBERING_NS))
        style = paragraph.find("w:pPr/w:pStyle", _NUMBERING_NS)
        style_id = style.get(f"{{{_NUMBERING_NAMESPACE}}}val") if style is not None else None
        num_pr = paragraph.find("w:pPr/w:numPr", _NUMBERING_NS)
        source = "direct"
        if num_pr is not None and num_pr.find("w:numId", _NUMBERING_NS) is not None:
            num_id = num_pr.find("w:numId", _NUMBERING_NS).get(f"{{{_NUMBERING_NAMESPACE}}}val")
            ilvl = _int_attribute(num_pr.find("w:ilvl", _NUMBERING_NS), "val", 0)
        elif style_id in style_numbering:
            num_id, ilvl = style_numbering[style_id]
            source = "style"
        else:
            rows.append({"text": text, "style_id": style_id, "num_id": None, "ilvl": None, "marker": None})
            continue
        levels = abstract_levels.get(num_to_abstract.get(num_id, ""), {})
        if ilvl not in levels:
            rows.append({"text": text, "style_id": style_id, "num_id": num_id, "ilvl": ilvl, "marker": None})
            continue
        for deeper, deeper_level in levels.items():
            if deeper <= ilvl:
                continue
            trigger = deeper_level["restart"] if deeper_level["restart"] is not None else deeper
            if trigger == 0 or trigger != ilvl + 1:
                continue
            counters[(num_id, deeper)] = deeper_level["start"] - 1
        counters[(num_id, ilvl)] = counters.get((num_id, ilvl), levels[ilvl]["start"] - 1) + 1

        def substitute(match: re.Match, *, levels: dict[int, dict], num_id: str) -> str:
            level_index = int(match.group(1)) - 1
            level = levels.get(level_index)
            if level is None:
                return match.group(0)
            # A level that has not counted since its last restart sits at its
            # start value minus one; it is displayed at its start value, which is
            # how a level the document skips still shows a usable number.
            value = max(
                counters.get((num_id, level_index), level["start"]),
                level["start"],
            )
            return _format_number(value, level["num_fmt"])

        marker = re.sub(
            r"%(\d)",
            lambda match: substitute(match, levels=levels, num_id=num_id),
            levels[ilvl]["lvl_text"] or "",
        )
        rows.append({
            "text": text,
            "style_id": style_id,
            "num_id": num_id,
            "ilvl": ilvl,
            "marker": marker.rstrip(),
            "numbering_source": source,
        })
    return rows


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


def run_native_markdown_conversion(tmp_path: Path) -> NativeConversionResult:
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
    return NativeConversionResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        output=output,
        report=report,
        source_ast=source_ast,
        normalized_ast=normalized_ast,
    )
