#!/usr/bin/env bash
# Local CI — the authoritative gate. .github/workflows/tests.yml runs the
# same lint, consistency, PII (generic patterns: no private denylist on a
# hosted runner) and suite stages for pull requests on the public repo.
#
#   scripts/ci.sh            lint + full suite (core and the packages/
#                            fetch plugin; an empty HOME, offline) + the
#                            extras-sensitive tests with no extras
#                            + fuzzers at CI depth
#   scripts/ci.sh --nightly  ...fuzzers at nightly depth (minutes)
#   scripts/ci.sh --mutation ...plus the mutation harness (an hour+;
#                            mutates engine files in place — run it
#                            alone, never alongside edits)
#   scripts/ci.sh --quick    lint + suite (and no-extras) only
#
# Exit 0 only when every stage passes. A one-line result is appended
# to .ci/history.log (gitignored) so the last green commit is on record.
set -u
cd "$(dirname "$0")/.."
# Never a TTY: CLI tests spawn `taxjson run` as subprocesses that inherit
# stdin, and a pending-election prompt would otherwise stop the suite to
# wait on the keyboard (and then record the default — a wrong exit code)
# when the gate is run from a terminal, e.g. by scripts/release.sh.
exec </dev/null
# A private temp root for the whole gate, removed on exit. A parser reads
# the broker CSVs beside a file (Kraken ledgers, Webull years), so a file
# another run or agent left in the shared temp dir must never sit beside
# this run's test files; the tests also keep each file in its own folder
# (tests/_tmpfiles.py). Defence in depth.
CI_TMP=$(mktemp -d "${TMPDIR:-/tmp}/taxjson-ci.XXXXXX") || exit 2
trap 'rm -rf "$CI_TMP"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export TMPDIR="$CI_TMP"
PY="${PYTHON:-$PWD/venv/bin/python3}"
MODE=default
for a in "$@"; do case "$a" in
  --nightly) MODE=nightly ;; --mutation) MODE=mutation ;; --quick) MODE=quick ;;
  -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
  *) echo "unknown flag: $a" >&2; exit 2 ;;
esac; done

mkdir -p .ci
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)
DIRTY=$([ -n "$(git status --porcelain 2>/dev/null)" ] && echo "+dirty" || echo "")
START=$(date +%s)
FAILED=()
stage() {   # stage NAME cmd...
  local name="$1"; shift
  printf '\n== %s ==\n' "$name"
  if "$@"; then printf '   %s: ok\n' "$name"; else printf '   %s: FAILED\n' "$name"; FAILED+=("$name"); fi
}

# 1. Lint, critical tier only (syntax errors, undefined names,
#    misused comparisons) — the class of defect audits kept finding.
#    A missing linter FAILS the stage (it used to print 'skipped' and
#    the gate stayed green; release.sh tags on that). The [dev] extra
#    installs ruff (S024-23).
if "$PY" -m ruff --version >/dev/null 2>&1; then
  stage lint "$PY" -m ruff check --select E9,F63,F7,F82 src/ tests/ packages/
else
  printf '\n== lint ==\n   lint: FAILED (ruff is not installed: pip install -e ".[dev]")\n'
  FAILED+=("lint")
fi

# 2. Release consistency, then the full unit suite.
stage consistency bash scripts/check-consistency.sh
# tax-logic is the spec: every rule id known, every test's country
# consistent, no rule without a test beyond the shrink-only baseline
# (scripts/check_tax_rules.py; tests/tax_rules/).
stage tax-rules "$PY" scripts/check_tax_rules.py
# The tree scan covers packages/ (every tracked and untracked file).
stage pii bash scripts/check-pii.sh
# An empty HOME and cache folder, as on a GitHub runner: the suite never
# reads the developer's rate cache (tests/_hermetic also gives every test
# process a synthetic HOME with made-up rates, offline). The suite used
# to pass here and fail on every hosted run because it did.
mkdir -p "$CI_TMP/home/.cache"
SUITE_ENV=(env HOME="$CI_TMP/home" XDG_CACHE_HOME="$CI_TMP/home/.cache"
           XDG_CONFIG_HOME="$CI_TMP/home/.config" TAXJSON_OFFLINE=1)
# Unwrapped (docs/output-style.md): a phrase a test looks for never
# depends on where a temp path made a message wrap. The style tests set
# their own width (tests/_style.py, tests/test_output_style.py).
stage suite "${SUITE_ENV[@]}" TAXJSON_WIDTH=0 "$PY" -m unittest discover -s tests -p "test_*.py" -q
# The tests that touch an optional extra, with every extra hidden, as
# GitHub's matrix jobs install taxjson (seconds; scripts/ci_no_extras.sh).
stage no-extras bash scripts/ci_no_extras.sh "$PY"
# The broker-fetch plugin (packages/taxjson-fetch): its own tests, run
# from the checkout whether or not it is pip-installed here (its
# tests/_support.py registers the entry point when it is not).
stage fetch-plugin "${SUITE_ENV[@]}" PYTHONPATH="$PWD/packages/taxjson-fetch/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PY" -m unittest discover -s packages/taxjson-fetch/tests -p "test_*.py" -q

# 3. Property fuzzers at depth. The suite already runs them at the
#    default (200/150/200); CI runs deeper, nightly deeper still.
fuzz_run() {   # the fuzz modules import as top-level names from tests/
  ( cd tests && env TAXJSON_FUZZ_BOOKS="$F" TAXJSON_TRANSFER_FUZZ_BOOKS="$T" \
      TAXJSON_STRADDLE_FUZZ_BOOKS="$S" \
      "$PY" -m unittest -q test_engine_invariants test_transfer_fuzz test_settle_straddle_fuzz )
}
if [ "$MODE" != quick ]; then
  if [ "$MODE" = default ]; then F=1000; T=500; S=400; else F=5000; T=3000; S=1600; fi
  stage "fuzz(conservation=$F,transfer=$T,straddle=$S)" fuzz_run
fi

# 4. Mutation harness (opt-in). Refuses on a dirty tree: it rewrites
#    engine files in place and an interrupted run leaves a mutant.
if [ "$MODE" = mutation ]; then
  if [ -n "$DIRTY" ]; then echo "== mutation == refused: working tree is dirty"; FAILED+=("mutation-precondition");
  else stage mutation "$PY" scripts/mutation_audit.py --yes; fi
fi

ELAPSED=$(( $(date +%s) - START ))
if [ ${#FAILED[@]} -eq 0 ]; then RESULT=PASS; else RESULT="FAIL(${FAILED[*]})"; fi
LINE="$(date -u +%Y-%m-%dT%H:%M:%SZ) $SHA$DIRTY $MODE $RESULT ${ELAPSED}s"
echo "$LINE" >> .ci/history.log
printf '\n%s\n' "$LINE"
[ "$RESULT" = PASS ]
