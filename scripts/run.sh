#!/bin/sh
set -eu

SKILL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

DEPS=
if [ -f "$SKILL_ROOT/OFFLINE_PACKAGE" ] || [ -d "$SKILL_ROOT/wheelhouse" ]; then
    DEPS="$SKILL_ROOT/.deps"
fi

dependencies_ready() {
    if [ -n "$DEPS" ]; then
        PYTHONPATH="$DEPS${PYTHONPATH:+:$PYTHONPATH}" \
            "$1" -B -c 'import docx, lxml, markdown_it' >/dev/null 2>&1
    else
        "$1" -B -c 'import docx, lxml, markdown_it' >/dev/null 2>&1
    fi
}

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
        python3.9 \
        python3.8 \
        /usr/bin/python3
    do
        [ -n "$candidate" ] || continue
        if [ "${candidate#/}" = "$candidate" ]; then
            command -v "$candidate" >/dev/null 2>&1 || continue
        else
            [ -x "$candidate" ] || continue
        fi
        if "$candidate" -B -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
            PYTHON=$candidate
            return 0
        fi
    done
    return 1
}

if ! select_python; then
    echo "WXDF-E-PYTHON-UNAVAILABLE: Python 3.8 or newer is required." >&2
    exit 3
fi

if [ -n "$DEPS" ]; then
    if [ -x "$SKILL_ROOT/scripts/setup.sh" ]; then
        if ! WXDF_PYTHON="$PYTHON" "$SKILL_ROOT/scripts/setup.sh" --offline >&2; then
            echo "WXDF-E-OFFLINE-BOOTSTRAP: bundled dependencies could not be installed." >&2
            exit 3
        fi
    fi
elif ! dependencies_ready "$PYTHON" && [ -x "$SKILL_ROOT/scripts/setup.sh" ]; then
    echo "WXDF-I-DEPENDENCY-BOOTSTRAP: installing dependencies into $SKILL_ROOT/.venv" >&2
    if ! WXDF_PYTHON="$PYTHON" "$SKILL_ROOT/scripts/setup.sh" >&2; then
        echo "WXDF-E-DEPENDENCY-BOOTSTRAP: dependencies could not be installed." >&2
        exit 3
    fi
    PYTHON="$SKILL_ROOT/.venv/bin/python3"
fi

if ! dependencies_ready "$PYTHON"; then
    if [ -n "$DEPS" ]; then
        echo "WXDF-E-DEPENDENCY-MISSING: the offline bundle does not provide dependencies for this interpreter." >&2
        echo "Covered CPython versions are listed in $SKILL_ROOT/wheelhouse/manifest.json." >&2
    else
        echo "WXDF-E-DEPENDENCY-MISSING: python-docx, lxml and markdown-it-py are required." >&2
        echo "Install them with: \"$SKILL_ROOT/scripts/setup.sh\"" >&2
        echo "Or run: $PYTHON -m pip install -r \"$SKILL_ROOT/requirements.txt\"" >&2
    fi
    exit 3
fi

if [ -n "$DEPS" ]; then
    WXDF_DEPS="$DEPS"
    export WXDF_DEPS
fi

export PYTHONDONTWRITEBYTECODE=1
exec "$PYTHON" -B "$SKILL_ROOT/scripts/main.py" "$@"
