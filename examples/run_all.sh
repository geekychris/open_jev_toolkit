#!/usr/bin/env bash
# Run every example against the local server (start it first: scripts/serve.sh start).
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-../runtime/venv/bin/python}"
./01_curl.sh
for f in 0[2-6]_*.py; do echo; echo "=== $f"; "$PY" "$f"; done
