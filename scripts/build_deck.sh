#!/usr/bin/env bash
# Render the deck: docs/deck/taxjson-deck.html -> taxjson-deck.pdf.
#
#   scripts/build_deck.sh
#
# 1. scripts/deck_commands.py --write: the commands slide from the help
#    page's groups (_COMMAND_GROUPS, the Maintainer group left out).
# 2. A throwaway virtualenv with WeasyPrint 70.0 from PyPI (the version
#    the deck has always been rendered with; it needs the system's Pango),
#    removed on exit.
# 3. The PDF, rendered from the repository root, and the HTML's sha256 in
#    docs/deck/taxjson-deck.pdf.sha256: tests/test_deck.py fails when the
#    HTML changes and the PDF is not rebuilt.
#
# Commit the HTML, the PDF and the .sha256 together. Look at every page
# first (pdftoppm -png -r 50 docs/deck/taxjson-deck.pdf /tmp/deck).
set -euo pipefail
cd "$(dirname "$0")/.."
HTML=docs/deck/taxjson-deck.html
PDF=docs/deck/taxjson-deck.pdf
PY="${PYTHON:-python3}"
"$PY" scripts/deck_commands.py --write
VENV=$(mktemp -d "${TMPDIR:-/tmp}/taxjson-deck.XXXXXX")
trap 'rm -rf "$VENV"' EXIT
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --disable-pip-version-check "weasyprint==70.0"
"$VENV/bin/weasyprint" "$HTML" "$PDF"
sha=$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$HTML")
printf '%s  %s\n' "$sha" "$(basename "$HTML")" > "$PDF.sha256"
echo "wrote $PDF ($(wc -c < "$PDF") bytes) and $PDF.sha256 ($sha)"
