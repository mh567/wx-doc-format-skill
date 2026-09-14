"""T05 focused contracts: one owner for the canonical-JSON plus sha256 rule.

Before T05 the identity rule (canonical JSON serialisation followed by a sha256
digest) was copied into seven modules. After T05 ``wxdf.digest`` is the only
implementation and every module imports it.

The digest value is locked here because checkpoints compare stored digests: the
canonical JSON text and the ``sha256:<hex>`` string may not change.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


OWNER = "wxdf.digest"
_SAMPLE = {"b": 1, "a": [2, {"d": None, "c": True}], "e": "中文"}
# Locked values produced by the pre-T05 implementation.
_SAMPLE_CANONICAL = '{"a":[2,{"c":true,"d":null}],"b":1,"e":"中文"}'
_SAMPLE_HEX = "250d157890915c4474295eb7c67f8e45c04550c6a6e873f812fedb8d8299951c"
_SAMPLE_DIGEST = "sha256:" + _SAMPLE_HEX


def test_digest_module_owns_the_identity_rule() -> None:
    owner = importlib.import_module(OWNER)
    assert owner.canonical_json(_SAMPLE) == _SAMPLE_CANONICAL
    assert owner.digest_hex(_SAMPLE) == _SAMPLE_HEX
    assert owner.digest(_SAMPLE) == _SAMPLE_DIGEST
    assert owner.digest_hex(_SAMPLE) == hashlib.sha256(_SAMPLE_CANONICAL.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "module_name, owner_attribute",
    [
        ("wxdf.checkpoint_store", "digest"),
        ("wxdf.transaction", "digest_hex"),
        ("wxdf.validation", "digest"),
        ("wxdf.enhancement", "digest"),
        ("wxdf.decision_ledger", "digest"),
        ("wxdf.review", "digest"),
        ("wxdf.response_store", "digest"),
    ],
)
def test_modules_import_the_single_owner(module_name: str, owner_attribute: str) -> None:
    owner = importlib.import_module(OWNER)
    module = importlib.import_module(module_name)
    assert getattr(module, owner_attribute) is getattr(owner, owner_attribute)


@pytest.mark.parametrize(
    "module_name, removed_helper",
    [
        ("wxdf.checkpoint_store", "_canonical"),
        ("wxdf.checkpoint_store", "_digest"),
        ("wxdf.transaction", "_digest"),
        ("wxdf.validation", "_canonical_digest"),
        ("wxdf.enhancement", "_digest"),
        ("wxdf.decision_ledger", "_facts_digest"),
        ("wxdf.review", "_content_digest"),
    ],
)
def test_local_identity_copies_are_deleted(module_name: str, removed_helper: str) -> None:
    module = importlib.import_module(module_name)
    assert not hasattr(module, removed_helper), f"{module_name}.{removed_helper} is still a local copy"


def test_response_store_keeps_its_seam_but_delegates_to_the_owner() -> None:
    owner = importlib.import_module(OWNER)
    response_store = importlib.import_module("wxdf.response_store")
    assert response_store.content_digest.__module__ == "wxdf.response_store"
    assert response_store.content_digest(_SAMPLE) == owner.digest(_SAMPLE)


def test_consumer_outputs_still_match_the_owner() -> None:
    owner = importlib.import_module(OWNER)
    from wxdf import decision_ledger, transaction

    facts = {
        "parser": {"id": "p", "version": "1.0"},
        "source": {"content_sha256": "sha256:" + "a" * 64},
    }
    assert decision_ledger.source_binding(facts)["source_facts_sha256"] == owner.digest(facts)
    assert transaction.transaction_content_digest([]) == owner.digest([])
