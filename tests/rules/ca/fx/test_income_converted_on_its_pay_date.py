"""Fixes of 2026-10-29, round 7 (wave C), group taxtext: the standalone
pipeline tools. The validator survives an impossible trade date beside
a settlement date (backlog TJ-0987), reads the qty alias (TJ-0977) and
checks multiplier and market_value (TJ-0979); the .tt conversion reads
the qty alias too (TJ-0977), writes an untimed row's clock (TJ-0983),
reads an income line's per-share price (TJ-0984) and keeps a broker
trade cancellation (TJ-0985); currency conversion prices income on its
pay date (TJ-0974) and names a selected rate's own source (TJ-0986);
sort and conversion keep the per-file broker accounts (TJ-0976). One
test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented tickers (SYN*), invented account
ids, small amounts.

Formerly: test_fix_1029_r7c_taxtext
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


def _cli(module, args, cwd=None, stdin=None):
    """Run a taxjson module as the pipeline does: (rc, stdout, stderr)."""
    env = dict(os.environ, TAXJSON_WIDTH="0", TAXJSON_OFFLINE="1",
               PYTHONPATH=str(REPO_ROOT / "src"))
    c = subprocess.run(python_module_argv(f"taxjson.bin.{module}",
                                          [str(a) for a in args]),
                       capture_output=True, text=True, env=env, cwd=cwd,
                       input=stdin)
    return c.returncode, c.stdout, c.stderr


def _buy(**kw):
    row = {"action": "BUYSELL", "date": "2025-02-28",
           "date_settle": "2025-03-03", "time": "10:00:00",
           "symbol": "SYN.TO", "quantity": 10.0, "price": 10.0,
           "net_amount": 100.0, "fee": 0.0, "currency": "CAD",
           "account": "margin"}
    row.update(kw)
    return row


# ======================================= TJ-0974 income on its pay date
class TestTJ0974IncomeConvertedOnItsPayDate(unittest.TestCase):
    """TJ-0974: a USD 100 dividend paid June 2 with date_settle June 3
    was converted at June 3's rate (1.50: CAD 150) instead of the pay
    date's (1.25: CAD 125).

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0974IncomeConvertedOnItsPayDate"""

    RATES = ("2025-06-02 12:00:00 USD CAD 1.25\n"
             "2025-06-03 12:00:00 USD CAD 1.50\n")

    @staticmethod
    def _div(action="DIVIDEND", **kw):
        row = {"action": action, "date": "2025-06-02",
               "date_settle": "2025-06-03", "time": "10:00:00",
               "symbol": "SYN.US", "currency": "USD", "net_amount": 100.0,
               "gross_amount": 100.0, "account": "margin"}
        row.update(kw)
        return row

    def _convert(self, rows):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            rates = Path(d) / "rates.txt"
            book.write_text(json.dumps({"transactions": rows}))
            rates.write_text(self.RATES)
            rc, out, err = _cli("taxjson_convert_currency",
                                [book, "--to", "CAD", "--rates", rates])
        self.assertEqual(rc, 0, err)
        return json.loads(out)["transactions"]

    @rule(R.CA_FX_01)
    def test_income_withholding_and_fees_take_the_pay_dates_rate(self):
        rows = self._convert([
            self._div(),
            self._div("DIVIDEND_IN_LIEU"),
            self._div("TAX", net_amount=-15.0, gross_amount=-15.0),
            self._div("INTEREST", symbol="CASH"),
            self._div("FEE", symbol="CASH", net_amount=8.0,
                      gross_amount=0.0)])
        self.assertEqual([r["net_amount"] for r in rows],
                         [125.0, 125.0, -18.75, 125.0, 10.0])

    @rule(R.CA_FX_01)
    def test_a_trade_keeps_its_settlement_dates_rate(self):
        rows = self._convert([_buy(date="2025-06-02",
                                   date_settle="2025-06-03",
                                   currency="USD")])
        self.assertEqual(rows[0]["net_amount"], 150.0)

    @rule(R.US_FX_02)
    def test_rate_date_is_the_one_rule(self):
        from taxjson.bin.taxjson_convert_currency import rate_date
        from taxjson.model.transaction import TaxTransaction
        self.assertEqual(rate_date(self._div()), "2025-06-02")
        self.assertEqual(rate_date(self._div(date_settle="")),
                         "2025-06-02")
        self.assertEqual(rate_date(_buy()), "2025-03-03")
        self.assertEqual(rate_date(_buy(date_settle="")), "2025-02-28")
        self.assertEqual(rate_date(TaxTransaction(**self._div())),
                         "2025-06-02")


if __name__ == "__main__":
    unittest.main()
