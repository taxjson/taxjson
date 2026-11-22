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
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from taxjson.rules import ids as R
COUNTRIES = ("canada", "usa")


# ------------------------------------------------------------ run_gains
def _book(sym, cur):
    from taxjson.model.transaction import TaxTransaction
    from taxjson.lib.row_types import STOCK_DIVIDEND

    def t(i, action, d, s, q, net, **kw):
        return TaxTransaction(
            id=i, action=action, date=d, date_settle=d, time="10:00:00",
            symbol=s, quantity=q, price=abs(net / q) if q else 0.0,
            net_amount=net, currency=cur, account="m", **kw)
    ph = "PHM" + sym[3:]
    return [t("xs", "BUYSELL", "2025-01-03", ph, -10, 500.0),
            t("d1", "DIVIDEND", "2025-02-01", sym, 0, 25.0),
            t("b0", "BUYSELL", "2025-02-02", sym, 10, -100.0),
            t("b", "BUYSELL", "2025-02-03", sym, 100, -1000.0,
              commission=9.95),
            t("sd", "BUYSELL", "2025-03-03", sym, 5, 0.0,
              type=STOCK_DIVIDEND),
            t("d2", "DIVIDEND", "2025-04-01", sym, 0, 50.0),
            t("s", "BUYSELL", "2025-05-05", sym, -50, 600.0,
              commission=4.99)]


def _run_gains(country):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    sym, cur = (("ZZR.TO", "CAD") if country == "canada"
                else ("ZZR.US", "USD"))
    with tempfile.TemporaryDirectory() as td:
        ph = Path(td) / "missing_history.json"
        ph.write_text(json.dumps([{"symbol": "PHM" + sym[3:],
                                   "account": "m"}]))
        from _mh import from_json
        ph = from_json(ph, _book(sym, cur), until="2025-12-31")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = run_gains(_book(sym, cur), (), (), GainsRequest(
                country=country, year=2025, taxable=True,
                incomplete_history=ph))
    return sym, cur, r, err.getvalue()


class TestRunGainsPins(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_reports.TestRunGainsPins"""

    @rule(R.CA_STKDIV_01)
    @rule_absent(R.CA_STKDIV_01, country="usa")
    @rule(R.US_STKDIV_01)
    def test_stock_dividend_line_and_option_keys_by_country(self):
        # A2-0861: the Canadian ATTENTION line and option summary keys
        # (pipeline `if req.country == 'canada'` gates) never reach a US
        # book; A2-0906 / A2-0907 m1592: no line for a plain purchase.
        _s, _c, ca, ca_err = _run_gains("canada")
        _s, _c, us, us_err = _run_gains("usa")
        lines = [ln for ln in ca_err.splitlines() if "stock dividend" in ln]
        self.assertEqual(len(lines), 1, ca_err)
        self.assertIn("ZZR.TO: stock dividend of 5 share(s) on 2025-03-03",
                      lines[0])
        self.assertIn("in Canada it is a dividend", lines[0])
        self.assertNotIn("in Canada", us_err)
        self.assertNotIn("declared amount", us_err)
        self.assertIn("§305(a)", us_err)
        keys = ("option_premium_timing", "option_grant_since",
                "option_buyback_loss_superficial")
        for k in keys:
            self.assertIn(k, ca["summary"])
            self.assertNotIn(k, us["summary"])

    @rule(R.CA_ACB_02)
    @rule(R.US_BASIS_01)
    def test_fees_by_ticker_and_manual_rows(self):
        # A2-0907: fees after a non-trade row (m1580), by_ticker after a
        # tainted row (m1607) and with two dividends (m1609), and a
        # manual-reporting row without gain or cost (m1660 / m1661).
        for country in COUNTRIES:
            sym, cur, r, _err = _run_gains(country)
            fees = r["summary"]["total_fees_by_currency"]
            self.assertAlmostEqual(fees[cur]["total"], 14.94, msg=country)
            self.assertAlmostEqual(fees[cur]["stocks"], 14.94, msg=country)
            self.assertIn(sym, r["by_ticker"], country)
            self.assertAlmostEqual(r["by_ticker"][sym]["total_div"], 75.0,
                                   msg=country)
            self.assertNotIn("PHM" + sym[3:], r["by_ticker"], country)
            manual = r["manual_reporting_required"]
            self.assertEqual([m["symbol"] for m in manual],
                             ["PHM" + sym[3:]], country)
            for k in ("gain", "cost"):
                self.assertNotIn(k, manual[0], (country, k))


if __name__ == "__main__":
    unittest.main()
