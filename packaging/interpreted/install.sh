#!/bin/sh
set -eu

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -gt 0 ]; then
    DESTINATION=$1
    if [ -z "$DESTINATION" ]; then
        echo "Installation destination must not be empty." >&2
        exit 2
    fi
else
    DESTINATION="$HOME/.codex/skills/wx-doc-format"
fi

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$PACKAGE_DIR" && sha256sum -c SHA256SUMS.txt)
elif command -v shasum >/dev/null 2>&1; then
    (cd "$PACKAGE_DIR" && shasum -a 256 -c SHA256SUMS.txt)
else
    echo "SHA-256 verifier not found; install sha256sum or shasum." >&2
    exit 1
fi

VERIFY_DIR=$(mktemp -d "${TMPDIR:-/tmp}/wx-doc-format-verify.XXXXXX")
EXPECTED_FILES="$VERIFY_DIR/expected-files.txt"
ACTUAL_FILES="$VERIFY_DIR/actual-files.txt"
ACTUAL_UNSORTED="$VERIFY_DIR/actual-files-unsorted.txt"
UNSAFE_MARKER="$VERIFY_DIR/unsafe-filename"
awk '{print substr($0, 67)}' "$PACKAGE_DIR/SHA256SUMS.txt" | LC_ALL=C sort > "$EXPECTED_FILES"
find "$PACKAGE_DIR" -type f ! -name SHA256SUMS.txt -exec sh -c '
    package_dir=$1
    unsafe_marker=$2
    shift 2
    for path do
        relative=${path#"$package_dir"/}
        case "$relative" in
            *"
"*|*"	"*) : > "$unsafe_marker"; continue ;;
        esac
        printf "%s\n" "$relative"
    done
' sh "$PACKAGE_DIR" "$UNSAFE_MARKER" {} + > "$ACTUAL_UNSORTED"
if [ -e "$UNSAFE_MARKER" ]; then
    rm -rf "$VERIFY_DIR"
    echo "Package contains an unsafe filename." >&2
    exit 1
fi
LC_ALL=C sort "$ACTUAL_UNSORTED" > "$ACTUAL_FILES"
if ! cmp -s "$EXPECTED_FILES" "$ACTUAL_FILES"; then
    rm -rf "$VERIFY_DIR"
    echo "Package file set does not match SHA256SUMS.txt." >&2
    exit 1
fi
rm -rf "$VERIFY_DIR"

if [ "$DESTINATION" = "/" ]; then
    echo "Installation to the filesystem root is forbidden." >&2
    exit 2
fi

DESTINATION_PARENT=$(dirname -- "$DESTINATION")
DESTINATION_NAME=$(basename -- "$DESTINATION")
if [ -z "$DESTINATION_NAME" ] || [ "$DESTINATION_NAME" = "/" ] || [ "$DESTINATION_NAME" = "." ] || [ "$DESTINATION_NAME" = ".." ]; then
    echo "Installation destination must name a dedicated directory." >&2
    exit 2
fi
mkdir -p "$DESTINATION_PARENT"
DESTINATION_PARENT=$(CDPATH= cd -- "$DESTINATION_PARENT" && pwd -P)
DESTINATION="$DESTINATION_PARENT/$DESTINATION_NAME"
HOME_DIR=$(CDPATH= cd -- "${HOME:-/}" && pwd -P)
if [ "$DESTINATION" = "/" ] || [ "$DESTINATION" = "$HOME_DIR" ]; then
    echo "Installation to the filesystem root or home directory is forbidden." >&2
    exit 2
fi

STAGING="$DESTINATION.staging.$$"
BACKUP="$DESTINATION.backup.$(date +%Y%m%d%H%M%S).$$"
if [ -e "$STAGING" ]; then
    echo "Staging path already exists: $STAGING" >&2
    exit 2
fi

cleanup_staging() {
    if [ -d "$STAGING" ]; then
        rm -rf "$STAGING"
    fi
}
trap cleanup_staging EXIT HUP INT TERM

mkdir "$STAGING"
cp -R "$PACKAGE_DIR"/. "$STAGING"/
chmod +x "$STAGING/scripts/run.sh" "$STAGING/scripts/install.sh"
if [ -f "$STAGING/runtime/python/bin/python3" ]; then
    chmod +x "$STAGING/runtime/python/bin/python3"
fi
EXPECTED_VERSION=$(tr -d '\r\n' < "$STAGING/VERSION")
if ! STAGED_VERSION=$("$STAGING/scripts/run.sh" --version); then
    echo "Staged runtime version check failed." >&2
    exit 1
fi
if [ "$STAGED_VERSION" != "$EXPECTED_VERSION" ]; then
    echo "Staged runtime version does not match package VERSION." >&2
    exit 1
fi

if [ -e "$DESTINATION" ]; then
    mv "$DESTINATION" "$BACKUP"
fi
if ! mv "$STAGING" "$DESTINATION"; then
    if [ -e "$BACKUP" ]; then
        mv "$BACKUP" "$DESTINATION"
    fi
    exit 1
fi
trap - EXIT HUP INT TERM
chmod +x "$DESTINATION/scripts/run.sh" "$DESTINATION/scripts/install.sh"
if [ -f "$DESTINATION/runtime/python/bin/python3" ]; then
    chmod +x "$DESTINATION/runtime/python/bin/python3"
fi
echo "Installed wx-doc-format-skill to $DESTINATION"
if [ -e "$BACKUP" ]; then
    echo "Previous installation retained at $BACKUP"
fi
