#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
CONTAINER_IMAGE=${WXDF_LINUX_BUILD_IMAGE:-quay.io/pypa/manylinux2014_aarch64@sha256:1145c233b5693c770b878d51e64261603b0d374942ff134d589656799f72e9f9}
VERSION=$(tr -d '\r\n' < "$PROJECT_DIR/VERSION")
LOCAL_UID=$(id -u)
LOCAL_GID=$(id -g)

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required for the Linux ARM64 build." >&2
    exit 2
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running. Start the ARM64 Colima profile first." >&2
    exit 2
fi

docker run --rm --platform linux/arm64 \
    -e WXDF_HOST_UID="$LOCAL_UID" \
    -e WXDF_HOST_GID="$LOCAL_GID" \
    -e WXDF_VERSION="$VERSION" \
    -v "$PROJECT_DIR:/workspace" \
    -w /workspace \
    "$CONTAINER_IMAGE" \
    /bin/bash -lc '
        set -euo pipefail
        PYTHON=/opt/python/cp313-cp313/bin/python
        BUILD_ROOT=/workspace/build/nuitka/local-linux-arm64
        OUTPUT_DIR=/workspace/dist/local
        mkdir -p "$BUILD_ROOT" "$OUTPUT_DIR"
        yum install -y libjpeg-turbo-devel zlib-devel
        if [ -f /opt/_internal/static-libs-for-embedding-only.tar.xz ]; then
            (cd /opt/_internal && tar xf static-libs-for-embedding-only.tar.xz)
        fi
        "$PYTHON" -m pip install --disable-pip-version-check \
            -r packaging/nuitka/requirements-runtime.txt \
            -r packaging/nuitka/requirements-build.txt
        "$PYTHON" -m pytest tests/ -q
        env NUITKA_CACHE_DIR="$BUILD_ROOT/cache" "$PYTHON" \
            packaging/nuitka/build_distribution.py \
            --build-dir "$BUILD_ROOT/compile" \
            --output-dir "$OUTPUT_DIR" \
            --platform-id kylin-v10-arm64 \
            --archive
        "$PYTHON" packaging/nuitka/smoke_distribution.py \
            "$OUTPUT_DIR/wx-doc-format-skill-$WXDF_VERSION-kylin-v10-arm64"
        chown -R "$WXDF_HOST_UID:$WXDF_HOST_GID" \
            "$BUILD_ROOT" \
            "$OUTPUT_DIR/wx-doc-format-skill-$WXDF_VERSION-kylin-v10-arm64" \
            "$OUTPUT_DIR/wx-doc-format-skill-$WXDF_VERSION-kylin-v10-arm64.tar.gz"
    '

echo "Linux ARM64 package: $PROJECT_DIR/dist/local/wx-doc-format-skill-$VERSION-kylin-v10-arm64.tar.gz"
