"""Settle-straddle differential fuzzer — promoted from the
round-six scratch harness that found the phase-unaware
_bal_before walk bug (core.py; seeds 113/317/433/781/1433).

The invariants fuzzer never generates settle-lagged rows; this one does
nothing else. Canada books where BUYSELL rows carry date_settle 1-3
days after date, with one plain SPLIT placed before / on / strictly
inside / after a chosen anchor trade's lag, or dated the anchor's trade
day with an EVENING clock time (IB's 20:25 corporate-action batch —
the anchor executed that morning is pre-split, like "inside"); long
and short positions,
partial closes, wash rebuys in-window, occasional sheltered context.

Design constraints that make the differential laws sound:
  * All trade dates sit on a 9-day grid, one row per (symbol, slot)
    globally, lags <= 3.  Hence (i) lags can never reorder two trades
    relative to each other, and (ii) every pairwise date difference is
    a multiple of 9 shifted by at most +-3, so +-30-day window
    membership and the +30 still-held evaluation cannot flip when lags
    are cleared (27+-3 <= 30 in-window; 36+-3 >= 33 out).
  * The SPLIT lands within +-5 days of the anchor slot, so it can only
    ever straddle the ANCHOR trade's lag, never a neighbour's.

Laws checked per seed:
  (a) no exception at all for plain splits; SplitStraddlesSettlement-
      Error raised for (and only for) rename-splits strictly inside a
      lag;
  (b) conservation on every run: wash - parked == nowash + permanent;
  (c) placement != inside: clearing every date_settle changes no total
      (the lag must be a no-op when nothing straddles);
  (d) placement == inside: totals equal the equivalent book where the
      straddling trade keeps its pre-split-denominated quantity and
      settles on its trade date (booked before the split).
"""
import io
import json
import os
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta

from taxjson.engine.by_country import get_tax_rules
from taxjson.engine.ca.taxrules import SplitStraddlesSettlementError
from taxjson.model.transaction import TaxTransaction
from _support.engine_settle_fuzz_books import build_book, clear_settle

# 200 seeds (50 per split placement) keep the default suite fast;
# the round-six sweep ran 1,600 and CI/nightly should too.
N_SEEDS = int(os.environ.get("TAXJSON_STRADDLE_FUZZ_BOOKS", "200"))


def run(txs, shel, wash=True):
    with redirect_stderr(io.StringIO()):
        return get_tax_rules("canada").compute_gains(
            [TaxTransaction(**t.to_dict()) for t in txs],
            sheltered_transactions=[TaxTransaction(**t.to_dict())
                                    for t in shel],
            detect_wash_sales=wash)


def totals(res):
    gains = [g for g in res["transactions"]
             if g.get("qty") and "gain" in g and not g.get("tainted")]
    w = round(sum(float(g["gain"] or 0) for g in gains), 2)
    p = round(sum(float(g.get("permanently_disallowed") or 0)
                  for g in gains), 2)
    parked = round(sum(float(r.get("deferred_wash") or 0.0)
                       for r in res.get("inventory") or []), 2)
    return w, p, parked


def conservation_gap(txs, shel):
    rw = run(txs, shel, True)
    rn = run(txs, shel, False)
    w, perm, parked = totals(rw)
    n, _, _ = totals(rn)
    return round((w - parked) - (n + perm), 2), (w, perm, parked, n)


def book_dump(txs, shel):
    return json.dumps({"taxable": [t.to_dict() for t in txs],
                       "sheltered": [t.to_dict() for t in shel]},
                      indent=1)


def check_seed(seed):
    """None, or a violation string."""
    txs, shel, meta = build_book(seed)
    pl = meta["placement"]
    # ---- (a)+(b) main run --------------------------------------
    try:
        gap, parts = conservation_gap(txs, shel)
    except Exception as e:  # noqa: BLE001
        return f"{seed}: " + "crash" + ": " + str(f"{type(e).__name__}: {e}")
    if abs(gap) > 0.05:
        return f"{seed}: " + "conservation" + ": " + str(f"gap={gap} {parts}")
    if pl not in ("inside", "evening"):
        # ---- (c) lag no-op differential ----------------------
        t2 = clear_settle(txs)
        try:
            gap2, parts2 = conservation_gap(t2, shel)
        except Exception as e:  # noqa: BLE001
            return f"{seed}: " + "crash-cleared" + ": " + str(f"{type(e).__name__}: {e}")
        a = totals(run(txs, shel, True)) + totals(run(txs, shel,
                                                      False))[:1]
        b = totals(run(t2, shel, True)) + totals(run(t2, shel,
                                                     False))[:1]
        if any(abs(x - y) > 0.011 for x, y in zip(a, b)):
            return f"{seed}: " + "lag-not-noop" + ": " + str(f"lagged={a} cleared={b}")
    else:
        # ---- (d) straddle differential -----------------------
        t3 = clear_settle(txs, only_id=meta["anchor_id"])
        if pl == "evening":
            # The oracle books the anchor BEFORE the split. With the
            # anchor now settling on its trade date, the ladder would
            # put a same-day split first, so the oracle's split moves
            # to the next morning — the same economics (IB's evening
            # batch IS the next open's split).
            nxt = (date.fromisoformat(meta["split_date"])
                   + timedelta(days=1)).isoformat()
            t3 = [TaxTransaction(**{**t.to_dict(), "date": nxt,
                                    "time": "00:00:01"})
                  if t.action == "SPLIT" else t for t in t3]
        try:
            a = totals(run(txs, shel, True)) + totals(
                run(txs, shel, False))[:1]
            b = totals(run(t3, shel, True)) + totals(
                run(t3, shel, False))[:1]
        except Exception as e:  # noqa: BLE001
            return f"{seed}: " + "crash-differential" + ": " + str(f"{type(e).__name__}: {e}")
        if any(abs(x - y) > 0.011 for x, y in zip(a, b)):
            return f"{seed}: " + "straddle-differential" + ": " + str(f"straddle={a} presplit-booked={b}")
        # ---- (a) rename-in-lag must refuse -------------------
        t4 = []
        for t in txs:
            if t.action == "SPLIT":
                dct = t.to_dict()
                dct["symbol_new"] = "S0N.TO"
                t4.append(TaxTransaction(**dct))
            elif t.date > meta["split_date"]:
                dct = t.to_dict()
                dct["symbol"] = "S0N.TO"
                t4.append(TaxTransaction(**dct))
            else:
                t4.append(t)
        sh4 = []
        for t in shel:
            if t.date > meta["split_date"]:
                dct = t.to_dict()
                dct["symbol"] = "S0N.TO"
                sh4.append(TaxTransaction(**dct))
            else:
                sh4.append(t)
        try:
            run(t4, sh4, True)
            return f"{seed}: " + "rename-in-lag-not-refused" + ": " + str("")
        except SplitStraddlesSettlementError:
            pass
        except Exception as e:  # noqa: BLE001
            return f"{seed}: " + "rename-in-lag-wrong-error" + ": " + str(f"{type(e).__name__}: {e}")
    if pl in ("before", "after") and seed % 7 == 0:
        # rename NOT in a lag must not raise (fresh-symbol rename)
        t5, sh5 = [], []
        for t in txs:
            if t.action == "SPLIT":
                dct = t.to_dict()
                dct["symbol_new"] = "S0N.TO"
                t5.append(TaxTransaction(**dct))
            elif t.date > meta["split_date"]:
                dct = t.to_dict()
                dct["symbol"] = "S0N.TO"
                t5.append(TaxTransaction(**dct))
            else:
                t5.append(t)
        for t in shel:
            if t.date > meta["split_date"]:
                dct = t.to_dict()
                dct["symbol"] = "S0N.TO"
                sh5.append(TaxTransaction(**dct))
            else:
                sh5.append(t)
        try:
            g5, p5 = conservation_gap(t5, sh5)
            if abs(g5) > 0.05:
                return f"{seed}: " + "rename-conservation" + ": " + str(f"gap={g5} {p5}")
            pass
        except SplitStraddlesSettlementError as e:
            return f"{seed}: " + "rename-off-lag-refused" + ": " + str(str(e))
        except Exception as e:  # noqa: BLE001
            return f"{seed}: " + "rename-off-lag-crash" + ": " + str(f"{type(e).__name__}: {e}")
    return None


class TestSettleStraddleFuzz(unittest.TestCase):
    """Differential laws over settle-lagged books. On failure the
    message carries seed + kind; rebuild via build_book(seed) in a
    scratch script and shrink from there."""

    def test_straddle_differentials(self):
        fails = [v for v in (check_seed(s) for s in range(N_SEEDS))
                 if v]
        if fails:
            self.fail(f"{len(fails)} violation(s); first: {fails[0]}")


if __name__ == "__main__":
    unittest.main()
