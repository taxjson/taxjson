"""`taxjson redact FILE...` — strip account numbers, names and addresses
from a broker export while keeping every row shape, so a real statement
can be shared as a parser sample or attached to a bug report.

What it changes, and nothing else:
  * account identifiers — IB `U1234567` / `DU…` / `F…` ids, Questrade
    `Account #`, IB `Xfer Account` / `ClientAccountID`, RBC
    `"Account: 12345678 - Margin"` headers, Fidelity/Schwab-style
    `Z12345678` / `1234-5678` values in an account column, any
    `account … <digits>` phrase — each distinct id becomes a stable
    placeholder of the SAME shape (`U99900001`, `99900001`, `9990-0002`)
    and every occurrence in the file, descriptions included, is
    replaced. Ids shorter than five characters and date-like 8-digit
    numbers are never treated as ids, so quantities and prices stay;
  * holder identity — IB `Account Information` Name / Alias / address
    rows, `AccountAlias` columns, `Name:` / `Client:` / `Owner:` header
    lines (quoted or not), e-mail addresses;
  * anything matching the private denylist (`~/.config/taxjson/
    pii-denylist`, the same file scripts/check-pii.sh uses) or `--also`
    — case-insensitive; an invalid pattern is reported, not raised.
Encoding: UTF-8 (BOM kept), UTF-16 (BOM or NUL-stuffed; re-written as
UTF-8 with a note), else cp1252 with a note — a file the strict UTF-8
parsers refuse is still redacted, and the note says so. Line endings
are preserved byte for byte.

Output goes to `<name>.redacted<ext>` beside the input (or --out DIR),
with any id in the NAME itself replaced too; an existing copy is only
overwritten with --force, a symlink never. The input is never modified.
The report shows placeholders and id lengths, never the ids. Descriptions
are free text a broker fills in — the report reminds you to read them.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_IB_ID = re.compile(r"(?<![A-Za-z0-9])(?:DU|U|F|I)\d{7,8}(?![0-9])")
# Free text needs 7+ digits (hyphens allowed) — a 5-digit "id" is too
# often a round quantity elsewhere in the file. Explicit account COLUMNS
# accept 5+.
_ACCOUNT_PHRASE = re.compile(
    r"(account(?:\s*(?:#|number|no\.?|id))?\s*[:#]?\s*)(\d[\d-]{5,11}\d)(?![\d]|\.\d)",
    re.IGNORECASE)
_COL_ID = re.compile(r"^[A-Z]{0,2}\d[\d-]{3,}[A-Z0-9]?$")     # 5+ chars, mostly digits
_DATE8 = re.compile(r"^(19|20)\d{6}$")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACCOUNT_COLS = ("account #", "account", "xfer account", "account number",
                 "account no", "account no.", "acct", "account id",
                 "clientaccountid", "client account id", "acctid")
_ALIAS_COLS = ("accountalias", "account alias", "account name",
               "account nickname", "nickname")
_IDENTITY_ROWS = ("name", "account alias", "address", "street", "city",
                  "state", "postal code", "zip", "country", "phone",
                  "email", "customer id", "client name", "holder",
                  "owner", "primary owner", "customer")
_HEADER_LINE = re.compile(
    r'^(\s*"?)((?:name|client|client name|account holder|owner|primary owner|customer)\s*[:,]\s*)',
    re.IGNORECASE)


class Report:
    def __init__(self) -> None:
        self.accounts: Dict[str, str] = {}      # original -> placeholder
        self.identity_rows = 0
        self.emails = 0
        self.patterns = 0
        self.description_columns: List[str] = []
        self.encoding = "utf-8"
        self.notes: List[str] = []

    @staticmethod
    def masked(s: str) -> str:
        """Never the id: its shape and length only ('U + 8 digits')."""
        digits = sum(c.isdigit() for c in s)
        lead = re.match(r"[A-Za-z]*", s).group(0)
        return f"{lead + ' + ' if lead else ''}{digits} digits"


def _placeholder(orig: str, n: int) -> str:
    """Same shape as the original: letters and punctuation kept in place,
    the digit positions become a 9990-prefixed, zero-padded counter —
    the shape scripts/check-pii.sh recognises as synthetic."""
    digits = sum(c.isdigit() for c in orig)
    body = "9990" + str(n).zfill(max(digits - 4, 1))
    body = body[-digits:] if digits >= 5 else body
    it = iter(body)
    return "".join(next(it) if c.isdigit() else c for c in orig) if digits >= 5 \
        else orig.translate(str.maketrans("0123456789", "9999999999"))


def _split(line: str) -> Optional[List[str]]:
    try:
        rows = list(csv.reader(io.StringIO(line)))
    except csv.Error:
        return None
    return rows[0] if rows else []


def _is_id(v: str) -> bool:
    v = v.strip()
    if _IB_ID.fullmatch(v):
        return True
    if _COL_ID.fullmatch(v) and sum(c.isdigit() for c in v) >= 5 and not _DATE8.fullmatch(v):
        return True
    return False


def _collect_ids(lines: List[str]) -> List[str]:
    ids: List[str] = []
    seen = set()

    def add(v: str) -> None:
        v = v.strip()
        if v and v not in seen and _is_id(v):
            seen.add(v); ids.append(v)

    col_idx: Dict[str, List[int]] = {}
    for line in lines:
        for m in _IB_ID.finditer(line):
            add(m.group(0))
        for m in _ACCOUNT_PHRASE.finditer(line):
            add(m.group(2))
        cells = _split(line.rstrip("\r\n"))
        if not cells:
            continue
        low = [c.strip().lower() for c in cells]
        if len(low) > 1 and low[1] == "header":               # IB sections
            col_idx[low[0]] = [i for i, c in enumerate(low) if c in _ACCOUNT_COLS]
            continue
        if any(c in _ACCOUNT_COLS for c in low) and "data" not in low[:2]:
            col_idx["__flat__"] = [i for i, c in enumerate(low) if c in _ACCOUNT_COLS]
            continue
        if len(low) > 1 and low[1] == "data":
            for i in col_idx.get(low[0], []):
                if i < len(cells):
                    add(cells[i])
            if low[0] == "account information" and len(cells) > 3 \
                    and low[2] in ("account", "accounts included"):
                for tok in re.split(r"[,\s]+", cells[3]):
                    add(tok)
        else:
            for i in col_idx.get("__flat__", []):
                if i < len(cells):
                    add(cells[i])
    return ids


def _replace_field(line: str, cells: List[str], idx: int, value: str) -> str:
    """Replace one CSV field in the RAW line, leaving every other byte
    (quoting, spacing, line ending) as it was."""
    old = cells[idx]
    if not old:
        return line
    # The field may or may not be quoted in the raw text; try both.
    for form in (f'"{old}"', old):
        pos = line.find(form)
        if pos >= 0:
            rep = f'"{value}"' if form.startswith('"') else value
            return line[:pos] + rep + line[pos + len(form):]
    return line


def compile_patterns(pats: List[str]) -> Tuple[List[re.Pattern], List[str]]:
    good, bad = [], []
    for p in pats:
        try:
            good.append(re.compile(p, re.IGNORECASE))
        except re.error as e:
            bad.append(f"{p!r}: {e}")
    return good, bad


def redact_text(text: str, extra_patterns: Optional[List[str]] = None
                ) -> Tuple[str, Report]:
    rep = Report()
    compiled, bad = compile_patterns(extra_patterns or [])
    for b in bad:
        rep.notes.append(f"pattern skipped (not a valid regex): {b}")
    lines = text.splitlines(keepends=True)
    for n, orig in enumerate(_collect_ids(lines), start=1):
        rep.accounts[orig] = _placeholder(orig, n)
    # Longest first so a shorter id that is a substring of a longer one
    # cannot pre-empt it.
    ordered = sorted(rep.accounts.items(), key=lambda kv: -len(kv[0]))
    alias_cols: Dict[str, List[int]] = {}
    out: List[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        cells = _split(body)
        low = [c.strip().lower() for c in cells] if cells else []
        if low and len(low) > 1 and low[1] == "header":
            if "description" in low[2:] and low[0] not in rep.description_columns:
                rep.description_columns.append(low[0])
            alias_cols[low[0]] = [i for i, c in enumerate(low) if c in _ALIAS_COLS]
        elif low and any(c in _ALIAS_COLS for c in low) and "data" not in low[:2]:
            alias_cols["__flat__"] = [i for i, c in enumerate(low) if c in _ALIAS_COLS]
        # Identity rows (IB Account Information) and alias columns.
        if (low and len(low) > 3 and low[0] == "account information"
                and low[1] == "data" and low[2] in _IDENTITY_ROWS and cells[3].strip()):
            line = _replace_field(line, cells, 3, "REDACTED")
            rep.identity_rows += 1
        elif low:
            sect = low[0] if len(low) > 1 and low[1] == "data" else "__flat__"
            for i in alias_cols.get(sect, []):
                if i < len(cells) and cells[i].strip() and not _is_id(cells[i]):
                    line = _replace_field(line, cells, i, "REDACTED")
                    rep.identity_rows += 1
        m = _HEADER_LINE.match(line)
        if m and not (low and len(low) > 1 and low[1] in ("header", "data")):
            rest = line[m.end():]
            if m.group(1).strip().endswith('"'):
                new_rest = re.sub(r'^[^"\r\n]*', "REDACTED", rest, count=1)
            else:
                new_rest = re.sub(r'^[^,\r\n]*', "REDACTED", rest, count=1)
            line = line[:m.end()] + new_rest
            rep.identity_rows += 1
        for orig, ph in ordered:
            if orig in line:
                line = re.sub(r"(?<![A-Za-z0-9.])" + re.escape(orig) + r"(?![A-Za-z0-9]|\.\d)", ph, line)
        if _EMAIL.search(line):
            line, k = _EMAIL.subn("redacted@example.com", line)
            rep.emails += k
        for pat in compiled:
            line, k = pat.subn("REDACTED", line)
            rep.patterns += k
        out.append(line)
    return "".join(out), rep


def decode_export(raw: bytes) -> Tuple[str, str, bytes]:
    """(text, encoding-name, bom-to-restore). UTF-16 with or without a
    BOM, else strict UTF-8 (BOM kept), else cp1252."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16", b""
    head = raw[:4096]
    if b"\x00" in head:
        enc = "utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"
        return raw.decode(enc, errors="replace"), enc, b""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    try:
        return raw[len(bom):].decode("utf-8"), "utf-8", bom
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252", b""


def load_denylist(path: Optional[str] = None) -> List[str]:
    p = Path(path or os.environ.get("TAXJSON_PII_DENYLIST")
             or Path.home() / ".config" / "taxjson" / "pii-denylist")
    if not p.is_file():
        return []
    pats = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            pats.append(s)
    return pats


def redacted_name(src: Path, accounts: Dict[str, str]) -> str:
    stem = src.stem
    for orig, ph in sorted(accounts.items(), key=lambda kv: -len(kv[0])):
        stem = stem.replace(orig, ph)
    # Ids that appear only in the name (IB names downloads after the account).
    n = len(accounts)
    for m in list(_IB_ID.finditer(stem)) + list(re.finditer(r"(?<!\d)\d{8,}(?!\d)", stem)):
        tok = m.group(0)
        if _DATE8.fullmatch(tok) or tok in accounts.values():
            continue
        n += 1
        stem = stem.replace(tok, _placeholder(tok, n))
    return f"{stem}.redacted{src.suffix}"


def redact_file(src: Path, out_dir: Optional[Path], extra: List[str],
                check_only: bool, force: bool = False
                ) -> Tuple[Optional[Path], Report]:
    raw = src.read_bytes()
    text, enc, bom = decode_export(raw)
    new, rep = redact_text(text, extra)
    rep.encoding = enc
    if enc != "utf-8":
        rep.notes.append(f"input was {enc}; the copy is written as UTF-8 "
                         f"(the strict UTF-8 parsers would refuse the original — say so in the report)")
    if check_only:
        return None, rep
    dst_dir = out_dir or src.parent
    if dst_dir.exists() and not dst_dir.is_dir():
        raise SystemExit(f"taxjson redact: --out {dst_dir} is not a directory")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / redacted_name(src, rep.accounts)
    if dst.resolve() == src.resolve():
        raise SystemExit(f"taxjson redact: refusing to overwrite {src}")
    if dst.is_symlink():
        raise SystemExit(f"taxjson redact: {dst} is a symlink — refusing to write through it")
    if dst.exists() and not force:
        raise SystemExit(f"taxjson redact: {dst} exists (use --force to overwrite)")
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        if bom:
            fh.write("﻿")
        fh.write(new)
    return dst, rep


def print_report(src: Path, dst: Optional[Path], rep: Report) -> None:
    shown_src = redacted_name(src, rep.accounts).replace(".redacted", "")
    where = f" -> {dst}" if dst else " (check only)"
    print(f"{shown_src}{where}")
    if rep.accounts:
        print(f"  account ids: {len(rep.accounts)} distinct, every occurrence replaced")
        for orig, ph in rep.accounts.items():
            print(f"    {Report.masked(orig)} -> {ph}")
    else:
        print("  account ids: none found")
    print(f"  identity rows/cells (name/alias/address): {rep.identity_rows}")
    print(f"  e-mail addresses: {rep.emails}")
    if rep.patterns:
        print(f"  denylist / --also matches: {rep.patterns}")
    for n in rep.notes:
        print(f"  NOTE: {n}")
    if rep.description_columns:
        print(f"  NOTE: free-text Description columns ({', '.join(rep.description_columns)}) "
              f"are kept verbatim — read them once for names before sharing.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="taxjson redact",
        description="Strip account numbers and identity from broker exports, keeping row shapes.")
    ap.add_argument("files", nargs="+", metavar="FILE")
    ap.add_argument("--out", metavar="DIR", help="Write redacted copies here (default: beside each input)")
    ap.add_argument("--also", action="append", default=[], metavar="REGEX",
                    help="Extra pattern to replace with REDACTED (repeatable, case-insensitive)")
    ap.add_argument("--no-denylist", action="store_true",
                    help="Ignore ~/.config/taxjson/pii-denylist")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing redacted copy")
    ap.add_argument("--check", action="store_true", help="Report only; write nothing")
    args = ap.parse_args(argv)
    extra = list(args.also) + ([] if args.no_denylist else load_denylist())
    _, bad = compile_patterns(extra)
    for b in bad:
        print(f"taxjson redact: warning: pattern skipped (not a valid regex): {b}", file=sys.stderr)
    rc = 0
    for f in args.files:
        src = Path(f)
        if not src.is_file():
            print(f"taxjson redact: {f}: not a file", file=sys.stderr); rc = 1; continue
        if src.stem.endswith(".redacted"):
            print(f"taxjson redact: {f}: already a redacted copy — skipped", file=sys.stderr); continue
        dst, rep = redact_file(src, Path(args.out) if args.out else None, extra, args.check, args.force)
        print_report(src, dst, rep)
    return rc


if __name__ == "__main__":
    sys.exit(main())
