"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import json
import subprocess
import sys
import tempfile
import unittest

from _support.tt_fx_project import REPO_ROOT, _env, _cli, _tt_project
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------ A2-0139 / A2-0201 / A2-0397
class TestTrustRocRecordDateEverywhere(unittest.TestCase):
    """CA-INC-DATE-ROC-TRUST was applied only in pipeline.run_gains:
    taxjson-audit and taxjson-explain booked a trust's ROC on its pay
    date and contradicted the .sum (gain 500 there, 0 in
    audit/explain).

    Formerly: test_fix_a2_pipecmd.TestTrustRocRecordDateEverywhere"""

    _LINES = [
        "BUYSELL 2025-01-06 10:00:00 ZZR.TO 2000 CAD 10 -20000 0",
        "BUYSELL 2025-03-10 10:00:00 ZZR.TO -1000 CAD 10 10000 0",
        "ADJUST 2025-03-20 12:00:00 ZZR.TO CAD -1000 type=roc "
        "record=2025-03-03",
    ]

    def _proj(self, td):
        root, home = _tt_project(td, self._LINES, year=2025)
        r = _cli(root, home, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        g = json.loads((root / "work" / "m_gains.json").read_text())
        sale = [t for t in g["transactions"] if t.get("gain") is not None]
        self.assertEqual(round(sale[0]["gain"], 2), 500.0)
        return root, home

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_audit_ties_out(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "audit")
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            self.assertNotIn("TIE-OUT FAILED", r.stdout + r.stderr)

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_explain_traces_the_booked_gain(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_explain",
                 "--country", "canada", "--year", "2025",
                 str(root / "work" / "m_base.json")],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, env=_env(home))
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("500.00", r.stdout)
            self.assertNotIn("EMPTY pool", r.stdout + r.stderr)


def _gains_one(book, country, **req):
    import copy, contextlib, io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)), [], [],
                        req=GainsRequest(country=country, taxable=True,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


# ---------------------------------------------------------------- A2-0039
class TestRocMovedIntoAClosedYearNamesTheSale(unittest.TestCase):
    """A January-paid trust ROC with a December record date lowers the
    ACB of a December sale: the pay-year run says which prior-year sale
    changed (that year may be filed without it).

    Formerly: test_fix_a2_pipecmd.TestRocMovedIntoAClosedYearNamesTheSale"""

    def _book(self):
        from tax_rules.dual import tx
        return [tx("BUYSELL", "2024-06-03", "ZZR.TO", 1000, 10000,
                   currency="CAD", settle="2024-06-04"),
                tx("BUYSELL", "2024-12-30", "ZZR.TO", -1000, 10000,
                   currency="CAD", settle="2024-12-31"),
                tx("ADJUST", "2025-01-08", "ZZR.TO", 0, -500.0, type="roc",
                   currency="CAD", record_date="2024-12-30",
                   description="RETURN OF CAPITAL REC 2024-12-30")]

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_pay_year_run_names_the_prior_year_sale(self):
        err = _gains_one(self._book(), "canada", year=2025)["_stderr"]
        line = [ln for ln in err.splitlines() if "2024 sale" in ln]
        self.assertTrue(line, err)
        self.assertIn("ATTENTION", line[0])
        self.assertIn("2024-12-31", line[0])        # the settle date
        self.assertIn("500.00", line[0])

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_same_year_move_is_quiet(self):
        book = self._book()
        book[2].date = book[2].date_settle = "2024-12-31"
        err = _gains_one(book, "canada", year=2024)["_stderr"]
        self.assertNotIn(" sale", err)


# ---------------------------------------------------------------- A2-0396
class TestWithholdingFollowsItsDividend(unittest.TestCase):
    """A TAX row withheld on a payment is in the same year as the
    payment's dividend when income dating moves the dividend (a US
    January RIC dividend on Dec 31; a Canadian trust's distribution on
    its record date).

    Formerly: test_fix_a2_pipecmd.TestWithholdingFollowsItsDividend"""

    def _us(self):
        from tax_rules.dual import tx
        return [tx("BUYSELL", "2025-06-02", "VXUS.US", 100, 5000),
                tx("DIVIDEND", "2026-01-05", "VXUS.US", 0, 92.0,
                   gross_amount=100.0, description="VXUS DIVIDEND"),
                tx("TAX", "2026-01-05", "VXUS.US", 0, 8.0,
                   description="VXUS DIVIDEND"),
                tx("TAX", "2026-02-05", "VXUS.US", 0, 3.0,
                   description="VXUS other")]

    def _ca(self):
        from tax_rules.dual import tx
        return [tx("BUYSELL", "2025-06-02", "ZXT.TO", 100, 5000,
                   currency="CAD"),
                tx("DIVIDEND", "2026-01-15", "ZXT.TO", 0, 50.0,
                   gross_amount=50.0, currency="CAD",
                   record_date="2025-12-30", income_label="distribution",
                   description="DIST ON 100 SHS REC 12/30/25 PAY 01/15/26"),
                tx("TAX", "2026-01-15", "ZXT.TO", 0, 7.5, currency="CAD",
                   description="DIST ON 100 SHS REC 12/30/25 PAY 01/15/26")]

    @staticmethod
    def _withheld(rows, year, settings):
        from taxjson.lib.income_summary import summarize_income
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules.from_settings(settings)
        res = summarize_income([t.to_dict() for t in rows], year,
                               rules=rules)
        return round(sum(float(c.get("tax", 0.0) or 0.0)
                         for per in res["ticker_stats"].values()
                         for c in per.values()), 2)

    @rule(R.US_INC_DATE_RIC)
    def test_us_ric_withholding_moves_with_the_dividend(self):
        st = {"country": "usa",
              "ric_january_dividends": ["VXUS.US 2026-01-05"]}
        self.assertEqual(self._withheld(self._us(), 2025, st), 8.0)
        # The unpaired February withholding keeps its pay date.
        self.assertEqual(self._withheld(self._us(), 2026, st), 3.0)

    @rule(R.CA_INC_DATE_TRUST)
    def test_events_view_dates_withholding_with_the_payment(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2025-06-02 10:00:00 ZXT.TO 100 CAD 50 -5000 0",
                "DIVIDEND 2026-01-15 10:00:00 ZXT.TO 100 CAD 0.5 50 "
                "label=distribution record=2025-12-30",
                "TAX 2026-01-15 10:00:00 ZXT.TO 100 CAD 0.075 7.5"])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r25 = _cli(root, home, "events", "2025").stdout
            r26 = _cli(root, home, "events", "2026").stdout
            self.assertIn("TAX ", r25)
            self.assertNotIn("TAX ", r26)

    @rule(R.CA_INC_DATE_TRUST)
    def test_ca_trust_withholding_moves_with_the_distribution(self):
        st = {"country": "canada"}
        self.assertEqual(self._withheld(self._ca(), 2025, st), 7.5)
        self.assertEqual(self._withheld(self._ca(), 2026, st), 0.0)


# ---------------------------------------------------------------- A2-0710
class TestByTickerFollowsRowsWithoutYear(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestByTickerFollowsRowsWithoutYear"""

    @rule(R.CA_INC_03)
    def test_deemed_dividend_pil(self):
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2025-01-06", "ABC.TO", 100, 1000,
                   currency="CAD"),
                tx("DIVIDEND_IN_LIEU", "2025-03-03", "ABC.TO", 0, 12.0,
                   gross_amount=12.0, currency="CAD", dealer_country="CA",
                   issuer_country="CA")]
        for year in (None, 2025):
            with self.subTest(year=year):
                r = _gains_one(book, "canada", year=year)
                bt = r["by_ticker"]["ABC.TO"]
                self.assertEqual(bt["total_pil"], 0.0)
                self.assertEqual(bt["total_div"], 12.0)


if __name__ == "__main__":
    unittest.main()
