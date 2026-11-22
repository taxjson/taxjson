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


# ---------------------------------------------------------------- TJ-0655
class TestUntypedManualRocWithARecordDate(unittest.TestCase):
    """100 trust units bought 2025-12-01 for 100 and sold 2025-12-30 for
    120; a hand-entered ADJUST of -50 paid 2026-01-05 with record date
    2025-12-29. With or without `type=roc`, it lowers the ACB on the
    record date: the 2025 gain is 70, none in 2026.

    Formerly: test_fix_1029_r7b_coreus.TestUntypedManualRocWithARecordDate"""

    def _gains(self, typ):
        book = [tx("BUYSELL", "2025-12-01", "ZQT.UN.TO", 100, 100.0,
                   currency="CAD"),
                tx("BUYSELL", "2025-12-30", "ZQT.UN.TO", -100, 120.0,
                   currency="CAD"),
                tx("ADJUST", "2026-01-05", "ZQT.UN.TO", 0, -50.0,
                   currency="CAD", record_date="2025-12-29", type=typ)]
        return _gains(book, country="canada", year=None)

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_untyped_row_uses_its_record_date(self):
        for typ in ("roc", ""):
            res = self._gains(typ)
            by_year = {}
            for t in res["transactions"]:
                if t.get("gain") is not None:
                    by_year[t["date"][:4]] = (by_year.get(t["date"][:4], 0)
                                              + t["gain"])
            self.assertEqual({y: round(g, 2) for y, g in by_year.items()},
                             {"2025": 70.0}, typ)

    def test_a_cost_raise_typed_otherwise_is_not_moved(self):
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules("canada")
        row = TaxTransaction(action="ADJUST", date="2026-01-05",
                             date_settle="2026-01-05", symbol="ZQT.UN.TO",
                             quantity=0.0, net_amount=50.0, currency="CAD",
                             account="margin", record_date="2025-12-29",
                             type="dist")
        self.assertEqual(rules.roc_record_date(row), "")
        row.type = ""
        self.assertEqual(rules.roc_record_date(row), "2025-12-29")
        row.corp_event_id = "ev1"
        self.assertEqual(rules.roc_record_date(row), "")


# ---------------------------------------------------------------- TJ-0729
class TestPaymentInLieuOfAReturnOfCapital(unittest.TestCase):
    """IB Canada pays 100 in lieu of a Canadian corporation's return of
    capital ("(Return of Capital)"): the payment it replaces was no
    taxable dividend, so s.260(5.1) deems no dividend: ordinary income.
    The same row without the label stays a deemed dividend.

    Formerly: test_fix_1029_r7b_coreus.TestPaymentInLieuOfAReturnOfCapital"""

    HEAD = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers Canada Inc\n"
            "Dividends,Header,Currency,Account,Date,Description,Amount\n")

    def _parse(self, marker):
        import tempfile
        from pathlib import Path
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.csv"
            p.write_text(self.HEAD + "Dividends,Data,CAD,U1234567,"
                         "2025-06-02,ZQR(CA9990000001) Payment in Lieu of "
                         f"Dividend CAD 1 per Share{marker},100\n")
            with contextlib.redirect_stderr(io.StringIO()):
                txs = IbBrokerage().parse_file(p)
        return [t for t in txs if t["action"] == "DIVIDEND_IN_LIEU"]

    @rule(R.CA_INC_03, R.CA_INC_07)
    def test_roc_pil_is_ordinary_income(self):
        from taxjson.lib.tx_load import coerce_transaction_row
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules("canada")
        roc = self._parse(" (Return of Capital)")
        plain = self._parse("")
        self.assertEqual(roc[0].get("income_label"), "return_of_capital")
        self.assertFalse(plain[0].get("income_label"))
        roc_row = coerce_transaction_row(roc[0], 0, "test")
        plain_row = coerce_transaction_row(plain[0], 0, "test")
        self.assertEqual(roc_row.income_label, "return_of_capital")
        self.assertFalse(rules.pil_is_dividend(roc_row))
        self.assertTrue(rules.pil_is_dividend(plain_row))

    def test_label_round_trips_through_tt(self):
        from taxjson.lib import tt_format as ct
        row = self._parse(" (Return of Capital)")[0]
        tail = ct.tt_tail(row, "DIVIDEND_IN_LIEU")
        self.assertIn("label=return_of_capital", tail)
        self.assertEqual(
            ct._check_facts("DIVIDEND_IN_LIEU",
                            {"label": "return_of_capital"}, tail,
                            "book.tt")["income_label"],
            "return_of_capital")


if __name__ == "__main__":
    unittest.main()
