#!/bin/sh
set -eu

SKILL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${WXDF_PYTHON:-python3}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "WXDF-E-PYTHON-UNAVAILABLE: $PYTHON is not available." >&2
    exit 3
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "WXDF-E-PYTHON-UNAVAILABLE: Python 3.10 or newer is required." >&2
    exit 3
fi

case "${1:-}" in
    "") REQUIREMENTS="$SKILL_ROOT/requirements.txt" ;;
    --dev) REQUIREMENTS="$SKILL_ROOT/requirements-dev.txt" ;;
    *)
        echo "Usage: $0 [--dev]" >&2
        exit 2
        ;;
esac

VENV="$SKILL_ROOT/.venv"
if [ ! -x "$VENV/bin/python3" ]; then
    "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python3" -m pip install -r "$REQUIREMENTS"
echo "Installed dependencies into $VENV"
