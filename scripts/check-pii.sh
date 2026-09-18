#!/usr/bin/env bash
# Personal-data / secret scan — the last thing between a commit and the
# public web. Runs in three places:
#   scripts/ci.sh                whole tree, tracked + untracked (every mode)
#   .git/hooks/pre-push          the diff and the commit messages a push
#                                would publish (--diff / --text on stdin)
#   scripts/check-pii.sh PATH…   ad hoc, on files or directories
#
# Two pattern sources:
#   1. Generic, below: broker account-id shapes, home paths, e-mail
#      addresses not on the allowlist, credential-looking strings.
#   2. Private denylist ~/.config/taxjson/pii-denylist — one POSIX
#      extended regex per line (also read by `taxjson redact`, so keep
#      to the common dialect: literals, [], (), |, +, *, {}), # comments —
#      holding YOUR real account numbers, names and addresses. It lives
#      outside every repository, so the strings it guards against are
#      never themselves committed. scripts/dev-setup.sh scaffolds it.
#
# Fails CLOSED: a scanner error (bad pattern, unreadable file, a text
# file that contains NUL bytes — UTF-16 exports read as "binary" and
# would otherwise be skipped) counts as a hit. File NAMES are scanned
# too (IB names downloads after the account id). Exit 1 with a masked
# file:line for every hit. Mark a genuine false positive with the word
# pii-ok on that line.
set -uo pipefail
PWD0="$PWD"
cd "$(dirname "$0")/.."
DENY="${TAXJSON_PII_DENYLIST:-$HOME/.config/taxjson/pii-denylist}"
ALLOW_EMAILS='noreply@anthropic\.com|users\.noreply\.github\.com|@example\.(com|org|net)|ckscijdtest@gmail\.com'
TEXT_EXT='csv|tt|txt|md|toml|json|py|sh|yml|yaml|html|css|cfg|ini|xml'

mode=tree
case "${1:-}" in --diff) mode=diff; shift ;; --text) mode=text; shift ;; esac
hits=0
fail() { hits=$((hits + 1)); echo "!! $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | head -20 | sed 's/^/   /'; }
# Masking never interprets the pattern (no delimiter/dialect issues): any
# 5+ char token that could be an id, name or address becomes <masked>.
mask() { sed -E 's/[A-Za-z0-9@._%+~-]*[0-9@][A-Za-z0-9@._%+~-]*/<masked>/g' | cut -c1-140; }

# ---- input -----------------------------------------------------------
NAMES=""     # newline-separated file names (tree mode), for the name scan
RAWF=""
if [ "$mode" != tree ]; then
  RAWF="$(mktemp)"; trap 'rm -f "$RAWF"' EXIT
  cat > "$RAWF"
  if ! LC_ALL=C tr -d '\0' < "$RAWF" | cmp -s - "$RAWF"; then
    fail "pushed content contains NUL bytes (a UTF-16 or binary text file): redact it or convert to UTF-8"
  fi
fi
if [ "$mode" = diff ]; then
  RAW="$(tr -d '\0' < "$RAWF")"
  INPUT="$(printf '%s\n' "$RAW" | grep -E '^\+' | grep -vE '^\+\+\+ ' | sed -E 's/^\+//')"
  NAMES="$(printf '%s\n' "$RAW" | sed -nE 's#^\+\+\+ b/##p')"
  scan() { printf '%s\n' "$INPUT" | grep -nE -e "$1" | sed -E 's/^([0-9]+):/added line \1: /'; }
elif [ "$mode" = text ]; then
  INPUT="$(tr -d '\0' < "$RAWF")"
  scan() { printf '%s\n' "$INPUT" | grep -nE -e "$1" | sed -E 's/^([0-9]+):/message line \1: /'; }
else
  if [ $# -gt 0 ]; then
    LIST=""
    for p in "$@"; do
      case "$p" in /*) ;; *) p="$PWD0/$p" ;; esac
      [ -e "$p" ] || { fail "path does not exist: $p"; continue; }
      LIST="$LIST$(find "$p" -type f -print0 | tr '\0' '\n')
"
    done
  else
    LIST="$(git ls-files -z --cached --others --exclude-standard | tr '\0' '\n')"
  fi
  NAMES="$LIST"
  scan() {   # NUL-delimited names so spaces/quotes cannot split or abort;
             # errors surface as "grep: …" lines (xargs' own status is
             # 123 for a plain no-match, so it cannot be used)
    printf '%s\n' "$LIST" | grep -v '^$' | tr '\n' '\0' \
      | xargs -0 -r grep -HInE -e "$1" --
  }
fi

report() {   # report LABEL PATTERN [EXCLUDE-REGEX]
  local out
  out="$(scan "$2" 2>&1 | grep -v 'pii-ok')"
  if printf '%s\n' "$out" | grep -q '^grep: '; then
    fail "scanner error while checking: $1" "$(printf '%s\n' "$out" | grep '^grep: ' | head -3)"
    out="$(printf '%s\n' "$out" | grep -v '^grep: ')"
  fi
  [ -n "${3:-}" ] && out="$(printf '%s\n' "$out" | grep -vE -e "$3")"
  [ -n "$out" ] || return 0
  fail "$1" "$(printf '%s\n' "$out" | mask)"
}

# ---- NUL bytes in text-like files (UTF-16 exports read as binary) -----
if [ "$mode" = tree ]; then
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "${f##*.}" in
      csv|tt|txt|md|toml|json|py|sh|yml|yaml|html|css|cfg|ini|xml)
        if LC_ALL=C tr -d '\0' < "$f" | cmp -s - "$f"; then :; else fail "text file contains NUL bytes (UTF-16? cannot be scanned): $f"; fi ;;
    esac
  done <<< "$NAMES"
fi

# ---- file names ------------------------------------------------------
if [ -n "$NAMES" ]; then
  n=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    b="${f##*/}"
    if printf '%s\n' "$b" | grep -oE 'U[0-9]{7,8}' | grep -qvE '^U1234567[0-9]?$|^U9990'; then n="$n$f
"; continue; fi
    if printf '%s\n' "$b" | grep -oE '[0-9]{8,}' | grep -qvE '^(19|20)[0-9]{6}$|^9990'; then n="$n$f
"; fi
  done <<< "$NAMES"
  [ -z "$n" ] || fail "file NAME carries an account-id shape (IB names downloads after the account)" "$(printf '%s' "$n" | mask)"
fi

# ---- generic patterns -------------------------------------------------
report "IB account id (U + 7-8 digits)"                  '\bU[0-9]{7,8}\b'                      'U1234567[0-9]?|U9990[0-9]+'
report "8-digit number next to the word account"        '[Aa]ccount[^0-9]{0,20}[0-9]{8}'       '9990[0-9]{4,}|1234567[89]'
report "home directory path"                            '/home/[a-z][a-z0-9_-]+|/Users/[A-Za-z][A-Za-z0-9_-]+' ''
report "e-mail address not on the allowlist"            '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' "$ALLOW_EMAILS"
report "credential-looking string"                      'ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|(api[_-]?key|secret|token|passw(or)?d)["'"'"' ]*[=:]["'"'"' ]*[A-Za-z0-9_\-]{20,}' ''

# ---- private denylist -------------------------------------------------
if [ -r "$DENY" ]; then
  while IFS= read -r pat || [ -n "$pat" ]; do
    pat="${pat%$'\r'}"
    case "$pat" in ''|'#'*) continue ;; esac
    printf '' | grep -qE -e "$pat" -- - 2>/dev/null; rc=$?
    if [ "$rc" -eq 2 ]; then fail "denylist line is not a valid extended regex (that guard is DISABLED until fixed): $(printf '%s' "$pat" | mask)"; continue; fi
    report "private denylist match" "$pat" ''
  done < "$DENY"
  denynote="denylist: $(printf '%s' "$DENY" | sed "s#^$HOME#~#")"
else
  denynote="no private denylist at ~/.config/taxjson/pii-denylist — generic patterns only (scripts/dev-setup.sh scaffolds one)"
fi

if [ "$hits" -gt 0 ]; then
  echo; echo "check-pii: $hits problem(s) — fix the content, or mark a genuine false positive with pii-ok on that line."
  exit 1
fi
echo "check-pii: clean ($denynote)"
