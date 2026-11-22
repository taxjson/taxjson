"""The units of a symbol held at the end of a date, from the book's own
rows (balance_on): position deltas per account and raw symbol, splits and
renames applied, on the trade or the settlement date (DATE_BASES).
bin/taxjson_apply_distributions.py prices a distribution's record-date
holding with it; the T1135 and the corporate-action views read it too.
"""
from typing import List


DATE_BASES = ("trade", "settle")


def _row_date(t: dict, date_basis: str) -> str:
    """The date a row changes the holder-of-record balance on. Under
    `settle` that is `date_settle` (falling back to the trade date for
    rows that carry none — SPLIT / OPENING_BALANCE / ADJUST); under
    `trade` it is `date`."""
    if date_basis == "settle":
        return str(t.get("date_settle") or t.get("date") or "")
    return str(t.get("date") or "")


def balance_on(transactions: List[dict], symbol: str, date: str,
               date_basis: str = "settle") -> float:
    """Shares of `symbol` held at end of `date`, from the book's own
    rows: position deltas summed per account and raw symbol, each SPLIT
    applied to its own symbol's shares, renames (symbol_new) moving
    them — so a map row keyed to the CURRENT ticker finds shares bought
    under a pre-rename one, and only those.

    `date_basis` picks which date a row moves the balance on. Fund
    record dates go by the holder of record — the SETTLED position —
    so under `settle` (what `tjs run` uses in both countries: the
    record date is a market fact, not the tax-year date basis —
    partition INPUTS-10) a sale traded 06-19 that settles 06-22 still
    holds on a 06-19 record date, and a buy traded ON the record date
    (settling after it) is not yet credited. `trade` is a manual
    override."""
    if date_basis not in DATE_BASES:
        raise ValueError(f"date_basis must be one of {DATE_BASES}, "
                         f"got {date_basis!r}")
    # A trade executed BEFORE a split but settling AFTER it: the engine
    # re-denominates its quantity into post-split shares
    # (engine/ca/taxrules, the settle-lag re-denomination), because under
    # settle ordering the pool splits first. Do the same here, or the
    # pre-split sale was subtracted from the already-split balance (audit
    # S000-07: 1500 shares sized an ADJUST where the engine held 1000).
    factor = _settle_lag_factors(transactions) \
        if date_basis == "settle" else {}
    # Order like the Canada balance walk (corporate_timeline 'ca_balance'
    # profile, tax-logic CA-DATE-14): at one sort date an opening balance
    # or a settle-lagged execution first, then a split (effective at the
    # open), then the day's own trades — whatever the export's row order
    # (audit A2-0225: a buy listed before a same-stamp SPLIT was scaled
    # by it).
    def _phase(t: dict) -> int:
        act = t.get("action")
        if act == "OPENING_BALANCE":
            return 0
        if act == "SPLIT":
            return 1
        d = str(t.get("date") or "")
        return 0 if d and d < _row_date(t, date_basis) else 2
    rows = sorted(transactions,
                  key=lambda t: (_row_date(t, date_basis), _phase(t),
                                 str(t.get("time") or "")))
    # One corporate event = one application: an account fed by two
    # brokers carries the same SPLIT once per broker CSV (distinct ids,
    # so upstream dedup keeps both). The gains engine dedupes these per
    # corporate event; without the same dedup here, the blended
    # per-account split apportioned DOUBLED quantities (2026-09 audit).
    # split_copies is THE corporate-event rule (CA-CORP-12): the same
    # split booked on two dates by two sources (within a week) is ONE
    # event, kept on its earliest date. Keyed per account here: each
    # account's own copy scales that account's shares.
    from taxjson.lib.corporate_timeline import (normalize_symbol_new,
                                                split_copies)
    _dup_splits = split_copies(rows, per_account=True)
    # Shares per (account, raw symbol), the way the engine walk holds
    # them: a SPLIT scales and moves only the shares of ITS symbol (in
    # its account), so shares already held under a rename's target are
    # not scaled by the rename's ratio (audit A2-0021), an old ticker
    # traded again after its rename stays its own holding (A2-0074),
    # and one account's copy of a split never scales another account's
    # shares (A2-0986).
    held: dict = {}
    for t in rows:
        if _row_date(t, date_basis) > date:
            break
        sym = str(t.get("symbol") or "")
        acct = str(t.get("account") or "")
        act = t.get("action")
        if act in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            held[(acct, sym)] = held.get((acct, sym), 0.0) + (
                float(t.get("quantity") or 0.0) * factor.get(id(t), 1.0))
        elif act == "SPLIT":
            if id(t) in _dup_splits:
                continue
            # A ratio of 0 is a rename only: the US engine skips its
            # scaling and still moves the shares to symbol_new
            # (engine/us/walk.py, the US SPLIT branch). Skipping the
            # whole row left the shares under the old ticker and sized
            # the new one's distribution on none (backlog TJ-0491).
            ratio = float(t.get("quantity") or 0.0) or 1.0
            target = normalize_symbol_new(sym, t.get("symbol_new")) or sym
            if ratio == 1.0 and target == sym:
                continue
            for key in [k for k in held
                        if k[1] == sym and (not acct or k[0] == acct)]:
                moved = held.pop(key) * ratio
                held[(key[0], target)] = held.get((key[0], target),
                                                  0.0) + moved
    return sum(q for (_a, s), q in held.items() if s == symbol)


def _settle_lag_factors(transactions: List[dict]) -> dict:
    """{id(row): factor} for BUYSELL/ASSIGN rows whose settle lag
    straddles a plain SPLIT of their symbol: executed (date, time)
    before the split's moment, settling after its date. The same test
    the gains engine uses to re-denominate the executed quantity, on
    the same split events: one split reported twice (two sources, dates
    within a week) is ONE event, counted once (split_copies, keyed per
    security like the engine's dedup — GitHub #92: both copies scaled
    the lagged sale and the record-date holding came out short)."""
    from taxjson.lib.corporate_timeline import dedupe_split_events
    splits = [t for t in dedupe_split_events(transactions)
              if t.get("action") == "SPLIT" and t.get("date")
              and float(t.get("quantity") or 0.0)
              and (t.get("symbol_new") or t.get("symbol"))
              == t.get("symbol")]
    out: dict = {}
    if not splits:
        return out
    for t in transactions:
        d, ds = str(t.get("date") or ""), str(t.get("date_settle") or "")
        if t.get("action") not in ("BUYSELL", "ASSIGN") or not d \
                or not ds or d >= ds:
            continue
        f = 1.0
        for sp in splits:
            spd = str(sp.get("date_settle") or sp.get("date"))
            if (sp.get("symbol") == t.get("symbol")
                    and (d, str(t.get("time") or "00:00:00"))
                    < (spd, str(sp.get("time") or "00:00:00"))
                    and spd < ds):
                f *= float(sp["quantity"])
        if abs(f - 1.0) > 1e-12:
            out[id(t)] = f
    return out
