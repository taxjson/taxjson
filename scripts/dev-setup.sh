#!/bin/bash
# Developer setup (clone-then-run): venv + editable install with the extras the full
# pipeline uses ([fx] = FX-rate fetching for taxjson run, [web] =
# taxjson serve). Idempotent — safe to
# re-run after a pull. Activate afterwards with: source setup.sh. End users: see install.sh (curl one-liner).
set -e
cd "$(dirname "$0")/.."

python3 -m venv venv                       # no-op if venv already exists
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -e ".[web,fx,dev]"

echo "✅ taxjson installed. Activate with: source setup.sh"
echo "   Optional: venv/bin/pip install -e '.[ibkr]' for the harvest IBKR price tier"

