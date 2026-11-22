"""Re-audit-2 tests-tagging round: tests that pin a tax-logic clause no
@rule-tagged test used to kill (every book here is synthetic).

Formerly: test_fix_a2_tagging"""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def _row(d, q, px, acct='margin', sym='XYZ.TO', t='10:00:00', cur='CAD'):
    return TaxTransaction(action='BUYSELL', date=d, date_settle=d, time=t,
                          symbol=sym, quantity=float(q), price=float(px),
                          net_amount=round(abs(q * px), 2), currency=cur,
                          account=acct)


def _ca(tax, shel=None, aff=None):
    with contextlib.redirect_stderr(io.StringIO()):
        return CanadaTaxRules().compute_gains(
            tax, sheltered_transactions=shel, affiliated_transactions=aff)


def _sales(res):
    return [(e['date'], round(e['disallowed_amount'], 2),
             round(e.get('permanently_disallowed', 0.0), 2))
            for e in res['transactions'] if 'proceeds' in e]


class TestCaSameMomentHolderRank(unittest.TestCase):
    """A2-0487: CA-SL-10's same-moment tie-break (taxable, then sheltered,
    then affiliated) was pinned only by books where the export order
    already put the taxable row first. Purchases BEFORE the loss are
    matched latest first, so with no holder rank the last-listed
    (registered / affiliated) row of a same-moment pair would back the
    denial and make it permanent.

    Formerly: test_fix_a2_tagging.TestCaSameMomentHolderRank"""

    BOOK = [_row('2025-01-02', 100, 50), _row('2025-02-20', 50, 45),
            _row('2025-03-03', -50, 40)]

    @rule(R.CA_SL_10)
    def test_pre_loss_same_moment_taxable_before_tfsa(self):
        res = _ca(self.BOOK, [_row('2025-02-20', 50, 45, 'tfsa')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])

    @rule(R.CA_SL_10)
    def test_pre_loss_same_moment_taxable_before_affiliated(self):
        res = _ca(self.BOOK, aff=[_row('2025-02-20', 50, 45, 'spouse')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])

    @rule(R.CA_SL_10)
    def test_pre_loss_purchases_latest_first(self):
        # A2-0838 (SL10 mutant): the TFSA bought first, the taxable
        # account later, both before the loss: the LATER (taxable)
        # purchase backs the denial, so it is deferred, not permanent.
        tax = [_row('2025-01-02', 100, 50), _row('2025-02-20', 50, 45),
               _row('2025-03-03', -50, 40)]
        res = _ca(tax, [_row('2025-02-10', 50, 45, 'tfsa')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])
        # Bought the other way round, the later TFSA purchase backs it.
        tax[1] = _row('2025-02-10', 50, 45)
        res = _ca(tax, [_row('2025-02-20', 50, 45, 'tfsa')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 416.67)])



def _opt(d, q, px, sym='ABC250718C00045000.TO', **kw):
    r = _row(d, q, px, sym=sym, **kw)
    r.net_amount = round(abs(q * px * 100), 2)
    return r


class TestCaOptionLossReplacement(unittest.TestCase):
    """A2-0838 (SL06 mutants): CA-SL-06 — an option's loss is replaced
    only by the identical contract, never by the shares or another
    series.

    Formerly: test_fix_a2_tagging.TestCaOptionLossReplacement"""

    BOOK = [_opt('2025-02-03', 1, 5), _opt('2025-03-03', -1, 2)]

    def _denied(self, extra):
        return [round(e['disallowed_amount'], 2)
                for e in _ca(self.BOOK + extra)['transactions']
                if 'proceeds' in e]

    @rule(R.CA_SL_06)
    def test_shares_never_replace_an_option(self):
        self.assertEqual(self._denied([_row('2025-03-10', 100, 50,
                                                sym='ABC.TO')]), [0.0])

    @rule(R.CA_SL_06)
    def test_another_series_never_replaces_an_option(self):
        self.assertEqual(self._denied(
            [_opt('2025-03-10', 1, 1.5, 'ABC250718C00050000.TO')]), [0.0])
        self.assertEqual(self._denied(
            [_opt('2025-03-10', 1, 1.5, 'ABC251017C00045000.TO')]), [0.0])

    @rule(R.CA_SL_06)
    def test_the_identical_contract_does(self):
        self.assertEqual(self._denied([_opt('2025-03-10', 1, 1.5)]), [300.0])



if __name__ == '__main__':
    unittest.main()
