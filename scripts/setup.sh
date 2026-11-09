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

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "WXDF-E-PYTHON-UNAVAILABLE: Python 3.10 or newer is required." >&2
    exit 3
fi

VENV="$SKILL_ROOT/.venv"
if [ ! -x "$VENV/bin/python3" ]; then
    if ! "$PYTHON" -m venv "$VENV"; then
        echo "WXDF-E-VENV-UNAVAILABLE: python -m venv is required to install dependencies." >&2
        exit 3
    fi
fi

if [ "$MODE" = "offline" ]; then
    TAG=$("$VENV/bin/python3" -c 'import sys; print("cp%d%d" % sys.version_info[:2])')
    if [ ! -d "$WHEELHOUSE/common" ] || [ ! -d "$WHEELHOUSE/$TAG" ]; then
        echo "WXDF-E-VENDOR-UNAVAILABLE: the bundled wheelhouse does not cover Python $TAG." >&2
        exit 3
    fi
    PIP_CONFIG_FILE=/dev/null \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    "$VENV/bin/python3" -m pip install \
        --no-index \
        --find-links "$WHEELHOUSE/common" \
        --find-links "$WHEELHOUSE/$TAG" \
        -r "$SKILL_ROOT/requirements.txt"
else
    "$VENV/bin/python3" -m pip install -r "$SKILL_ROOT/requirements.txt"
fi

echo "Installed dependencies into $VENV"
