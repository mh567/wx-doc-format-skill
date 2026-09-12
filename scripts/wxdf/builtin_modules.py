"""Compatibility exports for the modular conversion module seam."""

from .native_stages import (
    LedgerReplayBlocked,
    NativeEnhancementBlocked,
    NativeFileProtocolPause,
    UnresolvedDecisionError,
    _native_auditor,
    _native_candidate_set,
    _native_normalizer,
    _native_publisher,
    _native_renderer,
    _native_resolver,
    _native_reviewer,
    _native_source_ast,
    _native_transaction,
    native_review_candidate_set_module,
    native_stage_executors,
)
from .semantic_modules import (
    ModuleExecutionContext,
    appendix_semantics_module,
    build_native_candidate_baseline,
    caption_semantics_module,
    collect_native_candidates,
    document_structure_module,
    list_semantics_module,
    native_semantic_modules,
    protected_content_module,
    source_facts_module,
    source_region_module,
    table_semantics_module,
)
from .semantics.appendices import collect_native_appendix_candidates, collect_source_appendix_semantics, prepare_native_appendix_semantics
from .semantics.captions import collect_caption_candidates
from .semantics.lists import analyze_lists
from .semantics.protected import collect_protected_candidates
from .semantics.regions import resolve_source_regions
from .semantics.source_ast import materialize_list_semantics
from .semantics.structure import collect_structure_candidates
from .semantics.tables import collect_table_candidates
from .parsers import parse

__all__ = [name for name in globals() if not name.startswith("__")]
