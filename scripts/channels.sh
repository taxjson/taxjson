#!/usr/bin/env bash
# Where each release channel points, what this machine's production copy
# runs, and the releases (the same page as `taxjson channels`).
#   scripts/channels.sh          # the newest 20 releases
#   scripts/channels.sh all      # every release
#   scripts/channels.sh --json   # as data
# It fetches tags and main from origin first; offline it says so and shows
# what this clone already knows. Production copy: ~/.local/share/taxjson,
# or TAXJSON_PROD_DIR.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x venv/bin/python3 ]; then PY=venv/bin/python3; else PY=python3; fi
fi
# The core needs only the standard library, so the checkout's own source
# runs without an install.
TAXJSON_DEV_DIR="$PWD" PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$PY" -m taxjson.bin.taxjson_run channels "$@"
