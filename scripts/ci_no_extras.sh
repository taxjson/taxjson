#!/usr/bin/env bash
# The extras- and plugin-sensitive tests as a core install runs them (a
# stage of scripts/ci.sh; under a minute).
#
#   scripts/ci_no_extras.sh [PYTHON [MODULE...]]
#
# GitHub's matrix jobs install taxjson WITHOUT the optional extras
# (`pip install -e .`), and run the suite BEFORE they install the
# broker-fetch plugin (taxjson-fetch); a developer's venv usually has
# both (scripts/dev-setup.sh). A test that imported pandas
# unconditionally, or expected yfinance, or met a fetcher where CI has
# none (TJ-0267: "no fetcher is installed" came first there), passed the
# local gate and failed every hosted run. This stage runs the tests that
# touch the extras (EXTRAS_TESTS) and the plugin (PLUGIN_TESTS: every
# module that runs `fetch`, reads lib/fetchers.py or names taxjson_fetch,
# found by its text), or the MODULEs given, with every extra and the
# plugin hidden:
#
#   * a sitecustomize on PYTHONPATH (scripts/hide_optional.py) makes
#     importing an INSTALLED extra fail as on a core install (a test's
#     own stand-in on PYTHONPATH, e.g. a fake yfinance package, still
#     imports), and hides the plugin: no distribution named
#     taxjson-fetch is found (its dist-info, or an egg-info a build left
#     beside its source: so no fetcher entry point), and its module
#     imports only from a PYTHONPATH entry (a test that puts
#     packages/taxjson-fetch/src there, as CI's checkout allows), not
#     through the editable install;
#   * a clean environment (env -i): an empty HOME, no TAXJSON_* variable,
#     no proxy, no TAXJSON_WIDTH (the workflow sets none), stdin
#     /dev/null;
#   * no network where the platform can take it away (Linux user
#     namespaces; the loopback stays up), else it says so.
#
# The full suite needs none of this: every test process already runs in
# a synthetic HOME, offline (tests/_hermetic).
set -u
cd "$(dirname "$0")/.."
PY="${1:-${PYTHON:-$PWD/venv/bin/python3}}"
shift $(( $# > 0 ? 1 : 0 ))
# The modules that import an extra, check for one, or stand one in.
EXTRAS_TESTS="test_ci_hermetic test_fx_boc test_fix_m_engine test_xlsx_to_csv
  test_price_chain test_fix_generalize_e test_rbc_parser"
# The modules that touch the broker-fetch plugin, by what they contain:
# a `fetch` command (not a `git fetch` line), the fetcher discovery, the
# plugin's package or its refusal.
PLUGIN_RE="[\"']fetch[\"']|lib\\.fetchers|import fetchers|taxjson_fetch|no fetcher is installed"
PLUGIN_TESTS="$(cd tests && grep -HE "$PLUGIN_RE" test_*.py | grep -vE '\bgit\b' \
  | cut -d: -f1 | sort -u | sed 's/\.py$//' | tr '\n' ' ')"
# shellcheck disable=SC2086
MODULES="${*:-$(printf '%s\n' $EXTRAS_TESTS $PLUGIN_TESTS | awk '!seen[$0]++' | tr '\n' ' ')}"
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/taxjson-noextras.XXXXXX") || exit 2
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/home/.cache" "$ROOT/tmp" "$ROOT/hide"

# The extras' top-level import names ([project.optional-dependencies]
# all in pyproject.toml; tests/test_ci_hermetic.py keeps the two in step).
EXTRAS="yfinance pandas numpy openpyxl anthropic google.generativeai ib_insync"
# The broker-fetch plugin: its import name and its distribution's
# (normalized) name.
PLUGIN=taxjson_fetch
# The sitecustomize that hides them (scripts/hide_optional.py, shared
# with scripts/ci.sh's suite stage, which hides the plugin only).
"$PY" -I scripts/hide_optional.py "$ROOT/hide" --extras "$EXTRAS" \
  --plugin "$PLUGIN" || exit 2

# No network: a new network namespace (only loopback, brought up), the
# user mapped back to itself so file permissions behave as outside.
NONET=(unshare --net --map-root-user sh -c
       'u=$0 g=$1; shift; ip link set lo up && exec unshare --user --map-user="$u" --map-group="$g" "$@"'
       "$(id -u)" "$(id -g)")
if command -v unshare >/dev/null 2>&1 && command -v ip >/dev/null 2>&1 \
   && [ "$("${NONET[@]}" id -u 2>/dev/null)" = "$(id -u)" ]; then
  echo "   network: none (loopback only)"
else
  NONET=()
  echo "   network: NOT isolated here (no unprivileged user namespaces)"
fi

# In parallel, as CI's matrix runs the suite (scripts/run_tests_parallel.py:
# a process per module, `_hermetic` first in each, so the synthetic HOME
# is in place before a test module imports taxjson; its collection loads
# every test module, so one that cannot be imported without an extra or
# the plugin fails here as on CI). Its recorded times stay in this
# stage's own folder.
# shellcheck disable=SC2086
cd tests && ${NONET[@]+"${NONET[@]}"} env -i \
  PATH="$(dirname "$PY"):/usr/bin:/bin" LANG=C.UTF-8 CI=true \
  HOME="$ROOT/home" XDG_CACHE_HOME="$ROOT/home/.cache" TMPDIR="$ROOT/tmp" \
  PYTHONPATH="$ROOT/hide:$(dirname "$PWD")/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PY" ../scripts/run_tests_parallel.py --durations "$ROOT/durations.json" \
  ${TAXJSON_TEST_JOBS:+--jobs "$TAXJSON_TEST_JOBS"} $MODULES </dev/null
