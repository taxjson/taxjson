"""Regression tests for fix round 7 (wave B), group coreus: the engines'
income and US paths.

TJ-0647 a US stock dividend's entitled sale before a split was counted
        in pre-split shares: the dividend ratio came out wrong.
TJ-0648 an entitled sale under the ticker a rename replaced was not
        found: the held shares took the whole dividend.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7b_coreus
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
import _paths


def _gains(book, sheltered=(), year=2025, country="usa", **req):
    """pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res, **match):
    return [t for t in res["transactions"]
            if t.get("qty") and all(t.get(k) == v for k, v in match.items())]


# ---------------------------------------------------------------- TJ-0728
class TestDividendCompensationPaidOnAShort(unittest.TestCase):
    """100 shares shorted 2025-01-06 for 1000; 20 of dividend
    compensation paid 2025-01-15; covered for 900. Covered 2025-01-21
    (within 45 days) the payment is the cover's cost: gain 80, no paid
    compensation left. Covered 2025-03-03 it stays paid compensation
    (gain 100), and the estimate on the standard deduction does not
    subtract it.

    Formerly: test_fix_1029_r7b_coreus.TestDividendCompensationPaidOnAShort"""

    def _book(self, cover):
        return [tx("BUYSELL", "2025-01-06", "ZQK.US", -100, 1000.0),
                tx("DIVIDEND_IN_LIEU", "2025-01-15", "ZQK.US", 100, -20.0,
                   type="dividend_in_lieu", gross_amount=-20.0),
                tx("BUYSELL", cover, "ZQK.US", 100, 900.0)]

    @rule(R.US_INC_04)
    def test_short_closed_within_45_days_capitalizes(self):
        res = _gains(self._book("2025-01-21"))
        cover = _sales(res, direction="SHORT")[0]
        self.assertAlmostEqual(cover["gain"], 80.0)
        pil = [t for t in res["transactions"]
               if t.get("action") == "DIVIDEND_IN_LIEU"][0]
        self.assertAlmostEqual(pil["pil"], 0.0)
        self.assertAlmostEqual(pil["capitalized_263h"], 20.0)
        from taxjson.lib.tax_forms import build_8949
        row = build_8949(_sales(res, direction="SHORT"),
                         2025)["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (1000.0, 920.0, 80.0))

    @rule(R.US_INC_04)
    def test_short_held_longer_keeps_the_payment(self):
        res = _gains(self._book("2025-03-03"))
        cover = _sales(res, direction="SHORT")[0]
        self.assertAlmostEqual(cover["gain"], 100.0)
        pil = [t for t in res["transactions"]
               if t.get("action") == "DIVIDEND_IN_LIEU"][0]
        self.assertAlmostEqual(pil["pil"], -20.0)
        self.assertNotIn("capitalized_263h", pil)

    def test_a_negative_row_with_no_short_is_left_alone(self):
        """A reversal of a payment received on long shares."""
        book = [tx("BUYSELL", "2025-01-06", "ZQK.US", 100, 1000.0),
                tx("DIVIDEND_IN_LIEU", "2025-01-15", "ZQK.US", 100, -20.0,
                   type="dividend_in_lieu", gross_amount=-20.0)]
        res = _gains(book)
        pil = [t for t in res["transactions"]
               if t.get("action") == "DIVIDEND_IN_LIEU"][0]
        self.assertAlmostEqual(pil["pil"], -20.0)

    @rule(R.US_INC_04)
    def test_estimate_does_not_subtract_paid_compensation(self):
        import json
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        from taxjson.lib.dispatch import python_module_argv
        src = _paths.REPO / "src"

        def estimate(paid):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "taxjson.toml").write_text(
                    "[settings]\ncountry = \"usa\"\nyear = 2025\n"
                    "base_currency = \"USD\"\nsource_currencies = []\n"
                    "local_timezone = \"America/New_York\"\n\n"
                    "[accounts.a]\ntype = \"taxable\"\n\n"
                    "[estimate]\nother_income = 100000\n")
                (root / "ticker.map").write_text("")
                d = root / "inputs" / "a"
                d.mkdir(parents=True)
                (d / "book.tt").write_text(
                    "BUYSELL 2025-01-06 10:00:00 ZQK.US -100 USD 10 1000 0\n"
                    "BUYSELL 2025-03-03 10:00:00 ZQK.US 100 USD 9 900 0\n")
                if paid:
                    (d / "book.csv").write_text(
                        "Statement,Header,Field Name,Field Value\n"
                        "Statement,Data,BrokerName,Interactive Brokers LLC\n"
                        "Dividends,Header,Currency,Account,Date,"
                        "Description,Amount\n"
                        "Dividends,Data,USD,U1234567,2025-01-15,"
                        "ZQK(US9990000001) Payment in Lieu of Dividend "
                        "USD 0.2 per Share,-20\n")
                env = dict(os.environ, PYTHONPATH=str(src),
                           TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
                argv = python_module_argv("taxjson.bin.taxjson_run")
                for args in (("run", "--no-input"),
                             ("estimate", "--json")):
                    r = subprocess.run(argv + ["-C", str(root), *args],
                                       capture_output=True, text=True,
                                       env=env, stdin=subprocess.DEVNULL,
                                       timeout=600)
                    self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                return json.loads(r.stdout)["estimate"]
        with_paid, without = estimate(True), estimate(False)
        self.assertEqual(with_paid["estimated_tax"],
                         without["estimated_tax"])
        self.assertEqual(with_paid["pil_paid_not_deducted"], 20.0)
        self.assertTrue(any("Schedule A" in n for n in with_paid["notes"]))


if __name__ == "__main__":
    unittest.main()
