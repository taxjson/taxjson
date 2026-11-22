"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import tax_forms
from taxjson.rules import ids as R
from _support.filing_bin_reports import _g


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# t1135
# ---------------------------------------------------------------------------

from unittest import mock  # noqa: E402

from tax_rules import rule, rule  # noqa: E402


# ---------------------------------------------------------------------------
# form-export, sum FOR THE RETURN, stand-alone layout checks
# ---------------------------------------------------------------------------

from taxjson.bin import taxjson_form_export as FE  # noqa: E402


def _row(rep, symbol):
    return next(r for r in rep["rows"] if r["symbol"] == symbol)


class TestSchedule3Units(unittest.TestCase):
    """S003-04 / S033-02: a written option under grant timing counted its
    write AND its buy-back (5 contracts showed 10 units).

    Formerly: test_fix_l_filing_b.TestSchedule3Units"""

    @rule(R.CA_OPT_01)
    def test_grant_write_and_buyback_count_once(self):
        from taxjson.engine.by_country import get_tax_rules
        from taxjson.model.transaction import TaxTransaction
        sym = "QQQ250620C00050000.TO"
        rows = [TaxTransaction(action="BUYSELL", date=d, date_settle=s,
                               symbol=sym, quantity=q, net_amount=n,
                               currency="CAD", account="margin", time=t)
                for d, s, q, n, t in (
                    ("2025-02-03", "2025-02-04", -5.0, 649.0, "10:00:00"),
                    ("2025-04-01", "2025-04-02", 5.0, -161.0, "10:00:00"))]
        for timing in ("grant", "close"):
            with self.subTest(timing=timing):
                res = get_tax_rules("canada").compute_gains(
                    rows, option_premium_timing=timing)
                rep = tax_forms.build_schedule3(res["transactions"], 2025)
                r = _row(rep, sym)
                self.assertEqual(r["units"], 5.0)
                self.assertAlmostEqual(r["gain"], 488.0, places=2)

    def test_open_write_counts_at_the_write(self):
        rep = tax_forms.build_schedule3([
            _g(symbol="XYZ250620P00040000.TO", qty=-4.0, proceeds=0.0,
               cost=-400.0, gain=400.0, direction="SHORT", grant=True)],
            2025)
        self.assertEqual(rep["rows"][0]["units"], 4.0)

    def test_crypto_units_keep_full_precision(self):
        # S032-21: 0.00003 BTC showed 0 units beside nonzero proceeds.
        rep = tax_forms.build_schedule3(tax_forms.mark_crypto([
            _g(symbol="BTC", qty=-0.00003, proceeds=2.70, cost=2.00),
            _g(symbol="ETH", qty=-0.34325885, proceeds=1000.0,
               cost=900.0)]), 2025)
        self.assertEqual(_row(rep, "BTC")["units"], 0.00003)
        self.assertEqual(_row(rep, "ETH")["units"], 0.34325885)
        text = " ".join(FE.render_schedule3(rep, 2025, "CAD").split())
        self.assertIn("0.00003 BTC", text)
        self.assertIn("0.34325885 ETH", text)


class TestSchedule3Amounts(unittest.TestCase):
    """Formerly: test_fix_l_filing_b.TestSchedule3Amounts"""
    def test_net_debit_write_shows_no_proceeds(self):
        # S032-19: a put written for a net debit showed proceeds 0.35
        # and an invented ACB of 0.70.
        rep = tax_forms.build_schedule3([
            _g(symbol="ZZ250620P00010000.US", qty=-1.0, proceeds=-0.0,
               cost=0.3459, gain=-0.3459, direction="SHORT")], 2025)
        r = rep["rows"][0]
        self.assertEqual((r["proceeds"], r["acb"], r["outlays"], r["gain"]),
                         (0.0, 0.0, 0.35, -0.35))
        # A credit write of the same size: proceeds 0.35, ACB 0.
        rep = tax_forms.build_schedule3([
            _g(symbol="ZZ250620P00010000.US", qty=-1.0, proceeds=-0.0,
               cost=-0.3459, gain=0.3459, direction="SHORT")], 2025)
        r = rep["rows"][0]
        self.assertEqual((r["proceeds"], r["acb"], r["gain"]),
                         (0.35, 0.0, 0.35))

    @rule(R.CA_ACB_02)
    def test_commission_and_fee_are_both_outlays(self):
        # S032-18: the outlays accumulator was unpinned.
        ents = [_g(proceeds=8993.0, cost=7000.0, commission=5.0, fee=2.0)]
        rep = tax_forms.build_schedule3(ents, 2025)
        r = rep["rows"][0]
        self.assertEqual(r["proceeds"], 9000.0)
        self.assertEqual(r["outlays"], 7.0)
        self.assertEqual(r["acb"], 7000.0)
        self.assertEqual(r["gain"], 1993.0)
        tot = FE.filing_totals(ents, 2025)
        self.assertEqual((tot["proceeds"], tot["outlays"], tot["acb"]),
                         (9000.0, 7.0, 7000.0))
        self.assertEqual(rep["lines"][0]["proceeds"], 9000.0)

    @rule(R.CA_SL_09)
    def test_denials_sum_and_split_notes(self):
        # S032-20 / R1-312 / S033-03.
        rep = tax_forms.build_schedule3([
            _g(proceeds=500.0, cost=600.0, gain=0.0, raw_gain=-100.0,
               disallowed_amount=100.0),
            _g(proceeds=500.0, cost=600.0, gain=0.0, raw_gain=-100.0,
               disallowed_amount=100.0, date="2025-06-02"),
            _g(symbol="MIX.TO", proceeds=300.0, cost=1000.0, gain=0.0,
               raw_gain=-700.0, disallowed_amount=700.0,
               permanently_disallowed=300.0),
            _g(symbol="PERM.TO", proceeds=300.0, cost=500.0, gain=0.0,
               raw_gain=-200.0, disallowed_amount=200.0,
               permanently_disallowed=200.0)], 2025)
        r = _row(rep, "AAA.TO")
        self.assertEqual(r["denied"], 200.0)
        self.assertEqual(r["dispositions"], 2)
        self.assertIn("superficial loss 200.00 denied", r["notes"])
        m = _row(rep, "MIX.TO")["notes"]
        self.assertIn("superficial loss 400.00 denied", m)
        self.assertIn("superficial loss 300.00 PERMANENTLY denied", m)
        p = _row(rep, "PERM.TO")["notes"]
        self.assertNotIn("add it to the ACB of the replacement", p)
        self.assertIn("affiliated person", p)


class TestSchedule3Render(unittest.TestCase):
    """Formerly: test_fix_l_filing_b.TestSchedule3Render"""
    def test_crypto_note_follows_the_line_routing(self):
        # S032-23: the note's own year test was unpinned.
        ents = tax_forms.mark_crypto([_g(symbol="BTC", qty=-1.0)])
        for year, want in ((2025, "crypto-assets on 15200/15301"),
                           (2023, "crypto-assets with the other "
                                  "properties (15199/15300)"),
                           # The 2024 form's two periods (A2-0166).
                           (2024, "crypto-assets and other properties "
                                  "on 10693/10694")):
            with self.subTest(year=year):
                rep = tax_forms.build_schedule3(ents, year)
                self.assertIn(want, FE.render_schedule3(rep, year, "CAD"))

    def test_csv_write_is_atomic(self):
        # S032-24: a failed write left a truncated CSV in place.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.csv"
            out.write_text("good\n")
            rep = tax_forms.build_schedule3([_g()], 2025)

            # The row writer fails half-way (it wrote to its own temp
            # file, never to out.csv; issue #9: a temp of its own).
            def boom(_rep, f):
                f.write("partial")
                raise OSError(27, "File too large")
            with mock.patch.object(FE, "_rows_csv", boom):
                with self.assertRaises(OSError):
                    FE.write_csv(rep, out)
            self.assertEqual(out.read_text(), "good\n")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["out.csv"])
            FE.write_csv(rep, out)
            self.assertIn("AAA.TO", out.read_text())
