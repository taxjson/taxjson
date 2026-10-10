"""`taxjson slip-audit`: the broker's T5 / T3 slips against the books'
income, per account and slip box (Canada; tax-logic CA-SLIP-01..03).

The slips come from inputs/slips/:

- `slips.toml`, typed from the PDFs (`taxjson slip-audit --template`
  prints one per account) — one `[[slip]]` table per slip:

      [[slip]]
      type = "T5"               # T5 | T3 | T5008
      issuer = "RBC"            # a label; never a name
      account = "margin"        # the taxjson account the payments are in
      currency = "USD"          # the slip's currency (box 27 / its column)
      broker_account = "..."    # optional: the account number on the slip,
                                # only to tell two broker accounts of one
                                # label apart (hashed; never printed)
      security = "XYZQ.TO"      # optional: the one fund a T3 is for
      boxes = { 24 = 634.90, 15 = 12.00, 16 = 1.80 }

      [[slip.line]]             # optional per-security lines (an RBC
      symbol = "ABCQ.TO"        # summary, a split-corp page)
      date = 2025-03-14         # optional
      box = "18"
      amount = 5.50

- Interactive Brokers' dividends reports (`U*.YYYY.dividends.csv`,
  lib/ib_dividends): read as that account's T5 and its T3s, payment by
  payment; the report is matched to the project account whose books
  carry the IB account (the books keep a hash of it), or named in
  `[[ib_report]] file = "..." account = "margin"`.

The comparison, per account (and per broker account where the slips
name one) and per slip currency:

    category                 slip boxes              books
    Canadian dividends       T5 24+10, T3 49+23      dividends and payments in
                                                     lieu of a Canadian issuer
    Capital-gains dividends  T5 18                   [[capital_gains_dividends]]
    Trust capital gains      T3 21                   (split as the T3 does)
    Foreign income           T5 15, T3 24+25         a foreign issuer's
    Other income             T5 14, T3 26            (split as the T3 does)
    Foreign tax withheld     T5 16, T3 33+34         TAX rows
    Return of capital        T3 42                   ADJUST rows (roc-sum)
    Interest                 T5 13                   INTEREST credits

A slip in CAD is compared with the books' CAD (each payment at the Bank
of Canada rate of its date, CA-FX-01), and the payments converted from
another currency are also shown at the year's average rate (the mean
of the Bank's daily rates for the year in the FX cache), saying which
of the two the slip's figure is closer to. A slip in another currency
is compared in that currency; both CAD conversions are shown.

Nothing here changes a figure: the report lists what differs and the
exact lines (a [[capital_gains_dividends]] entry, ROC .tt lines) the
user may apply.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from taxjson.lib import project_layout as _PL

SCHEMA_VERSION = 1
SLIPS_DIR = ("inputs", "slips")
SLIPS_FILE = "slips.toml"
DEFAULT_TOLERANCE = 1.00
# The broker converts a foreign payment at its own rate, not the Bank's:
# a CAD box holding converted money is allowed this share of the
# converted part on top of the tolerance.
FX_BAND = 0.005
# No T5 is issued for less than $50 of interest in a year.
INTEREST_SLIP_MIN = 50.0
# A payment and its book row are the same payment when their dates are
# this close (a broker's report date vs the export's posting date).
MATCH_DAYS = 7
SUGGEST_TT = "slip-audit.tt"

CATEGORIES = ("ca_div", "cg_div", "trust_cg", "foreign", "other",
              "foreign_tax", "roc", "interest")
CAT_WORDS = {
    "ca_div": "Canadian dividends",
    "cg_div": "capital-gains dividends",
    "trust_cg": "trust capital gains",
    "foreign": "foreign income",
    "other": "other income",
    "foreign_tax": "foreign tax withheld",
    "roc": "return of capital",
    "interest": "interest",
}
CAT_LABEL = {
    "ca_div": "Canadian dividends",
    "cg_div": "Capital-gains dividends",
    "trust_cg": "Trust capital gains",
    "foreign": "Foreign income",
    "other": "Other income",
    "foreign_tax": "Foreign tax withheld",
    "roc": "Return of capital",
    "interest": "Interest",
}
# The categories the books cannot tell apart from a trust's
# distribution: taken from the T3's own split (CA-SLIP-01).
SPLIT_CATS = ("ca_div", "trust_cg", "foreign", "other")
INCOME_CATS = ("ca_div", "cg_div", "trust_cg", "foreign", "other")

BOX_CAT: Dict[str, Dict[str, str]] = {
    "T5": {"24": "ca_div", "10": "ca_div", "18": "cg_div",
           "15": "foreign", "16": "foreign_tax", "13": "interest",
           "14": "other"},
    "T3": {"49": "ca_div", "23": "ca_div", "21": "trust_cg",
           "24": "foreign", "25": "foreign", "26": "other",
           "33": "foreign_tax", "34": "foreign_tax", "42": "roc"},
    "T5008": {"20": "cost", "21": "proceeds", "16": "quantity"},
}
# Boxes a slip carries that taxjson does not compare: amounts derived
# from others (the grossed-up taxable dividends, the credits) or ones
# the books never hold.
OTHER_BOXES: Dict[str, Tuple[str, ...]] = {
    "T5": ("11", "12", "17", "19", "20", "25", "26", "30"),
    "T3": ("22", "30", "31", "32", "35", "37", "38", "39", "40", "41",
           "45", "46", "47", "48", "50", "51"),
    "T5008": ("19",),
}
# Boxes that hold identifiers, not money: never typed into slips.toml.
ID_BOXES: Dict[str, Tuple[str, ...]] = {
    "T5": ("21", "22", "23", "27", "28", "29"),
    "T3": ("12", "14", "16", "18"),
    "T5008": ("12", "13", "14", "15", "17", "18"),
}
CAT_BOXES = {
    "ca_div": "T5 24/10, T3 49/23", "cg_div": "T5 18",
    "trust_cg": "T3 21", "foreign": "T5 15, T3 24/25",
    "other": "T5 14, T3 26", "foreign_tax": "T5 16, T3 33/34",
    "roc": "T3 42", "interest": "T5 13",
}
# lib/ib_dividends categories -> ours.
_IB_CAT = {"eligible": "ca_div", "non_eligible": "ca_div",
           "cg_div": "cg_div", "trust_cg": "trust_cg",
           "foreign": "foreign", "other": "other", "roc": "roc",
           "interest": "interest"}

_SLIP_KEYS = {"type", "issuer", "account", "broker_account", "broker_key",
              "currency", "security", "code", "boxes", "line", "note",
              "source", "status"}
SLIP_STATUSES = ("original", "amended")
# work/: the project's salt of the broker_key --import-cra writes (a
# random value; never in slips.toml, never in inputs/).
KEY_SALT_FILE = ".slip_key_salt"
_LINE_KEYS = {"symbol", "date", "box", "amount", "note"}
_TOP_KEYS = {"year", "slip", "ib_report", "annual_average"}


class SlipsError(ValueError):
    """slips.toml (or an IB report) cannot be used: one message naming
    the file and the entry."""


# ------------------------------------------------------------ model
@dataclass
class Line:
    symbol: str          # as typed (upper)
    root: str
    date: str            # ISO or ""
    box: str
    category: str
    amount: float


@dataclass
class Slip:
    where: str                       # "slips.toml [[slip]] #2", a file
    type: str                        # T5 | T3 | T5008
    issuer: str
    account: str
    currency: str
    broker_hashes: Tuple[str, ...] = ()
    broker_masked: str = ""
    security: str = ""               # the one security's root
    code: str = ""                   # T5008 type code
    amounts: Dict[str, float] = field(default_factory=dict)
    boxes: Dict[str, float] = field(default_factory=dict)
    lines: List[Line] = field(default_factory=list)
    payments: List[Any] = field(default_factory=list)   # ib Payment
    source: str = "typed"            # typed | ib
    empty: bool = False
    # The categories the slip's source can show (None: every one): IB's
    # dividends report has no interest.
    covers: Optional[Tuple[str, ...]] = None
    origin: str = ""                 # slips.toml `source` (cra:<file>)
    detail_only: bool = False        # an IB report beside its CRA slip
    status: str = ""                 # original | amended ("" not said)
    broker_key: str = ""             # slips.toml broker_key (salted)

    @property
    def scope(self) -> str:
        return self.broker_hashes[0] if self.broker_hashes else "*"

    def short_label(self) -> str:
        iss = self.issuer or "?"
        if len(iss) > 28:
            iss = iss[:27].rstrip() + "…"
        bits = [self.type, iss] + ([self.security] if self.security else [])
        return " ".join(bits) + (" (payments only)" if self.detail_only
                                 else "")

    def label(self) -> str:
        bits = [self.type, self.issuer or "?"]
        if self.security:
            bits.append(self.security)
        if self.broker_masked:
            bits.append(self.broker_masked)
        return " ".join(bits) + f" ({self.currency})" + (
            ", payments only" if self.detail_only else "")


@dataclass
class BookRow:
    account: str
    id: str
    action: str
    symbol: str
    root: str
    currency: str        # the payment's own currency
    native: float        # in `currency`
    cad: float           # the books' base-currency amount
    pay_date: str
    tax_date: str
    source: str          # the masked input file name
    source_hash: str
    category: str
    cg_part: float = 0.0     # the box-18 share (native), DIVIDEND only
    pil: bool = False
    # The broker accounts the row may be from: its own, else every one
    # its input file names (an IB statement of two accounts).
    hashes: Tuple[str, ...] = ()
    description: str = ""
    record: str = ""         # the record date the row carries, if any


# ------------------------------------------------------------ helpers
def slips_dir(root: Path) -> Path:
    """The project's own inputs/slips/ — with exports shared by every
    year too: the year folder's inputs/slips/, since slips belong to one
    tax year (lib/project_layout.slips_dir)."""
    return _PL.slips_dir(root)


def _root(sym: str) -> str:
    from taxjson.bin.taxjson_reconcile_slips import norm_symbol, slip_symbol
    return norm_symbol(slip_symbol(str(sym or "")))


def _amount(v: Any, where: str) -> float:
    if isinstance(v, bool):
        raise SlipsError(f"{where}: {v!r} is not an amount")
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip().replace("$", "")
        if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", s):
            raise SlipsError(
                f"{where}: {v!r} is not an amount — type it as a number "
                f"(1234.56; a thousands comma is fine, a decimal comma or "
                f"a space inside the number is not)")
        f = float(s.replace(",", ""))
    else:
        raise SlipsError(f"{where}: {v!r} is not an amount")
    if f != f or f in (float("inf"), float("-inf")):
        raise SlipsError(f"{where}: {v!r} is not an amount")
    return f


def _iso(v: Any, where: str) -> str:
    if isinstance(v, _dt.date):
        return v.isoformat()
    s = str(v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            _dt.date.fromisoformat(s)
            return s
        except ValueError:
            pass
    raise SlipsError(f"{where}: date {v!r} is not YYYY-MM-DD")


def _days(a: str, b: str) -> int:
    try:
        return abs((_dt.date.fromisoformat(a[:10])
                    - _dt.date.fromisoformat(b[:10])).days)
    except ValueError:
        return 10 ** 6


def broker_hashes(number: str) -> Tuple[str, ...]:
    """The hashes a broker account number may carry in the books (as the
    export prints it, or without its spaces and dashes)."""
    from taxjson.bin.taxjson_brokerage import hash_broker_account
    raw = str(number or "").strip()
    out = [hash_broker_account(raw)]
    bare = re.sub(r"[\s-]", "", raw)
    if bare != raw:
        out.append(hash_broker_account(bare))
    return tuple(dict.fromkeys(out))


def _mask(number: str) -> str:
    from taxjson.lib.positions_reports import mask_account
    return mask_account(number)


def _shown(path: Path) -> str:
    from taxjson.lib.brokerages.base import shown_name
    return shown_name(path)


# ------------------------------------------------------------ broker_key
def key_salt(root: Path, create: bool = False) -> Optional[str]:
    """The project's broker_key salt (work/.slip_key_salt, 32 hex);
    `create` makes one when there is none. None: no salt."""
    p = Path(root) / "work" / KEY_SALT_FILE
    try:
        v = p.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        v = ""
    if re.fullmatch(r"[0-9a-f]{32}", v):
        return v
    if not create:
        return None
    import os
    import secrets
    from taxjson.lib.safe_write import write_atomic
    v = secrets.token_hex(16)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(p, v + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return v


def broker_key(salt: str, book_hash: str) -> str:
    """The broker_key slips.toml carries for a broker account: the
    books' hash of it, salted with the project's work/ salt — unlike the
    books' own (unsalted) hash, it cannot be turned back into an account
    number by trying every number."""
    return hashlib.sha256(f"taxjson-slip-key:{salt}:{book_hash}"
                          .encode()).hexdigest()[:10]


def resolve_keys(slips: List["Slip"], hashes: Iterable[str],
                 salt: Optional[str], problems: List[str]) -> List["Slip"]:
    """Each slip's broker_key turned back into the books' hash of its
    broker account (`hashes`: every one the books and IB's reports
    carry). A key no account matches is said and kept apart (its slip
    then has no books)."""
    hashes = set(hashes)
    by_key = {broker_key(salt, h): h for h in hashes} if salt else {}
    out = []
    for s in slips:
        k = s.broker_key
        if not k:
            out.append(s)
        elif k in by_key:
            out.append(replace(s, broker_hashes=(by_key[k],)))
        elif k in hashes:
            problems.append(
                f"{s.where}: broker_key is the books' own hash of the "
                f"broker account (an earlier --import-cra wrote it; it can "
                f"be turned back into the account number) — delete the "
                f"table and import the slip again to write a salted key")
            out.append(s)
        else:
            problems.append(
                f"{s.where}: broker_key {k} is no broker account in the "
                f"books — written with another salt (work/{KEY_SALT_FILE} "
                f"deleted?) or for an account no longer in the books: "
                f"delete the table and import the slip again, or type "
                f"broker_account")
            out.append(s)
    return out


# ------------------------------------------------------------ slips.toml
def load_slips_file(path: Path, cfg: Dict[str, Any], year: Optional[int]
                    ) -> Tuple[List[Slip], List[Dict[str, str]],
                               Dict[str, float]]:
    """(slips, [[ib_report]] entries, [annual_average] overrides) of a
    slips.toml. Raises SlipsError naming the file and the entry."""
    from taxjson.lib.tomlcompat import tomllib
    rel = "inputs/slips/" + path.name
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise SlipsError(f"{rel}: cannot read it ({e})") from None
    try:
        doc = tomllib.loads(text)
    except Exception as e:                              # noqa: BLE001
        raise SlipsError(f"{rel}: not valid TOML ({e})") from None
    unknown = sorted(set(doc) - _TOP_KEYS)
    if unknown:
        raise SlipsError(f"{rel}: unknown key(s) {', '.join(unknown)} — "
                         f"the file holds year, [[slip]] tables, "
                         f"[[ib_report]] and [annual_average]")
    fy = doc.get("year")
    if fy is not None and year is not None and str(fy) != str(year):
        raise SlipsError(f"{rel}: year = {fy}, but the project's year is "
                         f"{year} — last year's slips?")
    accounts = cfg.get("accounts") or {}
    slips: List[Slip] = []
    tables = doc.get("slip") or []
    if not isinstance(tables, list):
        raise SlipsError(f"{rel}: write each slip as a [[slip]] table")
    for i, t in enumerate(tables, 1):
        slips.append(_slip_from_table(t, f"{rel} [[slip]] #{i}", accounts))
    reports = []
    for i, t in enumerate(doc.get("ib_report") or [], 1):
        w = f"{rel} [[ib_report]] #{i}"
        if not isinstance(t, dict) or set(t) - {"file", "account"} \
                or not t.get("file") or not t.get("account"):
            raise SlipsError(f"{w}: give file = \"<the report's name in "
                             f"inputs/slips/>\" and account = \"<a "
                             f"taxjson account>\"")
        if str(t["account"]) not in accounts:
            raise SlipsError(f"{w}: account {t['account']!r} is not an "
                             f"[accounts.*] table of taxjson.toml")
        if str(t["account"]) not in slip_accounts({"accounts": accounts}):
            raise SlipsError(f"{w}: account {t['account']!r} gets no T5 "
                             f"or T3 (registered or crypto)")
        reports.append({"file": str(t["file"]), "account": str(t["account"])})
    avg: Dict[str, float] = {}
    for cur, v in (doc.get("annual_average") or {}).items():
        w = f"{rel} [annual_average] {cur}"
        if not re.fullmatch(r"[A-Z]{3}", str(cur)):
            raise SlipsError(f"{w}: a currency is three capital letters")
        f = _amount(v, w)
        if f <= 0:
            raise SlipsError(f"{w}: a rate is positive")
        avg[str(cur)] = f
    return slips, reports, avg


def _slip_from_table(t: Any, where: str, accounts: Dict[str, Any]) -> Slip:
    if not isinstance(t, dict):
        raise SlipsError(f"{where}: not a table")
    unknown = sorted(set(t) - _SLIP_KEYS)
    if unknown:
        raise SlipsError(f"{where}: unknown key(s) {', '.join(unknown)} "
                         f"(known: {', '.join(sorted(_SLIP_KEYS))})")
    typ = str(t.get("type") or "").strip().upper()
    if typ == "NR4":
        raise SlipsError(f"{where}: an NR4 is a non-resident's slip — "
                         f"slip-audit compares T5, T3 and T5008 slips")
    if typ not in BOX_CAT:
        raise SlipsError(f"{where}: type = {t.get('type')!r}; give "
                         f"\"T5\", \"T3\" or \"T5008\"")
    acct = str(t.get("account") or "").strip()
    if not acct:
        raise SlipsError(f"{where}: account is required — the taxjson "
                         f"account ([accounts.<name>]) the slip's "
                         f"payments are in")
    if acct not in accounts:
        raise SlipsError(f"{where}: account {acct!r} is not an "
                         f"[accounts.*] table of taxjson.toml")
    a = accounts.get(acct) or {}
    if str(a.get("type") or "") == "sheltered":
        raise SlipsError(f"{where}: {acct} is a registered (sheltered) "
                         f"account — it gets no {typ}; leave it out")
    if a.get("crypto") and typ != "T5008":
        raise SlipsError(f"{where}: {acct} is a crypto account — staking "
                         f"rewards are on no {typ}; leave it out")
    cur = str(t.get("currency") or "CAD").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise SlipsError(f"{where}: currency {t.get('currency')!r} is not "
                         f"a three-letter code (CAD, USD)")
    boxes_in = t.get("boxes") or {}
    if not isinstance(boxes_in, dict):
        raise SlipsError(f"{where}: boxes = {{ 24 = 1.00, ... }} (box "
                         f"number = amount)")
    boxes: Dict[str, float] = {}
    amounts: Dict[str, float] = {}
    for k, v in boxes_in.items():
        box = str(k).strip().lstrip("0") or "0"
        w = f"{where} box {box}"
        if box in ID_BOXES.get(typ, ()):
            raise SlipsError(f"{w}: an identifier, not an amount — leave "
                             f"it out (never type an account number or "
                             f"a SIN here)")
        if box not in BOX_CAT[typ] and box not in OTHER_BOXES.get(typ, ()):
            raise SlipsError(f"{w}: not a {typ} amount box (compared: "
                             f"{', '.join(sorted(BOX_CAT[typ], key=int))})")
        f = _amount(v, w)
        boxes[box] = f
        cat = BOX_CAT[typ].get(box)
        if cat:
            amounts[cat] = amounts.get(cat, 0.0) + f
    sec = str(t.get("security") or "").strip()
    lines: List[Line] = []
    for j, ln in enumerate(t.get("line") or [], 1):
        w = f"{where} line #{j}"
        if not isinstance(ln, dict):
            raise SlipsError(f"{w}: not a table")
        bad = sorted(set(ln) - _LINE_KEYS)
        if bad:
            raise SlipsError(f"{w}: unknown key(s) {', '.join(bad)} "
                             f"(known: {', '.join(sorted(_LINE_KEYS))})")
        sym = str(ln.get("symbol") or "").strip().upper()
        if not sym:
            raise SlipsError(f"{w}: symbol is required (as the books "
                             f"spell it, or its root)")
        box = str(ln.get("box") or "").strip()
        cat = BOX_CAT[typ].get(box)
        if not cat or typ == "T5008":
            raise SlipsError(f"{w}: box {ln.get('box')!r} is not a {typ} "
                             f"income box (compared: "
                             f"{', '.join(sorted(BOX_CAT[typ], key=int))})")
        lines.append(Line(symbol=sym, root=_root(sym),
                          date=(_iso(ln["date"], w) if ln.get("date")
                                else ""),
                          box=box, category=cat,
                          amount=_amount(ln.get("amount"), w)))
    if lines and not boxes:
        # A slip typed as its per-security lines only: the lines are the
        # slip's boxes.
        for ln in lines:
            amounts[ln.category] = amounts.get(ln.category, 0.0) + ln.amount
            boxes[ln.box] = boxes.get(ln.box, 0.0) + ln.amount
    number = str(t.get("broker_account") or "").strip()
    key = str(t.get("broker_key") or "").strip().lower()
    if key and not re.fullmatch(r"[0-9a-f]{10}", key):
        raise SlipsError(f"{where}: broker_key {t.get('broker_key')!r} is "
                         f"not the books' 10-character key (`taxjson "
                         f"slip-audit --import-cra` writes it; type "
                         f"broker_account instead)")
    if key and number:
        raise SlipsError(f"{where}: give broker_account or broker_key, "
                         f"not both")
    src = str(t.get("source") or "").strip()
    status = str(t.get("status") or "").strip().lower()
    if status and status not in SLIP_STATUSES:
        raise SlipsError(f"{where}: status = {t.get('status')!r}; give "
                         f"\"original\" or \"amended\" (a cancelled slip: "
                         f"delete its table)")
    return Slip(where=where, type=typ, status=status, broker_key=key,
                issuer=str(t.get("issuer") or "").strip(), account=acct,
                currency=cur,
                broker_hashes=(broker_hashes(number) if number
                               else (key,) if key else ()),
                broker_masked=_mask(number) if number else "",
                origin=src,
                security=_root(sec) if sec else "",
                code=str(t.get("code") or "").strip().upper(),
                amounts=amounts, boxes=boxes, lines=lines,
                empty=not boxes and not lines)


_TABLE_RE = re.compile(r"^\s*\[")
_SLIP_HEAD_RE = re.compile(r"^\s*\[\[\s*slip\s*\]\]\s*(?:#.*)?$")
_SLIP_SUB_RE = re.compile(r"^\s*\[{1,2}\s*slip\s*\.")


def slip_table_spans(text: str) -> List[Tuple[int, int]]:
    """[(first line, end line)) of each [[slip]] table of a slips.toml,
    in order — its [slip.boxes] / [[slip.line]] parts included, the
    comments and blank lines after it not."""
    lines = text.splitlines()
    spans: List[Tuple[int, int]] = []
    cur: Optional[int] = None

    def close(end: int) -> None:
        e = end
        while e > cur + 1 and (not lines[e - 1].strip()
                               or lines[e - 1].lstrip().startswith("#")):
            e -= 1
        spans.append((cur, e))
    for i, ln in enumerate(lines):
        if _SLIP_HEAD_RE.match(ln):
            if cur is not None:
                close(i)
            cur = i
        elif cur is not None and _TABLE_RE.match(ln) \
                and not _SLIP_SUB_RE.match(ln):
            close(i)
            cur = None
    if cur is not None:
        close(len(lines))
    return spans


def comment_out_tables(text: str, which: Dict[int, str]) -> str:
    """slips.toml with the [[slip]] tables numbered in `which` (1-based)
    turned into comments under the line `which` gives. Raises SlipsError
    when the tables cannot be told apart in the text."""
    from taxjson.lib.tomlcompat import tomllib
    spans = slip_table_spans(text)
    try:
        n = len(tomllib.loads(text).get("slip") or [])
    except Exception:                                   # noqa: BLE001
        n = -1
    if n != len(spans):
        raise SlipsError(f"inputs/slips/{SLIPS_FILE}: its [[slip]] tables "
                         f"cannot be told apart in the text — delete the "
                         f"replaced table by hand")
    lines = text.splitlines()
    for idx in sorted(which, reverse=True):
        a, b = spans[idx - 1]
        lines[a:b] = (["# " + which[idx]]
                      + ["# " + ln if ln.strip() else "#"
                         for ln in lines[a:b]])
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# ------------------------------------------------------------ IB reports
def ib_slips(rep: Any, account: str, year: Optional[int],
             fx_cache: Optional[dict] = None) -> List[Slip]:
    """IB's dividends report as slips: the account's T5 (every payment,
    for the per-payment match) and one T3 per fund, in CAD — the
    report's base amounts (IB's own rate, GrossInBase) when the base is
    CAD; for a USD-base account each payment at the Bank of Canada rate
    of its pay date (CA-FX-01; a Canadian slip is in CAD). Raises
    SlipsError when a rate is not in the FX cache."""
    where = _shown(rep.path)
    to_cad = _report_to_cad(rep, where, fx_cache)
    covers = tuple(c for c in CATEGORIES if c != "interest")
    t5 = Slip(where=where, type="T5", issuer="IB", account=account,
              currency="CAD", broker_hashes=(rep.account_hash,),
              broker_masked=rep.account_masked, source="ib",
              payments=list(rep.payments), covers=covers)
    t3s: Dict[str, Slip] = {}
    for p in rep.payments:
        for c in p.components:
            cat = _IB_CAT.get(c.category or "")
            if cat is None:
                continue
            if c.slip == "T3":
                tgt = t3s.setdefault(p.symbol, Slip(
                    where=where, type="T3", issuer="IB", account=account,
                    currency="CAD", broker_hashes=(rep.account_hash,),
                    broker_masked=rep.account_masked, source="ib",
                    security=_root(p.symbol), covers=covers))
            else:
                tgt = t5
            g, w = to_cad(p, c)
            tgt.amounts[cat] = tgt.amounts.get(cat, 0.0) + g
            if w:
                tgt.amounts["foreign_tax"] = (
                    tgt.amounts.get("foreign_tax", 0.0) + w)
    return [t5] + [t3s[k] for k in sorted(t3s)]


def _report_to_cad(rep: Any, where: str, fx_cache: Optional[dict]):
    """(gross, withheld) of a report component in CAD: a function."""
    if rep.base_currency == "CAD":
        return lambda p, c: (c.gross_base, c.withheld_base)
    from taxjson.bin import to_base_curr as T
    cache = fx_cache if fx_cache is not None else T.load_cache()
    rates: Dict[str, Dict[str, float]] = {}
    for cur in sorted({p.currency for p in rep.payments} - {"CAD"}):
        dates = sorted(p.pay_date for p in rep.payments
                       if p.currency == cur and p.pay_date)
        if not dates:
            continue
        lo = (_dt.date.fromisoformat(dates[0])
              - _dt.timedelta(days=10)).isoformat()
        rows = T.resolve_rows(cache, cur, "CAD", lo, dates[-1],
                              _dt.date.today().isoformat())
        rates[cur] = {d: float(v) for d, v, _src in rows}

    def conv(p, c):
        if p.currency == "CAD":
            return c.gross, c.withheld
        r = rates.get(p.currency, {}).get(p.pay_date)
        if r is None:
            raise SlipsError(
                f"inputs/slips/{where}: IB's report is in "
                f"{rep.base_currency} (the account's base currency) and "
                f"the FX cache has no Bank of Canada {p.currency} rate "
                f"for {p.symbol}'s payment of {p.pay_date or '?'} — run "
                f"`taxjson run` (it fills the cache), then slip-audit")
        return c.gross * r, c.withheld * r
    return conv


def report_identity(rep: Any) -> Tuple:
    """What two copies of one dividends report share: the account, the
    years and every payment."""
    return (rep.account_hash, tuple(rep.years), rep.base_currency,
            tuple((p.symbol, p.pay_date, p.ex_date, p.pil, p.currency,
                   tuple((c.label, round(c.gross, 4),
                          round(c.withheld, 4)) for c in p.components))
                  for p in rep.payments))


def find_ib_reports(root: Path) -> List[Path]:
    from taxjson.lib.ib_dividends import is_dividends_report
    d = slips_dir(root)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() == ".csv"
                   and is_dividends_report(p)),
                  key=lambda p: p.name.lower())


# ------------------------------------------------------------ FX
def annual_average(currency: str, year: Any,
                   cache: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """The year's average rate: the mean of the Bank of Canada's daily
    rates for the year in the FX cache (~/.currency_price_cache.json; no
    download). None when the cache has none."""
    if cache is None:
        from taxjson.bin import to_base_curr as T
        cache = T.load_cache()
    blk = ((cache.get("_boc") or {}).get(f"{currency}CAD") or {})
    obs = blk.get("obs") if isinstance(blk, dict) else None
    if not isinstance(obs, dict):
        return None
    vals = []
    for d, v in obs.items():
        if not str(d).startswith(f"{year}-"):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0 and f == f:
            vals.append(f)
    if not vals:
        return None
    return {"rate": round(sum(vals) / len(vals), 6),
            "observations": len(vals),
            "source": f"the mean of the {len(vals)} Bank of Canada daily "
                      f"rates of {year} in the FX cache"}


# ------------------------------------------------------------ the books
def _row_amount(t: dict) -> float:
    return float(t.get("gross_amount") or 0.0) \
        or float(t.get("net_amount") or 0.0)


def _load_doc(p: Path) -> Optional[dict]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


_NATIVE_SUFFIXES = ("_raw.json", "_filled.json", "_sorted.json")


def _native_doc(cache: Path, acct: str) -> Optional[dict]:
    for suf in _NATIVE_SUFFIXES:
        p = cache / f"{acct}{suf}"
        if p.exists():
            return _load_doc(p)
    return None


def slip_accounts(cfg: Dict[str, Any]) -> List[str]:
    """The accounts that get T5/T3 slips: taxable, not crypto."""
    return sorted(n for n, a in (cfg.get("accounts") or {}).items()
                  if str((a or {}).get("type") or "") == "taxable"
                  and not (a or {}).get("crypto"))


def load_books(root: Path, cfg: Dict[str, Any], year: int,
               accounts: Iterable[str]) -> Tuple[List[BookRow], List[str]]:
    """The year's income rows of `accounts` (BookRow), and the problems
    (an account with no books). Dividends and payments in lieu by their
    tax date (a Canadian trust's by its record date, CA-INC-DATE-TRUST),
    withholding with its payment, ROC by its ACB date, interest when
    paid."""
    from taxjson.lib.cg_dividends import allocate, entries_from_config
    from taxjson.lib.income_dating import IncomeRules, is_canadian_issuer
    cache = Path(root) / "work"
    rules = IncomeRules.from_settings(cfg.get("settings") or {})
    problems: List[str] = []
    accounts = list(accounts)
    natives: Dict[str, Dict[str, dict]] = {}
    native_divs: List[Tuple[str, dict]] = []
    for acct in sorted(set(accounts) | set(_all_accounts(cache))):
        doc = _native_doc(cache, acct)
        if doc is None:
            continue
        rows = [t for t in doc.get("transactions") or []
                if isinstance(t, dict)]
        natives[acct] = {str(t.get("id")): t for t in rows}
        native_divs += [(acct, t) for t in rows
                        if t.get("action") == "DIVIDEND"]
    entries = entries_from_config(cfg)
    frac: Dict[Tuple[str, str], float] = {}
    if entries:
        taxable = {n for n, a in (cfg.get("accounts") or {}).items()
                   if str((a or {}).get("type") or "") == "taxable"}
        frac = allocate(entries, native_divs, date_of=rules.income_date,
                        default_accounts=taxable)
    ystr = str(year)
    out: List[BookRow] = []
    for acct in accounts:
        base = _load_doc(cache / f"{acct}_base.json")
        if base is None:
            if (_PL.inputs_dir(Path(root)) / acct).is_dir():
                problems.append(f"{acct}: no readable work/{acct}_base.json "
                                f"— run `taxjson run`")
            continue
        rows = [t for t in base.get("transactions") or []
                if isinstance(t, dict)]
        file_hashes = _file_hashes(base)
        wh = rules.withholding_dates(rows)
        nat = natives.get(acct, {})
        for t in rows:
            act = str(t.get("action") or "")
            if act in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                d = rules.income_date(t)
                amt = _row_amount(t)
                cat = ("ca_div" if is_canadian_issuer(t)
                       and not _is_receipt(t) else "foreign")
            elif act == "TAX":
                d = wh.get(id(t)) or str(t.get("date") or "")
                amt = float(t.get("net_amount") or 0.0) \
                    or float(t.get("gross_amount") or 0.0)
                cat = "foreign_tax"
            elif act == "ADJUST":
                d = rules.roc_date(t)
                if str(t.get("id") or "").startswith("DIST-"):
                    d = str(t.get("date_settle") or t.get("date") or "")
                amt = -float(t.get("net_amount") or 0.0)
                cat = "roc"
            elif act == "INTEREST":
                d = str(t.get("date") or "")
                amt = float(t.get("net_amount") or 0.0) \
                    or float(t.get("gross_amount") or 0.0)
                if amt <= 0:
                    continue        # debit (margin) interest: not on a slip
                cat = "interest"
            else:
                continue
            pay = str(t.get("date") or "")
            # Rows of the year, and payments paid in the year but dated
            # in another (kept for the per-payment match).
            if not (str(d).startswith(ystr) or pay.startswith(ystr)):
                continue
            n = nat.get(str(t.get("id")))
            if n is not None:
                ncur = str(n.get("currency") or "CAD").upper()
                if act in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                    namt = _row_amount(n)
                elif act == "ADJUST":
                    namt = -float(n.get("net_amount") or 0.0)
                else:
                    namt = float(n.get("net_amount") or 0.0) \
                        or float(n.get("gross_amount") or 0.0)
            else:
                ncur, namt = str(t.get("currency") or "CAD").upper(), amt
            f = frac.get((acct, str(t.get("id")))) \
                if act == "DIVIDEND" else None
            sym = str(t.get("symbol") or "")
            out.append(BookRow(
                account=acct, id=str(t.get("id") or ""), action=act,
                symbol=sym, root=_root(sym) if sym and sym != "CASH" else "",
                currency=ncur, native=namt, cad=amt, pay_date=pay[:10],
                tax_date=str(d)[:10],
                source=str(t.get("source") or ""),
                source_hash=str(t.get("source_account") or ""),
                category=cat, cg_part=(namt * f) if f else 0.0,
                pil=(act == "DIVIDEND_IN_LIEU"),
                hashes=((str(t["source_account"]),)
                        if t.get("source_account")
                        else file_hashes.get(str(t.get("source") or ""),
                                             ())),
                description=str(t.get("description") or ""),
                record=str(t.get("record_date") or "")[:10]))
        _redate_prior_roc([r for r in out if r.account == acct], year,
                          problems)
    return out, problems


def _redate_prior_roc(rows: List[BookRow], year: int,
                      problems: List[str]) -> None:
    """A hand-entered ROC line (.tt ADJUST) with no record= is dated by
    its pay date; when the distribution paid that day belongs to another
    year by its record date (a December record date paid in January),
    the line is that year's T3 box 42: it is counted there, never netted
    against this year's slip, and said (CA-INC-DATE-ROC-TRUST)."""
    ystr = str(year)
    divs = [r for r in rows if r.action == "DIVIDEND" and r.root]
    for r in rows:
        if not (r.action == "ADJUST" and r.category == "roc"
                and not r.record and r.source.lower().endswith(".tt")
                and r.root):
            continue
        hits = [d for d in divs if d.root == r.root
                and _days(d.pay_date, r.pay_date) <= 3
                and d.tax_date[:4] != r.tax_date[:4]]
        if not hits:
            continue
        d = min(hits, key=lambda x: _days(x.pay_date, r.pay_date))
        problems.append(
            f"{r.account}: {r.symbol} ADJUST {r.pay_date} ({r.source}) "
            f"has no record= and is dated by its pay date, but the "
            f"distribution paid {d.pay_date} has record date "
            f"{d.tax_date}: it is {d.tax_date[:4]}'s return of capital, "
            f"not compared with the {ystr} slip — add `record="
            f"{d.tax_date}` to the line (CA-INC-DATE-ROC-TRUST)")
        r.tax_date = d.tax_date


def _is_receipt(t: dict) -> bool:
    """A depositary receipt (a CDR): written on a venue that lists
    receipts (markets.toml `receipts = true`) or named one in the
    export ([lists] receipt_words). Its dividends are the underlying
    foreign company's: T5 box 15, not a Canadian dividend."""
    from taxjson.lib.markets import (receipt_suffixes, receipt_words,
                                     suffix_of)
    if suffix_of(str(t.get("symbol") or "")) in receipt_suffixes():
        return True
    words = set(re.findall(r"[A-Z]+", str(t.get("description") or "")
                           .upper()))
    return bool(words & receipt_words())


def _file_hashes(doc: dict) -> Dict[str, Tuple[str, ...]]:
    """{input file's shown name: the broker-account hashes the parse
    recorded for it} from a book's metadata."""
    out: Dict[str, set] = {}
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    srcs = (meta or {}).get("sources") if isinstance(meta, dict) else None
    for e in srcs or []:
        om = (e or {}).get("original_metadata") if isinstance(e, dict) \
            else None
        sa = (om or {}).get("source_accounts") if isinstance(om, dict) \
            else None
        for f, hs in (sa or {}).items():
            if isinstance(hs, list):
                out.setdefault(str(f), set()).update(str(h) for h in hs)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def broker_account_files(root: Path, accounts: Iterable[str]
                         ) -> Dict[str, Tuple[str, ...]]:
    """{broker-account hash: the input files naming it} from the parse
    metadata of `accounts`' books (work/<account>_base.json): every
    broker account an export names, also one no income row carries."""
    out: Dict[str, set] = {}
    for acct in accounts:
        base = _load_doc(Path(root) / "work" / f"{acct}_base.json")
        for f, hs in _file_hashes(base or {}).items():
            for h in hs:
                out.setdefault(h, set()).add(Path(f).name)
    return {h: tuple(sorted(v)) for h, v in out.items()}


def _all_accounts(cache: Path) -> List[str]:
    names = set()
    for suf in _NATIVE_SUFFIXES:
        for p in cache.glob(f"*{suf}"):
            if not p.name.startswith("."):
                names.add(p.name[: -len(suf)])
    return sorted(names)


# ------------------------------------------------------------ audit
@dataclass
class Money:
    """A books figure in one comparison currency, with the CAD bucket's
    alternatives: `fx` is the part converted from another currency at
    the daily rates, `alt` the same figure with that part at the annual
    average instead."""
    amt: float = 0.0
    fx: float = 0.0
    alt: float = 0.0
    alt_ok: bool = True

    def add(self, other: "Money", k: float = 1.0) -> None:
        self.amt += other.amt * k
        self.fx += other.fx * k
        self.alt += other.alt * k
        self.alt_ok = self.alt_ok and other.alt_ok


def _row_money(r: BookRow, bucket: str, avg: Dict[str, Optional[float]],
               amount: Optional[float] = None) -> Money:
    """The row's money in the bucket's currency; `amount` (native) for a
    part of the row (the box-18 share), scaled to CAD by the row's own
    rate."""
    nat = r.native if amount is None else amount
    if bucket != "CAD":
        return Money(amt=nat, alt=nat)
    if r.currency == "CAD":
        cad = r.cad if amount is None else amount
        return Money(amt=cad, alt=cad)
    cad = r.cad if amount is None else (
        amount * (r.cad / r.native) if r.native else 0.0)
    rate = avg.get(r.currency)
    return Money(amt=cad, fx=cad, alt=(nat * rate) if rate else cad,
                 alt_ok=rate is not None)


def audit(root: Path, cfg: Dict[str, Any], *,
          tolerance: float = DEFAULT_TOLERANCE,
          account: Optional[str] = None,
          fx_cache: Optional[dict] = None) -> Dict[str, Any]:
    """The whole audit as a JSON-ready dict (schema_version 1; the text
    report and the checklist read it). Raises SlipsError for an input
    that cannot be used."""
    from taxjson.lib.cg_dividends import CgDividendMapError
    settings = cfg.get("settings") or {}
    year = settings.get("year")
    if year is None:
        raise SlipsError("taxjson.toml has no [settings] year")
    year = int(year)
    accts = slip_accounts(cfg)
    if account:
        if account not in (cfg.get("accounts") or {}):
            raise SlipsError(f"account {account!r} is not an [accounts.*] "
                             f"table of taxjson.toml")
        if account not in accts:
            raise SlipsError(f"account {account!r} gets no T5 or T3: only "
                             f"a taxable, non-crypto account does")
        accts = [account]
    d = slips_dir(root)
    sf = d / SLIPS_FILE
    slips: List[Slip] = []
    reports_map: List[Dict[str, str]] = []
    avg_override: Dict[str, float] = {}
    sources: List[Dict[str, Any]] = []
    if sf.is_file():
        slips, reports_map, avg_override = load_slips_file(sf, cfg, year)
        sources.append({"kind": "slips.toml", "file": f"inputs/slips/"
                        f"{SLIPS_FILE}", "slips": len(slips)})
    try:
        books, problems = load_books(root, cfg, year, accts)
    except CgDividendMapError as e:
        raise SlipsError(str(e)) from None
    # IB reports: matched to the account whose books carry the IB account.
    fx_notes: List[str] = []
    seen_reports: Dict[Tuple, Tuple[Path, Tuple]] = {}
    report_hashes: set = set()
    for p, rep, acct in _reports(root, books, reports_map, problems):
        report_hashes.add(rep.account_hash)
        # Two copies of one report (`... (1).csv`): read once; two
        # different reports of one account and year: which is right
        # cannot be told.
        ident = report_identity(rep)
        k = (rep.account_hash, tuple(rep.years))
        if k in seen_reports:
            first, ident0 = seen_reports[k]
            if ident0 == ident:
                problems.append(
                    f"inputs/slips/{_shown(p)}: the same IB dividends report "
                    f"as {_shown(first)} (IB account {rep.account_masked}) "
                    f"— read once; delete one of them")
                continue
            raise SlipsError(
                f"inputs/slips/{_shown(p)} and {_shown(first)}: two "
                f"different IB dividends reports of IB account "
                f"{rep.account_masked} for {', '.join(rep.years) or year} "
                f"— keep the newer download only")
        seen_reports[k] = (p, ident)
        if account and acct != account:
            continue
        yrs = rep.years
        if yrs and str(year) not in yrs:
            raise SlipsError(f"inputs/slips/{_shown(p)}: IB's report for "
                             f"{', '.join(yrs)}, not {year}")
        new = ib_slips(rep, acct, year, fx_cache)
        slips += new
        src = {"kind": "ib-dividends", "file": "inputs/slips/" + _shown(p),
               "account": acct, "broker_account": rep.account_masked,
               "payments": len(rep.payments),
               "unknown_components": [
                   {"component": k, "amount": round(v, 2)}
                   for k, v in rep.unknown],
               "problems": list(rep.problems)}
        if rep.base_currency != "CAD":
            src["converted_from"] = rep.base_currency
            fx_notes.append(_usd_base_note(rep, acct, year, fx_cache, new))
        sources.append(src)
        for k, v in rep.unknown:
            problems.append(f"inputs/slips/{_shown(p)}: IB component "
                            f"{k!r} ({v:.2f} {rep.base_currency}) is not "
                            f"one taxjson knows — not compared")
    # Every broker account an export names, also one with no income
    # rows (a Webull export: trades only) — a slip --import-cra keyed to
    # it is that account's.
    known_files = broker_account_files(root, slip_accounts(cfg))
    slips = resolve_keys(slips, {h for r in books for h in r.hashes}
                         | report_hashes | set(known_files),
                         key_salt(root), problems)
    if account:
        slips = [s for s in slips if s.account == account]
    slips = [s for s in slips if s.account in accts or s.account == account]
    # Averages for every foreign currency in sight.
    curs = sorted({r.currency for r in books if r.currency != "CAD"}
                  | {s.currency for s in slips if s.currency != "CAD"})
    avgs: Dict[str, Optional[Dict[str, Any]]] = {}
    for c in curs:
        if c in avg_override:
            avgs[c] = {"rate": avg_override[c], "observations": None,
                       "source": "slips.toml [annual_average]"}
        else:
            avgs[c] = annual_average(c, year, fx_cache)
    rate = {c: (v["rate"] if v else None) for c, v in avgs.items()}
    res_accounts = []
    issues: List[Dict[str, Any]] = []
    sugg_cgd: List[Dict[str, Any]] = []
    sugg_tt: Dict[str, List[str]] = {}
    notes: List[str] = []
    coverage_no_slip: List[Dict[str, Any]] = []
    slips_no_books: List[Dict[str, Any]] = []
    for acct in accts:
        a_slips = [s for s in slips if s.account == acct]
        a_rows = [r for r in books if r.account == acct]
        acc = _audit_account(acct, a_slips, a_rows, year, tolerance, rate,
                             issues, sugg_cgd, sugg_tt, notes,
                             coverage_no_slip, slips_no_books,
                             known_files=known_files)
        if acc is not None:
            res_accounts.append(acc)
    t5008 = _t5008(root, slips, year, tolerance, rate)
    status = ("no-slips" if not slips
              else "attention" if issues else "ok")
    return {
        "schema_version": SCHEMA_VERSION,
        "year": year,
        "country": "canada",
        "tolerance": tolerance,
        "fx_band": FX_BAND,
        "sources": sources,
        "annual_average": avgs,
        "accounts": res_accounts,
        "coverage": {"no_slip": coverage_no_slip,
                     "slips_without_books": slips_no_books},
        "t5008": t5008,
        "suggestions": {
            "capital_gains_dividends": sugg_cgd,
            "tt_lines": [{"account": a, "file": f"inputs/{a}/{SUGGEST_TT}",
                          "lines": ls} for a, ls in sorted(sugg_tt.items())
                         if ls],
            "notes": fx_notes + notes},
        "problems": problems,
        "issues": issues,
        "status": status,
    }


def _reports(root: Path, books: List[BookRow],
             reports_map: List[Dict[str, str]], problems: List[str]):
    """(path, report, project account) of every IB dividends report in
    inputs/slips/: the account named in [[ib_report]], else the one
    whose books carry the IB account. Raises SlipsError."""
    from taxjson.lib.ib_dividends import IBReportError, read_report
    hashes_of: Dict[str, set] = {}
    for r in books:
        for h in r.hashes:
            hashes_of.setdefault(h, set()).add(r.account)
    named = {m["file"]: m["account"] for m in reports_map}
    for p in find_ib_reports(root):
        try:
            rep = read_report(p)
        except IBReportError as e:
            raise SlipsError(str(e)) from None
        acct = named.get(p.name)
        if acct is None:
            owners = sorted(hashes_of.get(rep.account_hash, ()))
            if len(owners) == 1:
                acct = owners[0]
            elif len(owners) > 1:
                raise SlipsError(
                    f"inputs/slips/{_shown(p)}: IB account "
                    f"{rep.account_masked} is in the books of "
                    f"{', '.join(owners)} — name one in slips.toml: "
                    f"[[ib_report]] file = \"<this file's name>\" "
                    f"account = \"<account>\"")
            else:
                problems.append(
                    f"inputs/slips/{_shown(p)}: no taxable account's books "
                    f"carry IB account {rep.account_masked} (a registered "
                    f"account gets no T5/T3; else an IB statement for it "
                    f"in inputs/<account>/?) — not compared; name the "
                    f"account in slips.toml: [[ib_report]] file = "
                    f"\"<this file's name>\" account = \"<account>\"")
                continue
        yield p, rep, acct


def _usd_base_note(rep: Any, acct: str, year: int, fx_cache, slips
                   ) -> str:
    from taxjson.lib.report_model import fmt_money
    daily = sum(sum(v for c, v in s.amounts.items() if c in INCOME_CATS)
                for s in slips)
    avg = annual_average(rep.base_currency, year, fx_cache)
    alt_cad = 0.0
    ok = avg is not None
    for p in rep.payments:
        for c in p.components:
            if _IB_CAT.get(c.category or "") not in INCOME_CATS:
                continue
            if p.currency == "CAD":
                alt_cad += c.gross
            elif p.currency == rep.base_currency and avg:
                alt_cad += c.gross * avg["rate"]
            else:
                ok = False
    alt = (f"; at the {year} average rate ({avg['rate']:.4f}) "
           f"{fmt_money(alt_cad)} CAD") if ok else ""
    return (f"{acct} ({rep.account_masked}): IB's dividends report is in "
            f"{rep.base_currency}, the account's base currency — each "
            f"payment is converted to CAD at the Bank of Canada rate of "
            f"its pay date (a Canadian slip is in CAD): income "
            f"{fmt_money(daily)} CAD{alt}")


def _issue(issues: List[Dict[str, Any]], acct: str, kind: str,
           text: str) -> None:
    issues.append({"account": acct, "kind": kind, "text": text})


def _audit_account(acct, a_slips, a_rows, year, tol, rate, issues,
                   sugg_cgd, sugg_tt, notes, coverage_no_slip,
                   slips_no_books, known_files=None
                   ) -> Optional[Dict[str, Any]]:
    ystr = str(year)
    in_year = [r for r in a_rows if r.tax_date.startswith(ystr)]
    income = _income_by_source(in_year)
    live = [s for s in a_slips if not s.empty and s.type != "T5008"]
    # A CRA slip (--import-cra) and IB's report of one broker account
    # are one slip: the CRA copy's boxes are compared; the report keeps
    # its payments for the per-payment match. So is a slip typed for
    # that broker account (broker_account / broker_key) with a box the
    # report also covers — one typed for its interest only (box 13, which
    # the report has none of) is compared beside it.
    whole = [s for s in live if s.source != "ib" and s.broker_hashes and (
        s.origin.startswith("cra:") or any(
            abs(v) >= 0.005 and c != "interest"
            for c, v in s.amounts.items()))]
    for i, s in enumerate(live):
        if s.source == "ib" and any(
                c.type == s.type and set(c.broker_hashes)
                & set(s.broker_hashes)
                and (s.type == "T5" or c.security == s.security)
                for c in whole):
            live[i] = replace(s, amounts={}, covers=(), detail_only=True)
    # A slip keyed by the books' broker-account key shows its input files.
    files: Dict[str, set] = {}
    for r in a_rows:
        for h in r.hashes:
            files.setdefault(h, set()).add(r.source or "?")
    for i, s in enumerate(live):
        if s.broker_hashes and not s.broker_masked:
            live[i] = replace(s, broker_masked=", ".join(sorted(
                files.get(s.broker_hashes[0], ()) or set(
                    (known_files or {}).get(s.broker_hashes[0], ())) or {
                    "#" + (s.broker_key or "?")[:6]})))
    for s in a_slips:
        if s.empty:
            _issue(issues, acct, "empty-slip",
                   f"{s.where}: {s.label()} has no boxes yet — type them "
                   f"from the slip")
    if not live:
        if any(abs(v) > 0.005 for src in income.values()
               for v in src["amounts"].values()):
            only_small_interest = all(
                set(k for k, v in src["amounts"].items() if abs(v) > 0.005)
                <= {"interest"} for src in income.values()) and sum(
                    src["amounts"].get("interest", 0.0)
                    for src in income.values()) < INTEREST_SLIP_MIN
            entry = {"account": acct, "sources": sorted(income),
                     "income": {k: round(v, 2) for k, v in
                                _sum_amounts(income).items()},
                     "small_interest_only": only_small_interest}
            coverage_no_slip.append(entry)
            if not only_small_interest:
                _issue(issues, acct, "no-slip",
                       f"{acct}: income in the books "
                       f"({_fmt_amounts(entry['income'])}) and no T5/T3 "
                       f"slip for it")
        return None if not a_slips else {
            "account": acct, "groups": [], "status": "no-slip"}
    # Groups: the broker accounts the slips name — joined when one
    # input file holds several of them (an IB statement of two
    # accounts: their rows cannot be told apart) — or the whole label.
    unscoped = any(not s.broker_hashes for s in live)
    parent: Dict[str, str] = {}

    def find(h: str) -> str:
        while parent.setdefault(h, h) != h:
            parent[h] = parent[parent[h]]
            h = parent[h]
        return h

    def union(hs: Iterable[str]) -> None:
        hs = list(hs)
        for h in hs[1:]:
            parent[find(h)] = find(hs[0])

    named = {h for s in live for h in s.broker_hashes}
    for s in live:
        union(s.broker_hashes)
    for r in a_rows:
        hs = [h for h in r.hashes if h in named]
        if len(hs) > 1:
            union(hs)
    groups: Dict[str, Dict[str, Any]] = {}
    for s in live:
        key = find(s.broker_hashes[0]) if s.broker_hashes else "*"
        g = groups.setdefault(key, {"slips": [], "rows": [],
                                    "masked": []})
        g["slips"].append(s)
        if s.broker_masked and s.broker_masked not in g["masked"]:
            g["masked"].append(s.broker_masked)
    for g in groups.values():
        g["masked"] = " + ".join(sorted(g["masked"]))
    uncovered: List[BookRow] = []
    unattributed: List[BookRow] = []
    # A row with no broker account (a .tt line, a [[distributions]]
    # adjustment) goes where its security is held: the one group (or
    # the rows of no slip) holding it, else the one group whose slips
    # name it; held in several places with no slip naming it, it is
    # listed apart.
    held: Dict[str, set] = {}
    placed: List[Tuple[BookRow, Optional[str]]] = []
    for r in a_rows:
        hs = [h for h in r.hashes if h in named]
        g = find(hs[0]) if hs else ("*" if unscoped and r.hashes else None)
        if r.root and r.hashes:
            held.setdefault(r.root, set()).add(g or "-")
        placed.append((r, g))
    slip_roots: Dict[str, set] = {}
    for key, gg in groups.items():
        for sl in gg["slips"]:
            names = {sl.security} | {ln.root for ln in sl.lines} | {
                _root(p.symbol) for p in sl.payments}
            for n in names - {""}:
                slip_roots.setdefault(n, set()).add(key)
    for r, g in placed:
        if g is None and not r.hashes and r.root:
            # The distribution it belongs to: the same security's broker
            # row on its record date or within a week of its pay date.
            near = {gx or "-" for x, gx in placed
                    if x.hashes and x.root == r.root
                    and x.action in ("DIVIDEND", "ADJUST")
                    and ((r.record and x.tax_date == r.record)
                         or _days(x.pay_date, r.pay_date) <= MATCH_DAYS)}
            if len(near) == 1:
                g = next(iter(near))
                if g == "-":
                    uncovered.append(r)
                    continue
                groups[g]["rows"].append(r)
                continue
        if g is None and not r.hashes:
            where_held = held.get(r.root, set()) if r.root else set()
            if len(where_held) == 1:
                g = next(iter(where_held))
                g = None if g == "-" else g
                if g is None:
                    uncovered.append(r)
                    continue
            elif len(slip_roots.get(r.root, ())) == 1:
                g = next(iter(slip_roots[r.root]))
            elif r.category == "roc" and len(_roc_match(r, groups)) == 1:
                # A hand-entered return of capital (a T3's box 42) goes
                # with the one slip of its fund showing that amount.
                g = _roc_match(r, groups)[0]
            elif not where_held and len(groups) == 1 and not any(
                    x.hashes and gx is None for x, gx in placed):
                g = next(iter(groups))
            elif unscoped:
                g = "*"
            else:
                unattributed.append(r)
                continue
        if g is None:
            g = "*" if unscoped else None
        if g is None:
            uncovered.append(r)
        else:
            groups[g]["rows"].append(r)
    if unattributed:
        inc = _income_by_source([r for r in unattributed
                                 if r.tax_date.startswith(ystr)])
        for src, e in sorted(inc.items()):
            notes.append(f"{acct}: {_fmt_amounts(e['amounts'])} from {src} "
                         f"is not tied to one broker account (its "
                         f"securities are held at several) — compared "
                         f"with no slip")
    if uncovered:
        inc = _income_by_source([r for r in uncovered
                                 if r.tax_date.startswith(ystr)])
        if inc:
            total = _sum_amounts(inc)
            coverage_no_slip.append({"account": acct, "sources": sorted(inc),
                                     "income": {k: round(v, 2) for k, v
                                                in total.items()},
                                     "small_interest_only": False})
            _issue(issues, acct, "no-slip",
                   f"{acct}: income from {', '.join(sorted(inc))} "
                   f"({_fmt_amounts(total)}) is on no slip — every slip of "
                   f"the account names another broker account")
    out_groups = []
    for key in sorted(groups):
        g = groups[key]
        g["acct_rows"] = a_rows
        out_groups.append(_audit_group(acct, key, g, year, tol, rate, issues,
                                       sugg_cgd, sugg_tt, notes,
                                       slips_no_books))
    st = ("differences" if any(i["account"] == acct for i in issues)
          else "ok")
    return {"account": acct, "groups": out_groups, "status": st}


def _roc_match(r: BookRow, groups: Dict[str, Dict[str, Any]]) -> List[str]:
    """The groups whose slips give the row's fund this return of capital
    (a T3's box 42, or an IB report's T3 component)."""
    out = []
    for key, g in groups.items():
        for sl in g["slips"]:
            amts = []
            if sl.type == "T3" and sl.security == r.root:
                amts.append(sl.amounts.get("roc", 0.0))
            for p in sl.payments:
                if _root(p.symbol) == r.root:
                    amts.append(p.amount("roc", base=True))
            if any(a and abs(a - r.cad) <= 0.01 for a in amts):
                out.append(key)
                break
    return out


def _income_by_source(rows: List[BookRow]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        src = r.source or "(no input file)"
        e = out.setdefault(src, {"amounts": {}})
        e["amounts"][r.category] = e["amounts"].get(r.category, 0.0) + r.cad
    return {k: v for k, v in out.items()
            if any(abs(x) > 0.005 for x in v["amounts"].values())}


def _sum_amounts(inc: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    t: Dict[str, float] = {}
    for v in inc.values():
        for k, x in v["amounts"].items():
            t[k] = t.get(k, 0.0) + x
    return t


def _fmt_amounts(d: Dict[str, float]) -> str:
    from taxjson.lib.report_model import fmt_money
    return ", ".join(f"{CAT_WORDS.get(k, k)} {fmt_money(v)} CAD"
                     for k, v in sorted(d.items(), key=lambda kv:
                                        CATEGORIES.index(kv[0])
                                        if kv[0] in CATEGORIES else 99)
                     if abs(v) > 0.005)


def _audit_group(acct, key, g, year, tol, rate, issues, sugg_cgd, sugg_tt,
                 notes, slips_no_books) -> Dict[str, Any]:
    from taxjson.lib.report_model import fmt_money
    ystr = str(year)
    slips: List[Slip] = g["slips"]
    rows: List[BookRow] = g["rows"]
    in_year = [r for r in rows if r.tax_date.startswith(ystr)]
    where = acct + (f" ({g['masked']})" if g["masked"] else "")
    if not in_year:
        for s in slips:
            slips_no_books.append({"account": acct, "slip": s.label(),
                                   "where": s.where})
        _issue(issues, acct, "no-books",
               f"{where}: {len(slips)} slip(s) and no income in the books "
               f"for {year} — an export missing from inputs/{acct}/?")
    foreign_curs = sorted({s.currency for s in slips} - {"CAD"})
    # Per-security T3 split (and every per-security slip figure).
    splits: Dict[str, Dict[str, float]] = {}
    for s in slips:
        if s.type == "T3" and s.security:
            sp = splits.setdefault(s.security, {})
            for c in SPLIT_CATS:
                if s.amounts.get(c):
                    sp[c] = sp.get(c, 0.0) + s.amounts[c]
    buckets: List[Dict[str, Any]] = []
    for bucket in ["CAD"] + foreign_curs:
        b_slips = [s for s in slips if s.currency == bucket]
        b_rows = [r for r in in_year
                  if (r.currency == bucket if bucket != "CAD"
                      else r.currency not in foreign_curs)]
        if not b_slips:
            tot = sum(r.cad for r in b_rows)
            if abs(tot) >= 0.005:
                _issue(issues, acct, "no-slip",
                       f"{where}: {bucket} income in the books "
                       f"({fmt_money(tot)} CAD) and no {bucket} slip")
            continue
        books: Dict[str, Money] = {c: Money() for c in CATEGORIES}
        per_root: Dict[str, Dict[str, Money]] = {}
        for r in b_rows:
            m = _row_money(r, bucket, rate)
            cat = r.category
            if r.cg_part:
                cg = _row_money(r, bucket, rate, r.cg_part)
                books["cg_div"].add(cg)
                per_root.setdefault(r.root, {}).setdefault(
                    "cg_div", Money()).add(cg)
                books[cat].add(m)
                books[cat].add(cg, -1.0)
                rest = Money()
                rest.add(m)
                rest.add(cg, -1.0)
                per_root.setdefault(r.root, {}).setdefault(
                    cat, Money()).add(rest)
            else:
                books[cat].add(m)
                per_root.setdefault(r.root, {}).setdefault(
                    cat, Money()).add(m)
        # A T3 splits a trust's distribution the books carry as one
        # dividend: take the books' income of that security in the
        # T3's proportions (CA-SLIP-01).
        split_notes = []
        for sec, sp in sorted(splits.items()):
            tot = sum(sp.values())
            pr = per_root.get(sec) or {}
            base = Money()
            for c in ("ca_div", "foreign"):
                if c in pr:
                    base.add(pr[c])
            if tot <= 0 or abs(base.amt) < 0.005:
                continue
            # The books carry a trust's distribution as the cash paid:
            # net of the foreign tax the trust withheld (T3 box 34) when
            # no TAX row holds it, and with the T3's return of capital
            # inside it when no ADJUST row books that. The T3's income
            # boxes are gross: split the cash grossed up by box 34, and
            # leave the return of capital to the ROC line.
            s_roc = sum(sl.amounts.get("roc", 0.0) for sl in b_slips
                        if sl.type == "T3" and sl.security == sec)
            s_tax = sum(sl.amounts.get("foreign_tax", 0.0) for sl in b_slips
                        if sl.type == "T3" and sl.security == sec)
            b_roc = sum(_row_money(r, bucket, rate).amt for r in b_rows
                        if r.root == sec and r.category == "roc")
            b_tax = sum(_row_money(r, bucket, rate).amt for r in b_rows
                        if r.root == sec and r.category == "foreign_tax")
            missing = max(0.0, s_roc - b_roc)
            gross_up = max(0.0, s_tax - b_tax)
            k = 1.0
            implied_tax = 0.0
            for gu in ([gross_up, 0.0] if gross_up else [0.0]):
                if base.amt and any(
                        abs(base.amt + gu - (tot + m))
                        <= max(tol, 0.005 * (tot + m))
                        for m in {0.0, missing, s_roc}):
                    k = tot / base.amt
                    implied_tax = gu
                    break
            if implied_tax:
                part = Money(amt=implied_tax, alt=implied_tax)
                books["foreign_tax"].add(part)
                notes.append(f"{where}: {sec}'s distributions are booked "
                             f"net of the {fmt_money(implied_tax)} {bucket} "
                             f"of foreign tax the trust withheld (T3 box "
                             f"34): counted here as withheld; claim it "
                             f"from the T3")
            for c in ("ca_div", "foreign"):
                if c in pr:
                    books[c].add(pr[c], -1.0)
                    pr[c] = Money()
            for c, v in sp.items():
                part = Money()
                part.add(base, k * v / tot)
                books[c].add(part)
                pr.setdefault(c, Money()).add(part)
            split_notes.append(sec)
        lines = []
        for c in CATEGORIES:
            slip_amt = sum(s.amounts.get(c, 0.0) for s in b_slips)
            bm = books[c]
            if all(s.covers is not None and c not in s.covers
                   for s in b_slips):
                if abs(bm.amt) >= 0.005:
                    notes.append(
                        f"{where}: {CAT_WORDS[c]} in the books "
                        f"{fmt_money(bm.amt)} {bucket} — IB's dividends "
                        f"report has none; type the T5's box 13 into "
                        f"inputs/slips/{SLIPS_FILE} (type = \"T5\", the "
                        f"IB account as broker_account, boxes = {{ 13 = "
                        f"... }}) to compare it")
                continue
            if abs(slip_amt) < 0.005 and abs(bm.amt) < 0.005:
                continue
            diff = round(bm.amt - slip_amt, 2) + 0.0
            allowed = tol + FX_BAND * abs(bm.fx)
            closest = None
            if bucket == "CAD" and abs(bm.fx) >= 0.005 and bm.alt_ok:
                closest = ("daily" if abs(bm.amt - slip_amt)
                           <= abs(bm.alt - slip_amt) else "annual")
            line = {"category": c, "label": CAT_LABEL[c],
                    "boxes": CAT_BOXES[c], "slip": round(slip_amt, 2),
                    "books": round(bm.amt, 2), "diff": round(diff, 2),
                    "tolerance": round(allowed, 2),
                    "converted": round(bm.fx, 2),
                    "books_annual_average": (round(bm.alt, 2)
                                             if bm.alt_ok and abs(bm.fx)
                                             >= 0.005 else None),
                    "closest": closest}
            if bucket != "CAD":
                r_ = rate.get(bucket)
                cad_daily = sum(_row_money(r, "CAD", rate).amt
                                for r in b_rows if r.category == c)
                line["books_cad_daily"] = round(cad_daily, 2)
                line["slip_cad_annual_average"] = (round(slip_amt * r_, 2)
                                                   if r_ else None)
            ok = abs(diff) <= allowed + 1e-9
            if (not ok and c == "interest" and abs(slip_amt) < 0.005
                    and 0 < bm.amt < INTEREST_SLIP_MIN):
                ok = True
                line["note"] = (f"under {INTEREST_SLIP_MIN:.0f} of "
                                f"interest: no T5 is issued")
            line["status"] = ("under-50" if line.get("note")
                              else "ok" if ok else "differs")
            if not ok:
                _issue(issues, acct, "differs",
                       f"{where} {bucket}: {CAT_WORDS[c]} "
                       f"({CAT_BOXES[c]}) slip {fmt_money(slip_amt)}, "
                       f"books {fmt_money(bm.amt)} ({diff:+,.2f})")
            lines.append(line)
            if c == "cg_div" and not ok and slip_amt > bm.amt and not any(
                    ln.category == "cg_div" for sl in b_slips
                    for ln in sl.lines) and not any(
                    sl.source == "ib" for sl in b_slips):
                notes.append(
                    f"{where}: box 18 capital-gains dividends on the slip "
                    f"({fmt_money(slip_amt)} {bucket}) are "
                    f"{fmt_money(slip_amt - bm.amt)} more than "
                    f"[[capital_gains_dividends]] names — name the "
                    f"split-share or fund corporations they came from "
                    f"(the broker's per-security summary lists them), or "
                    f"type them as [[slip.line]] box 18 lines for the "
                    f"exact tables")
        buckets.append({"currency": bucket,
                        "slips": [s.short_label() for s in b_slips],
                        "lines": lines, "split_by_t3": split_notes})
    payments = _match_payments(acct, where, slips, rows, year, issues)
    securities = _securities(acct, where, slips, rows, year, tol,
                             sugg_cgd, sugg_tt, notes,
                             g.get("acct_rows") or rows)
    record_year = [{"symbol": r.symbol, "paid": r.pay_date,
                    "counted_in": r.tax_date[:4]}
                   for r in rows
                   if r.category in ("ca_div", "foreign") and r.pay_date
                   and r.tax_date and r.pay_date[:4] != r.tax_date[:4]
                   and (r.pay_date.startswith(ystr)
                        or r.tax_date.startswith(ystr))]
    slip_interest = sum(s.amounts.get("interest", 0.0) for s in slips)
    if slip_interest and not any(r.category == "interest" for r in rows):
        notes.append(f"{where}: the slips show interest (T5 box 13 "
                     f"{fmt_money(slip_interest)}) and the books have no "
                     f"INTEREST rows (the export has none): line 12100 "
                     f"takes it from the slip — taxjson leaves interest "
                     f"out of its figures (CA-RPT-06)")
    return {"key": "*" if key == "*" else g["masked"], "account": acct,
            "broker_account": g["masked"] or None,
            "slips": [{"label": s.label(), "where": s.where,
                       "type": s.type, "currency": s.currency,
                       "source": s.source,
                       "boxes": {k: round(v, 2) for k, v in
                                 sorted(s.boxes.items(),
                                        key=lambda kv: int(kv[0]))}
                       if s.source == "typed" else
                       {c: round(v, 2) for c, v in s.amounts.items()}}
                      for s in slips],
            "buckets": buckets, "payments": payments,
            "securities": securities, "record_year": record_year}


def _match_payments(acct, where, slips, rows, year, issues
                    ) -> Dict[str, List[Dict[str, Any]]]:
    """IB's payments (and dated slip lines) against the books' rows of
    the same security, kind and date (within MATCH_DAYS)."""
    ystr = str(year)
    out = {"missing_from_books": [], "missing_from_slip": [],
           "amount_differs": [], "other_year": [], "matched": 0}
    pays = [p for s in slips if s.source == "ib" for p in s.payments]
    if not pays:
        return out
    cand: Dict[Tuple[str, bool], List[BookRow]] = {}
    for r in rows:
        if r.action in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            cand.setdefault((r.root, r.pil), []).append(r)
    # Book payments: one per (root, kind, pay date) — a reversal and its
    # repost on one day are one payment.
    bgroups: Dict[Tuple[str, bool, str], List[BookRow]] = {}
    for (rt, pil), rs in cand.items():
        for r in rs:
            bgroups.setdefault((rt, pil, r.pay_date), []).append(r)
    used = set()
    for p in sorted(pays, key=lambda p: (p.pay_date, p.symbol)):
        rt = _root(p.symbol)
        opts = [(k, v) for k, v in bgroups.items()
                if k[0] == rt and k[1] == p.pil and k not in used
                and _days(k[2], p.pay_date) <= MATCH_DAYS]
        if not opts:
            if abs(p.gross) < 0.005:
                continue        # a withholding adjustment, no payment
            if p.pay_date.startswith(ystr) or not p.pay_date:
                out["missing_from_books"].append({
                    "symbol": p.symbol, "date": p.pay_date,
                    "currency": p.currency, "amount": round(p.gross, 2),
                    "pil": p.pil})
                _issue(issues, acct, "missing-from-books",
                       f"{where}: {p.symbol} {p.pay_date} "
                       f"{'payment in lieu' if p.pil else 'dividend'} "
                       f"{p.gross:,.2f} {p.currency} is on IB's report and "
                       f"not in the books")
            else:
                out["other_year"].append({
                    "symbol": p.symbol, "date": p.pay_date,
                    "currency": p.currency, "amount": round(p.gross, 2),
                    "note": f"paid in {p.pay_date[:4]}; IB's {year} report "
                            f"includes it"})
            continue
        k, grp = min(opts, key=lambda kv: _days(kv[0][2], p.pay_date))
        used.add(k)
        out["matched"] += 1
        book_amt = sum(r.native for r in grp)
        # The books may hold the payment without its return of capital
        # (a DIVIDEND line taking it back out, a suggestion applied).
        roc = sum(c.gross for c in p.components
                  if _IB_CAT.get(c.category or "") == "roc")
        if all(abs(book_amt - want) > 0.01 + 0.0005 * abs(p.gross)
               for want in ((p.gross, p.gross - roc) if roc
                            else (p.gross,))):
            out["amount_differs"].append({
                "symbol": p.symbol, "date": p.pay_date,
                "currency": p.currency, "slip": round(p.gross, 2),
                "books": round(book_amt, 2),
                "diff": round(book_amt - p.gross, 2)})
            _issue(issues, acct, "amount-differs",
                   f"{where}: {p.symbol} {p.pay_date} IB's report "
                   f"{p.gross:,.2f}, books {book_amt:,.2f} {p.currency}")
        tds = {r.tax_date[:4] for r in grp}
        if p.pay_date.startswith(ystr) and ystr not in tds:
            out["other_year"].append({
                "symbol": p.symbol, "date": p.pay_date,
                "currency": p.currency, "amount": round(p.gross, 2),
                "note": f"the books count it in {', '.join(sorted(tds))} "
                        f"(record date, CA-INC-DATE-TRUST)"})
    for k, grp in sorted(bgroups.items()):
        if k in used:
            continue
        r0 = grp[0]
        if not any(r.tax_date.startswith(ystr) for r in grp):
            continue
        amt = sum(r.native for r in grp)
        if abs(amt) < 0.005:
            continue
        out["missing_from_slip"].append({
            "symbol": r0.symbol, "date": r0.pay_date,
            "currency": r0.currency, "amount": round(amt, 2),
            "pil": r0.pil})
        _issue(issues, acct, "missing-from-slip",
               f"{where}: {r0.symbol} {r0.pay_date} "
               f"{'payment in lieu' if r0.pil else 'dividend'} "
               f"{amt:,.2f} {r0.currency} is in the books and not on IB's "
               f"report")
    return out


def _securities(acct, where, slips, rows, year, tol, sugg_cgd, sugg_tt,
                notes, acct_rows=()) -> List[Dict[str, Any]]:
    """Per-security figures where the slips have them: box 18 against
    [[capital_gains_dividends]], ROC against the books' ADJUST rows,
    and a T3's split. Suggestions are the lines that bring the books to
    the slip."""
    from taxjson.lib.report_model import fmt_money
    ystr = str(year)
    in_year = [r for r in rows if r.tax_date.startswith(ystr)]
    sec_slip: Dict[str, Dict[str, float]] = {}
    sec_cur: Dict[str, str] = {}
    dated_cg: Dict[str, List[Tuple[Any, float]]] = {}
    dated_roc: Dict[str, List[Tuple[Any, float]]] = {}
    # A fund with its own T3 slip (typed, or --import-cra): its return of
    # capital is that slip's box 42, not IB's report's again.
    t3_typed = {s.security for s in slips
                if s.type == "T3" and s.source != "ib" and s.security}
    for s in slips:
        if s.source == "ib":
            if s.type != "T5":
                continue
            for p in s.payments:
                rt = _root(p.symbol)
                for c in p.components:
                    cat = _IB_CAT.get(c.category or "")
                    if cat == "roc" and rt in t3_typed:
                        continue
                    if cat in ("cg_div", "roc"):
                        d = sec_slip.setdefault(rt, {})
                        d[cat] = d.get(cat, 0.0) + c.gross
                        sec_cur[rt] = p.currency
                        (dated_cg if cat == "cg_div" else dated_roc
                         ).setdefault(rt, []).append((p, c.gross))
            continue
        if s.security:
            d = sec_slip.setdefault(s.security, {})
            for c in ("cg_div", "roc"):
                if s.amounts.get(c):
                    d[c] = d.get(c, 0.0) + s.amounts[c]
                    sec_cur[s.security] = s.currency
        for ln in s.lines:
            if ln.category in ("cg_div", "roc"):
                d = sec_slip.setdefault(ln.root, {})
                d[ln.category] = d.get(ln.category, 0.0) + ln.amount
                sec_cur[ln.root] = s.currency
    out = []
    for rt in sorted(sec_slip):
        sl = sec_slip[rt]
        cur = sec_cur.get(rt, "CAD")
        rrows = [r for r in in_year if r.root == rt]
        sym = (next((r.symbol for r in rrows
                     if r.category in ("ca_div", "foreign")), "")
               or next((r.symbol for r in rrows), "") or rt)
        bk_cg = sum(r.cg_part for r in rrows if r.currency == cur)
        bk_roc = sum(r.native for r in rrows
                     if r.category == "roc" and r.currency == cur)
        rec = {"symbol": sym, "currency": cur,
               "slip": {k: round(v, 2) for k, v in sl.items()},
               "books": {"cg_div": round(bk_cg, 2),
                         "roc": round(bk_roc, 2)}}
        out.append(rec)
        # Box 18.
        s_cg = sl.get("cg_div", 0.0)
        if s_cg - bk_cg > max(0.005, min(tol, 0.01 * s_cg) if s_cg else 0):
            _suggest_cgd(acct, rt, sym, cur, s_cg, bk_cg, rrows,
                         dated_cg.get(rt), year, sugg_cgd, notes, where)
        elif bk_cg - s_cg > tol:
            notes.append(f"{where}: [[capital_gains_dividends]] names "
                         f"{fmt_money(bk_cg)} {cur} of {sym} and the slip "
                         f"{fmt_money(s_cg)} — check the entry's amount")
        # ROC.
        s_roc = sl.get("roc", 0.0)
        if s_roc - bk_roc > max(0.005, 0.0):
            _suggest_roc(acct, rt, sym, cur, s_roc - bk_roc, rrows,
                         dated_roc.get(rt), year, sugg_tt, notes, where,
                         slips, acct_rows)
        elif bk_roc - s_roc > tol and s_roc:
            notes.append(f"{where}: the books return {fmt_money(bk_roc)} "
                         f"{cur} of {sym}'s capital and the slip "
                         f"{fmt_money(s_roc)} — check the ADJUST rows "
                         f"(`taxjson roc-sum`)")
    # A T3's split, said once per security.
    for s in slips:
        if s.type == "T3" and s.security and s.source == "typed":
            parts = [f"{CAT_WORDS[c]} {fmt_money(v)}"
                     for c, v in s.amounts.items() if c in SPLIT_CATS
                     and abs(v) > 0.005]
            if len(parts) > 1:
                notes.append(f"{where}: the T3 for {s.security} splits its "
                             f"distribution ({', '.join(parts)}); the books "
                             f"carry it as one dividend — file the T3's "
                             f"boxes (CA-INC-02); taxjson's estimate counts "
                             f"the whole as eligible dividends "
                             f"(CA-EST-TRUST)")
    t3_ib = {}
    for s in slips:
        if s.type == "T3" and s.source == "ib" and not s.detail_only:
            t3_ib[s.security] = s
    for sec, s in sorted(t3_ib.items()):
        parts = [f"{CAT_WORDS[c]} {fmt_money(v)}"
                 for c, v in s.amounts.items() if abs(v) > 0.005]
        notes.append(f"{where}: IB's T3 for {sec}: {', '.join(parts)} "
                     f"{s.currency}; the books carry the distribution as "
                     f"one dividend — file the T3's boxes (CA-INC-02)")
    return out


def _suggest_cgd(acct, rt, sym, cur, s_cg, bk_cg, rrows, dated, year,
                 sugg_cgd, notes, where) -> None:
    from taxjson.lib.report_model import fmt_money
    divs = [r for r in rrows if r.action == "DIVIDEND"
            and r.currency == cur]
    if dated:
        for p, amt in dated:
            if p.pil:
                notes.append(f"{where}: {p.symbol} {p.pay_date}: "
                             f"{fmt_money(amt)} {p.currency} of a payment "
                             f"in lieu is a box-18 capital-gains dividend "
                             f"on the slip; [[capital_gains_dividends]] "
                             f"covers dividends only, so the books keep it "
                             f"as income")
                continue
            hit = [r for r in divs if _days(r.pay_date, p.pay_date)
                   <= MATCH_DAYS]
            if not hit:
                continue
            r0 = min(hit, key=lambda r: _days(r.pay_date, p.pay_date))
            booked = sum(r.cg_part for r in hit if r.pay_date == r0.pay_date)
            if amt - booked <= 0.005:
                continue
            if booked > 0:
                notes.append(f"{where}: change the [[capital_gains_"
                             f"dividends]] entry for {r0.symbol} "
                             f"{r0.pay_date} to amount = {amt:.2f}")
                continue
            sugg_cgd.append(_cgd_entry(r0.symbol, f"date = {r0.pay_date}",
                                       amt, acct, r0.pay_date, None))
        return
    if not divs:
        notes.append(f"{where}: the slip shows a box-18 capital-gains "
                     f"dividend of {fmt_money(s_cg)} {cur} for {sym} and "
                     f"the books have no {cur} dividend of it to name")
        return
    if bk_cg > 0:
        notes.append(f"{where}: [[capital_gains_dividends]] names "
                     f"{fmt_money(bk_cg)} {cur} of {sym}; the slip says "
                     f"{fmt_money(s_cg)} — change the entry")
        return
    total = sum(r.native for r in divs)
    if s_cg > total + 0.005:
        notes.append(f"{where}: the slip's box 18 for {sym} "
                     f"({fmt_money(s_cg)} {cur}) is more than its "
                     f"dividends in the books ({fmt_money(total)})")
        return
    sugg_cgd.append(_cgd_entry(divs[0].symbol, f"year = {year}", s_cg, acct,
                               None, year))


def _cgd_entry(symbol, when, amount, acct, date, year) -> Dict[str, Any]:
    toml = (f"[[capital_gains_dividends]]\nsymbol = \"{symbol}\"\n{when}\n"
            f"amount = {amount:.2f}\naccount = \"{acct}\"")
    return {"symbol": symbol, "date": date, "year": year,
            "amount": round(amount, 2), "account": acct, "toml": toml}


def _suggest_roc(acct, rt, sym, cur, missing, rrows, dated, year, sugg_tt,
                 notes, where, slips, acct_rows=()) -> None:
    from taxjson.lib.report_model import fmt_money
    lines = sugg_tt.setdefault(acct, [])
    # Never a line the books already hold (in another broker account's
    # rows, another year by its record date, or not yet counted here):
    # applying it would book the return of capital twice.
    counted = {id(x) for x in rrows}

    def held_already(action: str, amt: float) -> Optional[BookRow]:
        for r in acct_rows:
            if r.root != rt or r.currency != cur or id(r) in counted:
                continue
            if action == "ADJUST" and r.category == "roc" \
                    and abs(r.native - amt) <= 0.01:
                return r
            if action == "DIVIDEND" and r.action == "DIVIDEND" \
                    and abs(r.native + amt) <= 0.01:
                return r
        return None

    def add(line: str, action: str, amt: float) -> None:
        r = held_already(action, amt)
        if r is not None:
            notes.append(f"{where}: {sym}: the books already hold "
                         f"{action} {r.pay_date} {fmt_money(abs(amt))} "
                         f"{cur} ({r.source or 'no input file'}), not "
                         f"counted against this slip — check its date "
                         f"(record=) and account rather than adding it "
                         f"again")
        elif line not in lines:
            lines.append(line)
    divs = [r for r in rrows if r.category in ("ca_div", "foreign")
            and r.currency == cur and r.action == "DIVIDEND"]
    if dated:
        for p, amt in dated:
            hit = [r for r in divs if _days(r.pay_date, p.pay_date)
                   <= MATCH_DAYS]
            r0 = (min(hit, key=lambda r: _days(r.pay_date, p.pay_date))
                  if hit else None)
            s = r0.symbol if r0 else sym
            d = r0.pay_date if r0 else p.pay_date
            add(f"ADJUST {d} 09:30:00 {s} {cur} -{amt:.2f} type=roc",
                "ADJUST", amt)
            if r0 is not None:
                booked = sum(r.native for r in hit
                             if r.pay_date == r0.pay_date)
                if abs(booked - p.gross) <= 0.01 + 0.0005 * abs(p.gross):
                    # The dividend row holds the whole payment, ROC
                    # included: take the ROC back out of income.
                    add(f"DIVIDEND {d} 09:30:00 {s} 0 {cur} 0 -{amt:.2f}",
                        "DIVIDEND", amt)
        return
    # A typed T3: no date on the slip — the year's last distribution
    # (with its record date when the books date it by that,
    # CA-INC-DATE-TRUST / CA-INC-DATE-ROC-TRUST).
    ystr = str(year)
    yrows = [r for r in divs if r.tax_date.startswith(ystr)]
    lr = max(yrows, key=lambda r: (r.tax_date, r.pay_date)) \
        if yrows else None
    last = lr.pay_date if lr else f"{year}-12-31"
    rec = (f" record={lr.tax_date}" if lr and lr.tax_date != lr.pay_date
           else "")
    s = lr.symbol if lr else sym
    add(f"ADJUST {last} 16:00:00 {s} {cur} -{missing:.2f} type=roc{rec}",
        "ADJUST", missing)
    div_line = (f"DIVIDEND {last} 16:00:00 {s} 0 {cur} 0 -{missing:.2f}"
                + (f"{rec} label=distribution" if rec else ""))
    slip_income = sum(sl.amounts.get(c, 0.0) for sl in slips
                      if sl.security == rt for c in SPLIT_CATS)
    # The cash paid, grossed up by the foreign tax the trust withheld
    # (T3 box 34) when no TAX row holds it.
    s_tax = sum(sl.amounts.get("foreign_tax", 0.0) for sl in slips
                if sl.security == rt and sl.type == "T3")
    b_tax = sum(r.native for r in rrows if r.category == "foreign_tax"
                and r.tax_date.startswith(ystr))
    book_income = sum(r.native for r in yrows) + max(0.0, s_tax - b_tax)
    if slip_income and abs(book_income - slip_income - missing) <= max(
            0.02, 0.0005 * slip_income):
        add(div_line, "DIVIDEND", missing)
    elif not slip_income:
        notes.append(f"{where}: {sym}'s return of capital "
                     f"({fmt_money(missing)} {cur}) — if the books' "
                     f"dividend includes it, also add `{div_line}`")


# ------------------------------------------------------------ T5008
def _t5008(root: Path, slips: List[Slip], year: int, tol: float,
           rate: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
    """Aggregated T5008 slips (per type code, IB's 'Various') against the
    account's computed dispositions: options (OPC) apart from the rest.
    Information only — `taxjson reconcile-slips` reconciles per
    security (the checklist's t5008 step)."""
    t = [s for s in slips if s.type == "T5008" and not s.empty]
    if not t:
        return []
    from taxjson.bin.taxjson_reconcile_slips import load_computed
    from taxjson.lib.report_model import resolve_gains_files
    from taxjson.lib.ticker_map import is_option_ticker
    out = []
    cache = Path(root) / "work"
    for acct in sorted({s.account for s in t}):
        gf = resolve_gains_files(cache, acct).get(acct)
        comp = load_computed([gf], year) if gf else {}
        books = {"options": {"proceeds": 0.0, "cost": 0.0},
                 "securities": {"proceeds": 0.0, "cost": 0.0}}
        for rt, rec in comp.items():
            k = "options" if is_option_ticker(rt) else "securities"
            books[k]["proceeds"] += float(rec.get("proceeds_gross") or 0.0)
            books[k]["cost"] += float(rec.get("cost") or 0.0)
        for k in ("options", "securities"):
            ss = [s for s in t if s.account == acct
                  and (s.code == "OPC") == (k == "options")]
            if not ss:
                continue
            p_cad = c_cad = 0.0
            approx = False
            for s in ss:
                r = 1.0 if s.currency == "CAD" else rate.get(s.currency)
                if r is None:
                    continue
                approx = approx or s.currency != "CAD"
                p_cad += s.amounts.get("proceeds", 0.0) * r
                c_cad += s.amounts.get("cost", 0.0) * r
            out.append({"account": acct, "class": k,
                        "codes": sorted({s.code or "?" for s in ss}),
                        "slip_proceeds": round(p_cad, 2),
                        "books_proceeds": round(books[k]["proceeds"], 2),
                        "slip_cost": round(c_cad, 2),
                        "books_cost": round(books[k]["cost"], 2),
                        "at_annual_average": approx})
    return out


# ------------------------------------------------------------ questions
def question_keys(rep: Dict[str, Any]) -> List[str]:
    """One checklist question per account with findings: the account
    and a digest of its findings (a later, different finding is a new
    question; lib/checklist QUESTION_STEPS)."""
    by: Dict[str, List[str]] = {}
    for i in rep.get("issues") or []:
        by.setdefault(i["account"], []).append(i["kind"] + ":" + i["text"])
    out = []
    for a, items in sorted(by.items()):
        h = hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:8]
        out.append(f"{a}|{len(items)}|{h}")
    return out


def key_text(key: str) -> str:
    p = key.split("|")
    if len(p) == 3:
        return f"{p[0]} ({p[1]} finding{'s' if p[1] != '1' else ''})"
    return key


# ------------------------------------------------------------ template
def template(root: Path, cfg: Dict[str, Any]) -> str:
    """A slips.toml to fill in: a T5 per taxable account and currency
    with income in the books, the boxes the books suggest commented
    out (type the slip's figures; never the books')."""
    settings = cfg.get("settings") or {}
    year = int(settings.get("year"))
    accts = slip_accounts(cfg)
    books, _p = load_books(root, cfg, year, accts)
    ystr = str(year)
    # The broker accounts IB's dividends reports cover: read as their
    # T5 and T3s, so no table is printed for them.
    reports_map: List[Dict[str, str]] = []
    sf = slips_dir(root) / SLIPS_FILE
    if sf.is_file():
        reports_map = load_slips_file(sf, cfg, year)[1]
    covered: Dict[str, Dict[str, str]] = {}
    for _path, rep, acct in _reports(root, books, reports_map, []):
        covered.setdefault(acct, {})[rep.account_hash] = rep.account_masked
    out = [f"# T5 / T3 slips for {year}, typed from the PDFs "
           f"(`taxjson slip-audit` compares them with the books).",
           "# One [[slip]] per slip. Type each box as the slip prints it; "
           "leave out the boxes it leaves blank.",
           "# Never type a name, a SIN, an address or an account number "
           "here except in broker_account",
           "# (only to tell two broker accounts of one account label "
           "apart; it is hashed, never printed).",
           f"year = {year}", ""]
    t5_boxes = (("24", "ca_div", "actual amount of eligible dividends"),
                ("10", "ca_div", "actual amount of other than eligible "
                 "dividends"),
                ("18", "cg_div", "capital gains dividends"),
                ("13", "interest", "interest from Canadian sources"),
                ("15", "foreign", "foreign income"),
                ("16", "foreign_tax", "foreign tax paid"))
    any_slip = False
    for acct in accts:
        rows = [r for r in books if r.account == acct
                and r.tax_date.startswith(ystr)]
        cov = covered.get(acct) or {}
        if cov:
            out += [f"# {acct}: IB's dividends report of "
                    f"{', '.join(sorted(cov.values()))} is read as that "
                    f"broker account's T5 and T3 slips — no table for it.",
                    "# (It has no interest: a T5 box 13 from IB goes in a "
                    "table of its own with the IB account as "
                    "broker_account.)", ""]
            rows = [r for r in rows if not set(r.hashes) & set(cov)]
        if not rows:
            continue
        for cur in sorted({r.currency for r in rows}):
            cats = {r.category for r in rows if r.currency == cur}
            any_slip = True
            out += ["[[slip]]", 'type = "T5"', 'issuer = ""',
                    f'account = "{acct}"', f'currency = "{cur}"',
                    '# broker_account = ""', "[slip.boxes]"]
            for box, cat, what in sorted(t5_boxes,
                                         key=lambda b: b[1] not in cats):
                seen = "  (the books have some)" if cat in cats else ""
                out.append(f"# {box} = 0.00   # {what}{seen}")
            out.append("")
        out += ["# A T3 per fund:",
                "# [[slip]]", '# type = "T3"', '# issuer = ""',
                f'# account = "{acct}"', '# currency = "CAD"',
                '# security = "XYZQ.TO"',
                "# boxes = { 49 = 0.00, 21 = 0.00, 25 = 0.00, 26 = 0.00, "
                "34 = 0.00, 42 = 0.00 }", ""]
    if not any_slip:
        out.append("# The books have no taxable income for this year.")
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------ text
def _m(v: Any) -> str:
    from taxjson.lib.report_model import fmt_money
    return "" if v is None else fmt_money(v)


def render(rep: Dict[str, Any], width_: Optional[int] = None,
           details: bool = False) -> List[str]:
    """The report in the house style (lib/out.Doc). The default view is
    the essentials (docs/output-style.md): the slips read, the tables,
    the findings and the lines to add, each said in one line; `details`
    adds the annual-average rates, the per-payment notes, each
    suggestion's reason and the Notes section."""
    from taxjson.lib.out import Doc, act
    d = Doc(f"SLIP AUDIT — tax year {rep['year']}, CAD, tolerance "
            f"{rep['tolerance']:.2f}", width_=width_)
    d.section("SLIPS")
    if not rep["sources"]:
        if details:
            d.para("No slips in inputs/slips/: type them into inputs/slips/"
                   f"{SLIPS_FILE} (`taxjson slip-audit --template` prints "
                   "one to fill in), or add IB's dividends report "
                   "(U*.YYYY.dividends.csv).")
        else:
            d.line(act("No slips in inputs/slips/: type them into "
                       f"{SLIPS_FILE}", "tjs slip-audit --template"))
    for s in rep["sources"]:
        if s["kind"] == "slips.toml":
            d.item(f"{s['file']}: {s['slips']} slip(s)")
        elif details:
            d.item(f"{s['file']}: IB's dividends report for "
                   f"{s['broker_account']}, read as account "
                   f"{s['account']}'s T5 and T3 slips ({s['payments']} "
                   f"payments, amounts at IB's rate)")
        else:
            d.item(f"{s['file']}: IB's dividends report, account "
                   f"{s['account']} ({s['payments']} payments)")
    for cur, a in sorted((rep.get("annual_average") or {}).items()):
        if not details:
            break
        if a:
            d.item(f"Annual average {cur}/CAD {a['rate']:.4f}: "
                   f"{a['source']}")
        else:
            d.item(f"Annual average {cur}/CAD: none (no {rep['year']} "
                   f"Bank of Canada rates in the FX cache; `taxjson run` "
                   f"fetches them)")
    legend = True
    for acc in rep["accounts"]:
        for g in acc["groups"]:
            head = acc["account"].upper() + (
                f" — {g['broker_account']}" if g["broker_account"] else "")
            d.section(head)
            for b in g["buckets"]:
                d.para(f"{b['currency']}: {', '.join(b['slips'])}")
                if legend:
                    # The legend, before the first table (Essentials
                    # first).
                    d.line("SLIP: the slip's boxes; BOOKS: the books' "
                           "income; DIFF = BOOKS − SLIP.")
                    legend = False
                if b["currency"] == "CAD":
                    hdr = ["ITEM", "BOXES", "SLIP", "BOOKS", "DIFF",
                           "STATUS", "AT AVERAGE", "CLOSER TO"]
                    body = [[ln["label"], ln["boxes"], _m(ln["slip"]),
                             _m(ln["books"]), f"{ln['diff']:+,.2f}",
                             ln["status"].replace("-", " "),
                             _m(ln.get("books_annual_average")),
                             ln.get("closest") or ""] for ln in b["lines"]]
                    d.table(hdr, body, drop=(1, 6, 7))
                else:
                    cur = b["currency"]
                    hdr = ["ITEM", "BOXES", f"SLIP {cur}", f"BOOKS {cur}",
                           "DIFF", "STATUS", "BOOKS CAD DAILY",
                           "SLIP CAD AVERAGE"]
                    body = [[ln["label"], ln["boxes"], _m(ln["slip"]),
                             _m(ln["books"]), f"{ln['diff']:+,.2f}",
                             ln["status"].replace("-", " "),
                             _m(ln.get("books_cad_daily")),
                             _m(ln.get("slip_cad_annual_average"))]
                            for ln in b["lines"]]
                    d.table(hdr, body, drop=(1, 7, 6))
                if b["split_by_t3"] and details:
                    d.para("The books' income of "
                           + ", ".join(b["split_by_t3"])
                           + " is split as its T3 splits it.")
                d.blank()
            p = g["payments"]
            items = []
            for e in p["missing_from_books"]:
                items.append(f"Not in the books: {e['symbol']} {e['date']} "
                             f"{_m(e['amount'])} {e['currency']}"
                             + (" (payment in lieu)" if e["pil"] else ""))
            for e in p["missing_from_slip"]:
                items.append(f"Not on the slip: {e['symbol']} {e['date']} "
                             f"{_m(e['amount'])} {e['currency']}"
                             + (" (payment in lieu)" if e["pil"] else ""))
            for e in p["amount_differs"]:
                items.append(f"Amount differs: {e['symbol']} {e['date']} "
                             f"slip {_m(e['slip'])}, books {_m(e['books'])} "
                             f"{e['currency']}")
            for e in p["other_year"]:
                items.append(f"Another year: {e['symbol']} {e['date']} "
                             f"{_m(e['amount'])} {e['currency']}"
                             + (f" — {e['note']}" if details else ""))
            if (p.get("matched") and details) or items:
                d.para(f"Payments on IB's report: {p.get('matched', 0)} "
                       f"matched to the books' rows"
                       + (":" if items else "."))
                d.items(items)
            ry = [f"Counted in {e['counted_in']}: {e['symbol']} paid "
                  f"{e['paid']} (record date, CA-INC-DATE-TRUST; a slip "
                  f"dated by the pay date counts it in {e['paid'][:4]})"
                  for e in g["record_year"]]
            if ry and details:
                d.para("Dated by the record date:")
                d.items(ry)
    cov = rep["coverage"]
    if cov["no_slip"] or cov["slips_without_books"]:
        d.section("COVERAGE")
        for e in cov["no_slip"]:
            what = ", ".join(e["sources"])
            amt = _fmt_amounts(e["income"])
            if e.get("small_interest_only"):
                if details:
                    d.item(f"{e['account']}: only {amt} (from {what}) — no "
                           f"T5 is issued below {INTEREST_SLIP_MIN:.0f} of "
                           f"interest; it is still income")
                else:
                    d.item(f"{e['account']}: only {amt}, below the T5 "
                           f"minimum; still income")
            elif details:
                d.item(f"{e['account']}: {amt} from {what} is on no slip")
            else:
                d.item(f"{e['account']}: {amt} is on no slip")
        for e in cov["slips_without_books"]:
            d.item(f"{e['account']}: {e['slip']} ({e['where']}) has no "
                   f"income in the books")
    if rep.get("t5008"):
        d.section("T5008 (aggregated slips; `taxjson reconcile-slips` "
                  "reconciles per security)")
        if any(t["at_annual_average"] for t in rep["t5008"]):
            d.line("*: converted at the annual average.")
        hdr = ["ACCOUNT", "CLASS", "CODES", "SLIP PROCEEDS",
               "BOOKS PROCEEDS", "SLIP COST", "BOOKS COST"]
        body = [[t["account"], t["class"], "/".join(t["codes"]),
                 _m(t["slip_proceeds"]) + ("*" if t["at_annual_average"]
                                            else ""),
                 _m(t["books_proceeds"]), _m(t["slip_cost"]),
                 _m(t["books_cost"])] for t in rep["t5008"]]
        d.table(hdr, body, drop=(2, 5, 6))
    sg = rep["suggestions"]
    if sg["capital_gains_dividends"] or sg["tt_lines"]:
        d.section("SUGGESTIONS")
        if sg["capital_gains_dividends"]:
            d.para("Add to taxjson.toml (T5 box 18 is a capital gain, "
                   "CA-INC-06; `taxjson divs-sum` then shows it apart):"
                   if details else "Add to taxjson.toml (T5 box 18):")
            for e in sg["capital_gains_dividends"]:
                d.blank()
                for ln in e["toml"].splitlines():
                    d.line("  " + ln)
            d.blank()
        for e in sg["tt_lines"]:
            d.para(f"Add to {e['file']} (return of capital lowers the "
                   f"ACB, T3 box 42; a DIVIDEND line with a negative "
                   f"amount takes it out of the dividend income; "
                   f"`taxjson roc-sum` then shows it):" if details else
                   f"Add to {e['file']} (T3 box 42, return of capital):")
            for ln in e["lines"]:
                d.line("  " + ln)
            d.blank()
    if sg["notes"] and details:
        d.section("NOTES")
        for n in sg["notes"]:
            d.item(n)
    if rep.get("problems"):
        d.section("NOT COMPARED")
        d.items(rep["problems"])
    d.blank()
    issues = rep.get("issues") or []
    if issues:
        by: Dict[str, int] = {}
        for i in issues:
            by[i["account"]] = by.get(i["account"], 0) + 1
        if details:
            d.para(f"{len(issues)} finding(s) ("
                   + ", ".join(f"{a} {n}" for a, n in sorted(by.items()))
                   + "): apply the suggestions, or explain each difference "
                   "and mark the checklist's t5-t3 step done.")
        else:
            if sg["notes"]:
                d.line(f"{len(sg['notes'])} note(s) on these findings — "
                       f"tjs slip-audit --details")
            _by = ", ".join(f"{a} {n}" for a, n in sorted(by.items())[:3])
            d.line(act(f"{len(issues)} finding(s) ({_by}"
                       + (" ..." if len(by) > 3 else "")
                       + "): apply the suggestions or explain each",
                       "tjs slip-audit --details"))
    elif not rep["sources"]:
        if details:
            d.para("No slips to compare yet.")
    else:
        if sg["notes"] and not details:
            d.line(f"{len(sg['notes'])} note(s) — tjs slip-audit --details")
        d.para("Every slip agrees with the books within the tolerance.")
    return d.lines()
