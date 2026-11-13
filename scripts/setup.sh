#!/bin/sh
set -eu

SKILL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${WXDF_PYTHON:-python3}

MODE=online
case "${1:-}" in
    "") ;;
    --dev) MODE=dev ;;
    --offline) MODE=offline ;;
    *)
        echo "Usage: $0 [--dev|--offline]" >&2
        exit 2
        ;;
esac

WHEELHOUSE="$SKILL_ROOT/wheelhouse"

require_compatible_venv() {
    [ -x "$VENV/bin/python3" ] || return 0
    if "$VENV/bin/python3" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'; then
        return 0
    fi
    echo "WXDF-E-VENV-UNAVAILABLE: existing .venv uses Python older than 3.8; remove it and retry." >&2
    exit 3
}
if [ -f "$SKILL_ROOT/OFFLINE_PACKAGE" ] || [ -d "$WHEELHOUSE" ]; then
    if [ "$MODE" = "dev" ]; then
        echo "WXDF-E-OFFLINE-DEV: development dependencies are not bundled in an offline package." >&2
        exit 2
    fi
    MODE=offline
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "WXDF-E-PYTHON-UNAVAILABLE: $PYTHON is not available." >&2
    exit 3
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'; then
    echo "WXDF-E-PYTHON-UNAVAILABLE: Python 3.8 or newer is required." >&2
    exit 3
fi

if [ "$MODE" = "offline" ]; then
    if [ -f "$WHEELHOUSE/wheels.tar.xz" ]; then
        FRESH=0
        [ -f "$SKILL_ROOT/.deps/.wxdf-state.json" ] || FRESH=1
        "$PYTHON" -B "$SKILL_ROOT/scripts/offline_install.py" \
            "$WHEELHOUSE" "$SKILL_ROOT/.deps"
        if [ "$FRESH" = "1" ]; then
            echo "Installed dependencies into $SKILL_ROOT/.deps"
        fi
        exit 0
    fi
    echo "WXDF-E-VENDOR-UNAVAILABLE: the offline wheelhouse has no wheels.tar.xz." >&2
    exit 3
else
    VENV="$SKILL_ROOT/.venv"
    if [ ! -x "$VENV/bin/python3" ]; then
        if ! "$PYTHON" -m venv "$VENV"; then
            echo "WXDF-E-VENV-UNAVAILABLE: python -m venv is required to install dependencies." >&2
            exit 3
        fi
    fi
    require_compatible_venv
    "$VENV/bin/python3" -m pip install -r "$SKILL_ROOT/requirements.txt"
fi

echo "Installed dependencies into $VENV"
