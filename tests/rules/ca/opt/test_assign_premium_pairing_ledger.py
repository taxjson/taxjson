"""Re-audit-2 test pins (tests-pins-03), the gains engines: each test
fails when the rule it names is reverted or mutated. Synthetic books
only (QZ* tickers, round numbers).

Formerly: test_fix_a2_pins1_03_engine"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as RID
from _support.engine_years import _by_year, _run, _tt

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules


def _both(book):
    """The Canada book and its US twin (.TO -> .US, CAD -> USD)."""
    us = book.replace('.TO', '.US').replace(' CAD ', ' USD ')
    return (_run(CanadaTaxRules(), _tt(book))[0],
            _run(USATaxRules(), _tt(us))[0])


class TestAssignPremiumPairingLedger(unittest.TestCase):
    """A2-0510: each pairing rule of the ASSIGN premium ledger
    (_AssignPremiumLedger) — the exact-size preference (R1-178), the
    exact-size reservation (S070-18) and the direction filter on later
    legs (R1-28) — moves dollars when removed.

    Formerly: test_fix_a2_pins1_03_engine.TestAssignPremiumPairingLedger"""

    @rule(RID.CA_OPT_08)
    @rule(RID.US_OPT_05)
    def test_exact_size_entry_preferred(self):
        """A 1-lot and a 2-lot written put assigned at one moment, one
        200-share stock leg: the 2-lot premium is the leg's."""
        ca, us = _both("""
            BUYSELL 2025-11-03 10:00:00 QZM251219P00050000.TO -1 CAD 1 100
            BUYSELL 2025-11-03 10:00:00 QZM251219P00048000.TO -2 CAD 3 600
            ASSIGN 2025-12-19 16:20:00 QZM251219P00050000.TO 1 CAD 0 0
            ASSIGN 2025-12-19 16:20:00 QZM251219P00048000.TO 2 CAD 0 0
            BUYSELL 2025-12-19 16:20:00 QZM.TO 200 CAD 48 9600
            BUYSELL 2026-02-02 10:00:00 QZM.TO -200 CAD 60 12000
        """)
        self.assertEqual(_by_year(ca), {'2026': 3000.0})
        self.assertEqual(_by_year(us), {'2026': 3000.0})

    @rule(RID.CA_OPT_08)
    @rule(RID.US_OPT_05)
    def test_exact_size_leg_keeps_its_entry(self):
        """A 1-lot and a 3-lot written put assigned the same day; the
        stock arrives as 200 + 100 + 100. The 100-share entry is
        reserved for an exact-size leg, so the 200-share leg takes only
        the 3-lot's share."""
        ca, us = _both("""
            BUYSELL 2025-11-03 10:00:00 QZN251219P00050000.TO -1 CAD 1 100
            BUYSELL 2025-11-03 10:00:00 QZN251219P00048000.TO -3 CAD 3 900
            ASSIGN 2025-12-19 16:20:00 QZN251219P00050000.TO 1 CAD 0 0
            ASSIGN 2025-12-19 16:20:00 QZN251219P00048000.TO 3 CAD 0 0
            BUYSELL 2025-12-19 16:20:00 QZN.TO 200 CAD 48 9600
            BUYSELL 2025-12-19 16:20:01 QZN.TO 100 CAD 50 5000
            BUYSELL 2025-12-19 16:20:02 QZN.TO 100 CAD 48 4800
            BUYSELL 2026-02-02 10:00:00 QZN.TO -200 CAD 60 12000
        """)
        self.assertEqual(_by_year(ca), {'2026': 2800.0})
        self.assertEqual(_by_year(us), {'2026': 3000.0})

    @rule(RID.CA_OPT_08)
    @rule(RID.US_OPT_05)
    def test_later_leg_of_the_other_direction_is_not_waited_for(self):
        """A written call assigned for 2 contracts with one 100-share SELL
        leg, and an unrelated same-day 100-share BUY: the whole premium
        goes to the sell leg's proceeds."""
        ca, us = _both("""
            BUYSELL 2026-01-05 10:00:00 QZW.TO 100 CAD 50 5000
            BUYSELL 2026-02-02 10:00:00 QZW260320C00060000.TO -2 CAD 3 600
            ASSIGN 2026-03-20 16:00:00 QZW260320C00060000.TO 2 CAD 0 0
            ASSIGN 2026-03-20 16:00:01 QZW.TO -100 CAD 60 6000
            BUYSELL 2026-03-20 16:00:02 QZW.TO 100 CAD 60 6000
            BUYSELL 2027-02-10 10:00:00 QZW.TO -100 CAD 60 6000
        """)
        self.assertEqual(_by_year(ca), {'2026': 1600.0, '2027': 0.0})
        self.assertEqual(_by_year(us), {'2026': 1600.0, '2027': 0.0})

    # The three books above pair every leg by identity (_pair_assign_
    # legs, A2-0050), so the ledger's proximity rules no longer decide
    # them. The books below reach the proximity rules.

    @rule(RID.CA_OPT_08)
    @rule(RID.US_OPT_05)
    def test_proximity_prefers_the_exact_size_entry(self):
        """Three written puts assigned; one 300-share leg at the 48
        strike pairs with the 48 put by identity, and the remaining 200
        shares take the 2-lot (exact-size) entry, not the 1-lot staged
        before it. The 1-lot's premium is left unconsumed and named."""
        book = """
            BUYSELL 2025-11-03 10:00:00 QZM251219P00052000.TO -1 CAD 1 100
            BUYSELL 2025-11-03 10:00:00 QZM251219P00050000.TO -2 CAD 2 400
            BUYSELL 2025-11-03 10:00:00 QZM251219P00048000.TO -1 CAD 3 300
            ASSIGN 2025-12-19 16:20:00 QZM251219P00052000.TO 1 CAD 0 0
            ASSIGN 2025-12-19 16:20:01 QZM251219P00050000.TO 2 CAD 0 0
            ASSIGN 2025-12-19 16:20:02 QZM251219P00048000.TO 1 CAD 0 0
            BUYSELL 2025-12-19 16:20:03 QZM.TO 300 CAD 48 14400
            BUYSELL 2026-02-02 10:00:00 QZM.TO -300 CAD 60 18000
        """
        for R, b in ((CanadaTaxRules(), book),
                     (USATaxRules(), book.replace('.TO', '.US')
                      .replace(' CAD ', ' USD '))):
            res, err = _run(R, _tt(b))
            self.assertEqual(_by_year(res), {'2026': 4300.0})
            self.assertIn("unconsumed option-assignment adjustment", err)
            self.assertIn("-100.00", err)

    @rule(RID.CA_OPT_08)
    @rule(RID.US_OPT_05)
    def test_exact_size_later_leg_reserves_its_entry(self):
        """Two 1-lot written puts; a 150-share buy (no identity sign)
        then a 100-share buy at the 48 strike. The 52 put's entry is
        held for the exact-size leg, so the 150-share trade never
        absorbs it; when that leg is the 48 put's own, the 52 premium
        stays unconsumed and is named — never folded silently."""
        book = """
            BUYSELL 2025-11-03 10:00:00 QZN251219P00052000.TO -1 CAD 1 100
            BUYSELL 2025-11-03 10:00:00 QZN251219P00048000.TO -1 CAD 3 300
            ASSIGN 2025-12-19 16:20:00 QZN251219P00052000.TO 1 CAD 0 0
            ASSIGN 2025-12-19 16:20:01 QZN251219P00048000.TO 1 CAD 0 0
            BUYSELL 2025-12-19 16:20:02 QZN.TO 150 CAD 49 7350
            BUYSELL 2025-12-20 10:00:00 QZN.TO 100 CAD 48 4800
            BUYSELL 2026-02-02 10:00:00 QZN.TO -250 CAD 60 15000
        """
        for R, b in ((CanadaTaxRules(), book),
                     (USATaxRules(), book.replace('.TO', '.US')
                      .replace(' CAD ', ' USD '))):
            res, err = _run(R, _tt(b))
            self.assertEqual(_by_year(res), {'2026': 3150.0})
            self.assertIn("unconsumed option-assignment adjustment", err)

    @rule(RID.US_OPT_05)
    def test_opposite_direction_entry_taken_by_the_first_leg(self):
        """A written call's premium (a SELL-direction entry) with only
        BUY legs after it (a parser whose sign disagrees): no SELL leg
        is coming, so the first buy takes it — a later BUY leg does not
        hold it back (_later_legs filters by direction). US FIFO lots
        make the year visible."""
        us, _err = _run(USATaxRules(), _tt("""
            BUYSELL 2026-01-05 10:00:00 QZW.US 100 USD 50 5000
            BUYSELL 2026-02-02 10:00:00 QZW260320C00060000.US -2 USD 3 600
            ASSIGN 2026-03-20 16:00:00 QZW260320C00060000.US 2 USD 0 0
            BUYSELL 2026-03-20 16:00:01 QZW.US 100 USD 60 6000
            BUYSELL 2026-03-21 10:00:00 QZW.US 100 USD 61 6100
            BUYSELL 2026-04-01 10:00:00 QZW.US -200 USD 70 14000
            BUYSELL 2027-02-10 10:00:00 QZW.US -100 USD 70 7000
        """))
        self.assertEqual(_by_year(us), {'2026': 3600.0, '2027': 900.0})


if __name__ == "__main__":
    unittest.main()
