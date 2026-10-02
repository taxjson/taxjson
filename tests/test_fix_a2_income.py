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


def _usa_gains(rows, map_rows):
    """apply-distributions on dict rows, then the US engine."""
    import copy
    from taxjson.bin.taxjson_apply_distributions import apply_distributions
    from taxjson.lib.core import coerce_transaction_row
    from taxjson.lib.pipeline import GainsRequest, run_gains
    doc = {"transactions": copy.deepcopy(rows),
           "metadata": {"target_currency": "USD"}}
    (doc, _n), _e = _quiet(apply_distributions, doc, map_rows, ACCT,
                           country="usa")
    txs = [coerce_transaction_row(t, i, "t")
           for i, t in enumerate(doc["transactions"])]
    res, err = _quiet(run_gains, txs, [], [],
                      req=GainsRequest(country="usa", taxable=True))
    return doc, res, err


def _trade(date, qty, price, sym, settle=None, time="10:00:00"):
    return {"action": "BUYSELL", "date": date, "time": time,
            "date_settle": settle or date, "symbol": sym,
            "quantity": float(qty), "price": float(price),
            "net_amount": -float(qty) * float(price), "currency": "USD",
            "account": ACCT}


class TestMapAdjustStamp(unittest.TestCase):
    """A2-0071, A2-0988: the map ADJUST reaches the holder-of-record lots
    in a trade-date-ordered engine."""

    @rule("US-DIST-01")
    def test_a2_0071_sold_on_record_date_keeps_adjust(self):
        rows = [_trade("2025-03-03", 100, 10, "XYZ.US", "2025-03-04"),
                _trade("2025-12-29", -100, 12, "XYZ.US", "2025-12-30")]
        doc, res, err = _usa_gains(rows, [("XYZ.US", "2025-12-29", 0.5)])
        self.assertAlmostEqual(res["summary"]["total_gain"], 150.0, 2)
        self.assertNotIn("NOT applied", err)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"]
        self.assertEqual(adj[0]["date_settle"], "2025-12-29")

    @rule("US-DIST-01")
    def test_a2_0988_buy_on_record_date_gets_no_share(self):
        rows = [_trade("2025-02-03", 100, 30, "VTI.US", "2025-02-04"),
                _trade("2025-06-20", 100, 30, "VTI.US", "2025-06-23"),
                _trade("2025-07-01", -100, 45, "VTI.US", "2025-07-02")]
        _doc, res, _err = _usa_gains(rows, [("VTI.US", "2025-06-20", 0.5)])
        self.assertAlmostEqual(res["summary"]["total_gain"], 1450.0, 2)

    def test_no_straddle_keeps_record_date_stamp(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        rows = [_trade("2025-03-03", 100, 10, "XYZ.US", "2025-03-04")]
        (doc, _n), _e = _quiet(apply_distributions,
                               {"transactions": rows, "metadata": {}},
                               [("XYZ.US", "2025-12-29", 0.5)], ACCT)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"][0]
        self.assertEqual((adj["date"], adj["time"], adj["date_settle"]),
                         ("2025-12-29", "23:59:58", "2025-12-29"))


    @rule("US-DIST-01")
    def test_a2_0071_no_lots_warning_names_basis_increase(self):
        from taxjson.lib.core import coerce_transaction_row
        from taxjson.lib.pipeline import GainsRequest, run_gains
        row = {"action": "ADJUST", "date": "2025-06-30", "time": "23:59:58",
               "date_settle": "2025-06-30", "symbol": "XYZ.US",
               "quantity": 0.0, "net_amount": 50.0, "currency": "USD",
               "account": ACCT}
        _r, err = _quiet(run_gains, [coerce_transaction_row(row, 0, "t")],
                         [], [], req=GainsRequest(country="usa",
                                                  taxable=True))
        self.assertIn("basis increase", err)
        self.assertNotIn("return of capital", err)


class TestRocDoubleEntry(unittest.TestCase):
    """A2-0072 (broker ROC + map ROC), A2-0232 (map ROC whose cash is a
    DIVIDEND row)."""

    _BOOK = [
        {"action": "BUYSELL", "date": "2025-03-03", "time": "10:00:00",
         "date_settle": "2025-03-04", "symbol": "XYZ.UN.TO",
         "quantity": 1000.0, "price": 1.0, "net_amount": -1000.0,
         "currency": "CAD", "account": ACCT},
        {"action": "ADJUST", "date": "2026-01-08", "time": "00:00:00",
         "symbol": "XYZ.UN.TO", "quantity": 0.0, "net_amount": -20.0,
         "type": "roc", "record_date": "2025-12-30", "currency": "CAD",
         "account": ACCT,
         "description": "XYZ UNITS RETURN OF CAPITAL REC 12/30/25 "
                        "PAY 01/08/26"}]

    @rule("CA-DIST-01")
    def test_a2_0072_apply_distributions_warns_either_date(self):
        import copy
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        for d in ("2025-12-30", "2026-01-08"):
            doc = {"transactions": copy.deepcopy(self._BOOK),
                   "metadata": {}}
            _o, err = _quiet(apply_distributions, doc,
                             [("XYZ.UN.TO", d, -0.02)], ACCT,
                             country="canada")
            self.assertIn("reduced TWICE", err, d)

    def test_a2_0072_roc_sum_warns_across_year_end(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        for mapdate in ("2025-12-30", "2026-01-08"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "work").mkdir()
                (root / "taxjson.toml").write_text(
                    '[settings]\nyear = 2025\ncountry = "canada"\n'
                    'base_currency = "CAD"\n'
                    '[accounts.margin]\ntype = "taxable"\n')
                native = [dict(t, account="margin") for t in self._BOOK]
                (root / "work" / "margin_raw.json").write_text(
                    json.dumps({"transactions": native}))
                dist = {"action": "ADJUST", "date": mapdate,
                        "time": "23:59:58", "date_settle": mapdate,
                        "symbol": "XYZ.UN.TO", "quantity": 0.0,
                        "currency": "CAD", "net_amount": -20.0,
                        "type": "dist", "account": "margin",
                        "id": f"DIST-XYZ.UN.TO-{mapdate}-margin"}
                (root / "work" / "margin_base.json").write_text(
                    json.dumps({"transactions": native + [dist]}))
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run",
                     "-C", str(root), "roc-sum"],
                    cwd=repo, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("twice", r.stderr, mapdate)

    @rule("CA-DIST-01")
    def test_a2_0232_map_roc_with_dividend_cash_warns(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        book = [dict(self._BOOK[0], symbol="ZRE.TO"),
                {"action": "DIVIDEND", "date": "2025-06-30",
                 "time": "00:00:00", "symbol": "ZRE.TO", "quantity": 0.0,
                 "net_amount": 120.0, "currency": "CAD", "account": ACCT}]
        _o, err = _quiet(apply_distributions,
                         {"transactions": book, "metadata": {}},
                         [("ZRE.TO", "2025-06-27", -0.12)], ACCT,
                         country="canada")
        self.assertIn("still counted IN FULL as income", err)


if __name__ == "__main__":
    unittest.main()
