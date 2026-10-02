"""T5 box 18 capital-gains dividends: the project's override list.

A split-share or mutual-fund corporation may designate part of a
dividend a CAPITAL-GAINS dividend (ITA s.130.1(4) / s.131(1); T5 box 18,
line 17400 of the return). It is a capital gain of the shareholder
(50% inclusion), not a taxable dividend: no gross-up, no dividend tax
credit. No broker export says which payments those are — IB prints
"(Ordinary Dividend)", RBC and Questrade a plain dividend — so the
books carry them as dividends. Only the slip (or IBKR's own dividends
report, "T5: Capital Gains") tells.

`capital_gains_dividends.map` at the project root names them. Canada
only (lib/country PROJECT_FILE_COUNTRY): a US project refuses the file.

    # SYMBOL   WHEN         AMOUNT   [ACCOUNT]
    LFE.TO     2025         all              # every 2025 LFE dividend
    XTD.TO     2025-09-10   5.50             # 5.50 of the Sep-10 payment
    FFN.TO     2024         1711.05  margin  # box 18 total for the year

- SYMBOL: the dividend row's symbol as the books spell it (after
  ticker.map); a bare root without a suffix (LFE) matches that root's
  Canadian listings only (LFE.TO, LFE.V ...) — never another class or
  preferred series (LFE.PR.B.TO) nor a foreign listing (LFE.US): name
  those in full.
- WHEN: a year (every dividend whose tax date is in it) or one date
  (the payment's pay date, or its record date when the books date it
  by the record date).
- AMOUNT: `all`, or the box-18 amount in the row's currency — the
  TOTAL over the matching rows, shared among them pro rata. A plain
  decimal with a decimal point (thousands commas in groups of three):
  a decimal comma is refused.
- ACCOUNT: optional. Without it the entry covers the taxable accounts
  (only they get a T5); name an account to restrict it.

The ledger and ACB are never touched (a box-18 dividend does not change
ACB). Only the income views read the file: `divs-sum` shows the amount
apart from the dividends, and the Canadian estimate moves it from the
grossed-up eligible dividends into capital gains.

An entry that matches no dividend row, an amount above the rows' total,
or matching rows in more than one currency is an error naming the line.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

MAP_NAME = "capital_gains_dividends.map"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")
# Digits with an optional decimal point; thousands commas only in
# groups of three before the point.
_AMOUNT_RE = re.compile(r"(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+")


class CgDividendMapError(ValueError):
    """A capital_gains_dividends.map the views cannot apply."""


@dataclass(frozen=True)
class Entry:
    line: int
    symbol: str          # upper-case
    when: str            # "YYYY" or "YYYY-MM-DD"
    amount: Optional[float]   # None = all
    account: str = ""

    def where(self) -> str:
        return f"{MAP_NAME} line {self.line}"

    def matches_symbol(self, symbol: str) -> bool:
        """Exact, or a bare root against ROOT.<Canadian listing suffix>:
        a preferred series or another class (FTN.PR.A.TO) and a foreign
        listing of a same-root issuer (T.US for T) are other securities
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
                f"`LFE.TO 2025 all` or `XTD.TO 2025-09-10 5.50`), got "
                f"{raw.strip()!r}")
        sym, when, amt = parts[0].upper(), parts[1], parts[2]
        if not (_YEAR_RE.match(when) or _DATE_RE.match(when)):
            raise CgDividendMapError(
                f"{where}: WHEN must be a year (2025) or a date "
                f"(2025-09-10), got {when!r}")
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
                         parts[3] if len(parts) == 4 else ""))
    return out


def load_map(root: Path) -> Optional[List[Entry]]:
    """The project's entries, or None when there is no map file."""
    p = Path(root) / MAP_NAME
    if not os.path.lexists(p):
        return None
    if not p.is_file():
        # A directory or a dangling symlink is not "no map": the box-18
        # dividends would silently show as ordinary dividends (audit
        # A2-0994).
        raise CgDividendMapError(
            f"{MAP_NAME}: cannot read it (not a regular file"
            + (" — a dangling symlink" if p.is_symlink() else "") + ")")
    try:
        text = p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise CgDividendMapError(f"{MAP_NAME}: cannot read it ({e})")
    return parse_map(text)


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
                    f"already named by an earlier line — one line per "
                    f"payment")
            out[k] = f
    return out
