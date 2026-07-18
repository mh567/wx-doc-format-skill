"""Map source list evidence onto the WX template list hierarchy."""

from __future__ import annotations


ORDERED_LIST_TYPES = frozenset({"lower_letter_paren", "decimal_paren"})
UNORDERED_LIST_TYPES = frozenset({"dash", "bullet_dot"})

_ROLE_STYLES = {
    "list_letter": "1.1一级列项-编号",
    "list_dash": "1.2一级列项-无编号",
    "list_decimal": "2.1二级列项-有编号",
    "list_bullet": "2.2二级列项-无编号",
}


def source_list_type(num_fmt: str | None, lvl_text: str | None) -> str:
    """Describe the source marker without assigning a WX hierarchy level."""
    marker = (lvl_text or "").strip()
    if num_fmt == "bullet":
        return "dash" if marker.startswith(("—", "-")) else "bullet_dot"
    if num_fmt in {"lowerLetter", "upperLetter"}:
        return "lower_letter_paren"
    return "decimal_paren"


def normalize_wx_list_type(list_type: str | None, level: int) -> str:
    """Return the marker type required by the WX hierarchy at *level*."""
    level = max(0, int(level or 0))
    if list_type in UNORDERED_LIST_TYPES:
        return "dash" if level == 0 else "bullet_dot"
    return "lower_letter_paren" if level == 0 else "decimal_paren"


def wx_list_role(list_type: str | None, level: int) -> str:
    normalized = normalize_wx_list_type(list_type, level)
    return {
        "lower_letter_paren": "list_letter",
        "dash": "list_dash",
        "decimal_paren": "list_decimal",
        "bullet_dot": "list_bullet",
    }[normalized]


def wx_list_style_name(
    list_type: str | None,
    level: int,
    template_profile: dict | None = None,
) -> str:
    role = wx_list_role(list_type, level)
    fallback = _ROLE_STYLES[role]
    if template_profile:
        return template_profile.get("resolved_styles", {}).get(role, fallback)
    return fallback


def wx_numbering_abstract_key(list_type: str | None, level: int) -> str | None:
    normalized = normalize_wx_list_type(list_type, level)
    if normalized == "lower_letter_paren":
        return "list_letter_abstract"
    if normalized == "decimal_paren":
        return "list_decimal_abstract"
    return None
