#!/usr/bin/env bash
# Personal-data / secret scan — the last thing between a commit and the
# public web. Runs in three places:
#   scripts/ci.sh                whole tree, tracked + untracked (every mode)
#   .git/hooks/pre-push          the diff, the commit / tag messages and
#                                the author/committer/tagger identities a
#                                push would publish (--diff / --text /
#                                --message / --identity on stdin)
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
#   3. Private figure list ~/.config/taxjson/pii-amounts (override:
#      TAXJSON_PII_AMOUNTS) — the maintainer's own money figures, as
#      salted SHA-256 hashes (no plain figure on disk), written by
#        scripts/check-pii.sh --collect-amounts PROJECT_DIR...
#      from each project's reports/, work/*.sum, *.toml and *.tt, and
#      from the raw exports under its inputs/ (there also prices,
#      quantities and rates with 3+ decimals, broker reference codes and
#      dated clock times; see FIG_PY below). Every mode except
#      --identity refuses a line holding a value whose hash is listed
#      ("matches a figure from your own books"),
#      naming file:line, never the figure. There is NO pii-ok escape: a
#      synthetic number that collides gets a different number. A missing
#      list at the default path is skipped silently (contributors have
#      none); one named by TAXJSON_PII_AMOUNTS that is missing, or a list
#      that cannot be read or parsed, fails the scan.
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
# Account / Account # / Account Number is an account number (the pre-push
# --diff reads the header from the hunk or the working-tree file). An IB
# id matches in either case (u1234567 in IB HTML element ids). A SIN
# labelled SIN / NAS / social insurance matches in any separator form
# (none, space, dash, dot) when its check digit holds; an SSN labelled
# SSN / TIN / ITIN / Tax ID likewise. The denylist must be UTF-8 (a
# leading BOM is dropped; NUL bytes, invalid UTF-8 or a directory fail
# the scan, since no pattern of such a file could ever match). Denylist
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
ALLOW_EMAILS='noreply@anthropic\.com|noreply@github\.com|users\.noreply\.github\.com|@example\.(com|org|net)|ckscijdtest@gmail\.com'
# Real binaries: the only files the scan may skip. Everything else is
# scanned as text (grep -a, so a stray Latin-1 byte cannot make grep call
# the file "binary" and skip it), and a NUL byte in it — a UTF-16 export
# whatever its extension (.tsv, .log, .ofx) — fails closed.
BIN_EXT='pdf|png|jpe?g|gif|ico|webp|bmp|tiff?|svgz|xlsx|xlsm|xls|docx|doc|pptx|odt|ods|zip|gz|tgz|bz2|xz|7z|whl|woff2?|ttf|otf|eot|mp3|mp4|mov|pyc'
is_bin() { printf '%s\n' "$1" | grep -qiE "\.($BIN_EXT)\$"; }

AMOUNTS="${TAXJSON_PII_AMOUNTS:-$HOME/.config/taxjson/pii-amounts}"

# The private figure list (source 3 above): one Python helper shared by
# --collect-amounts and every scan, so both read figures the same way.
# A figure is an amount with cents — 1,234.56 or 1234.5 — normalized to  # pii-ok
# plain digits with two decimals; it is "distinctive" (worth listing)
# with 5+ digits and cents other than 00/25/50/75, the figures a
# synthetic example is unlikely to hit by chance.
# The raw exports under a project's inputs/ add three more kinds, each
# hashed under its own prefix and each with its own "distinctive" test
# (only distinctive values are listed, so a short or round synthetic
# value never collides by chance; nothing is exempted for appearing in
# the repo — a collision gets a different synthetic value):
#   (amounts with cents from inputs/ are listed only from 6 digits up:
#         an export holds thousands of small ones)
#   dec:  a number with 3+ decimals after trailing zeros are dropped and
#         6+ significant digits — prices, FX rates, fractional and coin
#         quantities (12.34567, .000123456, 1,234.5678); a shorter one  # pii-ok
#         such as a 4-decimal rate is too common to list
#   code: a broker reference — a 6+ character letters-and-digits token
#         with 2+ digits (internal security / option codes, OCC option
#         symbols with their root, order refs), or a 7+ digit number
#         that is not a YYYYMMDD date and does not end in 0000;
#         case-insensitive. Not codes: a word ending in a 2-digit
#         number (THRU02), month-name dates (20JUN25), an
#         option series without its root, and public security ids (an
#         ISIN or CUSIP whose check digit holds)
#   ts:   a clock time with seconds next to its date (2025-01-02 10:11:12,
#         20250102;101112, 1/2/2025 10:11:12), seconds not :00 and not
#         23:59:59 (placeholder and period-end stamps)  # pii-ok
FIG_PY='
import hashlib, os, re, secrets, sys
AMT = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+\.\d{1,2}|\d{3,}\.\d{1,2})(?![\d])")
# Text pulled out of a binary (a PDF content stream) is full of drawing
# coordinates such as 172.1: there only an exact two-decimal amount counts.
AMT2 = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+\.\d{2}|\d{3,}\.\d{2})(?![\d])")
SUFFIXES = (".txt", ".sum", ".json", ".toml", ".tt", ".csv", ".md", "")
HEAD = "# taxjson private figure list: salted SHA-256 of figures from your own books (scripts/check-pii.sh --collect-amounts)"
# The inputs/ kinds (see the header above).
# Both readings of "5,234.5678": one number, or a cell 5 then 234.5678.  # pii-ok
DEC = re.compile(r"(?<![\w.:])(\d{1,3}(?:,\d{3})+\.\d{3,}|\d*\.\d{3,})(?!\d|\.\d)")
DEC_CELL = re.compile(r"(?<![\w.:])(\d*\.\d{3,})(?!\d|\.\d)")
CODE = re.compile(r"(?<![A-Za-z0-9.])([A-Za-z0-9]{6,})(?![A-Za-z0-9]|\.\d)")
TS = (re.compile(r"(?<!\d)(\d{4})-(\d\d)-(\d\d)(?:[ T;]|,\s*)(\d\d):(\d\d):(\d\d)(?!\d)"),
      re.compile(r"(?<!\d)(\d{4})(\d\d)(\d\d)(?:[;T]|,\s*)(\d\d):?(\d\d):?(\d\d)(?!\d)"),
      re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})[ ,]+(\d{1,2}):(\d\d):(\d\d)(?!\d)"))
DATE8 = re.compile(r"(19|20)\d\d(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")
def normalize(tok):
    whole, frac = tok.replace(",", "").split(".")
    return (whole.lstrip("0") or "0") + "." + (frac + "0")[:2]
def distinctive(v):
    return (float(v) >= 100 and v[-2:] not in ("00", "25", "50", "75")
            and len(v.replace(".", "")) >= 5)
def figures(line, strict=False):
    return [normalize(tok) for tok in (AMT2 if strict else AMT).findall(line)]
def dec_value(tok):
    whole, frac = tok.replace(",", "").split(".")
    whole, frac = whole.lstrip("0") or "0", frac.rstrip("0")
    if len(frac) < 3 or len((whole + frac).lstrip("0")) < 6:
        return None
    return "dec:" + whole + "." + frac
# Not broker references: a date written with a month name (20JUN25,
# DEC21), an option series without its root (250620C00050000), and a
# public security id (an ISIN or CUSIP with a valid check digit).
MONTHTOK = re.compile(r"\d{0,4}(JAN|FEB|MAR|APR|MAY|JUNE?|JULY?|AUG|SEPT?|OCT|NOV|DEC)\d{0,4}")
OCCTAIL = re.compile(r"\d{6}[CP]\d{8}")
OCC = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,5}\d?) {1,5}(\d{6}[CP]\d{8})(?!\d)")
def _luhn(digits):
    total = 0
    for i, c in enumerate(reversed(digits)):
        d = int(c) * (2 if i % 2 else 1)
        total += d // 10 + d % 10
    return total % 10 == 0
def _isin(t):
    return (len(t) == 12 and t[:2].isalpha() and t[-1].isdigit()
            and _luhn("".join(str(int(c, 36)) for c in t)))
def _cusip(t):
    if len(t) != 9 or not t[-1].isdigit():
        return False
    total = 0
    for i, c in enumerate(t[:8]):
        v = int(c, 36) * (2 if i % 2 else 1)
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(t[-1])
def code_value(tok):
    t = tok.upper()
    digits = sum(c.isdigit() for c in t)
    if (digits < 2 or MONTHTOK.fullmatch(t) or OCCTAIL.fullmatch(t)
            or re.fullmatch(r"[A-Z]+\d\d", t)):      # a word + number: THRU02
        return None
    if t.isdigit() and (len(t) < 7 or t.endswith("0000") or len(set(t)) < 3
                        or (len(t) == 8 and DATE8.fullmatch(t))):
        return None
    if _isin(t) or _cusip(t):
        return None
    if re.fullmatch(r"\d{8}T\d{6}", t):       # a compact date-time: a ts
        return None
    return "code:" + t
def ts_values(line):
    out = []
    for n, rx in enumerate(TS):
        for m in rx.finditer(line):
            g = m.groups()
            if n == 2:
                g = (g[2], g[0], g[1]) + g[3:]
            y, a, b, hh, mm, ss = g
            if ss == "00" or (hh, mm, ss) == ("23", "59", "59"):
                continue
            out.append("ts:%s-%02d-%02d %02d:%s:%s" % (y, int(a), int(b), int(hh), mm, ss))
    return out
def raw_values(line):
    """The inputs/ kinds found on a line (prefixed, distinctive only)."""
    out = [v for v in map(dec_value, DEC.findall(line) + DEC_CELL.findall(line)) if v]
    out += [v for v in map(code_value, CODE.findall(line)) if v]
    out += ["code:" + r + t for r, t in OCC.findall(line)]   # IB "ABC   250620C00050000"
    return out + ts_values(line)
def values(line, strict=False):
    """Every listed kind a line can carry: amounts with cents (the
    original, unprefixed kind) plus the inputs/ kinds."""
    return [v for v in figures(line, strict) if distinctive(v)] + raw_values(line)
def digest(salt, v):
    return hashlib.sha256((salt + ":" + v).encode()).hexdigest()
def load(path):
    salt, hashes = None, set()
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if salt is None:
                if not re.fullmatch(r"salt [0-9a-f]{32,}", line):
                    raise SystemExit("figure list: line %d: expected the salt line" % n)
                salt = line.split()[1]
            elif re.fullmatch(r"[0-9a-f]{64}", line):
                hashes.add(line)
            else:
                raise SystemExit("figure list: line %d is not a SHA-256 hash" % n)
    if salt is None:
        raise SystemExit("figure list has no salt line")
    return salt, hashes
def project_files(d):
    found = []
    for sub, _dirs, names in os.walk(os.path.join(d, "reports")):
        found += [os.path.join(sub, x) for x in names]
    w = os.path.join(d, "work")
    if os.path.isdir(w):
        found += [os.path.join(w, x) for x in os.listdir(w) if x.endswith(".sum")]
    found += [os.path.join(d, x) for x in os.listdir(d)
              if x.endswith((".toml", ".tt"))]
    return sorted(p for p in found if os.path.isfile(p)
                  and os.path.splitext(p)[1] in SUFFIXES)
def input_files(d):
    """Every file under inputs/ (the raw broker exports), any extension."""
    found = []
    for sub, _dirs, names in os.walk(os.path.join(d, "inputs")):
        found += [os.path.join(sub, x) for x in names]
    return sorted(p for p in found if os.path.isfile(p))
def read_text(p):
    with open(p, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw.count(b"\0") > len(raw) // 4:
        return raw.decode("utf-16", errors="ignore")
    return raw.decode("utf-8", errors="ignore")
BINARY = (".pdf", ".xlsx", ".xls", ".xlsm", ".zip", ".gz", ".png", ".jpg", ".jpeg", ".docx", ".ods")
def collect(path, dirs):
    if os.path.exists(path):
        salt, hashes = load(path)
    else:
        salt, hashes = secrets.token_hex(16), set()
    before = len(hashes)
    for d in dirs:
        if not os.path.isdir(d):
            raise SystemExit("not a directory: " + d)
        got = set()
        for p in project_files(d):
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    got.update(v for v in figures(line) if distinctive(v))
        skipped = 0
        for p in input_files(d):
            if p.lower().endswith(BINARY):
                skipped += 1
                continue
            for line in read_text(p).splitlines():
                # An export holds thousands of small amounts: only 6+
                # digit ones (1,000.00 and up) are rare enough to list.  # pii-ok
                got.update(v for v in figures(line)
                           if distinctive(v) and len(v) >= 7)
                got.update(raw_values(line))
        if skipped:
            print("check-pii: warning: %d binary file(s) under %s/inputs not read (export them as text to list their values)" % (skipped, d), file=sys.stderr)
        if not got:
            print("check-pii: warning: no figures found under %s (a project folder with reports/ or inputs/?)" % d, file=sys.stderr)
        hashes.update(digest(salt, v) for v in got)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(HEAD + "\nsalt " + salt + "\n")
        f.writelines(h + "\n" for h in sorted(hashes))
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    print("check-pii: figure list %s: %d figure(s), %d new"
          % (sys.argv[3], len(hashes), len(hashes) - before))
def hit(salt, hashes, line, strict=False):
    return any(digest(salt, v) in hashes for v in values(line, strict))
def main():
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "collect":
        return collect(path, sys.argv[4:])
    salt, hashes = load(path)
    sep = sys.argv[3]
    if cmd == "files":           # paths on stdin -> path SEP line
        xt = sys.argv[4] + "/" if len(sys.argv) > 4 and sys.argv[4] else None
        for p in sys.stdin.read().splitlines():
            if not p:
                continue
            strict = bool(xt) and p.startswith(xt)
            with open(p, encoding="utf-8", errors="ignore") as f:
                for n, line in enumerate(f, 1):
                    if hit(salt, hashes, line, strict):
                        print(p + sep + str(n))
    elif cmd == "diff":          # a unified diff -> new path SEP new line
        path_, n, old, new = "?", 0, 0, 0
        for line in sys.stdin.read().splitlines():
            if old > 0 or new > 0:   # inside a hunk: count both sides
                if line.startswith("+"):
                    if hit(salt, hashes, line[1:]):
                        print(path_ + sep + str(n))
                    n += 1; new -= 1
                elif line.startswith("-"):
                    old -= 1
                elif line.startswith("\\"):
                    pass             # "\ No newline at end of file"
                else:
                    n += 1; old -= 1; new -= 1
                continue
            m = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                old = int(m.group(1) if m.group(1) is not None else 1)
                n = int(m.group(2))
                new = int(m.group(3) if m.group(3) is not None else 1)
            elif line.startswith("+++ "):
                path_ = line[4:]
                path_ = path_[2:] if path_.startswith("b/") else path_
            elif line.startswith("diff --git "):
                path_ = "?"
    elif cmd == "text":          # lines on stdin -> LABEL line N
        for n, line in enumerate(sys.stdin.read().splitlines(), 1):
            if hit(salt, hashes, line):
                print(sys.argv[4] + " line " + str(n))
main()
'

mode=tree
MSG=0        # --message: --text plus the money-amount check (A2-1384)
case "${1:-}" in
  --diff) mode=diff; shift ;; --text) mode=text; shift ;;
  --message) mode=text; MSG=1; shift ;;
  --identity) mode=identity; shift ;;
  --collect-amounts)
    shift
    [ $# -gt 0 ] || { echo "usage: scripts/check-pii.sh --collect-amounts PROJECT_DIR..." >&2; exit 2; }
    PYB="$(command -v "${PYTHON:-python3}" 2>/dev/null)" || { echo "check-pii: python3 is needed to collect figures" >&2; exit 2; }
    dirs=()
    for d in "$@"; do case "$d" in /*) dirs+=("$d") ;; *) dirs+=("$PWD0/$d") ;; esac; done
    exec "$PYB" -c "$FIG_PY" collect "$AMOUNTS" "$(printf '%s' "$AMOUNTS" | sed "s#^$HOME#~#")" "${dirs[@]}"
    ;;
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
AMT_INPUT="" # --diff: the added prose / comment text checked for money amounts
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
  # Money amounts (A2-1384, security review M2) are checked where owner
  # figures were once quoted: every added line of a CHANGELOG or a
  # markdown / reST doc, and the COMMENT part (from a #, //, /* or <!--
  # at the start or after a blank) of any other added line, each
  # prefixed with its file. Data and code are not: fixtures carry
  # synthetic amounts.
  AMT_INPUT="$(printf '%s\n' "$RAW" | LC_ALL=C awk '
    /^diff --git / { f = ""; next }
    /^\+\+\+ / { f = substr($0, 5); sub(/^b\//, "", f); next }
    /^\+/ {
      s = substr($0, 2)
      if (f ~ /(^|\/)CHANGELOG[^\/]*$/ || f ~ /\.(md|markdown|rst)$/) { print f ": " s; next }
      if (match(s, /(^|[ \t])(#|\/\/|\/\*|<!--)/)) print f ": " substr(s, RSTART)
    }')"
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

# A LABELLED SIN in any separator form — spaced, dashed, dotted or none
# (A2-0760, A2-1387): the label makes an unspaced 9-digit run safe to
# test, and the check digit still has to hold.
sin_label_filter() {
  grep -av "(embedded text)$SEP" | LC_ALL=C awk '
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
    while (match(s, /[0-9][0-9][0-9][ .-]?[0-9][0-9][0-9][ .-]?[0-9][0-9][0-9]/)) {
      pre = (RSTART > 1) ? substr(s, RSTART - 1, 1) : ""
      post = substr(s, RSTART + RLENGTH, 1)
      d = substr(s, RSTART, RLENGTH); gsub(/[ .-]/, "", d)
      if (pre !~ /[0-9]/ && post !~ /[0-9]/ && luhn(d)) { keep = 1; break }
      s = substr(s, RSTART + 1)
    }
    if (keep) print
  }'
}

# A labelled US SSN / TIN (3-2-4, any separator) that could be issued:
# area not 000, 666 or 9xx, group not 00, serial not 0000 (A2-1387).
ssn_filter() {
  grep -av "(embedded text)$SEP" | LC_ALL=C awk '
  {
    s = $0; keep = 0
    while (match(s, /[0-9][0-9][0-9][ .-]?[0-9][0-9][ .-]?[0-9][0-9][0-9][0-9]/)) {
      pre = (RSTART > 1) ? substr(s, RSTART - 1, 1) : ""
      post = substr(s, RSTART + RLENGTH, 1)
      d = substr(s, RSTART, RLENGTH); gsub(/[ .-]/, "", d)
      a = substr(d, 1, 3); g = substr(d, 4, 2); r = substr(d, 6, 4)
      if (pre !~ /[0-9]/ && post !~ /[0-9]/ && a != "000" && a != "666" \
          && a !~ /^9/ && g != "00" && r != "0000") { keep = 1; break }
      s = substr(s, RSTART + 1)
    }
    if (keep) print
  }'
}

# A message line with the bare word pii-ok is a declared synthetic number.
amount_filter() { grep -avE '(^|[^A-Za-z0-9_-])pii-ok([^A-Za-z0-9_-]|$)' || true; }

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
    # A lower-case id (IB HTML names: u1234567.html) — not glued to a
    # letter before it, so an ordinary word ending in 'u' is not one (A2-0449).
    if printf '%s\n' "$b" | grep -oE '(^|[^A-Za-z])u[0-9]{7,8}' | sed -E 's/^[^u]//' \
        | grep -qvE '^u1234567[0-9]?$|^u9990'; then n="$n$f
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
# Either case: IB HTML element ids carry the id lower-cased (A2-0449).
report "IB account id (U + 7-8 digits)"                  '(^|[^A-Za-z0-9])[Uu][0-9]{7,8}([^0-9]|$)' '[Uu]1234567[0-9]?|[Uu]9990[0-9]+'
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
report "social insurance number (labelled SIN / NAS, valid check digit)" \
  '(\b([Ss][Ii][Nn]|NAS)\b|[Ss]ocial [Ii]nsurance|[Aa]ssurance [Ss]ociale)[^0-9A-Za-z]{0,10}([Nn]umber|[Nn]o|[Nn]um.{1,2}ro)?[^0-9A-Za-z]{0,20}[0-9]{3}[ .-]?[0-9]{3}[ .-]?[0-9]{3}([^0-9]|$)' '' sin_label_filter
report "social security number (labelled SSN / TIN / Tax ID)" \
  '(\b(SSN|I?TIN)\b|\b[Tt]ax ?[Ii][Dd]\b|[Ss]ocial [Ss]ecurity)[^0-9A-Za-z]{0,10}([Nn]umber|[Nn]o)?[^0-9A-Za-z]{0,20}[0-9]{3}[ .-]?[0-9]{2}[ .-]?[0-9]{4}([^0-9]|$)' '' ssn_filter
# Case-insensitive (CI=-i also hides the hit's content): `TOKEN=`,
# `Api_Key:` and `SECRET =` as well as the lower-case spellings. The
# broker tokens taxjson-fetch takes: `--refresh-token <tok>` /
# `--flex-token=<tok>` and a QUESTRADE_REFRESH_TOKEN / *_FLEX_TOKEN
# assignment (exported or not) with a value of 12+ characters. An
# all-capitals placeholder value (YOUR_REFRESH_TOKEN) is not a token
# (2026-10 security review M6).
CRED_RE='ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|(api[_-]?key|secret|token|passw(or)?d)["'"'"' ]*[=:]["'"'"' ]*[A-Za-z0-9_\-]{20,}|--(refresh|flex)-token[ =]["'"'"']?[A-Za-z0-9_.-]{12,}|(questrade_refresh_token|[a-z0-9_]*flex_token)[ ]*=[ ]*["'"'"']?[A-Za-z0-9_.-]{12,}'
CRED_PLACEHOLDER='(--[Rr][Ee][Ff][Rr][Ee][Ss][Hh]-[Tt][Oo][Kk][Ee][Nn][ =]|--[Ff][Ll][Ee][Xx]-[Tt][Oo][Kk][Ee][Nn][ =]|[Tt][Oo][Kk][Ee][Nn]["'"'"' ]*[=:])["'"'"' ]*[A-Z_]{12,}([^A-Za-z0-9_.-]|$)'
CI="-i"
report "credential-looking string"                      "$CRED_RE" "$CRED_PLACEHOLDER"
CI=""
# A commit or tag MESSAGE (--message) never quotes a money amount with
# thousands separators and cents (1,234,567.89): owner-book totals once
# reached the public history that way (A2-1384). A synthetic number in a
# message carries the word pii-ok on its line (bare, as messages have no
# comment syntax). The pushed DIFF (--diff) gets the same check on its
# CHANGELOG / doc lines and code comments (AMT_INPUT above; security
# review M2); data, code and the tree scan are not checked for it.
AMOUNT_RE='(^|[^0-9,.])[0-9]{1,3}(,[0-9]{3})+[.][0-9]{2}([^0-9]|$)'
if [ "$MSG" = 1 ]; then
report "money amount in a commit/tag message (thousands separators and cents; mark a synthetic one pii-ok)" \
  "$AMOUNT_RE" '' amount_filter
fi
if [ "$mode" = diff ] && [ -n "$AMT_INPUT" ]; then
  _input="$INPUT"; INPUT="$AMT_INPUT"
  report "money amount added to a CHANGELOG / doc or a code comment (thousands separators and cents; mark a synthetic one pii-ok)" \
    "$AMOUNT_RE" '' amount_filter
  INPUT="$_input"
fi
fi
# ---- account-number columns (tree / ad hoc / pre-push diff) ----------
# A Questrade or RBC export names the account in a column ("Account #",
# "Account"), far from the word on the line the regex above needs.
# MARKED=1 (diff mode): each line starts with '+' (an added row, the only
# kind reported) or ' ' (context, or the file's header row from the
# working tree — a hunk rarely carries the header).
ACCT_AWK='
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
    line = $0; m = "+"
    if (MARKED) { m = substr(line, 1, 1); line = substr(line, 2) }
    sub(/\r$/, "", line); sub(/^\357\273\277/, "", line)
    n = cells(line); hdr = 0
    for (i = 1; i <= n; i++) {
      v = tolower(trim(C[i]))
      if (v ~ /^account( ?#| ?number| ?no\.?| ?id)?$/) { col[i] = 1; hdr = 1 }
    }
    if (hdr || m != "+") next
    for (i in col) {
      v = trim(C[i])
      if (v ~ /^[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]?$/ \
          && v !~ /^9990/ && v !~ /^1234567[89]$/) {
        print FN SEP (MARKED ? "added row" : NR) ":" line; break
      }
    }
  }'
acct_out=""
if [ "$mode" = tree ]; then
  acct_cols() {
    printf '%s\n' "$SCANLIST" | grep -iE '\.(csv|tsv)$' | while IFS= read -r f; do
      [ -f "$f" ] || continue
      LC_ALL=C awk -v FN="$f" -v SEP="$SEP" -v MARKED=0 "$ACCT_AWK" "$f"
    done
  }
  acct_out="$(acct_cols | grep -avE -e "$PII_OK" | sed "s#^${XT:-/nonexistent}/##" | tr "$SEP" ':')"
elif [ "$mode" = diff ]; then
  # The pre-push diff (A2-1388): split the hunks of every .csv/.tsv per
  # file, then read them with the file's header row in front.
  DC="$(mktemp -d)"; trap 'rm -rf "$DC" "${RAWF:-}"' EXIT
  printf '%s\n' "$RAW" | LC_ALL=C awk -v D="$DC" '
    /^diff --git / { out = ""; next }
    /^\+\+\+ / { p = substr($0, 5); sub(/^b\//, "", p); out = ""
                 if (tolower(p) ~ /\.(csv|tsv)$/) { k++; out = D "/" k
                   print p > (out ".path"); close(out ".path") }
                 next }
    out != "" && /^[+ ]/ { print > out }'
  acct_diff() {
    local pf f path
    for pf in "$DC"/*.path; do
      [ -f "$pf" ] || continue
      f="${pf%.path}"; path="$(cat "$pf")"
      [ -f "$f" ] || continue
      { [ -f "$path" ] && head -n 1 "$path" | sed 's/^/ /'; cat "$f"; } \
        | LC_ALL=C awk -v FN="$path" -v SEP="$SEP" -v MARKED=1 "$ACCT_AWK"
    done
  }
  acct_out="$(acct_diff | grep -avE -e "$PII_OK" | tr "$SEP" ':')"
fi
[ -z "$acct_out" ] || fail "8-9 digit account number under an Account column" "$(printf '%s\n' "$acct_out" | mask)"

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
# Valid UTF-8? iconv (glibc / macOS) or python3; with neither, the NUL
# and BOM guards still apply.
deny_utf8() {
  if command -v iconv >/dev/null 2>&1; then
    iconv -f UTF-8 -t UTF-8 < "$1" >/dev/null 2>&1
  elif PYU="$(command -v "${PYTHON:-python3}" 2>/dev/null)" && [ -n "$PYU" ]; then
    "$PYU" -c 'import sys; open(sys.argv[1], "rb").read().decode("utf-8")' "$1" 2>/dev/null
  else
    return 0
  fi
}
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
elif [ -e "$DENY" ] && [ ! -f "$DENY" ]; then
  fail "private denylist $DENYSHOW is not a regular file (a directory?) — that guard is DISABLED until fixed"
  denynote="denylist not a file"
elif [ -f "$DENY" ] && ! LC_ALL=C tr -d '\0' < "$DENY" | cmp -s - "$DENY"; then
  # A UTF-16 save (Notepad 'Unicode') reads as NUL-stuffed bytes: no
  # pattern would ever match (A2-0044/A2-0450).
  fail "private denylist $DENYSHOW contains NUL bytes (saved as UTF-16?) — save it as UTF-8; that guard is DISABLED until fixed"
  denynote="denylist not UTF-8"
elif [ -f "$DENY" ] && ! deny_utf8 "$DENY"; then
  fail "private denylist $DENYSHOW is not valid UTF-8 (a cp1252 / Latin-1 save?) — save it as UTF-8; an accented pattern would never match"
  denynote="denylist not UTF-8"
elif [ -r "$DENY" ]; then
  BOM=$'\xef\xbb\xbf'
  while IFS= read -r pat || [ -n "$pat" ]; do
    pat="${pat%$'\r'}"
    # A UTF-8 BOM (Notepad's 'UTF-8 with BOM') glued to the first
    # pattern would make it unmatchable (A2-0044/A2-0458).
    pat="${pat#"$BOM"}"
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

# ---- private figure list ----------------------------------------------
# Hashes of the maintainer's own figures (source 3 above). No pii-ok
# escape: a synthetic number that collides gets a different number.
AMTSHOW="$(printf '%s' "$AMOUNTS" | sed "s#^$HOME#~#")"
FIGMSG="matches a figure from your own books (private figure list $AMTSHOW) — use a different, synthetic number"
if [ "$mode" = identity ]; then
  :
elif [ ! -e "$AMOUNTS" ]; then
  if [ -n "${TAXJSON_PII_AMOUNTS:-}" ]; then
    fail "private figure list $AMTSHOW (TAXJSON_PII_AMOUNTS) does not exist"
  fi
elif [ ! -f "$AMOUNTS" ] || [ ! -r "$AMOUNTS" ]; then
  fail "private figure list $AMTSHOW cannot be read — that guard is DISABLED until fixed"
elif ! PYF="$(command -v "${PYTHON:-python3}" 2>/dev/null)" || [ -z "$PYF" ]; then
  fail "private figure list $AMTSHOW present but no python3 to check it — cannot be scanned"
else
  case "$mode" in
    tree) fig_out="$(printf '%s\n' "$SCANLIST" | grep -v '^$' \
                     | "$PYF" -c "$FIG_PY" files "$AMOUNTS" "$SEP" "$XT" 2>&1)"; rc=$? ;;
    diff) fig_out="$(tr -d '\0' < "$RAWF" | "$PYF" -c "$FIG_PY" diff "$AMOUNTS" "$SEP" 2>&1)"; rc=$? ;;
    text) fig_out="$(printf '%s\n' "$INPUT" | "$PYF" -c "$FIG_PY" text "$AMOUNTS" "$SEP" "$([ "$MSG" = 1 ] && echo message || echo text)" 2>&1)"; rc=$? ;;
  esac
  if [ "$rc" -ne 0 ]; then
    fail "private figure list $AMTSHOW could not be checked — that guard is DISABLED until fixed" "$(printf '%s\n' "$fig_out" | tail -3 | mask)"
  elif [ -n "$fig_out" ]; then
    # Location only: the path (XT mirror shown as the binary's own path)
    # and line number — never the line, which holds the figure.
    fail "$FIGMSG" "$(printf '%s\n' "$fig_out" \
      | sed "s#^${XT:-/nonexistent}/\(.*\)$SEP#\1 (embedded text)$SEP#; s#^$HOME/#~/#" | tr "$SEP" ':' | head -50)"
  fi
  denynote="$denynote; figure list: $AMTSHOW"
fi

if [ "$hits" -gt 0 ]; then
  echo; echo "check-pii: $hits problem(s) — fix the content, or mark a genuine false positive with a '# pii-ok' (or 'pii-ok:') comment on that line (a figure-list match has no such escape: change the number)."
  exit 1
fi
echo "check-pii: clean ($denynote)"
