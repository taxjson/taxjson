#!/usr/bin/env bash
# taxjson one-line installer.
#
#   bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
#   bash -c "$(curl -fsSL https://taxjson.com/install.sh)" _ --with-fetch
#
# What it does: checks git and Python 3.9+, clones the LATEST RELEASE
# (newest vX.Y.Z tag) into ~/.local/share/taxjson — or fast-forwards an
# existing install to it — builds a private virtualenv there with the
# [fx] extra, and links the `taxjson` command (and its short name `tjs`)
# into ~/.local/bin. The core only: broker auto-fetch (`taxjson fetch` for
# Questrade / IBKR Flex) is the separate taxjson-fetch package, installed
# into the same environment with --with-fetch (or TAXJSON_WITH_FETCH=1).
# Re-running is safe and is how you upgrade. Nothing touches your tax
# project folders.
#
# Knobs (environment variables):
#   TAXJSON_DIR      install location        (default ~/.local/share/taxjson)
#   TAXJSON_BIN      where `taxjson` and `tjs` are linked (default ~/.local/bin)
#   TAXJSON_CHANNEL  release | dev           (dev tracks the main branch)
#   TAXJSON_EXTRAS   pip extras to install   (default fx; "" for none)
#   TAXJSON_REPO     git remote              (default the GitHub repo)
#   TAXJSON_WITH_FETCH  1 = also install taxjson-fetch (same as --with-fetch)
set -euo pipefail

DIR="${TAXJSON_DIR:-$HOME/.local/share/taxjson}"
BIN="${TAXJSON_BIN:-$HOME/.local/bin}"
CHANNEL="${TAXJSON_CHANNEL:-release}"
EXTRAS="${TAXJSON_EXTRAS-fx}"
REPO="${TAXJSON_REPO:-https://github.com/taxjson/taxjson.git}"
WITH_FETCH="${TAXJSON_WITH_FETCH:-0}"
for arg in "$@"; do
  case "$arg" in
    --with-fetch) WITH_FETCH=1 ;;
    -h|--help) echo "usage: install.sh [--with-fetch]   (also installs taxjson-fetch: Questrade / IBKR Flex auto-fetch)"; exit 0 ;;
    *) printf 'unknown option: %s (known: --with-fetch)\n' "$arg" >&2; exit 2 ;;
  esac
done
OS="$(uname -s)"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

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
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --tags --quiet origin
else
  mkdir -p "$(dirname "$DIR")"
  git clone --quiet "$REPO" "$DIR"
fi
DIR="$(cd "$DIR" && pwd -P)"          # canonical: the symlink target must be absolute
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
case "$CHANNEL" in
  release)
    # Releases are exactly vX.Y.Z: a hand-pushed rc/four-part tag never ships.
    TARGET="$(git -C "$DIR" tag -l 'v[0-9]*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
    [ -n "$TARGET" ] || die "No release tags found in $REPO (set TAXJSON_CHANNEL=dev to track main)."
    CUR="$(git -C "$DIR" describe --tags --exact-match 2>/dev/null || echo none)"
    [ "$CUR" = "$TARGET" ] || git -C "$DIR" checkout --quiet "refs/tags/$TARGET"
    echo "   release $TARGET"
    ;;
  dev)
    git -C "$DIR" checkout --quiet main
    git -C "$DIR" pull --ff-only --quiet origin main
    echo "   dev channel: main @ $(git -C "$DIR" rev-parse --short HEAD)"
    ;;
  *) die "TAXJSON_CHANNEL must be 'release' or 'dev' (got '$CHANNEL')." ;;
esac

say "3/4 Python environment"
[ -x "$DIR/venv/bin/python" ] || "$PY" -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install --quiet --upgrade pip
if [ -n "$EXTRAS" ]; then
  "$DIR/venv/bin/python" -m pip install --quiet -e "$DIR[$EXTRAS]" \
    || { echo "   WARNING: extras [$EXTRAS] failed to install — falling back to the core package."; \
         echo "   WARNING: without [fx]: no FX rates before 2017-01-03 (Yahoo fallback) and none at all for a non-CAD base."; \
         "$DIR/venv/bin/python" -m pip install --quiet -e "$DIR"; }
else
  "$DIR/venv/bin/python" -m pip install --quiet -e "$DIR"
fi
# An install that already has the fetcher keeps it on upgrade.
if [ "$WITH_FETCH" != 1 ] && "$DIR/venv/bin/python" -m pip show --quiet taxjson-fetch >/dev/null 2>&1; then
  WITH_FETCH=1
fi
if [ "$WITH_FETCH" = 1 ] && [ ! -d "$DIR/packages/taxjson-fetch" ]; then
  echo "   NOTE: this release predates the taxjson-fetch split — its \`taxjson fetch\` is built in."
elif [ "$WITH_FETCH" = 1 ]; then
  # The broker fetcher (Questrade REST API, IBKR Flex) lives in the same
  # repository and release tag; the core never holds broker clients.
  "$DIR/venv/bin/python" -m pip install --quiet -e "$DIR/packages/taxjson-fetch"
  echo "   taxjson-fetch installed: $("$DIR/venv/bin/taxjson" fetch --list | head -1)"
fi
echo "   $("$DIR/venv/bin/taxjson" --version)"

say "4/4 Command → $BIN/taxjson"
mkdir -p "$BIN"
if [ -e "$BIN/taxjson" ] && [ ! -L "$BIN/taxjson" ]; then
  die "$BIN/taxjson exists and is not a symlink (another install, e.g. pipx?). Move it aside or set TAXJSON_BIN."
fi
ln -sfn "$DIR/venv/bin/taxjson" "$BIN/taxjson"
# `tjs`: the same program under a shorter name (releases that have it).
if [ -x "$DIR/venv/bin/tjs" ]; then
  if [ -e "$BIN/tjs" ] && [ ! -L "$BIN/tjs" ]; then
    echo "   NOTE: $BIN/tjs exists and is not a symlink — left alone; use \`taxjson\`."
  else
    ln -sfn "$DIR/venv/bin/tjs" "$BIN/tjs"
    echo "   also linked $BIN/tjs (the same program, shorter)"
  fi
fi
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "   NOTE: $BIN is not on your PATH. Add this to your shell profile:"
     echo "         export PATH=\"$BIN:\$PATH\"" ;;
esac

cat <<DONE

✅ taxjson installed.

   Next: make a folder for a tax year and scaffold it —
     mkdir -p ~/taxes/$(date +%Y) && cd ~/taxes/$(date +%Y) && taxjson init --country canada   (or --country usa)
   then drop your broker CSV exports into inputs/<account>/ and run
     taxjson run
   Docs: https://taxjson.com  ·  https://github.com/taxjson/taxjson#readme
   Upgrade later by re-running this installer.
DONE
if [ "$WITH_FETCH" != 1 ]; then
  echo "   Questrade / IBKR auto-fetch: re-run with --with-fetch (installs taxjson-fetch)."
fi
}
trap 'printf "\n\033[31m✗ install did not complete — re-running this installer is safe and resumes.\033[0m\n" >&2' ERR
main "$@"
