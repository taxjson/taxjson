"""Re-audit-2 test pins (tests-pins-04/05/06, reports group): labels and
accumulators that held at the audit but that no test failed on when
reverted. Each test names the finding and the mutant it kills.

  A2-0880 / A2-1585  fees-sum's per-brokerage 'non-option' line, its
                     $/UNIT header and 'Non-option vs option fees:'
                     heading; `tjs sum`'s NON-OPT column; the tx
                     views' 'TOTAL STAKING (crypto, ordinary income):'
  A2-1555            fx-cash's per-currency ACQUIRED column sums
  A2-0533            `tjs sum`'s summarize_gains / summarize_income
                     accumulators (2+ rows per ticker and term, 2
                     withholding rows, an 'other' row, all-zero rows)
  A2-0861 / A2-0906 / A2-0907
                     run_gains: the Canadian stock-dividend line and the
                     Canadian option summary keys stay out of a US book;
                     no note for a plain purchase; fees after a non-trade
                     row; by_ticker after a tainted row and with two
                     dividends; a manual-reporting row has no gain/cost
  A2-0865 / A2-0866 / A2-1532
                     checklist: a stale wash file makes audit and
                     form-export 'attention'; the t5-t3 step names the
                     TAXABLE line; the US slip and margin-interest words

All data is synthetic.

Formerly: test_fix_a2_pins2_reports
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _tool(module, *args):
    env = dict(os.environ, PYTHONPATH=str(SRC), NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", f"taxjson.bin.{module}", *map(str, args)],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=180)


# ------------------------------------------------------------ fees-sum
class TestFeesSumLabels(unittest.TestCase):
    """A2-0880: the converting branch's per-brokerage line says
    'non-option' (S031-04); A2-1585: its heading and the $/UNIT column
    header (the legend's '$/UNIT is ...' does not count).

    Formerly: test_fix_a2_pins2_reports.TestFeesSumLabels"""

    def test_converting_labels(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"
            work.mkdir()
            (work / "c.json").write_text(json.dumps({
                "metadata": {"source_brokerage": "questrade"},
                "transactions": [dict(
                    action="BUYSELL", date="2025-03-03",
                    date_settle="2025-03-04", time="10:00:00",
                    symbol="ENB.TO", quantity=10, price=50.0,
                    net_amount=-500.0, currency="CAD", account="margin",
                    commission=9.95)]}))
            rates = Path(td) / "to_base.csv"
            rates.write_text("2025-03-03 12:00:00 USD CAD 1.40000\n")
            r = _tool("taxjson_fees", "--cache", work, "--to", "CAD",
                      "--rates", rates)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        header = next(ln for ln in lines if ln.startswith("BROKERAGE"))
        self.assertIn("$/UNIT", header)
        self.assertNotIn("$/SHARE", header)
        # The split is its own table under a sentence-case heading
        # (docs/output-style.md): BROKERAGE  NON-OPTION  OPTIONS.
        i = lines.index("Non-option vs option fees")
        self.assertRegex(lines[i + 1], r"^BROKERAGE\s+NON-OPTION\s+OPTIONS$")
        row = next(ln for ln in lines[i + 1:]
                   if ln.strip().startswith("questrade"))
        self.assertRegex(row, r"questrade\s+9\.95\s+0\.00$")


# --------------------------------------------------- summarize_gains
def _g(symbol, gain, cost, proceeds, days, term, d, **kw):
    return dict(symbol=symbol, currency="USD", proceeds=proceeds, cost=cost,
                gain=gain, days_held=days, term=term, date=d,
                action="BUYSELL", **kw)


class TestSumGainsAccumulators(unittest.TestCase):
    """A2-0533 F02 (pil), F05/F06 (st/lt), F07 (trades), F08 (hold days),
    F09 (all-zero rows skipped), F10 (option fees), F12 (TOTAL ST).

    Formerly: test_fix_a2_pins2_reports.TestSumGainsAccumulators"""

    ROWS = [_g("AAA.US", 100, 1000, 1100, 10, "SHORT_TERM", "2025-03-03"),
            _g("AAA.US", 50, 1000, 1050, 20, "SHORT_TERM", "2025-04-03"),
            _g("AAA.US", 200, 1000, 1200, 400, "LONG_TERM", "2025-05-03"),
            _g("AAA.US", 300, 1000, 1300, 500, "LONG_TERM", "2025-06-03"),
            _g("BBB.US", -100, 1000, 900, 6, "SHORT_TERM", "2025-03-05"),
            _g("BBB.US", 0, 0, 0, 4, "SHORT_TERM", "2025-03-06"),
            _g("AAA250620C00050000.US", 40, 100, 140, 30, "SHORT_TERM",
               "2025-05-20", commission=1.25),
            _g("AAA250620C00055000.US", 20, 100, 120, 30, "SHORT_TERM",
               "2025-05-21", commission=0.75),
            _g("ZZZ.US", 0, 0, 0, 1, "SHORT_TERM", "2025-07-01"),
            dict(symbol="CCC.US", currency="USD",
                 action="DIVIDEND_IN_LIEU", pil=12.5, date="2025-03-01"),
            dict(symbol="CCC.US", currency="USD",
                 action="DIVIDEND_IN_LIEU", pil=7.5, date="2025-06-01")]

    def _sum(self):
        from taxjson.lib.gain_summary import summarize_gains
        return summarize_gains({"transactions": [dict(r) for r in self.ROWS],
                                "summary": {"year": 2025}})

    @rule(R.US_HOLD_01)
    def test_per_ticker_and_term_sums(self):
        s = self._sum()
        a = s["ticker_stats"]["AAA.US"]["USD"]
        # Options count under their underlying: 100 + 50 + 40 + 20.
        self.assertAlmostEqual(a["st_gain"], 210.0)
        self.assertAlmostEqual(a["lt_gain"], 500.0)
        self.assertAlmostEqual(s["ticker_stats"]["CCC.US"]["USD"]["pil"],
                               20.0)
        self.assertAlmostEqual(s["option_fees"]["USD"], 2.0)

    @rule(R.US_HOLD_01)
    def test_report_totals(self):
        from taxjson.bin.taxjson_sum_gains import format_report
        rep = format_report(self._sum(), no_color=True)
        lines = rep.splitlines()
        # The all-zero ZZZ.US row is skipped; AAA's ST/LT (its options
        # included) are summed.
        self.assertFalse(any(ln.startswith("ZZZ.US") for ln in lines), rep)
        aaa = next(ln for ln in lines if ln.startswith("AAA.US "))
        self.assertIn("[ST:   210.00 LT:   500.00]", aaa)
        total = next(ln for ln in lines if ln.startswith("TOTAL"))
        # TOTAL ST = 210 (AAA and its options) - 100 + 0 (BBB) = 110.
        self.assertIn("ST:   110.00", total)
        self.assertIn("LT:   500.00", total)
        # Avg days over the 8 trades: (10+20+400+500+6+4+30+30)/8 = 125.
        self.assertRegex(total, r"\s125\.0\s")


# -------------------------------------------------- summarize_income
class TestSumIncomeAccumulators(unittest.TestCase):
    """A2-0533 F14 (pil), F15 (withholding), F17 ('other' income), F18
    (an all-zero ticker is skipped).

    Formerly: test_fix_a2_pins2_reports.TestSumIncomeAccumulators"""

    ROWS = [dict(action="DIVIDEND", symbol="AAA.US", currency="USD",
                 gross_amount=100.0, date="2025-03-15"),
            dict(action="TAX", symbol="AAA.US", currency="USD",
                 net_amount=15.0, date="2025-03-15"),
            dict(action="DIVIDEND", symbol="AAA.US", currency="USD",
                 gross_amount=100.0, date="2025-06-15"),
            dict(action="TAX", symbol="AAA.US", currency="USD",
                 net_amount=15.0, date="2025-06-15"),
            dict(action="DIVIDEND_IN_LIEU", symbol="AAA.US",
                 currency="USD", gross_amount=4.0, date="2025-09-15"),
            dict(action="DIVIDEND_IN_LIEU", symbol="AAA.US",
                 currency="USD", gross_amount=6.0, date="2025-12-15"),
            dict(action="OTHER_INCOME", type="other", symbol="BBB.US",
                 currency="USD", net_amount=9.0, date="2025-05-01"),
            dict(action="DIVIDEND", symbol="ZZZ.US", currency="USD",
                 gross_amount=10.0, date="2025-04-01"),
            dict(action="DIVIDEND", symbol="ZZZ.US", currency="USD",
                 gross_amount=-10.0, date="2025-04-02")]

    @rule(R.US_INC_03)
    def test_sums_and_skip(self):
        from taxjson.lib.income_summary import summarize_income
        from taxjson.bin.taxjson_sum_income import format_report
        s = summarize_income([dict(r) for r in self.ROWS], 2025)
        a = s["ticker_stats"]["AAA.US"]["USD"]
        self.assertAlmostEqual(a["div"], 200.0)
        self.assertAlmostEqual(a["tax"], 30.0)
        self.assertAlmostEqual(a["pil"], 10.0)
        self.assertAlmostEqual(s["ticker_stats"]["BBB.US"]["USD"]["div"],
                               9.0)
        rep = format_report(s)
        self.assertFalse(any(ln.startswith("ZZZ.US")
                             for ln in rep.splitlines()), rep)
        self.assertTrue(any(ln.startswith("BBB.US")
                            for ln in rep.splitlines()), rep)


if __name__ == "__main__":
    unittest.main()
