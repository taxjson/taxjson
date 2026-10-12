#!/usr/bin/env bash
# Does .gitleaks.toml still report what it should? Its allowlist used to
# be a word list matched against the whole finding ("fake|example|
# synthetic|placeholder|dummy|made-up|…"), so a real secret whose setting
# NAME — or value — held one of those words was hidden. This scans a
# throwaway folder of invented files laid out like the repository and
# checks that every one is reported:
#
#   scripts/gitleaks-regression.sh <gitleaks binary> [config]
#
# The credential-looking values are random, made here on each run, so no
# value that looks like a secret is ever written into the repository. CI's
# "secrets" job runs it with the pinned binary — on a pull request the
# BASE commit's copy of this script, against the config the pull request
# proposes, so a change cannot weaken the test its config must pass;
# tests/test_fix_release_hardening.py runs it when a gitleaks binary is on
# hand (TAXJSON_GITLEAKS, or gitleaks on PATH).
# Exit 0: all as expected; 1: something is not reported (or a clean file
# is); 2: usage.
set -euo pipefail
BIN="${1:-}"
[ -n "$BIN" ] || { echo "usage: scripts/gitleaks-regression.sh <gitleaks binary> [config]" >&2; exit 2; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG_IN="${2:-$ROOT/.gitleaks.toml}"
[ -f "$CFG_IN" ] || { echo "gitleaks-regression: no config at $CFG_IN" >&2; exit 2; }
CONFIG="$(cd "$(dirname "$CFG_IN")" && pwd)/$(basename "$CFG_IN")"
case "$BIN" in */*) case "$BIN" in /*) ;; *) BIN="$(pwd)/$BIN" ;; esac ;; *) BIN="$(command -v "$BIN")" ;; esac
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
# A letter, then a digit, then a letter…: high-entropy, and no two
# letters side by side, so a random value never spells one of the generic
# rule's stopwords (a plain random string did, now and then, and went
# unreported).
rnd() {
  local n="${1:-32}" l d out=""
  l="$(LC_ALL=C tr -dc 'BCDFGHJKLMNPQRSTVWXZbcdfghjklmnpqrstvwxz' </dev/urandom | head -c "$n" || true)"
  d="$(LC_ALL=C tr -dc '2-9' </dev/urandom | head -c "$n" || true)"
  for ((i = 0; i < n / 2; i++)); do out="$out${l:i:1}${d:i:1}"; done
  printf '%s' "$out"
}
ghp() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 36 || true; }

mkdir -p "$WORK/src/taxjson/lib" "$WORK/tests" "$WORK/docs" "$WORK/scripts"
# Must be reported: ordinary values whatever their setting is called (each
# word the old allowlist matched), wherever they are — a random value in a
# test is not a fixture either — and values that merely CONTAIN one of the
# old allowlist's strings.
printf 'example_api_key = "%s"\n' "$(rnd)" > "$WORK/src/taxjson/lib/example_settings.py"
printf 'FAKE_SERVICE_TOKEN="%s"\n' "$(rnd)" > "$WORK/scripts/fake-env.sh"
printf 'synthetic_secret = "%s"\n' "$(rnd)" > "$WORK/tests/test_synthetic.py"
printf 'placeholder_password = "%s"\n' "$(rnd)" > "$WORK/tests/test_placeholder.py"
printf 'dummy_api_token = "%s"\n' "$(rnd)" > "$WORK/tests/test_dummy.py"
printf 'made_up_client_secret = "%s"\n' "$(rnd)" > "$WORK/tests/test_made_up.py"
printf '%s = "%s%s%s"\n' api_key "$(rnd 16)" 1234567890 "$(rnd 16)" > "$WORK/src/taxjson/lib/digits.py"
printf 'auth_token = "%saB3dE5%s"\n' "$(rnd 16)" "$(rnd 16)" > "$WORK/tests/test_unit_token.py"
printf 'Example token for the docs: ghp_%s\n' "$(ghp)" > "$WORK/docs/example.md"
WANT_FOUND="src/taxjson/lib/example_settings.py scripts/fake-env.sh tests/test_synthetic.py
tests/test_placeholder.py tests/test_dummy.py tests/test_made_up.py src/taxjson/lib/digits.py
tests/test_unit_token.py docs/example.md"
# Must NOT be reported: code that only names a secret.
printf 'api_key = os.environ["TAXJSON_API_KEY"]\n' > "$WORK/src/taxjson/lib/clean.py"
WANT_QUIET="src/taxjson/lib/clean.py"

( cd "$WORK" && "$BIN" dir . --config "$CONFIG" --redact --no-banner --exit-code 0 -l error \
    -f csv -r "$WORK/report.csv" )
found="$(tail -n +2 "$WORK/report.csv" | cut -d, -f3 | sed 's#^\./##' | sort -u)"
bad=0
for f in $WANT_FOUND; do grep -qx "$f" <<<"$found" || { echo "✗ not reported: $f"; bad=1; }; done
for f in $WANT_QUIET; do ! grep -qx "$f" <<<"$found" || { echo "✗ reported, but it holds no secret: $f"; bad=1; }; done
[ "$bad" = 0 ] && echo "✓ gitleaks config ($CONFIG): ordinary secrets reported whatever they are called; a clean file quiet"
exit "$bad"
