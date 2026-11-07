#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "aarch64" ]; then
    echo "This build requires Linux ARM64." >&2
    exit 2
fi

PYTHON=${WXDF_PYTHON:-"$PROJECT_DIR/scripts/.venv/bin/python3"}
if [ ! -x "$PYTHON" ]; then
    PYTHON=$(command -v python3 || true)
fi
if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    echo "Build interpreter not found. Set WXDF_PYTHON." >&2
    exit 2
fi
if ! "$PYTHON" -c 'import docx, lxml, markdown_it' >/dev/null 2>&1; then
    echo "Builder dependencies are missing. Run: $PYTHON -m pip install -r requirements.txt" >&2
    exit 2
fi
cd "$PROJECT_DIR"

VENDOR_ROOT="dist/vendor/kylin-v10-arm64"
BUNDLE=${WXDF_BUNDLE_PYTHON:-1}

"$PYTHON" packaging/interpreted/fetch_vendor.py --kind common --output "$VENDOR_ROOT/common"

if [ "$BUNDLE" = "1" ]; then
    "$PYTHON" packaging/interpreted/fetch_python.py --output dist/runtime-python/kylin-v10-arm64
    TAG=$("$PYTHON" -c 'import json; print(json.load(open("dist/runtime-python/kylin-v10-arm64/python.json"))["python_tag"])')
    "$PYTHON" packaging/interpreted/fetch_vendor.py --kind native \
        --platform manylinux_2_28_aarch64 \
        --python-version "${TAG#cp}" \
        --abi "$TAG" \
        --python-tag "$TAG" \
        --output "$VENDOR_ROOT/$TAG"
    set -- --runtime-python "dist/runtime-python/kylin-v10-arm64/python" "$@"
else
    TAG=$("$PYTHON" -c 'import sys; print("cp%d%d" % sys.version_info[:2])')
    "$PYTHON" packaging/interpreted/fetch_vendor.py --kind native --python-tag "$TAG" --output "$VENDOR_ROOT/$TAG"
fi

"$PYTHON" packaging/interpreted/build_package.py \
    --platform kylin-v10-arm64 \
    --vendor-root "$VENDOR_ROOT" \
    --output-dir dist/local "$@"

VERSION=$(tr -d '\r\n' < VERSION)
PACKAGE="dist/local/wx-doc-format-skill-$VERSION-kylin-v10-arm64"
EVIDENCE=$(mktemp -d "/tmp/wxdf-interpreted-kylin.XXXXXX")
"$PYTHON" packaging/interpreted/smoke_distribution.py "$PACKAGE" --evidence-dir "$EVIDENCE"

echo "package: $PACKAGE"
echo "acceptance evidence: $EVIDENCE"
