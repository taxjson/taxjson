"""A slip's symbol read as a book's (slip_symbol, norm_symbol, split_listing:
a listing suffix kept apart, a broker's option description as OCC) and the
computed dispositions per symbol root a slip is matched against
(load_computed). bin/taxjson_reconcile_slips.py matches T5008 and 1099-B
rows with them; lib/slip_audit.py reads them too.
"""
import itertools
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.tax_forms import grant_buyback_units, load_json, short_carried_loss
from taxjson.lib.country import CANADA
from taxjson.lib.futures import is_plain_future, section_1256_kind
from taxjson.lib.futures_prefixes import canonical_futures_symbol
from taxjson.model.result import GainRow

# Every known listing suffix (lib/markets; .VN was missing here).
from taxjson.lib.brokerages.base import is_ca_pref_series
from taxjson.lib.markets import listing_suffix_re
_SUFFIX_RE = listing_suffix_re()


# Broker option descriptions -> OCC (R1-209): IB prints
# "SAMPLE 21MAR25 50 C", Webull "CALL SAMPLE03/21/25 50"; the books carry
# SAMPLE250321C00050000.
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
     "NOV", "DEC"), 1)}
# A strike with or without thousands separators ('5,000.00', R1-170 /  # pii-ok
# A2-1113 — the parsers' OPTION_STRIKE_RE accepts it); a decimal comma
# ('2,50') does not match and the cell stays a plain (unmatched) symbol.
_STRIKE = r"((?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?)"
_IB_OPT_RE = re.compile(
    r"^([A-Z][A-Z0-9.]*)\s+(\d{1,2})([A-Z]{3})(\d{2})\s+"
    + _STRIKE + r"\s+([CP])$")
_WB_OPT_RE = re.compile(
    r"^(CALL|PUT)\s+([A-Z][A-Z0-9.]*?)\s*(\d{2})/(\d{2})/(\d{2})\s+"
    + _STRIKE + r"$")
_PADDED_OCC_RE = re.compile(r"^([A-Z][A-Z0-9.]*)\s+(\d{6}[CP]\d{8})$")


class UnrepresentableStrike(ValueError):
    """An option description whose strike the OCC symbol cannot hold."""


def _occ(root: str, yy: str, mm: int, dd: int, cp: str,
         strike: str) -> str:
    """The OCC symbol of a broker option description. The OCC strike
    field holds thousandths in 8 digits: a strike finer than 0.001
    ('1.0004') or above 99999.999 raises UnrepresentableStrike — rounding
    it named another contract (strike 1.000) and certified the slip
    against it (backlog TJ-0902)."""
    from decimal import Decimal, InvalidOperation
    try:
        milli = Decimal(strike.replace(',', '')) * 1000
    except InvalidOperation:
        raise UnrepresentableStrike(strike)
    if milli != milli.to_integral_value() or not 0 <= milli < 10 ** 8:
        raise UnrepresentableStrike(strike)
    return f"{root}{yy}{mm:02d}{dd:02d}{cp}{int(milli):08d}"


def split_listing(sym: str) -> Tuple[str, str]:
    """(root, market suffix or '') of a normalized symbol. A dotted
    preferred series has no market suffix: the L of SYN.PR.L is its series
    (backlog TJ-1203)."""
    m = None if is_ca_pref_series(sym) else _SUFFIX_RE.search(sym or "")
    if not m:
        return sym, ""
    return sym[:m.start()], m.group(1).upper()


def slip_symbol(raw: str, strict: bool = False) -> str:
    """A slip cell as a book-style symbol, KEEPING any listing suffix
    the slip wrote (SAMPLB.TO stays apart from SAMPLB.US — R1-292): upper-
    cased, broker option descriptions rewritten to OCC and share-class
    separators ('SAMPLC B', 'SAMPLC/B', 'SAMPLC-B') to the books' dot form.
    An option description whose strike OCC cannot hold raises
    UnrepresentableStrike with `strict`, and so does a futures symbol that
    names no contract (FuturesSymbolError); otherwise it stays a plain
    (unmatched) symbol."""
    s = (raw or "").replace("\ufeff", "").strip().upper().lstrip(".")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return ""
    # A future's `/` or `\` prefix is `F:`, as the books spell it
    # (lib/futures_prefixes, backlog TJ-5150); one that names no contract
    # raises with `strict`, else stays as written (unmatched).
    try:
        s = canonical_futures_symbol(s)
    except ValueError:
        if strict:
            raise
    try:
        m = _IB_OPT_RE.match(s)
        if m and m.group(3) in _MONTHS:
            return _occ(m.group(1), m.group(4), _MONTHS[m.group(3)],
                        int(m.group(2)), m.group(6), m.group(5))
        m = _WB_OPT_RE.match(s)
        if m:
            return _occ(m.group(2), m.group(5), int(m.group(3)),
                        int(m.group(4)), m.group(1)[0], m.group(6))
    except UnrepresentableStrike:
        if strict:
            raise
        return s
    m = _PADDED_OCC_RE.match(s)
    if m:
        return m.group(1) + m.group(2)
    root, sfx = split_listing(s)
    if re.fullmatch(r"[A-Z0-9]+(?:[ /\-.][A-Z0-9]+)+", root):
        root = re.sub(r"[ /\-.]", ".", root)
    return f"{root}.{sfx}" if sfx else root


def norm_symbol(sym: str) -> str:
    return split_listing((sym or "").strip().upper().lstrip("."))[0]


def _bump(rec: Dict[str, Any], sfx: str, **vals) -> None:
    """Add vals to rec and to rec['listings'][sfx]."""
    sub = rec["listings"].setdefault(
        sfx, {k: (None if k == "cost" else 0.0) for k in rec
              if k != "listings"})
    for tgt in (rec, sub):
        for k, v in vals.items():
            if k == "cost":
                if v is not None:
                    tgt["cost"] = (tgt["cost"] or 0.0) + v
            else:
                tgt[k] += v


def _computed_record() -> Dict[str, Any]:
    return {"qty": 0.0, "proceeds_net": 0.0, "proceeds_gross": 0.0,
            "cost": 0.0, "rows": 0, "tainted_rows": 0,
            "open_grant_qty": 0.0, "open_grant": 0.0, "open_grant_net": 0.0,
            "prior_grant": 0.0, "unknown_proceeds_rows": 0,
            "capitalized_263h": 0.0, "future_loss_rows": 0, "listings": {}}


def _open_units(writes: List[Tuple], closed: Dict[str, float]) -> List[float]:
    """The units of each write (in write order) still open after
    `closed` {account: units}: as the engine closes a short option's
    lots (_short_lot_close), FIFO within the closing account, another's
    earliest only when its own run out (a contract moved). The latest
    writes of all accounts were taken for the open ones: another
    account's write, at its premium, was left closed (backlog TJ-1092)."""
    left = [float(w[1]) for w in writes]
    moved = 0.0
    for account, units in sorted(closed.items()):
        for i, w in enumerate(writes):
            if w[4] == account and units > 1e-12:
                take = min(units, left[i])
                left[i] -= take
                units -= take
        moved += units
    for i in range(len(left)):
        take = min(moved, left[i])
        left[i] -= take
        moved -= take
    return left


def _future_on_slip(symbol: str, country: str) -> bool:
    """A plain future whose close form-export shows on a slip's shape:
    every one on Schedule 3, on Form 8949 only one off §1256 (the other
    goes to Form 6781 by hand, US-FUT-02)."""
    if not is_plain_future(symbol):
        return False
    return country == CANADA or not section_1256_kind(symbol)


def _grant_closed(row: Dict[str, Any]) -> Dict[str, Any]:
    """A row's `grant_closed` ({year: {units, premium}}), or {} when it
    is not an object, as form-export's grant_buyback_units reads it
    (TJ-2940: a string stopped reconcile-slips with an AttributeError)."""
    gc = row.get("grant_closed")
    return gc if isinstance(gc, dict) else {}


def load_computed(gains_paths: List[Path],
                  year: Optional[int],
                  date_basis: str = "settle",
                  country: str = CANADA) -> Dict[str, Dict[str, Any]]:
    """Aggregate computed dispositions per symbol root:
    {ROOT: {qty, proceeds_net, proceeds_gross, cost, rows, tainted_rows,
    listings}} (listings: the same per listing suffix — SAMPLB.TO and
    SAMPLB.US are different securities, R1-292).
    Tainted rows are INCLUDED in the counts here (the broker's slip will
    include those sales too) but flagged so a basis mismatch on a tainted
    symbol reads as expected, not alarming."""
    out: Dict[str, Dict[str, Any]] = {}
    ystr = str(year) if year else None
    # This year's grant-timing writes per listing, in write order:
    # (order, units, gross premium, net premium, account).
    grant_writes: Dict[Tuple[str, str],
                       List[Tuple[Tuple[str, str, int], float, float,
                                  float, str]]] = {}
    # The units of this year's writes closed, per listing and account.
    short_close_qty: Dict[Tuple[str, str], Dict[str, float]] = {}

    def _closed(key: Tuple[str, str], account: str, units: float) -> None:
        by = short_close_qty.setdefault(key, {})
        by[account] = by.get(account, 0.0) + units
    for p in gains_paths:
        data = load_json(p)
        # The grant-timed closes the engine leaves out of the gains rows
        # (nil proceeds and gain: a worthless expiry). The contract
        # closed, so the broker's slip of this year reports it at the
        # write's premium: an earlier year's write as a buy-back's
        # prior_grant, this year's with its write's row below (backlog
        # TJ-1091; a file without the key: the expiry date below).
        for c in data.get("grant_nil_closes") or []:
            if date_basis == "trade":
                date = c.get("date") or c.get("date_settle") or ""
            else:
                date = c.get("date_settle") or c.get("date") or ""
            if ystr and not date.startswith(ystr):
                continue
            root, sfx = split_listing((c.get("symbol") or "").strip().upper()
                                      .lstrip("."))
            if not root:
                continue
            rec = out.setdefault(root, _computed_record())
            for y, v in _grant_closed(c).items():
                units = abs(float((v or {}).get("units") or 0.0))
                if ystr and str(y) < ystr:
                    # cost: a listing with no other row of the year starts
                    # at None, and _compare subtracts it from the slip's
                    # (backlog TJ-1181).
                    _bump(rec, sfx, qty=units, rows=1, cost=0.0,
                          prior_grant=float((v or {}).get("premium") or 0.0))
                elif not ystr or str(y) == ystr:
                    _bump(rec, sfx, qty=units, cost=0.0)
                    _closed((root, sfx), str(c.get("account") or p), units)
        # Tainted (unknown-cost) dispositions live in
        # manual_reporting_required, NOT transactions — the pipeline
        # strips them there before writing the gains file, which made
        # every tainted_rows counter in this tool a permanent no-op
        # and a real broker-reported sale show as
        # MISSING_FROM_COMPUTED with the wrong diagnosis (2026-09
        # audit). Fold them back in, flagged.
        _manual = [dict(e, tainted=True)
                   for e in data.get("manual_reporting_required") or []
                   if GainRow(e).quantity]
        for e in itertools.chain(data.get("transactions") or [], _manual):
            if e.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            # Manual (unknown-cost) rows carry qty and proceeds but no
            # `gain` (the pipeline strips it); requiring `gain` dropped
            # every one of them again (R1-206).
            if "qty" not in e or ("gain" not in e and not e.get("tainted")):
                continue
            # Year-scope on the same basis the gains files (and the
            # broker's slip) use: the IRS recognizes on TRADE date, so a
            # 1099-B includes a Dec-31 sale settling in January — the
            # unconditional settle-first read excluded it and produced
            # spurious MISMATCH rows against the tool's own form-export.
            # CRA times dispositions on SETTLEMENT date (the default).
            if date_basis == "trade":
                date = e.get("date") or e.get("date_settle") or ""
            else:
                date = e.get("date_settle") or e.get("date") or ""
            if ystr and not date.startswith(ystr):
                continue
            full = (e.get("symbol") or "").strip().upper().lstrip(".")
            root, sfx = split_listing(full)
            if not root:
                continue
            # open_grant*: this year's grant-timing writes still open at
            # the year end (premium reported now, s.49(1); the broker's
            # slip reports it at the close). prior_grant: the premium of
            # an EARLIER year's grant write this year's buy-back closed —
            # the broker's close-year slip carries it as proceeds
            # (A2-0657).
            rec = out.setdefault(root, _computed_record())
            view = GainRow(e)
            proceeds = float(view.proceeds or 0.0)
            cost = float((row_cost := view.cost) or 0.0)   # read once
            outlays = float(view.commission or 0.0) + float(view.fee or 0.0) + float(view.write_outlay or 0.0)   # (an assigned call's write commission: the gross form-export shows, TJ-5462)
            short = (e.get("direction") or "LONG") == "SHORT"
            # An unknown-cost cover of a short sale made before the data
            # (form-export's is_short_cover): the cover cost is known,
            # what the short sale brought in is NOT — the row has no
            # `cost`. Reading it as 0 certified a zero-proceeds slip and
            # called an absent one a worthless expiry (backlog TJ-0906).
            unknown_proceeds = bool(short and e.get("tainted")
                                    and row_cost is None)
            if short:
                # Engine short convention: `cost` holds the (negated)
                # short-sale proceeds and `proceeds` the (negated)
                # cover cost — the SWAP form-export performs. Merely
                # stripping signs compared the slip's proceeds against
                # the COVER COST, a false MISMATCH equal to the gain
                # (2026-09 audit). Un-negated, never abs(): a write whose
                # commission exceeds its premium brought in a net DEBIT
                # (cost +7 is proceeds -7), which abs() made +7 and
                # certified a wrong-signed slip (backlog TJ-0780; form-
                # export's build_8949 swaps the same way).
                proceeds, cost = -cost + 0.0, -proceeds + 0.0
                # A US re-short's carried wash-sale loss is basis on the
                # 1099-B, not a cut of its proceeds (backlog TJ-0465;
                # form-export's (d) and (e) the same).
                _carried = short_carried_loss(e)
                proceeds, cost = proceeds + _carried, cost + _carried
                if e.get("grant") and -float(row_cost or 0.0) \
                        + outlays >= 0:
                    # A grant-timing WRITE: the row's commission is the
                    # write's own, so the gross premium is the net plus
                    # it (form-export's Schedule 3 shows the same gross,
                    # R1-40). A buy-back's fee is a cost, never proceeds.
                    proceeds = -float(row_cost or 0.0)
                else:
                    outlays = float(view.write_outlay or 0.0)   # (a close-timing write's: gross, TJ-5462)
            _fut_loss = 0
            if not unknown_proceeds and _future_on_slip(full, country):
                # Booked on its settled P/L (lib/futures): form-export
                # shows it as the T5008 does, a gain as proceeds, a loss
                # as cost (before any denial, commissions inside: its
                # build_schedule3, build_8949). The generic short swap
                # and long reading compared neither (backlog TJ-1093). A
                # tainted row's raw_gain is the engine's made-up figure.
                if country == CANADA:
                    _pl = (float(rg)
                           if (rg := view.raw_gain) is not None
                           and not e.get("tainted") else proceeds - cost)
                    proceeds, cost = max(_pl, 0.0) + 0.0, max(-_pl, 0.0) + 0.0
                else:
                    # Form 8949 reshapes only a negative figure: a row of
                    # carried §1091(d) basis keeps its (d) and (e)
                    # (build_8949; backlog TJ-1252).
                    _pl = proceeds - cost
                    if proceeds < 0 or cost < 0:
                        proceeds, cost = (max(_pl, 0.0) + 0.0,
                                          max(-_pl, 0.0) + 0.0)
                outlays = 0.0
                _fut_loss = 1 if _pl < -0.005 else 0
            q = abs(float(view.quantity or 0.0))
            # Grant timing books a written option twice — the WRITE
            # record and its buy-back/expiry — while the slip reports
            # ONE disposition of those contracts: quantity counts once
            # (R1-18). Settled after the loop.
            key = (root, sfx)
            if e.get("grant"):
                _ws = grant_writes.setdefault(key, [])
                _ws.append(((str(e.get("date") or ""),
                             str(e.get("date_settle") or ""), len(_ws)),
                            q, proceeds + outlays, proceeds,
                            str(e.get("account") or p)))
                q = 0.0
            elif short:
                _closed(key, str(e.get("account") or p),
                        grant_buyback_units(e, year))
                if ystr:
                    _prior = sum(
                        float((v or {}).get("premium") or 0.0)
                        for y, v in _grant_closed(e).items()
                        if str(y) < ystr)
                    if _prior:
                        _bump(rec, sfx, prior_grant=_prior)
            if unknown_proceeds:
                proceeds = outlays = 0.0
            _bump(rec, sfx, qty=q, proceeds_net=proceeds,
                  proceeds_gross=proceeds + outlays, cost=cost, rows=1,
                  tainted_rows=(1 if e.get("tainted")
                                and not unknown_proceeds else 0),
                  unknown_proceeds_rows=1 if unknown_proceeds else 0,
                  capitalized_263h=float(view.capitalized_263h or 0.0),
                  future_loss_rows=_fut_loss)
    from datetime import date as _date
    from taxjson.lib.option_symbols import parse_option_expiry
    _today = _date.today().isoformat()
    for (root, sfx), writes in grant_writes.items():
        writes.sort(key=lambda w: w[0])
        gq = sum(w[1] for w in writes)
        _by_acct = short_close_qty.get((root, sfx), {})
        extra = max(0.0, gq - sum(_by_acct.values()))
        if extra <= 1e-9:
            continue
        _exp = parse_option_expiry(root)
        if ystr and _exp and _exp <= f"{ystr}-12-31" and _exp < _today:
            # The contract expired by the year end, so none of it is
            # open then: the units no buy-back closed expired, a close
            # the engine leaves out of the gains file when it is worth
            # nil (zero proceeds and gain). The broker's slip reports
            # them, at the write's premium, in THIS year — they are not
            # "still open" (backlog TJ-0781).
            _bump(out[root], sfx, qty=extra)
            continue
        # Written this year, still open (or closed next year): the
        # write itself is the year's disposition, at its own premium — an
        # average of every write's put a closed write's premium on the
        # open contract (backlog TJ-0779).
        og = ogn = 0.0
        for left, (_order, units, gross, net, _a) in zip(
                _open_units(writes, _by_acct), writes):
            if units > 1e-12:
                og += gross * left / units
                ogn += net * left / units
        _bump(out[root], sfx, qty=extra, open_grant_qty=extra,
              open_grant=og, open_grant_net=ogn)
    return out
