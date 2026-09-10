"""Executable built-in modules composed by the conversion kernel."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .parsers import digest_source_facts, parse, summarize_source_facts
from .registry import ModuleDescriptor, PlanError, RegisteredModule
from .semantics.regions import resolve_source_regions
from .semantics.structure import collect_structure_candidates
from .semantics.equivalence import compare_structure_equivalence
from .semantics.parser_equivalence import compare_parser_equivalence
from .semantics.source_projection import project_source_facts
from .semantics.protected import collect_protected_candidates
from .semantics.lists import analyze_lists
from .semantics.tables import collect_source_table_semantics, collect_table_candidates
from .semantics.captions import collect_caption_candidates, collect_source_caption_semantics
from .semantics.appendices import (
    audit_native_appendix,
    collect_native_appendix_candidates,
    collect_source_appendix_semantics,
    prepare_native_appendix_semantics,
)
from .semantics.source_ast import build_source_ast, materialize_list_semantics


class LedgerReplayBlocked(RuntimeError):
    """Raised when a supplied decision ledger forbids the native replay."""

    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__("cannot apply invalidated or conflicted revisions")


class UnresolvedDecisionError(RuntimeError):
    """Raised when the registered failure policy forbids publishing unresolved groups."""

    def __init__(self, unresolved: list[dict[str, Any]]) -> None:
        self.unresolved = list(unresolved)
        super().__init__(
            f"{len(self.unresolved)} unresolved decision group(s) block publication"
        )


class NativeFileProtocolPause(RuntimeError):
    """Stop the native chain after request capture and before resolution."""

    def __init__(self) -> None:
        super().__init__("native file protocol is waiting for external responses")


class NativeEnhancementBlocked(RuntimeError):
    """Stop native execution when a requested enhancement was not validated."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        self.result = dict(result)
        self.diagnostics = [
            dict(diagnostic)
            for record in result.get("results", [])
            if isinstance(record, Mapping)
            for diagnostic in record.get("diagnostics", [])
            if isinstance(diagnostic, Mapping)
        ]
        super().__init__("native enhancement response validation failed")


@dataclass
class ModuleExecutionContext:
    """Per-conversion capability bus shared by executable modules.

    ``require`` and ``provide`` are the only way a module reaches the shared
    artifacts, so the bus observes the capabilities a module really read and
    wrote while it was active.
    """

    request: Any
    artifacts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    _active: dict[str, Any] | None = field(default=None, repr=False)
    _reads: list[str] = field(default_factory=list, repr=False)
    _writes: list[str] = field(default_factory=list, repr=False)

    def begin_module(self, descriptor: ModuleDescriptor, position: int) -> None:
        """Start observing the capabilities of one module execution."""

        self._active = {"module_id": descriptor.module_id, "position": position}
        self._reads = []
        self._writes = []

    def reset_observation(self) -> None:
        """Drop the capability observations of the failing attempt."""

        self._reads = []
        self._writes = []

    def end_module(self, status: str, error: BaseException | None = None) -> dict[str, Any]:
        """Close the active execution and merge its observation into the record."""

        active = self._active or {}
        module_id = str(active.get("module_id") or "")
        observation: dict[str, Any] = {
            "order": int(active.get("position") or 0),
            "produced": list(self._writes),
            "consumed": list(self._reads),
            "status": status,
        }
        if error is not None:
            observation["error"] = {"type": type(error).__name__, "message": str(error)}
        # The record is a fresh mapping so that a Module summary shared with a
        # published artifact is never mutated by the observation.
        record = {**self.evidence.get(module_id, {}), **observation}
        self.evidence[module_id] = record
        self._active = None
        self._reads = []
        self._writes = []
        return record

    def provide(self, capability: str, value: Any) -> None:
        if capability in self.artifacts:
            raise PlanError(f"runtime capability {capability} already provided")
        self.artifacts[capability] = value
        if self._active is not None and capability not in self._writes:
            self._writes.append(capability)

    def require(self, capability: str) -> Any:
        if capability not in self.artifacts:
            raise PlanError(f"runtime capability {capability} is unavailable")
        if self._active is not None and capability not in self._reads:
            self._reads.append(capability)
        return self.artifacts[capability]

    def stage(self, name: str) -> Any:
        """Time a module stage when the request wired a metrics collector."""

        return _metrics_stage(self, name)


@contextmanager
def _metrics_stage(context: ModuleExecutionContext, name: str) -> Iterator[None]:
    metrics = context.services.get("metrics")
    if metrics is None:
        yield
        return
    with metrics.stage(name):
        yield


def _source_facts_summary(facts: Mapping[str, Any]) -> dict[str, Any]:
    return summarize_source_facts(dict(facts))


# ---------------------------------------------------------------------------
# Semantic modules: parsing, regions, structure, protected content and the
# list, table, caption and appendix candidate owners.
# ---------------------------------------------------------------------------


def source_facts_module(*, native: bool = False) -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        with context.stage("source"):
            restored = (context.request.options or {}).get("restored_source_facts")
            facts = (
                json.loads(json.dumps(restored, ensure_ascii=False))
                if isinstance(restored, Mapping)
                else parse(context.request.input_path, {"source_store": context.services.get("store")})
            )
            metrics = context.services.get("metrics")
            if metrics is not None:
                metrics.record_source_opened()
            summary = _source_facts_summary(facts)
            digest = digest_source_facts(dict(facts))
        context.provide("source_facts", facts)
        context.evidence["source-facts-parser"] = summary
        if native:
            context.provide("source_facts_summary", summary)
            context.provide("source_facts_digest", digest)
        return summary

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="source-facts-parser",
            version="1.0",
            stages=("source",),
            requires=(),
            provides=("source_facts", "source_facts_summary", "source_facts_digest")
            if native
            else ("source_facts",),
        ),
        execute=execute,
    )


def source_region_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        facts = context.require("source_facts")
        resolution = resolve_source_regions(context.request.input_path, facts)
        regions = resolution["regions"]
        candidates = resolution["candidates"]
        context.provide("source_regions", regions)
        context.provide("source_region_candidates", candidates)
        context.provide("source_region", resolution)
        evidence = resolution["evidence"]
        context.evidence["source-region"] = evidence
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="source-region",
            version="1.0",
            stages=("scope",),
            requires=("source_facts",),
            provides=("source_regions", "source_region_candidates", "source_region"),
        ),
        execute=execute,
    )


def document_structure_module(*, protect_semantics: bool = False) -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        candidates = collect_structure_candidates(
            context.require("source_facts"),
            context.require("source_regions"),
            protect_semantics=protect_semantics,
        )
        context.provide("document_structure_candidates", candidates)
        evidence = {
            "candidate_count": len(candidates),
            "title_candidate_count": sum(
                item.get("proposal", {}).get("value", {}).get("role") == "title"
                for item in candidates
            ),
            "heading_candidate_count": sum(
                item.get("proposal", {}).get("value", {}).get("role") == "heading"
                for item in candidates
            ),
            "body_candidate_count": sum(
                item.get("proposal", {}).get("value", {}).get("role") == "body"
                for item in candidates
            ),
        }
        context.evidence["document-structure"] = evidence
        context.provide("document_structure", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="document-structure",
            version="1.0",
            stages=("role",),
            requires=("source_facts", "source_regions"),
            provides=("document_structure_candidates", "document_structure"),
        ),
        execute=execute,
    )


def source_projection_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        projection = project_source_facts(
            context.require("source_facts"),
            context.require("source_regions"),
        )
        context.provide("source_projection", projection)
        evidence = {
            "paragraph_count": len(projection["paragraphs"]),
            "table_count": len(projection["tables"]),
            "image_count": projection["image_count"],
            "list_item_count": projection["list_item_count"],
        }
        context.evidence["source-projection"] = evidence
        context.provide("source_parser", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="source-projection",
            version="1.0",
            stages=("role",),
            requires=("source_facts", "source_regions"),
            provides=("source_projection", "source_parser"),
        ),
        execute=execute,
    )


def protected_content_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        candidates = collect_protected_candidates(context.require("source_facts"))
        context.provide("protected_content_candidates", candidates)
        evidence = {
            "candidate_count": len(candidates),
            "note_count": sum(
                item.get("proposal", {}).get("value", {}).get("reason") == "note"
                for item in candidates
            ),
            "opaque_count": sum(
                str(item.get("proposal", {}).get("value", {}).get("reason") or "").startswith("opaque_")
                for item in candidates
            ),
        }
        context.evidence["protected-content"] = evidence
        context.provide("protected_content", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="protected-content",
            version="1.0",
            stages=("role",),
            requires=("source_facts",),
            provides=("protected_content_candidates", "protected_content"),
        ),
        execute=execute,
    )


def list_semantics_module(*, native: bool = False) -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        facts = context.require("source_facts")
        if native:
            semantics = analyze_lists(facts)
            materialized, candidates, item_map = materialize_list_semantics(
                context.require("source_ast"),
                facts,
                semantics,
            )
        else:
            protected_ids = {
                str(node_id)
                for candidate in context.require("protected_content_candidates")
                for node_id in candidate.get("target_node_ids", [])
            }
            structure_ids = {
                str(node_id)
                for candidate in context.require("document_structure_candidates")
                if candidate.get("proposal", {}).get("value", {}).get("role") in {"title", "heading"}
                for node_id in candidate.get("target_node_ids", [])
            }
            semantics = analyze_lists(
                facts,
                context={"protected_node_ids": sorted(protected_ids | structure_ids)},
            )
        context.provide("list_semantics_result", semantics)
        evidence = {
            "status": semantics["status"],
            "list_count": len(semantics["lists"]),
            "item_count": len(semantics["items"]),
            "continuation_count": len(semantics["continuations"]),
            "diagnostic_count": len(semantics["diagnostics"]),
        }
        if native:
            context.provide("list_semantics_candidates", candidates)
            context.provide("list_item_map", item_map)
        context.evidence["list-semantics"] = evidence
        context.provide("list_semantics", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="list-semantics",
            version="1.0",
            stages=("relation",),
            requires=("source_facts", "source_ast")
            if native
            else (
                "source_facts",
                "document_structure_candidates",
                "protected_content_candidates",
            ),
            provides=(
                "list_semantics_result",
                "list_semantics_candidates",
                "list_item_map",
                "list_semantics",
            )
            if native
            else ("list_semantics_result", "list_semantics"),
        ),
        execute=execute,
    )


def table_semantics_module(*, native: bool = False) -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        semantics = collect_source_table_semantics(context.require("source_facts"))
        context.provide("table_semantics_result", semantics)
        if native:
            candidates = collect_table_candidates(context.require("source_ast"))
            context.provide("table_semantics_candidates", candidates)
            evidence = {
                "candidate_count": len(candidates),
                "source_count": len(semantics),
            }
        else:
            evidence = {
                "table_count": len(semantics),
                "data_count": sum(item.get("table_type") == "data" for item in semantics),
                "layout_count": sum(item.get("table_type") == "layout" for item in semantics),
                "code_sample_count": sum(item.get("table_type") == "code_sample" for item in semantics),
                "caption_eligible_count": sum(bool(item.get("caption_eligible")) for item in semantics),
            }
        context.evidence["table-semantics"] = evidence
        context.provide("table_semantics", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="table-semantics",
            version="1.0",
            stages=("relation",),
            requires=("source_facts", "source_ast") if native else ("source_facts",),
            provides=(
                "table_semantics_result",
                "table_semantics_candidates",
                "table_semantics",
            )
            if native
            else ("table_semantics_result", "table_semantics"),
        ),
        execute=execute,
    )


def _materializes_caption_node(candidate: Mapping[str, Any]) -> bool:
    """Return whether a caption candidate creates a caption block."""

    proposal = candidate.get("proposal")
    if not isinstance(proposal, Mapping) or proposal.get("operation") != "create_node":
        return False
    value = proposal.get("value")
    if not isinstance(value, Mapping):
        return False
    node = value.get("node")
    return isinstance(node, Mapping) and node.get("role") == "caption"


def caption_semantics_module(*, native: bool = False) -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        semantics = collect_source_caption_semantics(
            context.require("source_facts"),
            context.require("table_semantics_result"),
        )
        context.provide("caption_semantics_result", semantics)
        captions = semantics["captions"]
        generated_count = 0
        if native:
            candidates = collect_caption_candidates(context.require("source_ast"))
            context.provide("caption_semantics_candidates", candidates)
            generated_count = sum(
                1 for candidate in candidates if _materializes_caption_node(candidate)
            )
        evidence = {
            "caption_count": len(captions),
            "table_caption_count": sum(item.get("caption_type") == "table" for item in captions),
            "figure_caption_count": sum(item.get("caption_type") == "figure" for item in captions),
            "associated_count": sum(item.get("status") == "associated" for item in captions),
            "issue_count": len(semantics["issues"]),
            "generated_caption_count": generated_count,
        }
        context.evidence["caption-semantics"] = evidence
        context.provide("caption_semantics", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="caption-semantics",
            version="1.0",
            stages=("relation",),
            requires=("source_facts", "source_ast", "table_semantics_result")
            if native
            else ("source_facts", "table_semantics_result"),
            provides=(
                "caption_semantics_result",
                "caption_semantics_candidates",
                "caption_semantics",
            )
            if native
            else ("caption_semantics_result", "caption_semantics"),
        ),
        execute=execute,
    )


def appendix_semantics_module(*, native: bool = False) -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        facts = context.require("source_facts")
        semantics = collect_source_appendix_semantics(facts)
        if native:
            source_ast = context.require("source_ast")
            list_result = context.require("list_semantics_result")
            semantics = prepare_native_appendix_semantics(facts, source_ast, semantics, list_result)
            context.provide(
                "appendix_semantics_candidates",
                collect_native_appendix_candidates(facts, source_ast, semantics, list_result),
            )
        context.provide("appendix_semantics_result", semantics)
        evidence = {
            "candidate_count": len(semantics["candidates"]),
            "accepted_count": sum(bool(item.get("accepted")) for item in semantics["candidates"]),
            "rejected_count": sum(not bool(item.get("accepted")) for item in semantics["candidates"]),
            "range_count": len(semantics["ranges"]),
            "heading_count": sum(len(item.get("heading_decisions") or []) for item in semantics["ranges"]),
            "issue_count": len(semantics["issues"]),
        }
        context.evidence["appendix-semantics"] = evidence
        context.provide("appendix_semantics", semantics if native else evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="appendix-semantics",
            version="1.0",
            stages=("relation",),
            requires=("source_facts", "source_ast", "list_semantics_result")
            if native
            else ("source_facts",),
            provides=(
                "appendix_semantics_result",
                "appendix_semantics_candidates",
                "appendix_semantics",
            )
            if native
            else ("appendix_semantics_result", "appendix_semantics"),
        ),
        execute=execute,
    )


def legacy_full_adapter_module(
    runner: Callable[[Any], Any],
    *,
    provides: tuple[str, ...],
) -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> Any:
        options = dict(context.request.options or {})
        adapter_artifacts: dict[str, Any] = {}
        options["_adapter_artifacts"] = adapter_artifacts
        options["_native_structure_context"] = {
            "source_facts": context.require("source_facts"),
            "candidates": context.require("document_structure_candidates"),
        }
        options["_native_region_context"] = {
            **context.require("source_region"),
            "source_facts": context.require("source_facts"),
        }
        options["_native_source_context"] = {
            "source_facts": context.require("source_facts"),
            "projection": context.require("source_projection"),
        }
        options["_native_protected_context"] = {
            "source_facts": context.require("source_facts"),
            "candidates": context.require("protected_content_candidates"),
        }
        options["_native_list_context"] = {
            "source_facts": context.require("source_facts"),
            "semantics": context.require("list_semantics_result"),
        }
        options["_native_table_context"] = {
            "semantics": context.require("table_semantics_result"),
        }
        options["_native_caption_context"] = {
            "semantics": context.require("caption_semantics_result"),
        }
        options["_native_appendix_context"] = {
            "semantics": context.require("appendix_semantics_result"),
        }
        adapter_request = replace(context.request, options=options)
        result = runner(adapter_request)
        context.provide("legacy_run_result", result)
        legacy_ast = adapter_artifacts.get("source_ast")
        legacy_model = adapter_artifacts.get("source_document_model")
        if legacy_ast is None and legacy_model is not None:
            from .migration import migrate_legacy_model

            legacy_ast = migrate_legacy_model(legacy_model).ast
        if legacy_ast is None:
            source_ast_path = getattr(result, "artifacts", {}).get("source_ast")
            if source_ast_path and Path(source_ast_path).is_file():
                legacy_ast = json.loads(Path(source_ast_path).read_text(encoding="utf-8"))
        context.provide("legacy_source_ast", legacy_ast)
        for capability in provides:
            if capability not in context.artifacts:
                context.provide(capability, _legacy_capability_payload(capability, result, context))
        return result

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="legacy-full-adapter",
            version="1.0",
            stages=("normalize", "render", "audit", "publish"),
            requires=(
                "source_facts",
                "source_region",
                "source_projection",
                "document_structure_candidates",
                "protected_content_candidates",
                "list_semantics_result",
                "table_semantics_result",
                "caption_semantics_result",
                "appendix_semantics_result",
            ),
            provides=(*provides, "legacy_run_result", "legacy_source_ast"),
            exclusive_group="full-conversion-adapter",
        ),
        execute=execute,
    )


def _legacy_capability_payload(
    capability: str,
    result: Any,
    context: ModuleExecutionContext,
) -> Any:
    """Expose a declared transitional capability without changing the report."""

    report = getattr(result, "report", {}) or {}
    if capability == "template_provider":
        template = getattr(context.request, "template_path", None)
        return {"template_path": str(template) if template is not None else None}
    if capability == "renderer":
        return {"artifacts": dict(getattr(result, "artifacts", {}) or {})}
    if capability == "publisher":
        return {"output_path": str(getattr(context.request, "output_path", ""))}
    return report.get(capability) or {}


def semantic_equivalence_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        legacy_ast = context.require("legacy_source_ast")
        if legacy_ast is None:
            audit = {
                "schema_version": "1.0",
                "status": "unobserved",
                "passed": False,
                "issues": [{"type": "legacy_source_ast_unavailable"}],
            }
        else:
            audit = compare_structure_equivalence(
                context.require("source_facts"),
                context.require("source_regions"),
                context.require("document_structure_candidates"),
                legacy_ast,
            )
        context.provide("semantic_equivalence_audit", audit)
        context.evidence["semantic-equivalence-audit"] = audit
        return audit

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="semantic-equivalence-audit",
            version="1.0",
            stages=("audit",),
            requires=(
                "source_facts",
                "source_regions",
                "document_structure_candidates",
                "legacy_source_ast",
            ),
            provides=("semantic_equivalence_audit",),
        ),
        execute=execute,
    )


def source_parser_equivalence_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        legacy_ast = context.require("legacy_source_ast")
        if legacy_ast is None:
            audit = {
                "schema_version": "1.0",
                "status": "unobserved",
                "passed": False,
                "domains": {},
                "issues": [{"type": "legacy_source_ast_unavailable"}],
            }
        else:
            audit = compare_parser_equivalence(
                context.require("source_facts"),
                context.require("source_regions"),
                legacy_ast,
            )
        context.provide("source_parser_equivalence_audit", audit)
        context.evidence["source-parser-equivalence-audit"] = audit
        return audit

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="source-parser-equivalence-audit",
            version="1.0",
            stages=("audit",),
            requires=("source_facts", "source_regions", "legacy_source_ast"),
            provides=("source_parser_equivalence_audit",),
        ),
        execute=execute,
    )


def native_semantic_modules() -> tuple[RegisteredModule, ...]:
    """Return the semantic Modules that own parsing and recognition."""

    return (
        source_facts_module(native=True),
        source_region_module(),
        document_structure_module(protect_semantics=True),
        protected_content_module(),
        list_semantics_module(native=True),
        table_semantics_module(native=True),
        caption_semantics_module(native=True),
        appendix_semantics_module(native=True),
    )


# ---------------------------------------------------------------------------
# Native stage chain: source AST construction, CandidateSet aggregation,
# resolution, transaction, normalization, rendering, auditing, publication.
# ---------------------------------------------------------------------------


def _native_source_ast(context: ModuleExecutionContext) -> dict[str, Any]:
    facts = context.require("source_facts")
    with context.stage("relation"):
        restored = (context.request.options or {}).get("restored_source_ast")
        ast = (
            json.loads(json.dumps(restored, ensure_ascii=False))
            if isinstance(restored, Mapping)
            else build_source_ast(facts)
        )
    context.provide("source_ast", ast)
    evidence = {
        "block_count": len(ast["document"]["blocks"]),
        "container_count": len(ast["document"]["containers"]),
    }
    context.evidence["native-source-parser"] = evidence
    return ast


def _native_candidate_set(context: ModuleExecutionContext) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = [
        *context.require("source_region_candidates"),
        *context.require("document_structure_candidates"),
        *context.require("protected_content_candidates"),
        *context.require("list_semantics_candidates"),
        *context.require("appendix_semantics_candidates"),
        *context.require("table_semantics_candidates"),
        *context.require("caption_semantics_candidates"),
    ]
    options = context.request.options or {}
    supplied_ledger = options.get("decision_ledger")
    ledger_view: dict[str, Any] | None = None
    ledger_replay: dict[str, Any] | None = None
    if supplied_ledger is not None:
        from .decision_ledger import LedgerError, project_ledger, revision_decision

        if not isinstance(supplied_ledger, Mapping) or not isinstance(supplied_ledger.get("source_binding"), Mapping):
            raise LedgerError(
                "native decision ledger must carry a SourceFacts binding",
                code="source_binding_missing",
            )
        facts = context.require("source_facts")
        source_ast = context.require("source_ast")
        existing_node_ids = set(source_ast["document"]["blocks"]) | set(source_ast["document"]["containers"])
        ledger_view = project_ledger(facts, supplied_ledger, existing_node_ids=existing_node_ids)
        blocking = [
            item
            for item in ledger_view.get("diagnostics", [])
            if item.get("code") in {"revision_precondition_failed", "revision_conflict"}
        ]
        if blocking:
            context.provide("ledger_view", ledger_view)
            context.provide("ledger_replay", {
                "enabled": True,
                "error_code": "replay_blocked",
                "diagnostics": blocking,
            })
            raise LedgerReplayBlocked(blocking)
        for revision in ledger_view.get("active_revisions", []):
            ledger_decision = revision_decision(revision, source_ast["revision"])
            candidates.append({
                "candidate_id": f"ledger-revision-{revision['revision_id']}",
                "module_id": "decision_ledger",
                "module_version": str(revision.get("resolver_version") or "1.0"),
                "target_source_refs": _copy_refs(revision.get("target_source_refs")),
                "target_node_ids": list(revision.get("target_node_ids") or []),
                "proposal": {
                    "operation": ledger_decision["operation"],
                    "property_scope": list(revision.get("property_scope") or []),
                    "value": dict(ledger_decision.get("value") or {}),
                    "invalidates": list(ledger_decision.get("invalidates") or []),
                },
                "positive_evidence": [{"kind": "ledger_revision", "revision_id": revision["revision_id"]}],
                "negative_evidence": [],
                "scope": {"stages": list(revision.get("invalidates") or [])},
                "constraints": [],
                "rationale": "accepted revision restored from the decision ledger",
            })
        ledger_replay = {
            "enabled": True,
            "ledger_revision": supplied_ledger.get("revision"),
            "active_revision_count": len(ledger_view.get("active_revisions") or []),
            "baseline_ast_revision": source_ast["revision"],
        }
    enhancement_candidates, enhancement_result = _native_enhancement_candidates(
        context.require("source_ast"),
        options,
    )
    candidates.extend(enhancement_candidates)
    context.provide("candidate_set", candidates)
    context.provide("ledger_view", ledger_view)
    context.provide("ledger_replay", ledger_replay)
    context.provide("enhancement_result", enhancement_result)
    context.evidence["native-candidate-set"] = {
        "rule_candidate_count": len(candidates) - len(enhancement_candidates),
        "enhancement_candidate_count": len(enhancement_candidates),
        "enhancement_status": enhancement_result["status"],
    }
    if options.get("protocol_mode") == "generate_requests":
        raise NativeFileProtocolPause()
    blocking_results = [
        record
        for record in enhancement_result.get("results", [])
        if record.get("status") != "completed"
    ]
    if blocking_results:
        raise NativeEnhancementBlocked(enhancement_result)
    return candidates


def _native_enhancement_candidates(
    source_ast: Mapping[str, Any],
    options: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    """Collect validated native candidates from explicitly enabled adapters."""

    from llm_enhancer import CAPABILITY_REGISTRY, normalize_mode, should_enhance

    from .enhancement import EnhancementExecutor, build_native_request, serialize_native_request

    mode = str(options.get("llm_enhance") or "off")
    if mode == "off":
        return [], {
            "status": "disabled",
            "mode": mode,
            "requested_capabilities": [],
            "candidate_count": 0,
            "consumed": 0,
            "results": [],
        }

    normalized_mode = normalize_mode(mode)
    selected = [
        capability
        for capability in CAPABILITY_REGISTRY
        if should_enhance({}, capability, normalized_mode)
    ]
    configured_budget = options.get("enhancement_budget", len(selected))
    budget = configured_budget if isinstance(configured_budget, int) and not isinstance(configured_budget, bool) else -1
    timeout = options.get("enhancement_timeout_seconds", 30.0)
    timeout = float(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else 30.0
    adapter = options.get("enhancement_call")
    request_sink = options.get("enhancement_request_sink")
    executor = EnhancementExecutor()
    candidates: list[Mapping[str, Any]] = []
    records: list[dict[str, Any]] = []
    consumed = 0

    for capability in selected:
        remaining = max(0, budget - consumed)
        request = build_native_request(
            capability,
            source_ast,
            budget=min(1, remaining),
            timeout_seconds=timeout,
            request_id=f"native-{source_ast['revision']}-{capability}",
        )
        if isinstance(request_sink, list):
            request_sink.append(serialize_native_request(request))
        if options.get("protocol_mode") == "generate_requests":
            result = executor.execute(request, responses=[])
        else:
            result = executor.execute(request, call=adapter)
        consumed += result.consumed
        for candidate in result.candidates:
            item = json.loads(json.dumps(candidate, ensure_ascii=False))
            item["candidate_id"] = f"enhancement-{capability}-{item['candidate_id']}"
            item["module_id"] = f"enhancement-{capability}"
            candidates.append(item)
        records.append(
            {
                "capability": capability,
                "status": result.status,
                "candidate_count": len(result.candidates),
                "consumed": result.consumed,
                "diagnostics": [dict(item) for item in result.diagnostics],
                "request_summary": dict(result.request_summary),
            }
        )

    statuses = {record["status"] for record in records}
    if not selected:
        status = "no_targets"
    elif statuses == {"completed"}:
        status = "completed"
    elif "completed" in statuses:
        status = "completed_with_warnings"
    elif len(statuses) == 1:
        status = next(iter(statuses))
    else:
        status = "failed"
    return candidates, {
        "status": status,
        "mode": mode,
        "requested_capabilities": selected,
        "candidate_count": len(candidates),
        "consumed": consumed,
        "budget": {"limit": budget, "remaining": max(0, budget - consumed)},
        "results": records,
    }


def _copy_refs(value: Any) -> list[dict[str, Any]]:
    import copy as _copy

    if not isinstance(value, (list, tuple)):
        return []
    return [_copy.deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]


def _native_resolver(context: ModuleExecutionContext) -> dict[str, Any]:
    from .resolver import resolve

    source_ast = context.require("source_ast")
    decision_set = resolve(
        context.require("candidate_set"),
        {"revision": source_ast["revision"]},
        context.require("ledger_view"),
    )
    context.provide("decision_set", decision_set)
    if _plan_blocks_publication(context, decision_set):
        raise UnresolvedDecisionError(decision_set.get("unresolved") or [])
    return decision_set


def _plan_blocks_publication(context: ModuleExecutionContext, decision_set: Mapping[str, Any]) -> bool:
    unresolved = decision_set.get("unresolved") or []
    if not unresolved:
        return False
    plan = context.services.get("execution_plan")
    modules = getattr(plan, "modules", ()) or ()
    return any(module.failure_policy == "fail" for module in modules)


def _native_transaction(context: ModuleExecutionContext) -> Any:
    from .transaction import apply_stage_batch, make_snapshot

    source_ast = context.require("source_ast")
    decision_set = context.require("decision_set")
    snapshot = make_snapshot(source_ast)
    restored = (context.request.options or {}).get("restored_transaction_snapshot")
    if isinstance(restored, Mapping):
        snapshot.transactions = {
            str(transaction_id): str(digest)
            for transaction_id, digest in (restored.get("transactions") or {}).items()
        }
        snapshot.overlays = json.loads(json.dumps(restored.get("overlays") or {}, ensure_ascii=False))
        snapshot.presentation_overrides = json.loads(
            json.dumps(restored.get("presentation_overrides") or {}, ensure_ascii=False)
        )
    with context.stage("transaction"):
        committed = apply_stage_batch(
            snapshot,
            decision_set["decisions"],
            expected_revision=source_ast["revision"],
        )
    snapshot = committed.snapshot
    containers = snapshot.ast["document"]["containers"]
    children = snapshot.ast["document"]["children"]
    context.provide("transaction_commit", committed)
    context.provide("source_ast_revision", snapshot.ast["revision"])
    context.provide("source_ast_hierarchy", {
        "list_count": sum(node.get("kind") == "list" for node in containers.values()),
        "item_count": sum(node.get("kind") == "list_item" for node in containers.values()),
        "nested_list_count": sum(
            1
            for parent_id, node in containers.items()
            if node.get("kind") == "list_item"
            and any(containers.get(child, {}).get("kind") == "list" for child in children.get(parent_id, []))
        ),
    })
    return committed


def _native_normalizer(context: ModuleExecutionContext) -> Mapping[str, Any]:
    from .normalization import normalize
    from .template import load_template_bundle

    committed = context.require("transaction_commit")
    decision_set = context.require("decision_set")
    facts = context.require("source_facts")
    request = context.request
    store = context.services.get("store")
    with context.stage("normalize"):
        template = load_template_bundle(request.template_path) if request.template_path else {
            "resolved_styles": {
                "list_letter": "List Number",
                "list_decimal": "List Number 2",
                "list_dash": "List Bullet",
                "list_bullet": "List Bullet 2",
            },
            "numbering_ids": {},
        }
        normalized = normalize(
            committed.snapshot.ast,
            template,
            strategy=request.list_policy,
            overlay=committed.snapshot.overlays,
            presentation_override=committed.snapshot.presentation_overrides,
            context={
                "transaction_id": committed.transaction_id,
                "decision_set_version": decision_set["decision_set_version"],
            },
        )
    resources: dict[str, Any] = {}
    for resource_id, resource in facts.get("resources", {}).items():
        source_ref = resource.get("source_ref") if isinstance(resource, Mapping) else None
        if not isinstance(source_ref, Mapping):
            continue
        try:
            payload = store.resolve(dict(source_ref))
            if isinstance(payload, bytes):
                resources[resource_id] = payload
        except Exception:
            part = source_ref.get("part")
            if isinstance(part, str):
                candidate_path = request.input_path.parent / part
                if candidate_path.is_file():
                    resources[resource_id] = candidate_path
    context.provide("template_bundle", template)
    context.provide("normalized", normalized)
    context.provide("resources", resources)
    return normalized


def _native_renderer(context: ModuleExecutionContext) -> Mapping[str, Any]:
    from .rendering import render

    normalized = context.require("normalized")
    template = context.require("template_bundle")
    resources = context.require("resources")
    staging_path = context.services["staging_path"]
    with context.stage("render"):
        if hasattr(template, "path"):
            result = render(normalized, staging_path, template_bundle=template, resources=resources)
        else:
            result = render(normalized, staging_path, resources=resources)
        metrics = context.services.get("metrics")
        if metrics is not None:
            metrics.record_render()
    context.provide("render_plan", result["render_plan"])
    context.provide("render_result", result)
    return result


def _native_auditor(context: ModuleExecutionContext) -> dict[str, Any]:
    from .auditing import check_model, check_saved_output

    normalized = context.require("normalized")
    render_result = context.require("render_result")
    appendix_semantics = context.require("appendix_semantics")
    staging_path = context.services["staging_path"]
    with context.stage("audit"):
        model_findings = check_model(normalized)
        saved_findings = check_saved_output(staging_path, normalized, render_result["render_map"])
        appendix_findings = (
            audit_native_appendix(normalized["source_ast"], appendix_semantics)
            if appendix_semantics is not None
            else {"passed": True, "issues": []}
        )
    digest_after = digest_source_facts(dict(context.require("source_facts")))
    context.provide("model_audit", model_findings)
    context.provide("saved_output_audit", saved_findings)
    context.provide("appendix_audit", appendix_findings)
    context.provide("source_facts_integrity", {
        "digest_after": digest_after,
        "immutable": digest_after == context.require("source_facts_digest"),
    })
    return {"model": model_findings, "saved": saved_findings, "appendix": appendix_findings}


def _native_publisher(context: ModuleExecutionContext) -> bool:
    audits_passed = (
        context.require("model_audit").get("passed") is True
        and context.require("saved_output_audit").get("passed") is True
        and context.require("appendix_audit").get("passed") is True
    )
    fatal_diagnostics = [
        item
        for item in context.require("list_semantics_result").get("diagnostics", [])
        if item.get("severity") in {"error", "warning"}
    ]
    output_path = context.request.output_path
    if audits_passed and not fatal_diagnostics:
        context.services["staging_path"].replace(output_path)
        context.provide("published_output", str(output_path))
        return True
    context.provide("published_output", None)
    return False


def experimental_native_modules() -> tuple[RegisteredModule, ...]:
    """Small registered chain retained for the staged module replacement path.

    The experimental profile runs the minimal modular chain without candidates,
    resolution or a transaction: SourceFacts, list semantics, the source AST,
    normalization, rendering and the two mandatory audits.
    """

    def execute_list_semantics(context: ModuleExecutionContext) -> dict[str, Any]:
        with context.stage("scope"):
            semantics = analyze_lists(context.require("source_facts"))
        context.provide("list_semantics_result", semantics)
        context.evidence["list-semantics"] = {
            "status": semantics["status"],
            "list_count": len(semantics["lists"]),
            "item_count": len(semantics["items"]),
            "continuation_count": len(semantics["continuations"]),
            "diagnostic_count": len(semantics["diagnostics"]),
        }
        return semantics

    def execute_source_ast(context: ModuleExecutionContext) -> dict[str, Any]:
        with context.stage("role"):
            ast = build_source_ast(context.require("source_facts"))
        context.provide("source_ast", ast)
        return ast

    def execute_normalizer(context: ModuleExecutionContext) -> Mapping[str, Any]:
        from .normalization import normalize
        from template_profile import load_template_profile

        request = context.request
        with context.stage("normalize"):
            profile = (
                load_template_profile(request.template_path)
                if request.template_path
                else {"resolved_styles": {}, "numbering_ids": {}}
            )
            normalized = normalize(
                context.require("source_ast"),
                profile,
                strategy=request.list_policy,
            )
        context.provide("normalized", normalized)
        return normalized

    def execute_renderer(context: ModuleExecutionContext) -> Mapping[str, Any]:
        from .rendering import render

        request = context.request
        with context.stage("render"):
            result = render(
                context.require("normalized"),
                request.output_path,
                template_path=request.template_path,
            )
            metrics = context.services.get("metrics")
            if metrics is not None:
                metrics.record_render()
        context.provide("render_result", result)
        return result

    def execute_auditor(context: ModuleExecutionContext) -> dict[str, Any]:
        from .auditing import check_model, check_saved_output

        request = context.request
        normalized = context.require("normalized")
        render_result = context.require("render_result")
        with context.stage("audit"):
            model_findings = check_model(normalized)
            saved_findings = check_saved_output(
                request.output_path,
                normalized,
                render_result["render_map"],
            )
        context.provide("model_audit", model_findings)
        context.provide("saved_output_audit", saved_findings)
        return {"model": model_findings, "saved": saved_findings}

    return (
        source_facts_module(native=True),
        RegisteredModule(
            descriptor=ModuleDescriptor("list-semantics", "1.0", ("scope",), ("source_facts",), ("list_semantics_result",)),
            execute=execute_list_semantics,
        ),
        RegisteredModule(
            descriptor=ModuleDescriptor("native-source-parser", "1.0", ("role",), ("source_facts",), ("source_ast",)),
            execute=execute_source_ast,
        ),
        RegisteredModule(
            descriptor=ModuleDescriptor("experimental-normalizer", "1.0", ("normalize",), ("source_ast",), ("normalized",)),
            execute=execute_normalizer,
        ),
        RegisteredModule(
            descriptor=ModuleDescriptor("experimental-renderer", "1.0", ("render",), ("normalized",), ("render_result",)),
            execute=execute_renderer,
        ),
        RegisteredModule(
            descriptor=ModuleDescriptor(
                "experimental-auditor",
                "1.0",
                ("audit",),
                ("normalized", "render_result"),
                ("model_audit", "saved_output_audit"),
            ),
            execute=execute_auditor,
        ),
    )


def native_stage_executors() -> dict[str, Callable[[ModuleExecutionContext], Any]]:
    """Return the production implementation of the native stage chain."""

    return {
        "native-source-parser": _native_source_ast,
        "native-candidate-set": _native_candidate_set,
        "native-resolver": _native_resolver,
        "native-transaction": _native_transaction,
        "native-normalizer": _native_normalizer,
        "native-renderer": _native_renderer,
        "native-auditor": _native_auditor,
        "native-publisher": _native_publisher,
    }


__all__ = [
    "LedgerReplayBlocked",
    "NativeEnhancementBlocked",
    "NativeFileProtocolPause",
    "ModuleExecutionContext",
    "UnresolvedDecisionError",
    "appendix_semantics_module",
    "caption_semantics_module",
    "document_structure_module",
    "experimental_native_modules",
    "legacy_full_adapter_module",
    "list_semantics_module",
    "native_semantic_modules",
    "native_stage_executors",
    "semantic_equivalence_module",
    "source_parser_equivalence_module",
    "source_projection_module",
    "protected_content_module",
    "source_facts_module",
    "source_region_module",
    "table_semantics_module",
]
