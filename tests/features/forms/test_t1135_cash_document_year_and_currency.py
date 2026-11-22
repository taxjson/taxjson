"""Fixes of 2026-10-29, round 7 (wave B), group taxtext: the .tt
conversion keeps a bond coupon's property (backlog TJ-0856), an income
row's paid date on a settle-basis export (TJ-0862), the futures
settlement facts (TJ-0662), and sizes a .tt option line by the
project's ticker.map MULT (TJ-0864); the T1135 counts the gross income
of a foreign property, not a short's compensation paid (TJ-0837), an
intraday foreign custody holding in the threshold test (TJ-0646), a
cash ledger refused with no row to hold the refusal as unsettled
(TJ-0657), and the standalone tool refuses a cash document of another
year or base currency (TJ-0772). One test class per backlog item, each
named for it.

Every fixture is SYNTHETIC: invented tickers (SYN*, ZZ*), invented
account names, small amounts.

Formerly: test_fix_1029_r7b_taxtext
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from taxjson.bin import taxjson_t1135 as T
from taxjson.lib import t1135_cash as TC
from taxjson.model.transaction import TaxTransaction


def _row(i, date, sym, q, net, action="BUYSELL", time="10:00:00",
         acct="a"):
    return TaxTransaction(id=i, action=action, date=date, date_settle=date,
                          time=time, symbol=sym, quantity=q,
                          net_amount=net, gross_amount=0.0,
                          price=abs(net / q) if q else 0.0,
                          currency="CAD", account=acct)


# ========== TJ-0772 a cash document of another year or base currency
class TestTJ0772CashDocumentMustBeThisReports(unittest.TestCase):
    """TJ-0772: taxjson-t1135 --year 2025 --base-currency CAD read a
    cash document of 2024, or one in US dollars, as this year's Canadian
    dollars (only its date basis was checked).

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0772CashDocumentMustBeThisReports"""

    def _main(self, **doc_kw):
        doc = {"version": TC.VERSION, "year": 2025, "currency": "CAD",
               "basis": "settle", "ledger_status": "computed",
               "accounts": {"a": "USA"},
               "series": [{"account": "a", "currency": "EUR",
                           "country": "USA",
                           "points": [["2025-01-01", 100.0, 156.0]],
                           "unknown": False, "why": ""}],
               "not_counted": [], "stablecoins": [], "interest": {}}
        doc.update(doc_kw)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "a_base.json"
            base.write_text(json.dumps({"transactions": [
                _row("b", "2025-06-02", "SYN.US", 1, 150.0).to_dict()]}))
            cash = Path(td) / "cash.json"
            cash.write_text(json.dumps(doc))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = T.main([str(base), "--year", "2025",
                             "--base-currency", "CAD", "--json",
                             "--option-premium-timing", "close",
                             "--cash", str(cash)])
        return rc, out.getvalue(), err.getvalue()

    def test_another_years_document_is_refused(self):
        rc, _out, err = self._main(year=2024)
        self.assertEqual(rc, 2)
        self.assertIn("is for the year 2024, the report for 2025", err)

    def test_another_base_currencys_document_is_refused(self):
        rc, _out, err = self._main(currency="USD")
        self.assertEqual(rc, 2)
        self.assertIn("has its costs in USD, the report in CAD", err)

    def test_this_reports_document_is_read(self):
        rc, out, err = self._main()
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["max_total_cost"], 306.0)


if __name__ == "__main__":
    unittest.main()
