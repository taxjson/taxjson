"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic."""
import unittest

from taxjson.lib import tax_estimate as te
from tax_rules import rule


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

    @rule("CA-RPT-03")
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
            (self._tx("BUYSELL", 100), P.TRADE),
            (self._tx("BUYSELL", 0.5), P.TRADE),        # a fractional buy
            (self._tx("BUYSELL", 1, opt), P.TRADE),      # an option buy, not an ASSIGN
            (self._tx("BUYSELL", -100), P.TRADE),        # one rung (CA-DATE-14)
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
                               # tied trades: input order (CA-DATE-14)
                               ("BUYSELL", -100), ("BUYSELL", 100),
                               ("ADJUST", 5), ("BUYSELL", 0)])


class TestOptionTimingFlagsPinned(unittest.TestCase):
    """G1-8: the grant-timing transition year reaches the engine flags."""

    def test_flags(self):
        from taxjson.lib.pipeline import option_timing_flags as f
        self.assertEqual(f({"year": 2026, "option_grant_timing_since": 2025,
                            "country": "canada"}),
                         ["--option-premium-timing", "grant",
                          "--option-grant-since", "2025"])
        # Default: since = the project year.
        self.assertEqual(f({"year": 2026, "country": "canada"}),
                         ["--option-premium-timing", "grant",
                          "--option-grant-since", "2026"])
        self.assertEqual(f({"option_premium_timing": "close", "country": "canada"}),
                         ["--option-premium-timing", "close"])
        self.assertEqual(f({"year": 2025, "option_buyback_loss_superficial": True,
                            "country": "canada"}),
                         ["--option-premium-timing", "grant",
                          "--option-grant-since", "2025", "--option-buyback-wash"])
        self.assertEqual(f({"year": 2025, "country": "us"}), [])


class TestOptionBoundaryAmountsPinned(unittest.TestCase):
    """G1-9: premium, paid and the net amounts in the advice, to the cent."""

    OPT = "ZZQ250620C00010000.TO"
    OPT2 = "ZZR250321P00020000.TO"

    def _book(self):
        from taxjson.lib.core import TaxTransaction as TT

        def T(**kw):
            return TT(**{"action": "BUYSELL", "currency": "CAD", "account": "margin", **kw})
        return [
            # Long 1, then sell 5: 1 closes the long, 4 are written for
            # 4/5 of the 1,500.00 net = 1,200.00 (lot A, 300.00 a unit).
            T(date="2024-11-01", date_settle="2024-11-04", symbol=self.OPT, quantity=1, price=2, net_amount=201.0),
            T(date="2024-12-02", date_settle="2024-12-03", symbol=self.OPT, quantity=-5, price=3, net_amount=1500.0),
            # Lot B: 1 more for 310.00.
            T(date="2024-12-05", date_settle="2024-12-06", symbol=self.OPT, quantity=-1, price=3.1, net_amount=310.0),
            # 2025: buy 3 back for 361.20 (all from lot A), 2 expire (A, B).
            T(date="2025-02-03", date_settle="2025-02-04", symbol=self.OPT, quantity=3, price=1.2, net_amount=361.2),
            T(date="2025-06-20", date_settle="2025-06-20", symbol=self.OPT, quantity=2, price=0, net_amount=0.0),
            # Written 2024, never closed although it expired 2025-03-21.
            T(date="2024-12-10", date_settle="2024-12-11", symbol=self.OPT2, quantity=-2, price=1.5, net_amount=298.0),
        ]

    @rule("CA-OPT-03", "CA-OPT-05", "CA-OPT-07")
    def test_close_timing_rows(self):
        from datetime import date
        from taxjson.lib.option_boundary import straddling
        rows = straddling(self._book(), 2025, "close", None, today=date(2026, 9, 29))
        got = [(r["symbol"], r["close_kind"], r["units"], r["premium"], r["paid"], r["close_year"])
               for r in rows]
        self.assertEqual(got, [
            (self.OPT, "buy-back", 3.0, 900.0, 361.2, 2025),
            (self.OPT, "expiry", 1.0, 300.0, 0.0, 2025),
            (self.OPT, "expiry", 1.0, 310.0, 0.0, 2025),
            (self.OPT2, "expired?", 2.0, 298.0, 0.0, None),
        ])
        self.assertEqual([r["written"] for r in rows],
                         ["2024-12-03", "2024-12-03", "2024-12-06", "2024-12-11"])
        by = {r["close_kind"]: r for r in rows}
        self.assertIn("net 538.80 in 2025", by["buy-back"]["where"])
        self.assertIn("the Act puts +900.00 in 2024 and -361.20 in 2025", by["buy-back"]["action"])
        self.assertIn("premium 300.00 recognised in 2025", rows[1]["where"])
        self.assertIn("premium 310.00 recognised in 2025", rows[2]["where"])
        self.assertTrue(by["expired?"]["attention"])
        self.assertIn("298.00 premium", by["expired?"]["action"])
        self.assertEqual([r["attention"] for r in rows], [False, False, False, True])

    @rule("CA-OPT-01", "CA-OPT-03")
    def test_grant_timing_rows(self):
        from datetime import date
        from taxjson.lib.option_boundary import straddling
        rows = straddling(self._book(), 2025, "grant", 2024, today=date(2026, 9, 29))
        by = {r["close_kind"]: r for r in rows}
        self.assertIn("premium 900.00 in 2024; buy-back loss 361.20 in 2025", by["buy-back"]["where"])
        self.assertIn("premium 300.00 recognised in 2024; nothing in 2025", rows[1]["where"])


class TestFxCashSkipsShelteredAccounts(unittest.TestCase):
    """G1-2: a sheltered account's USD round trip stays out of s.39."""

    def test_sheltered_usd_is_ignored(self):
        import json
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_run import _fx_cash_doc

        def tx(date, net, qty, account):
            return dict(action="BUYSELL", date=date, date_settle=date, time="10:00:00",
                        symbol="AAA.US", quantity=qty, currency="USD",
                        net_amount=net, account=account)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n'
                '[accounts.tfsa]\ntype = "sheltered"\n')
            (work / "margin_raw.json").write_text(json.dumps({"transactions": [
                tx("2026-01-10", 10000.0, -100, "margin"),
                tx("2026-02-10", 10000.0, 50, "margin")]}))
            # The same round trip, bigger, in each registered account.
            for acct in ("rrsp", "tfsa"):
                (work / f"{acct}_raw.json").write_text(json.dumps({"transactions": [
                    tx("2026-01-10", 50000.0, -500, acct),
                    tx("2026-02-10", 50000.0, 250, acct)]}))
            (work / "to_base.csv").write_text(
                "2026-01-10 12:00:00 USD CAD 1.30\n"
                "2026-02-10 12:00:00 USD CAD 1.40\n")
            ledger, verdict, base, year, country = _fx_cash_doc(root, work)
        self.assertAlmostEqual(ledger["net_gain"], 1000.0, places=2)
        self.assertAlmostEqual(ledger["per_currency"]["USD"]["acquired"], 10000.0, places=2)
        self.assertAlmostEqual(verdict["reportable"], 800.0, places=2)
        self.assertEqual({e["account"] for e in ledger["events"]}, {"margin"})


_ESTIMATE_BOOK = """\
BUYSELL 2025-01-15 09:30:00 XEI.TO 100.00000000 CAD 10.00000000 1000.00000 0.00000
BUYSELL 2025-06-20 10:15:00 XEI.TO -100.00000000 CAD 15.00000000 1500.00000 0.00000
BUYSELL 2025-02-03 10:00:00 XEI250321C00016000.TO -2.00000000 CAD 1.50000000 300.00000 0.00000
BUYSELL 2025-02-20 10:00:00 XEI250321C00016000.TO 2.00000000 CAD 0.40000000 80.00000 0.00000
DIVIDEND 2025-04-01 09:30:00 XEI.TO 0.00000000 CAD 0.00000000 120.00000
DIVIDEND 2025-04-02 09:30:00 ZZQ.US 0.00000000 CAD 0.00000000 200.00000
DIVIDEND_IN_LIEU 2025-05-01 09:30:00 XEI.TO 0.00000000 CAD 0.00000000 70.00000
"""


class TestEstimateInputsEndToEnd(unittest.TestCase):
    """G1-4: `taxjson estimate` gathers stock + option gains, Canadian and
    foreign dividends and PIL from the books, and taxes them exactly."""

    def test_estimate_from_a_run(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ, TAXJSON_OFFLINE="1",
                   PYTHONPATH=str(repo / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "book.tt").write_text(_ESTIMATE_BOOK)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\nprovince = "ON"\n'
                '[accounts.margin]\ntype = "taxable"\n')

            def cli(*args):
                return subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root), *args],
                    capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            r = cli("run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = cli("estimate", "--json", "--other-income", "150000")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = json.loads(r.stdout)
            # R1-44: the Schedule 3 outputs say slip capital gains
            # (lines 17400/17600) are not in their rows.
            sum_txt = cli("sum").stdout
            fe_txt = cli("form-export").stdout
        for txt in (sum_txt, fe_txt):
            self.assertIn("17600", txt)
            self.assertIn("17400", txt)
        self.assertEqual(doc["totals"]["stock"], 500.0)
        self.assertEqual(doc["totals"]["option"], 220.0)
        self.assertEqual(doc["totals"]["pil"], 70.0)
        e = doc["estimate"]
        # realized 720 (500 stock + 220 option) + 120 CA + 200 foreign + 70 PIL
        self.assertEqual(e["investment_income"], 1110.0)
        self.assertEqual(e["taxable_gain"], 360.0)
        self.assertEqual(e["grossed_eligible"], 165.6)
        self.assertEqual(e["ftc_assumed"], 30.0)
        self.assertEqual(e["tax_with"], {"federal": 27059.53, "provincial": 15522.76,
                                         "total": 42582.29})
        self.assertEqual(e["tax_base"], {"federal": 26907.54, "provincial": 15388.4,
                                         "total": 42295.95})
        self.assertEqual(e["estimated_tax"], 286.35)


class TestT1135SuperficialLossAddition(unittest.TestCase):
    """G7-0: the T1135 cost amount includes the s.53(1)(f) addition the
    engine makes to the replacement property's ACB."""

    def _report(self, rows, extra_rows=()):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_t1135 import build_report
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": rows}))
            g = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains", "--country", "canada",
                 "--year", "2025", "--taxable", str(base)],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=dict(os.environ, PYTHONPATH=str(repo / "src")))
            self.assertEqual(g.returncode, 0, g.stderr[-2000:])
            gains = Path(tmp) / "margin_gains.json"
            gains.write_text(g.stdout)
            doc = json.loads(g.stdout)
            rep = build_report([base], [gains], 2025, {}, "CAD")
        return rep, doc

    @staticmethod
    def _t(i, d, sym, q, p):
        return dict(id=i, date=d, date_settle=d, time="10:00:00", action="BUYSELL",
                    symbol=sym, quantity=q, price=p, gross_amount=abs(q * p),
                    net_amount=abs(q * p), commission=0.0, fee=0.0, currency="CAD",
                    account="margin")

    def test_denied_loss_raises_the_replacement_cost(self):
        rows = [self._t("a1", "2024-06-03", "XYZ.US", 100, 10),
                self._t("a2", "2025-03-03", "XYZ.US", -100, 5),
                self._t("a3", "2025-03-10", "XYZ.US", 100, 5)]
        rep, doc = self._report(rows)
        self.assertEqual(doc["summary"]["total_disallowed"], 500.0)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 1000.0)            # 500 paid + 500 denied
        self.assertEqual(p["max_cost"], 1000.0)
        self.assertEqual(rep["max_total_cost"], 1000.0)

    def test_partial_sale_after_the_addition(self):
        rows = [self._t("a1", "2024-06-03", "XYZ.US", 100, 10),
                self._t("a2", "2025-03-03", "XYZ.US", -100, 5),
                self._t("a3", "2025-03-10", "XYZ.US", 100, 5),
                self._t("a4", "2025-09-10", "XYZ.US", -40, 20)]
        rep, _doc = self._report(rows)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 600.0)             # 60 of 100 at 10.00

    def test_replacement_bought_before_the_losing_sale(self):
        rows = [self._t("a1", "2024-06-03", "XYZ.US", 100, 10),
                self._t("a2", "2025-03-03", "XYZ.US", 100, 5),     # replacement first
                self._t("a3", "2025-03-10", "XYZ.US", -100, 5)]    # then the loss
        rep, doc = self._report(rows)
        denied = doc["summary"]["total_disallowed"]
        self.assertGreater(denied, 0)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        inv = {i["symbol"]: i for i in doc["inventory"]}
        self.assertEqual(p["year_end_cost"], round(inv["XYZ.US"]["total_cost"], 2))


class TestToBaseOptionCollision(unittest.TestCase):
    """R1-16: TOBASE's root rename must not pool a US-listed option into a
    Montreal contract with the same code."""

    BOOK = (
        "BUYSELL 2025-02-03 10:00:00 RY270115C00100000.TO 1.00000000 CAD 3.00000000 300.00000 0.00000\n"
        "BUYSELL 2025-02-04 10:00:00 RY270115C00100000.US 1.00000000 CAD 1.00000000 100.00000 0.00000\n"
        "BUYSELL 2025-03-03 10:00:00 RY270115C00100000.TO -1.00000000 CAD 2.50000000 250.00000 0.00000\n"
        "BUYSELL 2025-02-05 10:00:00 KGC270115C00012000.US 1.00000000 CAD 1.20000000 120.00000 0.00000\n")

    def test_us_option_kept_separate_and_named(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(repo / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "book.tt").write_text(self.BOOK)
            (root / "ticker.map").write_text("TOBASE RY.US RY.TO\nTOBASE KGC.US K.TO\n")
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')

            def cli(*args):
                return subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root), *args],
                    capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            r = cli("run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            # merge2's warnings reach the .sum DIAGNOSTICS section.
            run_err = (root / "reports" / "margin.sum").read_text()
            base = json.loads((root / "work" / "margin_base.json").read_text())
            gains = json.loads((root / "work" / "margin_gains.json").read_text())
        syms = {t["symbol"] for t in base["transactions"]}
        self.assertIn("RY270115C00100000.US", syms)             # not pooled into .TO
        self.assertIn("K270115C00012000.TO", syms)              # no collision: renamed as before
        (d,) = [t for t in gains["transactions"] if "gain" in t]
        self.assertEqual((d["symbol"], round(d["gain"], 2)), ("RY270115C00100000.TO", -50.0))
        self.assertIn("RY270115C00100000.US", run_err)
        self.assertIn("kept separate", run_err)


class TestNonNorthAmericanSettlement(unittest.TestCase):
    """G5-0: LSE (GBP) and ASX (AUD) shares settle T+2, not the US T+1."""

    def test_gbp_and_aud_settle_t2(self):
        from taxjson.lib.brokerages.ib_extractor import get_ib_settlement
        from taxjson.lib.dates import settlement_lag_days
        self.assertEqual(get_ib_settlement("2025-03-28", "Stocks", "GBP"), "2025-04-01")
        self.assertEqual(get_ib_settlement("2026-05-14", "Stocks", "AUD"), "2026-05-18")
        # A Dec-30 sale settles in the next tax year.
        self.assertEqual(get_ib_settlement("2026-12-30", "Stocks", "GBP")[:4], "2027")
        self.assertEqual(get_ib_settlement("2026-12-30", "Stocks", "AUD")[:4], "2027")
        # North America unchanged; the UK/EU move to T+1 on 2027-10-11.
        self.assertEqual(get_ib_settlement("2025-03-28", "Stocks", "USD"), "2025-03-31")
        self.assertEqual(get_ib_settlement("2025-03-28", "Stocks", "CAD"), "2025-03-31")
        self.assertEqual(settlement_lag_days("2027-10-08", "GBP"), 2)
        self.assertEqual(settlement_lag_days("2027-10-11", "GBP"), 1)
        self.assertEqual(settlement_lag_days("2027-10-11", "EUR"), 1)
        self.assertEqual(settlement_lag_days("2027-10-11", "AUD"), 2)


class TestZeroValueSpinoffInChecklist(unittest.TestCase):
    """R1-11: a taxable spin-off booked at $0 keeps the elections step open
    (not run-clean: that step gates close-year and the report banners)."""

    def test_elections_step_needs_attention(self):
        import tempfile
        from pathlib import Path
        from taxjson.lib import checklist as cl
        from test_fix_filing_a import _cl_ctx, _cl_project
        table = {"elect": (0, "No pending elections.\n", "")}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            ctx = _cl_ctx(root, cfg, table)
            self.assertEqual(cl.d_elections(ctx).status, "done")
            (root / "work" / "margin_corp_spinoff_value.diag").write_text(
                "warning: margin: spin-off ZZQ.US on 2025-04-04 (event e1) is booked at $0\n")
            r = cl.d_elections(_cl_ctx(root, cfg, table))
            clean = cl.d_run_clean(_cl_ctx(root, cfg, table))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("booked at $0", r.detail)
        self.assertIn("fmv_per_share", r.detail)
        self.assertNotIn("booked at $0", clean.detail)


class TestExpiredOpenOptionWarns(unittest.TestCase):
    """R1-37: a long option held past expiry with no expiry row is named."""

    def _run(self, book):
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(repo / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "book.tt").write_text(book)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
                 "run", "--no-input"],
                capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return r.stderr, (root / "reports" / "margin.sum").read_text()

    BUY = ("BUYSELL 2025-02-03 10:00:00 ZZQ251219C00015000.TO 1.00000000 CAD "
           "2.01000000 201.00000 0.00000\n")

    @rule("CA-OPT-04")
    def test_missing_expiry_row_is_named(self):
        err, summ = self._run(self.BUY)
        for txt in (err, summ):
            self.assertIn("ZZQ251219C00015000.TO expired 2025-12-19", txt)
            self.assertIn("201.00 paid is a loss of 2025", txt)

    @rule("CA-OPT-04")
    def test_dec_31_expiry_is_the_years(self):
        # re-audit A2-0716: an expiry ON the year end was skipped.
        err, summ = self._run(
            "BUYSELL 2025-02-03 10:00:00 ZZQ251231C00015000.TO 1.00000000 "
            "CAD 2.01000000 201.00000 0.00000\n")
        self.assertIn("ZZQ251231C00015000.TO expired 2025-12-31", err)
        self.assertIn("201.00 paid is a loss of 2025", err)

    def test_expiry_row_present_is_quiet(self):
        err, summ = self._run(self.BUY + (
            "BUYSELL 2025-12-19 16:00:00 ZZQ251219C00015000.TO -1.00000000 CAD "
            "0.00000000 0.00000 0.00000\n"))
        self.assertNotIn("expired 2025-12-19", err + summ)


if __name__ == "__main__":
    unittest.main()
