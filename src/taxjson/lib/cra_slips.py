"""T5 / T3 slips downloaded from CRA My Account ("Tax information
slips"), one PDF per slip, read into slips.toml tables for `taxjson
slip-audit --import-cra` (tax-logic CA-SLIP-04).

CRA prints every issuer's slip in one layout (pdftotext -layout):

    2025 T5 slip (original) from SAMPLE BROKERAGE INC.
        Box
                     Box name                                 Box value
        number
        13           Interest from Canadian sources               12.34
                     Actual amount of dividends other than
        10                                                         0.00
                     eligible dividends
        27           Foreign currency                               CAD
      Other information
        Box number   Box name                                 Box value
        15           Foreign income                               40.00

Only three things are read: the slip line (year, T5 or T3, original or
amended, the issuer — a company), the box rows (a known amount box of
the slip's type and its amount; box 27's currency code) and nothing
else. The page also carries the recipient's name and other personal
details: no line but those is kept, so they are never read into
anything, stored or printed. The CRA copy shows no account number.

An imported slip is given to a project account by the books: the
issuer's name against the brokers' names (lib/brokerages/detect
DISPLAY_NAMES), then the slips of one broker are shared out among its
broker accounts (the books' hashed keys) so that the slips' dividends,
withholding and interest are closest to each account's; a T3 goes to
the fund (and broker account) whose distributions match it by name
and amount. What cannot be placed is listed, not guessed.
"""
from __future__ import annotations

import itertools
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SLIP_TITLES = {"T5": "Statement of Investment Income",
               "T3": "Statement of Trust Income"}
# The amount boxes of each slip (the boxes CRA prints with money).
AMOUNT_BOXES = {
    "T5": ("10", "11", "12", "13", "14", "15", "16", "17", "18", "19",
           "20", "24", "25", "26", "30"),
    "T3": ("21", "22", "23", "24", "25", "26", "30", "31", "32", "33",
           "34", "35", "37", "38", "39", "40", "41", "42", "45", "46",
           "47", "48", "49", "50", "51"),
}
CURRENCY_BOX = "27"

_FROM_RE = re.compile(r"^\s*(\d{4})\s+(T5|T3)\s+slip\s+\(([a-z ]+)\)\s+"
                      r"from\s+(\S.*?)\s*$")
_ROW_RE = re.compile(r"^\s*(\d{1,2})\s{2,}(?:\S.*?\s{2,})?(\S+)\s*$")
_HEAD_RE = re.compile(r"^\s*Box(?:\s+number)?(?:\s{2,}|$)")
_AMOUNT_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}$")
# Words of an issuer's or a broker's name that tell nothing apart.
_GENERIC = {"INC", "LTD", "LTEE", "CORP", "CORPORATION", "COMPANY", "THE",
            "AND", "OF", "DE", "DU", "LA", "LES", "ET", "CANADA", "CANADIAN",
            "SERVICES", "INVESTMENT", "INVESTMENTS", "FUND", "FUNDS", "ETF",
            "INDEX", "TRUST", "LIMITED", "FINANCIAL", "SECURITIES", "PLC",
            "INC.", "LTD."}


class CraSlipError(ValueError):
    """A PDF that cannot be read as a CRA T5 / T3 slip."""


@dataclass
class CraSlip:
    shown: str                    # the file's shown name (ids masked)
    year: int
    type: str                     # T5 | T3
    status: str                   # original | amended | ...
    issuer: str
    currency: str = "CAD"
    boxes: Dict[str, float] = field(default_factory=dict)

    def total(self, boxes) -> float:
        return sum(self.boxes.get(b, 0.0) for b in boxes)


def parse_text(text: str, shown: str = "slip") -> CraSlip:
    """A CRA slip page's text (pdftotext -layout). Raises CraSlipError
    when it is not a T5 or T3 slip."""
    lines = text.splitlines()
    head = None
    for i, ln in enumerate(lines):
        m = _FROM_RE.match(ln)
        if m:
            head = (i, m)
            break
    if head is None:
        raise CraSlipError(f"{shown}: not a CRA T5 or T3 slip (no "
                           f"'YYYY T5 slip (original) from ...' line)")
    i, m = head
    issuer = [m.group(4)]
    j = i + 1
    while j < len(lines) and lines[j].strip() and not _HEAD_RE.match(
            lines[j]) and not _ROW_RE.match(lines[j]):
        issuer.append(lines[j].strip())
        j += 1
    typ = m.group(2)
    slip = CraSlip(shown=shown, year=int(m.group(1)), type=typ,
                   status=m.group(3).strip(),
                   issuer=" ".join(" ".join(issuer).split()))
    for ln in lines[j:]:
        r = _ROW_RE.match(ln)
        if not r:
            continue
        box = r.group(1).lstrip("0") or "0"
        val = r.group(2)
        if box == CURRENCY_BOX and typ == "T5" \
                and re.fullmatch(r"[A-Z]{3}", val):
            slip.currency = val
        elif box in AMOUNT_BOXES[typ] and _AMOUNT_RE.match(val):
            slip.boxes[box] = float(val.replace(",", ""))
    if not slip.boxes:
        raise CraSlipError(f"{shown}: a {typ} slip with no box amounts")
    return slip


def pdf_text(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe is None:
        raise CraSlipError("pdftotext is not installed (poppler-utils: "
                           "`sudo apt install poppler-utils`, `brew "
                           "install poppler`); it reads the CRA PDFs")
    try:
        p = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CraSlipError(f"pdftotext failed on {path.name}: {e}") from None
    if p.returncode != 0:
        raise CraSlipError(f"pdftotext could not read it (exit "
                           f"{p.returncode})")
    return p.stdout.decode("utf-8", errors="replace")


def read_pdf(path: Path) -> CraSlip:
    from taxjson.lib.brokerages.base import shown_name
    shown = shown_name(path)
    try:
        text = pdf_text(Path(path))
    except CraSlipError as e:
        raise CraSlipError(f"{shown}: {e}") from None
    return parse_text(text, shown)


def pdf_paths(args: List[str]) -> List[Path]:
    """The PDFs named, a folder's *.pdf (not recursive)."""
    out: List[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out += sorted((q for q in p.iterdir()
                           if q.is_file() and q.suffix.lower() == ".pdf"),
                          key=lambda q: q.name.lower())
        elif p.is_file():
            out.append(p)
        else:
            raise CraSlipError(f"{a}: no such file or folder")
    return out


# ------------------------------------------------------------ placing
def _words(text: str) -> set:
    return {w for w in re.findall(r"[A-Z0-9]+", str(text).upper())
            if len(w) >= 2 and w not in _GENERIC}


def broker_of_issuer(issuer: str) -> Optional[str]:
    """The broker id whose display name shares a word with the issuer
    (INTERACTIVE BROKERS ... -> ib); None when none or several."""
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    iw = _words(issuer)
    hits = [b for b, name in DISPLAY_NAMES.items()
            if b != "generic" and _words(name) & iw]
    return hits[0] if len(hits) == 1 else None


@dataclass
class Group:
    """One broker account's book rows in one project account."""
    account: str
    hashes: Tuple[str, ...]
    broker: Optional[str]
    sources: Tuple[str, ...]
    rows: List[Any] = field(default_factory=list)


def _row_broker(acct: str, base: dict) -> Dict[str, str]:
    """{input file: broker id} from a base book's metadata (the parse
    file `<account>_<broker>.json` lists its input files)."""
    out: Dict[str, str] = {}
    meta = base.get("metadata") if isinstance(base, dict) else None
    for e in (meta or {}).get("sources") or []:
        if not isinstance(e, dict):
            continue
        f = Path(str(e.get("file") or "")).name
        if not (f.startswith(f"{acct}_") and f.endswith(".json")):
            continue
        broker = f[len(acct) + 1:-len(".json")]
        om = e.get("original_metadata") or {}
        for src in (om.get("source_accounts") or {}):
            out[str(src)] = broker
    return out


def book_groups(root: Path, cfg: Dict[str, Any], year: int
                ) -> List[Group]:
    """The broker accounts in the books of every slip account: rows that
    share a broker-account key (an IB statement of two accounts joins
    them)."""
    import json
    from taxjson.lib import slip_audit as SA
    books, _p = SA.load_books(root, cfg, year, SA.slip_accounts(cfg))
    groups: List[Group] = []
    for acct in SA.slip_accounts(cfg):
        try:
            base = json.loads((Path(root) / "work" / f"{acct}_base.json")
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            base = {}
        brk = _row_broker(acct, base)
        parent: Dict[str, str] = {}

        def find(h: str) -> str:
            while parent.setdefault(h, h) != h:
                h = parent[h]
            return h
        rows = [r for r in books if r.account == acct and r.hashes]
        for r in rows:
            for h in r.hashes[1:]:
                parent[find(h)] = find(r.hashes[0])
        by: Dict[str, Group] = {}
        for r in rows:
            k = find(r.hashes[0])
            g = by.setdefault(k, Group(acct, (), None, ()))
            g.rows.append(r)
            g.hashes = tuple(sorted(set(g.hashes) | set(r.hashes)))
            g.sources = tuple(sorted(set(g.sources) | {r.source}))
            b = brk.get(r.source)
            if b and g.broker is None:
                g.broker = b
        groups += [by[k] for k in sorted(by)]
    return groups


def _cad(amount: float, cur: str, rate: Dict[str, Optional[float]]
         ) -> Optional[float]:
    if cur == "CAD":
        return amount
    r = rate.get(cur)
    return None if r is None else amount * r


@dataclass
class Placement:
    slip: CraSlip
    account: Optional[str] = None
    group: Optional[Group] = None
    key: str = ""                  # the broker-account key written
    security: str = ""             # a T3's fund, as the books spell it
    how: str = ""                  # how it was placed, or why not


_T3_INCOME = ("21", "23", "24", "25", "26", "49")
_T5_DIVS = ("10", "15", "18", "24")


def place(slips: List[CraSlip], groups: List[Group], year: int,
          rate: Dict[str, Optional[float]], ib_reports: List[Any] = (),
          account: Optional[str] = None) -> List[Placement]:
    """Give each slip a project account (and broker account, and a T3
    its fund) from the books; `account` places the slips no broker
    name or match places."""
    ystr = str(year)
    out = [Placement(s) for s in slips]
    t3_roots: Dict[Tuple[str, Tuple[str, ...]], set] = {}
    # T3s: the fund whose distributions match by name and amount.
    for pl in out:
        s = pl.slip
        if s.type != "T3":
            continue
        want_all = s.total(_T3_INCOME + ("42",))
        want_inc = s.total(_T3_INCOME)
        iw = _words(s.issuer)
        cands = []
        for g in groups:
            if account and g.account != account:
                continue
            roots: Dict[str, List[Any]] = {}
            for r in g.rows:
                if r.category in ("ca_div", "foreign") and r.root \
                        and r.tax_date.startswith(ystr):
                    roots.setdefault(r.root, []).append(r)
            for rt, rs in roots.items():
                have = sum(r.cad for r in rs)
                d = min(abs(have - want_all), abs(have - want_inc)) \
                    / max(want_all, 1.0)
                desc = set()
                for r in rs:
                    desc |= _words(getattr(r, "description", "") or "")
                name = (len(iw & desc) / len(iw)) if iw else 0.0
                cands.append((d, -name, g, rs[0].symbol, rt))
        good = [c for c in cands if c[0] <= 0.02 or (-c[1] >= 0.6
                                                     and c[0] <= 0.25)]
        good.sort(key=lambda c: (c[0] > 0.02, c[1], c[0]))
        if not good:
            pl.how = ("no fund in the books whose distributions match "
                      "its amounts or name")
            continue
        if len(good) > 1 and good[1][0] <= 0.02 and good[0][0] <= 0.02 \
                and abs(good[0][0] - good[1][0]) < 0.002 \
                and good[0][1] == good[1][1]:
            pl.how = (f"ambiguous: {good[0][3]} and {good[1][3]} "
                      f"distributions match it equally")
            continue
        d, nm, g, sym, rt = good[0]
        pl.group, pl.account, pl.security = g, g.account, sym
        pl.how = (f"fund {sym}: its distributions in the books "
                  f"{'match the amounts' if d <= 0.02 else 'match the name'}")
        t3_roots.setdefault((g.account, g.hashes), set()).add(rt)
    # T5s: by broker, shared out among the broker's accounts.
    by_broker: Dict[Optional[str], List[Placement]] = {}
    for pl in out:
        if pl.slip.type == "T5":
            by_broker.setdefault(broker_of_issuer(pl.slip.issuer),
                                 []).append(pl)
    for broker, pls in by_broker.items():
        gs = [g for g in groups if broker and g.broker == broker
              and (not account or g.account == account)]
        if not gs:
            for pl in pls:
                if account:
                    pl.account = account
                    pl.how = (f"account {account}, as asked (no broker "
                              f"account of the issuer in the books)")
                else:
                    pl.how = ("no broker in the books by the issuer's "
                              "name — import it again naming its account "
                              "(`taxjson slip-audit ACCOUNT --import-cra "
                              "FILE`), or it is outside the books (a bank "
                              "account)")
            continue
        books = []
        for g in gs:
            skip = t3_roots.get((g.account, g.hashes), set())
            rs = [r for r in g.rows if r.tax_date.startswith(ystr)
                  and r.root not in skip]
            books.append((
                sum(r.cad for r in rs if r.category in
                    ("ca_div", "foreign")),
                sum(r.cad for r in rs if r.category == "foreign_tax"),
                sum(r.cad for r in rs if r.category == "interest")))
        cad = []
        for pl in pls:
            s = pl.slip
            vals = [_cad(s.total(_T5_DIVS), s.currency, rate),
                    _cad(s.boxes.get("16", 0.0), s.currency, rate),
                    _cad(s.boxes.get("13", 0.0), s.currency, rate)]
            cad.append([v if v is not None else 0.0 for v in vals])
        best = None
        if len(gs) ** len(pls) <= 20000:
            for combo in itertools.product(range(len(gs)), repeat=len(pls)):
                cost = 0.0
                for gi in set(combo):
                    tot = [sum(cad[k][j] for k, c in enumerate(combo)
                               if c == gi) for j in range(3)]
                    cost += sum(abs(tot[j] - books[gi][j]) for j in range(3))
                if best is None or cost < best[0] - 1e-6:
                    best = (cost, combo)
        if best is None:
            for pl in pls:
                pl.how = "too many broker accounts to share the slips out"
            continue
        for pl, gi in zip(pls, best[1]):
            g = gs[gi]
            pl.group, pl.account = g, g.account
            pl.how = (f"{broker} broker account by its payments"
                      if len(gs) > 1 else f"the {broker} broker account")
    # The key written: one hash of the group; an IB report's account
    # whose T5 matches the slip, when the group holds several.
    for pl in out:
        g = pl.group
        if g is None:
            continue
        pl.key = g.hashes[0] if g.hashes else ""
        if len(g.hashes) > 1:
            for rep in ib_reports:
                if rep.account_hash not in g.hashes:
                    continue
                if pl.slip.type == "T5":
                    got = sum(c.gross_base for p in rep.payments
                              for c in p.components if c.slip == "T5"
                              and c.category in ("eligible",
                                                 "non_eligible"))
                    want = pl.slip.total(("24", "10"))
                    if want and abs(got - want) <= max(1.0, 0.005 * want):
                        pl.key = rep.account_hash
                        break
                else:
                    from taxjson.lib.slip_audit import _root
                    if any(_root(p.symbol) == _root(pl.security)
                           and any(c.slip == "T3" for c in p.components)
                           for p in rep.payments):
                        pl.key = rep.account_hash
                        break
    return out


# ------------------------------------------------------------ writing
def table(pl: Placement) -> str:
    """The slips.toml [[slip]] table of a placed slip."""
    s = pl.slip
    boxes = ", ".join(f"{b} = {v:.2f}" for b, v in
                      sorted(s.boxes.items(), key=lambda kv: int(kv[0]))
                      if abs(v) >= 0.005)
    lines = ["[[slip]]", f'type = "{s.type}"',
             f'issuer = "{_toml_str(s.issuer)}"',
             f'account = "{pl.account}"', f'currency = "{s.currency}"']
    if pl.key:
        lines.append(f'broker_key = "{pl.key}"   # '
                     + ", ".join(pl.group.sources if pl.group else ()))
    if pl.security:
        lines.append(f'security = "{pl.security}"')
    lines.append(f'source = "cra:{_toml_str(s.shown)}"')
    lines.append(f"boxes = {{ {boxes} }}")
    return "\n".join(lines)


def _toml_str(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')
