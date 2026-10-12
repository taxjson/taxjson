#!/usr/bin/env bash
# The gate's PASS records: which exact tree passed the full gate, when,
# in which mode, on which Python. scripts/ci.sh writes one; release.sh
# reads it to skip re-running a gate that already passed on the very
# tree it releases (docs/releasing.md, "Reusing a PASS").
#
#   scripts/gate-record.sh dir
#       the record folder: $TAXJSON_GATE_CACHE, else
#       ${XDG_CACHE_HOME:-$HOME/.cache}/taxjson-gate (owner-only, 0700)
#   scripts/gate-record.sh write TREE MODE PYVER ELAPSED
#       record a PASS of TREE (git rev-parse HEAD^{tree}). Refused — a
#       note, exit 0 — unless the working tree is clean (nothing
#       modified, nothing untracked) and still TREE, and MODE is a full
#       gate (default, nightly, mutation; never quick).
#   scripts/gate-record.sh find TREE PYVER MAXDAYS
#       print the newest full-gate PASS record of TREE on Python PYVER
#       no older than MAXDAYS days; exit 1 when there is none.
#
# A record is a file <tree>.<mode>.py<version>.pass of key=value lines.
# Outside the repository (a fresh clone of the same commit finds it) and
# never trusted for anything but "this tree passed": it holds no code.
set -u
cd "$(dirname "$0")/.."
FULL_MODES="default nightly mutation"

gate_dir() {
  printf '%s\n' "${TAXJSON_GATE_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/taxjson-gate}"
}

case "${1:-}" in
  dir) gate_dir ;;
  write)
    [ $# -eq 5 ] || { echo "usage: $0 write TREE MODE PYVER ELAPSED" >&2; exit 2; }
    TREE="$2" MODE="$3" PYVER="$4" ELAPSED="$5"
    case " $FULL_MODES " in *" $MODE "*) ;; *)
      echo "   gate record: not written (mode $MODE is not a full gate)"; exit 0 ;; esac
    [[ "$PYVER" =~ ^[0-9]+\.[0-9]+\.[0-9]+[a-z0-9]*$ ]] || {
      echo "   gate record: not written (Python version '$PYVER' unknown)"; exit 0; }
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
      echo "   gate record: not written (the working tree is not clean)"; exit 0
    fi
    NOW_TREE="$(git rev-parse 'HEAD^{tree}' 2>/dev/null)"
    if [ "$NOW_TREE" != "$TREE" ]; then
      echo "   gate record: not written (HEAD moved during the gate)"; exit 0
    fi
    D="$(gate_dir)"
    ( umask 077; mkdir -p "$D" ) && chmod 700 "$D" || {
      echo "   gate record: not written ($D cannot be made)"; exit 0; }
    F="$D/$TREE.$MODE.py$PYVER.pass"
    ( umask 077
      { echo "tree=$TREE"
        echo "commit=$(git rev-parse HEAD)"
        echo "mode=$MODE"
        echo "python=$PYVER"
        echo "passed_at=$(date +%s)"
        echo "passed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "elapsed=$ELAPSED"
      } > "$F.tmp.$$" && mv -f "$F.tmp.$$" "$F" ) || {
      rm -f "$F.tmp.$$"; echo "   gate record: not written ($F)"; exit 0; }
    echo "   gate record: $F"
    ;;
  find)
    [ $# -eq 4 ] || { echo "usage: $0 find TREE PYVER MAXDAYS" >&2; exit 2; }
    TREE="$2" PYVER="$3" MAXDAYS="$4"
    [[ "$TREE" =~ ^[0-9a-f]{40,64}$ ]] && [ -n "$PYVER" ] \
      && [[ "$MAXDAYS" =~ ^[0-9]+$ ]] || exit 1
    D="$(gate_dir)"
    NOW=$(date +%s)
    BEST="" BEST_AT=0
    for MODE in $FULL_MODES; do
      F="$D/$TREE.$MODE.py$PYVER.pass"
      [ -f "$F" ] && [ ! -L "$F" ] && [ -O "$F" ] || continue
      grep -qx "tree=$TREE" "$F" && grep -qx "mode=$MODE" "$F" \
        && grep -qx "python=$PYVER" "$F" || continue
      AT="$(sed -n 's/^passed_at=\([0-9][0-9]*\)$/\1/p' "$F" | head -1)"
      [ -n "$AT" ] || continue
      [ $(( NOW - AT )) -le $(( MAXDAYS * 86400 )) ] && [ "$AT" -le "$NOW" ] || continue
      if [ "$AT" -gt "$BEST_AT" ]; then BEST="$F"; BEST_AT="$AT"; fi
    done
    [ -n "$BEST" ] || exit 1
    printf '%s\n' "$BEST"
    ;;
  *) sed -n '2,24p' "$0"; exit 2 ;;
esac
