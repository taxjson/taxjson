"""Bug-review batch C (income): dividend estimation, income dating and
the year hand-off (findings 38, 49, 65, 76, 77, 92 of the 2026-10
review).

All data is synthetic (made-up tickers and accounts).

Formerly: test_fix_batch_c_income
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


def _div(d, sym, qty, per, acct="margin", cur="CAD", **kw):
    r = {"action": "DIVIDEND", "date": d, "date_settle": d, "symbol": sym,
         "quantity": qty, "gross_amount": qty * per,
         "net_amount": qty * per, "currency": cur, "account": acct}
    r.update(kw)
    return r


class TestDivEstimateSchedule(unittest.TestCase):
    """Formerly: test_fix_batch_c_income.TestDivEstimateSchedule"""

    @rule(R.CA_PLAN_06)
    def test_38_staggered_weekly_postings_stay_weekly(self):
        """Two accounts post a weekly payer three days apart: four weekly
        payments of 1/share, not one chained payment of 4/share."""
        from taxjson.lib import div_estimate as DE
        rows = []
        for acct, dates in (("margin", ["2025-01-06", "2025-01-13",
                                        "2025-01-20", "2025-01-27"]),
                            ("other", ["2025-01-09", "2025-01-16",
                                       "2025-01-23", "2025-01-30"])):
            rows += [(acct, _div(d, "ZZW.TO", 100, 1.0, acct=acct))
                     for d in dates]
        sched = DE.schedule(DE.payments(rows)["ZZW.TO"])
        self.assertEqual([round(p.per_share, 6) for p in sched],
                         [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(
            DE.detect_frequency([p.pay_date for p in sched])[0], "weekly")
        # One account's special beside its regular payment, two days
        # apart, is still one payment of both.
        rows = [("margin", _div("2025-03-14", "ZZS.TO", 10, 1.0)),
                ("margin", _div("2025-03-16", "ZZS.TO", 10, 0.5))]
        sched = DE.schedule(DE.payments(rows)["ZZS.TO"])
        self.assertEqual([round(p.per_share, 6) for p in sched], [1.5])

    @rule(R.CA_PLAN_06)
    def test_92_two_broker_accounts_in_one_account(self):
        """combined_broker_accounts: each broker account's 10 shares
        receive 10 on one day: 1/share, not 2."""
        from taxjson.lib import div_estimate as DE
        rows = [("margin", _div("2025-02-03", "ZZU.US", 10, 1.0, cur="USD",
                                source_account=src))
                for src in ("synthetic-a", "synthetic-b")]
        rows.append(("margin", {"action": "TAX", "type": "tax",
                                "date": "2025-02-03", "symbol": "ZZU.US",
                                "net_amount": 1.5, "currency": "USD",
                                "account": "margin",
                                "source_account": "synthetic-a"}))
        pays = DE.payments(rows)["ZZU.US"]
        self.assertEqual(sorted(p.per_share for p in pays), [1.0, 1.0])
        self.assertEqual(sorted(p.tax for p in pays), [0.0, 1.5])
        sched = DE.schedule(pays)
        self.assertEqual([p.per_share for p in sched], [1.0])
        # A reversal and its re-posting in one broker account net there,
        # not against the other broker account's payment.
        rows = [("margin", _div("2025-02-03", "ZZU.US", 10, 1.0, cur="USD",
                                source_account=src))
                for src in ("synthetic-a", "synthetic-b")]
        rows.append(("margin", _div("2025-02-05", "ZZU.US", 10, -1.0,
                                    cur="USD",
                                    source_account="synthetic-a")))
        pays = DE.payments(rows)["ZZU.US"]
        self.assertEqual([(p.source, p.per_share) for p in pays],
                         [("synthetic-b", 1.0)])


if __name__ == "__main__":
    unittest.main()
