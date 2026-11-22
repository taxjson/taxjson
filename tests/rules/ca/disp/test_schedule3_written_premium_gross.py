"""Owner-decisions round, area engine-forms.

S069-23: a call on an ADJUSTED option series (root + digit, e.g. ZZS1)
or an option on the same FUTURES contract bought in a loss's window is
flagged for a manual check (warn-only, both countries), the way a
warrant is (CA-SL-14 / US-WASH-14).

R1-40: the Canada Schedule 3 shows a written option's premium GROSS as
proceeds with its write commission as an outlay (gain unchanged).

Formerly: test_fix_d_engine_forms
"""
from __future__ import annotations

import unittest

from taxjson.lib import country as C
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def gains_one(country, book, **req):
    import contextlib
    import copy
    import io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)), [], [],
                        req=GainsRequest(country=country, taxable=True,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _grant_row(symbol="ZZQ250919C00064000.US", cost=-623.45, fee=4.95,
               commission=0.0, gain=None, date="2025-07-16"):
    return {"date": date, "date_settle": date, "symbol": symbol, "qty": 1.0,
            "proceeds": 0.0, "cost": cost,
            "gain": -cost if gain is None else gain, "raw_gain": -cost,
            "disallowed_amount": 0.0, "days_held": 0, "term": None,
            "direction": "SHORT", "commission": commission, "fee": fee,
            "account": "margin", "is_option": True, "grant": True}


class TestScheduleThreeWrittenPremiumGross(unittest.TestCase):
    """R1-40: a written option's premium is shown GROSS as proceeds and
    its write commission as an outlay; the gain is unchanged.

    Formerly: test_fix_d_engine_forms.TestScheduleThreeWrittenPremiumGross"""

    def _row(self, entries):
        from taxjson.lib.tax_forms import build_schedule3
        rep = build_schedule3(entries, 2025)
        self.assertEqual(len(rep["rows"]), 1)
        return rep["rows"][0], rep

    @rule(R.CA_DISP_06)
    def test_grant_row_gross_premium_and_commission_outlay(self):
        r, _ = self._row([_grant_row()])
        self.assertAlmostEqual(r["proceeds"], 628.40)
        self.assertAlmostEqual(r["outlays"], 4.95)
        self.assertAlmostEqual(r["acb"], 0.0)
        self.assertAlmostEqual(r["gain"], 623.45)
        self.assertAlmostEqual(r["proceeds"] - r["acb"] - r["outlays"],
                               r["gain"], places=2)

    @rule(R.CA_DISP_06)
    def test_buyback_commission_stays_in_the_acb(self):
        # The buy-back row's fee is an acquisition cost (in the ACB): it
        # must not be added to proceeds or outlays.
        back = {"date": "2025-07-25", "date_settle": "2025-07-28",
                "symbol": "ZZQ250919C00064000.US", "qty": 1.0,
                "proceeds": -198.30, "cost": 0.0, "gain": -198.30,
                "raw_gain": -198.30, "disallowed_amount": 0.0,
                "days_held": 9, "term": None, "direction": "SHORT",
                "commission": 0.0, "fee": 1.05, "account": "margin",
                "is_option": True, "grant": False}
        r, _ = self._row([_grant_row(), back])
        self.assertAlmostEqual(r["proceeds"], 628.40)
        self.assertAlmostEqual(r["outlays"], 4.95)
        self.assertAlmostEqual(r["acb"], 198.30)
        self.assertAlmostEqual(r["gain"], 425.15)

    @rule(R.CA_DISP_06)
    def test_debit_write_shows_premium_and_commission(self):
        # Commission 5.00 above a 2.00 premium: net debit 3.00.
        r, _ = self._row([_grant_row(cost=3.0, fee=5.0, gain=-3.0)])
        self.assertAlmostEqual(r["proceeds"], 2.0)
        self.assertAlmostEqual(r["outlays"], 5.0)
        self.assertAlmostEqual(r["acb"], 0.0)
        self.assertAlmostEqual(r["gain"], -3.0)

    @rule(R.CA_DISP_06)
    def test_line_totals_and_filing_lines_agree(self):
        from taxjson.bin.taxjson_form_export import filing_lines
        rows = [_grant_row(), _grant_row(symbol="ZZQ250919P00070000.US",
                                         cost=-100.0, fee=1.0,
                                         commission=0.5)]
        lines = filing_lines(rows, 2025)
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0]["proceeds"], 628.40 + 101.5)
        self.assertAlmostEqual(lines[0]["outlays"], 4.95 + 1.5)
        self.assertAlmostEqual(lines[0]["gain"], 723.45)

    @rule(R.CA_DISP_06)
    def test_engine_grant_write_end_to_end(self):
        opt = "ZZQ250919C00064000.US"
        book = [tx("BUYSELL", "2025-07-15", opt, -1, 623.45,
                   settle="2025-07-16", price=6.284, fee=4.95),
                tx("BUYSELL", "2025-07-25", opt, 1, 198.30,
                   settle="2025-07-28", price=1.983)]
        res = gains_one(C.CANADA, book, year=2025,
                        option_premium_timing="grant",
                        option_grant_since=2025)
        from taxjson.lib.tax_forms import build_schedule3
        rep = build_schedule3(res["transactions"], 2025)
        r = rep["rows"][0]
        self.assertAlmostEqual(r["outlays"], 4.95, places=2)
        self.assertAlmostEqual(r["proceeds"], 628.40, places=2)
        self.assertAlmostEqual(r["gain"], 425.15, places=2)

    def test_reconcile_slips_gross_includes_the_write_commission(self):
        import json
        import tempfile
        from pathlib import Path
        from taxjson.lib.slip_symbols import load_computed
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_gains.json"
            p.write_text(json.dumps({"transactions": [_grant_row()]}))
            c = load_computed([p], 2025)
        rec = next(iter(c.values()))
        self.assertAlmostEqual(rec["proceeds_net"], 623.45)
        self.assertAlmostEqual(rec["proceeds_gross"], 628.40)


if __name__ == "__main__":
    unittest.main()
