#!/bin/bash
# Run the wx-doc-format benchmark
set -e
cd "$(dirname "$0")/scripts"
python3 benchmark.py "$@"
