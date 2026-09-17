#!/usr/bin/env bash
# Release consistency (run by scripts/ci.sh in every mode):
#   - every shell script parses;
#   - when HEAD carries a release tag, pyproject's version matches it;
#   - the CHANGELOG has an Unreleased section or a heading for that tag.
set -euo pipefail
cd "$(dirname "$0")/.."
for f in install.sh setup.sh scripts/*.sh; do bash -n "$f" || { echo "syntax: $f"; exit 1; }; done
V="$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -1)"
[ -n "$V" ] || { echo "no version in pyproject.toml"; exit 1; }
TAG="$(git describe --tags --exact-match 2>/dev/null || true)"
if [ -n "$TAG" ] && [ "$TAG" != "v$V" ]; then
  echo "HEAD is tagged $TAG but pyproject.toml says $V"; exit 1
fi
grep -q "^## Unreleased\|^## v$V " CHANGELOG.md || { echo "CHANGELOG.md lacks '## Unreleased' or '## v$V'"; exit 1; }
echo "scripts parse; version $V${TAG:+ = $TAG}; changelog heading present"
