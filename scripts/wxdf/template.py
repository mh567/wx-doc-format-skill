"""Template capability loading and preflight for the modular path."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from docx import Document
from lxml import etree

from template_profile import load_template_profile
from template_finalizer import extract_template_toc_fragment
from toc_contract import TOC_MAX_LEVEL


class TemplateCapabilityError(ValueError):
    """Raised when a template cannot express required semantic roles."""


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
    template_sha256: str
    profile: Mapping[str, Any]
    presentation_contract: Mapping[str, Any]
    toc_fragment_xml: tuple[bytes, ...]


def load_template_bundle(template: Path | str) -> TemplateBundle:
    template_path = Path(template)
    profile = load_template_profile(template_path)
    document = Document(str(template_path))
    toc_fragment = tuple(
        etree.tostring(element, encoding="utf-8")
        for element in extract_template_toc_fragment(document)
    )
    resolved = profile.get("resolved_styles", {})
    contract = {
        "source": {
            "path": str(template_path),
            "sha256": hashlib.sha256(template_path.read_bytes()).hexdigest(),
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
        "captions": {"table_sequence": "Table", "figure_sequence": "Figure"},
    }
    return TemplateBundle(
        schema_version="1.0",
        path=template_path,
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
