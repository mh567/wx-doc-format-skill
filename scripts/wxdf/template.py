"""Template capability loading and preflight for the modular path."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from template_profile import load_template_profile
from text_utils import CAPTION_SEQUENCE_FALLBACK, CAPTION_SEQUENCE_KINDS, sequence_identifier
from toc_contract import TOC_MAX_LEVEL


class TemplateCapabilityError(ValueError):
    """Raised when a template cannot express required semantic roles."""


def extract_template_toc_fragment(document: Any) -> list:
    """Copy the template TOC content control and its trailing page break.

    The native chain owns this read of the template OOXML so that loading a
    template never depends on the legacy finalizer.
    """

    children = list(document.element.body)
    for index, child in enumerate(children):
        if child.tag != qn("w:sdt"):
            continue
        instructions = [node.text or "" for node in child.iter(qn("w:instrText"))]
        if not any(re.search(r"\bTOC\b", value, re.I) for value in instructions):
            continue
        fragment = [deepcopy(child)]
        if index + 1 < len(children):
            following = children[index + 1]
            if following.tag == qn("w:p") and any(
                node.get(qn("w:type")) == "page" for node in following.iter(qn("w:br"))
            ):
                fragment.append(deepcopy(following))
        return fragment
    return []


def _paragraph_style_id(paragraph: Any) -> str | None:
    properties = paragraph.find(qn("w:pPr"))
    style = properties.find(qn("w:pStyle")) if properties is not None else None
    return style.get(qn("w:val")) if style is not None else None


def _caption_label(paragraph: Any) -> str:
    """Return the label the caption paragraph leads with."""

    for node in paragraph.iter(qn("w:t")):
        text = (node.text or "").strip()
        if text:
            return text
    return ""


def _has_graphic(element: Any) -> bool:
    return any(node.tag in {qn("w:drawing"), qn("w:pict"), qn("w:object")} for node in element.iter())


def extract_caption_sequences(document: Any, profile: Mapping[str, Any]) -> dict[str, Any]:
    """Read the sequence identifier the template uses for each caption kind.

    Delivered captions have to look like the template's, and Word matches a
    cross-reference item by the sequence identifier, so the identifier is the
    template's own (``SEQ 表`` / ``SEQ 图``) instead of a name this converter
    invents.  A caption's own label names its kind and is the deciding evidence.
    When the label is not one the WX convention knows, the object the caption
    sits next to decides: a caption above a table labels that table and a caption
    below a graphic labels that graphic.  A kind the template does not show falls
    back to the WX label, and ``source`` records whether both kinds came from the
    template.
    """

    resolved = profile.get("resolved_styles") or {}
    caption_style = resolved.get("caption")
    style_id = ((profile.get("styles") or {}).get(caption_style) or {}).get("style_id") if caption_style else None
    if style_id is None:
        # Without a caption style there is nothing to identify, and an empty id
        # would match every paragraph that carries no style at all.
        return {
            "table_sequence": CAPTION_SEQUENCE_FALLBACK["table"],
            "figure_sequence": CAPTION_SEQUENCE_FALLBACK["figure"],
            "source": "fallback",
        }
    children = list(document.element.body)
    found: dict[str, str] = {}
    for index, child in enumerate(children):
        if child.tag != qn("w:p") or _paragraph_style_id(child) != style_id:
            continue
        identifier = sequence_identifier(child)
        if not identifier:
            continue
        kind = CAPTION_SEQUENCE_KINDS.get(_caption_label(child))
        if kind is None:
            following = children[index + 1] if index + 1 < len(children) else None
            previous = children[index - 1] if index > 0 else None
            if following is not None and following.tag == qn("w:tbl"):
                kind = "table"
            elif previous is not None and _has_graphic(previous):
                kind = "figure"
        if kind and kind not in found:
            found[kind] = identifier
    return {
        "table_sequence": found.get("table", CAPTION_SEQUENCE_FALLBACK["table"]),
        "figure_sequence": found.get("figure", CAPTION_SEQUENCE_FALLBACK["figure"]),
        "source": "template" if set(CAPTION_SEQUENCE_FALLBACK) <= set(found) else "fallback",
    }


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _materialize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_materialize(item) for item in value]
    return value


@dataclass(frozen=True)
class TemplateBundle:
    """Immutable template capabilities and retained presentation fragments."""

    schema_version: str
    path: Path
    template_bytes: bytes
    template_sha256: str
    profile: Mapping[str, Any]
    presentation_contract: Mapping[str, Any]
    toc_fragment_xml: tuple[bytes, ...]


def load_template_bundle(
    template: Path | str,
    *,
    template_bytes: bytes | None = None,
) -> TemplateBundle:
    template_path = Path(template)
    raw = template_path.read_bytes() if template_bytes is None else bytes(template_bytes)
    with tempfile.TemporaryDirectory(prefix="wxdf-template-") as directory:
        snapshot = Path(directory) / "template.docx"
        snapshot.write_bytes(raw)
        profile = load_template_profile(snapshot)
    profile["path"] = str(template_path)
    document = Document(io.BytesIO(raw))
    toc_fragment = tuple(
        etree.tostring(element, encoding="utf-8")
        for element in extract_template_toc_fragment(document)
    )
    resolved = profile.get("resolved_styles", {})
    contract = {
        "source": {
            "path": str(template_path),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "styles": {
            "resolved_roles": dict(resolved),
            "missing_roles": list(profile.get("missing_roles") or []),
        },
        "numbering": {"ids": dict(profile.get("numbering_ids") or {})},
        "tables": dict(profile.get("table_style") or {}),
        "toc": {
            "mode": "template_fragment" if toc_fragment else "generated",
            "fragment_count": len(toc_fragment),
            "max_level": TOC_MAX_LEVEL,
            "update_fields_on_open": True,
        },
        "appendix": {
            "title_style": resolved.get("appendix_title"),
            "heading_styles": {
                str(level): resolved.get(f"appendix_heading_{level}")
                for level in range(1, 4)
            },
            "page_break_before": True,
        },
        "page_layout": {"preserve_template_sections": True},
        "captions": extract_caption_sequences(document, profile),
    }
    return TemplateBundle(
        schema_version="1.0",
        path=template_path,
        template_bytes=raw,
        template_sha256=contract["source"]["sha256"],
        profile=_freeze(profile),
        presentation_contract=_freeze(contract),
        toc_fragment_xml=toc_fragment,
    )


def profile_from(template: Mapping[str, Any] | TemplateBundle) -> Mapping[str, Any]:
    return template.profile if isinstance(template, TemplateBundle) else template


def materialize_profile(template: Mapping[str, Any] | TemplateBundle) -> dict[str, Any]:
    return _materialize(profile_from(template))


def presentation_contract_from(template: Mapping[str, Any] | TemplateBundle) -> dict[str, Any]:
    if isinstance(template, TemplateBundle):
        return _materialize(template.presentation_contract)
    return {}


def load_profile(template: Path | str) -> dict[str, Any]:
    return load_template_profile(template)


def preflight_template(profile: Mapping[str, Any], *, required_roles: Sequence[str] = ()) -> dict[str, Any]:
    resolved = dict(profile.get("resolved_styles") or {})
    missing = sorted({str(role) for role in required_roles if not resolved.get(str(role))})
    if missing:
        raise TemplateCapabilityError(f"template is missing required roles: {', '.join(missing)}")
    return {
        "ok": True,
        "resolved_roles": {role: resolved[role] for role in sorted(set(required_roles)) if role in resolved},
        "missing_roles": missing,
        "numbering_ids": dict(profile.get("numbering_ids") or {}),
    }


def required_list_roles(normalized: Mapping[str, Any]) -> list[str]:
    roles = set()
    for node in (normalized.get("presentation") or {}).get("nodes", {}).values():
        style_role = node.get("style_role")
        if style_role:
            roles.add(str(style_role))
    return sorted(roles)
