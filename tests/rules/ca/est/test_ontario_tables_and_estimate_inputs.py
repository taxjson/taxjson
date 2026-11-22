"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest

from taxjson.lib import tax_estimate as te
import _paths


class _EstimateCase(unittest.TestCase):
    def tearDown(self):
        te.apply_vintage(2025)          # the import-time default


class TestCanadaTablesPinned(_EstimateCase):
    """G1-1: the 2025 federal/ON and 2026 ON tables, to the cent.

    Formerly: test_fix_misc.TestCanadaTablesPinned"""

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
        # + 75,532 x 29% + 46,586 x 33% = 73,773.23, less the phased-down  # pii-ok
        # BPA 14,538 x 14.5% = 2,108.01.  # pii-ok
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


_ESTIMATE_BOOK = """\
BUYSELL 2025-01-15 09:30:00 XEI.TO 100.00000000 CAD 10.00000000 1000.00000 0.00000
BUYSELL 2025-06-20 10:15:00 XEI.TO -100.00000000 CAD 15.00000000 1500.00000 0.00000
BUYSELL 2025-02-10 10:00:00 XEI250321C00016000.TO -2.00000000 CAD 1.50000000 300.00000 0.00000
BUYSELL 2025-02-20 10:00:00 XEI250321C00016000.TO 2.00000000 CAD 0.40000000 80.00000 0.00000
DIVIDEND 2025-04-01 09:30:00 XEI.TO 0.00000000 CAD 0.00000000 120.00000
DIVIDEND 2025-04-02 09:30:00 ZZQ.US 0.00000000 CAD 0.00000000 200.00000
DIVIDEND_IN_LIEU 2025-05-01 09:30:00 XEI.TO 0.00000000 CAD 0.00000000 70.00000
"""


class TestEstimateInputsEndToEnd(unittest.TestCase):
    """G1-4: `tjs estimate` gathers stock + option gains, Canadian and
    foreign dividends and PIL from the books, and taxes them exactly.

    Formerly: test_fix_misc.TestEstimateInputsEndToEnd"""

    def test_estimate_from_a_run(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = _paths.REPO
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
        # The federal base is 26907.545, an exact half cent: cut up since
        # step 6h(4) (26907.54 before).
        self.assertEqual(e["tax_base"], {"federal": 26907.55, "provincial": 15388.4,
                                         "total": 42295.95})
        self.assertEqual(e["estimated_tax"], 286.35)


if __name__ == "__main__":
    unittest.main()
