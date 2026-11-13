from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def test_sources_use_python38_grammar_and_standard_library() -> None:
    for path in (*SCRIPTS.rglob("*.py"), *(ROOT / "tests").rglob("*.py")):
        if ".venv" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 8))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "removeprefix", "removesuffix", "BooleanOptionalAction", "is_relative_to",
                }, path
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                assert all(alias.name != "TypeAlias" for alias in node.names), path


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], True),
        (["--strict-normalize"], True),
        (["--no-strict-normalize"], False),
        (["--strict-normalize", "--no-strict-normalize"], False),
        (["--no-strict-normalize", "--strict-normalize"], True),
    ],
)
def test_cli_normalization_flags_work_without_boolean_optional_action(
    tmp_path: Path, monkeypatch, flags: list[str], expected: bool,
) -> None:
    import main
    from wxdf.run_result import RunResult

    monkeypatch.delattr(main.argparse, "BooleanOptionalAction", raising=False)
    configurations = []

    def convert(self, request):
        configurations.append(request.options["checkpoint_configuration"])
        return RunResult("completed", 0, terminal_replay=True)

    monkeypatch.setattr(main.ConversionKernel, "convert", convert)
    with pytest.raises(SystemExit) as raised:
        main.main([
            "--input", str(tmp_path / "source.md"),
            "--generate-requests", str(tmp_path / "requests"),
            *flags,
        ])
    assert raised.value.code == 0
    assert configurations[0]["strict_normalize"] is expected


@pytest.mark.parametrize("prefix", ["", "sha256:", "sha256:sha256:"])
def test_transaction_replay_accepts_one_optional_digest_prefix(prefix: str) -> None:
    from test_modular_ast import _ast
    from wxdf.transaction import (
        TransactionConflict, apply_stage_batch, make_snapshot, transaction_content_digest,
    )

    decisions = [{
        "transaction_id": "replay",
        "operation": "update_role",
        "target_node_ids": ["p0"],
        "property_scope": ["role"],
        "value": {"role": "note"},
    }]
    digest = transaction_content_digest(decisions)
    snapshot = make_snapshot(_ast())
    snapshot.transactions["replay"] = prefix + digest[len("sha256:"):]
    if prefix == "sha256:sha256:":
        with pytest.raises(TransactionConflict, match="different content digest"):
            apply_stage_batch(snapshot, decisions, expected_revision=1)
    else:
        result = apply_stage_batch(snapshot, decisions, expected_revision=1)
        assert result.status == "already_committed"
        assert result.snapshot is snapshot
