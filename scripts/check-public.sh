#!/usr/bin/env bash
# What GitHub serves BESIDE the code, scanned for personal data: the
# notes of every release, the title and body of every issue and pull
# request, and every issue / pull-request conversation comment, review
# comment and commit comment. None of it is in git, so neither the
# pre-push hook nor ci.sh ever sees it, and a release note is immutable.
#
#   scripts/check-public.sh [--repo OWNER/NAME]
#
# Each kind goes through scripts/check-pii.sh (the private denylist, the
# private figure list and the generic patterns); release notes as
# --message (money amounts too, as for commit messages), the rest as
# --text. Read-only: `gh api` GETs, nothing written anywhere. A hit is
# shown masked, as check-pii.sh shows it, with the address of the item
# that holds it (never its title or text). scripts/promote.sh runs this
# before moving a channel forward.
#
# The repository: --repo, else TAXJSON_SLUG, else origin's github.com
# address, else taxjson/taxjson.
# Exit 0 clean; 1 a hit; 2 cannot check (no gh, not logged in, an API
# error, no python3) — it fails closed, never "clean" on what it could
# not read.
set -uo pipefail
cd "$(dirname "$0")/.."
SLUG="${TAXJSON_SLUG:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) SLUG="${2:-}"; shift 2 || shift ;;
    --repo=*) SLUG="${1#--repo=}"; shift ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) echo "check-public: unknown argument '$1' (usage: scripts/check-public.sh [--repo OWNER/NAME])" >&2; exit 2 ;;
  esac
done
if [ -z "$SLUG" ]; then
  SLUG="$(git remote get-url origin 2>/dev/null | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')"
fi
SLUG="${SLUG:-taxjson/taxjson}"
[[ "$SLUG" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "check-public: '$SLUG' is not OWNER/NAME" >&2; exit 2; }
command -v gh >/dev/null 2>&1 || { echo "check-public: gh is not installed — cannot read what GitHub serves" >&2; exit 2; }
gh auth status >/dev/null 2>&1 || { echo "check-public: gh is not logged in (gh auth login) — cannot read what GitHub serves" >&2; exit 2; }
PY="$(command -v "${PYTHON:-python3}" 2>/dev/null)" || { echo "check-public: python3 is needed" >&2; exit 2; }

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
# kind  check-pii mode  API path
KINDS="releases --message repos/$SLUG/releases?per_page=100
issues --text repos/$SLUG/issues?state=all&per_page=100
issue-comments --text repos/$SLUG/issues/comments?per_page=100
review-comments --text repos/$SLUG/pulls/comments?per_page=100
commit-comments --text repos/$SLUG/comments?per_page=100"

# One JSON document per page (gh api --paginate prints them back to
# back) -> KIND.txt (every item, one after another), and for each item
# KIND.d/N.txt plus a line "N<TAB>address" in KIND.idx, to name the item
# that holds a hit (check-pii.sh masks line numbers).
SPLIT_PY='
import json, os, sys
kind, src, txt, idx = sys.argv[1:5]
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
d = txt[:-4] + ".d"
os.makedirs(d)
with open(txt, "w", encoding="utf-8") as t, open(idx, "w", encoding="utf-8") as x:
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        if kind == "releases":
            where = "release " + str(it.get("tag_name") or it.get("id"))
            parts = [it.get("name"), it.get("body")]
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
rc=0; summary=""
while read -r kind mode path; do
  [ -n "$kind" ] || continue
  if ! gh api --paginate "$path" > "$W/$kind.json" 2> "$W/$kind.err"; then
    echo "check-public: could not read the $kind of $SLUG: $(tail -1 "$W/$kind.err" | cut -c1-200)" >&2
    exit 2
  fi
  if ! count="$("$PY" -c "$SPLIT_PY" "$kind" "$W/$kind.json" "$W/$kind.txt" "$W/$kind.idx" 2> "$W/$kind.err")"; then
    echo "check-public: could not read the $kind of $SLUG: $(tail -1 "$W/$kind.err" | cut -c1-200)" >&2
    exit 2
  fi
  summary="$summary $count $kind,"
  [ -s "$W/$kind.txt" ] || continue
  # The whole kind at once; on a hit (or a scanner error), item by item
  # to say where.
  scripts/check-pii.sh "$mode" < "$W/$kind.txt" > /dev/null 2>&1 && continue
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
done <<< "$KINDS"
summary="${summary%,}"
if [ "$rc" -ne 0 ]; then
  echo
  echo "check-public: personal data in what GitHub serves for $SLUG — edit or delete that issue, comment or release on GitHub (an immutable release's notes cannot be edited in place)."
  exit 1
fi
echo "check-public: clean — what GitHub serves for $SLUG:$summary"
