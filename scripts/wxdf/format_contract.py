"""Element format contracts shared by the output audit and the input review.

The template is the authority for every expected value: a role's expected
formatting is resolved from the template's own style (including its numbering
level) at load time, so nothing here hardcodes a point size, a line spacing or an
indentation.

The expectation is **per role**, not per style.  A paragraph is first classified
into a role (body, a heading level, a caption, a note, a list item, ...) without
assuming the document uses any particular style, then its *effective* formatting
is compared with that role's expected values.  A document therefore conforms when
what it renders matches the spec, whatever styles produced it.

Two consumers share the expectations but not the assumptions:

* ``consumer="audit"`` checks a rendered artifact, so it exempts the direct
  properties the renderer writes for structure (outline level, numbering
  instance, page break, layout-cell restoration).  A property the template
  specifies, including alignment, is never exempt.
* ``consumer="review"`` checks an author's source document, so nothing is exempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn

from appendix_semantics import appendix_role_from_style, is_explicit_appendix_title
from note_semantics import source_note_role
from text_utils import heading_level_from_text, is_formula_number
from toc_contract import TOC_MAX_LEVEL

PPR = "pPr"
RPR = "rPr"

MAX_HEADING_LEVEL = 6
APPENDIX_HEADING_LEVELS = (1, 2, 3)

# Run properties that are always acceptable: a character style reference and the
# language/complex-script hints python-docx and Word write alongside real
# formatting.  Mirrors ``table_formatting.RUN_ALLOWED_TAGS``.
RUN_BASE_ALLOWED = frozenset({"rStyle", "lang", "bCs", "iCs"})
# Paragraph properties that carry the formatting the spec governs.  Anything else
# Word writes directly (``bidi``, ``snapToGrid``, ``keepNext``, ``outlineLvl``,
# ``numPr``, the paragraph-mark ``rPr``, ...) is a layout-engine hint rather than
# a deviation, so only these are worth reporting when they appear directly.
FORMAT_BEARING_PARAGRAPH_TAGS = frozenset({"ind", "spacing", "jc", "shd", "pBdr", "tabs", "framePr"})
# The renderer writes these direct properties to express structure rather than
# formatting, so the artifact audit exempts them.  Alignment is deliberately
# absent: the template specifies ``jc`` on its body and heading styles, and a
# template requirement outranks the renderer's own behaviour.
RENDERER_OWNED_PARAGRAPH = frozenset()
# The spec (2.2.2) lets an author emphasise text with bold or by switching to the
# bold-family face, so a run may carry those directly; a size, underline, colour
# or highlight override is a deviation.
EMPHASIS_FONT = "黑体"
FORBIDDEN_RUN_TAGS = frozenset({"u", "color", "spacing", "position", "highlight", "shd", "vertAlign", "kern"})

# Numbering formats the spec recognises.  An ordered instance maps to the ordered
# list roles, anything else to the unordered ones.
_ORDERED_NUM_FORMATS = frozenset(
    {"decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman"}
)
_TOC_TITLES = ("目录", "目次", "目  次", "目  录")
# A caption's text is recognised only in its strong form, a label followed by a
# number ("表1", "图 2.1", "表A.1").  The looser forms the caption parser accepts
# ("表：说明") would also claim ordinary prose that happens to start with 图/表.
_CAPTION_TEXT = re.compile(r"^[图表]\s*(?:[A-Za-z][.．])?\d+(?:[-.．]\d+)*")


@dataclass(frozen=True)
class PropertyRef:
    """One comparable property: a container, its child tag and one attribute."""

    container: str
    tag: str
    attr: str = "val"

    @property
    def key(self) -> str:
        return f"{self.tag}.{self.attr}" if self.attr != "val" else self.tag


@dataclass(frozen=True)
class ElementContract:
    """A role's comparable properties and the template style that defines them."""

    role: str
    style_role: str
    properties: tuple[PropertyRef, ...]


def _p(tag: str, attr: str = "val") -> PropertyRef:
    return PropertyRef(PPR, tag, attr)


def _r(tag: str, attr: str = "val") -> PropertyRef:
    return PropertyRef(RPR, tag, attr)


_FONT_REFS = (_r("rFonts", "eastAsia"), _r("rFonts", "ascii"), _r("rFonts", "hAnsi"))
_TYPE_REFS = (_r("sz", "val"), _r("b", "val"))
_LINE_REFS = (_p("spacing", "line"), _p("spacing", "lineRule"))
_HALF_LINE_REFS = (_p("spacing", "beforeLines"), _p("spacing", "afterLines"))

_BODY_REFS = (*_FONT_REFS, *_TYPE_REFS, *_LINE_REFS, _p("ind", "firstLineChars"), _p("jc", "val"))
_HEADING_REFS = (*_BODY_REFS, *_HALF_LINE_REFS)
_CAPTION_REFS = (*_FONT_REFS, *_TYPE_REFS, *_LINE_REFS, *_HALF_LINE_REFS, _p("ind", "firstLineChars"), _p("jc", "val"))
_NOTE_REFS = (*_FONT_REFS, *_TYPE_REFS, _p("spacing", "before"), _p("ind", "left"), _p("ind", "hanging"))
_LIST_REFS = (*_FONT_REFS, *_TYPE_REFS, *_LINE_REFS, _p("ind", "left"), _p("ind", "leftChars"), _p("ind", "hanging"))
_FORMULA_REFS = (*_FONT_REFS, *_TYPE_REFS, _p("spacing", "line"), _p("jc", "val"))


def _contract(role: str, style_role: str, properties: Iterable[PropertyRef]) -> ElementContract:
    return ElementContract(role, style_role, tuple(properties))


# Two element families are deliberately absent:
#   * the table of contents, whose contract is owned by
#     ``toc_detector.audit_toc_replacement``;
#   * table body cells, whose formatting is owned by the table format contract in
#     ``table_formatting``.
# Restating either here would duplicate that owner and double-report.
CONTRACTS: dict[str, ElementContract] = {
    "body": _contract("body", "body", _BODY_REFS),
    "title": _contract("title", "title", _HEADING_REFS),
    "caption": _contract("caption", "caption", _CAPTION_REFS),
    "note": _contract("note", "note", _NOTE_REFS),
    "numbered_note": _contract("numbered_note", "numbered_note", _NOTE_REFS),
    "list_letter": _contract("list_letter", "list_letter", _LIST_REFS),
    "list_decimal": _contract("list_decimal", "list_decimal", _LIST_REFS),
    "list_dash": _contract("list_dash", "list_dash", _LIST_REFS),
    "list_bullet": _contract("list_bullet", "list_bullet", _LIST_REFS),
    "appendix_title": _contract("appendix_title", "appendix_title", _CAPTION_REFS),
    "formula": _contract("formula", "formula", _FORMULA_REFS),
}
for _level in range(1, MAX_HEADING_LEVEL + 1):
    CONTRACTS[f"heading_{_level}"] = _contract(f"heading_{_level}", f"heading_{_level}", _HEADING_REFS)
for _level in APPENDIX_HEADING_LEVELS:
    CONTRACTS[f"appendix_heading_{_level}"] = _contract(
        f"appendix_heading_{_level}", f"appendix_heading_{_level}", _HEADING_REFS
    )


def _normalized(value: str | None) -> str:
    return (value or "").casefold().replace(" ", "")


# ---------------------------------------------------------------------------
# Role resolution
#
# A role is decided by the strongest available evidence, never by assuming the
# document uses the template's styles.  The first layer reads what the paragraph
# *is* (appendix, note, an outline level, a numbering instance); the second reads
# a style name, accepting both the template's names and the common equivalents
# Word documents use; the third reads the text.  Only a paragraph no layer can
# place stays unclassified.

# Style names Word and Chinese templates commonly use, beyond whatever the loaded
# template binds.  Keyed by the normalized name.
_COMMON_STYLE_ROLES: dict[str, str] = {
    "normal": "body",
    "正文": "body",
    "bodytext": "body",
    "plaintext": "body",
    "文档标题": "title",
    "title": "title",
    "caption": "caption",
    "题注": "caption",
    "图表标题": "caption",
    "附录标题": "appendix_title",
    "附录一级标题": "appendix_heading_1",
    "附录二级标题": "appendix_heading_2",
    "附录三级标题": "appendix_heading_3",
}
for _level in range(1, MAX_HEADING_LEVEL + 1):
    for _pattern in (f"heading{_level}", f"标题{_level}"):
        _COMMON_STYLE_ROLES[_pattern] = f"heading_{_level}"
for _level in range(1, TOC_MAX_LEVEL + 1):
    _COMMON_STYLE_ROLES[f"toc{_level}"] = f"toc_entry_{_level}"
    _COMMON_STYLE_ROLES[f"目录{_level}"] = f"toc_entry_{_level}"


def style_role_index(profile: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Return ``{normalized style name: role}``.

    The common names are laid down first and the template's own bindings are
    added only for names they do not claim.  A template may bind a role to a
    stock style as a fallback (this template binds ``formula`` to ``Normal``),
    and that binding carries no information: ``Normal`` is the body style, so
    letting it through would classify every body paragraph as a formula.
    """

    index = dict(_COMMON_STYLE_ROLES)
    resolved = profile.get("resolved_styles", {}) if isinstance(profile, Mapping) else {}
    if isinstance(resolved, Mapping):
        for role, name in resolved.items():
            if role not in CONTRACTS or not name:
                continue
            index.setdefault(_normalized(str(name)), role)
    return index


def contract_for_style(profile: Mapping[str, Any] | None, style_name: str | None) -> ElementContract | None:
    if not style_name:
        return None
    role = style_role_index(profile).get(_normalized(style_name))
    return CONTRACTS.get(role) if role else None


def _is_image_only(paragraph: Any) -> bool:
    """Return whether the paragraph's whole content is a drawing.

    The spec keeps such a paragraph on no style at all and gives it one direct
    property, ``jc=center`` (decision 2), so the body contract must not judge it:
    its centring is the requirement, not a deviation from the body style's
    justification.
    """

    if paragraph.text.strip():
        return False
    for child in paragraph._p.iter():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in {"drawing", "pict"}:
            return True
    return False


def _has_math_element(paragraph: Any) -> bool:
    """Return whether the paragraph carries an Office Math object.

    A formula is identified by what it contains rather than by a style name: the
    template binds the formula role to ``Normal`` as a fallback, so the style
    carries no signal.
    """

    return next(paragraph._p.iter(qn("m:oMath")), None) is not None


def _default_paragraph_style_name(paragraph: Any) -> str | None:
    try:
        styles = paragraph.part.document.styles
        return _normalized(styles.default(WD_STYLE_TYPE.PARAGRAPH).name)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _derives_from_default(paragraph: Any) -> bool:
    """Return whether the paragraph's style chain ends at the document default.

    A paragraph whose style ultimately derives from the body default is running
    text, whatever the style is called, so the body contract can judge it without
    the classification resting on the style's name.
    """

    default = _default_paragraph_style_name(paragraph)
    if default is None:
        return False
    return any(_normalized(style.name) == default for style in _style_chain(paragraph.style))


def resolve_role(paragraph: Any, *, profile: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Return ``{"role", "evidence"}`` for a paragraph, or ``None``.

    ``None`` means no layer could tell what the paragraph is; it is counted as
    unclassified rather than silently skipped or guessed at.
    """

    style_name = paragraph.style.name if paragraph.style is not None else ""
    text = paragraph.text.strip()

    appendix_role = appendix_role_from_style(style_name)
    if appendix_role:
        return {"role": appendix_role, "evidence": ("appendix_style",)}
    if text and is_explicit_appendix_title(text):
        return {"role": "appendix_title", "evidence": ("appendix_text",)}

    # Two paragraph kinds carry a presentation the body contract must not judge:
    # an image paragraph, which the spec centres and keeps off every style
    # (decision 2), and a formula number, whose own layout the formula contract
    # owns.  Both keep an authored alignment the body style does not describe.
    if _is_image_only(paragraph):
        return None
    if text and is_formula_number(text):
        return None

    numbering = _numbering_descriptor(paragraph)
    note_role = source_note_role(style_name, numbering, text)
    if note_role:
        return {"role": note_role, "evidence": ("note_semantics",)}

    # The TOC title and a caption are matched by text before the style layer.
    # Both sit on a generic style in practice (this template keeps its TOC title
    # on ``Normal``), and the body contract would otherwise judge their centred
    # alignment as a deviation.  Their own contracts are owned elsewhere: the TOC
    # by ``toc_detector`` and captions by the caption semantics.
    if text in _TOC_TITLES:
        return {"role": "toc_title", "evidence": ("toc_title_text",)}
    if text and _CAPTION_TEXT.match(text):
        return {"role": "caption", "evidence": ("caption_text",)}

    outline = resolve_paragraph_value(paragraph, PropertyRef(PPR, "outlineLvl", "val"))[0]
    if outline is not None:
        try:
            level = int(outline)
        except (TypeError, ValueError):
            level = 9
        if 0 <= level < MAX_HEADING_LEVEL:
            return {"role": f"heading_{level + 1}", "evidence": ("outline_level",)}

    # The style name is consulted before the numbering instance because the two
    # level model emits ``w:ilvl 0`` for every list item and carries the rendered
    # level in the style instead: a second level item styled ``2.1二级列项-有编号``
    # would otherwise read as a first level one.
    style_role = style_role_index(profile).get(_normalized(style_name))
    if style_role:
        return {"role": style_role, "evidence": ("style_name",)}

    number_id, ilvl = _paragraph_numbering(paragraph)
    if number_id is not None and _numbering_level(paragraph, number_id, ilvl) is not None:
        num_format = _numbering_format(paragraph, number_id, ilvl)
        depth = 0
        try:
            depth = int(ilvl or 0)
        except (TypeError, ValueError):
            depth = 0
        if num_format in _ORDERED_NUM_FORMATS:
            return {"role": "list_letter" if depth < 1 else "list_decimal", "evidence": ("numbering",)}
        return {"role": "list_dash" if depth < 1 else "list_bullet", "evidence": ("numbering",)}

    if _has_math_element(paragraph):
        return {"role": "formula", "evidence": ("math_element",)}

    heading_level = heading_level_from_text(text) if text else None
    if heading_level is not None:
        return {"role": f"heading_{min(heading_level, MAX_HEADING_LEVEL)}", "evidence": ("heading_text",)}

    if _derives_from_default(paragraph):
        return {"role": "body", "evidence": ("default_style",)}
    return None


# ---------------------------------------------------------------------------
# Property resolution


def _properties_element(parent: Any, container: str) -> Any:
    return parent.find(qn(f"w:{container}")) if parent is not None else None


def _child_value(properties: Any, tag: str, attr: str) -> str | None:
    if properties is None:
        return None
    child = properties.find(qn(f"w:{tag}"))
    if child is None:
        return None
    return child.get(qn(f"w:{attr}"))


def _on_off(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.casefold() not in {"0", "false", "off", "no"}


def _doc_defaults(styles_root: Any, container: str, tag: str, attr: str) -> str | None:
    if styles_root is None:
        return None
    defaults = styles_root.find(qn("w:docDefaults"))
    if defaults is None:
        return None
    group = defaults.find(qn(f"w:{container}Default"))
    return _child_value(_properties_element(group, container), tag, attr) if group is not None else None


def _style_chain(style: Any) -> list[Any]:
    chain: list[Any] = []
    current = style
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.base_style
    return chain


def _numbering_level(document: Any, num_id: Any, ilvl: Any) -> Any:
    """Return the ``w:lvl`` element a paragraph's numbering points at."""

    if document is None or num_id is None:
        return None
    try:
        root = document.part.numbering_part.element
    except (AttributeError, KeyError, NotImplementedError, ValueError):
        return None
    if root is None:
        return None
    try:
        number = int(num_id)
        level_index = int(ilvl or 0)
    except (TypeError, ValueError):
        return None
    abstract_id = None
    for num in root.findall(qn("w:num")):
        if num.get(qn("w:numId")) == str(number):
            reference = num.find(qn("w:abstractNumId"))
            abstract_id = reference.get(qn("w:val")) if reference is not None else None
            break
    if abstract_id is None:
        return None
    for abstract in root.findall(qn("w:abstractNum")):
        if abstract.get(qn("w:abstractNumId")) != str(abstract_id):
            continue
        for level in abstract.findall(qn("w:lvl")):
            if level.get(qn("w:ilvl")) == str(level_index):
                return level
    return None


def _numbering_level_for_paragraph(paragraph: Any, num_id: Any, ilvl: Any) -> Any:
    return _numbering_level(paragraph.part.document, num_id, ilvl)


def _numbering_format(paragraph: Any, num_id: Any, ilvl: Any) -> str | None:
    level = _numbering_level_for_paragraph(paragraph, num_id, ilvl)
    if level is None:
        return None
    num_fmt = level.find(qn("w:numFmt"))
    return num_fmt.get(qn("w:val")) if num_fmt is not None else None


def _numbering_descriptor(paragraph: Any) -> dict[str, Any]:
    """Return the ``{lvl_text, num_fmt}`` a note-role check reads."""

    num_id, ilvl = _paragraph_numbering(paragraph)
    level = _numbering_level_for_paragraph(paragraph, num_id, ilvl)
    if level is None:
        return {}
    text = level.find(qn("w:lvlText"))
    fmt = level.find(qn("w:numFmt"))
    return {
        "lvl_text": text.get(qn("w:val")) if text is not None else None,
        "num_fmt": fmt.get(qn("w:val")) if fmt is not None else None,
    }


def _paragraph_numbering(paragraph: Any) -> tuple[str | None, str | None]:
    """Return the paragraph's direct ``(numId, ilvl)``, falling back to its style."""

    style_p_pr = _properties_element(paragraph.style._element, PPR) if paragraph.style is not None else None
    for source in (_properties_element(paragraph._p, PPR), style_p_pr):
        num_pr = source.find(qn("w:numPr")) if source is not None else None
        if num_pr is None:
            continue
        num_id = num_pr.find(qn("w:numId"))
        ilvl = num_pr.find(qn("w:ilvl"))
        number = num_id.get(qn("w:val")) if num_id is not None else None
        if number in {None, "0"}:
            continue
        return number, (ilvl.get(qn("w:val")) if ilvl is not None else None)
    return None, None


def resolve_paragraph_value(paragraph: Any, ref: PropertyRef) -> tuple[str | None, str]:
    """Resolve a paragraph-level property to ``(value, source)``.

    Precedence is direct paragraph property, numbering level, paragraph style
    chain, then document defaults, which is the order Word applies.
    """

    direct = _properties_element(paragraph._p, PPR)
    value = _child_value(direct, ref.tag, ref.attr)
    if value is not None:
        return value, "direct"

    num_id, ilvl = _paragraph_numbering(paragraph)
    value = _child_value(_numbering_level_p_pr(paragraph, num_id, ilvl), ref.tag, ref.attr)
    if value is not None:
        return value, "numbering"

    for style in _style_chain(paragraph.style):
        value = _child_value(_properties_element(style._element, PPR), ref.tag, ref.attr)
        if value is not None:
            return value, f"style:{style.name}"

    value = _doc_defaults(_styles_root(paragraph), PPR, ref.tag, ref.attr)
    if value is not None:
        return value, "docDefaults"
    return None, "unset"


def _numbering_level_p_pr(paragraph: Any, num_id: Any, ilvl: Any) -> Any:
    level = _numbering_level_for_paragraph(paragraph, num_id, ilvl)
    return level.find(qn("w:pPr")) if level is not None else None


def resolve_run_value(paragraph: Any, ref: PropertyRef, run: Any | None = None) -> tuple[str | None, str]:
    """Resolve a run-level property to ``(value, source)``.

    Precedence is the run's direct properties, the paragraph mark's run
    properties, the paragraph style chain, then document defaults.
    """

    if run is not None:
        value = _child_value(_properties_element(run._r, RPR), ref.tag, ref.attr)
        if value is not None:
            return value, "direct"

    mark = _properties_element(_properties_element(paragraph._p, PPR), RPR)
    value = _child_value(mark, ref.tag, ref.attr)
    if value is not None:
        return value, "paragraph_mark"

    for style in _style_chain(paragraph.style):
        value = _child_value(_properties_element(style._element, RPR), ref.tag, ref.attr)
        if value is not None:
            return value, f"style:{style.name}"

    value = _doc_defaults(_styles_root(paragraph), RPR, ref.tag, ref.attr)
    if value is not None:
        return value, "docDefaults"
    return None, "unset"


def _styles_root(target: Any) -> Any:
    try:
        return target.part.document.styles.element
    except AttributeError:
        return None


# ---------------------------------------------------------------------------
# Expected values (template is the authority)


def _style_numbering(style: Any) -> tuple[str | None, str | None]:
    style_p_pr = _properties_element(style._element, PPR)
    num_pr = style_p_pr.find(qn("w:numPr")) if style_p_pr is not None else None
    if num_pr is None:
        return None, None
    num_id = num_pr.find(qn("w:numId"))
    ilvl = num_pr.find(qn("w:ilvl"))
    return (
        num_id.get(qn("w:val")) if num_id is not None else None,
        ilvl.get(qn("w:val")) if ilvl is not None else None,
    )


def _resolve_style_value(style: Any, ref: PropertyRef, document: Any) -> str | None:
    """Resolve a template style's effective value for one property.

    A paragraph property may come from the numbering level the style binds, and
    the level keeps its paragraph properties under ``w:lvl/w:pPr``, which is where
    the list indentation lives.
    """

    num_id, ilvl = _style_numbering(style)
    if ref.container == PPR:
        level = _numbering_level(document, num_id, ilvl)
        level_p_pr = level.find(qn("w:pPr")) if level is not None else None
        value = _child_value(level_p_pr, ref.tag, ref.attr)
        if value is not None:
            return value
    for candidate in _style_chain(style):
        value = _child_value(_properties_element(candidate._element, ref.container), ref.tag, ref.attr)
        if value is not None:
            return value
    return _doc_defaults(_styles_root(document), ref.container, ref.tag, ref.attr)


def resolve_style_by_name(document: Any, name: str) -> Any | None:
    target = _normalized(name)
    for style in document.styles:
        if _normalized(style.name) == target:
            return style
    return None


def derive_baseline(profile: Mapping[str, Any], template_doc: Any) -> dict[str, dict[str, str | None]]:
    """Return ``{role: {property_key: expected}}`` resolved from the template."""

    resolved = profile.get("resolved_styles", {}) if isinstance(profile, Mapping) else {}
    baseline: dict[str, dict[str, str | None]] = {}
    for role, contract in CONTRACTS.items():
        name = resolved.get(contract.style_role) if isinstance(resolved, Mapping) else None
        if name is None:
            continue
        style = resolve_style_by_name(template_doc, str(name))
        if style is None:
            continue
        baseline[role] = {ref.key: _resolve_style_value(style, ref, template_doc) for ref in contract.properties}
    return baseline


def check_contract_completeness(profile: Mapping[str, Any], template_doc: Any) -> list[dict[str, Any]]:
    """Report a template that cannot satisfy the contract.

    A missing style or property would otherwise be reported once per document; it
    is a template configuration error and is reported once.
    """

    resolved = profile.get("resolved_styles", {}) if isinstance(profile, Mapping) else {}
    issues: list[dict[str, Any]] = []
    for role, contract in CONTRACTS.items():
        name = resolved.get(contract.style_role) if isinstance(resolved, Mapping) else None
        if name is None:
            issues.append({"code": "contract_role_unbound", "role": role})
            continue
        if resolve_style_by_name(template_doc, str(name)) is None:
            issues.append({"code": "contract_style_missing", "role": role, "style": str(name)})
    return issues


# ---------------------------------------------------------------------------
# Comparison


def _reported_paragraph_tags(contract: ElementContract, consumer: str) -> frozenset[str]:
    """Return the direct paragraph tags worth reporting for one consumer.

    A property the contract already compares is reported by the value comparison,
    so the scan covers only the format-bearing tags the contract does not own.
    """

    compared = {ref.tag for ref in contract.properties if ref.container == PPR}
    tags = set(FORMAT_BEARING_PARAGRAPH_TAGS) - compared
    if consumer == "audit":
        tags -= RENDERER_OWNED_PARAGRAPH
    return frozenset(tags)


def _is_emphasis(run: Any) -> bool:
    """Return whether a run's direct properties are the spec's emphasis form."""

    r_pr = run._r.find(qn("w:rPr"))
    if r_pr is None:
        return False
    weight = r_pr.find(qn("w:b"))
    if weight is not None and _on_off(weight.get(qn("w:val"))) is not False:
        return True
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is not None and fonts.get(qn("w:eastAsia")) == EMPHASIS_FONT:
        return True
    return False


def _run_issues(
    paragraph: Any,
    baseline: Mapping[str, str | None],
    path: str,
    role: str,
) -> list[dict[str, Any]]:
    """Compare each run's direct formatting against the baseline.

    A font a run sets to the baseline value is redundant, not a deviation, so the
    comparison is on the resulting effective value.  The emphasis form the spec
    allows (bold, or the bold-family face) excuses a font difference; a size,
    underline, colour or highlight override never does.
    """

    issues: list[dict[str, Any]] = []
    runs = getattr(paragraph, "runs", None)
    if runs is None:
        return issues
    for index, run in enumerate(runs, 1):
        r_pr = run._r.find(qn("w:rPr"))
        if r_pr is None:
            continue
        # Only a property the run sets itself is a run-level override; anything it
        # inherits is already compared once at the paragraph level.
        direct_tags = {child.tag for child in r_pr}
        for ref in (*_FONT_REFS, _r("sz", "val"), _r("szCs", "val")):
            if qn(f"w:{ref.tag}") not in direct_tags:
                continue
            expected = baseline.get(ref.key)
            if expected is None:
                continue
            actual, source = resolve_run_value(paragraph, ref, run=run)
            if actual == expected:
                continue
            if ref.tag == "rFonts" and _is_emphasis(run):
                continue
            issues.append({
                "code": "format_run_direct_override",
                "role": role,
                "property": f"run[{index}].{ref.key}",
                "expected": expected,
                "actual": actual,
                "source": source,
                "path": path,
                "severity": "medium",
            })
        for child in r_pr:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag not in FORBIDDEN_RUN_TAGS:
                continue
            value = child.get(qn("w:val")) or ""
            # ``vertAlign`` set to ``baseline`` states the default and changes
            # nothing, so it is a redundant setting rather than a deviation.
            if tag == "vertAlign" and value == "baseline":
                continue
            issues.append({
                "code": "format_run_direct_override",
                "role": role,
                "property": f"run[{index}].{tag}",
                "expected": None,
                "actual": value,
                "source": "direct",
                "path": path,
                "severity": "medium",
            })
    return issues


def audit_paragraph(
    paragraph: Any,
    contract: ElementContract,
    baseline: Mapping[str, str | None],
    path: str,
    *,
    consumer: str = "audit",
) -> list[dict[str, Any]]:
    """Compare one paragraph against the contract and its template expectation."""

    issues: list[dict[str, Any]] = []

    for ref in contract.properties:
        if consumer == "audit" and ref.tag in RENDERER_OWNED_PARAGRAPH:
            continue
        expected = baseline.get(ref.key)
        if ref.container == RPR:
            value, source = resolve_run_value(paragraph, ref)
            actual: Any = _on_off(value) if ref.tag == "b" else value
            expected_value: Any = _on_off(expected) if ref.tag == "b" and expected is not None else expected
            if ref.tag == "b" and expected_value is None:
                expected_value = False
            if ref.tag == "b" and actual is None:
                actual = False
        else:
            actual, source = resolve_paragraph_value(paragraph, ref)
            expected_value = expected
        if actual == expected_value:
            continue
        issues.append({
            "code": "format_value_mismatch",
            "role": contract.role,
            "property": ref.key,
            "expected": expected_value,
            "actual": actual,
            "source": source,
            "path": path,
            "severity": "medium",
        })

    reported = _reported_paragraph_tags(contract, consumer)
    direct = _properties_element(paragraph._p, PPR)
    if direct is not None and reported:
        unexpected = sorted({child.tag.rsplit("}", 1)[-1] for child in direct} & set(reported))
        if unexpected:
            issues.append({
                "code": "format_unexpected_direct",
                "role": contract.role,
                "properties": unexpected,
                "path": path,
                "severity": "low",
            })

    issues.extend(_run_issues(paragraph, baseline, path, contract.role))
    return issues


def _iter_body_paragraphs(doc: Any) -> Iterable[tuple[str, Any]]:
    for index, paragraph in enumerate(doc.paragraphs, 1):
        yield f"paragraph:{index}", paragraph


def audit_document_format(
    doc: Any,
    profile: Mapping[str, Any],
    template_doc: Any,
    *,
    consumer: str = "audit",
) -> dict[str, Any]:
    """Check every paragraph whose role the resolver can place.

    The classification is style-independent: a paragraph is judged by what it is,
    so a document written with Word's stock styles or a custom style set is
    covered exactly like one built from the template.  A paragraph no layer can
    place is counted as unclassified rather than silently dropped.
    """

    baseline = derive_baseline(profile, template_doc)
    issues: list[dict[str, Any]] = []
    checked = 0
    unclassified = 0
    for path, paragraph in _iter_body_paragraphs(doc):
        resolved = resolve_role(paragraph, profile=profile)
        role = str(resolved["role"]) if resolved else None
        contract = CONTRACTS.get(role) if role else None
        if contract is None or role not in baseline:
            unclassified += 1
            continue
        checked += 1
        issues.extend(audit_paragraph(paragraph, contract, baseline[role], path, consumer=consumer))
    return {"passed": not issues, "issues": issues, "checked": checked, "unclassified": unclassified}
