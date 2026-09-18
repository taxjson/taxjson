"""`taxjson redact FILE...` — strip account numbers, names and addresses
from a broker export while keeping every row shape, so a real statement
can be shared as a parser sample or attached to a bug report.

What it changes, and nothing else:
  * account identifiers — IB `U1234567`, Questrade `Account #`, IB
    `Xfer Account`, RBC `"Account: 12345678 - Margin"` headers, any
    `account … <digits>` phrase — each distinct id becomes a stable
    placeholder of the SAME length (`U9990001`, `99900001`, …) and every
    occurrence in the file, descriptions included, is replaced;
  * holder identity — IB `Account Information` Name / Alias / address
    rows, `Name:` / `Client:` header lines, e-mail addresses;
  * anything matching the private denylist (`~/.config/taxjson/
    pii-denylist`, the same file scripts/check-pii.sh uses) or `--also`.
Quantities, prices, dates, symbols, descriptions and section layout are
untouched, so the redacted file parses exactly like the original.

Output goes to `<name>.redacted<ext>` beside the input (or --out DIR);
the input is never modified. Descriptions are free text a broker fills
in — the report reminds you to read them once before sharing.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_IB_ID = re.compile(r"\bU\d{7,8}\b")
_ACCOUNT_PHRASE = re.compile(r"(account(?:\s*#|\s+number|\s+no\.?)?\s*[:#]?\s*)(\d{5,12})\b",
                             re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACCOUNT_COLS = ("account #", "account", "xfer account", "account number",
                 "account no", "acct", "account id")
_IDENTITY_ROWS = ("name", "account alias", "address", "street", "city",
                  "state", "postal code", "zip", "country", "phone",
                  "email", "customer id", "client name", "holder")


class Report:
    def __init__(self) -> None:
        self.accounts: Dict[str, str] = {}      # original -> placeholder
        self.identity_rows = 0
        self.emails = 0
        self.patterns = 0
        self.description_columns: List[str] = []

    def masked(self, s: str) -> str:
        return s[:2] + "*" * max(0, len(s) - 4) + s[-2:] if len(s) > 4 else "*" * len(s)


def _placeholder(orig: str, n: int) -> str:
    """Same shape and length as the original id: U9990001, 99900001,
    9990000012 — so parsers keep matching the column."""
    # 9990-prefixed, zero-padded counter: the shape scripts/check-pii.sh
    # recognises as synthetic (99900001, U99900012, 9990000003).
    if orig[:1].upper() == "U" and orig[1:].isdigit():
        width = max(len(orig) - 1, 5)
        return "U9990" + str(n).zfill(width - 4)
    width = max(len(orig), 5)
    return "9990" + str(n).zfill(width - 4)


def _split_csv_line(line: str) -> Optional[List[str]]:
    import csv
    import io
    try:
        rows = list(csv.reader(io.StringIO(line)))
    except csv.Error:
        return None
    return rows[0] if rows else []


def _collect_ids(lines: List[str]) -> List[str]:
    ids: List[str] = []
    seen = set()

    def add(v: str) -> None:
        v = v.strip()
        if v and v not in seen and (v.isdigit() or _IB_ID.fullmatch(v)):
            seen.add(v); ids.append(v)

    col_idx: Dict[str, List[int]] = {}
    for line in lines:
        for m in _IB_ID.finditer(line):
            add(m.group(0))
        for m in _ACCOUNT_PHRASE.finditer(line):
            add(m.group(2))
        cells = _split_csv_line(line)
        if not cells:
            continue
        low = [c.strip().lower() for c in cells]
        # IB multi-section: `<Section>,Header,<cols…>`; flat CSVs: the
        # first row is the header.
        if len(low) > 1 and low[1] == "header":
            sect = low[0]
            col_idx[sect] = [i for i, c in enumerate(low) if c in _ACCOUNT_COLS]
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


def redact_text(text: str, extra_patterns: Optional[List[str]] = None
                ) -> Tuple[str, Report]:
    rep = Report()
    lines = text.splitlines(keepends=True)
    for n, orig in enumerate(_collect_ids(lines), start=1):
        rep.accounts[orig] = _placeholder(orig, n)
    out: List[str] = []
    header_cols: Dict[str, List[str]] = {}
    for line in lines:
        cells = _split_csv_line(line.rstrip("\r\n"))
        low = [c.strip().lower() for c in cells] if cells else []
        if low and len(low) > 1 and low[1] == "header":
            header_cols[low[0]] = low
            if "description" in low[2:] and low[0] not in rep.description_columns:
                rep.description_columns.append(low[0])
        # Identity rows in IB's Account Information section, and
        # "Name: …" / "Client: …" header lines in flat exports.
        if (low and len(low) > 3 and low[0] == "account information"
                and low[1] == "data" and low[2] in _IDENTITY_ROWS):
            import csv, io
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="")
            w.writerow(cells[:3] + ["REDACTED"] + cells[4:])
            line = buf.getvalue() + ("\n" if line.endswith("\n") else "")
            rep.identity_rows += 1
        elif re.match(r'^\s*"?(name|client|client name|account holder)\s*:', line, re.I):
            line = re.sub(r'(:\s*)[^",\r\n]+', r"\1REDACTED", line, count=1)
            rep.identity_rows += 1
        for orig, ph in rep.accounts.items():
            if orig in line:
                line = re.sub(r"(?<![\w])" + re.escape(orig) + r"(?![\w])", ph, line)
        if _EMAIL.search(line):
            line, k = _EMAIL.subn("redacted@example.com", line)
            rep.emails += k
        for pat in extra_patterns or []:
            line, k = re.subn(pat, "REDACTED", line)
            rep.patterns += k
        out.append(line)
    return "".join(out), rep


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


def redact_file(src: Path, out_dir: Optional[Path], extra: List[str],
                check_only: bool) -> Tuple[Optional[Path], Report]:
    text = src.read_text(encoding="utf-8-sig", errors="replace")
    new, rep = redact_text(text, extra)
    if check_only:
        return None, rep
    dst_dir = out_dir or src.parent
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{src.stem}.redacted{src.suffix}"
    if dst.resolve() == src.resolve():
        raise SystemExit(f"taxjson redact: refusing to overwrite {src}")
    dst.write_text(new, encoding="utf-8")
    return dst, rep


def print_report(src: Path, dst: Optional[Path], rep: Report) -> None:
    where = f" -> {dst}" if dst else " (check only)"
    print(f"{src.name}{where}")
    if rep.accounts:
        print(f"  account ids: {len(rep.accounts)} distinct, every occurrence replaced")
        for orig, ph in rep.accounts.items():
            print(f"    {rep.masked(orig)} -> {ph}")
    else:
        print("  account ids: none found")
    print(f"  identity rows (name/alias/address): {rep.identity_rows}")
    print(f"  e-mail addresses: {rep.emails}")
    if rep.patterns:
        print(f"  denylist / --also matches: {rep.patterns}")
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
                    help="Extra pattern to replace with REDACTED (repeatable)")
    ap.add_argument("--no-denylist", action="store_true",
                    help="Ignore ~/.config/taxjson/pii-denylist")
    ap.add_argument("--check", action="store_true", help="Report only; write nothing")
    args = ap.parse_args(argv)
    extra = list(args.also) + ([] if args.no_denylist else load_denylist())
    rc = 0
    for f in args.files:
        src = Path(f)
        if not src.is_file():
            print(f"taxjson redact: {f}: not a file", file=sys.stderr); rc = 1; continue
        if ".redacted" in src.suffixes or src.stem.endswith(".redacted"):
            print(f"taxjson redact: {f}: already a redacted copy — skipped", file=sys.stderr); continue
        dst, rep = redact_file(src, Path(args.out) if args.out else None, extra, args.check)
        print_report(src, dst, rep)
    return rc


if __name__ == "__main__":
    sys.exit(main())
