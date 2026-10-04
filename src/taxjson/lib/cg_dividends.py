"""T5 box 18 capital-gains dividends: the project's override list.

A split-share or mutual-fund corporation may designate part of a
dividend a CAPITAL-GAINS dividend (ITA s.130.1(4) / s.131(1); T5 box 18,
line 17400 of the return). It is a capital gain of the shareholder
(50% inclusion), not a taxable dividend: no gross-up, no dividend tax
credit. No broker export says which payments those are — IB prints
"(Ordinary Dividend)", RBC and Questrade a plain dividend — so the
books carry them as dividends. Only the slip (or IBKR's own dividends
report, "T5: Capital Gains") tells.

The `[[capital_gains_dividends]]` entries of the project's taxjson.toml
name them. Canada only (lib/country CONFIG_COUNTRY): a US project
refuses the table.

    [[capital_gains_dividends]]       # every 2025 ABD dividend
    symbol = "ABD.TO"
    year = 2025
    amount = "all"

    [[capital_gains_dividends]]       # 0.75 of the Jun-13 payment
    symbol = "SAMPMG.TO"
    date = 2025-06-13
    amount = 0.75

    [[capital_gains_dividends]]       # box 18 total for the year
    symbol = "SAMPMH.TO"
    year = 2024
    amount = 123.45
    account = "margin"

- symbol: the dividend row's symbol as the books spell it (after
  ticker.map); a bare root without a suffix (ABD) matches that root's
  Canadian listings only (ABD.TO, ABD.V ...) — never another class or
  preferred series (ABD.PR.B.TO) nor a foreign listing (ABD.US): name
  those in full.
- year or date (exactly one): a year (every dividend whose tax date is
  in it) or one date (the payment's pay date, or its record date when
  the books date it by the record date).
- amount: "all", or the box-18 amount in the row's currency (a
  positive number) — the TOTAL over the matching rows, shared among
  them pro rata.
- account: optional. Without it the entry covers the taxable accounts
  (only they get a T5); name an account to restrict it.

(Before, a project-root `capital_gains_dividends.map` held these as
`SYMBOL WHEN AMOUNT [ACCOUNT]` lines; `taxjson migrate` converts it with
parse_map below.)

The ledger and ACB are never touched (a box-18 dividend does not change
ACB). Only the income views read the entries: `divs-sum` shows the amount
apart from the dividends, and the Canadian estimate moves it from the
grossed-up eligible dividends into capital gains.

An entry that matches no dividend row, an amount above the rows' total,
or matching rows in more than one currency is an error naming the entry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

MAP_NAME = "capital_gains_dividends.map"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")
# Digits with an optional decimal point; thousands commas only in
# groups of three before the point.
_AMOUNT_RE = re.compile(r"(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+")


TABLE = "capital_gains_dividends"


class CgDividendMapError(ValueError):
    """A [[capital_gains_dividends]] entry (or an old
    capital_gains_dividends.map line) the views cannot apply."""


@dataclass(frozen=True)
class Entry:
    line: int            # entry number (1-based; the old map's line)
    symbol: str          # upper-case
    when: str            # "YYYY" or "YYYY-MM-DD"
    amount: Optional[float]   # None = all
    account: str = ""
    legacy: bool = False      # parsed from the old map file

    def where(self) -> str:
        if self.legacy:
            return f"{MAP_NAME} line {self.line}"
        return f"taxjson.toml [[{TABLE}]] #{self.line}"

    def matches_symbol(self, symbol: str) -> bool:
        """Exact, or a bare root against ROOT.<Canadian listing suffix>:
        a preferred series or another class (FTN.PR.A.TO) and a foreign
        listing of a same-root issuer (SAMPMC.US for SAMPMC) are other securities
        and never carry a T5 box-18 dividend of this one (audit A2-0075,
        A2-0228)."""
        from taxjson.lib.income_dating import CA_LISTING_SUFFIXES
        s = (symbol or "").upper()
        if s == self.symbol:
            return True
        if "." in self.symbol:
            return False
        root, _dot, suffix = s.partition(".")
        return root == self.symbol and suffix in CA_LISTING_SUFFIXES

    def matches_date(self, iso: str, pay: str = "") -> bool:
        """A year matches the income (tax) date's year; a date matches
        the pay date or the income date (the record date of a
        trust-dated row) — the documented pay date used to be refused
        for a record-dated row (audit A2-0560)."""
        if len(self.when) == 10:
            return self.when in (iso, pay)
        return iso[:4] == self.when


def parse_map(text: str) -> List[Entry]:
    out: List[Entry] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        where = f"{MAP_NAME} line {n}"
        if len(parts) not in (3, 4):
            raise CgDividendMapError(
                f"{where}: expected SYMBOL WHEN AMOUNT [ACCOUNT] (e.g. "
                f"`ABD.TO 2025 all` or `ABD.TO 2025-06-16 1.25`), got "
                f"{raw.strip()!r}")
        sym, when, amt = parts[0].upper(), parts[1], parts[2]
        if not (_YEAR_RE.match(when) or _DATE_RE.match(when)):
            raise CgDividendMapError(
                f"{where}: WHEN must be a year (2025) or a date "
                f"(2025-06-16), got {when!r}")
        if amt.lower() == "all":
            amount = None
        else:
            # A plain decimal: float(amt.replace(',', '')) read the
            # decimal comma '17,11' as 1711 and '1_0' as 10 (audit
            # A2-0227, A2-0987, A2-0990).
            if not _AMOUNT_RE.fullmatch(amt):
                raise CgDividendMapError(
                    f"{where}: AMOUNT must be `all` or a positive amount "
                    f"in the dividend's currency with a decimal POINT "
                    f"(e.g. 17.11 or 1,711.05; a decimal comma is "
                    f"refused), got {amt!r}")
            amount = float(amt.replace(",", ""))
            if not amount > 0 or amount != amount or amount == float("inf"):
                raise CgDividendMapError(
                    f"{where}: AMOUNT must be positive, got {amt!r}")
        out.append(Entry(n, sym, when, amount,
                         parts[3] if len(parts) == 4 else "", True))
    return out


def entries_from_config(cfg: dict) -> Optional[List[Entry]]:
    """The [[capital_gains_dividends]] entries of a taxjson.toml (parsed),
    or None when it has none. Raises CgDividendMapError naming the first
    entry that cannot be applied: a missing or unknown key, both or
    neither of year/date, an amount that is not "all" or a positive
    number, an account that is not configured, or an entry repeated."""
    from taxjson.lib.project_tables import iso_date
    v = cfg.get(TABLE)
    if v is None:
        return None
    if not isinstance(v, list) or not all(isinstance(e, dict) for e in v):
        raise CgDividendMapError(
            f"[[{TABLE}]] must be an array of tables (one [[{TABLE}]] "
            f"section per entry), got `{TABLE} = {v!r}`")
    accounts = cfg.get("accounts") if isinstance(cfg.get("accounts"),
                                                 dict) else {}
    out: List[Entry] = []
    seen: Dict[Tuple[str, str, str], int] = {}
    for n, e in enumerate(v, 1):
        where = f"taxjson.toml [[{TABLE}]] #{n}"
        from taxjson.lib.config_check import CGD_KEYS
        extra = sorted(set(e) - set(CGD_KEYS))
        if extra:
            raise CgDividendMapError(
                f"{where}: unknown key(s) {', '.join(extra)} (an entry has "
                f"symbol, year or date, amount, and optionally account)")
        sym = e.get("symbol")
        if not isinstance(sym, str) or not sym.strip() \
                or len(sym.split()) != 1:
            raise CgDividendMapError(
                f"{where}: symbol must be the books' symbol as a string, "
                f"e.g. \"SAMPMI.TO\" (got {sym!r})")
        if ("year" in e) == ("date" in e):
            raise CgDividendMapError(
                f"{where}: give exactly one of `year = 2025` (every "
                f"dividend of that tax year) or `date = 2025-06-16` (one "
                f"payment)")
        if "year" in e:
            y = e["year"]
            if isinstance(y, bool) or not isinstance(y, int) \
                    or not 1900 <= y <= 2100:
                raise CgDividendMapError(
                    f"{where}: year must be a tax year such as 2025, "
                    f"unquoted (got {y!r})")
            when = f"{y:04d}"
        else:
            when = iso_date(e["date"]) or ""
            if not when:
                raise CgDividendMapError(
                    f"{where}: date must be a date such as 2025-06-16 "
                    f"(got {e['date']!r})")
        amt = e.get("amount")
        if isinstance(amt, str) and amt.strip().lower() == "all":
            amount = None
        elif (isinstance(amt, (int, float)) and not isinstance(amt, bool)
              and amt > 0 and amt != float("inf")):
            amount = float(amt)
        else:
            raise CgDividendMapError(
                f"{where}: amount must be \"all\" or the positive box 18 "
                f"amount in the dividend's currency, e.g. 1.25 (got "
                f"{amt!r})")
        acct = e.get("account", "")
        if not isinstance(acct, str):
            raise CgDividendMapError(f"{where}: account must be an account "
                                     f"name string (got {acct!r})")
        acct = acct.strip()
        if acct and accounts and acct not in accounts:
            raise CgDividendMapError(
                f"{where}: account {acct!r} is not an [accounts.*] section "
                f"of taxjson.toml")
        key = (sym.strip().upper(), when, acct)
        if key in seen:
            raise CgDividendMapError(
                f"{where}: {key[0]} {when}"
                + (f" ({acct})" if acct else "")
                + f" repeats entry #{seen[key]} — one entry per payment "
                  f"or year")
        seen[key] = n
        out.append(Entry(n, key[0], when, amount, acct))
    return out


def row_amount(tx: dict) -> float:
    """The dividend's money, as divs-sum totals it (gross when given)."""
    return float(tx.get("gross_amount") or 0.0) \
        or float(tx.get("net_amount") or 0.0)


def allocate(entries: Iterable[Entry],
             rows: Iterable[Tuple[str, dict]], *,
             date_of: Callable[[dict], str],
             default_accounts: Optional[Iterable[str]] = None
             ) -> Dict[Tuple[str, str], float]:
    """{(account, row id): fraction of the row that is a box-18
    capital-gains dividend}. `rows`: (account, DIVIDEND row) over EVERY
    year in the books (an entry for another year must still find its
    rows). `default_accounts`: the accounts an entry without ACCOUNT
    covers (the taxable ones); None = every account."""
    rows = [(a, t) for a, t in rows if t.get("action") == "DIVIDEND"]
    dflt = None if default_accounts is None else set(default_accounts)
    out: Dict[Tuple[str, str], float] = {}
    for e in entries:
        hits = [(a, t) for a, t in rows
                if (a == e.account if e.account
                    else (dflt is None or a in dflt))
                and e.matches_symbol(str(t.get("symbol") or ""))
                and e.matches_date(str(date_of(t) or ""),
                                   str(t.get("date") or ""))]
        if not hits:
            scope = (f"account {e.account!r}" if e.account
                     else "the taxable accounts")
            raise CgDividendMapError(
                f"{e.where()}: {e.symbol} {e.when} matches no dividend in "
                f"{scope} — check the symbol (as the books spell it, after "
                f"ticker.map) and the date (a year is the income year — "
                f"for a distribution dated by its record date, the "
                f"record date's year; a date is the pay date or that "
                f"record date)")
        if e.amount is None:
            frac = {(a, str(t.get("id"))): 1.0 for a, t in hits}
        else:
            curs = sorted({str(t.get("currency") or "?") for _a, t in hits})
            if len(curs) > 1:
                raise CgDividendMapError(
                    f"{e.where()}: {e.symbol} {e.when} matches dividends "
                    f"in {', '.join(curs)} — an amount is in ONE currency; "
                    f"give one line per date (or per account)")
            total = sum(row_amount(t) for _a, t in hits)
            if total <= 0 or e.amount > total + 0.005:
                raise CgDividendMapError(
                    f"{e.where()}: {e.amount:.2f} {curs[0]} is more than "
                    f"the {total:.2f} {curs[0]} of {e.symbol} dividends "
                    f"it matches ({e.when})")
            f = min(1.0, e.amount / total)
            frac = {(a, str(t.get("id"))): f for a, t in hits}
        for k, f in frac.items():
            if k in out:
                raise CgDividendMapError(
                    f"{e.where()}: a {e.symbol} dividend it matches is "
                    f"already named by an earlier entry — one entry per "
                    f"payment")
            out[k] = f
    return out
