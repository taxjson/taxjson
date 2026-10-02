"""Re-audit-2 fixes, income list (A2-...): distributions.map sizing and
dating, the per-account split of a blended pool, income dating of
Canadian split-share corporations and trusts, capital_gains_dividends.map
and ric_january_dividends matching.

All data is synthetic (fake account ids, invented tickers).
"""
import contextlib
import io
import unittest

from tax_rules import rule

ACCT = "55500001"  # pii-ok (synthetic)


def _buy(date, qty, sym, acct=ACCT, time="10:00:00", settle=None):
    return {"action": "BUYSELL", "date": date, "time": time,
            "date_settle": settle or date, "symbol": sym,
            "quantity": float(qty), "account": acct}


def _split(date, sym, ratio, new="", acct=ACCT, time="00:00:00"):
    return {"action": "SPLIT", "date": date, "time": time, "symbol": sym,
            "quantity": float(ratio), "symbol_new": new, "account": acct}


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


class TestBalanceOnPerSymbol(unittest.TestCase):
    """A2-0021, A2-0074, A2-0986, A2-0225: balance_on holds shares per
    (account, raw symbol) like the engine walk."""

    @rule("CA-DIST-01")
    def test_a2_0021_rename_split_does_not_scale_target_holding(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [_buy("2025-01-02", 1600, "AAA.TO"),
               _buy("2025-01-03", 50, "BBB.TO"),
               _split("2025-03-01", "AAA.TO", 0.0625, "BBB.TO")]
        self.assertAlmostEqual(balance_on(txs, "BBB.TO", "2025-12-31"), 150)
        txs = [_buy("2025-01-03", 30, "BBB.TO"),
               _split("2025-03-01", "AAA.TO", 2, "BBB.TO")]
        self.assertAlmostEqual(balance_on(txs, "BBB.TO", "2025-12-31"), 30)

    @rule("CA-DIST-01")
    def test_a2_0021_split_for_account_apportions_true_count(self):
        from taxjson.bin.taxjson_split_gains import split_for_account
        base = [_buy("2025-01-02", 1600, "AAA.TO"),
                _buy("2025-01-03", 50, "BBB.TO"),
                _split("2025-03-01", "AAA.TO", 0.0625, "BBB.TO")]
        combined = {"inventory": [{"symbol": "BBB.TO", "qty": 150.0,
                                   "total_cost": 21000.0}],
                    "transactions": [], "summary": {}}
        out = split_for_account(combined, ACCT, base)
        row = out["inventory"][0]
        self.assertAlmostEqual(row["qty"], 150.0)
        self.assertAlmostEqual(row["total_cost"], 21000.0)

    @rule("US-DIST-01")
    def test_a2_0074_old_ticker_after_rename_is_its_own_holding(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [_buy("2025-01-02", 100, "XYZ.US"),
               _split("2025-02-01", "XYZ.US", 2, "XYZN.US"),
               _buy("2025-03-01", 30, "XYZ.US")]
        self.assertAlmostEqual(balance_on(txs, "XYZ.US", "2025-12-31"), 30)
        self.assertAlmostEqual(balance_on(txs, "XYZN.US", "2025-12-31"), 200)

    @rule("US-DIST-01")
    def test_a2_0074_reused_ticker_map_rows_sized_per_holding(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        txs = [_buy("2022-01-03", 100, "FB.US"),
               _split("2022-06-09", "FB.US", 1, "META.US"),
               _buy("2025-02-03", 50, "FB.US"),
               _buy("2025-03-03", -10, "META.US")]
        doc = {"transactions": txs, "metadata": {}}
        (doc, n), _err = _quiet(
            apply_distributions, doc,
            [("META.US", "2025-06-30", 1.0), ("FB.US", "2025-06-30", 1.0)],
            ACCT, country="usa")
        adj = {(t["symbol"], t["net_amount"]) for t in doc["transactions"]
               if t["action"] == "ADJUST"}
        self.assertEqual(n, 2)
        self.assertEqual(adj, {("META.US", 90.0), ("FB.US", 50.0)})

    @rule("CA-DIST-01")
    def test_a2_0986_accounts_do_not_share_splits(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [_buy("2025-01-02", 100, "X.TO", "a"),
               _buy("2025-01-02", 100, "X.TO", "b"),
               _split("2025-02-01", "X.TO", 2, acct="a"),
               _split("2025-02-01", "X.TO", 2, acct="b")]
        self.assertAlmostEqual(balance_on(txs, "X.TO", "2025-12-31"), 400)

    @rule("CA-DIST-01")
    def test_a2_0225_split_before_same_stamp_trade(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        for order in (0, 1):
            txs = [_buy("2025-01-02", 100, "XYZ.TO"),
                   _buy("2025-03-03", 50, "XYZ.TO", time="09:30:00"),
                   _split("2025-03-03", "XYZ.TO", 2, time="09:30:00")]
            if order:
                txs[1], txs[2] = txs[2], txs[1]
            self.assertAlmostEqual(
                balance_on(txs, "XYZ.TO", "2025-03-03"), 250, msg=order)

    def test_conservation_message_names_direction(self):
        from taxjson.bin.taxjson_run import _blend_conservation_gaps
        blended = {"inventory": [{"symbol": "BBB.TO", "qty": 150}]}
        over = [{"inventory": [{"symbol": "BBB.TO", "qty": 160,
                                "blended_pool": True}]}]
        msg = _blend_conservation_gaps(blended, over)[0]
        self.assertIn("MORE than the pool", msg)
        self.assertIn("over-report", msg)
        self.assertNotIn("only", msg)
        under = [{"inventory": [{"symbol": "BBB.TO", "qty": 140,
                                 "blended_pool": True}]}]
        self.assertIn("under-report",
                      _blend_conservation_gaps(blended, under)[0])


if __name__ == "__main__":
    unittest.main()
