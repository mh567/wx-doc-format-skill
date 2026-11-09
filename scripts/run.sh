#!/bin/sh
set -eu

SKILL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

PYTHON=
select_python() {
    for candidate in \
        "${WXDF_PYTHON:-}" \
        "$SKILL_ROOT/.venv/bin/python3" \
        "$SKILL_ROOT/scripts/.venv/bin/python3" \
        python3 \
        python3.14 \
        python3.13 \
        python3.12 \
        python3.11 \
        python3.10 \
        /usr/bin/python3
    do
        [ -n "$candidate" ] || continue
        if [ "${candidate#/}" = "$candidate" ]; then
            command -v "$candidate" >/dev/null 2>&1 || continue
        else
            [ -x "$candidate" ] || continue
        fi
        if "$candidate" -B -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            PYTHON=$candidate
            return 0
        fi
    done
    return 1
}

if ! select_python; then
    echo "WXDF-E-PYTHON-UNAVAILABLE: Python 3.10 or newer is required." >&2
    exit 3
fi

if ! "$PYTHON" -B -c 'import docx, lxml, markdown_it' >/dev/null 2>&1; then
    echo "WXDF-E-DEPENDENCY-MISSING: python-docx, lxml and markdown-it-py are required." >&2
    echo "Install them with: \"$SKILL_ROOT/scripts/setup.sh\"" >&2
    echo "Or run: $PYTHON -m pip install -r \"$SKILL_ROOT/requirements.txt\"" >&2
    exit 3
fi

export PYTHONDONTWRITEBYTECODE=1
exec "$PYTHON" -B "$SKILL_ROOT/scripts/main.py" "$@"
