"""Written options that straddle a tax-year boundary — the data behind
`taxjson option-boundary`.

Walks the taxable books' option rows FIFO per symbol (the same order the
engine's grant-timing pre-scan uses) and returns one row per WRITE lot
whose close (buy-back, expiry or assignment) falls in a later year than
the write, or that is still open at the end of the project year. Each
row says where the premium and any later amount land under the timing
in force, and what — if anything — the filer has to do about a year
that was already filed (ITA s.49(1)–(4); IT-479R paras 21–27).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from taxjson.lib.core import TaxTransaction, is_option_symbol
from taxjson.lib.corporate_timeline import event_sort_key


def _sort_date(t: TaxTransaction) -> str:
    return t.date_settle if getattr(t, "date_settle", "") else t.date


@dataclass
class Close:
    date: str
    kind: str            # buy-back | expiry | assignment
    units: float
    paid: float          # amount paid to close (0 for expiry/assignment)


@dataclass
class WriteLot:
    symbol: str
    account: str
    write_date: str
    write_year: int
    units: float
    premium: float       # net premium received for these units
    closes: List[Close] = field(default_factory=list)
    open_units: float = 0.0

    @property
    def per_unit(self) -> float:
        return self.premium / self.units if self.units else 0.0


def write_lots(transactions: List[TaxTransaction]) -> List[WriteLot]:
    rows = sorted((t for t in transactions
                   if t.action in ("BUYSELL", "ASSIGN") and is_option_symbol(t.symbol or "")),
                  key=lambda x: event_sort_key(x, profile="ca_main", date_of=_sort_date))
    pos: Dict[str, float] = {}
    lots: Dict[str, List[WriteLot]] = {}
    out: List[WriteLot] = []
    for t in rows:
        q = float(t.quantity or 0.0)
        sym = t.symbol
        p = pos.get(sym, 0.0)
        if q < 0:
            opening = -q - min(-q, max(p, 0.0))
            if opening > 1e-9:
                lot = WriteLot(symbol=sym, account=t.account or "", write_date=_sort_date(t),
                               write_year=int(_sort_date(t)[:4]), units=opening,
                               premium=abs(float(t.net_amount or 0.0)) * (opening / -q),
                               open_units=opening)
                lots.setdefault(sym, []).append(lot); out.append(lot)
        elif q > 0 and p < -1e-9:
            rem = min(q, -p)
            per_paid = abs(float(t.net_amount or 0.0)) / q if q else 0.0
            kind = ("assignment" if t.action == "ASSIGN"
                    else "expiry" if abs(float(t.price or 0.0)) < 1e-12 and abs(float(t.net_amount or 0.0)) < 1e-9
                    else "buy-back")
            for lot in lots.get(sym, []):
                if rem <= 1e-9:
                    break
                take = min(rem, lot.open_units)
                if take > 1e-9:
                    lot.closes.append(Close(date=_sort_date(t), kind=kind, units=take,
                                            paid=per_paid * take if kind == "buy-back" else 0.0))
                    lot.open_units -= take; rem -= take
            lots[sym] = [l for l in lots.get(sym, []) if l.open_units > 1e-9]
        pos[sym] = p + q
    return out


def straddling(transactions: List[TaxTransaction], year: int, timing: str,
               since: Optional[int], filed_years: Optional[set] = None) -> List[Dict[str, Any]]:
    """Rows for `taxjson option-boundary`: every write lot with a close in a
    later year than the write, or still open at the end of `year`."""
    filed_years = filed_years or set()
    grant_mode = (timing or "close").lower() == "grant"
    rows: List[Dict[str, Any]] = []
    for lot in write_lots(transactions):
        later = [c for c in lot.closes if int(c.date[:4]) > lot.write_year]
        still_open = lot.open_units > 1e-9 and lot.write_year <= year
        if not later and not still_open:
            continue
        grant = grant_mode and (since is None or lot.write_year >= since)
        wy = lot.write_year
        # Under grant timing, a lot written before `since` is deliberately
        # left on close timing (the transition) — say so instead of
        # suggesting the switch that is already on.
        transition = grant_mode and since is not None and wy < since
        def _switch(prem_text: str) -> str:
            if transition:
                return (f"kept on close timing by option_grant_timing_since = {since} (transition): "
                        f"lower it to {wy} only if the {wy} return reported the premium ({prem_text}) as a {wy} gain")
            return f"enable grant timing with option_grant_timing_since = {wy} to correct"
        for c in later:
            prem = lot.per_unit * c.units
            cy = int(c.date[:4])
            if c.kind == "assignment":
                if grant:
                    where = f"folded into the share leg in {cy} (s.49(2)-(3)); the books show no {wy} gain"
                    action = (f"T1-ADJ {wy}: remove the {prem:,.2f} premium reported for {wy} (s.49(4)) — the {wy} return was filed with it"
                              if wy in filed_years else
                              f"if {wy} was filed with the {prem:,.2f} premium as a gain, amend {wy} to remove it (s.49(4)); otherwise nothing")
                else:
                    where = f"folded into the share leg in {cy}; nothing in {wy} (close timing)"
                    action = f"same as the Act's post-amendment state; if {wy} was filed with the premium as a gain, amend {wy} (s.49(4))"
            elif c.kind == "expiry":
                if grant:
                    where = f"premium {prem:,.2f} recognised in {wy}; nothing in {cy}"; action = "no amendment"
                else:
                    where = f"premium {prem:,.2f} recognised in {cy} (close timing)"
                    action = f"the Act puts it in {wy} (s.49(1)) — " + _switch(f"{prem:,.2f}") + (f"; {wy} was filed: T1-ADJ {wy}" if wy in filed_years and not transition else "")
            else:
                if grant:
                    where = f"premium {prem:,.2f} in {wy}; buy-back loss {c.paid:,.2f} in {cy}"; action = "no amendment (IT-479R para 24)"
                else:
                    where = f"net {prem - c.paid:,.2f} in {cy} (close timing)"
                    action = (f"the Act puts +{prem:,.2f} in {wy} and -{c.paid:,.2f} in {cy} — " + _switch(f"{prem:,.2f}")
                              + (f"; {wy} was filed: T1-ADJ {wy}" if wy in filed_years and not transition else ""))
            rows.append({"symbol": lot.symbol, "account": lot.account, "written": lot.write_date, "write_year": wy,
                         "units": c.units, "premium": round(prem, 2), "closed": c.date, "close_kind": c.kind,
                         "close_year": cy, "paid": round(c.paid, 2), "timing": "grant" if grant else "close",
                         "where": where, "action": action})
        if still_open:
            prem = lot.per_unit * lot.open_units
            if grant:
                where = f"premium {prem:,.2f} recognised in {wy}; open"
                action = f"if assigned in a later year after {wy} is filed: T1-ADJ {wy} to remove it (s.49(4)); if bought back: loss in that year; if it expires: nothing"
            else:
                where = f"nothing in {wy} while open (close timing)"
                action = f"the Act puts {prem:,.2f} in {wy} (s.49(1)) — " + _switch(f"{prem:,.2f}")
            rows.append({"symbol": lot.symbol, "account": lot.account, "written": lot.write_date, "write_year": wy,
                         "units": lot.open_units, "premium": round(prem, 2), "closed": "", "close_kind": "open",
                         "close_year": None, "paid": 0.0, "timing": "grant" if grant else "close",
                         "where": where, "action": action})
    rows.sort(key=lambda r: (r["write_year"], r["symbol"], r["written"]))
    return rows
