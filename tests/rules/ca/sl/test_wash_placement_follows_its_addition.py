"""Where a denied loss's addition is applied (CA-SL-09, s.53(1)(f)): the
placement the solver keeps for each addition (`run.wash_place`, by the
ADJUST's id: PLAN step 13, TJ-4113).

Two things the placement must hold across the solver's passes:

* an addition placed where the position held at day 30 begins (a landing
  row, CA-SL-19) stays there: a later pass must not move it back to the
  loss sale, where shares since sold would take it;
* a denial the solver withdraws takes its additions' placements with it:
  none is left for a later addition of the same loss and trigger to find.

Synthetic data only.
"""
import contextlib
import io
import unittest
from unittest import mock

from taxjson.engine.ca import solve as solve_module
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R

OLD = 'OLD.TO'


def _tx(d, q, net, *, sym=OLD, t='10:00:00'):
    return TaxTransaction(
        action='BUYSELL', date=d, date_settle=d, time=t, symbol=sym,
        quantity=float(q), price=abs(float(net) / float(q)),
        net_amount=float(net), currency='CAD', account='margin')


def _rename(d, old, new):
    return TaxTransaction(action='SPLIT', date=d, date_settle=d,
                          time='00:00:01', symbol=old, symbol_new=new,
                          quantity=1.0, currency='CAD', account='margin')


def _run(tax):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(tax)
    return res, err.getvalue()


def _records(res):
    return [(e['date'], round(e['cost'], 2), round(e['gain'], 2),
             round(e.get('disallowed_amount') or 0.0, 2))
            for e in res['transactions'] if e.get('symbol') == OLD and 'gain' in e]


class TestALandedAdditionStaysWhereThePositionBegins(unittest.TestCase):

    @rule(R.CA_SL_09, R.CA_SL_19, R.CA_ACB_RENAME)
    def test_a_pre_loss_replacement_sold_out_before_the_shares_come_by_a_rename(self):
        # 100 bought 11-20 (inside the 30 days before the loss); 50 sold
        # 12-03 at a loss of 500; the other 50 sold 12-06 at a gain of 250;
        # the 100 held at day 30 come by a rename on 12-09 (cost 1500), no
        # purchase. The 500 is denied (50 sold, 100 acquired, 100 held) and
        # belongs to the position held at day 30: it waits on the flat pool
        # from the 12-06 sale and joins the renamed shares (2000). The
        # 12-06 sale's own cost is its 1000: placed after the loss sale,
        # the 500 would turn that gain into a loss, denied in turn.
        res, err = _run([_tx('2024-10-01', 100, 1500, sym='BBB.TO'),
                         _tx('2024-11-20', 100, 2000),
                         _tx('2024-12-03', -50, 500),
                         _tx('2024-12-06', -50, 1250),
                         _rename('2024-12-09', 'BBB.TO', OLD),
                         _tx('2025-06-02', -100, 1200)])
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        self.assertEqual(_records(res), [('2024-12-03', 1000.0, 0.0, 500.0),
                                         ('2024-12-06', 1000.0, 250.0, 0.0),
                                         ('2025-06-02', 2000.0, -800.0, 0.0)])
        self.assertEqual([(a['date'], a['symbol']) for w in res['wash_sales']
                          for a in w['adjusts']], [('2024-12-06', OLD)])


class TestAWithdrawnDenialLeavesNoPlacement(unittest.TestCase):

    @rule(R.CA_SL_08, R.CA_SL_09)
    def test_every_placement_names_an_addition_of_the_pass(self):
        # A two-fill sale with a pre-loss replacement: the second fill is a
        # loss for one pass (its denial and addition made) and a gain the
        # next (both withdrawn). From then on no placement is kept for the
        # withdrawn addition.
        seen = []
        place = solve_module._place_wash_adjusts

        def watched(stream, wash_place):
            seen.append((sorted(wash_place),
                         sorted(t.id for t in stream
                                if t.action == 'ADJUST' and str(t.id).startswith('WASH_'))))
            return place(stream, wash_place)

        with mock.patch.object(solve_module, '_place_wash_adjusts', watched):
            res, err = _run([_tx('2025-01-02', 100, 1000), _tx('2025-01-22', 100, 1000),
                             _tx('2025-01-27', -100, 900, t='10:00:00'),
                             _tx('2025-01-27', -50, 520, t='10:00:01'),
                             _tx('2025-04-01', -50, 550)])
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        self.assertEqual(_records(res), [('2025-01-27', 1000.0, -50.0, 50.0),
                                         ('2025-01-27', 500.0, 20.0, 0.0),
                                         ('2025-04-01', 550.0, 0.0, 0.0)])
        counts = [len(adjusts) for _placed, adjusts in seen]
        self.assertTrue(any(b < a for a, b in zip(counts, counts[1:])),
                        f'no pass withdrew an addition: {counts}')
        for placed, adjusts in seen:
            self.assertEqual(placed, adjusts)


if __name__ == '__main__':
    unittest.main()
