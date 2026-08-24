#!/bin/sh
set -eu

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export WX_DOC_FORMAT_OFFLINE=1
export WX_DOC_FORMAT_RELEASE=1
exec "$PACKAGE_DIR/runtime/wx-doc-format" "$@"
