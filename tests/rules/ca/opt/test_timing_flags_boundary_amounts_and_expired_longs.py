"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
import _paths


class TestOptionTimingFlagsPinned(unittest.TestCase):
    """G1-8: the grant-timing transition year reaches the engine flags.

    Formerly: test_fix_misc.TestOptionTimingFlagsPinned"""

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
    """G1-9: premium, paid and the net amounts in the advice, to the cent.

    Formerly: test_fix_misc.TestOptionBoundaryAmountsPinned"""

    OPT = "ZZQ250620C00010000.TO"
    OPT2 = "ZZR250321P00020000.TO"

    def _book(self):
        from taxjson.model.transaction import TaxTransaction as TT

        def T(**kw):
            return TT(**{"action": "BUYSELL", "currency": "CAD", "account": "margin", **kw})
        return [
            # Long 1, then sell 5: 1 closes the long, 4 are written for
            # 4/5 of the 1,500.00 net = 1,200.00 (lot A, 300.00 a unit).  # pii-ok
            T(date="2024-11-01", date_settle="2024-11-04", symbol=self.OPT, quantity=1, price=2, net_amount=201.0),
            T(date="2024-12-02", date_settle="2024-12-03", symbol=self.OPT, quantity=-5, price=3, net_amount=1500.0),
            # Lot B: 1 more for 310.00.
            T(date="2024-12-05", date_settle="2024-12-06", symbol=self.OPT, quantity=-1, price=3.1, net_amount=310.0),
            # 2025: buy 3 back for 361.20 (all from lot A), 2 expire (A, B).
            T(date="2025-02-10", date_settle="2025-02-11", symbol=self.OPT, quantity=3, price=1.2, net_amount=361.2),
            T(date="2025-06-20", date_settle="2025-06-20", symbol=self.OPT, quantity=2, price=0, net_amount=0.0),
            # Written 2024, never closed although it expired 2025-03-21.
            T(date="2024-12-10", date_settle="2024-12-11", symbol=self.OPT2, quantity=-2, price=1.5, net_amount=298.0),
        ]

    @rule(R.CA_OPT_03, R.CA_OPT_05, R.CA_OPT_07)
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

    @rule(R.CA_OPT_01, R.CA_OPT_03)
    def test_grant_timing_rows(self):
        from datetime import date
        from taxjson.lib.option_boundary import straddling
        rows = straddling(self._book(), 2025, "grant", 2024, today=date(2026, 9, 29))
        by = {r["close_kind"]: r for r in rows}
        self.assertIn("premium 900.00 in 2024; buy-back loss 361.20 in 2025", by["buy-back"]["where"])
        self.assertIn("premium 300.00 recognised in 2024; nothing in 2025", rows[1]["where"])


class TestExpiredOpenOptionWarns(unittest.TestCase):
    """R1-37: a long option held past expiry with no expiry row is named.

    Formerly: test_fix_misc.TestExpiredOpenOptionWarns"""

    def _run(self, book):
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = _paths.REPO
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

    @rule(R.CA_OPT_04)
    def test_missing_expiry_row_is_named(self):
        err, summ = self._run(self.BUY)
        for txt in (err, summ):
            self.assertIn("ZZQ251219C00015000.TO expired 2025-12-19", txt)
            self.assertIn("201.00 paid is a loss of 2025", txt)

    @rule(R.CA_OPT_04)
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
