"""Re-audit-2 test pins (tests-pins-03): the position walks twinned
across tools — A2-1590 (one split reported by two brokers is applied
once; the apply-distributions split key includes the account), A2-1539
(ASSIGN and TRANSFER rows move every walk's position) — and A2-1564
(a US Kraken stablecoin withdrawal fee is a sale at par). Synthetic
data only."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule

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
    from taxjson.bin.taxjson_apply_distributions import balance_on
    return balance_on(rows, SYM, d)


# A split reported by two brokers of one account, two days apart.
_DUP_SPLIT = [_r("BUYSELL", "2025-01-02", 100),
              _r("SPLIT", "2025-06-11", 2.0),
              _r("SPLIT", "2025-06-13", 2.0),
              _r("BUYSELL", "2025-07-01", -200),
              _r("BUYSELL", "2025-08-01", 50)]


class TestSplitAppliedOnce(unittest.TestCase):
    """A2-1590: the export's trade events and split-gains' position start
    apply one split event once, as the radar, T1135 and
    apply-distributions do."""

    def test_export_events_keep_only_the_live_round(self):
        self.assertEqual(_events(_DUP_SPLIT), [("BUY", 50.0)])

    def test_export_events_two_accounts_one_split_event(self):
        """The export's walk sums every account's shares into one
        balance, so each account's own copy of a split is still ONE
        event for it: scaling the summed balance once per account
        doubled it and kept a closed round's events."""
        rows = [_r("BUYSELL", "2025-01-02", 100, account="a"),
                _r("BUYSELL", "2025-01-02", 100, account="b"),
                _r("SPLIT", "2025-06-11", 2.0, account="a"),
                _r("SPLIT", "2025-06-11", 2.0, account="b"),
                _r("BUYSELL", "2025-07-01", -200, account="a"),
                _r("BUYSELL", "2025-07-01", -200, account="b"),
                _r("BUYSELL", "2025-08-01", 50, account="a")]
        self.assertEqual(_events(rows), [("BUY", 50.0)])

    def test_split_gains_position_start(self):
        self.assertEqual(_starts(_DUP_SPLIT), "2025-08-01")

    def test_apply_distributions_balance(self):
        self.assertEqual(_balance(_DUP_SPLIT, "2025-06-30"), 200.0)

    def test_apply_distributions_split_key_has_the_account(self):
        """Each account's own copy of the split scales its own shares."""
        rows = [_r("BUYSELL", "2025-01-02", 100, account="a"),
                _r("BUYSELL", "2025-01-02", 100, account="b"),
                _r("SPLIT", "2025-06-11", 2.0, account="a"),
                _r("SPLIT", "2025-06-11", 2.0, account="b")]
        self.assertEqual(_balance(rows, "2025-06-30"), 400.0)


class TestAssignAndTransferMoveEveryWalk(unittest.TestCase):
    """A2-1539: a put assignment (ASSIGN +shares) and a transfer-in move
    the position in each twin walk; dropping either from a walk's action
    set changes its answer."""

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


class TestKrakenUsStablecoinFeeAtPar(unittest.TestCase):
    @rule("US-CRYPTO-03")
    def test_usdc_withdrawal_fee_is_a_sale_at_par(self):
        """A2-1564: in a US book a stablecoin is property, so a USDC
        withdrawal fee is a sale of USDC valued at its 1.00 par (no
        market lookup)."""
        from test_fix_d_crypto_drip import _KR_LEDGER_H, _kraken
        text = _KR_LEDGER_H + (
            '"L1","R1","2025-05-04 16:00:00","withdrawal","","currency",'
            '"USDC","spot","-100.0000","1.0000","0"\n')
        rows = _kraken(text, "kr_ledgers", cash=False)
        fee = [r for r in rows if r["action"] == "BUYSELL"]
        self.assertEqual(len(fee), 1)
        self.assertEqual(fee[0]["symbol"], "USDC")
        self.assertEqual(fee[0]["quantity"], -1.0)
        self.assertEqual(fee[0]["price"], 1.0)
        self.assertEqual(fee[0]["net_amount"], 1.0)


class TestSumGainsSortBy(unittest.TestCase):
    """A2-1596: taxjson-sum-gains --sort-by (get_sort_value) was never run
    by the suite. Each key orders the ticker summary by its own column,
    largest first."""

    STATS = {
        "AAA.TO": {"CAD": {"cap": 10.0, "opt": 0.0, "div": 100.0,
                           "pil": 0.0, "cost": 90.0, "proceeds": 100.0,
                           "trade_count": 1, "hold_days": [400]}},
        "MMM.TO": {"CAD": {"cap": 0.0, "opt": 60.0, "div": 0.0,
                           "pil": 30.0, "cost": 40.0, "proceeds": 100.0,
                           "trade_count": 1, "hold_days": [5]}},
        "ZZZ.TO": {"CAD": {"cap": 50.0, "opt": 0.0, "div": 0.0,
                           "pil": 0.0, "cost": 50.0, "proceeds": 100.0,
                           "trade_count": 2, "hold_days": [10, 30]}},
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
        self.assertEqual(self._order("holding_days"),
                         ["AAA.TO", "ZZZ.TO", "MMM.TO"])


if __name__ == "__main__":
    unittest.main()
