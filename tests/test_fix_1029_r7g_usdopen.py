"""Fix round 7 wave G of 2026-10-30, group usdopen: the last of the repairs of
a base-cost opening on a foreign listing (TJ-0990, TJ-1169, TJ-1298). Every
fixture is SYNTHETIC: made-up tickers, ids and amounts.

TJ-1329  a USD return of capital on a USD opening of a TSX unit was restated
         to CAD, and the account's native books were dropped.
"""
import json
import tempfile
import unittest

from _planted_rates import plant
from _qa_project import gains, project, tj, toml

_SYNR = ("BUYSELL 2026-01-05 10:00:00 SYNR.TO 5 CAD 20 100 0\n"
         "BUYSELL 2026-02-05 10:00:00 SYNR.TO -5 CAD 30 150 0\n")


def _rates(root, cleanup):
    text = "".join(f"{y}-{m:02d}-{dd:02d} 16:00:00 USD CAD 1.25 BOC\n"
                   for y, m in ((2025, 12), (2026, 1), (2026, 2), (2026, 3))
                   for dd in range(1, 29)) + "".join(
        f"2025-12-{dd} 16:00:00 USD CAD 1.25 BOC\n" for dd in (29, 30, 31))
    plant(root / "work" / "to_base.csv", text, cleanup=cleanup)


class _Run(unittest.TestCase):
    def _run(self, book):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = project(d.name, "p", {"margin/book.tt": book + _SYNR})
        (root / "taxjson.toml").write_text(toml("canada", year=2026))
        _rates(root, self.addCleanup)
        r = tj(root, "run", "--no-input")
        return root, r.stdout + r.stderr

    def _holding(self, root, sym):
        from taxjson.lib.tomlcompat import tomllib
        doc = tomllib.loads((root / "reports" / "margin_holdings.toml")
                            .read_text())
        return next(x for x in doc["holding"] if x["symbol"] == sym)

    def _assert_native_books(self, root, out):
        self.assertNotIn("raw holdings skipped", out)
        self.assertTrue((root / "work" / "margin_raw_gains.json").exists())
        self.assertIn("SYNR.TO", tj(root, "gains").stdout)
        # The unrelated sale's filing gain, whatever the native view does.
        sale = [g for g in gains(root) if g.get("qty")
                and g.get("symbol") == "SYNR.TO"]
        self.assertAlmostEqual(float(sale[0]["gain"]), 50.0, places=2)


class TestARowOnAUsdOpeningStaysInUsd(_Run):
    """TJ-1329: with no trade of the symbol, the pool's currency was the
    suffix's (.TO: CAD) although the USD opening stays in USD (TJ-1298):
    the USD ADJUST was restated to CAD beside it, and the account's native
    books were dropped."""

    def test_a_usd_adjust_on_a_usd_opening(self):
        for sym, kind in (("SYND.U.TO", "roc"), ("SYND.U.TO", "dist"),
                          ("SYNB.TO", "roc")):
            with self.subTest(sym=sym, kind=kind):
                root, out = self._run(
                    f"OPENING 2025-12-31 {sym} 100 USD 1000 2024-06-03\n"
                    f"ADJUST 2026-02-02 10:00:00 {sym} USD -100 type={kind}\n")
                self.assertNotIn("restated", out)
                self._assert_native_books(root, out)
                h = self._holding(root, sym)
                self.assertEqual(h["currency"], "USD")
                self.assertAlmostEqual(h["total_cost"], 900.0, places=2)
                self.assertAlmostEqual(h["base_total_cost"], 1125.0, places=2)

    def test_a_cad_adjust_on_a_usd_opening_is_restated_to_usd(self):
        """The CAD T3 box-42 shape `_raw_align_adjust_currency` names."""
        root, out = self._run(
            "OPENING 2025-12-31 SYND.U.TO 100 USD 1000 2024-06-03\n"
            "ADJUST 2026-02-02 10:00:00 SYND.U.TO CAD -125 type=roc\n")
        self.assertIn("restated as -100.00 USD", out)
        self._assert_native_books(root, out)
        h = self._holding(root, "SYND.U.TO")
        self.assertEqual(h["currency"], "USD")
        self.assertAlmostEqual(h["total_cost"], 900.0, places=2)
        self.assertAlmostEqual(h["base_total_cost"], 1125.0, places=2)


class TestTheChainsOtherShapesStand(_Run):
    """The recorded shapes before TJ-1329: TJ-1169's base line on a foreign
    listing, TJ-1298's bare USD opening, a CAD opening with a USD ADJUST."""

    def test_a_bare_usd_opening(self):
        root, out = self._run(
            "OPENING 2025-12-31 SYND.U.TO 100 USD 1000 2024-06-03\n")
        self.assertNotIn("restated", out)
        self._assert_native_books(root, out)
        h = self._holding(root, "SYND.U.TO")
        self.assertEqual((h["currency"], h["total_cost"]), ("USD", 1000.0))
        self.assertAlmostEqual(h["base_total_cost"], 1250.0, places=2)

    def test_a_usd_opening_and_a_usd_trade_with_a_usd_adjust(self):
        root, out = self._run(
            "OPENING 2025-12-31 SYND.U.TO 100 USD 1000 2024-06-03\n"
            "BUYSELL 2026-01-06 10:00:00 SYND.U.TO 10 USD 10 100 0\n"
            "ADJUST 2026-02-02 10:00:00 SYND.U.TO USD -100 type=roc\n")
        self.assertNotIn("restated", out)
        self._assert_native_books(root, out)
        self.assertEqual(self._holding(root, "SYND.U.TO")["currency"], "USD")

    def test_a_cad_opening_with_a_usd_adjust_on_a_tsx_listing(self):
        root, out = self._run(
            "OPENING 2025-12-31 SYND.TO 100 CAD 1250 2024-06-03\n"
            "ADJUST 2026-02-02 10:00:00 SYND.TO USD -100 type=roc\n")
        self.assertIn("restated as -125.00 CAD", out)
        self._assert_native_books(root, out)
        h = self._holding(root, "SYND.TO")
        self.assertEqual(h["currency"], "CAD")
        self.assertAlmostEqual(h["total_cost"], 1125.0, places=2)
        self.assertAlmostEqual(h["base_total_cost"], 1125.0, places=2)

    def test_a_base_line_on_a_us_listing_with_a_usd_adjust(self):
        root, out = self._run(
            "OPENING 2025-12-31 SYNQ.US 10 CAD 150 2024-06-03\n"
            "ADJUST 2026-02-02 10:00:00 SYNQ.US USD -8 type=roc\n")
        self.assertIn("SYNQ.US OPENING 2025-12-31: CAD amount restated as "
                      "120.00 USD", out)
        self._assert_native_books(root, out)
        h = self._holding(root, "SYNQ.US")
        self.assertEqual(h["currency"], "USD")
        self.assertAlmostEqual(h["total_cost"], 112.0, places=2)
        self.assertAlmostEqual(h["base_total_cost"], 140.0, places=2)

    def test_a_base_line_on_a_us_listing_sold_keeps_its_filing_cost(self):
        """TJ-0990's figures end to end: the sale's Canadian gain is on the
        stated CAD cost, not the native cost converted at the snapshot."""
        root, out = self._run(
            "OPENING 2025-12-31 SYNQ.US 10 CAD 150 2024-06-03\n"
            "BUYSELL 2026-02-03 10:00:00 SYNQ.US -10 USD 16 160 0\n")
        self._assert_native_books(root, out)
        sale = [g for g in gains(root) if g.get("qty")
                and g.get("symbol") == "SYNQ.US"]
        self.assertAlmostEqual(float(sale[0]["gain"]), 50.0, places=2)
        raw = json.loads((root / "work" / "margin_raw_gains.json").read_text())
        self.assertIn("SYNQ.US", {t["symbol"] for t in raw["transactions"]})


if __name__ == "__main__":
    unittest.main()
