"""Shared test helpers: a seeded generator of books whose trades straddle settlement, and a settle-date clearer.

Shared from: test_settle_straddle_fuzz
"""
import random
from datetime import date, timedelta

from taxjson.model.transaction import TaxTransaction


BASE = date(2025, 1, 6)
GRID = 9          # days between slots; > max lag + split offset


def d(off):
    return (BASE + timedelta(days=int(off))).isoformat()


def build_book(seed):
    """Correct position-tracked generator (gen_book above kept the
    emission logic; this wrapper re-walks to force clean full closes).
    Simpler: regenerate with explicit split-aware position tracking."""
    rng = random.Random(seed)
    # "evening": the split is DATED the anchor's trade day with an
    # after-close clock time (IB's 20:25 corporate-action batch) — the
    # anchor executed that morning is pre-split, exactly like "inside".
    placement = ("before", "on", "inside", "after",
                 "evening")[seed % 5]
    sym = "S0.TO"
    use_other = rng.random() < 0.5
    accounts = ["A0", "A1"][: rng.randint(1, 2)]
    use_shel = rng.random() < 0.4
    ratio = rng.choice([2.0, 3.0, 0.5])
    n_slots = rng.randint(8, 12)
    anchor_slot = rng.randint(3, n_slots - 3)
    anchor_lag = rng.choice([2, 3])

    if placement == "before":
        split_day = anchor_slot * GRID - rng.randint(4, 5)
    elif placement == "on":
        split_day = anchor_slot * GRID + anchor_lag
    elif placement == "inside":
        split_day = anchor_slot * GRID + rng.randint(1, anchor_lag - 1)
    elif placement == "evening":
        split_day = anchor_slot * GRID
    else:
        split_day = anchor_slot * GRID + anchor_lag + 1

    txs, shel = [], []
    anchor = [None]

    def emit(lst, sym_, slot, qty, acct, lag=0):
        day = slot * GRID
        px = rng.uniform(5.0, 50.0)
        kw = dict(action="BUYSELL", date=d(day),
                  time=f"{rng.randint(9, 15):02d}:{rng.randint(0, 59):02d}:00",
                  symbol=sym_, quantity=float(qty), currency="CAD",
                  net_amount=round(abs(qty) * px, 2), account=acct)
        if lag:
            kw["date_settle"] = d(day + lag)
        if rng.random() < 0.4:
            kw["price"] = round(px, 4)
        t = TaxTransaction(**kw)
        lst.append(t)
        return t

    # positions tracked in TRADE-DATE denomination; scaled once when
    # the walk passes split_day (trades executed pre-split are quoted
    # pre-split; post-split trades post-split -- broker semantics).
    pos = {a: 0.0 for a in accounts}
    shel_pos = [0.0]
    scaled = [False]

    def maybe_scale(day):
        # An evening split is still ahead of the trades DATED its day.
        if not scaled[0] and (day > split_day or (
                day == split_day and placement != "evening")):
            for a in pos:
                pos[a] *= ratio
            shel_pos[0] *= ratio
            scaled[0] = True

    slot = 0
    while slot < n_slots:
        day = slot * GRID
        maybe_scale(day)
        if slot == anchor_slot:
            a = accounts[0]
            q = rng.choice([8.0, 16.0, 24.0, 40.0])
            if pos[a] > 0 and rng.random() < 0.5:
                q = -min(q, pos[a]) * rng.choice([1.0, 0.5])
            elif pos[a] < 0 and rng.random() < 0.5:
                q = min(q, -pos[a]) * rng.choice([1.0, 0.5])
            elif rng.random() < 0.4:
                q = -q
            if not q:
                q = 8.0
            anchor[0] = emit(txs, sym, slot, q, a, lag=anchor_lag)
            pos[a] += q
        else:
            r = rng.random()
            if r < 0.15 and use_shel:
                q = rng.choice([8.0, 16.0, 24.0])
                if shel_pos[0] > 0 and rng.random() < 0.4:
                    q = -min(q, shel_pos[0])
                if q:
                    emit(shel, sym, slot, q, "rrsp")
                    shel_pos[0] += q
            elif r < 0.85:
                a = rng.choice(accounts)
                q = rng.choice([8.0, 16.0, 24.0, 40.0, 100.0])
                if pos[a] > 0 and rng.random() < 0.55:
                    q = -min(q, pos[a]) * rng.choice([1.0, 0.5])
                elif pos[a] < 0 and rng.random() < 0.55:
                    q = min(q, -pos[a]) * rng.choice([1.0, 0.5])
                elif rng.random() < 0.35:
                    q = -q
                if q:
                    emit(txs, sym, slot, q, a, lag=lag_for(rng))
                    pos[a] += q
        slot += 1
    # force full closes on dedicated tail slots (post-split units when
    # the split has passed; scale if the split lies beyond all slots)
    maybe_scale(slot * GRID)
    if not scaled[0]:
        for a in pos:
            pos[a] *= ratio
        shel_pos[0] *= ratio
        scaled[0] = True
    for a in accounts:
        if abs(pos[a]) > 1e-9:
            emit(txs, sym, slot, -pos[a], a, lag=lag_for(rng))
            slot += 1
    if use_shel and abs(shel_pos[0]) > 1e-9 and rng.random() < 0.5:
        emit(shel, sym, slot, -shel_pos[0], "rrsp")
        slot += 1

    # one plain SPLIT row (single corporate event)
    split = TaxTransaction(action="SPLIT", date=d(split_day),
                           time=("20:25:00" if placement == "evening"
                                 else "00:00:01"),
                           symbol=sym, symbol_new="",
                           quantity=float(ratio), currency="CAD",
                           account=accounts[0])
    txs.append(split)

    # independent second symbol: lagged rows, no split (pure noise)
    if use_other:
        opos = 0.0
        for oslot in range(rng.randint(2, 5)):
            q = rng.choice([10.0, 25.0, 50.0])
            if opos > 0 and rng.random() < 0.5:
                q = -min(q, opos)
            elif rng.random() < 0.3:
                q = -q
            if q:
                emit(txs, "S1.TO", oslot + 1, q, accounts[-1],
                     lag=lag_for(rng))
                opos += q
        if abs(opos) > 1e-9:
            emit(txs, "S1.TO", n_slots + 3, -opos, accounts[-1],
                 lag=lag_for(rng))

    meta = {"placement": placement, "ratio": ratio,
            "split_date": d(split_day), "anchor_id": anchor[0].id,
            "anchor_date": anchor[0].date,
            "anchor_settle": anchor[0].date_settle}
    return txs, shel, meta


def lag_for(rng):
    return rng.choice([0, 0, 0, 1, 2, 3])


def clear_settle(txs, only_id=None):
    out = []
    for t in txs:
        if t.action == "BUYSELL" and t.date_settle and \
                (only_id is None or t.id == only_id):
            dct = t.to_dict()
            dct["date_settle"] = ""
            out.append(TaxTransaction(**dct))
        else:
            out.append(t)
    return out
