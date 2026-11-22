"""Re-audit-2 test pins (tests-pins-03): the position walks twinned
across tools — A2-1590 (one split reported by two brokers is applied
once; the apply-distributions split key includes the account), A2-1539
(ASSIGN and TRANSFER rows move every walk's position) — and A2-1564
(a US Kraken stablecoin withdrawal fee is a sale at par). Synthetic
data only.

Formerly: test_fix_a2_pins1_03_walks"""
import os
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

SYM = "QZT.US"


def _r(action, d, qty, account="m", price=10.0, sym=SYM, **kw):
    return dict(action=action, date=d, date_settle=d, time="10:00:00",
                symbol=sym, quantity=qty, price=price,
                net_amount=abs(qty) * price, currency="USD",
                account=account, **kw)


def _events(rows):
    from taxjson.bin.taxjson_export import _load_trade_events
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "raw.json"
        p.write_text(json.dumps({"transactions": rows}))
        ev = _load_trade_events([p])
    return [(e["action"], e["qty"]) for e in ev.get(SYM, [])]


def _starts(rows):
    from taxjson.bin.taxjson_split_gains import _position_starts
    return _position_starts(rows, "settle").get(SYM)


def _balance(rows, d):
    from taxjson.lib.unit_balance import balance_on
    return balance_on(rows, SYM, d)


class TestAssignAndTransferMoveEveryWalk(unittest.TestCase):
    """A2-1539: a put assignment (ASSIGN +shares) and a transfer-in move
    the position in each twin walk; dropping either from a walk's action
    set changes its answer.

    Formerly: test_fix_a2_pins1_03_walks.TestAssignAndTransferMoveEveryWalk"""

    def _book(self, action):
        return [_r(action, "2025-01-10", 100),
                _r("BUYSELL", "2025-02-03", -100),
                _r("BUYSELL", "2025-03-03", 50)]

    def test_export_trade_events(self):
        for act in ("ASSIGN", "TRANSFER"):
            self.assertEqual(_events(self._book(act)), [("BUY", 50.0)], act)

    def test_split_gains_position_start(self):
        for act in ("ASSIGN", "TRANSFER"):
            self.assertEqual(_starts(self._book(act)), "2025-03-03", act)

    def test_apply_distributions_balance(self):
        for act in ("ASSIGN", "TRANSFER"):
            self.assertEqual(_balance(self._book(act), "2025-01-31"),
                             100.0, act)

    def test_corp_views_held(self):
        from taxjson.lib.corp_views import _held
        for act in ("ASSIGN", "TRANSFER", "OPENING_BALANCE"):
            self.assertEqual(_held(self._book(act), SYM, "2025-01-31",
                                   True), 100.0, act)

    def test_edge_cases_walk(self):
        from taxjson.lib.edge_cases import _walk
        for act in ("ASSIGN", "TRANSFER", "OPENING_BALANCE"):
            rows = [dict(r, _acct="m") for r in self._book(act)]
            pos = _walk(rows, lambda r: date.fromisoformat(r["date"]),
                        until=date(2025, 1, 31))
            self.assertEqual(pos.get(("m", SYM)), 100.0, act)


class TestSumGainsSortBy(unittest.TestCase):
    """A2-1596: taxjson-sum-gains --sort-by (get_sort_value) was never run
    by the suite. Each key orders the ticker summary by its own column,
    largest first.

    Formerly: test_fix_a2_pins1_03_walks.TestSumGainsSortBy"""

    STATS = {
        "AAA.TO": {"CAD": {"cap": 10.0, "opt": 0.0, "div": 100.0,
                           "pil": 0.0, "cost": 90.0, "proceeds": 100.0,
                           "trade_count": 1, "hold_days": [400]}},
        "MMM.TO": {"CAD": {"cap": 0.0, "opt": 60.0, "div": 0.0,
                           "pil": 30.0, "cost": 40.0, "proceeds": 100.0,
                           "trade_count": 1, "hold_days": [300]}},
        "ZZZ.TO": {"CAD": {"cap": 50.0, "opt": 0.0, "div": 0.0,
                           "pil": 0.0, "cost": 50.0, "proceeds": 100.0,
                           "trade_count": 2, "hold_days": [200, 200]}},
    }

    def _order(self, key):
        from taxjson.bin.taxjson_sum_gains import format_report
        out = format_report({"ticker_stats": self.STATS, "total_year": 2025,
                             "returns_by_asset": {}, "total_fees": {},
                             "option_fees": {}},
                            sort_by=key, no_color=True)
        rows = [ln.split()[0] for ln in out.splitlines()
                if ln[:6] in ("AAA.TO", "MMM.TO", "ZZZ.TO")]
        return rows

    def test_each_key(self):
        self.assertEqual(self._order("ticker"), ["AAA.TO", "MMM.TO", "ZZZ.TO"])
        self.assertEqual(self._order("total"), ["AAA.TO", "MMM.TO", "ZZZ.TO"])
        self.assertEqual(self._order("total_gain"),
                         ["MMM.TO", "ZZZ.TO", "AAA.TO"])
        self.assertEqual(self._order("capital_gain"),
                         ["ZZZ.TO", "AAA.TO", "MMM.TO"])
        self.assertEqual(self._order("option_gain"),
                         ["MMM.TO", "AAA.TO", "ZZZ.TO"])
        self.assertEqual(self._order("dividend"),
                         ["AAA.TO", "MMM.TO", "ZZZ.TO"])
        self.assertEqual(self._order("pil"), ["MMM.TO", "AAA.TO", "ZZZ.TO"])
        # The average per trade (ZZZ: 2 trades, 400 days in all).
        self.assertEqual(self._order("holding_days"),
                         ["AAA.TO", "MMM.TO", "ZZZ.TO"])


if __name__ == "__main__":
    unittest.main()
