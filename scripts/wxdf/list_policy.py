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
    """

    render_level: int
    style_role: str


@dataclass(frozen=True)
class ListPolicy:
    """Projection from a list's semantic level to WX presentation.

    The role at index ``i`` is used for render level ``i``; the last role repeats
    for any deeper level, so adding a template level is a policy change that
    leaves ``semantics/lists.py`` untouched.  Both shipping policies keep the
    true semantic level, so the projection has no level cap: a bounded
    projection would need a policy that sets one, and no such policy exists, so
    the cap was removed instead of shipping a knob no policy reads.

    ``chapter_scoped_sequence`` keeps the ``wx-default-v2`` ordered behaviour
    (per-chapter numbering scope and an ordered ordinal recomputed from source
    order).  ``bind_multilevel_numbering`` makes the policy synthesize a
    list-only numbering instance for the ordered roles in the produced document,
    so the emitted ``w:ilvl`` is a real level instead of a clamped zero and the
    list numbering never shares counters with the heading outline.
    """

    name: str
    ordered_roles: tuple[str, ...]
    unordered_roles: tuple[str, ...]
    chapter_scoped_sequence: bool = False
    bind_multilevel_numbering: bool = False

    def project(self, list_kind: str, semantic_level: int) -> ListProjection:
        try:
            level = max(0, int(semantic_level))
        except (TypeError, ValueError):
            level = 0
        roles = self.unordered_roles if list_kind == "unordered" else self.ordered_roles
        return ListProjection(render_level=level, style_role=roles[min(level, len(roles) - 1)])

    def deeper_roles(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.ordered_roles[1:], *self.unordered_roles[1:])))


DEFAULT_POLICY_NAME = "wx-default-v2"

# ``legacy`` keeps the historical per-depth style projection with the template's
# own (mostly single-level) numbering bindings; ``wx-default-v2`` is the product
# default, keeps the true semantic level, and synthesizes a list-only numbering
# instance the produced document materializes so real multi-level numbering is
# emitted without sharing counters with the heading outline.
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
        bind_multilevel_numbering=True,
    ),
}


def default_projection(list_kind: str, semantic_level: int) -> ListProjection:
    """Project one list item through the product default policy.

    The list semantics layer records the projected ``render_level`` before a run
    policy is known, so it projects through the default; the normalization layer
    re-projects the same semantic level with the run's policy.
    """

    return LIST_POLICIES[DEFAULT_POLICY_NAME].project(list_kind, semantic_level)
