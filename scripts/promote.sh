#!/usr/bin/env bash
# Point a release channel at a release — what new installs (and re-runs
# of the installer on that channel) get from now on.
#
#   scripts/promote.sh v0.17.0            # stable → v0.17.0
#   scripts/promote.sh v0.18.0 beta       # beta   → v0.18.0
#   TAXJSON_PROMOTE_TRAILERS='Co-Authored-By: …' scripts/promote.sh v0.17.0
#
# Tagging a release (scripts/release.sh) makes it `latest` and nothing
# more; `stable` and `beta` move only when you run this. That is what
# lets you release as often as you like without a newcomer's first
# install landing on whatever was tagged an hour ago. It commits
# channels.json on main ("Promote vX.Y.Z to stable") and pushes — no new
# tag, no rebuild. Moving a channel BACKWARDS (rolling a bad release
# back) is allowed, but it asks first. `taxjson promote` runs this.
set -euo pipefail
cd "$(dirname "$0")/.."
die() { echo "✗ $*" >&2; exit 1; }

TAG="${1:-}"; CH="${2:-stable}"
[ -n "$TAG" ] || die "usage: scripts/promote.sh vX.Y.Z [stable|beta]"
case "$TAG" in [0-9]*) TAG="v$TAG" ;; esac
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "A release is vX.Y.Z (got '$TAG') — the installer ships no other tag."
case "$CH" in
  stable|beta) ;;
  latest) die "latest is always the newest release tag — tag with scripts/release.sh; there is nothing to promote." ;;
  *) die "Channel must be stable or beta (got '$CH')." ;;
esac
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || die "Promote from main."
[ -f channels.json ] || die "No channels.json in this checkout — pull main first."
[ -z "$(git status --porcelain -- channels.json)" ] || die "channels.json has uncommitted changes."
git fetch --tags --force --quiet origin || die "Could not fetch from origin — promoting pushes, so it needs the remote."
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || die "No tag $TAG — cut it with scripts/release.sh first."
git merge-base --is-ancestor origin/main HEAD || die "Local main is behind origin/main — pull first."

CURRENT="$(sed -nE "s/.*\"$CH\"[[:space:]]*:[[:space:]]*\"(v[^\"]+)\".*/\1/p" channels.json | sed -n 1p)"
[ -n "$CURRENT" ] || die "channels.json has no \"$CH\" entry — add one by hand (\"$CH\": \"vX.Y.Z\")."
if [ "$CURRENT" = "$TAG" ]; then echo "$CH already points at $TAG."; exit 0; fi
# Backwards is how a bad release is rolled back — never by accident.
NEWER="$(printf '%s\n%s\n' "$CURRENT" "$TAG" | sort -V | tail -1)"
if [ "$NEWER" = "$CURRENT" ]; then
  ok=""
  read -r -p "$CH is at $CURRENT; move it BACK to $TAG? [y/N] " ok || true
  [ "$ok" = y ] || [ "$ok" = Y ] || die "Nothing changed."
fi

sed -i.bak -E "s/(\"$CH\"[[:space:]]*:[[:space:]]*\")v[^\"]*(\")/\1$TAG\2/" channels.json && rm -f channels.json.bak
grep -q "\"$CH\": \"$TAG\"" channels.json || { git checkout -- channels.json; die "Could not update channels.json."; }
# Trailers only when the caller passes them (TAXJSON_PROMOTE_TRAILERS,
# one per line): a fixed Co-Authored-By would stamp every promote with a
# session that never made it.
MSG="Promote $TAG to $CH"
[ -z "${TAXJSON_PROMOTE_TRAILERS:-}" ] || MSG="$MSG

$TAXJSON_PROMOTE_TRAILERS"
# Only channels.json, whatever else is staged.
git commit -q -m "$MSG" -- channels.json
# The pre-push PII gate (commit messages and identities included), run
# here whether or not this clone has the hook installed, as release.sh
# does.
if [ -x scripts/hooks/pre-push ]; then
  printf 'refs/heads/main %s refs/heads/main %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)" \
    | scripts/hooks/pre-push origin "$(git remote get-url origin)" \
    || die "pre-push gate refused — nothing pushed. The promote commit is local: git reset --hard HEAD~1 to drop it."
fi
git push -q origin main || die "Push failed — the promote commit is local (git reset --hard HEAD~1 to drop it, or pull --rebase and push)."
echo "✓ $CH → $TAG  (was $CURRENT). New installs on $CH get it now; re-running the installer on $CH moves there."
