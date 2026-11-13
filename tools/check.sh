#!/bin/sh
set -eu

CHECK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$CHECK_ROOT"
export WXDF_PYTHON="${WXDF_PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1

case "${1:-}" in
    ""|--smoke) ;;
    *) echo "Usage: sh tools/check.sh [--smoke]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || exit 2

git -c core.fsmonitor=false diff --check
git -c core.fsmonitor=false diff --cached --check
git -c core.fsmonitor=false show --format= --check --first-parent HEAD
CHECK_BINARIES=$(git -c core.fsmonitor=false ls-files -- '*.so' '*.dylib' '*.pyd' '*.pyc' '*.pyo' '*.whl' '*payload.bin')
if [ -n "$CHECK_BINARIES" ]; then
    echo "Forbidden tracked binaries: $CHECK_BINARIES" >&2
    exit 1
fi

if [ "${1:-}" != --smoke ]; then
    "$WXDF_PYTHON" -m pytest tests/ -q
fi

[ "$(sh scripts/run.sh --version)" = "$(tr -d '\r\n' < VERSION)" ]
CHECK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/wxdf-check.XXXXXX")
trap 'rm -rf "$CHECK_DIR"' 0

WX_DOC_FORMAT_RELEASE=1 sh scripts/run.sh \
    --input "$CHECK_ROOT/input/test_baseline.md" \
    --output "$CHECK_DIR/smoke.docx" \
    --template "$CHECK_ROOT/assets/wx_template.docx" \
    --report "$CHECK_DIR/report.json"
sh scripts/run.sh --review \
    --input "$CHECK_DIR/smoke.docx" \
    --template "$CHECK_ROOT/assets/wx_template.docx" \
    --report-md "$CHECK_DIR/review.md"

"$WXDF_PYTHON" - "$CHECK_DIR" <<'PY'
import json
from pathlib import Path
import sys
import zipfile

directory = Path(sys.argv[1])
summary = json.loads((directory / "report.json").read_text())["public_summary"]
assert summary["schema_version"] == "1.0"
assert summary["status"] == "completed", summary
assert type(summary["unexpected_styles_count"]) is int
assert summary["unexpected_styles_count"] == 0, summary
assert summary["manual_review_required"] is False, summary
assert summary["audits"]["audit"] in {"passed", "not_run"}, summary
for audit in ("model_audit", "saved_output_audit", "appendix_preservation_audit"):
    assert summary["audits"][audit] == "passed", summary
with zipfile.ZipFile(directory / "smoke.docx") as document:
    assert document.testzip() is None
    document.getinfo("word/document.xml")
assert (directory / "review.md").stat().st_size > 0
print("Version, conversion, audits, DOCX integrity and review passed.")
PY
