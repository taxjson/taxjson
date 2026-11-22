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


# ---------------------------------------------------------------- A2-0398
class TestListedRicDividendIsNamed(unittest.TestCase):
    """A January dividend listed in ric_january_dividends leaves the pay
    year's books: both project years say so on the ATTENTION channel.

    Formerly: test_fix_a2_pipecmd.TestListedRicDividendIsNamed"""

    @rule(R.US_INC_DATE_RIC)
    def test_both_years_name_the_moved_dividend(self):
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2025-06-02", "VTI.US", 10, 2000),
                tx("DIVIDEND", "2026-01-05", "VTI.US", 0, 90.0,
                   gross_amount=90.0, description="VTI DIVIDEND")]
        kw = dict(ric_january_dividends=("VTI.US 2026-01-05",))
        e26 = _gains_one(book, "usa", year=2026, **kw)["_stderr"]
        e25 = _gains_one(book, "usa", year=2025, **kw)["_stderr"]
        self.assertIn("ATTENTION: income year: VTI.US: dividend 90.00", e26)
        self.assertIn("NOT in 2026's numbers", e26)
        self.assertIn("counted in 2025 here", e25)
        e24 = _gains_one(book, "usa", year=2024, **kw)["_stderr"]
        self.assertNotIn("VTI.US", e24)


if __name__ == "__main__":
    unittest.main()
