"""T04 focused contracts: the compatibility facades are gone.

The production modules used to resolve helper symbols at call time through
``sys.modules`` name lookups, so the real call graph depended on what a test
monkeypatched.  After T04 the call graph is static: no facade module exists and
no production module performs a ``sys.modules`` name lookup.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.mark.parametrize("module_name", ["wxdf.builtin_modules", "wxdf.run_store"])
def test_compatibility_facades_are_deleted(module_name: str) -> None:
    assert importlib.util.find_spec(module_name) is None


def test_semantic_modules_has_no_dynamic_lookup_helper() -> None:
    from wxdf import semantic_modules

    assert not hasattr(semantic_modules, "_compat")
    assert not hasattr(semantic_modules, "compatibility_hook")
    assert "compatibility_hook" not in semantic_modules.__all__


def test_request_append_owns_the_replace_seam() -> None:
    from wxdf import request_append

    assert not hasattr(request_append, "_compat_replace_bytes")
    assert "_replace_bytes" in vars(request_append)


def test_production_modules_do_not_look_up_sys_modules() -> None:
    for filename in (
        "semantic_modules.py",
        "native_stages.py",
        "checkpoint_store.py",
        "kernel.py",
    ):
        source = (SCRIPTS / "wxdf" / filename).read_text(encoding="utf-8")
        assert "sys.modules" not in source, filename
