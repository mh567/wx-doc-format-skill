#!/bin/sh
set -eu

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

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

export PYTHONDONTWRITEBYTECODE=1

SAW_SUPPORTED_PYTHON=0
select_python() {
    for candidate in \
        "${WXDF_PYTHON:-}" \
        "$PACKAGE_DIR/runtime/python/bin/python3" \
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
            SAW_SUPPORTED_PYTHON=1
            tag=$("$candidate" -B -c 'import sys; print("cp%d%d" % sys.version_info[:2])')
            [ -d "$PACKAGE_DIR/vendor/$tag" ] || continue
            PYTHON=$candidate
            PYTHON_TAG=$tag
            return 0
        fi
    done
    return 1
}

if ! select_python; then
    if [ "$SAW_SUPPORTED_PYTHON" = "1" ]; then
        echo "WXDF-E-VENDOR-UNAVAILABLE: dependencies for the installed Python version are not bundled in this package." >&2
    else
        echo "WXDF-E-PYTHON-UNAVAILABLE: Python 3.10 or newer is required for $PLATFORM." >&2
    fi
    exit 3
fi

if [ ! -d "$PACKAGE_DIR/vendor/common" ]; then
    echo "WXDF-E-VENDOR-UNAVAILABLE: shared dependencies are not bundled in this package." >&2
    exit 3
fi
if [ ! -r "$PACKAGE_DIR/lib/payload.bin" ]; then
    echo "WXDF-E-PAYLOAD-MISSING: lib/payload.bin is missing." >&2
    exit 3
fi

export WX_DOC_FORMAT_OFFLINE=1
export WX_DOC_FORMAT_RELEASE=1
exec "$PYTHON" -I -B "$PACKAGE_DIR/lib/boot.py" "$@"
