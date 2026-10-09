#!/usr/bin/env bash
# The extras-sensitive tests as a core install runs them (a stage of
# scripts/ci.sh; seconds).
#
#   scripts/ci_no_extras.sh [PYTHON [MODULE...]]
#
# GitHub's matrix jobs install taxjson WITHOUT the optional extras
# (`pip install -e .`); a developer's venv usually has them. A test that
# imported pandas unconditionally, or expected yfinance, passed the local
# gate and failed every hosted run. This stage runs the tests that touch
# the extras (EXTRAS_TESTS, or the MODULEs given) with every extra
# hidden:
#
#   * a sitecustomize on PYTHONPATH makes importing an INSTALLED extra
#     fail as on a core install (hide_extras; a test's own stand-in on
#     PYTHONPATH, e.g. a fake yfinance package, still imports);
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
MODULES="${*:-$EXTRAS_TESTS}"
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/taxjson-noextras.XXXXXX") || exit 2
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/home/.cache" "$ROOT/tmp" "$ROOT/hide"

# The extras' top-level import names ([project.optional-dependencies]
# all in pyproject.toml; tests/test_ci_hermetic.py keeps the two in step).
EXTRAS="yfinance pandas numpy openpyxl anthropic google.generativeai ib_insync"
hide_extras() {
  cat > "$ROOT/hide/sitecustomize.py" <<EOF
import os, site, sys, sysconfig
from importlib.machinery import PathFinder
_HIDDEN = "$EXTRAS".split()
_SITE = [os.path.realpath(p) for p in {
    sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"],
    *getattr(site, "getsitepackages", lambda: [])(),
    getattr(site, "getusersitepackages", lambda: "")()} if p]
class _HideExtras:
    """scripts/ci_no_extras.sh: the optional extras are not installed."""
    @staticmethod
    def find_spec(name, path=None, target=None):
        if not any(name == h or name.startswith(h + ".") for h in _HIDDEN):
            return None
        spec = PathFinder.find_spec(name, path)
        where = spec and (spec.origin if spec.has_location else
                          next(iter(spec.submodule_search_locations or ()), None))
        if where and any(os.path.realpath(where).startswith(s + os.sep)
                         for s in _SITE):
            raise ModuleNotFoundError(f"No module named {name!r} (an "
                                      f"optional extra, hidden)", name=name)
        return spec
sys.meta_path.insert(0, _HideExtras)
EOF
}
hide_extras

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

# `_hermetic` first: the synthetic HOME before any test module imports
# taxjson, as unittest discovery does it.
# shellcheck disable=SC2086
cd tests && ${NONET[@]+"${NONET[@]}"} env -i \
  PATH="$(dirname "$PY"):/usr/bin:/bin" LANG=C.UTF-8 CI=true \
  HOME="$ROOT/home" XDG_CACHE_HOME="$ROOT/home/.cache" TMPDIR="$ROOT/tmp" \
  PYTHONPATH="$ROOT/hide:$(dirname "$PWD")/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PY" -m unittest _hermetic $MODULES </dev/null
