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
#
# It promotes only what GitHub holds: the local main must BE origin/main
# (fetched first; nothing unpushed rides along), the tag an annotated
# vX.Y.Z that origin has, on origin/main, and the channel's current
# value is read from origin/main:channels.json. A forward move also
# needs the GitHub Actions tests.yml run for the push of the tag's
# commit to main to be green (gh api; it waits for a run still going;
# TAXJSON_PROMOTE_IGNORE_CI=1 skips the check, with a warning), and a
# clean scripts/check-public.sh (release notes, issues and comments on
# GitHub; skipped with a warning without a working gh).
# A refused push takes the promote commit back off main.
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
[ "$(git cat-file -t "refs/tags/$TAG")" = tag ] \
  || die "$TAG is a lightweight tag — a release is an annotated tag (scripts/release.sh makes one)."
# The tag GitHub has, not one only this clone holds (fetch --tags never
# removes a local-only tag).
REMOTE_TAG="$(git ls-remote origin "refs/tags/$TAG" | cut -f1)" || die "Could not list origin's tags."
[ -n "$REMOTE_TAG" ] || die "$TAG is not on origin — a release is pushed by scripts/release.sh before it is promoted."
[ "$REMOTE_TAG" = "$(git rev-parse "refs/tags/$TAG")" ] || die "This clone's $TAG is not origin's — fetch the tags again (git fetch --tags --force origin)."
# What GitHub's main holds, and nothing else: the promote commit is
# pushed as main, so a local commit not yet pushed would ride along.
AHEAD="$(git rev-list --count origin/main..HEAD)"; BEHIND="$(git rev-list --count HEAD..origin/main)"
[ "$AHEAD" = 0 ] && [ "$BEHIND" = 0 ] \
  || die "Local main is not origin/main ($AHEAD commit(s) ahead, $BEHIND behind) — push or pull first, so a promote publishes nothing but itself."
git merge-base --is-ancestor "$TAG^{commit}" origin/main || die "$TAG is not on origin/main — promote only releases tagged on main."

# The channel's current release as installers see it: origin/main's file.
ORIGIN_CHANNELS="$(git show origin/main:channels.json 2>/dev/null)" || die "origin/main has no channels.json."
CURRENT="$(printf '%s\n' "$ORIGIN_CHANNELS" | sed -nE "s/.*\"$CH\"[[:space:]]*:[[:space:]]*\"(v[^\"]+)\".*/\\1/p" | sed -n 1p)"
[ -n "$CURRENT" ] || die "channels.json has no \"$CH\" entry — add one by hand (\"$CH\": \"vX.Y.Z\")."
if [ "$CURRENT" = "$TAG" ]; then echo "$CH already points at $TAG."; exit 0; fi
# Backwards is how a bad release is rolled back — never by accident.
NEWER="$(printf '%s\n%s\n' "$CURRENT" "$TAG" | sort -V | tail -1)"
if [ "$NEWER" = "$CURRENT" ]; then
  ok=""
  read -r -p "$CH is at $CURRENT; move it BACK to $TAG? [y/N] " ok || true
  [ "$ok" = y ] || [ "$ok" = Y ] || die "Nothing changed."
fi

# CI must have passed on the commit the tag names: promote is where a
# release reaches strangers, and the local gate is one machine. The run
# is the one for the push to main (a pull request's run tests a merge
# into whatever its base was then). Still running: wait for it. A
# rollback is not held to it — an older tag may predate a CI fix.
SLUG="${TAXJSON_SLUG:-$(git remote get-url origin | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')}"
SLUG="${SLUG:-taxjson/taxjson}"
ci_gate() {
  if [ "${TAXJSON_PROMOTE_IGNORE_CI:-}" = 1 ]; then
    echo "⚠ WARNING: GitHub Actions CI NOT checked for $TAG (TAXJSON_PROMOTE_IGNORE_CI=1)." >&2
    return 0
  fi
  local how="TAXJSON_PROMOTE_IGNORE_CI=1 promotes without the check" sha run id st
  command -v gh >/dev/null 2>&1 || die "Can't check CI: the gh command is not installed ($how)."
  sha="$(git rev-parse "$TAG^{commit}")"
  run="$(gh api "repos/$SLUG/actions/workflows/tests.yml/runs?head_sha=$sha&event=push&branch=main&per_page=1" \
           --jq '.workflow_runs[0] // empty | "\(.id) \(.status) \(.conclusion)"')" \
    || die "Could not ask GitHub about CI for $TAG ($how)."
  [ -n "$run" ] || die "No tests.yml run for $TAG (${sha:0:9}) on a push to main — was it pushed to main? ($how)"
  id="${run%% *}"; st="${run#* }"
  if [ "${st%% *}" != completed ]; then
    echo "CI for $TAG is still running — waiting for it…"
    gh run watch "$id" --repo "$SLUG" --exit-status >/dev/null 2>&1 \
      || die "CI failed on $TAG: https://github.com/$SLUG/actions/runs/$id — nothing changed."
  elif [ "$st" != "completed success" ]; then
    die "CI failed on $TAG (${st#completed }): https://github.com/$SLUG/actions/runs/$id — nothing changed."
  fi
  echo "✓ CI passed on $TAG (tests.yml)"
}
# What GitHub serves beside the code (release notes, issues, pull
# requests, comments), scanned for personal data before a release
# reaches more people (scripts/check-public.sh, read-only). Without a
# working gh it is skipped with a warning.
public_gate() {
  if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
    echo "⚠ WARNING: release notes, issues and comments on GitHub NOT scanned — gh is not installed or not logged in." >&2
    return 0
  fi
  [ -f scripts/check-public.sh ] || die "scripts/check-public.sh is missing from this checkout."
  TAXJSON_SLUG="$SLUG" bash scripts/check-public.sh \
    || die "scripts/check-public.sh refused (above): fix what GitHub serves first — nothing changed."
}
if [ "$NEWER" != "$CURRENT" ]; then ci_gate; public_gate; fi

sed -i.bak -E "s/(\"$CH\"[[:space:]]*:[[:space:]]*\")v[^\"]*(\")/\1$TAG\2/" channels.json && rm -f channels.json.bak
grep -q "\"$CH\": \"$TAG\"" channels.json || { git checkout -- channels.json; die "Could not update channels.json."; }
# Trailers only when the caller passes them (TAXJSON_PROMOTE_TRAILERS,
# one per line): a fixed Co-Authored-By would stamp every promote with a
# session that never made it.
MSG="Promote $TAG to $CH"
[ -z "${TAXJSON_PROMOTE_TRAILERS:-}" ] || MSG="$MSG

$TAXJSON_PROMOTE_TRAILERS"
# Only channels.json, whatever else is staged.
PREV="$(git rev-parse HEAD)"
git commit -q -m "$MSG" -- channels.json
PROMOTE_COMMIT="$(git rev-parse HEAD)"
# Take the promote commit back off main when it cannot be published —
# only that one commit: HEAD must be it, its parent the main we started
# from, its only file channels.json. It ends as `git reset --hard PREV`
# would for that commit (HEAD, index and file of channels.json back), but
# leaves any other staged or uncommitted work exactly as it was.
undo_promote() {
  if [ "$(git rev-parse HEAD)" = "$PROMOTE_COMMIT" ] \
     && [ "$(git rev-parse -q --verify HEAD~1)" = "$PREV" ] \
     && [ "$(git log -1 --format=%s HEAD)" = "Promote $TAG to $CH" ] \
     && [ "$(git diff-tree --no-commit-id --name-only -r HEAD)" = channels.json ] \
     && git reset -q --soft "$PREV" && git reset -q "$PREV" -- channels.json \
     && git checkout -q "$PREV" -- channels.json; then
    return 0
  fi
  echo "✗ Could not take the promote commit back safely — HEAD is $(git rev-parse --short HEAD); drop it by hand (git reset --keep HEAD~1)." >&2
  return 1
}
# The pre-push PII gate (commit messages and identities included), run
# here whether or not this clone has the hook installed, as release.sh
# does.
if [ -x scripts/hooks/pre-push ]; then
  if ! printf 'refs/heads/main %s refs/heads/main %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)" \
       | scripts/hooks/pre-push origin "$(git remote get-url origin)"; then
    undo_promote && die "pre-push gate refused — nothing pushed, and the promote commit is taken back."
    die "pre-push gate refused — nothing pushed."
  fi
fi
if ! git push -q origin main; then
  undo_promote && die "The push to origin was refused (did main move on?) — the promote commit is taken back; nothing changed here or there. Pull, then run it again."
  die "The push to origin was refused."
fi
echo "✓ $CH → $TAG  (was $CURRENT). New installs on $CH get it now; re-running the installer on $CH moves there."
