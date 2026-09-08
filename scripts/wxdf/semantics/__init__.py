"""Semantic modules and compatibility adapters for the modular pipeline."""

from .compat import collect_compat_candidates
from .appendices import annotate_appendix_ranges, collect_appendix_candidates
from .captions import audit_caption_placement, build_caption_overlay, collect_caption_candidates, invalidate_overlay
from .protected import collect_protected_candidates
from .regions import classify_regions, collect_scope_candidates
from .structure import collect_structure_candidates

__all__ = [
    "classify_regions",
    "annotate_appendix_ranges",
    "audit_caption_placement",
    "build_caption_overlay",
    "collect_compat_candidates",
    "collect_appendix_candidates",
    "collect_caption_candidates",
    "collect_protected_candidates",
    "collect_scope_candidates",
    "collect_structure_candidates",
    "invalidate_overlay",
]
