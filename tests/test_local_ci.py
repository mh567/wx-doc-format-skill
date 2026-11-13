import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def check_repository(tmp_path):
    (tmp_path / "tools").mkdir()
    shutil.copy2(ROOT / "tools/check.sh", tmp_path / "tools/check.sh")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "VERSION").write_text("1.0\n")
    (tmp_path / "scripts/run.sh").write_text(
        '#!/bin/sh\nexec "$WXDF_PYTHON" "$CHECK_FIXTURE" "$@"\n'
    )
    fixture = tmp_path / "fixture.py"
    fixture.write_text('''import json
import os
from pathlib import Path
import sys
import zipfile

arguments = sys.argv[1:]
failure = os.environ["CHECK_FAILURE"]
if "--version" in arguments:
    print("wrong" if failure == "version" else "1.0")
elif "--output" in arguments:
    output = Path(arguments[arguments.index("--output") + 1])
    if failure == "zip":
        output.write_text("invalid DOCX")
    else:
        with zipfile.ZipFile(output, "w") as document:
            document.writestr("word/document.xml", "<document/>")
    report = Path(arguments[arguments.index("--report") + 1])
    report.write_text(json.dumps({"public_summary": {
        "schema_version": "1.0", "status": "completed",
        "unexpected_styles_count": 0, "manual_review_required": False,
        "audits": {"audit": "not_run", "model_audit": "passed",
                   "saved_output_audit": "failed" if failure == "audit" else "passed",
                   "appendix_preservation_audit": "passed"}
    }}))
else:
    Path(arguments[arguments.index("--report-md") + 1]).write_text("Review complete")
''')
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "VERSION"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c",
         "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True,
    )
    return tmp_path


def run_check(directory, failure=""):
    return subprocess.run(
        ["sh", str(directory / "tools/check.sh"), "--smoke"],
        env={**os.environ, "WXDF_PYTHON": sys.executable,
             "CHECK_FIXTURE": str(directory / "fixture.py"), "CHECK_FAILURE": failure},
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("failure", ["", "audit", "zip", "version"])
def test_smoke_checks_validate_outputs_even_when_commands_succeed(check_repository, failure):
    result = run_check(check_repository, failure)
    assert (result.returncode == 0) == (failure == ""), result.stdout + result.stderr


@pytest.mark.parametrize("state", ["staged", "committed"])
def test_checks_reject_whitespace_errors_in_staged_and_committed_changes(check_repository, state):
    (check_repository / "whitespace.txt").write_text("trailing whitespace \n")
    subprocess.run(
        ["git", "-C", str(check_repository), "add", "whitespace.txt"], check=True,
    )
    if state == "committed":
        subprocess.run(
            ["git", "-C", str(check_repository), "-c", "user.name=Test", "-c",
             "user.email=test@example.invalid", "commit", "-qm", "whitespace fixture"],
            check=True,
        )

    result = run_check(check_repository)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "trailing whitespace" in result.stdout + result.stderr
