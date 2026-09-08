"""Contracts for the modular conversion pipeline.

The package is intentionally independent from the legacy document model.  The
first implementation task only exposes data contracts and pure validators.
"""

from .contracts import (
    AST_SCHEMA_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    PATCH_SCHEMA_VERSION,
    SOURCE_FACTS_SCHEMA_VERSION,
    ContractError,
)

__all__ = [
    "AST_SCHEMA_VERSION",
    "CHECKPOINT_SCHEMA_VERSION",
    "PATCH_SCHEMA_VERSION",
    "SOURCE_FACTS_SCHEMA_VERSION",
    "ContractError",
]
