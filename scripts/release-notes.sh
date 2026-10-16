#!/usr/bin/env bash
# Print a release's notes: the CHANGELOG's `## vX.Y.Z` section, without
# its heading and without leading or trailing blank lines. Empty output
# when there is no such section.
#
#   scripts/release-notes.sh vX.Y.Z [CHANGELOG-FILE]   # default CHANGELOG.md
#
# scripts/release.sh scans these notes before it tags; the public repo's
# release.yml creates the GitHub release from them when the publisher
# pushes the tag (docs/releasing.md).
set -euo pipefail
TAG="${1:-}"
[ -n "$TAG" ] || { echo "usage: scripts/release-notes.sh vX.Y.Z [CHANGELOG-FILE]" >&2; exit 2; }
FILE="${2:-CHANGELOG.md}"
[ -r "$FILE" ] || { echo "release-notes: cannot read $FILE" >&2; exit 2; }
awk -v h="## $TAG" 'index($0, h) == 1 && (length($0) == length(h) || substr($0, length(h) + 1, 1) == " ") { f = 1; next }
                    f && /^## / { exit }
                    f' "$FILE" \
  | awk 'NF { b = 1 } b' | awk '{ l[NR] = $0 } NF { n = NR } END { for (i = 1; i <= n; i++) print l[i] }'
