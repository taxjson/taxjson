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
#   4. runs the FULL local gate (scripts/ci.sh, fuzzers included);
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
#   scripts/release.sh vX.Y.Z [--notes FILE]
# It never touches channels.json. Roll a bad release back by promoting
# the previous good one (scripts/promote.sh vX.Y.Z — it asks before
# moving a channel backwards) — never by moving or deleting a published
# tag.
set -euo pipefail
PWD0="$PWD"
USAGE="usage: scripts/release.sh vX.Y.Z [--notes FILE]   (X.Y.Z also accepted)"
V=""; NOTES_IN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --notes) [ $# -ge 2 ] || { echo "--notes needs a file"; exit 1; }; NOTES_IN="$2"; shift 2 ;;
    --notes=*) NOTES_IN="${1#--notes=}"; shift ;;
    -h|--help) echo "$USAGE"; exit 0 ;;
    -*) echo "unknown option '$1' — $USAGE"; exit 1 ;;
    *) [ -z "$V" ] || { echo "$USAGE"; exit 1; }; V="$1"; shift ;;
  esac
done
[ -n "$V" ] || { echo "$USAGE"; exit 1; }
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
if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is not clean — commit or stash first:"; git status --porcelain | sed 's/^/    /'; exit 1
fi
git fetch --tags --quiet origin
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "$TAG already exists"; exit 1; }
git merge-base --is-ancestor origin/main HEAD || { echo "local main is behind origin/main — pull first"; exit 1; }

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
FETCH_TOML=packages/taxjson-fetch/pyproject.toml
sed -i -e "s/^version = \"[^\"]*\"/version = \"$V\"/" \
       -e "s/\"taxjson>=[^\"]*\"/\"taxjson>=$V\"/" "$FETCH_TOML"
grep -q "^version = \"$V\"" "$FETCH_TOML" && grep -q "\"taxjson>=$V\"" "$FETCH_TOML" \
  || { echo "taxjson-fetch version bump failed"; exit 1; }
"$PY" -m pip install -e . --quiet
[ "$("$PY" -m taxjson.bin.taxjson_run --version)" = "taxjson $V" ] || { echo "taxjson --version disagrees with $V"; exit 1; }

echo "== full gate =="
scripts/ci.sh || { echo "gate FAILED — release aborted (CHANGELOG/pyproject edits left for you to inspect)"; exit 1; }

git add CHANGELOG.md pyproject.toml "$FETCH_TOML" docs/troubleshooting.md
# An earlier aborted run may already have committed the bump: tag HEAD then.
git diff --cached --quiet || git commit -q -m "release $TAG"
git tag -a "$TAG" -m "taxjson $TAG"
# The pre-push PII gate (commit and tag messages, identities, ref names,
# binary files) runs here whether or not this clone has the hook
# installed: ci.sh's tree scan never sees messages or identities (S025-06).
Z=0000000000000000000000000000000000000000
printf 'refs/heads/main %s refs/heads/main %s\nrefs/tags/%s %s refs/tags/%s %s\n' \
    "$(git rev-parse HEAD)" "$(git rev-parse origin/main)" \
    "$TAG" "$(git rev-parse "$TAG")" "$TAG" "$Z" \
  | scripts/hooks/pre-push origin "$(git remote get-url origin)" \
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
