#!/usr/bin/env bash
# Land this clone's new commits on main through a pull request — the one
# way scripts/release.sh --prepare and scripts/promote.sh publish main
# (docs/releasing.md, "main takes pull requests only").
#
#   scripts/land.sh [--no-wait] [--timeout MINUTES] [--footer TEXT]
#
# It refuses unless this is main, main is not behind origin/main and the
# pre-push PII gate (scripts/hooks/pre-push, run here through bash whether
# or not the hook is installed) passes on what it would publish. Then it
# pushes the new commits as a branch land/<time>-<sha>, opens the pull
# request, and has it rebase-merged once its checks pass: by GitHub's
# auto-merge (`gh pr merge --auto --rebase`) when main's ruleset requires
# checks and the repository allows auto-merge; otherwise it waits for every
# check itself and merges (`gh pr merge --rebase`). Then it brings local
# main to what landed: a rebase merge gives the commits new ids, so
# `git pull --rebase` drops the local copies. Uncommitted work is left as
# it was (--autostash).
#
# A failed check (or a pull request closed meanwhile) turns auto-merge off,
# closes the pull request and deletes its branch: nothing merges later
# behind your back; the commits stay on local main to fix and land again.
#
# Exit: 0 landed (or nothing to land); 1 a check failed, the pull request
# was closed, or a push / merge failed; 2 refused before anything was
# pushed; 3 still waiting at the timeout (the pull request stays open; with
# auto-merge it merges by itself, else the message says how to merge it).
#
# TAXJSON_LAND_DIRECT=1 pushes straight to main instead (after the same
# gate) — only for a remote with no pull-request rule: the script tests'
# throwaway remotes. Never `git push --no-verify`.
set -euo pipefail
cd "$(dirname "$0")/.."
die() { echo "✗ $1" >&2; exit "${2:-2}"; }
WAIT=1; TIMEOUT=60; FOOTER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-wait) WAIT=0; shift ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    --footer) FOOTER="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) die "unknown option $1 (scripts/land.sh [--no-wait] [--timeout MINUTES] [--footer TEXT])" ;;
  esac
done
[[ "$TIMEOUT" =~ ^[0-9]+$ ]] || die "--timeout takes whole minutes (got '$TIMEOUT')."

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || die "Land from main (this is $(git rev-parse --abbrev-ref HEAD))."
for f in scripts/hooks/pre-push scripts/check-pii.sh; do
  [ -f "$f" ] || die "$f is missing from this checkout — the pre-push PII gate cannot run, so nothing is landed."
done
git fetch -q origin main || die "Could not fetch main from origin."
AHEAD="$(git rev-list --count origin/main..HEAD)"
BEHIND="$(git rev-list --count HEAD..origin/main)"
[ "$AHEAD" -gt 0 ] || { echo "Nothing to land: main is origin/main."; exit 0; }
[ "$BEHIND" = 0 ] || die "main moved on by $BEHIND commit(s) — git pull --rebase first, then land."
HEAD_SHA="$(git rev-parse HEAD)"
Z=0000000000000000000000000000000000000000
URL_ORIGIN="$(git remote get-url origin)"

# The pre-push PII gate on exactly what this publishes (commits, patches,
# messages, identities, the branch name), through bash: a lost executable
# bit does not skip it. `git push` runs the installed hook again.
gate() {   # gate REMOTE_REF REMOTE_SHA
  printf 'refs/heads/main %s %s %s\n' "$HEAD_SHA" "$1" "$2" \
    | bash scripts/hooks/pre-push origin "$URL_ORIGIN" \
    || die "pre-push gate refused — nothing pushed (the commits stay on local main)." 2
}

if [ "${TAXJSON_LAND_DIRECT:-}" = 1 ]; then
  gate refs/heads/main "$(git rev-parse origin/main)"
  git push -q origin main || die "The push to origin was refused (did main move on, or does main take pull requests only?)." 1
  echo "✓ landed $AHEAD commit(s) on main (direct push, TAXJSON_LAND_DIRECT=1)"
  exit 0
fi

command -v gh >/dev/null 2>&1 || die "No gh command: install it and gh auth login (main takes pull requests only)."
gh auth status >/dev/null 2>&1 || die "gh is not logged in (gh auth login)."
SLUG="${TAXJSON_SLUG:-$(printf '%s\n' "$URL_ORIGIN" | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')}"
SLUG="${SLUG:-taxjson/taxjson}"
FIRST="$(git log -1 --format=%s "$(git rev-list --reverse origin/main..HEAD | sed -n 1p)")"
TITLE="$(git log -1 --format=%s)"
[ "$AHEAD" = 1 ] || TITLE="$FIRST (+$((AHEAD - 1)) more)"
BODY="$(git log --reverse --format='- %s' origin/main..HEAD)"
[ -z "$FOOTER" ] || BODY="$BODY

$FOOTER"
# The title and body are published too: the subjects passed the gate's
# message scan; the footer is scanned here.
if [ -n "$FOOTER" ] && ! printf '%s\n' "$FOOTER" | bash scripts/check-pii.sh --message >/dev/null; then
  die "the --footer text hit scripts/check-pii.sh --message — nothing pushed." 2
fi
BRANCH="land/$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
gate "refs/heads/$BRANCH" "$Z"

git push -q origin "HEAD:refs/heads/$BRANCH" || die "Could not push the branch $BRANCH." 1
URL="$(gh pr create --repo "$SLUG" --base main --head "$BRANCH" --title "$TITLE" --body "$BODY")" \
  || die "Could not open the pull request (the branch $BRANCH is pushed; delete it: git push origin --delete $BRANCH)." 1
URL="$(printf '%s\n' "$URL" | grep -oE 'https://[^ ]+/pull/[0-9]+' | tail -1)"
[ -n "$URL" ] || die "gh pr create gave no pull request address (the branch $BRANCH is pushed)." 1

# GitHub's auto-merge only when main's rules require checks (otherwise a
# pull request with nothing required is mergeable at once, before CI) and
# the repository allows it; else this script waits for the checks itself.
AUTO=0
REQUIRED="$(gh api "repos/$SLUG/rules/branches/main" --jq '[.[] | select(.type == "required_status_checks")] | length' 2>/dev/null || echo 0)"
if [ "${REQUIRED:-0}" != 0 ] && gh pr merge "$URL" --repo "$SLUG" --auto --rebase --delete-branch >/dev/null 2>&1; then
  AUTO=1
  echo "→ $URL: GitHub merges it (rebase) once its required checks pass"
else
  echo "→ $URL: merging it (rebase) here once every check has passed"
fi
[ "$WAIT" = 1 ] || exit 0

fail() {   # fail MESSAGE — nothing may merge later behind the caller's back
  [ "$AUTO" = 0 ] || gh pr merge "$URL" --repo "$SLUG" --disable-auto >/dev/null 2>&1 || true
  gh pr close "$URL" --repo "$SLUG" --delete-branch >/dev/null 2>&1 || true
  die "$1 — the pull request is closed and its branch deleted; the commits stay on local main: fix, commit, and land again." 1
}
END=$(( $(date +%s) + TIMEOUT * 60 ))
while :; do
  STATE="$(gh pr view "$URL" --repo "$SLUG" --json state -q .state 2>/dev/null || echo UNKNOWN)"
  case "$STATE" in
    MERGED) break ;;
    CLOSED) die "The pull request was closed without merging: $URL" 1 ;;
  esac
  # `gh pr checks` prints "name<TAB>state<TAB>elapsed<TAB>link" and exits
  # non-zero while a check is pending or failed: read the states, not the
  # exit code. Its checks are the head commit's, so landing a commit again
  # shows the last pull request's results until this one's run exists:
  # an Actions check counts only when its run is on this branch.
  RUNS="$(gh run list --repo "$SLUG" --branch "$BRANCH" --json databaseId --jq '[.[].databaseId | tostring] | join(" ")' 2>/dev/null || true)"
  CHECKS="$({ gh pr checks "$URL" --repo "$SLUG" 2>/dev/null || true; } | awk -F'\t' -v runs="$RUNS" '
    BEGIN { n = split(runs, r, " "); for (i = 1; i <= n; i++) mine[r[i]] = 1 }
    NF >= 2 {
      if (match($4, /\/actions\/runs\/[0-9]+\//) && !(substr($4, RSTART + 14, RLENGTH - 15) in mine)) next
      print
    }')"
  FAILED="$(printf '%s\n' "$CHECKS" | awk -F'\t' '$2 == "fail" || $2 == "cancel" { printf "%s, ", $1 }')"
  [ -z "$FAILED" ] || fail "Check(s) failed: ${FAILED%, } ($URL)"
  # Counted whole (wc reads everything): `grep -q` would stop early and,
  # with pipefail, turn a writer's SIGPIPE into "every check passed".
  NOTPASS="$(printf '%s\n' "$CHECKS" | awk -F'\t' 'NF >= 2 && $2 != "pass" && $2 != "skipping"' | wc -l)"
  if [ "$AUTO" = 0 ] && [ -n "$CHECKS" ] && [ "$NOTPASS" -eq 0 ]; then
    gh pr merge "$URL" --repo "$SLUG" --rebase --delete-branch >/dev/null \
      || die "Every check passed but the merge failed: $URL (merge it on GitHub, then git pull --rebase)." 1
  fi
  if [ "$(date +%s)" -ge "$END" ]; then
    if [ "$AUTO" = 1 ]; then
      die "Still waiting after $TIMEOUT min: $URL merges by itself when its checks pass (then: git pull --rebase)." 3
    fi
    die "Still waiting after $TIMEOUT min: once $URL's checks pass, merge it (gh pr merge $URL --rebase --delete-branch), then git pull --rebase." 3
  fi
  sleep "${TAXJSON_LAND_POLL_SECS:-20}"
done
# What landed are rebased copies of these commits: rebasing onto them
# drops ours.
git pull -q --rebase --autostash origin main
LANDED="$(git rev-parse HEAD)"
[ "$LANDED" = "$(git rev-parse origin/main)" ] \
  || echo "⚠ local main is not origin/main after the pull — check git status." >&2
echo "✓ landed $AHEAD commit(s) on main as ${LANDED:0:9}: $URL"
