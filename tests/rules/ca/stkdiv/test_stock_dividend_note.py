"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import unittest
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


# --------------------------- A2-0709 / A2-0711 / A2-1220 / A2-1224 / A2-1218
class TestCanadaStockDividendNote(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestCanadaStockDividendNote"""

    @staticmethod
    def _book(adjust=None):
        from tax_rules.dual import tx
        b = [tx("BUYSELL", "2024-02-03", "QSC.TO", 1200, 12000,
                currency="CAD"),
             tx("BUYSELL", "2024-06-26", "QSC.TO", 180, 0.0, price=0.0,
                currency="CAD", type="stock_dividend")]
        if adjust:
            b.append(tx("ADJUST", adjust, "QSC.TO", 0, 1332.0,
                        currency="CAD"))
        return b

    @rule(R.CA_STKDIV_01)
    def test_wording_year_and_quiet_once_added(self):
        e24 = _gains_one(self._book(), "canada", year=2024)["_stderr"]
        self.assertIn("stock dividend of 180", e24)
        self.assertIn("books the ACB only", e24)
        self.assertNotIn("ACB and income", e24)
        # Another year's run: not this year's event.
        e25 = _gains_one(self._book(), "canada", year=2025)["_stderr"]
        self.assertNotIn("stock dividend of 180", e25)
        # The cost added (distributions.map's record-date row): quiet.
        e24b = _gains_one(self._book("2024-06-19"), "canada",
                          year=2024)["_stderr"]
        self.assertNotIn("stock dividend of 180", e24b)

    @rule(R.CA_STKDIV_01)
    def test_sheltered_book_is_quiet(self):
        import copy, contextlib, io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(copy.deepcopy(self._book()), [], [],
                      req=GainsRequest(country="canada", taxable=False,
                                       year=2024))
        self.assertNotIn("stock dividend of 180", err.getvalue())

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_income_dating_advice_not_for_a_sheltered_book(self):
        import copy, contextlib, io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2024-06-03", "ZZR.TO", 1000, 10000,
                   currency="CAD"),
                tx("ADJUST", "2025-01-08", "ZZR.TO", 0, -500.0, type="roc",
                   currency="CAD")]
        for taxable, want in ((True, True), (False, False)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                run_gains(copy.deepcopy(book), [], [], req=GainsRequest(
                    country="canada", taxable=taxable, year=2025))
            self.assertEqual("no record date" in err.getvalue(), want,
                             err.getvalue())


if __name__ == "__main__":
    unittest.main()
