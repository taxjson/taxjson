#!/bin/bash
# Developer setup (clone-then-run): venv + editable install with the extras the full
# pipeline uses ([fx] = FX-rate fetching for taxjson run, [dev] = the
# CI linter). Idempotent — safe to
# re-run after a pull. Activate afterwards with: source setup.sh. End users: see install.sh (curl one-liner).
#   scripts/dev-setup.sh --hook-only   only (re)install the pre-push hook
set -e
cd "$(dirname "$0")/.."

# Pre-push personal-data scan and tag guard (a push to a public repo IS
# publication). Installed into this clone's OWN hooks folder,
# <git-common-dir>/hooks (shared by every worktree of the clone; a
# worktree's .git is a file, so `.git/hooks` is not it) — also when a
# global core.hooksPath is set: git then runs that folder's pre-push
# instead, and a chaining global hook (one that runs
# "$(git rev-parse --git-common-dir)/hooks/pre-push") reaches this one.
# The installed file is a small wrapper that runs the pushing checkout's
# own scripts/hooks/pre-push, so each worktree is checked by its own copy.
HOOK_MARK="# taxjson pre-push wrapper (scripts/dev-setup.sh)"
install_hook() {
  local common hook hp ghook
  common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || {
    echo "   NOTE: not a git checkout — install scripts/hooks/pre-push as the pre-push hook by hand."; return 0; }
  mkdir -p "$common/hooks"
  hook="$common/hooks/pre-push"
  if [ -L "$hook" ] && readlink "$hook" | grep -q 'scripts/hooks/pre-push$'; then
    rm -f "$hook"                       # the older symlink install: replaced
  elif [ -e "$hook" ] && ! grep -qF "$HOOK_MARK" "$hook" 2>/dev/null; then
    echo "   NOTE: $hook already exists and is not ours — left alone; make it run scripts/hooks/pre-push."
    return 0
  fi
  cat > "$hook.tmp" <<HOOK
#!/bin/sh
$HOOK_MARK: the tag guard and PII scan
# of the checkout being pushed from (any worktree of this clone).
h="\$(git rev-parse --show-toplevel)/scripts/hooks/pre-push"
[ -x "\$h" ] || { echo "pre-push: \$h is missing — the taxjson tag guard and PII scan cannot run; push from a checkout that has it" >&2; exit 1; }
exec "\$h" "\$@"
HOOK
  chmod 755 "$hook.tmp" && mv -f "$hook.tmp" "$hook"
  echo "   pre-push hook installed: $hook (tag guard + scripts/check-pii.sh; every worktree of this clone)"
  hp="$(git config --path core.hooksPath || true)"
  if [ -n "$hp" ]; then
    ghook="$hp/pre-push"
    if [ "$hp" -ef "$common/hooks" ]; then
      :
    elif [ -f "$ghook" ] && grep -q 'git-common-dir' "$ghook" && grep -q 'hooks/pre-push' "$ghook"; then
      echo "   core.hooksPath=$hp is set: git runs $ghook, which chains to the hook above."
    else
      echo "   WARNING: core.hooksPath=$hp is set, so git runs the hooks there, and $ghook does not run the hook above —"
      echo "            the tag guard and PII scan will NOT run on push. Add to $ghook:"
      echo "              own=\"\$(git rev-parse --git-common-dir)/hooks/pre-push\"; [ -x \"\$own\" ] && exec \"\$own\" \"\$@\""
    fi
  fi
}
if [ "${1:-}" = --hook-only ]; then install_hook; exit 0; fi

python3 -m venv venv                       # no-op if venv already exists
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -e ".[fx,dev]"
# The broker-fetch plugin (`taxjson fetch`: Questrade, IBKR Flex) — a separate
# distribution in packages/, installed beside the core. --no-deps: its only
# dependency is the core installed just above from this checkout.
venv/bin/pip install --no-deps -e packages/taxjson-fetch

install_hook
DENY="$HOME/.config/taxjson/pii-denylist"
if [ ! -e "$DENY" ]; then
  mkdir -p "$(dirname "$DENY")"
  (umask 077; cat > "$DENY") <<'DL'
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

