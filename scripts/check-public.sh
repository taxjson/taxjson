#!/usr/bin/env bash
# What GitHub serves BESIDE the code, scanned for personal data: the
# notes of every release, the title and body of every issue and pull
# request, and every issue / pull-request conversation comment, review
# comment, pull-request review (the text a review is submitted with: GitHub
# keeps it apart from the review's line comments, one list per pull
# request — backlog TJ-0944) and commit comment. None of it is in git, so
# neither the pre-push hook nor ci.sh ever sees it.
#
#   scripts/check-public.sh [--repo OWNER/NAME] [--days N]
#
# --days N: only what changed in the last N days (issues and pull requests
# updated, comments updated, the reviews of the pull requests updated), plus
# the messages of the commits on main in
# those days — the daily "privacy watch" workflow (.github/
# workflows/privacy-watch.yml) runs it with --days 2. Release notes are
# always read whole (a release's notes can be edited at any time and
# carry no "updated" date). Without --days: everything, and no commit
# messages (old history is kept as it is; the pre-push hook and CI's
# per-commit scan check new ones).
#
# Each kind goes through scripts/check-pii.sh (the private denylist, the
# private figure list and the generic patterns); release notes as
# --message (money amounts too, as for commit messages), the rest as
# --text. Read-only: `gh api` GETs, nothing written anywhere. A hit is
# shown masked, as check-pii.sh shows it, with the address of the item
# that holds it (never its title or text). scripts/promote.sh runs this
# before moving a channel forward.
#
# The repository: --repo, else TAXJSON_SLUG, else the github.com address
# of the remote `public` (the maintainer's clone: origin is the private dev
# repo), else origin's (a clone of the public repo, as in its workflows),
# else taxjson/taxjson. gh's own login, or a GH_TOKEN it is
# given (a workflow's read-only token).
# Exit 0 clean; 1 a hit; 2 cannot check (no gh, not logged in, an API
# error, no python3) — it fails closed, never "clean" on what it could
# not read.
set -uo pipefail
cd "$(dirname "$0")/.."
SLUG="${TAXJSON_SLUG:-}"
DAYS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) SLUG="${2:-}"; shift 2 || shift ;;
    --repo=*) SLUG="${1#--repo=}"; shift ;;
    --days) DAYS="${2:-}"; shift 2 || shift ;;
    --days=*) DAYS="${1#--days=}"; shift ;;
    -h|--help) sed -n '2,37p' "$0"; exit 0 ;;
    *) echo "check-public: unknown argument '$1' (usage: scripts/check-public.sh [--repo OWNER/NAME] [--days N])" >&2; exit 2 ;;
  esac
done
for r in public origin; do
  [ -z "$SLUG" ] || break
  SLUG="$(git remote get-url "$r" 2>/dev/null | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')"
done
SLUG="${SLUG:-taxjson/taxjson}"
[[ "$SLUG" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "check-public: '$SLUG' is not OWNER/NAME" >&2; exit 2; }
[ -z "$DAYS" ] || [[ "$DAYS" =~ ^[1-9][0-9]*$ ]] || { echo "check-public: --days takes a whole number of days (got '$DAYS')" >&2; exit 2; }
command -v gh >/dev/null 2>&1 || { echo "check-public: gh is not installed — cannot read what GitHub serves" >&2; exit 2; }
[ -n "${GH_TOKEN:-}" ] || gh auth status >/dev/null 2>&1 || { echo "check-public: gh is not logged in (gh auth login) — cannot read what GitHub serves" >&2; exit 2; }
PY="$(command -v "${PYTHON:-python3}" 2>/dev/null)" || { echo "check-public: python3 is needed" >&2; exit 2; }

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
SINCE=""; Q=""
if [ -n "$DAYS" ]; then
  SINCE="$(date -u -d "-$DAYS days" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-"$DAYS"d +%Y-%m-%dT%H:%M:%SZ)"
  Q="&since=$SINCE"
fi
# kind  check-pii mode  API path
KINDS="releases --message repos/$SLUG/releases?per_page=100
issues --text repos/$SLUG/issues?state=all&per_page=100$Q
issue-comments --text repos/$SLUG/issues/comments?per_page=100$Q
review-comments --text repos/$SLUG/pulls/comments?per_page=100$Q
commit-comments --text repos/$SLUG/comments?per_page=100"
[ -z "$DAYS" ] || KINDS="$KINDS
commits --message repos/$SLUG/commits?per_page=100$Q"

# One JSON document per page (gh api --paginate prints them back to
# back) -> KIND.txt (every item, one after another), and for each item
# KIND.d/N.txt plus a line "N<TAB>address" in KIND.idx, to name the item
# that holds a hit (a line number in KIND.txt would not say which item).
SPLIT_PY='
import json, os, sys
kind, src, txt, idx, since = sys.argv[1:6]
raw = open(src, encoding="utf-8").read()
dec = json.JSONDecoder(); pos = 0; items = []
while True:
    while pos < len(raw) and raw[pos].isspace():
        pos += 1
    if pos >= len(raw):
        break
    doc, pos = dec.raw_decode(raw, pos)
    if not isinstance(doc, list):
        raise SystemExit("unexpected API answer (not a list)")
    items += doc
# Commit comments have no `since` parameter: keep those updated since.
if since and kind == "commit-comments":
    items = [it for it in items if isinstance(it, dict)
             and str(it.get("updated_at") or it.get("created_at") or "") >= since]
d = txt[:-4] + ".d"
os.makedirs(d)
with open(txt, "w", encoding="utf-8") as t, open(idx, "w", encoding="utf-8") as x:
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        if kind == "releases":
            where = "release " + str(it.get("tag_name") or it.get("id"))
            parts = [it.get("name"), it.get("body")]
        elif kind == "commits":
            where = "commit " + str(it.get("sha") or "")[:12]
            parts = [(it.get("commit") or {}).get("message")]
        elif kind == "issues":
            where = it.get("html_url") or str(it.get("number"))
            parts = [it.get("title"), it.get("body")]
        else:
            where = it.get("html_url") or str(it.get("id"))
            parts = [it.get("body")]
        text = "\n".join(p for p in parts if p).replace("\r\n", "\n").replace("\r", "\n") + "\n"
        t.write(text)
        with open(os.path.join(d, "%d.txt" % n), "w", encoding="utf-8") as one:
            one.write(text)
        x.write("%d\t%s\n" % (n, " ".join(where.split())))
print(len(items))
'
# The pull requests among the issues read (the issues list holds them,
# each with a `pull_request` key): one number per line.
PRS_PY='
import json, sys
raw = open(sys.argv[1], encoding="utf-8").read()
dec = json.JSONDecoder(); pos = 0
while True:
    while pos < len(raw) and raw[pos].isspace():
        pos += 1
    if pos >= len(raw):
        break
    doc, pos = dec.raw_decode(raw, pos)
    for it in doc if isinstance(doc, list) else []:
        if isinstance(it, dict) and it.get("pull_request") \
                and isinstance(it.get("number"), int):
            print(it["number"])
'
rc=0; summary=""
# scan KIND MODE: $W/KIND.json (read already) through check-pii.sh.
scan() {
  local kind="$1" mode="$2" count out named n where
  if ! count="$("$PY" -c "$SPLIT_PY" "$kind" "$W/$kind.json" "$W/$kind.txt" "$W/$kind.idx" "$SINCE" 2> "$W/$kind.err")"; then
    echo "check-public: could not read the $kind of $SLUG: $(tail -1 "$W/$kind.err" | cut -c1-200)" >&2
    exit 2
  fi
  summary="$summary $count $kind,"
  [ -s "$W/$kind.txt" ] || return 0
  # The whole kind at once; on a hit (or a scanner error), item by item
  # to say where.
  scripts/check-pii.sh "$mode" < "$W/$kind.txt" > /dev/null 2>&1 && return 0
  rc=1
  echo "!! $kind of $SLUG:"
  named=0
  while IFS=$'\t' read -r n where; do
    out="$(scripts/check-pii.sh "$mode" < "$W/$kind.d/$n.txt" 2>&1)" && continue
    named=1
    echo "   in $where:"
    printf '%s\n' "$out" | grep -v '^check-pii: ' | grep -v '^$' | sed 's/^/     /'
  done < "$W/$kind.idx"
  if [ "$named" = 0 ]; then
    echo "   (no single item hits on its own; the scan of them together did:)"
    scripts/check-pii.sh "$mode" < "$W/$kind.txt" 2>&1 | grep -v '^check-pii: ' | sed 's/^/     /'
  fi
}
while read -r kind mode path; do
  [ -n "$kind" ] || continue
  if ! gh api --paginate "$path" > "$W/$kind.json" 2> "$W/$kind.err"; then
    echo "check-public: could not read the $kind of $SLUG: $(tail -1 "$W/$kind.err" | cut -c1-200)" >&2
    exit 2
  fi
  scan "$kind" "$mode"
done <<< "$KINDS"
# The reviews of each pull request read above (with --days: those updated
# in the window — a review submitted updates its pull request).
if ! prs="$("$PY" -c "$PRS_PY" "$W/issues.json" 2> "$W/reviews.err")"; then
  echo "check-public: could not read the pull requests of $SLUG: $(tail -1 "$W/reviews.err" | cut -c1-200)" >&2
  exit 2
fi
: > "$W/reviews.json"
for n in $prs; do
  if ! gh api --paginate "repos/$SLUG/pulls/$n/reviews?per_page=100" >> "$W/reviews.json" 2> "$W/reviews.err"; then
    echo "check-public: could not read the reviews of pull request $n of $SLUG: $(tail -1 "$W/reviews.err" | cut -c1-200)" >&2
    exit 2
  fi
  echo >> "$W/reviews.json"
done
scan reviews --text
summary="${summary%,}"
if [ "$rc" -ne 0 ]; then
  echo
  echo "check-public: personal data in what GitHub serves for $SLUG — edit or delete that issue, comment, review or release on GitHub (a release's title and notes stay editable even when it is immutable; a commit message needs the procedure in docs/releasing.md, "Something private leaked")."
  exit 1
fi
echo "check-public: clean — what GitHub serves for $SLUG${SINCE:+ (changed since $SINCE)}:$summary"
