#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
PYTHON=${WXDF_PYTHON:-"$PROJECT_DIR/scripts/.venv/bin/python3"}
if [ ! -x "$PYTHON" ]; then
    echo "Build interpreter not found: $PYTHON" >&2
    exit 2
fi
if ! "$PYTHON" -c 'import docx, lxml, markdown_it' >/dev/null 2>&1; then
    echo "Builder dependencies are missing. Run: $PYTHON -m pip install -r requirements.txt" >&2
    exit 2
fi
cd "$PROJECT_DIR"

VENDOR_ROOT="dist/vendor/macos-arm64"
VENDOR_TAGS=${WXDF_VENDOR_TAGS:-"3.12 3.13 3.14"}

"$PYTHON" packaging/interpreted/fetch_vendor.py --kind common --output "$VENDOR_ROOT/common"
for version in $VENDOR_TAGS; do
    digits=$(printf '%s' "$version" | tr -d '.')
    tag="cp$digits"
    "$PYTHON" packaging/interpreted/fetch_vendor.py --kind native \
        --platform macosx_11_0_arm64 \
        --python-version "$digits" \
        --abi "$tag" \
        --python-tag "$tag" \
        --output "$VENDOR_ROOT/$tag"
done

"$PYTHON" packaging/interpreted/build_package.py \
    --platform macos-arm64 \
    --vendor-root "$VENDOR_ROOT" \
    --output-dir dist/local "$@"

VERSION=$(tr -d '\r\n' < VERSION)
PACKAGE="dist/local/wx-doc-format-skill-$VERSION-macos-arm64"
EVIDENCE=$(mktemp -d "/tmp/wxdf-interpreted-macos.XXXXXX")
"$PYTHON" packaging/interpreted/smoke_distribution.py "$PACKAGE" --evidence-dir "$EVIDENCE"

echo "package: $PACKAGE"
echo "acceptance evidence: $EVIDENCE"
