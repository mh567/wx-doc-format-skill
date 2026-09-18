"""LLM enhancement capabilities and the mode that selects them.

A capability is identified by the name the CLI and the file protocol use.  The
registry is the single place a capability enters the pipeline; the phase letter
attached to each name survives in the request contract and in the compact mode
strings, so both directions of that mapping live here.
"""

from __future__ import annotations

from dataclasses import dataclass


AUTO_TRIGGER_THRESHOLD = 0.15
"""Suspicion score at or above which ``auto`` mode runs the gated capability."""


@dataclass(frozen=True)
class CapabilityConfig:
    """Descriptor for an LLM enhancement capability, keyed by its name."""

    name: str


CAPABILITY_REGISTRY: dict[str, CapabilityConfig] = {}

# Phase letter ↔ capability name (e.g. "A" ↔ "list_detect").
_CAPABILITY_BY_PHASE: dict[str, str] = {}
_PHASE_BY_CAPABILITY: dict[str, str] = {}


def register_capability(config: CapabilityConfig) -> CapabilityConfig:
    """Register a capability, rejecting a name that is already taken."""
    if config.name in CAPABILITY_REGISTRY:
        raise ValueError(f"Duplicate capability {config.name!r}")
    CAPABILITY_REGISTRY[config.name] = config
    return config


def _register_phase_alias(phase: str, capability_name: str) -> None:
    if phase in _CAPABILITY_BY_PHASE:
        raise ValueError(f"Duplicate phase alias {phase!r}")
    if capability_name not in CAPABILITY_REGISTRY:
        raise ValueError(f"Unknown capability {capability_name!r}")
    _CAPABILITY_BY_PHASE[phase] = capability_name
    _PHASE_BY_CAPABILITY[capability_name] = phase


def _resolve_capability(name: str) -> str | None:
    """Resolve a phase letter or capability name to a registry key.

    Returns ``None`` when neither an alias nor a registry key matches.
    """
    if name in CAPABILITY_REGISTRY:
        return name
    capability = _CAPABILITY_BY_PHASE.get(name)
    if capability in CAPABILITY_REGISTRY:
        return capability
    return None


def _resolve_legacy_phase(name: str) -> str:
    """Map a capability name to its phase letter; other input passes through."""
    return _PHASE_BY_CAPABILITY.get(name, name)


def _register_default_capabilities() -> None:
    for name in ("toc_region_review", "list_detect", "caption_gen", "document_review"):
        register_capability(CapabilityConfig(name))
    _register_phase_alias("S", "toc_region_review")
    _register_phase_alias("A", "list_detect")
    _register_phase_alias("B", "caption_gen")
    _register_phase_alias("C", "document_review")
    # The review-side capabilities.  They read the source document and produce
    # findings rather than patch candidates, so they have their own phases and
    # never run inside the conversion chain.
    for name in REVIEW_CAPABILITIES:
        register_capability(CapabilityConfig(name))
    _register_phase_alias("D", "citation_order_review")
    _register_phase_alias("E", "glossary_review")
    _register_phase_alias("F", "list_prose_review")


REVIEW_CAPABILITIES: tuple[str, ...] = (
    "citation_order_review",
    "glossary_review",
    "list_prose_review",
)
"""Capabilities the input review runs; the conversion chain never selects them."""

REVIEW_PHASES: frozenset[str] = frozenset({"D", "E", "F"})
"""Phase letters belonging to the review surface."""

CONVERSION_PHASES: frozenset[str] = frozenset({"S", "A", "B", "C"})
"""Phase letters belonging to the conversion surface."""


_register_default_capabilities()


_ENHANCE_MODE_ALIASES: dict[str, str] = {
    "toc_region_review": "s",
    "list_detect": "a",
    "caption_gen": "b",
    "document_review": "c",
    "citation_order_review": "d",
    "glossary_review": "e",
    "list_prose_review": "f",
    # ``all`` means every registered capability: each surface takes the phases it
    # owns, so one word selects the conversion's S/A/B/C and the review's D/E/F.
    "all": "sabcdef",
}

# Phase letters selected by each compact mode string.  The ``force-`` variants
# are reserved for a future modification-rate gate override and behave as their
# plain counterparts.
_ENHANCE_MODE_PHASES: dict[str, frozenset[str]] = {
    "s": frozenset({"S"}),
    "a": frozenset({"A"}),
    "b": frozenset({"B"}),
    "sa": frozenset({"S", "A"}),
    "sb": frozenset({"S", "B"}),
    "sab": frozenset({"S", "A", "B"}),
    "c": frozenset({"C"}),
    "sc": frozenset({"S", "C"}),
    "ac": frozenset({"A", "C"}),
    "bc": frozenset({"B", "C"}),
    "sac": frozenset({"S", "A", "C"}),
    "sbc": frozenset({"S", "B", "C"}),
    "abc": frozenset({"A", "B"}),
    "sabc": frozenset({"S", "A", "B", "C"}),
    "ab": frozenset({"A", "B"}),
    "d": frozenset({"D"}),
    "e": frozenset({"E"}),
    "f": frozenset({"F"}),
    "de": frozenset({"D", "E"}),
    "df": frozenset({"D", "F"}),
    "ef": frozenset({"E", "F"}),
    "def": frozenset({"D", "E", "F"}),
    "sabcdef": frozenset({"S", "A", "B", "C", "D", "E", "F"}),
    "force-d": frozenset({"D"}),
    "force-e": frozenset({"E"}),
    "force-f": frozenset({"F"}),
    "force-def": frozenset({"D", "E", "F"}),
    "force-sabcdef": frozenset({"S", "A", "B", "C", "D", "E", "F"}),
    "force-s": frozenset({"S"}),
    "force-a": frozenset({"A"}),
    "force-b": frozenset({"B"}),
    "force-sa": frozenset({"S", "A"}),
    "force-sb": frozenset({"S", "B"}),
    "force-sab": frozenset({"S", "A", "B"}),
    "force-c": frozenset({"C"}),
    "force-sc": frozenset({"S", "C"}),
    "force-ac": frozenset({"A", "C"}),
    "force-bc": frozenset({"B", "C"}),
    "force-sac": frozenset({"S", "A", "C"}),
    "force-sbc": frozenset({"S", "B", "C"}),
    "force-abc": frozenset({"A", "B"}),
    "force-sabc": frozenset({"S", "A", "B", "C"}),
    "force-ab": frozenset({"A", "B"}),
}


def normalize_mode(mode: str) -> str:
    """Normalize a user-supplied enhancement mode.

    Accepts registered capability names and ``"all"``, as well as compact phase
    combinations such as ``"ab"`` and ``"sabc"``, plus their ``"force-"``
    variants, and ``"auto"`` / ``"off"``.  Unknown modes are returned as-is.
    """
    return _ENHANCE_MODE_ALIASES.get(mode, mode)


def compute_suspicion_score(report: dict) -> float:
    """Compute a 0–1 *suspicion score* from the parse report.

    Higher values suggest the source document has structural ambiguity that
    would benefit from LLM enhancement.
    """
    summary = report.get("source_document_model_summary", {})
    parse_report = report.get("parse_report", {})
    block_counts = summary.get("block_counts", {})
    total_blocks = summary.get("block_count", 0) or 1

    def _ratio(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator > 0 else 0.0

    n_body = block_counts.get("body", 0) or 1
    n_heading = block_counts.get("heading", 0) or 1
    n_list = block_counts.get("list_item", 0) or 1

    # Signals arrive as an int (DOCX) or a list (Markdown); normalize to int.
    def _signal(value) -> float:
        return len(value) if isinstance(value, list) else (value if isinstance(value, (int, float)) else 0)

    suspect_visual_headings = _signal(parse_report.get("suspect_visual_headings", 0))
    ambiguous_short_paragraphs = _signal(parse_report.get("ambiguous_short_paragraphs", 0))
    ambiguous_unordered_paragraphs = _signal(parse_report.get("ambiguous_unordered_paragraphs", 0))
    ambiguous_short_paragraphs = max(ambiguous_short_paragraphs, ambiguous_unordered_paragraphs)
    inferred_headings = _signal(parse_report.get("inferred_headings", 0))
    inferred_lists = _signal(parse_report.get("inferred_lists", 0))
    unstyled_paragraphs = _signal(parse_report.get("unstyled_paragraphs", 0))

    n_table = block_counts.get("table", 0)
    n_image = block_counts.get("image", 0)

    raw = (
        0.25 * min(_ratio(suspect_visual_headings, n_body), 1.0)
        + 0.20 * min(_ratio(ambiguous_short_paragraphs, n_body), 1.0)
        + 0.15 * min(_ratio(inferred_headings, n_heading), 1.0)
        + 0.10 * min(_ratio(inferred_lists, n_list), 1.0)
        + 0.15 * min(_ratio(unstyled_paragraphs, n_body), 1.0)
        + 0.10 * min(_ratio(n_table, total_blocks), 1.0)
        + 0.05 * min(_ratio(n_image, total_blocks), 1.0)
    )

    return min(raw, 1.0)


def should_enhance(report: dict, phase: str, mode: str) -> bool:
    """Decide whether LLM enhancement should run for *phase* in *mode*.

    *mode* values
    -------------
    ``"off"``
        Never enhance — returns ``False`` for every phase.
    ``"auto"``
        Only enhance if ``compute_suspicion_score(report) >= 0.15`` for phase
        ``"A"``.
    Compact phase combinations such as ``"a"``, ``"ab"`` and ``"sabc"``
        Force-enable the named phases (bypass suspicion check).
    ``"abc"``
        Legacy alias for phases A and B. Use ``"sabc"`` or ``"all"`` to enable
        the post-audit C phase.
    ``"force-a"`` / ``"force-ab"`` / ``"force-sabc"``
        Same as the non-*force* variants.
    """
    phase = _resolve_legacy_phase(phase)

    if mode == "off":
        return False

    if mode in _ENHANCE_MODE_PHASES:
        return phase in _ENHANCE_MODE_PHASES[mode]

    if mode == "auto":
        summary = report.get("source_document_model_summary", {})
        block_count = summary.get("block_count", 0) or 0

        if phase == "S":
            return report.get("source_toc", {}).get("status") == "ambiguous"
        if phase == "A":
            # Short documents with few blocks are likely clean.
            if block_count < 5:
                return False
            # Without a template there are no heading/list styles to
            # override, so there is nothing for a classifier to act on.
            if not report.get("template_profile"):
                return False
            return compute_suspicion_score(report) >= AUTO_TRIGGER_THRESHOLD
        if phase == "B":
            # Gate on the modification rate the first phase achieved.
            applied_rate = report.get("llm_enhancer", {}).get("phase_a_applied_rate", 0.0)
            return applied_rate >= 0.05
        if phase == "C":
            return bool(report.get("review_packet", {}).get("targets"))
        return False

    return False
