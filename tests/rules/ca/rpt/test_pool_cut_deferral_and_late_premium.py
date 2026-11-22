"""What a sale reports of the denied losses in its ACB (CA-RPT-16: the
views count each trade's P/L before any denial, so a sale says how much
of its cost is an earlier denied loss) once a return of capital has cut
into them: the part still in the ACB (`deferred_loss_recovered`), the
part the capital returned took (`deferred_loss_cut`) and a put premium
delivered into the shares after the cut (`premium_after_cut`), each in
the share of the units sold, and each carried by a journal to the other
listing.

PLAN step 13, commit 0: pins for the pool's operations no test held (the
2026-11-17 mutation pass). Synthetic data only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import (
    contract, denied_loss, journal, rename, return_of_capital, run_canada, sale, trade)

TO, US = 'SYQ.TO', 'SYQ.US'
PUT = 'SYQ251219P00010000.TO'


def _cut():
    """100 units at 1000, 500 of it a denied loss; a return of capital of
    800 leaves an ACB of 200: 200 of the denied loss are still in it and
    300 went with the capital returned."""
    return denied_loss(TO) + [return_of_capital('2025-03-03', 800, sym=TO)]


def _put_assigned():
    """A put written for 120 and assigned: 100 more shares at 10, their
    cost lowered by the premium (s.49(3.1)) to 880."""
    return [contract('2025-03-10', -1, 120, PUT),
            contract('2025-04-01', 1, 0, PUT, action='ASSIGN'),
            trade('2025-04-01', 100, 1000, sym=TO)]


def _reported(rec):
    return tuple(round(rec.get(k) or 0.0, 2) for k in (
        'deferred_loss_recovered', 'deferred_loss_cut', 'premium_after_cut'))


class TestAPartSaleReportsItsShareOfTheCut(unittest.TestCase):

    @rule(R.CA_RPT_16, R.CA_ACB_06)
    def test_each_sale_reports_the_share_of_its_units(self):
        res, _ = run_canada(_cut() + [trade('2025-04-01', -40, 400, sym=TO),
                                      trade('2025-06-02', -60, 600, sym=TO)])
        first, second = sale(res, '2025-04-01', TO), sale(res, '2025-06-02', TO)
        self.assertEqual((round(first['cost'], 2), round(second['cost'], 2)),
                         (80.0, 120.0))
        # 40% then the remaining 60% of the 200 and of the 300: the two
        # sales report the whole of each, once.
        self.assertEqual(_reported(first), (80.0, 120.0, 0.0))
        self.assertEqual(_reported(second), (120.0, 180.0, 0.0))


class TestAPutPremiumDeliveredAfterTheCut(unittest.TestCase):

    @rule(R.CA_RPT_16, R.CA_OPT_06)
    def test_the_sale_reports_the_premium_with_the_cut(self):
        res, _ = run_canada(_cut() + _put_assigned()
                            + [trade('2025-06-02', -200, 2400, sym=TO)])
        rec = sale(res, '2025-06-02', TO)
        # ACB 200 + 880; the premium of 120 is in neither the ACB the
        # cut read nor the 300 it took: reported beside them.
        self.assertEqual((round(rec['cost'], 2), round(rec['gain'], 2)),
                         (1080.0, 1320.0))
        self.assertEqual(_reported(rec), (200.0, 300.0, 120.0))

    @rule(R.CA_RPT_16, R.CA_XLIST_01)
    def test_a_journal_carries_the_premium_with_the_units_share_of_the_cut(self):
        res, _ = run_canada(_cut() + _put_assigned()
                            + journal('2025-05-01', 80, TO, US)
                            + [trade('2025-06-02', -120, 1440, sym=TO),
                               trade('2025-06-03', -80, 960, sym=US)])
        # 120 of the 200 units stay, 80 are journaled: 60% and 40% of
        # the 200, of the 300 and of the 120.
        self.assertEqual(_reported(sale(res, '2025-06-02', TO)), (120.0, 180.0, 72.0))
        self.assertEqual(_reported(sale(res, '2025-06-03', US)), (80.0, 120.0, 48.0))

    @rule(R.CA_RPT_16, R.CA_OPT_06, R.CA_ACB_06)
    def test_a_second_return_of_capital_reads_the_premium_in_the_acb(self):
        # ACB 1080 (the 200 left and the 880 of the put's shares), 200 of
        # denied loss in it. A second return of capital of 900 leaves 180:
        # 20 more of the denied loss go with it (320 taken in all), and
        # that much of the put's 120 was in the ACB it read: 100 is left
        # to report beside the cut.
        res, _ = run_canada(_cut() + _put_assigned()
                            + [return_of_capital('2025-05-01', 900, sym=TO),
                               trade('2025-06-02', -200, 2400, sym=TO)])
        rec = sale(res, '2025-06-02', TO)
        self.assertEqual(round(rec['cost'], 2), 180.0)
        self.assertEqual(_reported(rec), (180.0, 320.0, 100.0))

    @rule(R.CA_RPT_16, R.CA_OPT_06, R.CA_ACB_06)
    def test_a_reversal_gives_back_part_of_the_cut_and_none_of_the_premium(self):
        # A put at a strike of 2: 100 shares for 200 less the 120, ACB
        # 280, of which 200 denied loss; 300 were taken. A return of
        # capital reversed by 50 makes the ACB 330: it holds 130 beyond
        # the 200, so 130 of the 300 come back (330 in the ACB, 170 still
        # taken). The put's 120 is reported whole.
        low_put = 'SYQ251219P00002000.TO'
        res, _ = run_canada(_cut() + [
            contract('2025-03-10', -1, 120, low_put),
            contract('2025-04-01', 1, 0, low_put, action='ASSIGN'),
            trade('2025-04-01', 100, 200, sym=TO),
            return_of_capital('2025-05-01', -50, sym=TO),
            trade('2025-06-02', -200, 2400, sym=TO)])
        rec = sale(res, '2025-06-02', TO)
        self.assertEqual(round(rec['cost'], 2), 330.0)
        self.assertEqual(_reported(rec), (330.0, 170.0, 120.0))


class TestASecondReturnOfCapitalAddsToWhatTheFirstTook(unittest.TestCase):

    @rule(R.CA_RPT_16, R.CA_ACB_06)
    def test_the_two_cuts_add_up(self):
        # 800 leaves an ACB of 200 and takes 300 of the 500 denied; 100
        # more leaves 100 and takes another 100: the sale reports 100
        # still in its cost and 400 taken.
        res, _ = run_canada(_cut() + [return_of_capital('2025-04-01', 100, sym=TO),
                                      trade('2025-06-02', -100, 1000, sym=TO)])
        rec = sale(res, '2025-06-02', TO)
        self.assertEqual((round(rec['cost'], 2), round(rec['gain'], 2)), (100.0, 900.0))
        self.assertEqual(_reported(rec), (100.0, 400.0, 0.0))


class TestAReturnOfCapitalBeyondTheAcb(unittest.TestCase):

    @rule(R.CA_ACB_07)
    def test_the_acb_is_nil_afterwards(self):
        # 100 bought for 1000; a return of capital of 1200 is a gain of
        # 200 that day and leaves an ACB of nil, not of -200: the sale for
        # 1500 gains 1500.
        res, _ = run_canada([trade('2025-01-06', 100, 1000, sym=TO),
                             return_of_capital('2025-03-03', 1200, sym=TO),
                             trade('2025-06-02', -100, 1500, sym=TO)])
        deemed = [r for r in res['transactions'] if r['date'] == '2025-03-03']
        self.assertEqual([round(r['gain'], 2) for r in deemed], [200.0])
        rec = sale(res, '2025-06-02', TO)
        self.assertEqual((round(rec['cost'], 2), round(rec['gain'], 2)), (0.0, 1500.0))


class TestTwoPoolsThatEachLostPartOfADeniedLossAreJoined(unittest.TestCase):
    """Each listing holds 100 at 1000, 500 of it a denied loss. A return
    of capital of 800 on the first leaves 200 of the denied loss in its
    ACB and takes 300; one of 700 on the second leaves 300 and takes 200.
    Joined, the pool reports what both hold and both lost: every denied
    dollar once, on the sales."""

    @staticmethod
    def _two(first, second):
        return (denied_loss(first) + [return_of_capital('2025-03-03', 800, sym=first)]
                + denied_loss(second) + [return_of_capital('2025-03-04', 700, sym=second)])

    @rule(R.CA_RPT_16, R.CA_XLIST_01)
    def test_a_journal_adds_the_units_share_to_what_the_other_listing_lost(self):
        # 60 of the first listing's 100 are journaled: 120 of its 200 and
        # 180 of its 300 go with them; the 40 left keep 80 and 120.
        res, _ = run_canada(self._two(TO, US) + journal('2025-05-01', 60, TO, US)
                            + [trade('2025-06-02', -40, 440, sym=TO),
                               trade('2025-06-03', -160, 960, sym=US)])
        self.assertEqual(_reported(sale(res, '2025-06-02', TO)), (80.0, 120.0, 0.0))
        self.assertEqual(_reported(sale(res, '2025-06-03', US)), (420.0, 380.0, 0.0))

    @rule(R.CA_RPT_16, R.CA_ACB_RENAME)
    def test_a_rename_adds_both(self):
        res, _ = run_canada(self._two('OLD.TO', 'NEW.TO')
                            + [rename('2025-05-01', 'OLD.TO', 'NEW.TO'),
                               trade('2025-06-03', -200, 1960, sym='NEW.TO')])
        self.assertEqual(_reported(sale(res, '2025-06-03', 'NEW.TO')), (500.0, 500.0, 0.0))

    @rule(R.CA_RPT_16, R.CA_XLIST_01, R.CA_OPT_06)
    def test_a_premium_delivered_after_one_cut_is_reported_once_after_the_join(self):
        # The first listing's 200 shares (its cut of 300, then the put's
        # 120) are all journaled to the second, which lost 200 of its own:
        # the sale of the 300 reports 500 taken and the one premium of 120.
        res, _ = run_canada(_cut() + _put_assigned()
                            + denied_loss(US) + [return_of_capital('2025-04-10', 700, sym=US)]
                            + journal('2025-05-01', 200, TO, US)
                            + [trade('2025-06-03', -300, 3600, sym=US)])
        self.assertEqual(_reported(sale(res, '2025-06-03', US)), (500.0, 500.0, 120.0))


if __name__ == '__main__':
    unittest.main()
