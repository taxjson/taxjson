#!/usr/bin/env bash
# Release consistency (run by scripts/ci.sh in every mode):
#   - every shell script parses;
#   - when HEAD carries a release tag, pyproject's version matches it;
#   - packages/taxjson-fetch's version matches the core's;
#   - the CHANGELOG has an Unreleased section or a heading for that tag;
#   - channels.json parses, and every release it names is a tag this
#     clone has (checked when the clone has any release tags).
set -euo pipefail
cd "$(dirname "$0")/.."
for f in install.sh setup.sh scripts/*.sh scripts/hooks/*; do bash -n "$f" || { echo "syntax: $f"; exit 1; }; done
V="$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -1)"
[ -n "$V" ] || { echo "no version in pyproject.toml"; exit 1; }
# The broker-fetch plugin ships in lockstep (scripts/release.sh bumps both).
FV="$(sed -n 's/^version = "\([^"]*\)"/\1/p' packages/taxjson-fetch/pyproject.toml | head -1)"
[ "$FV" = "$V" ] || { echo "packages/taxjson-fetch is version '$FV' but taxjson is $V (release.sh bumps both)"; exit 1; }
TAG="$(git describe --tags --exact-match 2>/dev/null || true)"
if [ -n "$TAG" ] && [ "$TAG" != "v$V" ]; then
  echo "HEAD is tagged $TAG but pyproject.toml says $V"; exit 1
fi
grep -q "^## Unreleased\|^## v$V " CHANGELOG.md || { echo "CHANGELOG.md lacks '## Unreleased' or '## v$V'"; exit 1; }
CH="$(PYTHONPATH="$PWD/src" "${PYTHON:-python3}" -c '
import sys
from taxjson.lib.channels import parse_channels, ChannelsError
try:
    c = parse_channels(open("channels.json", encoding="utf-8").read())
except (OSError, ChannelsError) as e:
    sys.exit(str(e))
print(" ".join(sorted(set(c.values()))))')" || { echo "channels.json: $CH"; exit 1; }
if [ -n "$(git tag -l 'v[0-9]*' 2>/dev/null | sed -n 1p)" ]; then
  for t in $CH; do
    git rev-parse -q --verify "refs/tags/$t" >/dev/null || { echo "channels.json names $t, but there is no such tag"; exit 1; }
  done
fi
echo "scripts parse; version $V${TAG:+ = $TAG} (taxjson-fetch too); changelog heading present; channels.json names ${CH:-nothing}"
