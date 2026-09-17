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

# Pre-push personal-data scan (a push to a public repo IS publication).
ln -sfn ../../scripts/hooks/pre-push .git/hooks/pre-push 2>/dev/null && echo "   pre-push hook installed (scripts/check-pii.sh)"
DENY="$HOME/.config/taxjson/pii-denylist"
if [ ! -e "$DENY" ]; then
  mkdir -p "$(dirname "$DENY")"
  cat > "$DENY" <<'DL'
# taxjson private PII denylist — one extended regex per line. Lives OUTSIDE
# every repository. Put here the exact strings that must never be
# published: your broker account numbers, your name, personal e-mail,
# your username. scripts/check-pii.sh (ci.sh, the pre-push hook) refuses
# any commit that contains a match.
DL
  chmod 600 "$DENY"
  echo "   scaffolded $DENY — add your account numbers, name and e-mail to it"
fi

echo "✅ taxjson installed. Activate with: source setup.sh"
echo "   Optional: venv/bin/pip install -e '.[ibkr]' for the harvest IBKR price tier"

