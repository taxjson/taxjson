#!/usr/bin/env bash
# Did GitHub CI pass on this commit? Asked by scripts/release.sh (before it
# tags) and scripts/promote.sh (before a channel moves forward), so both
# read CI the same way:
#
#   scripts/github-ci.sh <commit> <label> [--slug OWNER/NAME] [--appear-wait SECONDS]
#
# The run that counts is the tests.yml run for the PUSH of that commit to
# main (event push, branch main, head_sha the commit): a pull request's
# run tested a merge onto whatever its base was then, not the commit main
# holds. A run still going is waited for (polled), up to
# TAXJSON_CI_WAIT_MINS minutes (default 60: a run queued for a
# self-hosted runner that is offline waits as long as the runner is off);
# a failed poll is tried again, up to 5 in a row. Right after a
# push GitHub takes a few seconds to list the run: --appear-wait gives it
# that long to appear (polling every TAXJSON_CI_POLL_SECS, default 10).
# <label> names the commit in messages (a tag, or "the version commit").
#
# Exit 0: the run passed ("✓ CI passed on <label>" on stdout). Exit 1: it
# failed (the run's address is named), there is no such run, it was still
# queued or running at the time limit, GitHub could not be asked, or gh is
# missing — the reason on stderr, prefixed "✗".
# Read-only: `gh api` GETs.
set -euo pipefail
cd "$(dirname "$0")/.."
no() { echo "✗ $*" >&2; exit 1; }

SHA=""; LABEL=""; SLUG=""; APPEAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --slug) SLUG="${2:-}"; shift 2 ;;
    --appear-wait) APPEAR="${2:-}"; shift 2 ;;
    -*) echo "github-ci.sh: unknown option $1" >&2; exit 2 ;;
    *) if [ -z "$SHA" ]; then SHA="$1"; elif [ -z "$LABEL" ]; then LABEL="$1"; else echo "github-ci.sh: too many arguments" >&2; exit 2; fi; shift ;;
  esac
done
[ -n "$SHA" ] || { echo "usage: scripts/github-ci.sh <commit> <label> [--slug OWNER/NAME] [--appear-wait SECONDS]" >&2; exit 2; }
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "github-ci.sh: '$SHA' is not a full commit sha" >&2; exit 2; }
[[ "$APPEAR" =~ ^[0-9]+$ ]] || { echo "github-ci.sh: --appear-wait takes whole seconds" >&2; exit 2; }
WAIT_MINS="${TAXJSON_CI_WAIT_MINS:-60}"
[[ "$WAIT_MINS" =~ ^[0-9]+$ ]] || { echo "github-ci.sh: TAXJSON_CI_WAIT_MINS takes whole minutes (got '$WAIT_MINS')" >&2; exit 2; }
LABEL="${LABEL:-${SHA:0:9}}"
if [ -z "$SLUG" ]; then
  SLUG="${TAXJSON_SLUG:-$(git remote get-url origin 2>/dev/null | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')}"
fi
SLUG="${SLUG:-taxjson/taxjson}"

command -v gh >/dev/null 2>&1 || no "Can't check CI: the gh command is not installed."
ask() {
  gh api "repos/$SLUG/actions/workflows/tests.yml/runs?head_sha=$SHA&event=push&branch=main&per_page=1" \
    --jq '.workflow_runs[0] // empty | "\(.id) \(.status) \(.conclusion)"'
}
run="$(ask)" || no "Could not ask GitHub about CI for $LABEL (${SHA:0:9})."
if [ -z "$run" ] && [ "$APPEAR" -gt 0 ]; then
  echo "No CI run listed yet for $LABEL (${SHA:0:9}) — waiting up to ${APPEAR}s for it to appear…"
  end=$(( $(date +%s) + APPEAR ))
  while [ -z "$run" ] && [ "$(date +%s)" -lt "$end" ]; do
    sleep "${TAXJSON_CI_POLL_SECS:-10}"
    run="$(ask)" || no "Could not ask GitHub about CI for $LABEL (${SHA:0:9})."
  done
fi
[ -n "$run" ] || no "No tests.yml run for $LABEL (${SHA:0:9}) on a push to main — was it pushed to main (and as the head of that push)?"
id="${run%% *}"; st="${run#* }"
if [ "${st%% *}" != completed ]; then
  echo "CI for $LABEL is still running — waiting for it: https://github.com/$SLUG/actions/runs/$id"
  # Polled through `gh api`, not `gh run watch`: watch reads check-run
  # annotations, which a fine-grained token may not, and so called every
  # run still going "failed". Bounded, as land.sh's wait is: a run queued
  # for an offline self-hosted runner would otherwise hold the cut (or the
  # promote) for ever.
  deadline=$(( $(date +%s) + WAIT_MINS * 60 )); errors=0
  while :; do
    sleep "${TAXJSON_CI_POLL_SECS:-10}"
    if now="$(gh api "repos/$SLUG/actions/runs/$id" --jq '"\(.status) \(.conclusion)"')"; then
      st="$now"; errors=0
      [ "${st%% *}" != completed ] || break
    else
      errors=$((errors + 1))
      [ "$errors" -lt 5 ] || no "Could not ask GitHub about CI for $LABEL (${SHA:0:9}): $errors tries in a row failed."
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      no "CI for $LABEL is still ${st%% *} after $WAIT_MINS min (TAXJSON_CI_WAIT_MINS): https://github.com/$SLUG/actions/runs/$id — is the runner online? Once the run has finished, cut (or promote) again."
    fi
  done
fi
if [ "$st" != "completed success" ]; then
  no "CI failed on $LABEL (${st#completed }): https://github.com/$SLUG/actions/runs/$id"
fi
echo "✓ CI passed on $LABEL (tests.yml, https://github.com/$SLUG/actions/runs/$id)"
