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
  ticker.map); a bare root without a suffix (LFE) matches LFE.TO.
- WHEN: a year (every dividend whose tax date is in it) or one date.
- AMOUNT: `all`, or the box-18 amount in the row's currency — the
  TOTAL over the matching rows, shared among them pro rata.
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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

MAP_NAME = "capital_gains_dividends.map"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")


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
        s = (symbol or "").upper()
        return s == self.symbol or (
            "." not in self.symbol and s.startswith(self.symbol + "."))

    def matches_date(self, iso: str) -> bool:
        return iso == self.when if len(self.when) == 10 \
            else iso[:4] == self.when


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
            try:
                amount = float(amt.replace(",", ""))
            except ValueError:
                raise CgDividendMapError(
                    f"{where}: AMOUNT must be `all` or a positive amount "
                    f"in the dividend's currency, got {amt!r}") from None
            if not amount > 0 or amount != amount or amount == float("inf"):
                raise CgDividendMapError(
                    f"{where}: AMOUNT must be positive, got {amt!r}")
        out.append(Entry(n, sym, when, amount,
                         parts[3] if len(parts) == 4 else ""))
    return out


def load_map(root: Path) -> Optional[List[Entry]]:
    """The project's entries, or None when there is no map file."""
    p = Path(root) / MAP_NAME
    if not p.is_file():
        return None
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
                and e.matches_date(str(date_of(t) or ""))]
        if not hits:
            scope = (f"account {e.account!r}" if e.account
                     else "the taxable accounts")
            raise CgDividendMapError(
                f"{e.where()}: {e.symbol} {e.when} matches no dividend in "
                f"{scope} — check the symbol (as the books spell it, after "
                f"ticker.map) and the date (the dividend's pay date)")
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
