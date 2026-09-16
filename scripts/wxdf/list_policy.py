"""The swappable WX list presentation policies.

The policy owns the ``semantic_level -> render_level`` map and the style role
each rendered level uses.  It lives in its own module so both the semantics
layer (which records the projected ``render_level`` on every list item it
publishes) and the normalization layer (which projects with the run's policy)
consume one definition instead of restating it.  ``wxdf.normalization``
re-exports these names, which is where the published interface is imported from.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ListProjection:
    """The WX presentation a list item's semantic level projects to.

    ``render_level`` is the level the renderer emits as ``w:ilvl``;
    ``style_role`` selects the WX list paragraph style.  The two are separate so
    a policy can reuse a style at several rendered levels.

    ``clamped`` records that the semantic level was deeper than the policy's
    ``max_level`` and was pulled back to it, which is what makes the clamp an
    observable diagnostic instead of a silent rewrite.
    """

    render_level: int
    style_role: str
    clamped: bool = False


@dataclass(frozen=True)
class ListPolicy:
    """Projection from a list's semantic level to WX presentation.

    The role at index ``i`` is used for render level ``i``; the last role repeats
    for any deeper level, so adding a template level is a policy change that
    leaves ``semantics/lists.py`` untouched.

    ``max_level`` caps the rendered level.  The product default ``wx-default-v2``
    sets it to ``1`` because ``AGENTS.md`` decision 1 recognises at most two list
    levels: semantic level 0 projects to render level 0 (the kind's first-level
    role) and semantic level 1 or deeper projects to render level 1 (the kind's
    second-level role), with anything deeper clamped to 1 and reported as
    ``list_render_level_clamped`` so the author can express the deeper structure
    with headings instead.  A policy that leaves ``max_level`` as ``None`` keeps
    the true semantic level, which is what the ``legacy`` policy and a
    caller-supplied policy do.

    ``chapter_scoped_sequence`` keeps the ``wx-default-v2`` ordered behaviour
    (per-chapter numbering scope and an ordered ordinal recomputed from source
    order).
    """

    name: str
    ordered_roles: tuple[str, ...]
    unordered_roles: tuple[str, ...]
    chapter_scoped_sequence: bool = False
    max_level: int | None = None

    def project(self, list_kind: str, semantic_level: int) -> ListProjection:
        try:
            level = max(0, int(semantic_level))
        except (TypeError, ValueError):
            level = 0
        roles = self.unordered_roles if list_kind == "unordered" else self.ordered_roles
        render_level = level
        clamped = False
        if self.max_level is not None and level > self.max_level:
            render_level = self.max_level
            clamped = True
        return ListProjection(
            render_level=render_level,
            style_role=roles[min(render_level, len(roles) - 1)],
            clamped=clamped,
        )


DEFAULT_POLICY_NAME = "wx-default-v2"

# ``legacy`` keeps the historical per-depth style projection with the template's
# own numbering bindings and no level cap, so a caller that supplies it still
# exercises the swap capability.  ``wx-default-v2`` is the product default: it
# caps the render level at the two levels decision 1 recognises, and it binds
# each role to the template instance that was designed for that level, so the
# first level shows ``a)`` and the second level shows the template's own ``1)``
# / second-level bullet form instead of a synthesized outline ``a.1``.
LIST_POLICIES: dict[str, ListPolicy] = {
    "legacy": ListPolicy(
        name="legacy",
        ordered_roles=("list_letter", "list_decimal"),
        unordered_roles=("list_dash", "list_bullet"),
    ),
    DEFAULT_POLICY_NAME: ListPolicy(
        name=DEFAULT_POLICY_NAME,
        ordered_roles=("list_letter", "list_decimal"),
        unordered_roles=("list_dash", "list_bullet"),
        chapter_scoped_sequence=True,
        max_level=1,
    ),
}


def default_projection(list_kind: str, semantic_level: int) -> ListProjection:
    """Project one list item through the product default policy.

    The list semantics layer records the projected ``render_level`` before a run
    policy is known, so it projects through the default; the normalization layer
    re-projects the same semantic level with the run's policy.
    """

    return LIST_POLICIES[DEFAULT_POLICY_NAME].project(list_kind, semantic_level)


# ---------------------------------------------------------------------------
# Source marker vocabulary and WX role mapping.
#
# This leaf owns the one definition of the source marker vocabulary
# (``source_list_type`` / ``normalize_wx_list_type``), the WX role each marker
# maps to, and the template style each role resolves to.  It used to live in the
# standalone ``list_style_mapping`` module that both the native evidence layer
# and the transitional legacy renderers imported; T10 converged that single
# definition here so there is one owner instead of a duplicate module.
# ---------------------------------------------------------------------------

ORDERED_LIST_TYPES = frozenset({"lower_letter_paren", "decimal_paren"})
UNORDERED_LIST_TYPES = frozenset({"dash", "bullet_dot"})

_ROLE_STYLES = {
    "list_letter": "1.1一级列项-编号",
    "list_dash": "1.2一级列项-无编号",
    "list_decimal": "2.1二级列项-有编号",
    "list_bullet": "2.2二级列项-无编号",
}


def is_marker_only_level(num_fmt: str | None, style_name: str | None, lvl_text: str | None) -> bool:
    """Return whether a list level carries its marker in the level text alone.

    The WX 一级列项-无编号 form is authored as a list style whose level has
    ``numFmt=none`` and a dash ``lvlText``: the level numbers nothing but it
    still marks the paragraph as a list item.  One owner for the rule, so the
    per-paragraph view and the group view classify it the same way.
    """

    if num_fmt != "none":
        return False
    if "列项" not in str(style_name or ""):
        return False
    return bool(str(lvl_text or "").strip())


def source_list_type(num_fmt: str | None, lvl_text: str | None) -> str:
    """Describe the source marker without assigning a WX hierarchy level."""
    marker = (lvl_text or "").strip()
    if num_fmt == "bullet":
        return "dash" if marker.startswith(("—", "-")) else "bullet_dot"
    if num_fmt == "none":
        # An unnumbered list level carries its marker in the level text alone:
        # the WX 一级列项-无编号 form is a dash, the second level is a dot.
        if marker.startswith(("—", "-")):
            return "dash"
        if marker.startswith("·") or not marker:
            return "bullet_dot"
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
    if normalized == "dash":
        return "list_dash_abstract"
    if normalized == "decimal_paren":
        return "list_decimal_abstract"
    if normalized == "bullet_dot":
        return "list_bullet_abstract"
    return None
