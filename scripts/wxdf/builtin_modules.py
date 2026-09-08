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
            requires=("source_region", "document_structure_candidates"),
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
    "document_structure_module",
    "legacy_full_adapter_module",
    "semantic_equivalence_module",
    "source_parser_equivalence_module",
    "source_facts_module",
    "source_region_module",
]
