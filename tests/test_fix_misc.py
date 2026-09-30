"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic."""
import unittest

from taxjson.lib import tax_estimate as te


class _EstimateCase(unittest.TestCase):
    def tearDown(self):
        te.apply_vintage(2025)          # the import-time default


class TestCanadaTablesPinned(_EstimateCase):
    """G1-1: the 2025 federal/ON and 2026 ON tables, to the cent."""

    def test_2025_federal_and_ontario_tables(self):
        t = te._VINTAGES["2025"]
        inf = float("inf")
        self.assertEqual(t["CA_FED_BRACKETS"],
                         [(57375, .145), (114750, .205), (177882, .26),
                          (253414, .29), (inf, .33)])
        self.assertEqual((t["CA_FED_BPA"], t["CA_FED_BPA_MIN"]), (16129.0, 14538.0))
        on = t["CA_PROVINCES"]["ON"]
        self.assertEqual(on["brackets"], [(52886, .0505), (105775, .0915),
                                          (150000, .1116), (220000, .1216),
                                          (inf, .1316)])
        self.assertEqual((on["bpa"], on["dtc_eligible"], on["surtax"]),
                         (12747.0, .10, [(5710, .20), (7307, .36)]))
        on26 = te._VINTAGES["2026"]["CA_PROVINCES"]["ON"]
        self.assertEqual(on26["brackets"], [(53891, .0505), (107785, .0915),
                                            (150000, .1116), (220000, .1216),
                                            (inf, .1316)])
        self.assertEqual((on26["bpa"], on26["surtax"]),
                         (12989.0, [(5818, .20), (7446, .36)]))

    def test_2025_ontario_estimate_every_bracket_exact(self):
        r = te.estimate_canada(year=2025, realized=0, eligible_div=0,
                               foreign_div=0, pil=300000, other_income=0,
                               other_losses=0, province="ON")
        # Federal, by hand: 57,375 x 14.5% + 57,375 x 20.5% + 63,132 x 26%
        # + 75,532 x 29% + 46,586 x 33% = 73,773.23, less the phased-down
        # BPA 14,538 x 14.5% = 2,108.01.
        fed = (57375 * .145 + 57375 * .205 + 63132 * .26 + 75532 * .29
               + 46586 * .33 - 14538 * .145)
        self.assertEqual(r["tax_with"]["federal"], round(fed, 2))
        self.assertEqual(r["tax_with"]["federal"], 71665.22)
        # Ontario: brackets less BPA 12,747 x 5.05%, surtax 20% over
        # 5,710 + 36% over 7,307, health premium 900.
        basic = (52886 * .0505 + 52889 * .0915 + 44225 * .1116
                 + 70000 * .1216 + 80000 * .1316 - 12747 * .0505)
        prov = basic + .20 * (basic - 5710) + .36 * (basic - 7307) + 900
        self.assertEqual(r["tax_with"]["provincial"], round(prov, 2))
        self.assertEqual(r["tax_with"]["provincial"], 45240.80)
        self.assertEqual(r["estimated_tax"], 116906.02)

    def test_2025_ontario_eligible_dividend_credits_exact(self):
        r = te.estimate_canada(year=2025, realized=0, eligible_div=50000,
                               foreign_div=0, pil=0, other_income=60000,
                               other_losses=0, province="ON")
        self.assertEqual(r["grossed_eligible"], 69000.0)
        self.assertAlmostEqual(r["trace_with"]["fed_dtc"], 69000 * .150198, places=6)
        self.assertAlmostEqual(r["trace_with"]["prov_dtc"], 69000 * .10, places=6)
        self.assertEqual(r["tax_with"], {"federal": 11083.88, "provincial": 4832.39,
                                         "total": 15916.27})
        self.assertEqual(r["tax_base"], {"federal": 6518.8, "provincial": 3277.95,
                                         "total": 9796.75})
        self.assertEqual(r["estimated_tax"], 6119.52)

    def test_2026_ontario_upper_bands_exact(self):
        r = te.estimate_canada(year=2026, realized=0, eligible_div=0,
                               foreign_div=0, pil=300000, other_income=0,
                               other_losses=0, province="ON")
        self.assertEqual(r["tax_with"], {"federal": 70899.99, "provincial": 45022.79,
                                         "total": 115922.78})
        self.assertEqual(r["estimated_tax"], 115922.78)


class TestCanadaAmtPinned(_EstimateCase):
    """G1-5: a binding AMT with every ATI term, to the cent."""

    def test_binding_amt_exact(self):
        r = te.estimate_canada(year=2026, realized=600000, eligible_div=0,
                               foreign_div=20000, pil=10000, staking=5000,
                               other_income=0, other_losses=40000,
                               province="ON")
        a = r["amt"]
        # ATI = gains 600,000 - 50% x 40,000 losses + foreign 20,000
        #       + PIL 10,000 + staking 5,000.
        self.assertEqual(a["adjusted_income"], 615000.0)
        # (615,000 - 181,440) x 20.5% - BPA 14,829 x 14% x 50% - FTC 3,000
        fed_min = (615000 - 181440) * .205 - 14829 * .14 * .5 - 20000 * .15
        self.assertEqual(a["minimum_fed"], round(fed_min, 2))
        self.assertEqual(a["minimum_fed"], 84841.77)
        self.assertEqual(a["bpa_credit"], 1038.03)
        self.assertEqual(a["regular_fed"], 72849.99)
        self.assertEqual(a["excess_fed"], 11991.78)
        self.assertEqual(a["provincial_amt_basic"], 2953.58)
        self.assertEqual(a["provincial_amt_surtax"], 1654.0)
        self.assertEqual(a["topup"], 16599.36)
        self.assertTrue(a["binding"])
        self.assertEqual(r["taxable_gain"], 280000.0)
        self.assertEqual(r["investment_income"], 635000.0)
        self.assertEqual(r["estimated_tax"], 120952.22)
        self.assertEqual(r["estimated_tax_with_amt"], 137551.58)


class TestFillCryptoAmountsPinned(unittest.TestCase):
    """G1-6: fill-crypto's valuation, row by row, from a seeded cache."""

    def test_price_times_quantity_and_stablecoins(self):
        import contextlib
        import io
        import json
        import sys
        import tempfile
        from pathlib import Path
        import taxjson.bin.fill_crypto_prices as fc
        rows = [
            # 0: staking reward, no price: FMV x qty, relabelled USD.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "SOL",
             "quantity": 1.5, "price": 0.0, "net_amount": 0.0, "currency": "CAD"},
            # 1: a sell with no price: FMV x |qty|.
            {"action": "BUYSELL", "date": "2026-03-02", "symbol": "SOL",
             "quantity": -2.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 2: stablecoin reward: 1.0 a unit.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "USDC",
             "quantity": 5.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 3: a reward already folded to USD: 1.0 a unit.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "USD",
             "quantity": 3.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 4: broker total, no price: the price is derived, total kept.
            {"action": "BUYSELL", "date": "2026-03-02", "symbol": "ETH",
             "quantity": 3.0, "price": 0.0, "net_amount": 150.0, "currency": "CAD"},
            # 5: a USD cash BUYSELL is not a reward: untouched.
            {"action": "BUYSELL", "date": "2026-03-02", "symbol": "USD",
             "quantity": 4.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 6: the lookup fails: left at 0 in its own currency, reported.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "ADA",
             "quantity": 2.0, "price": 0.0, "net_amount": 0.0, "currency": "CAD"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.json"
            inp.write_text(json.dumps({"transactions": rows}))
            cache = Path(tmp) / "cache.json"
            cache.write_text(json.dumps({"SOL-2026-03-02": 140.25}))

            def no_fetch(sym, date):
                if sym == "ADA":
                    return 0.0              # a failed lookup
                raise AssertionError(f"unexpected price fetch {sym} {date}")
            saved = (fc.CACHE_FILE, fc.get_crypto_price, sys.argv, fc.time.sleep)
            fc.CACHE_FILE = str(cache)
            fc.get_crypto_price = no_fetch
            fc.time.sleep = lambda s: None
            sys.argv = ["fill-crypto", str(inp), "--project-root", tmp]
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    fc.main()
            finally:
                fc.CACHE_FILE, fc.get_crypto_price, sys.argv, fc.time.sleep = saved
        got = json.loads(out.getvalue())["transactions"]
        pick = [(t["symbol"], t["price"], t["net_amount"], t["currency"]) for t in got]
        self.assertEqual(pick, [
            ("SOL", 140.25, 210.375, "USD"),
            ("SOL", 140.25, 280.5, "USD"),
            ("USDC", 1.0, 5.0, "USD"),
            ("USD", 1.0, 3.0, "USD"),
            ("ETH", 50.0, 150.0, "CAD"),
            ("USD", 0.0, 0.0, "USD"),
            ("ADA", 0.0, 0.0, "CAD"),
        ])
        self.assertIn("1 row(s) left UNPRICED", err.getvalue())
        self.assertEqual(got[0]["gross_amount"], 210.375)


class TestCaPriorityLadderPinned(unittest.TestCase):
    """G1-7: every rung of the Canada same-timestamp ladder, through the
    function and through the ca_main sort."""

    def _tx(self, action, qty, symbol="ZZQ.US"):
        from taxjson.lib.core import TaxTransaction
        return TaxTransaction(action=action, date="2025-06-10", time="10:00:00",
                              quantity=qty, symbol=symbol, currency="CAD")

    def test_each_rung(self):
        from taxjson.lib.corporate_timeline import CaPriority as P, _ca_priority
        opt = "ZZQ250620C00010000.US"
        cases = [
            (self._tx("OPENING_BALANCE", 100), P.OPENING_BALANCE),
            (self._tx("DISALLOW", 0), P.DISALLOW),
            (self._tx("ASSIGN", -1, opt), P.ASSIGN_OPTION),
            (self._tx("ASSIGN", 100), P.ASSIGN_STOCK_OR_SPLIT),
            (self._tx("SPLIT", 2.0), P.ASSIGN_STOCK_OR_SPLIT),
            (self._tx("ADJUST", 5), P.ADJUST),
            (self._tx("BUYSELL", 100), P.BUY),
            (self._tx("BUYSELL", 0.5), P.BUY),          # a fractional buy
            (self._tx("BUYSELL", 1, opt), P.BUY),        # an option buy, not an ASSIGN
            (self._tx("BUYSELL", -100), P.SELL),
            (self._tx("BUYSELL", 0), P.OTHER),
        ]
        for t, want in cases:
            self.assertEqual(_ca_priority(t), want, (t.action, t.quantity, t.symbol))

    def test_same_timestamp_sort_order(self):
        from taxjson.lib.corporate_timeline import event_sort_key
        opt = "ZZQ250620C00010000.US"
        rows = [self._tx("BUYSELL", 0), self._tx("ADJUST", 5),
                self._tx("BUYSELL", -100), self._tx("BUYSELL", 100),
                self._tx("ASSIGN", 100), self._tx("ASSIGN", -1, opt),
                self._tx("DISALLOW", 0), self._tx("OPENING_BALANCE", 100)]
        got = [(t.action, t.quantity) for t in
               sorted(rows, key=lambda t: event_sort_key(t, profile="ca_main"))]
        self.assertEqual(got, [("OPENING_BALANCE", 100), ("DISALLOW", 0),
                               ("ASSIGN", -1), ("ASSIGN", 100),
                               ("BUYSELL", 100), ("BUYSELL", -100),
                               ("ADJUST", 5), ("BUYSELL", 0)])


if __name__ == "__main__":
    unittest.main()
