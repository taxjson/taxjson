"""A rename class with two listings in it: each holder's units are the class's.

One source renamed into two targets on one date joins both targets into
one rename class (`SplitTimeline.class_at`): the TSX account books ZQA.TO
as ZQB.TO, the TFSA's US-listing rename (ZQA.US, which a TOBASE line books
as ZQA.TO) as ZQB.US. Both rows' VIOLATION reads each holder's units
across the class (`_Pos._held_by_holder`), not the row's listing alone.

Synthetic data only (ZQ tickers, made-up prices).
"""
import json
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar_main, _row


def _rename(d, old, new, acct, currency):
    return dict(_row(d, old, 1.0, 0.0, acct=acct, currency=currency),
                action="SPLIT", symbol_new=new, net_amount=0.0,
                time="00:00:00", id=f"{acct}-{old}-{d}-rename")


def _rows(taxable, sheltered):
    doc = json.loads(_radar_main('canada', taxable, sheltered, '2024-03-12',
                                 ('--all', '--json')))
    return {r['ticker']: r for s in doc['sections']
            for r in s.get('rows') or []}


_TSX = [_row('2024-01-10', 'ZQA.TO', 100, 20.0),
        _rename('2024-03-01', 'ZQA.TO', 'ZQB.TO', 'margin', 'CAD'),
        _row('2024-03-05', 'ZQB.TO', -60, 10.0)]


class TestOneClassTwoListings(unittest.TestCase):

    @rule(R.CA_PLAN_01)
    def test_taxable_rescue_counts_both_listings(self):
        """A second taxable account's US listing rebuys in the window:
        each row's rescue sells the taxable book's 120 units (40 ZQB.TO
        and 80 ZQB.US), not the row's listing alone (40 or 80). Each
        holder's total is asserted, so a rescue that names each listing
        apart (TJ-4577) still holds it."""
        rows = _rows(_TSX + [
            _row('2024-01-12', 'ZQA.TO', 50, 15.0, acct='usd', currency='USD'),
            _rename('2024-03-01', 'ZQA.TO', 'ZQB.US', 'usd', 'USD'),
            _row('2024-03-08', 'ZQB.US', 30, 8.0, acct='usd', currency='USD')],
            None)
        for t in ('ZQB.TO', 'ZQB.US'):
            self.assertEqual(rows[t]['category'], 'VIOLATION')
            total = {}
            for b in rows[t]['rescue']:
                total[b['holder']] = total.get(b['holder'], 0.0) + b['qty']
            self.assertEqual(total, {'taxable': 120.0}, t)

    @rule(R.CA_PLAN_01)
    def test_registered_buyer_of_the_other_listing_backs_the_row(self):
        """The TFSA bought ZQB.US in the window and holds 30: the ZQB.TO
        row names it as the units to sell and as the recent buy."""
        rows = _rows(_TSX, [
            _row('2024-01-15', 'ZQA.TO', 10, 15.0, acct='tfsa', currency='USD'),
            _rename('2024-03-01', 'ZQA.TO', 'ZQB.US', 'tfsa', 'USD'),
            _row('2024-03-08', 'ZQB.US', 20, 8.0, acct='tfsa', currency='USD')])
        row = rows['ZQB.TO']
        self.assertEqual(row['category'], 'VIOLATION')
        self.assertEqual([(b['holder'], b['account'], b['qty'])
                          for b in row['rescue']], [('sheltered', 'tfsa', 30.0)])
        self.assertEqual(row['recent_buy'], {'account': 'tfsa',
                                             'date': '2024-03-08'})

    @rule(R.CA_PLAN_01)
    def test_locked_counts_the_class_taxable_units(self):
        """No loss in the window; the TFSA bought 120 ZQB.US in it. A loss
        sale today of either row is superficial for up to 120 units of
        the taxable book's 150 (100 ZQB.TO and 50 ZQB.US): the forward
        view reads the class's taxable units, not the row's listing
        alone (100 or 50)."""
        rows = _rows(
            [_row('2024-01-10', 'ZQA.TO', 100, 20.0),
             _rename('2024-03-01', 'ZQA.TO', 'ZQB.TO', 'margin', 'CAD'),
             _row('2024-01-12', 'ZQA.TO', 50, 15.0, acct='usd', currency='USD'),
             _rename('2024-03-01', 'ZQA.TO', 'ZQB.US', 'usd', 'USD')],
            [_row('2024-01-15', 'ZQA.TO', 10, 15.0, acct='tfsa', currency='USD'),
             _rename('2024-03-01', 'ZQA.TO', 'ZQB.US', 'tfsa', 'USD'),
             _row('2024-03-08', 'ZQB.US', 120, 8.0, acct='tfsa', currency='USD')])
        for t in ('ZQB.TO', 'ZQB.US'):
            self.assertEqual(rows[t]['category'], 'LOCKED', t)
            self.assertEqual(rows[t]['at_risk_qty'], 120.0, t)
            self.assertIn('up to 120 of your 150 shares', rows[t]['advisory'], t)


if __name__ == '__main__':
    unittest.main()
