#!/bin/sh
set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(tr -d '\r\n' < "$SKILL_DIR/VERSION")
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

if [ "$PLATFORM" = "macos-arm64" ]; then
    MIN_MACOS_VERSION=$(tr -d '\r\n' < "$SKILL_DIR/artifacts/MACOS_MIN_VERSION.txt")
    if [ "$MIN_MACOS_VERSION" != "unknown" ]; then
        CURRENT_MACOS_VERSION=$(sw_vers -productVersion)
        if ! awk -v current="$CURRENT_MACOS_VERSION" -v minimum="$MIN_MACOS_VERSION" 'BEGIN {
            split(current, c, "."); split(minimum, m, ".");
            for (i = 1; i <= 3; i++) {
                cv = c[i] + 0; mv = m[i] + 0;
                if (cv > mv) exit 0;
                if (cv < mv) exit 1;
            }
            exit 0;
        }'; then
            echo "WXDF-E-RUNTIME-INCOMPATIBLE: this release requires macOS $MIN_MACOS_VERSION or newer" >&2
            exit 3
        fi
    fi
fi

ARCHIVE_NAME="wx-doc-format-skill-$VERSION-$PLATFORM.tar.gz"
SUMS_FILE="$SKILL_DIR/artifacts/SHA256SUMS.txt"
EXPECTED_SHA=$(awk -v name="$ARCHIVE_NAME" '$2 == name { print $1 }' "$SUMS_FILE")
if [ -z "$EXPECTED_SHA" ]; then
    echo "WXDF-E-RUNTIME-UNAVAILABLE: $PLATFORM is not available in release v$VERSION" >&2
    exit 3
fi

CACHE_ROOT=${WX_DOC_FORMAT_CACHE_DIR:-"$HOME/.cache/wx-doc-format"}
if printf '%s' "$CACHE_ROOT" | LC_ALL=C grep -q '[^ -~]'; then
    echo "WXDF-E-NONASCII-PATH: WX_DOC_FORMAT_CACHE_DIR must use printable ASCII characters." >&2
    exit 2
fi
PACKAGE_NAME="wx-doc-format-skill-$VERSION-$PLATFORM"
PACKAGE_DIR="$CACHE_ROOT/$VERSION/$PLATFORM/$PACKAGE_NAME"
if [ -x "$PACKAGE_DIR/scripts/run.sh" ]; then
    exec "$PACKAGE_DIR/scripts/run.sh" "$@"
fi
if [ -e "$PACKAGE_DIR" ]; then
    echo "WXDF-E-CACHE-INCOMPLETE: remove $PACKAGE_DIR and retry" >&2
    exit 1
fi

TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/wx-doc-format-bootstrap.XXXXXX")
cleanup_bootstrap() {
    rm -rf "$TEMP_ROOT"
}
trap cleanup_bootstrap EXIT HUP INT TERM
ARCHIVE_PATH="$TEMP_ROOT/$ARCHIVE_NAME"
if [ -n "${WX_DOC_FORMAT_ARCHIVE_DIR:-}" ]; then
    cp "$WX_DOC_FORMAT_ARCHIVE_DIR/$ARCHIVE_NAME" "$ARCHIVE_PATH"
else
    if ! command -v curl >/dev/null 2>&1; then
        echo "WXDF-E-DOWNLOADER-MISSING: curl is required for the first online run." >&2
        exit 1
    fi
    RELEASE_BASE_URL=${WX_DOC_FORMAT_RELEASE_BASE_URL:-"https://github.com/mh567/wx-doc-format-skill/releases/download/v$VERSION"}
    curl --fail --location --output "$ARCHIVE_PATH" "$RELEASE_BASE_URL/$ARCHIVE_NAME"
fi

if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA=$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA=$(shasum -a 256 "$ARCHIVE_PATH" | awk '{print $1}')
else
    echo "WXDF-E-HASHER-MISSING: sha256sum or shasum is required." >&2
    exit 1
fi
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo "WXDF-E-ARCHIVE-HASH: SHA256 mismatch for $ARCHIVE_NAME" >&2
    exit 1
fi

EXTRACT_ROOT="$TEMP_ROOT/extracted"
mkdir "$EXTRACT_ROOT"
tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_ROOT"
EXTRACTED_PACKAGE="$EXTRACT_ROOT/$PACKAGE_NAME"
if [ ! -x "$EXTRACTED_PACKAGE/scripts/install.sh" ]; then
    echo "WXDF-E-ARCHIVE-LAYOUT: expected package directory is missing" >&2
    exit 1
fi
"$EXTRACTED_PACKAGE/scripts/install.sh" "$PACKAGE_DIR"
trap - EXIT HUP INT TERM
rm -rf "$TEMP_ROOT"
exec "$PACKAGE_DIR/scripts/run.sh" "$@"
