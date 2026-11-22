"""TJ-4481 (b): the wash radar's forward views read an empty claim ledger.

After Canada's claims, `decide` points the claims' ledger at the empty
today ledger (`_today_ledger`): a sale today is judged on its own
(CA-SL-08), so units that backed an earlier loss's denial still back a
sale today. Read with the last sale's claims instead, the forward views
lose those units: the taxable-only case below turns EXITABLE into CLEAR,
and the registered one counts none of the account's window units.

Synthetic data only (ZZ tickers, made-up prices).
"""
import json
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar_main, _row


def _rows(taxable, sheltered, as_of, ticker):
    """The ticker's rows in the radar's --all --json document."""
    doc = json.loads(_radar_main('canada', taxable, sheltered, as_of,
                                 ('--all', '--json')))
    return [r for s in doc['sections'] for r in s.get('rows') or []
            if r.get('ticker') == ticker]


class TestSaleTodayJudgedOnItsOwn(unittest.TestCase):
    """TJ-4481 (b): the reset of the claims' ledger before the forward
    views, pinned on two made-up books."""

    @rule(R.CA_SL_08)
    def test_a_rebuy_that_backed_a_denial_still_backs_a_sale_today(self):
        """Buy 300, sell 100 at a loss, buy 100 back inside the window.
        The rebuy backs the loss's denial; on 2024-04-02 it is still a
        buy within 30 days of a sale that day, so a partial sale at a
        loss would be superficial: EXITABLE, not CLEAR."""
        rows = _rows([_row('2024-01-10', 'ZZX.TO', 300, 20.0),
                      _row('2024-03-01', 'ZZX.TO', -100, 10.0),
                      _row('2024-03-11', 'ZZX.TO', 100, 10.0)],
                     None, '2024-04-02', 'ZZX.TO')
        self.assertEqual([r['category'] for r in rows], ['EXITABLE'])
        self.assertEqual(rows[0]['taxable_qty'], 300.0)

    @rule(R.CA_SL_08)
    def test_registered_window_units_count_after_a_sale_claimed_them(self):
        """Two loss sales; a TFSA bought 5 units in their window. The
        second sale, on the radar's date, counts the TFSA's units for its
        denial; that day's forward view still shows them as bought in a
        sale's window."""
        rows = _rows([_row('2024-01-10', 'ZZC.TO', 100, 20.0),
                      _row('2024-03-01', 'ZZC.TO', -20, 10.0),
                      _row('2024-03-05', 'ZZC.TO', -20, 9.0),
                      _row('2024-03-12', 'ZZC.TO', 20, 9.0)],
                     [_row('2024-02-25', 'ZZC.TO', 5, 12.0, acct='tfsa')],
                     '2024-03-05', 'ZZC.TO')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['registered_window_qty'], 5.0)
        self.assertEqual(rows[0]['registered_window_last_add'], '2024-02-25')


if __name__ == '__main__':
    unittest.main()
