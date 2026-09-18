#!/usr/bin/env bash
# Personal-data / secret scan — the last thing between a commit and the
# public web. Runs in three places:
#   scripts/ci.sh                whole tracked tree (every mode)
#   .git/hooks/pre-push          only the lines the push would publish
#   scripts/check-pii.sh PATH…   ad hoc, on files or directories
#
# Two pattern sources:
#   1. Generic, below: broker account-id shapes, home paths, e-mail
#      addresses not on the allowlist, credential-looking strings.
#   2. Private denylist ~/.config/taxjson/pii-denylist — one extended
#      regex per line, # comments — holding YOUR real account numbers,
#      names and addresses. It lives outside every repository, so the
#      strings it guards against are never themselves committed.
#      scripts/dev-setup.sh scaffolds it.
#
# Exit 1 with a masked file:line for every hit. Mark a genuine false
# positive with the word pii-ok on that line.
set -uo pipefail
cd "$(dirname "$0")/.."
DENY="${TAXJSON_PII_DENYLIST:-$HOME/.config/taxjson/pii-denylist}"
ALLOW_EMAILS='noreply@anthropic\.com|users\.noreply\.github\.com|@example\.(com|org)|ckscijdtest@gmail\.com'

mode=tree
if [ "${1:-}" = "--diff" ]; then mode=diff; shift; fi
if [ "$mode" = diff ]; then
  INPUT="$(grep -E '^\+' | grep -vE '^\+\+\+ ' | sed -E 's/^\+//')"
  scan() { printf '%s\n' "$INPUT" | grep -nE "$1" | sed -E 's/^([0-9]+):/added line \1: /'; }
else
  # Tracked AND untracked (not ignored): a new file is scanned before it
  # is ever committed — git ls-files alone let an untracked fixture through.
  if [ $# -gt 0 ]; then FILES=$(find "$@" -type f); else FILES=$(git ls-files --cached --others --exclude-standard); fi
  scan() { printf '%s\n' "$FILES" | xargs -r grep -InE "$1" 2>/dev/null; }
fi

hits=0
report() {   # report LABEL PATTERN [EXCLUDE-REGEX]
  local out; out="$(scan "$2" | grep -v 'pii-ok')"
  [ -n "$3" ] && out="$(printf '%s\n' "$out" | grep -vE "$3")"
  [ -n "$out" ] || return 0
  hits=$((hits + 1))
  echo "!! $1"
  printf '%s\n' "$out" | head -20 | sed -E "s~($2)~<masked>~g" | cut -c1-140 | sed 's/^/   /'
}

report "IB account id (U + 7-8 digits)"                  '\bU[0-9]{7,8}\b'                      'U1234567[0-9]?|U9990[0-9]+'
report "8-digit number next to the word account"        '[Aa]ccount[^0-9]{0,20}[0-9]{8}'       '9990[0-9]{4,}|1234567[89]'
report "home directory path"                            '/home/[a-z][a-z0-9_-]+|/Users/[A-Za-z][A-Za-z0-9_-]+' ''
report "e-mail address not on the allowlist"            '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' "$ALLOW_EMAILS"
report "credential-looking string"                      'ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|(api[_-]?key|secret|token|passw(or)?d)["'"'"' ]*[=:]["'"'"' ]*[A-Za-z0-9_\-]{20,}' ''
if [ -r "$DENY" ]; then
  while IFS= read -r pat; do
    case "$pat" in ''|'#'*) continue ;; esac
    report "private denylist match" "$pat" ''
  done < "$DENY"
  denynote="denylist: $DENY"
else
  denynote="no private denylist at $DENY — generic patterns only (scripts/dev-setup.sh scaffolds one)"
fi

if [ "$hits" -gt 0 ]; then
  echo; echo "check-pii: $hits pattern(s) hit — fix the content, or mark a genuine false positive with pii-ok on that line."
  exit 1
fi
echo "check-pii: clean ($denynote)"
