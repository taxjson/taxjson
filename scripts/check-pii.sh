#!/usr/bin/env bash
# Personal-data / secret scan — the last thing between a commit and the
# public web. Runs in three places:
#   scripts/ci.sh                whole tree, tracked + untracked (every mode)
#   .git/hooks/pre-push          the diff, the commit / tag messages and
#                                the author/committer/tagger identities a
#                                push would publish (--diff / --text /
#                                --identity on stdin)
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
# Fails CLOSED: a scanner error (bad pattern, unreadable file, a
# non-binary file of any extension that contains NUL bytes — UTF-16
# exports read as "binary" and would otherwise be skipped — or a
# denylist that was configured but is missing or unreadable) counts as
# a hit. Known binary types (BIN_EXT) are not read as text: in tree and
# ad hoc mode their embedded text is extracted instead (zip/OOXML/ODF
# members, gzip, PNG text chunks, printable runs — PDF /Author, DOCX
# dc:creator, EXIF) and scanned with every pattern; extraction needs
# python3 and fails closed without it. File NAMES are scanned too —
# every path component, not just the basename (IB names downloads and
# folders after the account id) — including the new path of a pure
# rename. In a .csv/.tsv, an 8-9 digit value in a column headed
# Account / Account # / Account Number is an account number. Denylist
# matching is case-insensitive, and a run of 4+ literal digits in a
# denylist pattern also matches with spaces or dashes between the digits
# (1234 5678, 1234-5678). --identity reads "Name <email>" lines and
# applies the denylist and the e-mail allowlist. Exit 1 with a masked
# file:line for every hit. Mark a genuine false positive with a
# COMMENT marker on that line — `# pii-ok`, `// pii-ok`, `<!-- pii-ok`
# or `pii-ok:` — the bare word inside other text does not count.
set -uo pipefail
PWD0="$PWD"
cd "$(dirname "$0")/.."
DENY="${TAXJSON_PII_DENYLIST:-$HOME/.config/taxjson/pii-denylist}"
ALLOW_EMAILS='noreply@anthropic\.com|users\.noreply\.github\.com|@example\.(com|org|net)|ckscijdtest@gmail\.com'
# Real binaries: the only files the scan may skip. Everything else is
# scanned as text (grep -a, so a stray Latin-1 byte cannot make grep call
# the file "binary" and skip it), and a NUL byte in it — a UTF-16 export
# whatever its extension (.tsv, .log, .ofx) — fails closed.
BIN_EXT='pdf|png|jpe?g|gif|ico|webp|bmp|tiff?|svgz|xlsx|xlsm|xls|docx|doc|pptx|odt|ods|zip|gz|tgz|bz2|xz|7z|whl|woff2?|ttf|otf|eot|mp3|mp4|mov|pyc'
is_bin() { printf '%s\n' "$1" | grep -qiE "\.($BIN_EXT)\$"; }

mode=tree
case "${1:-}" in
  --diff) mode=diff; shift ;; --text) mode=text; shift ;;
  --identity) mode=identity; shift ;;
esac
hits=0
CI=""        # "-i" while scanning the (case-insensitive) denylist
# A genuine false positive carries a comment marker, not just the word.
PII_OK='(#|//|<!--|/\*)[[:space:]]*pii-ok([^A-Za-z0-9_-]|$)|pii-ok:'

# Denylist patterns: every run of 4+ literal digits also matches with a
# space or dash between digits. Escapes, bracket expressions and {m,n}
# are copied verbatim, so the result is still a valid ERE.
loosen() {
  LC_ALL=C awk '
  function flush(   k) {
    if (length(run) >= 4) {
      out = out substr(run, 1, 1)
      for (k = 2; k <= length(run); k++) out = out "[ -]?" substr(run, k, 1)
    } else out = out run
    run = ""
  }
  {
    s = $0; out = ""; run = ""; n = length(s); i = 1
    while (i <= n) {
      c = substr(s, i, 1)
      if (c ~ /[0-9]/) { run = run c; i++; continue }
      flush()
      if (c == "\\") { out = out substr(s, i, 2); i += 2; continue }
      if (c == "[") {
        j = i + 1
        if (substr(s, j, 1) == "^") j++
        if (substr(s, j, 1) == "]") j++
        while (j <= n) {
          if (substr(s, j, 2) == "[:") {
            e = index(substr(s, j + 2), ":]")
            if (e) { j = j + 2 + e + 1; continue }
          }
          if (substr(s, j, 1) == "]") break
          j++
        }
        out = out substr(s, i, j - i + 1); i = j + 1; continue
      }
      if (c == "{") {
        j = index(substr(s, i), "}")
        if (j) { out = out substr(s, i, j); i += j; continue }
      }
      out = out c; i++
    }
    flush(); print out
  }'
}

# YYYYMMDD that is a real calendar date (only those are exempt in names).
is_date8() {
  printf '%s\n' "$1" | awk '{
    y = substr($0,1,4)+0; m = substr($0,5,2)+0; d = substr($0,7,2)+0
    if (m < 1 || m > 12 || d < 1) exit 1
    split("31 28 31 30 31 30 31 31 30 31 30 31", L, " ")
    if (m == 2 && (y % 4 == 0 && (y % 100 != 0 || y % 400 == 0))) L[2] = 29
    exit (d <= L[m]) ? 0 : 1 }'
}
fail() { hits=$((hits + 1)); echo "!! $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | head -20 | sed 's/^/   /'; }
# Masking never interprets the pattern (no delimiter/dialect issues): any
# 5+ char token that could be an id, name or address becomes <masked>.
# Non-ASCII bytes print as '?' (a Latin-1 hit line must not garble the report).
mask() { LC_ALL=C sed -E 's/[^ -~]/?/g' | sed -E 's/[A-Za-z0-9@._%+~-]*[0-9@][A-Za-z0-9@._%+~-]*/<masked>/g' | cut -c1-140; }

SEP=$'\001'  # path / line separator in tree-mode hit lines (a path may hold ':')
XT=""        # scratch dir holding the text extracted from binary files
# Text inside a binary file, one string per line, written to argv[1]/<path>
# for every path on stdin: zip members (OOXML/ODF: docProps, cells) and
# their names, gzip, PNG tEXt/zTXt/iTXt, PDF streams (inflated), and runs
# of printable ASCII / UTF-16 in the raw bytes. Bounded in size.
EXTRACT_PY='
import gzip, io, os, re, sys, zipfile, zlib
LIM = 64 << 20
RUN = re.compile(rb"[\x20-\x7e\t]{4,}")
U16LE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
U16BE = re.compile(rb"(?:\x00[\x20-\x7e]){4,}")
def inflate(b):
    try:
        d = zlib.decompressobj()
        return d.decompress(b, LIM)
    except Exception:
        return b""
def texts(data, depth=0):
    out = [m.decode("ascii") for m in RUN.findall(data)]
    out += [m.decode("utf-16-le") for m in U16LE.findall(data)]
    out += [m.decode("utf-16-be") for m in U16BE.findall(data)]
    if depth > 2:
        return out
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            total = 0
            for info in z.infolist():
                out.append("member: " + info.filename)
                total += info.file_size
                if total > LIM:
                    raise ValueError("zip expands past the scan limit")
                out += texts(z.read(info), depth + 1)
        except zipfile.BadZipFile:
            pass
    elif data[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
                out += texts(g.read(LIM), depth + 1)
        except OSError:
            pass
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        i = 8
        while i + 8 <= len(data):
            n = int.from_bytes(data[i:i + 4], "big")
            kind, body = data[i + 4:i + 8], data[i + 8:i + 8 + n]
            if kind == b"zTXt" and b"\x00" in body:
                out += texts(inflate(body.split(b"\x00", 1)[1][1:]), depth + 1)
            elif kind == b"iTXt" and b"\x00" in body:
                k, rest = body.split(b"\x00", 1)
                if rest[:1] == b"\x01":
                    out += texts(inflate(rest[2:].split(b"\x00", 2)[-1]), depth + 1)
            i += 12 + n
    elif data[:5] == b"%PDF-":
        for m in re.finditer(rb"stream\r?\n", data):
            out += texts(inflate(data[m.end():m.end() + LIM]), depth + 1)
    return out
root = sys.argv[1]
for path in sys.stdin.read().splitlines():
    if not path:
        continue
    with open(path, "rb") as f:
        data = f.read(LIM + 1)
    if len(data) > LIM:
        raise SystemExit("binary file too large to scan: " + path)
    dst = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        for t in texts(data):
            f.write(t.replace("\x00", "") + "\n")
'

# ---- input -----------------------------------------------------------
NAMES=""     # newline-separated file names (tree mode), for the name scan
RAWF=""
if [ "$mode" != tree ]; then
  RAWF="$(mktemp)"; trap 'rm -f "$RAWF"' EXIT
  cat > "$RAWF"
  if [ "$mode" = text ] && ! LC_ALL=C tr -d '\0' < "$RAWF" | cmp -s - "$RAWF"; then
    fail "commit message contains NUL bytes"
  fi
fi
if [ "$mode" = diff ]; then
  RAW="$(tr -d '\0' < "$RAWF")"
  INPUT="$(printf '%s\n' "$RAW" | grep -E '^\+' | grep -vE '^\+\+\+ ' | sed -E 's/^\+//')"
  # Destination paths: +++ lines, plus the headers a PURE rename/copy
  # or an empty new file carries instead (no +++ line at all).
  NAMES="$( { printf '%s\n' "$RAW" | sed -nE 's#^\+\+\+ b/##p; s#^(rename|copy) to ##p'
              printf '%s\n' "$RAW" | sed -nE 's#^diff --git a/.* b/(.*)$#\1#p'; } | sort -u)"
  # git shows binaries as "Binary files … differ" and cannot scan them.
  # A real binary (pdf, png) is fine; a TEXT-extension file that git
  # calls binary is a UTF-16 (or NUL-stuffed) export and must not slip
  # through unscanned.
  b="$(printf '%s\n' "$RAW" | sed -nE 's#^Binary files .* and (b/)?(.*) differ$#\2#p' | grep -viE "\.($BIN_EXT)$" || true)"
  [ -z "$b" ] || fail "non-binary file(s) git treats as binary (UTF-16?) — cannot be scanned; convert to UTF-8" "$(printf '%s\n' "$b" | mask)"
  scan() { printf '%s\n' "$INPUT" | grep -anE $CI -e "$1" | sed -E 's/^([0-9]+):/added line \1: /'; }
elif [ "$mode" = text ] || [ "$mode" = identity ]; then
  INPUT="$(tr -d '\0' < "$RAWF")"
  scan() { printf '%s\n' "$INPUT" | grep -anE $CI -e "$1" | sed -E "s/^([0-9]+):/$mode line \\1: /"; }
else
  if [ $# -gt 0 ]; then
    # Ad hoc: LIST holds the absolute paths to read; NAMES holds each path
    # relative to the directory that contains the argument, so the name
    # scan sees what was asked about (a/b/file), never the $HOME prefix
    # above it (a home directory named after its owner is not a leak).
    LIST=""
    for p in "$@"; do
      case "$p" in /*) ;; *) p="$PWD0/$p" ;; esac
      [ -e "$p" ] || { fail "path does not exist: $p"; continue; }
      while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do p="${p%/}"; done
      case "$p" in */.) p="${p%/.}" ;; esac
      base="${p%/*}/"
      found="$(find "$p" -type f -print0 | tr '\0' '\n')"
      LIST="$LIST$found
"
      NAMES="$NAMES$(printf '%s\n' "$found" | while IFS= read -r f; do [ -n "$f" ] && printf '%s\n' "${f#"$base"}"; done)
"
    done
  else
    LIST="$(git ls-files -z --cached --others --exclude-standard | tr '\0' '\n')"
    NAMES="$LIST"
  fi
  SCANLIST="$(printf '%s\n' "$LIST" | while IFS= read -r f; do [ -n "$f" ] && ! is_bin "$f" && printf '%s\n' "$f"; done)"
  BINLIST="$(printf '%s\n' "$LIST" | while IFS= read -r f; do [ -n "$f" ] && [ -f "$f" ] && is_bin "$f" && printf '%s\n' "$f"; done)"
  if [ -n "$BINLIST" ]; then
    # Binary documents carry text the content scan never saw: PDF /Author,
    # DOCX dc:creator, PNG tEXt, a spreadsheet's cells. Extract it into a
    # scratch mirror of the paths and scan that with every pattern.
    XT="$(mktemp -d)"; trap 'rm -rf "$XT" "${RAWF:-}"' EXIT
    if PYB="$(command -v "${PYTHON:-python3}" 2>/dev/null)" && [ -n "$PYB" ]; then
      xerr="$(printf '%s\n' "$BINLIST" | "$PYB" -c "$EXTRACT_PY" "$XT" 2>&1)" \
        || fail "could not extract the text of binary file(s) to scan them" "$(printf '%s\n' "$xerr" | mask)"
      SCANLIST="$SCANLIST
$(cd "$XT" && find . -type f | sed "s#^\./#$XT/#")"
    else
      fail "binary file(s) present but no python3 to extract their text — cannot be scanned" "$(printf '%s\n' "$BINLIST" | mask)"
    fi
  fi
  scan() {   # NUL-delimited names so spaces/quotes cannot split or abort;
             # errors surface as "grep: …" lines (xargs' own status is
             # 123 for a plain no-match, so it cannot be used). -a: every
             # non-binary file is read as text (the NUL guard below fails
             # the ones that cannot be).
    # -Z: a NUL after the path (turned into $SEP), so a ':' inside a
    # path cannot be read as the end of it and unmask a denylist hit.
    printf '%s\n' "$SCANLIST" | grep -v '^$' | tr '\n' '\0' \
      | xargs -0 -r grep -HanZE $CI -e "$1" -- | tr '\0' "$SEP" \
      | sed "s#^${XT:-/nonexistent}/\(.*\)$SEP#\1 (embedded text)$SEP#"
  }
fi

# Keep a hit line only while PATTERN still matches after every EXEMPT
# token is removed from it: the exemption covers the synthetic token, not
# a real id (or its file path) that happens to share the line.
still_hit() {   # still_hit PATTERN EXEMPT-REGEX  (lines on stdin)
  local line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '%s\n' "$line" | sed -E "s#$2##g" | grep -aqE $CI -e "$1" && printf '%s\n' "$line"
  done
  return 0
}

# Luhn-valid 3-3-3 digit groups (a Social Insurance Number's shape).
sin_filter() {
  LC_ALL=C awk '
  function luhn(d,   i, t, x) {
    t = 0
    for (i = 9; i >= 1; i--) {
      x = substr(d, i, 1) + 0
      if ((9 - i) % 2 == 1) { x *= 2; if (x > 9) x -= 9 }
      t += x
    }
    return t % 10 == 0
  }
  {
    s = $0; keep = 0
    while (match(s, /[0-9][0-9][0-9][ -][0-9][0-9][0-9][ -][0-9][0-9][0-9]/)) {
      pre = (RSTART > 1) ? substr(s, RSTART - 1, 1) : ""
      post = substr(s, RSTART + RLENGTH, 1)
      d = substr(s, RSTART, RLENGTH); gsub(/[ -]/, "", d)
      if (pre !~ /[0-9]/ && post !~ /[0-9]/ && luhn(d)) { keep = 1; break }
      s = substr(s, RSTART + 1)
    }
    if (keep) print
  }'
}

# The SIN shape on real text only: a binary's extracted strings (PDF
# glyph-width arrays: '278 333 474') pass the check digit by chance.
sin_text() { grep -av "(embedded text)$SEP" | sin_filter; }

report() {   # report LABEL PATTERN [EXEMPT-REGEX [FILTER]]
  local out
  # -a throughout: a hit line with a stray non-UTF-8 byte must stay a
  # line, not collapse into grep's "binary file matches".
  out="$(scan "$2" 2>&1 | grep -avE -e "$PII_OK")"
  if printf '%s\n' "$out" | grep -aq '^grep: '; then
    fail "scanner error while checking: $1" "$(printf '%s\n' "$out" | grep -a '^grep: ' | head -3)"
    out="$(printf '%s\n' "$out" | grep -av '^grep: ')"
  fi
  [ -n "${3:-}" ] && out="$(printf '%s\n' "$out" | still_hit "$2" "$3")"
  [ -n "${4:-}" ] && out="$(printf '%s\n' "$out" | "$4")"
  [ -n "$out" ] || return 0
  # A denylist hit IS the private string (names have no digit for mask()
  # to catch): show where, never what.
  [ -n "$CI" ] && out="$(printf '%s\n' "$out" | sed -E \
      -e "s/^([^$SEP]*)$SEP([0-9]+):.*\$/\1:\2: <content hidden>/" \
      -e 's/^((added|text|identity) line [0-9]+):.*$/\1: <content hidden>/')"
  out="$(printf '%s\n' "$out" | tr "$SEP" ':')"
  fail "$1" "$(printf '%s\n' "$out" | mask)"
}

# ---- NUL bytes in any non-binary file (UTF-16 exports read as binary) --
if [ "$mode" = tree ]; then
  while IFS= read -r f; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    if LC_ALL=C tr -d '\0' < "$f" | cmp -s - "$f"; then :; else
      fail "text file contains NUL bytes (UTF-16? cannot be scanned): $(printf '%s\n' "$f" | sed "s#^$HOME#~#")"; fi
  done <<< "$SCANLIST"
fi

# ---- file names ------------------------------------------------------
if [ -n "$NAMES" ]; then
  n=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    # The whole (relative) path: an id-named FOLDER (inputs/<U-id>/)
    # published the id as surely as an id-named file (S024-16).
    b="$f"
    if printf '%s\n' "$b" | grep -oE 'U[0-9]{7,8}' | grep -qvE '^U1234567[0-9]?$|^U9990'; then n="$n$f
"; continue; fi
    # 8+ digit runs: only a synthetic 9990… id or a REAL date (YYYYMMDD
    # that parses) is exempt — 20991399 is an id, not a date.
    while IFS= read -r tok; do
      [ -n "$tok" ] || continue
      case "$tok" in 9990*) continue ;; esac
      if [ "${#tok}" -eq 8 ] && is_date8 "$tok"; then continue; fi
      n="$n$f
"; break
    done <<< "$(printf '%s\n' "$b" | grep -oE '[0-9]{8,}' || true)"
  done <<< "$NAMES"
  [ -z "$n" ] || fail "file NAME carries an account-id shape (IB names downloads after the account)" "$(printf '%s' "$n" | sort -u | mask)"
fi

if [ "$mode" != identity ]; then
# ---- generic patterns -------------------------------------------------
# The IB id inside a token too (an HTML element id `tbl..._U<7 digits>Body`):
# only a letter/digit right before the U, or a digit right after, ends it.
report "IB account id (U + 7-8 digits)"                  '(^|[^A-Za-z0-9])U[0-9]{7,8}([^0-9]|$)' 'U1234567[0-9]?|U9990[0-9]+'
# 'Account Number / Numéro de compte:,,,,,,,,NNNNNNNN' (Webull) puts a
# bilingual label and CSV padding between the two.
report "8-digit number next to the word account"        '[Aa]ccount[^0-9]{0,20}[0-9]{8}|[Aa]ccount [Nn](umber|o\.?)[^0-9]{0,40}[0-9]{8}' '9990[0-9]{4,}|1234567[89]'
report "home directory path"                            '/home/[a-z][a-z0-9_-]+|/Users/[A-Za-z][A-Za-z0-9_-]+' ''
fi
# Identities are published too (author / committer / tagger): the e-mail
# allowlist applies to them as to any text (S024-14).
report "e-mail address not on the allowlist"            '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' "$ALLOW_EMAILS"
if [ "$mode" != identity ]; then
report "social insurance number shape (3-3-3, valid check digit)" '\b[0-9]{3}[ -][0-9]{3}[ -][0-9]{3}\b' '' sin_text
report "credential-looking string"                      'ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|(api[_-]?key|secret|token|passw(or)?d)["'"'"' ]*[=:]["'"'"' ]*[A-Za-z0-9_\-]{20,}' ''
fi
# ---- account-number columns (tree / ad hoc) ---------------------------
# A Questrade or RBC export names the account in a column ("Account #",
# "Account"), far from the word on the line the regex above needs.
if [ "$mode" = tree ]; then
  acct_cols() {
    printf '%s\n' "$SCANLIST" | grep -iE '\.(csv|tsv)$' | while IFS= read -r f; do
      [ -f "$f" ] || continue
      LC_ALL=C awk -v FN="$f" -v SEP="$SEP" '
        function cells(line,   n, i, c, q, cur) {
          n = 0; q = 0; cur = ""
          for (i = 1; i <= length(line); i++) {
            c = substr(line, i, 1)
            if (c == "\"") { q = !q; continue }
            if (c == fs && !q) { C[++n] = cur; cur = ""; continue }
            cur = cur c
          }
          C[++n] = cur
          return n
        }
        function trim(v) { gsub(/^[ \t]+|[ \t]+$/, "", v); return v }
        BEGIN { fs = (tolower(FN) ~ /\.tsv$/) ? "\t" : "," }
        {
          sub(/\r$/, ""); n = cells($0); hdr = 0
          for (i = 1; i <= n; i++) {
            v = tolower(trim(C[i]))
            if (v ~ /^account( ?#| ?number| ?no\.?| ?id)?$/) { col[i] = 1; hdr = 1 }
          }
          if (hdr) next
          for (i in col) {
            v = trim(C[i])
            if (v ~ /^[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]?$/ \
                && v !~ /^9990/ && v !~ /^1234567[89]$/) {
              print FN SEP NR ":" $0; break
            }
          }
        }' "$f"
    done
  }
  out="$(acct_cols | grep -avE -e "$PII_OK" | sed "s#^${XT:-/nonexistent}/##" | tr "$SEP" ':')"
  [ -z "$out" ] || fail "8-9 digit account number under an Account column" "$(printf '%s\n' "$out" | mask)"
fi

# Show where a denylisted name is, never what: every path component the
# pattern matches is hidden (and the whole path when no single one does).
hide_parts() {   # hide_parts PATTERN  (paths on stdin)
  local f out c hid
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    out=""; hid=0
    IFS=/ read -ra parts <<< "$f"
    for c in "${parts[@]}"; do
      if [ -n "$c" ] && printf '%s\n' "$c" | grep -qiE -e "$1"; then c="<name hidden>"; hid=1; fi
      out="$out/$c"
    done
    [ "$hid" -eq 1 ] && printf '%s\n' "${out#/}" || printf '%s\n' "<path hidden>"
  done
}

# ---- private denylist -------------------------------------------------
# A denylist that was asked for (TAXJSON_PII_DENYLIST) but is missing, or
# one that exists but cannot be read, fails the scan: passing on the
# generic patterns alone would drop the guard without a word.
DENYSHOW="$(printf '%s' "$DENY" | sed "s#^$HOME#~#")"
if [ -e "$DENY" ] && [ ! -r "$DENY" ]; then
  fail "private denylist $DENYSHOW exists but cannot be read — fix its permissions"
  denynote="denylist unreadable"
elif [ ! -e "$DENY" ] && [ -n "${TAXJSON_PII_DENYLIST:-}" ]; then
  fail "private denylist $DENYSHOW (TAXJSON_PII_DENYLIST) does not exist"
  denynote="denylist missing"
elif [ -r "$DENY" ]; then
  while IFS= read -r pat || [ -n "$pat" ]; do
    pat="${pat%$'\r'}"
    case "$pat" in ''|'#'*) continue ;; esac
    printf '' | grep -qE -e "$pat" -- - 2>/dev/null; rc=$?
    if [ "$rc" -eq 2 ]; then fail "denylist line is not a valid extended regex (that guard is DISABLED until fixed): $(printf '%s' "$pat" | mask)"; continue; fi
    loose="$(printf '%s\n' "$pat" | loosen)"
    printf '' | grep -qE -e "$loose" -- - 2>/dev/null
    [ $? -eq 2 ] && loose="$pat"          # never trade a guard for a variant
    CI="-i"
    if [ "$mode" = identity ]; then
      report "private denylist match in a commit/tag IDENTITY (author, committer or tagger)" "$loose" ''
    else
      report "private denylist match" "$loose" ''
      if [ -n "$NAMES" ] && printf '%s\n' "$NAMES" | grep -qiE -e "$loose"; then
        fail "file NAME matches the private denylist" "$(printf '%s\n' "$NAMES" | grep -iE -e "$loose" | hide_parts "$loose" | sort -u)"
      fi
    fi
    CI=""
  done < "$DENY"
  denynote="denylist: $DENYSHOW"
else
  denynote="no private denylist at $DENYSHOW — generic patterns only (scripts/dev-setup.sh scaffolds one)"
fi

if [ "$hits" -gt 0 ]; then
  echo; echo "check-pii: $hits problem(s) — fix the content, or mark a genuine false positive with a '# pii-ok' (or 'pii-ok:') comment on that line."
  exit 1
fi
echo "check-pii: clean ($denynote)"
