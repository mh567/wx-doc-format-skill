"""Executable built-in modules composed by the conversion kernel."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any, Callable

from .parsers import parse, summarize_source_facts
from .registry import ModuleDescriptor, PlanError, RegisteredModule
from .semantics.regions import resolve_source_regions
from .semantics.structure import collect_structure_candidates
from .semantics.equivalence import compare_structure_equivalence
from .semantics.parser_equivalence import compare_parser_equivalence
from .semantics.source_projection import project_source_facts
from .semantics.protected import collect_protected_candidates
from .semantics.lists import analyze_lists
from .semantics.tables import collect_source_table_semantics
from .semantics.captions import collect_source_caption_semantics
from .semantics.appendices import collect_source_appendix_semantics


@dataclass
class ModuleExecutionContext:
    """Per-conversion artifact exchange shared by executable modules."""

    request: Any
    artifacts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def provide(self, capability: str, value: Any) -> None:
        if capability in self.artifacts:
            raise PlanError(f"runtime capability {capability} already provided")
        self.artifacts[capability] = value

    def require(self, capability: str) -> Any:
        if capability not in self.artifacts:
            raise PlanError(f"runtime capability {capability} is unavailable")
        return self.artifacts[capability]


def source_facts_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        facts = parse(context.request.input_path)
        summary = summarize_source_facts(facts)
        context.provide("source_facts", facts)
        context.evidence["source-facts-parser"] = summary
        return summary

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="source-facts-parser",
            version="1.0",
            stages=("source",),
            requires=(),
            provides=("source_facts",),
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


def document_structure_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        candidates = collect_structure_candidates(
            context.require("source_facts"),
            context.require("source_regions"),
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


def list_semantics_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
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
            context.require("source_facts"),
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
        context.evidence["list-semantics"] = evidence
        context.provide("list_semantics", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="list-semantics",
            version="1.0",
            stages=("relation",),
            requires=(
                "source_facts",
                "document_structure_candidates",
                "protected_content_candidates",
            ),
            provides=("list_semantics_result", "list_semantics"),
        ),
        execute=execute,
    )


def table_semantics_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        semantics = collect_source_table_semantics(context.require("source_facts"))
        context.provide("table_semantics_result", semantics)
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
            requires=("source_facts",),
            provides=("table_semantics_result", "table_semantics"),
        ),
        execute=execute,
    )


def caption_semantics_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        semantics = collect_source_caption_semantics(
            context.require("source_facts"),
            context.require("table_semantics_result"),
        )
        context.provide("caption_semantics_result", semantics)
        captions = semantics["captions"]
        evidence = {
            "caption_count": len(captions),
            "table_caption_count": sum(item.get("caption_type") == "table" for item in captions),
            "figure_caption_count": sum(item.get("caption_type") == "figure" for item in captions),
            "associated_count": sum(item.get("status") == "associated" for item in captions),
            "issue_count": len(semantics["issues"]),
        }
        context.evidence["caption-semantics"] = evidence
        context.provide("caption_semantics", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="caption-semantics",
            version="1.0",
            stages=("relation",),
            requires=("source_facts", "table_semantics_result"),
            provides=("caption_semantics_result", "caption_semantics"),
        ),
        execute=execute,
    )


def appendix_semantics_module() -> RegisteredModule:
    def execute(context: ModuleExecutionContext) -> dict[str, Any]:
        semantics = collect_source_appendix_semantics(context.require("source_facts"))
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
        context.provide("appendix_semantics", evidence)
        return evidence

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="appendix-semantics",
            version="1.0",
            stages=("relation",),
            requires=("source_facts",),
            provides=("appendix_semantics_result", "appendix_semantics"),
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
        return result

    return RegisteredModule(
        descriptor=ModuleDescriptor(
            module_id="legacy-full-adapter",
            version="1.0",
            stages=("normalize", "render", "audit", "publish"),
            requires=(
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


__all__ = [
    "ModuleExecutionContext",
    "appendix_semantics_module",
    "caption_semantics_module",
    "document_structure_module",
    "legacy_full_adapter_module",
    "list_semantics_module",
    "semantic_equivalence_module",
    "source_parser_equivalence_module",
    "source_projection_module",
    "protected_content_module",
    "source_facts_module",
    "source_region_module",
    "table_semantics_module",
]
