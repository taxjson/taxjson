#!/usr/bin/env bash
# Local CI — the same gates as .github/workflows/tests.yml, runnable
# anywhere (GitHub Actions is billing-gated on this private repo).
#
#   scripts/ci.sh            lint + full suite + fuzzers at CI depth
#   scripts/ci.sh --nightly  ...fuzzers at nightly depth (minutes)
#   scripts/ci.sh --mutation ...plus the mutation harness (an hour+;
#                            mutates engine files in place — run it
#                            alone, never alongside edits)
#   scripts/ci.sh --quick    lint + suite only
#
# Exit 0 only when every stage passes. A one-line result is appended
# to .ci/history.log (gitignored) so the last green commit is on record.
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-$PWD/venv/bin/python3}"
MODE=default
for a in "$@"; do case "$a" in
  --nightly) MODE=nightly ;; --mutation) MODE=mutation ;; --quick) MODE=quick ;;
  -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
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
if "$PY" -m ruff --version >/dev/null 2>&1; then
  stage lint "$PY" -m ruff check --select E9,F63,F7,F82 src/ tests/
else
  echo "== lint == skipped (pip install ruff into the venv to enable)"
fi

# 2. Release consistency, then the full unit suite.
stage consistency bash scripts/check-consistency.sh
stage suite "$PY" -m unittest discover -s tests -p "test_*.py" -q

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
