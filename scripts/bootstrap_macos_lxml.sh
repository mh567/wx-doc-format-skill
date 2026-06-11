#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${WX_DOC_FORMAT_VENV:-${SKILL_DIR}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "Creating isolated Python environment: ${VENV_DIR}"
"${PYTHON_BIN}" -m venv "${VENV_DIR}"

PY="${VENV_DIR}/bin/python"

echo "Upgrading packaging tools"
"${PY}" -m pip install --upgrade pip setuptools wheel

echo "Installing python-docx and lxml into isolated environment"
"${PY}" -m pip install --upgrade --force-reinstall --no-cache-dir --only-binary=:all: python-docx lxml

echo "Removing macOS quarantine attributes when present"
if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "${VENV_DIR}" 2>/dev/null || true
fi

echo "Applying ad-hoc code signature to native extension modules when possible"
if command -v codesign >/dev/null 2>&1; then
  while IFS= read -r file; do
    codesign --force --sign - "${file}" >/dev/null 2>&1 || true
  done < <(find "${VENV_DIR}" -type f \( -name "*.so" -o -name "*.dylib" \))
fi

echo "Verifying imports"
"${PY}" - <<'PY'
import lxml.etree
import docx
print("OK: lxml", lxml.etree.LXML_VERSION)
print("OK: python-docx", docx.__version__)
PY

echo "Done. Use this Python for best-effect conversion:"
echo "${PY}"
