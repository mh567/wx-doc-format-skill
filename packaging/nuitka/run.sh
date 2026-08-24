#!/bin/sh
set -eu

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if printf '%s' "$PACKAGE_DIR" | LC_ALL=C grep -q '[^ -~]'; then
    echo "Installed Skill path must use printable ASCII characters." >&2
    exit 2
fi
export WX_DOC_FORMAT_OFFLINE=1
export WX_DOC_FORMAT_RELEASE=1
exec "$PACKAGE_DIR/runtime/wx-doc-format" "$@"
