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


class TestIBTrustDistributionKeepsPayDate(unittest.TestCase):
    """S057-23 (documentation; owner decision 2026-10-01): an IB row on a
    Canadian trust has an accrual EX date but no record date and no
    'distribution' label, so a Canada project keeps its pay date.

    Formerly: test_fix_d_engine_forms.TestIBTrustDistributionKeepsPayDate"""

    @rule(R.CA_INC_DATE_TRUST)
    def test_ib_style_row_with_december_ex_date_stays_in_pay_year(self):
        book = [tx("BUYSELL", "2024-06-03", "ZZT.TO", 3000, 30000,
                   currency="CAD"),
                tx("DIVIDEND", "2025-01-08", "ZZT.TO", 0, 513.0,
                   gross_amount=513.0, type="dividend", currency="CAD",
                   ex_date="2024-12-31", issuer_country="CA",
                   description="ZZT(CA0000000000) Cash Dividend CAD 0.171 "
                               "per Share (Ordinary Dividend)")]
        got = [y for y in (2024, 2025)
               if any(t.get("action") == "DIVIDEND" for t in
                      gains_one(C.CANADA, book, year=y)["transactions"])]
        self.assertEqual(got, [2025])

    def test_tax_logic_says_why(self):
        from taxjson.lib import tax_logic as TL
        text = TL.catalog("canada")["CA-INC-DATE-TRUST"].text
        self.assertIn("IB prints no record date", text)
        self.assertIn("trust cannot be told from", text)


if __name__ == "__main__":
    unittest.main()
