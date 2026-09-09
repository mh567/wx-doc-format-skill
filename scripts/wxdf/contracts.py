"""Versioned data types and error definitions for modular pipeline contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, TypeAlias, TypedDict


SOURCE_FACTS_SCHEMA_VERSION = "1.0"
AST_SCHEMA_VERSION = "2.0"
PATCH_SCHEMA_VERSION = "2.0"
CHECKPOINT_SCHEMA_VERSION = "2.0"

STAGES = (
    "source",
    "scope",
    "role",
    "relation",
    "normalize",
    "render",
    "audit",
    "publish",
)
SOURCE_TYPES = ("markdown", "docx", "text", "unknown")
AST_BLOCK_TYPES = ("paragraph", "image", "table", "opaque")
AST_CONTAINER_KINDS = (
    "document",
    "section",
    "appendix",
    "quote",
    "list",
    "list_item",
    "table_row",
    "table_cell",
)
AST_ROLES = ("title", "body", "heading", "caption", "note", "numbered_note", "unknown")
TABLE_TYPES = ("data", "code_sample", "callout", "layout", "unknown")
LIST_KINDS = ("ordered", "unordered")
CAPTION_TYPES = ("figure", "table")
REVISION_STATUSES = ("active", "superseded", "invalidated", "conflicted")
CHECKPOINT_STATUSES = (
    "created",
    "running",
    "waiting_external",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
)
PATCH_OPERATIONS = (
    "create_node",
    "update_node_semantics",
    "update_role",
    "move_node",
    "update_association",
    "delete_node",
    "update_overlay",
    "set_presentation_override",
)
SAFE_PROPERTY_SCOPES = (
    "semantics",
    "role",
    "level",
    "caption_type",
    "target_id",
    "list_kind",
    "source_marker",
    "source_ordinal",
    "association",
    "children",
    "overlay",
    "presentation",
)

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]


class ContractError(ValueError):
    """Structured contract failure with a stable code and JSON path."""

    def __init__(self, code: str, path: str, message: str, *, contract: str | None = None) -> None:
        self.code = code
        self.path = path
        self.contract = contract
        prefix = f"{contract}: " if contract else ""
        super().__init__(f"{prefix}{code} at {path}: {message}")


@dataclass(frozen=True)
class SourceRefData:
    source_id: str
    part: str
    content_sha256: str
    span: Mapping[str, Any] | None = None
    node_path: Sequence[str | int] | None = None


@dataclass(frozen=True)
class CandidateData:
    candidate_id: str
    module_id: str
    module_version: str
    target_source_refs: Sequence[Mapping[str, Any]]
    target_node_ids: Sequence[str]
    proposal: Mapping[str, Any]
    positive_evidence: Sequence[Mapping[str, Any]]
    negative_evidence: Sequence[Mapping[str, Any]]
    scope: Mapping[str, Any]
    constraints: Sequence[Mapping[str, Any]]
    rationale: str
    confidence: float | None = None


class Decision(TypedDict, total=False):
    decision_id: str
    candidate_id: str
    operation: str
    target_node_ids: list[str]
    expected_revision: int
    property_scope: list[str]
    value: dict[str, Any]
    preconditions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    invalidates: list[str]


class Revision(TypedDict, total=False):
    revision_id: str
    parent_revision: int | None
    transaction_id: str
    target_source_refs: list[dict[str, Any]]
    target_node_ids: list[str]
    property_scope: list[str]
    decision: dict[str, Any]
    preconditions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    origin: dict[str, Any]
    resolver_version: str
    status: str
    supersedes: list[str]


class SourceFacts(TypedDict, total=False):
    facts_schema_version: str
    source: dict[str, Any]
    parser: dict[str, Any]
    units: dict[str, dict[str, Any]]
    containers: dict[str, dict[str, Any]]
    children: dict[str, list[str]]
    evidence: dict[str, dict[str, Any]]
    resources: dict[str, dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    extensions: dict[str, Any]


class SourceAST(TypedDict, total=False):
    schema_version: str
    revision: int
    document: dict[str, Any]


class Patch(TypedDict, total=False):
    patch_schema_version: str
    patch_id: str
    base_revision: int
    phase: str
    candidates: list[dict[str, Any]]
    decisions: list[Decision]


class RunCheckpoint(TypedDict, total=False):
    checkpoint_schema_version: str
    run_id: str
    status: str
    resume_stage: str | None
    source_facts: dict[str, Any]
    decision_ledger: dict[str, Any]
    source_ast: dict[str, Any]
    normalized_ast: dict[str, Any] | None
    content_overlay: list[dict[str, Any]]
    presentation_override: list[dict[str, Any]]
    target_map: dict[str, Any]
    resource_summary: dict[str, Any]
    contracts: dict[str, str]
    modules: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    responses: list[dict[str, Any]]
    applied_transactions: list[dict[str, Any]]
    transaction_content_digests: dict[str, str]
    budget: dict[str, Any]
    round: int
    candidate_baseline_id: str
