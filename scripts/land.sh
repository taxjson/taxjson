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
# check itself and merges (`gh pr merge --rebase`) — only while origin's
# main is still the base it pushed on, so the rebase replays the commits
# onto the base they were gated on (docs/releasing.md, "Why a rebase
# merge"). Then it brings local
# main to what landed: a rebase merge gives the commits new ids, so
# `git pull --rebase` drops the local copies. Uncommitted work is left as
# it was (--autostash).
#
# A failed check, a main that moved on meanwhile (another pull request
# merged, a promote from another clone) or a pull request closed meanwhile
# turns auto-merge off, closes the pull request and deletes its branch:
# nothing merges later behind your back; the commits stay on local main to
# fix (or pull --rebase) and land again.
#
# Exit: 0 landed (or nothing to land); 1 a check failed, main moved on, the
# pull request was closed, or a push / merge failed; 2 refused before anything was
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
    -h|--help) awk 'NR > 1 && !/^#/ { exit } NR > 1' "$0"; exit 0 ;;   # the comment block above
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
BASE="$(git rev-parse origin/main)"     # the base the commits were gated on
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

fail() {   # fail MESSAGE [NEXT] — nothing may merge later behind the caller's back
  [ "$AUTO" = 0 ] || gh pr merge "$URL" --repo "$SLUG" --disable-auto >/dev/null 2>&1 || true
  gh pr close "$URL" --repo "$SLUG" --delete-branch >/dev/null 2>&1 || true
  die "$1 — the pull request is closed and its branch deleted; the commits stay on local main: ${2:-fix, commit, and land again}." 1
}
# origin's main now (empty when it cannot be read).
origin_main() { git ls-remote origin refs/heads/main 2>/dev/null | cut -f1 || true; }
# Every check of the pushed commit, one "name<TAB>pass|fail|cancel|skipping|pending" line each:
# the jobs of its Actions runs on this branch, and its commit statuses. Not `gh pr checks`: the
# check-runs API it reads needs a permission fine-grained personal tokens cannot have, and the
# everyday token is one (publish kit D8, 2026-10-17). A run not finished yet, or one whose jobs
# cannot be read, counts as pending; one that ended with no job is a failure (a broken workflow).
# A workflow every pull request triggers (a .github/workflows/*.yml with a pull_request trigger
# and no paths filter: kit-checks.yml, tests.yml; privacy-watch.yml runs on a few paths only)
# whose run GitHub has not created yet counts as pending too: an early poll must not see only
# the runs that exist and call the rest passed.
expected() {
  local f
  for f in .github/workflows/*.yml; do
    [ -f "$f" ] || continue
    awk '/^on:/ { on = 1; if ($0 ~ /pull_request/) seen = 1; next }
         on && /^[^ #]/ { on = 0 }
         on && /^  pull_request:/ { pr = 1; seen = 1; next }
         on && pr && /^  [^ ]/ { pr = 0 }
         on && pr && /^    paths(-ignore)?:/ { filtered = 1 }
         END { exit !(seen && !filtered) }' "$f" || continue
    sed -nE 's/^name:[[:space:]]*"?([^"]*[^" ])"?[[:space:]]*$/\1/p' "$f" | sed -n 1p
  done
}
checks() {
  local id status name jobs runs
  runs="$(gh api "repos/$SLUG/actions/runs?branch=$BRANCH&head_sha=$HEAD_SHA&per_page=100" \
      --jq '.workflow_runs[] | [(.id | tostring), .status, .name] | @tsv' 2>/dev/null || true)"
  expected | while IFS= read -r name; do
    [ -n "$name" ] || continue
    printf '%s\n' "$runs" | awk -F'\t' -v n="$name" '$3 == n { found = 1 } END { exit !found }' \
      || printf '%s\tpending\n' "$name"
  done
  printf '%s\n' "$runs" |
  while IFS="$(printf '\t')" read -r id status name; do
    [ -n "$id" ] || continue
    [ "$status" = completed ] || { printf '%s\tpending\n' "$name"; continue; }
    jobs="$(gh api "repos/$SLUG/actions/runs/$id/jobs?per_page=100" --jq '.jobs[] | [.name,
        (if .status != "completed" then "pending" elif .conclusion == "success" or .conclusion == "neutral" then "pass"
         elif .conclusion == "skipped" then "skipping" elif .conclusion == "cancelled" then "cancel" else "fail" end)] | @tsv' 2>/dev/null)" \
      || { printf '%s\tpending\n' "$name"; continue; }
    [ -n "$jobs" ] && printf '%s\n' "$jobs" || printf '%s\tfail\n' "$name"
  done
  gh api "repos/$SLUG/commits/$HEAD_SHA/status" --jq '.statuses[] | [.context,
      (if .state == "success" then "pass" elif .state == "pending" then "pending" else "fail" end)] | @tsv' 2>/dev/null || true
}
END=$(( $(date +%s) + TIMEOUT * 60 ))
while :; do
  STATE="$(gh pr view "$URL" --repo "$SLUG" --json state -q .state 2>/dev/null || echo UNKNOWN)"
  case "$STATE" in
    MERGED) break ;;
    CLOSED) die "The pull request was closed without merging: $URL" 1 ;;
  esac
  CHECKS="$(checks)"
  FAILED="$(printf '%s\n' "$CHECKS" | awk -F'\t' '$2 == "fail" || $2 == "cancel" { printf "%s, ", $1 }')"
  [ -z "$FAILED" ] || fail "Check(s) failed: ${FAILED%, } ($URL)"
  # Counted whole (wc reads everything): `grep -q` would stop early and,
  # with pipefail, turn a writer's SIGPIPE into "every check passed".
  NOTPASS="$(printf '%s\n' "$CHECKS" | awk -F'\t' 'NF >= 2 && $2 != "pass" && $2 != "skipping"' | wc -l)"
  # Only onto the base the commits were gated on (docs/releasing.md, "Why
  # a rebase merge"): GitHub's rebase merge replays them onto whatever main
  # is by then, and a main that moved meanwhile would make the landed
  # commits trees no gate (local or this pull request's) ran. A main that
  # cannot be read is asked again next round; a merge GitHub's auto-merge
  # just made is the move itself.
  NOW_MAIN="$(origin_main)"
  if [ -n "$NOW_MAIN" ] && [ "$NOW_MAIN" != "$BASE" ]; then
    [ "$(gh pr view "$URL" --repo "$SLUG" --json state -q .state 2>/dev/null || echo UNKNOWN)" != MERGED ] || break
    fail "main moved on while the checks ran (origin/main is ${NOW_MAIN:0:9}; they ran on ${BASE:0:9}): $URL" \
         "git pull --rebase, run the gate again, and land again"
  fi
  if [ "$AUTO" = 0 ] && [ -n "$CHECKS" ] && [ "$NOTPASS" -eq 0 ] && [ "$NOW_MAIN" = "$BASE" ]; then
    gh pr merge "$URL" --repo "$SLUG" --rebase --delete-branch >/dev/null \
      || die "Every check passed but the merge failed: $URL (merge it on GitHub, then git pull --rebase)." 1
    break        # merged: not "still waiting" when the timeout falls in the same round
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
