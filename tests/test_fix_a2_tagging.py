"""Re-audit-2 tests-tagging round: tests that pin a tax-logic clause no
@rule-tagged test used to kill (every book here is synthetic)."""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from tax_rules import rule


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
    denial and make it permanent."""

    BOOK = [_row('2025-01-02', 100, 50), _row('2025-02-20', 50, 45),
            _row('2025-03-03', -50, 40)]

    @rule("CA-SL-10")
    def test_pre_loss_same_moment_taxable_before_tfsa(self):
        res = _ca(self.BOOK, [_row('2025-02-20', 50, 45, 'tfsa')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])

    @rule("CA-SL-10")
    def test_pre_loss_same_moment_taxable_before_affiliated(self):
        res = _ca(self.BOOK, aff=[_row('2025-02-20', 50, 45, 'spouse')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])


if __name__ == '__main__':
    unittest.main()
