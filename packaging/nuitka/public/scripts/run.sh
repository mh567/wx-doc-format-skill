#!/bin/sh
set -eu

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if printf '%s' "$PACKAGE_DIR" | LC_ALL=C grep -q '[^ -~]'; then
    echo "Installed Skill path must use printable ASCII characters." >&2
    exit 2
fi

SYSTEM=$(uname -s)
MACHINE=$(uname -m)
case "$SYSTEM/$MACHINE" in
    Darwin/arm64) PLATFORM=macos-arm64 ;;
    Linux/aarch64|Linux/arm64) PLATFORM=kylin-v10-arm64 ;;
    *)
        echo "WXDF-E-PLATFORM-UNSUPPORTED: $SYSTEM/$MACHINE" >&2
        exit 3
        ;;
esac

MANIFEST_PATH="$PACKAGE_DIR/manifest.json"
if [ ! -r "$MANIFEST_PATH" ]; then
    echo "WXDF-E-MANIFEST-MISSING: offline runtime manifest is unavailable" >&2
    exit 3
fi
if LC_ALL=C od -An -v -t u1 "$MANIFEST_PATH" |
    awk '{ for (i = 1; i <= NF; i++) if ($i == 0) found = 1 } END { exit(found ? 0 : 1) }'
then
    echo "WXDF-E-MANIFEST-INVALID: offline runtime manifest contains a NUL byte" >&2
    exit 3
fi
if ! LC_ALL=C awk -f "$PACKAGE_DIR/scripts/validate-manifest.awk" "$MANIFEST_PATH"; then
    echo "WXDF-E-MANIFEST-INVALID: offline runtime manifest is not valid JSON" >&2
    exit 3
fi
MANIFEST_COMPACT=$(LC_ALL=C tr -d ' \t\r\n' < "$MANIFEST_PATH")
case "$MANIFEST_COMPACT" in
    *'"platforms":{'*) ;;
    *)
        echo "WXDF-E-MANIFEST-INVALID: offline runtime manifest has no platforms mapping" >&2
        exit 3
        ;;
esac
if ! printf '%s\n' "$MANIFEST_COMPACT" | grep -F "\"$PLATFORM\":{" >/dev/null; then
    echo "WXDF-E-RUNTIME-UNAVAILABLE: $PLATFORM is not included in this offline package" >&2
    exit 3
fi
PLATFORM_STATUS=$(
    printf '%s\n' "$MANIFEST_COMPACT" |
        sed -n 's/.*"'"$PLATFORM"'":{[^}]*"status":"\([^"]*\)".*/\1/p'
)
case "$PLATFORM_STATUS" in
    accepted) ;;
    candidate)
        echo "WXDF-W-RUNTIME-CANDIDATE: $PLATFORM requires target-platform acceptance" >&2
        ;;
    unavailable)
        echo "WXDF-E-RUNTIME-UNAVAILABLE: $PLATFORM is unavailable in this offline package" >&2
        exit 3
        ;;
    *)
        echo "WXDF-E-MANIFEST-INVALID: invalid status for $PLATFORM" >&2
        exit 3
        ;;
esac

export WX_DOC_FORMAT_OFFLINE=1
export WX_DOC_FORMAT_RELEASE=1
exec "$PACKAGE_DIR/runtime/wx-doc-format" "$@"
