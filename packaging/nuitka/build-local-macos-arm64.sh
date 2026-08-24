#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
BOOTSTRAP_PYTHON=${WXDF_BOOTSTRAP_PYTHON:-python3.13}
BUILD_ROOT="$PROJECT_DIR/build/nuitka/local-macos-arm64"
VENV_DIR="$BUILD_ROOT/venv"
OUTPUT_DIR="$PROJECT_DIR/dist/local"
PACKAGE_NAME="wx-doc-format-skill-$(tr -d '\r\n' < "$PROJECT_DIR/VERSION")-macos-arm64"
PACKAGE_DIR="$OUTPUT_DIR/$PACKAGE_NAME"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "This build requires macOS ARM64." >&2
    exit 2
fi
if ! command -v "$BOOTSTRAP_PYTHON" >/dev/null 2>&1; then
    echo "Python 3.13 is required. Set WXDF_BOOTSTRAP_PYTHON to its executable." >&2
    exit 2
fi

mkdir -p "$BUILD_ROOT" "$OUTPUT_DIR"
if [ ! -x "$VENV_DIR/bin/python3" ]; then
    "$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR"
fi
PYTHON="$VENV_DIR/bin/python3"
"$PYTHON" -m pip install --disable-pip-version-check \
    -r "$PROJECT_DIR/packaging/nuitka/requirements-runtime.txt" \
    -r "$PROJECT_DIR/packaging/nuitka/requirements-build.txt"
"$PYTHON" -m pytest "$PROJECT_DIR/tests" -q

env NUITKA_CACHE_DIR="$BUILD_ROOT/cache" "$PYTHON" \
    "$PROJECT_DIR/packaging/nuitka/build_distribution.py" \
    --build-dir "$BUILD_ROOT/compile" \
    --output-dir "$OUTPUT_DIR" \
    --platform-id macos-arm64 \
    --archive

SMOKE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/wx-doc-format-macos-smoke.XXXXXX")
cleanup_smoke() {
    rm -rf "$SMOKE_ROOT"
}
trap cleanup_smoke EXIT HUP INT TERM
cp -R "$PACKAGE_DIR" "$SMOKE_ROOT/$PACKAGE_NAME"
"$PYTHON" "$PROJECT_DIR/packaging/nuitka/smoke_distribution.py" "$SMOKE_ROOT/$PACKAGE_NAME"

echo "macOS ARM64 package: $PACKAGE_DIR.tar.gz"
