"""Re-audit-2 test pins (tests-pins-03), the gains engines: each test
fails when the rule it names is reverted or mutated. Synthetic books
only (QZ* tickers, round numbers).

Formerly: test_fix_a2_pins1_03_engine"""
import unittest

import contextlib
import io

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as RID

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction


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
    affiliated — the rank decides, not the pre-pass row order.

    Formerly: test_fix_a2_pins1_03_engine.TestSameMomentHolderRank"""

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

    @rule(RID.CA_SL_03, RID.CA_SL_10)
    def test_canada_pre_loss_same_moment_taxable_first(self):
        seen = {self._pre_loss(CanadaTaxRules, 'TO', 'CAD', p / 100)
                for p in range(1490, 1510)}
        self.assertEqual(seen, {(-1300.0, 0.0)})

    @rule(RID.US_WASH_20)
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


class TestCanadaSharesNeverReplaceAPartlyHeldOption(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_03_engine.TestCanadaSharesNeverReplaceAPartlyHeldOption"""
    @rule(RID.CA_SL_06)
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


if __name__ == "__main__":
    unittest.main()
