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
