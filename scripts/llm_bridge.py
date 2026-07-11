#!/usr/bin/env python3
"""LLM bridge — reads prompt from stdin, calls LLM command, writes response to stdout.

Usage:
  LLM_COMMAND="codex exec" python3 llm_bridge.py < prompt.txt > response.json

The LLM_COMMAND environment variable must point to a command that:
  - Reads prompt from stdin
  - Writes response to stdout
  - Exits with code 0 on success
"""

import os
import subprocess
import sys

COMMAND = os.environ.get("LLM_COMMAND", "").strip()
if not COMMAND:
    print("Error: LLM_COMMAND environment variable not set", file=sys.stderr)
    sys.exit(1)

prompt = sys.stdin.read()
if not prompt.strip():
    print("Error: empty prompt", file=sys.stderr)
    sys.exit(1)

try:
    r = subprocess.run(
        COMMAND,
        input=prompt,
        text=True,
        shell=True,
        capture_output=True,
        timeout=120,
    )
    if r.returncode != 0:
        print(f"LLM command failed (exit {r.returncode}): {r.stderr[:200]}", file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(r.stdout or "")
except subprocess.TimeoutExpired:
    print("LLM command timed out", file=sys.stderr)
    sys.exit(1)
