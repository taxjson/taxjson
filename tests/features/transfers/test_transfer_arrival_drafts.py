"""Backlog group `missing` (2026-10-25): find-missing-history, its drafts
and transfers.

TJ-0008  a transfer row stating a book value of 0 was drafted as a $0.00
         purchase; filled in with its date only it booked a $0 lot. The
         line now leaves the cost as COST (the run refuses it until it is
         filled in), and a row with no id no longer says "(row ?)".
TJ-0009  a transfer-in the run reports PARTLY covered by .tt purchases
         was "not drafted: ... a line would count it twice": the other
         units are drafted now, at COST, with a CHECK.
TJ-0017  a grant-timed written option sold with no purchase in the
         files was called "NOT in the totals" while its premium was.
TJ-0018  one draft line per sale that went short: more than the deepest
         short when a purchase in the files came between two of them.
TJ-0043  `tjs transfers`' `! ` lines were over 100 columns and wrapped.
TJ-0044  the run's closing list named one $0 lot twice (part sold, part
         held): "2 positions at a $0 cost (SAMPK.TO, SAMPK.TO)".
TJ-0055  `tjs transfers` TYPE showed the broker's free text (with the
         other account's number) instead of a transfer type.
TJ-0074  the drafts' slower own-move tier paired a leg of an in-kind
         move (to a registered account) with an outside arrival.
TJ-0077  find-missing-history, sanity and transfers said nothing about
         books older than the inputs, and --write-missing-history wrote
         an OPENING line for a position a just-added purchase fixes.
TJ-0175  the --write-missing-history note was one unwrapped line.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_backlog_missing
"""
import unittest

from _style import CapturedWidth

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.missing_history import COST_PLACEHOLDER, format_purchase_drafts
from taxjson.lib.purchase_drafts import draft_purchases
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _transfer(sym, d, q, desc, acct="margin", **kw):
    return dict(action="TRANSFER", date=d, symbol=sym, quantity=float(q),
                currency="CAD", account=acct, description=desc, **kw)


# ------------------------------------------------------------- the drafts
class TestTransferDrafts(unittest.TestCase):
    """Formerly: test_fix_backlog_missing.TestTransferDrafts"""

    def test_tj0008_a_stated_zero_leaves_the_cost_to_fill_in(self):
        sale = TaxTransaction(action="BUYSELL", date="2024-09-10",
                              symbol="SAMPK.TO", quantity=-10,
                              net_amount=115.05, currency="CAD",
                              account="margin")
        rows = [_transfer("SAMPK.TO", "2024-06-03", 40,
                          "SAMPLE K CORP TRANSFER BOOK VALUE 0.00",
                          book_value=0.0)]
        drafts, gaps = draft_purchases([sale], country="canada",
                                       transfer_rows=rows)
        d, = drafts
        self.assertIsNone(d.cost)
        line = d.tt_line().split()
        self.assertEqual(line[6:8], [COST_PLACEHOLDER, COST_PLACEHOLDER])
        notes = " ".join(d.comments)
        self.assertIn("book value of 0", notes)
        self.assertNotIn("(row ?)", notes)
        # (a stated 0 is no average cost: that note is left out)
        self.assertNotIn("average cost", notes)
        self.assertEqual(gaps, [])
        # A stated book value is still the line's cost.
        rows[0]["description"] = "SAMPLE K CORP TRANSFER BOOK VALUE 600.00"
        d, = draft_purchases([sale], country="canada",
                             transfer_rows=rows)[0]
        self.assertEqual(d.cost, 600.0)

    def test_tj0009_a_partly_covered_arrival_drafts_the_other_units(self):
        tt = TaxTransaction(action="BUYSELL", date="2021-03-15",
                            symbol="SAMPL.TO", quantity=10, net_amount=150,
                            currency="CAD", account="margin",
                            source="old.tt")
        sale = TaxTransaction(action="BUYSELL", date="2024-10-01",
                              symbol="SAMPL.TO", quantity=-5,
                              net_amount=120.05, currency="CAD",
                              account="margin")
        rows = [_transfer("SAMPL.TO", "2024-06-03", 20,
                          "SAMPLE L CORP TRANSFER BOOK VALUE 400.00",
                          book_value=400.0)]
        drafts, gaps = draft_purchases([tt, sale], country="canada",
                                       transfer_rows=rows)
        self.assertEqual(gaps, [])
        d, = drafts
        self.assertEqual((d.quantity, d.cost, d.warn), (10.0, None, True))
        notes = " ".join(d.comments)
        self.assertIn("already hold 10 of these 20 units", notes)
        self.assertNotIn("count it twice", notes)
        text = format_purchase_drafts(drafts, gaps, country="canada",
                                      account="margin")
        self.assertIn("SAMPL.TO  10  CAD  COST  COST", text)

    def test_tj0074_an_in_kind_leg_is_no_slower_own_move(self):
        # margin's transfer-out went to the RRSP (an in-kind move, not
        # booked); qb's arrival 15 days later is from outside.
        sale = TaxTransaction(action="BUYSELL", date="2024-06-03",
                              symbol="ZZQ.TO", quantity=-50,
                              net_amount=145.05, currency="CAD",
                              account="qb")
        rows = [_transfer("ZZQ.TO", "2024-03-01", -50,
                          "ZZQ CORP TFER TO 99900002"),
                _transfer("ZZQ.TO", "2024-03-16", 50,
                          "ZZQ CORP TRANSFER BOOK VALUE 0.00", acct="qb",
                          book_value=0.0)]
        drafts, gaps = draft_purchases([sale], country="canada",
                                       transfer_rows=rows)
        self.assertEqual(drafts, [])
        self.assertIn("a move from your account margin",
                      " ".join(g.reason for g in gaps))
        drafts, gaps = draft_purchases(
            [sale], country="canada", transfer_rows=rows,
            in_kind_legs={("margin", "ZZQ.TO", "2024-03-01", -50.0)})
        self.assertEqual([(d.account, d.quantity) for d in drafts],
                         [("qb", 50.0)])
        self.assertNotIn("move from your account",
                         " ".join(g.reason for g in gaps))


if __name__ == "__main__":
    unittest.main()
