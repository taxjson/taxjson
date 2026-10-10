#!/usr/bin/env bash
# Cut a release: scripts/release.sh vX.Y.Z   (X.Y.Z also accepted)
#
# main is the development line; a vX.Y.Z tag is a release. Tagging makes
# the release `latest` and nothing more: new installs take `stable`, which
# channels.json on main names and only scripts/promote.sh moves
# (docs/releasing.md). This script is the only way a tag should be made:
#   1. refuses on a dirty tree or off main;
#   2. turns the CHANGELOG's "## Unreleased" into "## vX.Y.Z (date)"
#      (or requires that heading to exist already), and each
#      "Fixed in: unreleased" in docs/troubleshooting.md into the tag;
#   3. bumps pyproject.toml and, in lockstep, the taxjson-fetch plugin's
#      packages/taxjson-fetch/pyproject.toml (its version and its
#      `taxjson>=` floor), reinstalls so `taxjson --version` agrees;
#   4. runs the FULL local gate (scripts/ci.sh, fuzzers included) —
#      unless the gate already PASSED on this very commit's tree
#      (scripts/gate-record.sh: same tree hash, a full mode, the same
#      Python, within --gate-max-age days, default 7) and the release's
#      edits are proven to be only the version, tag and date lines of
#      step 2-3 (release_edits_only); then it runs the checks that read
#      those lines (scripts/ci.sh --release-edits) instead. --fresh-gate
#      always runs the full gate;
#   5. commits, tags (annotated), runs the pre-push PII gate itself, and
#      pushes main + the one tag (never `git push --tags`);
#   6. publishes the GitHub release: the CHANGELOG's `## vX.Y.Z` section
#      (or --notes FILE) as its notes, scanned by
#      `scripts/check-pii.sh --message` first (before anything is pushed
#      and again right before `gh release create`). Releases are
#      immutable, so notes are never fixed after the fact: a scan hit
#      stops the release. Without gh (or not logged in) it stops after
#      the push and prints the exact command to finish with.
#
#   scripts/release.sh vX.Y.Z [--notes FILE] [--fresh-gate] [--gate-max-age DAYS]
# It never touches channels.json. Roll a bad release back by promoting
# the previous good one (scripts/promote.sh vX.Y.Z — it asks before
# moving a channel backwards) — never by moving or deleting a published
# tag.
set -euo pipefail
PWD0="$PWD"
USAGE="usage: scripts/release.sh vX.Y.Z [--notes FILE] [--fresh-gate] [--gate-max-age DAYS]   (X.Y.Z also accepted)"
V=""; NOTES_IN=""; FRESH_GATE=""; GATE_MAX_AGE=7
while [ $# -gt 0 ]; do
  case "$1" in
    --notes) [ $# -ge 2 ] || { echo "--notes needs a file"; exit 1; }; NOTES_IN="$2"; shift 2 ;;
    --notes=*) NOTES_IN="${1#--notes=}"; shift ;;
    --fresh-gate) FRESH_GATE=1; shift ;;
    --gate-max-age) [ $# -ge 2 ] || { echo "--gate-max-age needs a number of days"; exit 1; }; GATE_MAX_AGE="$2"; shift 2 ;;
    --gate-max-age=*) GATE_MAX_AGE="${1#--gate-max-age=}"; shift ;;
    -h|--help) echo "$USAGE"; exit 0 ;;
    -*) echo "unknown option '$1' — $USAGE"; exit 1 ;;
    *) [ -z "$V" ] || { echo "$USAGE"; exit 1; }; V="$1"; shift ;;
  esac
done
[ -n "$V" ] || { echo "$USAGE"; exit 1; }
[[ "$GATE_MAX_AGE" =~ ^[0-9]+$ ]] || { echo "--gate-max-age must be a whole number of days (got '$GATE_MAX_AGE')"; exit 1; }
if [ -n "$NOTES_IN" ]; then
  case "$NOTES_IN" in /*) ;; *) NOTES_IN="$PWD0/$NOTES_IN" ;; esac
  [ -f "$NOTES_IN" ] && [ -r "$NOTES_IN" ] || { echo "--notes $NOTES_IN: not a readable file"; exit 1; }
fi
cd "$(dirname "$0")/.."
V="${V#v}"                                   # vX.Y.Z and X.Y.Z both accepted
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be vX.Y.Z or X.Y.Z (got 'v$V')"; exit 1; }
TAG="v$V"
PY="${PYTHON:-$PWD/venv/bin/python3}"

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "release from main only"; exit 1; }
# The pre-push PII gate below must run: a checkout without it (or
# without the scanner it calls) is refused before anything is changed.
for f in scripts/hooks/pre-push scripts/check-pii.sh; do
  [ -f "$f" ] || { echo "$f is missing from this checkout — the pre-push PII gate cannot run, so nothing is released"; exit 1; }
done
if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is not clean — commit or stash first:"; git status --porcelain | sed 's/^/    /'; exit 1
fi
git fetch --tags --quiet origin
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "$TAG already exists"; exit 1; }
git merge-base --is-ancestor origin/main HEAD || { echo "local main is behind origin/main — pull first"; exit 1; }
FETCH_TOML=packages/taxjson-fetch/pyproject.toml

# A PASS of the full gate on this exact tree (the clean tree being
# released, before the release's own edits), recorded by scripts/ci.sh.
PRE_TREE="$(git rev-parse 'HEAD^{tree}')"
REUSE=""
if [ -z "$FRESH_GATE" ]; then
  PYVER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
  REUSE="$(bash scripts/gate-record.sh find "$PRE_TREE" "$PYVER" "$GATE_MAX_AGE" 2>/dev/null || true)"
fi
# The release's own edits (below) and nothing else: every file but the
# four it edits as HEAD has it, nothing untracked, and each of the four
# equal to HEAD's once the lines it rewrites are masked — the version
# lines, the plugin's `taxjson>=` floor, the CHANGELOG heading (and the
# fresh Unreleased heading above it), the playbook's Fixed-in tag.
release_edits_only() {
  local line f st tagre vre
  vre="${V//./\\.}"; tagre="v$vre"
  while IFS= read -r line; do
    st="${line:0:2}"; f="${line:3}"
    case "$f" in CHANGELOG.md|docs/troubleshooting.md|pyproject.toml|"$FETCH_TOML") ;;
      *) echo "   $f is changed besides the release edits"; return 1 ;; esac
    [ "$st" = " M" ] || { echo "   $f: status '$st'"; return 1; }
  done < <(git status --porcelain --untracked-files=all)
  same() {   # same FILE PRE-SED POST-SED [POST-AWK]
    cmp -s <(git show "HEAD:$1" | sed -E "$2") \
           <(if [ -n "${4:-}" ]; then awk "$4" "$1"; else cat "$1"; fi | sed -E "$3") \
      || { echo "   $1 differs from HEAD beyond the release edits"; return 1; }
  }
  same pyproject.toml 's/^version = "[^"]*"/version = @V@/' "s/^version = \"$vre\"/version = @V@/" || return 1
  same "$FETCH_TOML" 's/^version = "[^"]*"/version = @V@/; s/"taxjson>=[^"]*"/"taxjson>=@V@"/' \
       "s/^version = \"$vre\"/version = @V@/; s/\"taxjson>=$vre\"/\"taxjson>=@V@\"/" || return 1
  same docs/troubleshooting.md 's/^- \*\*Fixed in:\*\* unreleased$/@FIXED@/' \
       "s/^- \\*\\*Fixed in:\\*\\* \`$tagre\`\$/@FIXED@/" || return 1
  # The new file is line 1, a blank, "## Unreleased", a blank, then the
  # old file from its line 2 with "## Unreleased…" renamed to the tag.
  same CHANGELOG.md 's/^## Unreleased.*/@HEAD@/' \
       "s/^## $tagre \([0-9]{4}-[0-9]{2}-[0-9]{2}\)\$/@HEAD@/" \
       'NR == 1 { print; next }
        NR <= 4 { if ((NR == 3) != ($0 == "## Unreleased") || (NR != 3 && $0 != "")) print "@NOT-THE-RELEASE-LAYOUT@"; next }
        { print }' || return 1
}

# CHANGELOG: promote Unreleased, or accept an existing heading.
if grep -q "^## $TAG " CHANGELOG.md; then
  :
elif grep -q "^## Unreleased" CHANGELOG.md; then
  sed -i "s/^## Unreleased.*/## $TAG ($(date +%F))/" CHANGELOG.md
  { echo "# Changelog"; echo; echo "## Unreleased"; echo; tail -n +2 CHANGELOG.md; } > CHANGELOG.md.new
  mv CHANGELOG.md.new CHANGELOG.md
else
  echo "CHANGELOG.md has neither '## Unreleased' nor '## $TAG'"; exit 1
fi
# The GitHub release's notes: the CHANGELOG section just named (or
# --notes FILE), scanned now — before the gate, the commit or the push —
# so a hit costs nothing; kept in a file of its own until the release
# exists (the command printed when gh is missing reads it).
NOTES="$(mktemp "${TMPDIR:-/tmp}/taxjson-notes-$TAG.XXXXXX")"
if [ -n "$NOTES_IN" ]; then
  cat "$NOTES_IN" > "$NOTES"
else
  awk -v h="## $TAG" 'index($0, h) == 1 && (length($0) == length(h) || substr($0, length(h) + 1, 1) == " ") { f = 1; next }
                      f && /^## / { exit }
                      f' CHANGELOG.md \
    | awk 'NF { b = 1 } b' | awk '{ l[NR] = $0 } NF { n = NR } END { for (i = 1; i <= n; i++) print l[i] }' > "$NOTES"
fi
[ -s "$NOTES" ] || { rm -f "$NOTES"; echo "release notes are empty (the CHANGELOG's '## $TAG' section, or --notes) — write them first"; exit 1; }
scan_notes() {   # fail closed: any hit or scanner error stops the release
  if ! scripts/check-pii.sh --message < "$NOTES"; then
    echo "release notes REFUSED by scripts/check-pii.sh --message ($1). GitHub releases are immutable: fix the notes (the CHANGELOG section or --notes FILE) and run again."
    return 1
  fi
}
scan_notes "nothing committed, tagged or pushed; CHANGELOG/pyproject edits left for you to inspect" || { rm -f "$NOTES"; exit 1; }
# The playbook's entries fixed since the last release name this one.
sed -i "s/^- \*\*Fixed in:\*\* unreleased\$/- **Fixed in:** \`$TAG\`/" docs/troubleshooting.md
sed -i "s/^version = \"[^\"]*\"/version = \"$V\"/" pyproject.toml
grep -q "^version = \"$V\"" pyproject.toml || { echo "pyproject version bump failed"; exit 1; }
# The broker-fetch plugin ships from the same tag, version for version,
# and needs at least this core (the `taxjson fetch` plugin interface).
sed -i -e "s/^version = \"[^\"]*\"/version = \"$V\"/" \
       -e "s/\"taxjson>=[^\"]*\"/\"taxjson>=$V\"/" "$FETCH_TOML"
grep -q "^version = \"$V\"" "$FETCH_TOML" && grep -q "\"taxjson>=$V\"" "$FETCH_TOML" \
  || { echo "taxjson-fetch version bump failed"; exit 1; }
"$PY" -m pip install -e . --quiet
[ "$("$PY" -m taxjson.bin.taxjson_run --version)" = "taxjson $V" ] || { echo "taxjson --version disagrees with $V"; exit 1; }

if [ -n "$REUSE" ] && ! release_edits_only; then
  echo "gate: not reusing $REUSE — the working tree differs from the gated tree by more than the release edits"
  REUSE=""
fi
if [ -n "$REUSE" ]; then
  echo "== gate: reusing the PASS of tree ${PRE_TREE:0:12} — $REUSE ($(sed -n 's/^mode=//p' "$REUSE") gate, Python $(sed -n 's/^python=//p' "$REUSE"), passed $(sed -n 's/^passed_utc=//p' "$REUSE")); --fresh-gate runs it again =="
  echo "== release-edit checks (the version, tag and date lines just written) =="
  scripts/ci.sh --release-edits || { echo "release-edit checks FAILED — release aborted (CHANGELOG/pyproject edits left for you to inspect; --fresh-gate runs the full gate)"; exit 1; }
else
  echo "== full gate =="
  scripts/ci.sh || { echo "gate FAILED — release aborted (CHANGELOG/pyproject edits left for you to inspect)"; exit 1; }
fi

git add CHANGELOG.md pyproject.toml "$FETCH_TOML" docs/troubleshooting.md
# An earlier aborted run may already have committed the bump: tag HEAD then.
git diff --cached --quiet || git commit -q -m "release $TAG"
git tag -a "$TAG" -m "taxjson $TAG"
# The pre-push PII gate (commit and tag messages, identities, ref names,
# binary files) runs here whether or not this clone has the hook
# installed (through bash: a lost executable bit does not skip it):
# ci.sh's tree scan never sees messages or identities (S025-06).
Z=0000000000000000000000000000000000000000
printf 'refs/heads/main %s refs/heads/main %s\nrefs/tags/%s %s refs/tags/%s %s\n' \
    "$(git rev-parse HEAD)" "$(git rev-parse origin/main)" \
    "$TAG" "$(git rev-parse "$TAG")" "$TAG" "$Z" \
  | bash scripts/hooks/pre-push origin "$(git remote get-url origin)" \
  || { echo "pre-push PII gate refused — nothing pushed (the commit and tag $TAG are local; fix, then delete the tag and re-run)"; exit 1; }
# One tag, by name — never `git push --tags` (a checkout can hold tags
# that must not be public; the pre-push hook refuses any other tag).
git push --quiet origin main "$TAG"
# The GitHub release. Its notes are published apart from the code (no
# push hook sees them) and an immutable release cannot be edited, so
# they are scanned again right before it is created.
# The GitHub repository: TAXJSON_SLUG, else origin's github.com address
# (gh's own guess, when neither names one).
SLUG="${TAXJSON_SLUG:-$(git remote get-url origin | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')}"
REPO_ARGS=(); [ -z "$SLUG" ] || REPO_ARGS=(--repo "$SLUG")
GH_CMD="scripts/check-pii.sh --message < $NOTES && gh release create $TAG --verify-tag -t \"taxjson $TAG\" -F $NOTES${SLUG:+ --repo $SLUG}"
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "pushed $TAG, but NO GitHub release yet: gh is $(command -v gh >/dev/null 2>&1 && echo 'not logged in (gh auth login)' || echo 'not installed')."
  echo "Finish it with (the notes are kept in $NOTES):"
  echo "    $GH_CMD"
  exit 1
fi
scan_notes "$TAG is pushed; the release is NOT created. Fix the notes, then: $GH_CMD" || exit 1
if ! gh release create "$TAG" --verify-tag -t "taxjson $TAG" -F "$NOTES" ${REPO_ARGS[@]+"${REPO_ARGS[@]}"}; then
  echo "pushed $TAG, but gh release create FAILED. Retry with (the notes are kept in $NOTES):"
  echo "    $GH_CMD"
  exit 1
fi
rm -f "$NOTES"
STABLE="$(sed -nE 's/.*"stable"[[:space:]]*:[[:space:]]*"(v[^"]+)".*/\1/p' channels.json 2>/dev/null | sed -n 1p || true)"
echo "released $TAG — it is now 'latest' (installs on --channel latest get it on their next run)."
echo "stable is still ${STABLE:-unset}; when $TAG has held up: scripts/promote.sh $TAG beta / scripts/promote.sh $TAG"
# The tag covers both distributions (the installer installs the plugin
# from the same checkout by default). To publish wheels as well:
#   python -m build && python -m build packages/taxjson-fetch
#   twine upload dist/taxjson-$V* packages/taxjson-fetch/dist/taxjson_fetch-$V*
