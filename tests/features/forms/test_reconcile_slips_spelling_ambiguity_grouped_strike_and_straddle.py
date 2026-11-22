"""Re-audit-2 fixes, filing-reports list (helper B): option-boundary,
reconcile-slips, Schedule 3 option units, the .sum per-asset block and
the fees report.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_b
"""
import contextlib
import io
import unittest

from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def T(action="BUYSELL", **kw):
    base = {"action": action, "currency": "CAD", "account": "margin",
            "time": "10:00:00"}
    base.update(kw)
    base.setdefault("date_settle", base.get("date"))
    return TaxTransaction(**base)


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------
# Schedule 3 / reconcile-slips option units, reconcile-slips
# ---------------------------------------------------------------------

def _gains(book, year, since, timing="grant"):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    req = GainsRequest(country="canada", year=year, taxable=True,
                       option_premium_timing=timing,
                       option_grant_since=since)
    res, _ = _quiet(run_gains, list(book), (), (), req)
    return res


def _computed(res, year):
    import json
    import tempfile
    from pathlib import Path
    from taxjson.lib.slip_symbols import load_computed
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "margin_gains_wash.json"
        p.write_text(json.dumps(res, default=str))
        return load_computed([p], year)


def _slip(tmpdir, text):
    from pathlib import Path
    from taxjson.bin.taxjson_reconcile_slips import load_slip
    p = Path(tmpdir) / "t5008.csv"
    p.write_text(text)
    return load_slip(p)


class TestReconcileSlipsHeaders(unittest.TestCase):
    """A2-0656: two exact spellings of one amount are ambiguous.

    Formerly: test_fix_a2_filrep_b.TestReconcileSlipsHeaders"""

    def test_a2_0656_two_exact_proceeds_columns_refused(self):
        from taxjson.bin.taxjson_reconcile_slips import (AmbiguousHeader,
                                                         _map_headers)
        for cols in (["Symbol", "Quantity", "Proceeds",
                      "Proceeds of disposition"],
                     ["Symbol", "Quantity", "Proceeds of disposition",
                      "Proceeds"],
                     ["Symbol", "Quantity", "Qty", "Proceeds"]):
            with self.assertRaises(AmbiguousHeader):
                _map_headers(cols)

    def test_a2_0656_ticker_and_security_name_still_accepted(self):
        from taxjson.bin.taxjson_reconcile_slips import _map_headers
        m = _map_headers(["Symbol", "Security", "Quantity", "Proceeds"])
        self.assertEqual(m["symbol"], "Symbol")


class TestReconcileSlipsGroupedStrike(unittest.TestCase):
    """A2-1113: a slip's option description with a grouped strike.

    Formerly: test_fix_a2_filrep_b.TestReconcileSlipsGroupedStrike"""

    def test_a2_1113_thousands_separator_strike(self):
        from taxjson.lib.slip_symbols import slip_symbol
        self.assertEqual(slip_symbol("CALL SPX12/19/25 5,000.00"),
                         "SPX251219C05000000")
        self.assertEqual(slip_symbol("SPX 19DEC25 5,000 C"),
                         "SPX251219C05000000")
        self.assertEqual(slip_symbol("CALL SPX12/19/25 5000.00"),
                         "SPX251219C05000000")
        # a decimal comma is not read as a strike
        self.assertNotEqual(slip_symbol("CALL XYZ12/19/25 2,50"),
                            "XYZ251219C00002000")


class TestReconcileSlipsGrantStraddle(unittest.TestCase):
    """A2-0657: a grant-timing write still open at Dec 31 needs no slip
    in the write year; the close year's slip carries the premium.

    Formerly: test_fix_a2_filrep_b.TestReconcileSlipsGrantStraddle"""

    S = "DEF260417C00033000.TO"
    BOOK = [T(id="w", date="2025-11-03", symbol=S, quantity=-3, price=0.8,
              net_amount=240.0),
            T(id="c", date="2026-01-12", symbol=S, quantity=3, price=0.21,
              net_amount=63.0)]

    @rule(R.CA_OPT_01)
    def test_a2_0657_write_year_no_slip_expected(self):
        import tempfile
        from taxjson.bin.taxjson_reconcile_slips import reconcile
        comp = _computed(_gains(self.BOOK, 2025, 2025), 2025)
        with tempfile.TemporaryDirectory() as tmp:
            slip = _slip(tmp, "Symbol,Quantity,Proceeds,Cost\n"
                              "ABC.TO,10,1000.00,900.00\n")
        rep = reconcile(slip, comp, 1.0)
        row = [r for r in rep["rows"] if r["symbol"].startswith("DEF")][0]
        self.assertEqual(row["symbol"], self.S)
        self.assertEqual(row["status"], "NO_SLIP_EXPECTED")
        self.assertIn("240.00", row["detail"])

    @rule(R.CA_OPT_01)
    def test_a2_0657_close_year_slip_with_prior_premium_ok(self):
        import tempfile
        from taxjson.bin.taxjson_reconcile_slips import reconcile
        comp = _computed(_gains(self.BOOK, 2026, 2025), 2026)
        with tempfile.TemporaryDirectory() as tmp:
            slip = _slip(tmp, "Symbol,Quantity,Proceeds,Cost\n"
                              f"{self.S},3,240.00,63.00\n")
        rep = reconcile(slip, comp, 1.0)
        self.assertTrue(rep["clean"], rep["rows"])
        self.assertIn("earlier-year", rep["rows"][0]["detail"])

    @rule(R.CA_OPT_01)
    def test_a2_0657_close_year_wrong_slip_still_mismatch(self):
        import tempfile
        from taxjson.bin.taxjson_reconcile_slips import reconcile
        comp = _computed(_gains(self.BOOK, 2026, 2025), 2026)
        with tempfile.TemporaryDirectory() as tmp:
            slip = _slip(tmp, "Symbol,Quantity,Proceeds,Cost\n"
                              f"{self.S},3,400.00,63.00\n")
        rep = reconcile(slip, comp, 1.0)
        self.assertFalse(rep["clean"])
