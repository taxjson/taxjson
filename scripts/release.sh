#!/usr/bin/env bash
# Cut a release: scripts/release.sh vX.Y.Z   (X.Y.Z also accepted)
#
# main is the development line; a vX.Y.Z tag is a release. Tagging makes
# the release `latest` and nothing more: new installs take `stable`, which
# channels.json on main names and only scripts/promote.sh moves
# (docs/releasing.md). This script is the only way a tag should be made:
#   1. refuses on a dirty tree or off main;
#   2. turns the CHANGELOG's "## Unreleased" into "## vX.Y.Z (date)"
#      (or requires that heading to exist already);
#   3. bumps pyproject.toml and, in lockstep, the taxjson-fetch plugin's
#      packages/taxjson-fetch/pyproject.toml (its version and its
#      `taxjson>=` floor), reinstalls so `taxjson --version` agrees;
#   4. runs the FULL local gate (scripts/ci.sh, fuzzers included);
#   5. commits, tags (annotated), runs the pre-push PII gate itself, and
#      pushes main + the tag.
# It never touches channels.json. Roll a bad release back by promoting
# the previous good one (scripts/promote.sh vX.Y.Z — it asks before
# moving a channel backwards) — never by moving or deleting a published
# tag.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:?usage: scripts/release.sh vX.Y.Z (or X.Y.Z)}"
V="${V#v}"                                   # vX.Y.Z and X.Y.Z both accepted
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be vX.Y.Z or X.Y.Z (got '$1')"; exit 1; }
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

git add CHANGELOG.md pyproject.toml "$FETCH_TOML"
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
git push --quiet origin main "$TAG"
STABLE="$(sed -nE 's/.*"stable"[[:space:]]*:[[:space:]]*"(v[^"]+)".*/\1/p' channels.json 2>/dev/null | sed -n 1p || true)"
echo "released $TAG — it is now 'latest' (installs on --channel latest get it on their next run)."
echo "stable is still ${STABLE:-unset}; when $TAG has held up: scripts/promote.sh $TAG beta / scripts/promote.sh $TAG"
# The tag covers both distributions (the installer installs the plugin
# from the same checkout by default). To publish wheels as well:
#   python -m build && python -m build packages/taxjson-fetch
#   twine upload dist/taxjson-$V* packages/taxjson-fetch/dist/taxjson_fetch-$V*
