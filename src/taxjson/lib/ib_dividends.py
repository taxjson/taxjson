"""Interactive Brokers' dividends report (`U*.YYYY.dividends.csv`): the
per-payment data behind IB's T5 and T3 slips.

IB's slip PDFs aggregate (one T5 per account, a T3 per fund); its
dividends report keeps every payment, split into the slip boxes it
lands in (`RevenueComponent`):

    DividendDetail,Header,DataDiscriminator,Currency,Symbol,Conid,Country,
        ReportDate,ExDate,Shares,RevenueComponent,QualifiedIndicator,Gross,
        GrossInBase,GrossInUSD,Withhold,WithholdInBase,WithholdInUSD
    DividendDetail,Data,Summary,CAD,SAMPX,1,CA,20250930,20250924,100,,...
    DividendDetail,Data,RevenueComponent,CAD,SAMPX,...,T3: Return of Capital,...
    PILDetail,Header,...           (the same columns: payments in lieu)

A `Summary` row is one payment; the `RevenueComponent` rows under it
split it: `T5: Eligible Dividend Income` (T5 box 24), `T5: Capital
Gains` (box 18), `T3: Eligible Dividend Income` (T3 box 49), `T3:
Foreign Non-Business Income` (box 25), `T3: Return of Capital` (box
42), `T3: Capital Gains` (box 21), `T3: Other Income` (box 26), and the
plain `Ordinary Dividend` / `Ordinary Div - NRA Withholding Exempt` /
`Other` (T5 box 24 for a Canadian issuer, box 15 for a foreign one).
`Gross` is in the payment's currency, `GrossInBase` in the account's
base currency at IB's rate; `Withhold*` is the tax withheld (negative).

The `Account` section carries the account holder's NAME: this module
never keeps it (only the account number, which is hashed the way the
books' rows carry it — bin/taxjson_brokerage.hash_broker_account, kept
in memory and in work/ only; slips.toml gets a salted key,
lib/slip_audit.broker_key — and masked to its first two characters for
display). A report holding two accounts is refused: its payment rows
do not say which account they are in.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The marker a dividends report carries (a header line of its detail
# section); enough to tell it from a T5008 CSV in inputs/slips/.
_MARKER_RE = re.compile(r"^﻿?(?:DividendDetail|PILDetail),Header,.*"
                        r"RevenueComponent", re.M)

# (slip type, category) of each RevenueComponent IB prints; matched on
# lower-cased words. The categories are lib/slip_audit's.
_T5_WORDS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("other than eligible",), "non_eligible"),
    (("non-eligible",), "non_eligible"),
    (("non eligible",), "non_eligible"),
    (("eligible dividend",), "eligible"),
    (("capital gain",), "cg_div"),
    (("foreign",), "foreign"),
    (("interest",), "interest"),
    (("other income",), "other"),
)
_T3_WORDS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("return of capital",), "roc"),
    (("other than eligible",), "non_eligible"),
    (("non-eligible",), "non_eligible"),
    (("non eligible",), "non_eligible"),
    (("eligible dividend",), "eligible"),
    (("foreign business",), "foreign"),
    (("foreign non-business",), "foreign"),
    (("foreign non business",), "foreign"),
    (("capital gain",), "trust_cg"),
    (("other income",), "other"),
)


class IBReportError(ValueError):
    """A dividends report that cannot be read (named file and line)."""


def is_dividends_report(path: Path) -> bool:
    """True for an IB dividends report (read the first 64 KiB)."""
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
    except OSError:
        return False
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = head.decode("latin-1")
    return bool(_MARKER_RE.search(text))


def component_category(component: str, country: str
                       ) -> Tuple[str, Optional[str]]:
    """(slip type "T5"|"T3", category or None when unknown) of one
    RevenueComponent. A component without a T5:/T3: prefix (`Ordinary
    Dividend`, `Other` ...) is on the T5: box 24 for a Canadian issuer,
    box 15 for a foreign one."""
    c = (component or "").strip().lower()
    for prefix, table, slip in (("t5:", _T5_WORDS, "T5"),
                                ("t3:", _T3_WORDS, "T3")):
        if c.startswith(prefix):
            rest = c[len(prefix):]
            for words, cat in table:
                if any(w in rest for w in words):
                    return slip, cat
            return slip, None
    return "T5", ("eligible" if (country or "").upper() == "CA"
                  else "foreign")


@dataclass
class Component:
    slip: str                   # T5 | T3
    category: Optional[str]     # None: a component this module does not know
    label: str                  # IB's RevenueComponent text
    gross: float                # payment currency
    gross_base: float           # account base currency (IB's rate)
    withheld: float             # positive = tax withheld, payment currency
    withheld_base: float


@dataclass
class Payment:
    """One payment (a dividend, or a payment in lieu: `pil`)."""
    symbol: str
    country: str
    currency: str
    pay_date: str               # ISO (IB's ReportDate)
    ex_date: str
    pil: bool
    shares: float = 0.0
    components: List[Component] = field(default_factory=list)

    @property
    def gross(self) -> float:
        return sum(c.gross for c in self.components)

    @property
    def gross_base(self) -> float:
        return sum(c.gross_base for c in self.components)

    @property
    def withheld(self) -> float:
        return sum(c.withheld for c in self.components)

    @property
    def withheld_base(self) -> float:
        return sum(c.withheld_base for c in self.components)

    def amount(self, category: str, base: bool = False) -> float:
        return sum((c.gross_base if base else c.gross)
                   for c in self.components if c.category == category)

    @property
    def slip_types(self) -> List[str]:
        return sorted({c.slip for c in self.components})


@dataclass
class Report:
    path: Path
    account_hash: str           # hash_broker_account(AccountNumber)
    account_masked: str         # first 2 characters + ***
    base_currency: str
    payments: List[Payment] = field(default_factory=list)
    unknown: List[Tuple[str, float]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    @property
    def years(self) -> List[str]:
        return sorted({p.pay_date[:4] for p in self.payments if p.pay_date})


def _iso(raw: str, where: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    if not m:
        raise IBReportError(f"{where}: date {s!r} is not YYYYMMDD")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _num(raw: str, col: str, where: str) -> float:
    s = (raw or "").strip()
    if not s:
        return 0.0
    try:
        v = float(s.replace(",", ""))
    except ValueError:
        raise IBReportError(f"{where}: {col} {s!r} is not a number") \
            from None
    if v != v or v in (float("inf"), float("-inf")):
        raise IBReportError(f"{where}: {col} {s!r} is not a number")
    return v


def read_report(path: Path) -> Report:
    """Read one dividends report. Raises IBReportError naming the file
    (its shown name: an account id in it masked) and line."""
    from taxjson.bin.taxjson_brokerage import hash_broker_account
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.positions_reports import mask_account
    name = shown_name(path)
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise IBReportError(f"{name}: cannot read it "
                            f"({e.strerror or e})") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    headers: Dict[str, List[str]] = {}
    acct = ""
    base = ""
    groups: Dict[Tuple, Payment] = {}
    order: List[Tuple] = []
    shares: Dict[Tuple, float] = {}
    summaries: Dict[Tuple, Component] = {}
    unknown: Dict[str, float] = {}
    for lineno, row in enumerate(csv.reader(io.StringIO(text)), 1):
        if len(row) < 2:
            continue
        sec, kind = row[0].strip(), row[1].strip()
        where = f"{name} line {lineno}"
        if kind == "Header":
            headers[sec] = [h.strip() for h in row[2:]]
            continue
        if kind != "Data" or sec not in headers:
            continue
        d = dict(zip(headers[sec], row[2:]))
        if sec == "Account":
            # Only the number and the base currency: the holder's name
            # and the alias are never read into anything.
            num = (d.get("AccountNumber") or "").strip()
            if acct and num and num != acct:
                # Its payment rows name no account: which account each
                # is cannot be told.
                raise IBReportError(
                    f"{where}: a second account ({mask_account(num)}, "
                    f"after {mask_account(acct)}) — the report's payments "
                    f"do not say which account they are in; download one "
                    f"dividends report per account")
            acct = num or acct
            base = (d.get("BaseCurrency") or "").strip().upper() or base
            continue
        if sec not in ("DividendDetail", "PILDetail"):
            continue
        disc = (d.get("DataDiscriminator") or "").strip()
        cur = (d.get("Currency") or "").strip().upper()
        sym = (d.get("Symbol") or "").strip().upper()
        if not sym:
            continue
        key = (sec, cur, sym, (d.get("Conid") or "").strip(),
               _iso(d.get("ReportDate", ""), where),
               _iso(d.get("ExDate", ""), where))
        if key not in groups:
            groups[key] = Payment(
                symbol=sym, country=(d.get("Country") or "").strip().upper(),
                currency=cur, pay_date=key[4], ex_date=key[5],
                pil=(sec == "PILDetail"))
            order.append(key)
        if disc == "Summary":
            shares[key] = _num(d.get("Shares", ""), "Shares", where)
            label = ""
        elif disc == "RevenueComponent":
            label = (d.get("RevenueComponent") or "").strip()
        else:
            continue
        slip, cat = component_category(label, groups[key].country)
        comp = Component(
            slip=slip, category=cat, label=label,
            gross=_num(d.get("Gross", ""), "Gross", where),
            gross_base=_num(d.get("GrossInBase", ""), "GrossInBase", where),
            withheld=-_num(d.get("Withhold", ""), "Withhold", where),
            withheld_base=-_num(d.get("WithholdInBase", ""),
                                "WithholdInBase", where))
        if disc == "Summary":
            summaries[key] = comp
            continue
        if cat is None:
            unknown[label] = unknown.get(label, 0.0) + comp.gross_base
        groups[key].components.append(comp)
    if not acct:
        raise IBReportError(f"{name}: no Account section with an "
                            f"AccountNumber — not an IB dividends report?")
    rep = Report(path=Path(path), account_hash=hash_broker_account(acct),
                 account_masked=mask_account(acct), base_currency=base
                 or "CAD")
    for k in order:
        p = groups[k]
        p.shares = shares.get(k, 0.0)
        summ = summaries.get(k)
        if not p.components:
            if summ is None:
                continue
            # A Summary row with no breakdown: one plain component.
            p.components.append(summ)
            rep.problems.append(
                f"{p.symbol} {p.pay_date}: a payment with no "
                f"RevenueComponent rows — read as an ordinary dividend")
        elif summ is not None and abs(summ.gross - p.gross) > 0.01:
            rep.problems.append(
                f"{p.symbol} {p.pay_date}: the components add up to "
                f"{p.gross:.2f} {p.currency}, the payment to "
                f"{summ.gross:.2f}")
        rep.payments.append(p)
    rep.unknown = sorted(unknown.items())
    return rep
