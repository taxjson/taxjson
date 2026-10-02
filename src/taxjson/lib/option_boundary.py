"""Written options that straddle a tax-year boundary — the data behind
`taxjson option-boundary`.

Walks the taxable books' option rows FIFO per symbol (the same order the
engine's grant-timing pre-scan uses) and returns one row per WRITE lot
whose close (buy-back, expiry or assignment) falls in a later year than
the write, or that is still open at the end of the project year. Each
row says where the premium and any later amount land under the timing
in force, and what — if anything — the filer has to do about a year
that was already filed (ITA s.49(1), (3), (3.1), (4),
https://laws-lois.justice.gc.ca/eng/acts/I-3.3/section-49.html;
IT-479R paras 23–32 — the writer's grant-year gain and later buy-back
loss: para 29 for calls, para 32 for puts).

Two findings are ATTENTION rather than information (`attention: True`):
  * a lot written in a year that is LOCKED (filed/<year>.json) but that
    this project keeps on transition close timing (written before
    `option_grant_timing_since`) — if that year was filed under grant
    timing the premium is taxed twice, once there and again at the close;
  * a lot still open although its expiry date has passed — within the
    year, or after it but on or before the last date the books cover
    (S075-00: a January expiry in books that run into February) — and
    before today (an expiry later than the run date is simply open):
    the export is missing the expiry/assignment row;
  * a lot this project puts on grant timing although the write year's
    lock records CLOSE timing — that return never reported the premium
    and this project does not report it either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from taxjson.lib.core import (TaxTransaction,
                              _make_assign_underlying_resolver,
                              is_option_symbol, parse_option_expiry)
from taxjson.lib.corporate_timeline import event_sort_key
from taxjson.lib.phantom_holdings import OrderStarts, unbacked_close


def _sort_date(t: TaxTransaction) -> str:
    """The settlement date (Canada's default tax-year basis)."""
    return t.date_settle if getattr(t, "date_settle", "") else t.date


def _trade_date(t: TaxTransaction) -> str:
    return t.date or getattr(t, "date_settle", "") or ""


def date_basis_of(tax_date: Optional[str]):
    """The row date a write or close is dated by: the project's
    [settings] tax_date, as the gains engine dates it (S075-05: a
    trade-basis project's Dec-31 write settling in January straddles
    2025/2026 in the engine but read as a 2026 write here)."""
    return _trade_date if str(tax_date or "settle").lower() == "trade" \
        else _sort_date


def _assignment_cite(symbol: str) -> str:
    """s.49(3) for an exercised call (premium into the writer's
    proceeds), s.49(3.1) for an exercised put (premium out of the
    writer's cost) — R1-39/R1-180: s.49(2) is the expiry rule for a
    corporation's options on its own shares."""
    from taxjson.lib.core import parse_option_right
    right = parse_option_right(symbol)
    if right == "P":
        return "s.49(3.1)"
    if right == "C":
        return "s.49(3)"
    return "s.49(3)/(3.1)"


def _buyback_cite(symbol: str) -> str:
    """IT-479R para 29 (calls) / para 32 (puts): the writer's grant-year
    gain and the later closing purchase's loss."""
    from taxjson.lib.core import parse_option_right
    right = parse_option_right(symbol)
    return {"C": "IT-479R para 29", "P": "IT-479R para 32"}.get(
        right, "IT-479R paras 29/32")


def _expired_test(transactions, year: int, today: Optional[date], date_of):
    """expired(expiry) -> True when a contract with that expiry date
    can no longer be open: it expired by the year end (or by the last
    date the books cover, S075-00) AND before `today`. On the expiry
    day itself the contract still trades and the broker posts the
    expiry row afterwards (A2-1112; the run's own expired-options
    warning skips that day too), while a Dec 31 expiry of a past year
    is past."""
    last = max((date_of(t) for t in transactions if date_of(t)),
               default="")
    bound = max(f"{year}-12-31", last[:10])
    now = (today or date.today()).isoformat()

    def expired(expiry: Optional[str]) -> bool:
        return bool(expiry) and expiry <= bound and expiry < now
    return expired


@dataclass
class Close:
    date: str
    kind: str            # buy-back | expiry | assignment | cash-settled
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
    # The broker coded the sale that "wrote" this lot CLOSING (IB code
    # C) with no long position in the data to close: it sold a long
    # bought before the data — not a write (audit S013-00).
    broker_closing: bool = False
    broker_basis: str = ''

    @property
    def per_unit(self) -> float:
        return self.premium / self.units if self.units else 0.0


def write_lots(transactions: List[TaxTransaction],
               tax_date: Optional[str] = None) -> List[WriteLot]:
    """FIFO walk of the option rows, per symbol. Follows the engine
    (lib/core.py) on two points it used to miss (R1-179, S075-09):
      * a SPLIT on an option symbol re-denominates the open position
        (quantity = ratio) and, with `symbol_new`, renames it — a lot
        written under the old symbol is closed by the buy-back under the
        new one;
      * an ASSIGN whose underlying never trades as stock for that account
        is CASH-SETTLED (index options): no share leg, no s.49(3) fold.
    Premiums and buy-back costs keep their sign (a write whose commission
    exceeds the premium nets a loss). Writes and closes are dated on
    `tax_date` (settle, the default, or trade)."""
    _date_of = date_basis_of(tax_date)
    # The engine's own pairing of an assignment with its share leg
    # (A2-0114, A2-0328): the option root is matched to the account's
    # stock line by class / futures-month spelling (RCI for RCI.B.TO,
    # BRKB for BRK.B.US) — an exact (account, root) test called those
    # physically settled assignments cash-settled. No stock line in the
    # option's account (an index option) -> None -> cash-settled.
    resolve_underlying = _make_assign_underlying_resolver(
        transactions, _date_of, quiet=True)
    rows = sorted((t for t in transactions
                   if t.action in ("BUYSELL", "ASSIGN", "SPLIT",
                                   "OPENING_BALANCE")
                   and is_option_symbol(t.symbol or "")),
                  key=lambda x: event_sort_key(x, profile="ca_main", date_of=_date_of))
    pos: Dict[str, float] = {}
    lots: Dict[str, List[WriteLot]] = {}
    out: List[WriteLot] = []
    orders = OrderStarts()
    for t in rows:
        q = float(t.quantity or 0.0)
        sym = t.symbol
        if t.action == "SPLIT":
            ratio = q if q > 0 else 1.0
            new = (getattr(t, "symbol_new", "") or "").strip() or sym
            p_old = pos.pop(sym, 0.0) * ratio
            moved = lots.pop(sym, [])
            for lot in moved:
                lot.open_units *= ratio
            if new != sym:
                pos[new] = pos.get(new, 0.0) + p_old
            else:
                pos[sym] = p_old
            if moved:
                lots.setdefault(new, []).extend(moved)
            continue
        p = pos.get(sym, 0.0)
        p_order = orders.prev(sym, t, p)
        if t.action == "OPENING_BALANCE":
            # A phantom opening (phantoms.json, --include-options): the
            # contracts were held LONG before the history starts, so the
            # sale that follows closes them — it is not a write (S044-09;
            # the engine books it the same way).
            pos[sym] = p + q
            continue
        net = float(t.net_amount or 0.0)
        if q < 0:
            opening = -q - min(-q, max(p, 0.0))
            if opening > 1e-9:
                # Base books store net magnitudes with the direction on
                # the quantity: a SELL's net is the premium received, and
                # a negative net (commission > premium) is a loss — kept.
                lot = WriteLot(symbol=sym, account=t.account or "", write_date=_date_of(t),
                               write_year=int(_date_of(t)[:4]), units=opening,
                               premium=net * (opening / -q),
                               open_units=opening,
                               broker_closing=unbacked_close(t, p, p_order),
                               broker_basis=str(getattr(t, "broker_basis", "")
                                                or ""))
                lots.setdefault(sym, []).append(lot); out.append(lot)
        elif q > 0 and p < -1e-9:
            rem = min(q, -p)
            # A buy-back's cost is the money paid: parser books carry it
            # positive, a .tt book negative (money out, A2-1111).
            per_paid = abs(net) / q if q else 0.0
            if t.action == "ASSIGN":
                kind = ("assignment" if resolve_underlying(t)
                        else "cash-settled")
            elif abs(float(t.price or 0.0)) < 1e-12 and abs(net) < 1e-9:
                kind = "expiry"
            else:
                kind = "buy-back"
            for lot in lots.get(sym, []):
                if rem <= 1e-9:
                    break
                take = min(rem, lot.open_units)
                if take > 1e-9:
                    lot.closes.append(Close(date=_date_of(t), kind=kind, units=take,
                                            paid=(per_paid * take
                                                  if kind in ("buy-back", "cash-settled")
                                                  else 0.0)))
                    lot.open_units -= take; rem -= take
            lots[sym] = [l for l in lots.get(sym, []) if l.open_units > 1e-9]
        pos[sym] = p + q
    return out


def filed_locks(root, settings: Optional[Dict[str, Any]] = None,
                warn=None):
    """Every filed-year lock this project can see — taxjson_filed.
    project_locks: its own ``filed/<year>.json`` files plus the previous
    year's record named by ``[settings] prior_year_record`` — as
    ``(years, timing)``: the locked years, and for each lock that
    records it the option timing its return used
    (``option_premium_timing``, ``option_grant_since``) — what
    `straddling` takes as ``filed_years`` and ``filed_timing``.
    ``warn(text)`` is called for a lock that cannot be read (its timing
    is then unknown). A ``prior_year_record`` that is not a path string
    is skipped here (every config reader refuses it). Shared by
    option-boundary's twin edge-cases, so the two give one verdict on
    the same contract (audit A2-0390, A2-1205, A2-0360)."""
    import json as _json
    from pathlib import Path as _Path
    from taxjson.bin import taxjson_filed as _tfl
    root = _Path(root)
    try:
        locks = _tfl.project_locks(root, settings or {})
    except _tfl.PriorRecordError:
        locks = [(y, p, "local") for y, p in _tfl.list_snapshots(root)]
    years: set = set()
    timing: Dict[int, Dict[str, Any]] = {}
    for fy, f, _where in locks:
        years.add(fy)
        try:
            doc = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            doc = None
            if warn:
                warn(f"cannot read {_tfl.lock_label(root, f)} ({e}) — its "
                     f"recorded option timing is unknown; `taxjson "
                     f"check-filed` checks the lock.")
        ot = doc.get("option_timing") if isinstance(doc, dict) else None
        if isinstance(ot, dict):
            timing[fy] = ot
    return years, timing

def expired_test(transactions: List[TaxTransaction], year: int,
                 today: Optional[date] = None,
                 tax_date: Optional[str] = None):
    """The one "this contract can no longer be open" predicate
    (`_expired_test`: expired by the later of the year end and the last
    date the books cover, and before today) for straddling, expired_open
    and `taxjson run`'s per-account warning (audit A2-1210, A2-1112)."""
    return _expired_test(transactions, year, today, date_basis_of(tax_date))

def _filed_on_close(wy: int, filed_timing: Optional[Dict[int, Dict[str, Any]]]) -> Optional[bool]:
    """True/False when the filed/<wy>.json lock RECORDS the timing its
    return used (close-year writes it) — True if a lot written in `wy`
    was on close timing there; None when the lock predates the record."""
    rec = (filed_timing or {}).get(wy) or {}
    if not rec or "option_premium_timing" not in rec:
        return None
    if str(rec.get("option_premium_timing") or "close").lower() != "grant":
        return True
    since = rec.get("option_grant_since")
    return since is not None and wy < int(since)


def straddling(transactions: List[TaxTransaction], year: int, timing: str,
               since: Optional[int], filed_years: Optional[set] = None,
               filed_timing: Optional[Dict[int, Dict[str, Any]]] = None,
               today: Optional[date] = None,
               tax_date: Optional[str] = None,
               filed_labels: Optional[Dict[int, str]] = None
               ) -> List[Dict[str, Any]]:
    """Rows for `taxjson option-boundary`: every write lot with a close in a
    later year than the write, or still open at the end of `year`.
    `filed_timing` maps a locked year to the option timing its lock
    recorded (`option_premium_timing`, `option_grant_since`). `today`
    (default: the run date) bounds the missing-expiry check: during the
    year a contract expiring after today is open, not missing a row.
    `tax_date` is the project's date basis (settle|trade).
    `filed_labels` names each locked year's lock in the advice (default
    filed/<year>.json; a lock reached through [settings]
    prior_year_record is named by its path, A2-0036)."""
    filed_years = set(filed_years or set()) | set((filed_timing or {}).keys())

    def _lk(y: int) -> str:
        return (filed_labels or {}).get(y) or f"filed/{y}.json"
    grant_mode = (timing or "close").lower() == "grant"
    # The missing-expiry cutoff is the EARLIER of the year end and today
    # (R1-36/R1-174/R1-190: comparing with Dec 31 alone flagged every
    # open write expiring later in the current year as a missing row).
    # S075-00: the books may run past Dec 31 (January of the next year
    # is part of a frozen year's inputs) — a contract that expired by
    # the LAST date the books cover cannot still be open either.
    _date_of = date_basis_of(tax_date)
    _expired = _expired_test(transactions, year, today, _date_of)
    rows: List[Dict[str, Any]] = []
    for lot in write_lots(transactions, tax_date=tax_date):
        later = [c for c in lot.closes if int(c.date[:4]) > lot.write_year]
        still_open = lot.open_units > 1e-9 and lot.write_year <= year
        if not later and not still_open:
            continue
        if lot.broker_closing:
            # Not a write at all (audit S013-00): the broker says the
            # sale CLOSED a long the data never bought. Asking for an
            # expiry row, or placing a premium in a year, is wrong
            # advice — the missing purchase is the fix.
            rows.append({
                "symbol": lot.symbol, "account": lot.account,
                "written": lot.write_date, "write_year": lot.write_year,
                "units": lot.units, "premium": round(lot.premium, 2),
                "closed": "", "close_kind": "broker-closing",
                "close_year": None, "paid": 0.0, "timing": "",
                "broker_closing": True,
                "where": (f"broker says closing (IB code C"
                          + (f", IB Basis {lot.broker_basis}"
                             if lot.broker_basis else "")
                          + "): the sale closed a LONG position bought "
                            "before the data — not a written option"),
                "action": ("ATTENTION: add the missing purchase "
                           "(`taxjson find-missing-history --gen-phantoms "
                           "phantoms.json`, then `taxjson run`); until "
                           "then the books treat the sale as a write "
                           "whose premium is a gain"),
                "attention": True})
            continue
        grant = grant_mode and (since is None or lot.write_year >= since)
        wy = lot.write_year
        # Under grant timing, a lot written before `since` is deliberately
        # left on close timing (the transition) — say so instead of
        # suggesting the switch that is already on.
        transition = grant_mode and since is not None and wy < since
        # ...unless the transition year is itself LOCKED: a year filed
        # under grant timing (a project's default `since` is its own
        # year) already taxed the premium, and close timing here taxes
        # it again at the close (2026-09 audit: consecutive default
        # projects reported +399 then +298 for a 298 economic gain).
        filed_close = _filed_on_close(wy, filed_timing)
        double = transition and wy in filed_years and filed_close is not True
        # The mirror case (S075-10): this project puts the lot on GRANT
        # timing (premium in wy, ITA s.49(1)) but the wy lock records
        # that wy was filed on CLOSE timing — the wy return never
        # reported the premium, and this project's later year does not
        # report it at the close either: it lands in no return.
        missed = grant and filed_close is True

        def _missed_text(prem_text: str) -> str:
            return (f"ATTENTION: {_lk(wy)} records CLOSE timing, so "
                    f"the {wy} return did not report the {prem_text} "
                    f"premium, and this project (grant timing from "
                    f"{since}) puts it in {wy} too — it is in no return. "
                    f"Either set option_grant_timing_since = {wy + 1} "
                    f"(keep {wy}'s contracts on close timing, as filed) or "
                    f"T1-ADJ {wy} to ADD the {prem_text} premium "
                    f"(s.49(1))")

        def _double_text(prem_text: str, cy: Optional[int]) -> str:
            known = filed_close is False
            where_now = f"again in {cy}" if cy else "again when it closes"
            return (f"ATTENTION: {wy} is locked ({_lk(wy)}) and "
                    + (f"its lock records grant timing, so the {wy} return "
                       f"reported the {prem_text} premium; " if known else
                       f"a {wy} project on grant timing (the default, "
                       f"since = its own year) reported the {prem_text} "
                       f"premium there; ")
                    + f"this project keeps the contract on close timing "
                      f"(option_grant_timing_since = {since}) and taxes it "
                    + where_now
                    + f". Set option_grant_timing_since = {wy} (the first "
                      f"year filed under grant timing) and keep it in "
                      f"every later project"
                    + ("" if known else
                       f" (if {wy} was in fact filed on close timing the "
                       f"transition is right and this can be ignored)"))

        def _switch(prem_text: str) -> str:
            if transition:
                return (f"kept on close timing by option_grant_timing_since = {since} (transition): "
                        f"lower it to {wy} only if the {wy} return reported the premium ({prem_text}) as a {wy} gain")
            return f"enable grant timing with option_grant_timing_since = {wy} to correct"
        for c in later:
            prem = lot.per_unit * c.units
            cy = int(c.date[:4])
            if c.kind == "assignment":
                if grant and missed:
                    where = f"folded into the share leg in {cy} ({_assignment_cite(lot.symbol)}); the books show no {wy} gain"
                    action = (f"no amendment — {_lk(wy)} records close timing, so the {wy} "
                              f"return never reported the {prem:,.2f} premium; the fold in {cy} is right")
                elif grant:
                    where = f"folded into the share leg in {cy} ({_assignment_cite(lot.symbol)}); the books show no {wy} gain"
                    action = (f"T1-ADJ {wy}: remove the {prem:,.2f} premium reported for {wy} (s.49(4)) — the {wy} return was filed with it"
                              if wy in filed_years else
                              f"if {wy} was filed with the {prem:,.2f} premium as a gain, amend {wy} to remove it (s.49(4)); otherwise nothing")
                else:
                    where = f"folded into the share leg in {cy}; nothing in {wy} (close timing)"
                    action = f"same as the Act's post-amendment state; if {wy} was filed with the premium as a gain, amend {wy} (s.49(4))"
                    if double:
                        action = (f"ATTENTION: {wy} is locked ({_lk(wy)}); if it was filed under grant timing "
                                  f"with the {prem:,.2f} premium as a gain, T1-ADJ {wy} to remove it (s.49(4))")
            elif c.kind == "expiry":
                if grant:
                    where = f"premium {prem:,.2f} recognised in {wy}; nothing in {cy}"
                    action = _missed_text(f"{prem:,.2f}") if missed else "no amendment"
                else:
                    where = f"premium {prem:,.2f} recognised in {cy} (close timing)"
                    action = (_double_text(f"{prem:,.2f}", cy) if double else
                              f"the Act puts it in {wy} (s.49(1)) — " + _switch(f"{prem:,.2f}") + (f"; {wy} was filed: T1-ADJ {wy}" if wy in filed_years and not transition else ""))
            else:
                # A buy-back, or a cash-settled assignment (S075-09: an
                # index option — the underlying never trades as stock, so
                # there is no share leg to fold into; the settlement is a
                # loss of the close year, exactly as the engine books it).
                what = ("cash settlement" if c.kind == "cash-settled"
                        else "buy-back")
                if grant:
                    where = f"premium {prem:,.2f} in {wy}; {what} loss {c.paid:,.2f} in {cy}"
                    action = (_missed_text(f"{prem:,.2f}") if missed
                              else f"no amendment ({_buyback_cite(lot.symbol)})")
                else:
                    where = f"net {prem - c.paid:,.2f} in {cy} (close timing)"
                    action = (_double_text(f"{prem:,.2f}", cy) if double else
                              f"the Act puts +{prem:,.2f} in {wy} and -{c.paid:,.2f} in {cy} — " + _switch(f"{prem:,.2f}")
                              + (f"; {wy} was filed: T1-ADJ {wy}" if wy in filed_years and not transition else ""))
            rows.append({"symbol": lot.symbol, "account": lot.account, "written": lot.write_date, "write_year": wy,
                         "units": c.units, "premium": round(prem, 2), "closed": c.date, "close_kind": c.kind,
                         "close_year": cy, "paid": round(c.paid, 2), "timing": "grant" if grant else "close",
                         "where": where, "action": action,
                         "attention": action.startswith("ATTENTION")})
        if still_open:
            prem = lot.per_unit * lot.open_units
            expiry = parse_option_expiry(lot.symbol)
            if _expired(expiry):
                # Past its expiry date within (or before) the tax year yet
                # never closed in the books: the export dropped the
                # expiry / assignment row. Not "open" — unknown.
                rows.append({"symbol": lot.symbol, "account": lot.account, "written": lot.write_date,
                             "write_year": wy, "units": lot.open_units, "premium": round(prem, 2),
                             "closed": "", "close_kind": "expired?", "close_year": None, "paid": 0.0,
                             "timing": "grant" if grant else "close",
                             "where": f"expired {expiry} but no expiry/assignment row — check the export",
                             "action": (f"ATTENTION: import the expiry, assignment or buy-back row for "
                                        f"{lot.open_units:g} unit(s) (the broker export is missing it); "
                                        f"until then the {prem:,.2f} premium's year is unknown"),
                             "attention": True})
                continue
            if grant:
                where = f"premium {prem:,.2f} recognised in {wy}; open"
                action = (_missed_text(f"{prem:,.2f}") if missed else
                          f"if assigned in a later year after {wy} is filed: T1-ADJ {wy} to remove it (s.49(4)); if bought back: loss in that year; if it expires: nothing")
            else:
                where = f"nothing in {wy} while open (close timing)"
                action = (_double_text(f"{prem:,.2f}", None) if double else
                          f"the Act puts {prem:,.2f} in {wy} (s.49(1)) — " + _switch(f"{prem:,.2f}"))
            rows.append({"symbol": lot.symbol, "account": lot.account, "written": lot.write_date, "write_year": wy,
                         "units": lot.open_units, "premium": round(prem, 2), "closed": "", "close_kind": "open",
                         "close_year": None, "paid": 0.0, "timing": "grant" if grant else "close",
                         "where": where, "action": action,
                         "attention": action.startswith("ATTENTION")})
    rows.sort(key=lambda r: (r["write_year"], r["symbol"], r["written"]))
    return rows


def expired_open(transactions: List[TaxTransaction], year: int,
                 today: Optional[date] = None,
                 tax_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Option positions — LONG or written — still open in the books
    although the contract's expiry has passed (by the year end, or by
    the last date the books cover, and before `today`): the export is
    missing the expiry, exercise or assignment row. A data check, not a
    tax rule: in either country the missing row keeps a written
    premium or a long option's cost out of the year it belongs to
    (S066-15 — a US project had no such check at all)."""
    _date_of = date_basis_of(tax_date)
    rows = sorted((t for t in transactions
                   if t.action in ("BUYSELL", "ASSIGN", "SPLIT",
                                   "OPENING_BALANCE")
                   and is_option_symbol(t.symbol or "")),
                  key=lambda x: event_sort_key(x, profile="ca_main",
                                               date_of=_date_of))
    pos: Dict[Any, float] = {}
    # Positions the broker says were opened before the data (a sale
    # coded IB C with no long held, a buy coded C with no short): the
    # missing row is the PURCHASE (or the write), not the expiry.
    closing: Dict[Any, bool] = {}
    orders = OrderStarts()
    for t in rows:
        key = (t.account or "", t.symbol)
        q = float(t.quantity or 0.0)
        if t.action == "SPLIT":
            ratio = q if q > 0 else 1.0
            new = (getattr(t, "symbol_new", "") or "").strip() or t.symbol
            p = pos.pop(key, 0.0) * ratio
            nk = (t.account or "", new)
            pos[nk] = pos.get(nk, 0.0) + p
            if closing.pop(key, False):
                closing[nk] = True
            continue
        _p = pos.get(key, 0.0)
        if unbacked_close(t, _p, orders.prev(key, t, _p)):
            closing[key] = True
        pos[key] = pos.get(key, 0.0) + q
        if abs(pos[key]) < 1e-9:
            closing.pop(key, None)
    _expired = _expired_test(transactions, year, today, _date_of)
    out = []
    for (acct, sym), q in sorted(pos.items()):
        if abs(q) < 1e-9:
            continue
        expiry = parse_option_expiry(sym)
        if _expired(expiry):
            out.append({"account": acct, "symbol": sym, "quantity": q,
                        "expiry": expiry,
                        "side": "written" if q < 0 else "long",
                        "broker_closing": bool(closing.get((acct, sym)))})
    return out
