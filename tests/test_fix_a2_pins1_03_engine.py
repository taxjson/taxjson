"""Re-audit-2 test pins (tests-pins-03), the gains engines: each test
fails when the rule it names is reverted or mutated. Synthetic books
only (QZ* tickers, round numbers)."""
import unittest

import contextlib
import io

from tax_rules import rule
from tax_rules.dual import tx
from test_fix_m_engine import _by_year, _run, _tt

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


def _both(book):
    """The Canada book and its US twin (.TO -> .US, CAD -> USD)."""
    us = book.replace('.TO', '.US').replace(' CAD ', ' USD ')
    return (_run(CanadaTaxRules(), _tt(book))[0],
            _run(USATaxRules(), _tt(us))[0])


class TestAssignPremiumPairingLedger(unittest.TestCase):
    """A2-0510: each pairing rule of the ASSIGN premium ledger
    (_AssignPremiumLedger) — the exact-size preference (R1-178), the
    exact-size reservation (S070-18) and the direction filter on later
    legs (R1-28) — moves dollars when removed."""

    @rule("CA-OPT-08")
    @rule("US-OPT-05")
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

    @rule("CA-OPT-08")
    @rule("US-OPT-05")
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

    @rule("CA-OPT-08")
    @rule("US-OPT-05")
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

    @rule("CA-OPT-08")
    @rule("US-OPT-05")
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

    @rule("CA-OPT-08")
    @rule("US-OPT-05")
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

    @rule("US-OPT-05")
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


def _bs(date, qty, price, sym, cur, account='margin', settle=None,
        time='09:30:00'):
    return TaxTransaction(action='BUYSELL', date=date, time=time,
                          date_settle=settle or date, symbol=sym,
                          quantity=qty, price=price,
                          net_amount=round(abs(qty) * price, 2),
                          currency=cur, account=account)


def _gain_perm(res):
    gain = sum((g.get('gain') if g.get('gain') is not None
                else g.get('raw_gain') or 0.0)
               for g in res['transactions'] if g.get('raw_gain') is not None)
    perm = sum(g.get('permanently_disallowed') or 0.0
               for g in res['transactions'])
    return round(gain, 2), round(perm, 2)


def _quiet(engine, *a, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return engine.compute_gains(*a, **kw)


class TestSameMomentHolderRank(unittest.TestCase):
    """A2-0534 (Canada) / A2-0913, A2-0941 (US): purchases at the same
    moment go to the taxable account first, then sheltered, then
    affiliated — the rank decides, not the pre-pass row order."""

    def _pre_loss(self, engine, sfx, cur, p):
        """A taxable buy and a sheltered buy at one moment BEFORE the loss
        sale; both still held at day 30. The pre-loss walk is latest
        first, so without the rank the sheltered row (listed last) would
        be taken first and the denial made permanent."""
        s = f'XYZ.{sfx}'
        taxable = [_bs('2025-01-06', 100, 20.0, s, cur),
                   _bs('2025-02-10', 100, 15.0, s, cur),
                   _bs('2025-03-03', -100, 10.0, s, cur),
                   _bs('2025-06-02', -100, 12.0, s, cur)]
        shel = [_bs('2025-02-10', 100, p, s, cur, account='aa-tfsa')]
        return _gain_perm(_quiet(engine(), taxable,
                                 sheltered_transactions=shel))

    @rule("CA-SL-03", "CA-SL-10")
    def test_canada_pre_loss_same_moment_taxable_first(self):
        seen = {self._pre_loss(CanadaTaxRules, 'TO', 'CAD', p / 100)
                for p in range(1490, 1510)}
        self.assertEqual(seen, {(-1300.0, 0.0)})

    @rule("US-WASH-20")
    def test_usa_same_moment_taxable_first(self):
        """The US rule (Reg. 1.1091-1(c) order acquired; same moment:
        taxable, then IRA, then affiliated), with the IRA's label
        sorting first and the IRA row settling first."""
        seen = {self._pre_loss(USATaxRules, 'US', 'USD', p / 100)
                for p in range(1490, 1510)}
        self.assertEqual(seen, {(-1300.0, 0.0)})
        s = 'XYZ.US'
        for p in range(1045, 1065):
            taxable = [_bs('2025-01-06', 100, 20.0, s, 'USD'),
                       _bs('2025-03-03', -100, 10.0, s, 'USD'),
                       _bs('2025-03-10', 100, 10.5, s, 'USD',
                           settle='2025-03-12'),
                       _bs('2025-06-02', -100, 12.0, s, 'USD')]
            ira = [_bs('2025-03-10', 100, p / 100, s, 'USD',
                       account='aa-ira', settle='2025-03-11')]
            self.assertEqual(
                _gain_perm(_quiet(USATaxRules(), taxable,
                                  sheltered_transactions=ira)),
                (-850.0, 0.0))


class TestUsTaintedLongLotNeverFeeds1091(unittest.TestCase):
    """A2-0548: a sale drawing on a phantom (OPENING_BALANCE) lot is
    manual-reporting only; its loss never feeds §1091 (US-BASIS-04),
    even when an ADJUST raised the lot's basis so it can lose."""

    def _book(self, sell_rebuy):
        b = [tx("OPENING_BALANCE", "2025-01-02", "PHL.US", 100, 0),
             tx("ADJUST", "2025-02-03", "PHL.US", 0, 1000),
             tx("BUYSELL", "2025-03-03", "PHL.US", -100, 500),
             tx("BUYSELL", "2025-03-10", "PHL.US", 100, 520)]
        if sell_rebuy:
            b.append(tx("BUYSELL", "2025-03-20", "PHL.US", -100, 530))
        return _quiet(USATaxRules(), b)

    @rule("US-BASIS-04")
    def test_rebuy_held(self):
        r = self._book(False)
        self.assertEqual(r['summary']['total_disallowed'], 0)
        self.assertFalse(r.get('wash_sales'))

    @rule("US-BASIS-04")
    def test_rebuy_sold_keeps_its_own_basis(self):
        r = self._book(True)
        self.assertEqual(r['summary']['total_disallowed'], 0)
        clean = [t for t in r['transactions']
                 if t.get('date') == '2025-03-20' and 'gain' in t]
        self.assertEqual(len(clean), 1)
        self.assertAlmostEqual(clean[0]['gain'], 10.0, places=2)


class TestUsSharesNeverReplaceAnOption(unittest.TestCase):
    """A2-0549: share for share (US-WASH-02); only the identical option
    replaces an option loss (US-WASH-03)."""
    OPT = "XYZ251219C00050000.US"

    def _dis(self, book):
        return round(_quiet(USATaxRules(), book)['summary']
                     ['total_disallowed'], 2)

    @rule("US-WASH-02")
    def test_share_buy_after_closed_option_loss(self):
        self.assertEqual(self._dis([
            tx("BUYSELL", "2025-03-03", self.OPT, 2, 1000),
            tx("BUYSELL", "2025-04-01", self.OPT, -2, 400),
            tx("BUYSELL", "2025-04-10", "XYZ.US", 100, 5000)]), 0)

    @rule("US-WASH-02", "US-WASH-17")
    def test_share_buy_with_option_partly_held(self):
        """The contract kept from the same purchase (bought 29 days
        before the loss) replaces one of the two sold; the 100 shares
        replace nothing."""
        self.assertEqual(self._dis([
            tx("BUYSELL", "2025-03-03", self.OPT, 3, 1500),
            tx("BUYSELL", "2025-04-01", self.OPT, -2, 400),
            tx("BUYSELL", "2025-04-10", "XYZ.US", 100, 5000)]), 300.0)

    @rule("US-WASH-03")
    def test_identical_option_rebought_is_a_replacement(self):
        self.assertEqual(self._dis([
            tx("BUYSELL", "2025-03-03", self.OPT, 2, 1000),
            tx("BUYSELL", "2025-04-01", self.OPT, -2, 400),
            tx("BUYSELL", "2025-04-10", self.OPT, 2, 500)]), 600.0)


class TestCanadaSharesNeverReplaceAPartlyHeldOption(unittest.TestCase):
    @rule("CA-SL-06")
    def test_share_buy_in_window_with_call_still_held(self):
        """A2-0919: two calls bought, one sold at a loss, the other held
        at day 30, 100 shares bought in the window: no denial."""
        c = "ZZQ260619C00050000.TO"
        book = [tx("BUYSELL", "2025-01-02", c, 2, 1000.0, currency="CAD"),
                tx("BUYSELL", "2025-03-10", c, -1, 200.0, currency="CAD"),
                tx("BUYSELL", "2025-03-12", "ZZQ.TO", 100, 5000.0,
                   currency="CAD")]
        r = _quiet(CanadaTaxRules(), book)
        rows = [g for g in r['transactions'] if g.get('symbol') == c
                and g.get('raw_gain') is not None]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['gain'], -300.0, places=2)
        self.assertAlmostEqual(rows[0].get('disallowed_amount') or 0.0, 0.0)


class TestUsRocExcessTerm(unittest.TestCase):
    @rule("US-ROC-02")
    def test_excess_on_a_lot_held_over_a_year_is_long_term(self):
        """A2-0921: the §301(c)(3) gain takes the lot's holding period."""
        r = _quiet(USATaxRules(), [
            tx("BUYSELL", "2023-01-03", "RCX.US", 100, 1000.0),
            tx("ADJUST", "2025-06-02", "RCX.US", 0, -1500.0)])
        rows = [g for g in r['transactions'] if not g.get('action')
                and g.get('date') == '2025-06-02']
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['gain'], 500.0, places=2)
        self.assertEqual(rows[0]['term'], 'LONG_TERM')


class TestCanadaQuantityTolerances(unittest.TestCase):
    """A2-1548: CA-CRYPTO-09's two stated numbers — under a millionth of
    a share is zero; a coin residue is zero only under a hundred-
    billionth of the position."""

    def _left(self, sym, buy, sell):
        r = _quiet(CanadaTaxRules(), [
            tx("BUYSELL", "2025-01-02", sym, buy, 1000.0, currency="CAD"),
            tx("BUYSELL", "2025-02-03", sym, -sell, 1200.0, currency="CAD")])
        return sum(float(i.get('qty') or 0) for i in r.get('inventory', [])
                   if i.get('symbol') == sym)

    @rule("CA-CRYPTO-09")
    def test_share_residue_around_a_millionth(self):
        self.assertAlmostEqual(self._left("QZA.TO", 10, 10 - 5e-6), 5e-6,
                               delta=1e-9)
        self.assertEqual(self._left("QZA.TO", 10, 10 - 5e-7), 0)

    @rule("CA-CRYPTO-09")
    def test_coin_residue_around_a_hundred_billionth(self):
        self.assertAlmostEqual(self._left("BTC", 10, 10 - 5e-9), 5e-9,
                               delta=1e-12)
        self.assertEqual(self._left("BTC", 10, 10 - 5e-11), 0)


if __name__ == "__main__":
    unittest.main()
