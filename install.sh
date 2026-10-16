#!/usr/bin/env bash
# taxjson one-line installer.
#
#   bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
#   bash -c "$(curl -fsSL https://taxjson.com/install.sh)" _ --without-fetch
#   bash -c "$(curl -fsSL https://taxjson.com/install.sh)" _ --channel beta
#
# What it does: checks git and Python 3.9+, clones the release on your
# CHANNEL into ~/.local/share/taxjson — or moves an existing install to
# it — builds a private virtualenv there with the [fx] extra (every
# dependency from the release's hash-pinned lock, requirements/), links
# the `taxjson` command (and its short name `tjs`) into ~/.local/bin.
# Broker auto-fetch (`taxjson fetch` for Questrade / IBKR Flex) is the
# separate taxjson-fetch package from the same release; it is installed
# into the same environment by default. --without-fetch (or
# TAXJSON_WITH_FETCH=0) leaves it out, and removes it from an install
# that has it. The opt-out is remembered in ~/.config/taxjson/fetch, so
# an upgrade keeps it out (off/0/no/false there; on/1/yes/true installs
# it); --with-fetch (or TAXJSON_WITH_FETCH=1) puts
# it back. Re-running is safe and is how you upgrade. Nothing touches
# your tax project folders.
#
# Channels (docs/releasing.md):
#   stable   the default: the release channels.json on main names
#   beta     the release channels.json names for testers
#   latest   the newest release tag (vX.Y.Z), as soon as it is tagged
#   dev      the main branch, unreleased
#   vX.Y.Z   exactly that release (a pin — also how you go back)
# Pick one with --channel NAME or TAXJSON_CHANNEL=NAME. The choice is
# remembered in ~/.config/taxjson/channel, so re-running the installer
# upgrades along the same channel. A channel never moves an install
# backwards; name a version to go back.
#
# Knobs (environment variables):
#   TAXJSON_DIR      install location        (default ~/.local/share/taxjson)
#   TAXJSON_BIN      where `taxjson` and `tjs` are linked (default ~/.local/bin)
#   TAXJSON_CHANNEL  stable | beta | latest | dev | vX.Y.Z  (as --channel)
#   TAXJSON_EXTRAS   extras to install       (default fx; "" for none; several:
#                    fx,xlsx) — each from its hash-pinned lock, requirements/
#                    <extra>.txt, like every dependency (SECURITY.md)
#   TAXJSON_REPO     git remote              (default the GitHub repo)
#   TAXJSON_WITH_FETCH  0 = leave taxjson-fetch out (as --without-fetch);
#                       1 = install it (the default; as --with-fetch)
#   TAXJSON_DRY_RUN  1 = say which release the channel resolves to and stop:
#                    an existing install only fetches tags; a new one is not
#                    created (the channel is resolved in a temporary clone)
set -euo pipefail

DIR="${TAXJSON_DIR:-$HOME/.local/share/taxjson}"
BIN="${TAXJSON_BIN:-$HOME/.local/bin}"
EXTRAS="${TAXJSON_EXTRAS-fx}"
REPO="${TAXJSON_REPO:-https://github.com/taxjson/taxjson.git}"
CHANNEL_FILE="$HOME/.config/taxjson/channel"
FETCH_FILE="$HOME/.config/taxjson/fetch"
USAGE="usage: install.sh [--channel stable|beta|latest|dev|vX.Y.Z] [--without-fetch]
  --channel        which release to install (default: the remembered one, else stable)
  --without-fetch  leave out taxjson-fetch, the Questrade / IBKR Flex auto-fetch
                   plugin (installed by default; remembered for upgrades)
  --with-fetch     install it again after a --without-fetch"
CHANNEL_ARG=""
FETCH_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --with-fetch) FETCH_ARG=1 ;;
    --without-fetch) FETCH_ARG=0 ;;
    --channel) [ $# -ge 2 ] || { printf '%s\n' "--channel needs a value" "$USAGE" >&2; exit 2; }
               CHANNEL_ARG="$2"; shift ;;
    --channel=*) CHANNEL_ARG="${1#--channel=}" ;;
    -h|--help) printf '%s\n' "$USAGE"; exit 0 ;;
    *) printf 'unknown option: %s\n%s\n' "$1" "$USAGE" >&2; exit 2 ;;
  esac
  shift
done
# --channel, else TAXJSON_CHANNEL, else what this machine was installed
# on, else stable.
CHANNEL="${CHANNEL_ARG:-${TAXJSON_CHANNEL:-}}"
if [ -z "$CHANNEL" ] && [ -f "$CHANNEL_FILE" ]; then CHANNEL="$(tr -d '[:space:]' < "$CHANNEL_FILE")"; fi
# A directory where the remembered channel belongs cannot be written:
# say so now and do not remember (a failed write after the checkout
# moved would stop the install half-way).
REMEMBER_CHANNEL="${TAXJSON_REMEMBER_CHANNEL:-1}"
if [ -d "$CHANNEL_FILE" ] && [ ! -L "$CHANNEL_FILE" ]; then
  printf 'warning: %s is a directory, not a file — the channel is not remembered (move it aside to fix)\n' "$CHANNEL_FILE" >&2
  REMEMBER_CHANNEL=0
fi
CHANNEL="${CHANNEL:-stable}"
case "$CHANNEL" in
  release) CHANNEL=latest ;;              # its name before channels existed
  [0-9]*) CHANNEL="v$CHANNEL" ;;          # 0.16.0 = v0.16.0
esac
case "$CHANNEL" in
  stable|beta|latest|dev) ;;
  *) [[ "$CHANNEL" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
       || { printf 'unknown channel %s — use stable, beta, latest, dev, or a release like v0.16.0\n' "'$CHANNEL'" >&2; exit 2; } ;;
esac
# taxjson-fetch: --with(out)-fetch, else TAXJSON_WITH_FETCH, else the
# opt-out this machine remembered, else installed.
FETCH_ASKED="${FETCH_ARG:-${TAXJSON_WITH_FETCH:-}}"
case "$FETCH_ASKED" in
  ""|0|1) ;;
  *) printf 'TAXJSON_WITH_FETCH=%s — use 1 (install taxjson-fetch, the default) or 0 (leave it out)\n' "$FETCH_ASKED" >&2; exit 2 ;;
esac
WITH_FETCH="$FETCH_ASKED"
# The remembered choice: off/0/no/false leaves it out, on/1/yes/true
# installs it (any case, spaces ignored); anything else is reported and
# the default (installed) applies. Checked here, before anything is
# cloned or checked out.
if [ -d "$FETCH_FILE" ] && [ ! -L "$FETCH_FILE" ]; then
  if [ "$FETCH_ASKED" = 0 ]; then
    printf '%s is a directory, not a file, so --without-fetch cannot be remembered — move it aside and re-run\n' "$FETCH_FILE" >&2
    exit 2
  fi
  printf 'warning: %s is a directory, not a file — ignored (taxjson-fetch is installed, the default)\n' "$FETCH_FILE" >&2
elif [ -z "$WITH_FETCH" ] && [ -f "$FETCH_FILE" ]; then
  case "$(tr -d '[:space:]' < "$FETCH_FILE" | tr '[:upper:]' '[:lower:]')" in
    off|0|no|false) WITH_FETCH=0 ;;
    on|1|yes|true) WITH_FETCH=1 ;;
    *) printf 'warning: %s should say off (leave taxjson-fetch out) or on — ignored (taxjson-fetch is installed, the default)\n' "$FETCH_FILE" >&2 ;;
  esac
fi
WITH_FETCH="${WITH_FETCH:-1}"
OS="$(uname -s)"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
# remember FILE VALUE: FILE (under ~/.config/taxjson, made private) holds
# VALUE, mode 600 — written to a temporary file and renamed into place,
# so a symlink planted at FILE is replaced, never written through.
remember() {
  local d tmp; d="$(dirname "$1")"
  mkdir -p "$(dirname "$d")"
  mkdir -p -m 700 "$d"
  tmp="$(mktemp "$d/.$(basename "$1").XXXXXX")"
  printf '%s\n' "$2" > "$tmp"
  chmod 600 "$tmp"
  if [ -L "$1" ]; then rm -f "$1"; fi
  mv -f "$tmp" "$1"
}

main() {
say "1/4 Prerequisites"
if ! have git; then
  if [ "$OS" = Darwin ]; then
    echo "Installing the Xcode command-line tools (provides git)…"
    xcode-select --install 2>/dev/null || true
    die "Re-run this installer once the tools have finished installing."
  fi
  die "git is required. Debian/Ubuntu: sudo apt-get install -y git"
fi
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
[ -n "$PY" ] || die "Python 3.9 or newer is required (3.11+ recommended). macOS: brew install python; Debian/Ubuntu: sudo apt-get install -y python3 python3-venv"
"$PY" -c 'import venv, ensurepip' 2>/dev/null \
  || die "$PY cannot create virtual environments. Debian/Ubuntu: sudo apt-get install -y python3-venv"
echo "   git $(git --version | awk '{print $3}')"
echo "   $("$PY" --version 2>&1) at $PY"

say "2/4 Source → $DIR"
DRY_RUN="${TAXJSON_DRY_RUN:-0}"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --tags --quiet origin
elif [ "$DRY_RUN" = 1 ]; then
  # A dry run changes nothing: resolving the channel needs the tags and
  # origin/main:channels.json, so it clones into a temporary folder that
  # is removed on exit, and never creates $DIR or its parent.
  DRY_TMP="$(mktemp -d)"
  trap 'rm -rf "$DRY_TMP"' EXIT
  echo "   (dry run: $DIR does not exist and is not created)"
  DIR="$DRY_TMP/src"
  git clone --quiet "$REPO" "$DIR"
else
  mkdir -p "$(dirname "$DIR")"
  git clone --quiet "$REPO" "$DIR"
fi
DIR="$(cd "$DIR" && pwd -P)"          # canonical: the symlink target must be absolute
# The newest release: exactly vX.Y.Z (a hand-pushed rc/four-part tag
# never ships). `sed -n 1p`, not `head -1`: head can close the pipe early
# and pipefail then fails the script.
newest() { git -C "$DIR" tag -l 'v[0-9]*' --sort=-v:refname | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sed -n 1p; }
# What channels.json on main names for a channel (a promote is a one-line
# commit there, never a new tag).
named() { { git -C "$DIR" show origin/main:channels.json 2>/dev/null || true; } \
            | sed -nE "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"(v[^\"]+)\".*/\\1/p" | sed -n 1p; }
# The newer of two vX.Y.Z.
vernewer() { printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1; }
CUR="$(git -C "$DIR" describe --tags --exact-match 2>/dev/null || true)"
case "$CHANNEL" in
  dev) TARGET=main ;;
  latest) TARGET="$(newest)" ;;
  stable|beta)
    TARGET="$(named "$CHANNEL")"
    if [ -z "$TARGET" ]; then
      echo "   (channels.json names no $CHANNEL release yet — using the newest)"
      TARGET="$(newest)"
    fi ;;
  *) git -C "$DIR" rev-parse -q --verify "refs/tags/$CHANNEL" >/dev/null || die "There is no release $CHANNEL."
     TARGET="$CHANNEL" ;;
esac
[ -n "$TARGET" ] || die "No release tags found in $REPO (--channel dev tracks main)."
if [ "$TARGET" != main ]; then
  git -C "$DIR" rev-parse -q --verify "refs/tags/$TARGET" >/dev/null \
    || die "channels.json names $TARGET for $CHANNEL, but $REPO has no such tag."
  # Only a release scripts/release.sh made: an annotated tag on main's
  # history (a lightweight tag, or one on a commit main never had, is
  # refused). Tags are not signed; this is not a signature check.
  [ "$(git -C "$DIR" cat-file -t "refs/tags/$TARGET")" = tag ] \
    || die "$TARGET is not an annotated release tag — refusing to install it."
  git -C "$DIR" merge-base --is-ancestor "refs/tags/$TARGET^{commit}" origin/main 2>/dev/null \
    || die "$TARGET is not on the main branch's history — refusing to install it."
  # A channel never takes an install backwards (this one may run ahead of
  # it after `taxjson deploy` or a pin); naming a version goes back.
  case "$CHANNEL" in v*) ;; *)
    if [[ "$CUR" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && [ "$CUR" != "$TARGET" ] \
        && [ "$(vernewer "$CUR" "$TARGET")" = "$CUR" ]; then
      echo "   on $CUR, newer than $CHANNEL ($TARGET) — staying (to go back: --channel $TARGET)"
      TARGET="$CUR"
    fi ;;
  esac
fi
if [ "$DRY_RUN" = 1 ]; then
  if [ "$TARGET" = main ]; then
    echo "   channel $CHANNEL → main  (dry run: nothing checked out or installed)"
  else
    echo "   channel $CHANNEL → release $TARGET  (dry run: nothing checked out or installed)"
  fi
  exit 0
fi
if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
  echo
  echo "   These tracked files differ from the checkout:"
  git -C "$DIR" status --porcelain --untracked-files=no | sed 's/^/     /'
  echo
  die "$DIR has local edits, so it will NOT be upgraded.
  Keep them:     cd $DIR && git stash
  Discard them:  cd $DIR && git checkout -- .
Then re-run this installer. (Your tax project folders are untouched either way.)"
fi
if [ "$TARGET" = main ]; then
  git -C "$DIR" checkout --quiet main
  git -C "$DIR" pull --ff-only --quiet origin main
  echo "   channel dev → main @ $(git -C "$DIR" rev-parse --short HEAD)"
else
  [ "$CUR" = "$TARGET" ] || git -C "$DIR" checkout --quiet "refs/tags/$TARGET"
  echo "   channel $CHANNEL → release $TARGET"
fi
# Remembered OUTSIDE the clone (a file inside would be a local change), so
# re-running upgrades along the same channel. A pinned version is
# remembered too. `taxjson deploy` keeps the remembered one
# (TAXJSON_REMEMBER_CHANNEL=0).
if [ "$REMEMBER_CHANNEL" != 0 ]; then
  remember "$CHANNEL_FILE" "$CHANNEL"
fi
# The fetch opt-out, remembered the same way (only when one was asked
# for: a plain re-run, or `taxjson deploy`, keeps what is there).
if [ "$FETCH_ASKED" = 0 ]; then
  remember "$FETCH_FILE" off
elif [ "$FETCH_ASKED" = 1 ] && { [ -f "$FETCH_FILE" ] || [ -L "$FETCH_FILE" ]; }; then
  rm -f "$FETCH_FILE"
fi

say "3/4 Python environment"
FRESH_VENV=0
[ -x "$DIR/venv/bin/python" ] || { "$PY" -m venv "$DIR/venv"; FRESH_VENV=1; }
VPY="$DIR/venv/bin/python"
# Dependencies come from the hash-pinned locks, never resolved by name:
# requirements/*.txt (scripts/lock_deps.py) name every package, its exact
# version and the sha256 of each of its files, and pip refuses anything
# else (--require-hashes; wheels only, so no build step fetches unpinned
# tools). taxjson and taxjson-fetch then come from this checkout with
# --no-deps. A release older than the locks uses main's.
LOCKS=""
if [ -f "$DIR/requirements/core.txt" ]; then
  LOCKS="$DIR/requirements"
elif git -C "$DIR" cat-file -e origin/main:requirements/core.txt 2>/dev/null; then
  LOCKS="$DIR/venv/.locks-from-main"
  rm -rf "$LOCKS"; mkdir -p "$LOCKS"
  for f in $(git -C "$DIR" ls-tree --name-only origin/main requirements/); do
    git -C "$DIR" show "origin/main:$f" > "$LOCKS/$(basename "$f")"
  done
  echo "   $TARGET predates the dependency locks — using main's (requirements/ on main)"
fi
LOCK_LOG="$DIR/venv/pip-install.log"
MISSING=""
# pip_locked LOCK...: install every package these locks pin, checked
# against their hashes. 0 = installed; 3 = no wheel for this platform or
# Python (MISSING names it). Anything else stops the install — above
# all a hash mismatch, which is never retried without the hashes.
pip_locked() {
  local f rc=0 args=() names=""
  for f in "$@"; do args+=(-r "$f"); names="$names requirements/${f##*/}"; done
  "$VPY" -m pip install --quiet --disable-pip-version-check --no-input \
    --require-hashes --only-binary :all: "${args[@]}" 2>"$LOCK_LOG" || rc=$?
  [ "$rc" = 0 ] && { rm -f "$LOCK_LOG"; return 0; }
  if grep -qiE 'do not match the hashes|require-hashes mode' "$LOCK_LOG"; then
    cat "$LOCK_LOG" >&2
    die "A package pip downloaded does not match the sha256 that taxjson's lock (${names# }) pins for it — refusing to install it.
  Nothing unpinned was installed instead. A proxy or mirror that rewrites
  downloads, a corrupted cache (python -m pip cache purge) or a tampered
  package on the index can cause this; see \"hash mismatch\" in
  docs/troubleshooting.md. Log: $LOCK_LOG"
  fi
  if grep -qE 'No matching distribution found|Could not find a version that satisfies' "$LOCK_LOG" \
      && ! grep -qiE 'retrying|connection|timed out|name resolution|ssl|proxy|max retries' "$LOCK_LOG"; then
    MISSING="$(sed -n 's/.*No matching distribution found for \([^ ]*\).*/\1/p' "$LOCK_LOG" | sed -n 1p)"
    return 3
  fi
  cat "$LOCK_LOG" >&2
  die "pip could not install the pinned dependencies (above). Check the network and re-run. Log: $LOCK_LOG"
}
if [ -n "$LOCKS" ]; then
  # pip itself and setuptools (taxjson's build backend) first, from the
  # lock: no unpinned pip self-upgrade, no build environment fetched.
  pip_locked "$LOCKS/build.txt" \
    || die "No wheel of ${MISSING:-pip / setuptools} for $("$VPY" --version 2>&1) on this platform in requirements/build.txt."
  EXTRA_LOCKS=()
  IFS=',' read -r -a WANTED <<< "$EXTRAS"
  for e in ${WANTED[@]+"${WANTED[@]}"}; do
    e="$(printf '%s' "$e" | tr -d '[:space:]')"
    [ -n "$e" ] || continue
    if ! [[ "$e" =~ ^[A-Za-z0-9._-]+$ ]] || [ ! -f "$LOCKS/$e.txt" ] \
        || [ "$e" = build ] || [ "$e" = core ]; then
      die "TAXJSON_EXTRAS: no extra [$e] in this release's dependency locks ($(cd "$LOCKS" && ls -- *.txt | sed 's/\.txt$//' | grep -vxE 'build|core' | tr '\n' ' '))"
    fi
    EXTRA_LOCKS+=("$LOCKS/$e.txt")
  done
  CORE_ONLY=1
  if [ ${#EXTRA_LOCKS[@]} -gt 0 ]; then
    rc=0
    pip_locked "$LOCKS/core.txt" "${EXTRA_LOCKS[@]}" || rc=$?
    if [ "$rc" = 0 ]; then
      CORE_ONLY=0
    else
      echo "   WARNING: extras [$EXTRAS] NOT installed: no prebuilt wheel of ${MISSING:-a dependency} for"
      echo "   WARNING: $("$VPY" --version 2>&1) on $(uname -s) $(uname -m) in taxjson's dependency lock (pip's log: $LOCK_LOG)."
      echo "   WARNING: installing the core package only — its dependencies are pinned too; nothing unpinned is installed."
      echo "   WARNING: without [fx]: no FX rates before 2007-05-01 or for currencies the Bank of Canada does not publish (the Yahoo Finance fallback), and none at all for a non-CAD base."
      echo "   WARNING: another Python (3.9-3.13) may have the wheels: re-run with it first on PATH."
    fi
  fi
  if [ "$CORE_ONLY" = 1 ]; then
    pip_locked "$LOCKS/core.txt" \
      || die "No wheel of ${MISSING:-a core dependency} for $("$VPY" --version 2>&1) on this platform in requirements/core.txt."
  fi
  "$VPY" -m pip install --quiet --disable-pip-version-check --no-deps --no-build-isolation -e "$DIR"
else
  # A fork or a test repository with no locks at all (taxjson's main has
  # had them since v0.28.4): say plainly that nothing is pinned.
  echo "   WARNING: no dependency locks (requirements/) in this release or on main — dependencies come from PyPI unpinned."
  if [ -n "$EXTRAS" ]; then
    "$VPY" -m pip install --quiet -e "$DIR[$EXTRAS]" \
      || { echo "   WARNING: extras [$EXTRAS] failed to install — falling back to the core package."; \
           "$VPY" -m pip install --quiet -e "$DIR"; }
  else
    "$VPY" -m pip install --quiet -e "$DIR"
  fi
fi
# The broker fetcher (Questrade REST API, IBKR Flex) is its own package
# (its own dependencies, an entry-point plugin) in the same repository
# and release tag; the core never holds broker clients.
if [ ! -d "$DIR/packages/taxjson-fetch" ]; then
  echo "   NOTE: this release predates the taxjson-fetch split — its \`taxjson fetch\` is built in."
elif [ "$WITH_FETCH" = 0 ]; then
  if "$DIR/venv/bin/python" -m pip show --quiet taxjson-fetch >/dev/null 2>&1; then
    "$DIR/venv/bin/python" -m pip uninstall --quiet --yes taxjson-fetch
    echo "   taxjson-fetch removed (--without-fetch; --with-fetch puts it back)"
  else
    echo "   taxjson-fetch left out (--without-fetch; --with-fetch adds it)"
  fi
else
  # An install from before v0.19.0 (core only) gains the plugin on its
  # next upgrade: say so once, with the way to keep it out.
  if [ "$FRESH_VENV" = 0 ] && [ -z "$FETCH_ASKED" ] \
      && ! "$DIR/venv/bin/python" -m pip show --quiet taxjson-fetch >/dev/null 2>&1; then
    echo "   adding taxjson-fetch (installed by default since v0.19.0; re-run with --without-fetch to keep it out)"
  fi
  # --no-deps: its only dependency is the core, installed just above
  # from this same checkout (anything else it needs is in core.txt) —
  # nothing is resolved by name from an index.
  if [ -n "$LOCKS" ]; then
    "$VPY" -m pip install --quiet --disable-pip-version-check --no-deps --no-build-isolation -e "$DIR/packages/taxjson-fetch"
  else
    "$VPY" -m pip install --quiet --no-deps -e "$DIR/packages/taxjson-fetch"
  fi
  echo "   taxjson-fetch installed: $("$DIR/venv/bin/taxjson" fetch --list | head -1)"
fi
echo "   $("$DIR/venv/bin/taxjson" --version)"

say "4/4 Command → $BIN/taxjson"
mkdir -p "$BIN"
# Replace a link only when it is ours (it points into $DIR, or nowhere
# yet): another program's `taxjson` / `tjs` — a file, or a symlink to
# something else — is left alone with a note.
ours() {   # ours LINK: true when LINK may be (re)pointed at this install
  [ -e "$1" ] || [ -L "$1" ] || return 0
  [ -L "$1" ] || return 1
  local t; t="$(readlink "$1")"
  case "$t" in "$DIR"/*) return 0 ;; esac
  t="$(readlink -f "$1" 2>/dev/null || true)"
  case "$t" in "$DIR"/*) return 0 ;; esac
  return 1
}
if ours "$BIN/taxjson"; then
  ln -sfn "$DIR/venv/bin/taxjson" "$BIN/taxjson"
else
  echo "   NOTE: $BIN/taxjson is another program's ($( [ -L "$BIN/taxjson" ] && echo "a link to $(readlink "$BIN/taxjson")" || echo "not a symlink")) — left alone."
  echo "         Run this install as $DIR/venv/bin/taxjson, move that one aside and re-run, or set TAXJSON_BIN."
fi
# `tjs`: the same program under a shorter name (releases that have it).
if [ -x "$DIR/venv/bin/tjs" ]; then
  if ours "$BIN/tjs"; then
    ln -sfn "$DIR/venv/bin/tjs" "$BIN/tjs"
    echo "   also linked $BIN/tjs (the same program, shorter)"
  else
    echo "   NOTE: $BIN/tjs is another program's — left alone; use \`taxjson\` (or $DIR/venv/bin/tjs)."
  fi
fi
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "   NOTE: $BIN is not on your PATH. Add this to your shell profile:"
     echo "         export PATH=\"$BIN:\$PATH\"" ;;
esac

cat <<DONE

✅ taxjson installed.

   Next: make the folder for your taxes and this year's project in it —
     mkdir -p ~/taxes && cd ~/taxes && tjs init --country canada && cd $(date +%Y)   (or --country usa)
   then drop your broker CSV exports into ~/taxes/inputs/<account>/ and run
     tjs run
   Try it first on made-up data: tjs init --demo ~/taxjson-demo
   Docs: https://taxjson.com  ·  https://github.com/taxjson/taxjson#readme
   Upgrade later by re-running this installer: it follows the $CHANNEL channel
   (--channel stable|beta|latest|dev|vX.Y.Z to switch).
DONE
if [ "$WITH_FETCH" = 0 ] && [ -d "$DIR/packages/taxjson-fetch" ]; then
  echo "   Questrade / IBKR auto-fetch is left out (--without-fetch, remembered); --with-fetch adds it."
fi
}
trap 'printf "\n\033[31m✗ install did not complete — re-running this installer is safe and resumes.\033[0m\n" >&2' ERR
main "$@"
