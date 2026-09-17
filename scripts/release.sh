#!/usr/bin/env bash
# Cut a release: scripts/release.sh X.Y.Z
#
# main is the development line; a vX.Y.Z tag is production — the curl
# installer checks out the newest tag, never main. This script is the
# only way a tag should be made:
#   1. refuses on a dirty tree or off main;
#   2. turns the CHANGELOG's "## Unreleased" into "## vX.Y.Z (date)"
#      (or requires that heading to exist already);
#   3. bumps pyproject.toml, reinstalls so `taxjson --version` agrees;
#   4. runs the FULL local gate (scripts/ci.sh, fuzzers included);
#   5. commits, tags (annotated), and pushes main + the tag.
# Roll back a bad release by tagging the previous good commit as the
# next patch version — never by moving or deleting a published tag.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:?usage: scripts/release.sh X.Y.Z}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be X.Y.Z (no leading v)"; exit 1; }
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
"$PY" -m pip install -e . --quiet
[ "$("$PY" -m taxjson.bin.taxjson_run --version)" = "taxjson $V" ] || { echo "taxjson --version disagrees with $V"; exit 1; }

echo "== full gate =="
scripts/ci.sh || { echo "gate FAILED — release aborted (CHANGELOG/pyproject edits left for you to inspect)"; exit 1; }

git add CHANGELOG.md pyproject.toml
git commit -q -m "release $TAG"
git tag -a "$TAG" -m "taxjson $TAG"
git push --quiet origin main "$TAG"
echo "released $TAG — installers pick it up on their next run"
