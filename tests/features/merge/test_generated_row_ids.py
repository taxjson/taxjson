"""A row's id tells separate events apart: the corporate event a generated
row belongs to and a rename's destination are part of it, so two events'
rows never collapse in the dedup, repeated copies of one event still do,
and two copies of a rename that disagree on its destination are refused,
both named (backlog TJ-4985, TJ-1348).

Synthetic rows only (made-up tickers and event ids).
"""
import contextlib
import io
import unittest

from taxjson.bin.taxjson_sort import (RenameDestinationConflict, deduplicate,
                                      plan_dedup, rename_conflicts)
from taxjson.lib.cli_diag import InputContentError
from taxjson.lib.corp_actions import CorporateAction, resolve_event
from taxjson.lib.tt_format import compute_tt_id
from taxjson.model.transaction import TaxTransaction


def _spin(event_id, qty, country, election, hints):
    """The resolver's rows of one spin-off of QZSP from QZRX on 2025-04-01."""
    ev = CorporateAction(
        date='2025-04-01', time='09:30:00', action_type='spinoff',
        source_symbol='QZRX', source_isin='', target_symbol='QZSP',
        target_isin='', ratio_new=1, ratio_old=10, qty_disposed=0.0,
        qty_received=qty, fmv=0.0, currency='USD', target_currency='USD',
        account='b', event_id=event_id)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, election, country=country, hints=hints)
    return [TaxTransaction(**r) for r in rows]


def _rename(new, source='', **kw):
    return TaxTransaction(action='SPLIT', date='2025-03-03', symbol='QZO.US',
                          quantity=1.0, currency='USD', account='m',
                          description='NAME CHANGE', symbol_new=new,
                          source=source, **kw)


class TestTwoEventsNeverShareAnId(unittest.TestCase):
    def _two(self, country, election, hints):
        return (_spin('ev-a', 10, country, election, hints)
                + _spin('ev-b', 5, country, election, hints))

    def test_us_two_spin_offs_with_one_amount_keep_both_parent_cuts(self):
        rows = self._two('usa', 'tax_free_355', {'allocated_acb': 40})
        cuts = [r for r in rows if r.action == 'ADJUST']
        self.assertEqual(len(cuts), 2)
        self.assertEqual({(r.symbol, r.net_amount) for r in cuts},
                         {('QZRX', -40.0)})
        self.assertEqual(len({r.id for r in rows}), 4, [r.id for r in rows])
        kept, dropped = deduplicate(list(rows), return_dropped=True)
        self.assertEqual((len(kept), dropped), (4, []))

    def test_canada_two_spin_offs_with_one_amount_keep_both_parent_cuts(self):
        rows = self._two('canada', 'rollover_s_86_1',
                         {'allocated_acb_cad': 20})
        cuts = [r for r in rows if r.action == 'ADJUST']
        self.assertEqual(len(cuts), 2)
        self.assertEqual(len({r.id for r in rows}), len(rows))
        self.assertEqual(len(deduplicate(list(rows))), len(rows))

    def test_two_events_of_one_day_and_quantity_keep_both_share_rows(self):
        # The new shares' rows of two events that differ in nothing else.
        rows = (_spin('ev-a', 10, 'usa', 'tax_free_355', {'allocated_acb': 40})
                + _spin('ev-b', 10, 'usa', 'tax_free_355',
                        {'allocated_acb': 40}))
        self.assertEqual(len(deduplicate(list(rows))), 4)

    def test_copies_of_one_event_still_collapse(self):
        once = _spin('ev-a', 10, 'usa', 'tax_free_355', {'allocated_acb': 40})
        again = _spin('ev-a', 10, 'usa', 'tax_free_355', {'allocated_acb': 40})
        self.assertEqual([r.id for r in once], [r.id for r in again])
        kept, dropped = deduplicate(once + again, return_dropped=True)
        self.assertEqual((len(kept), len(dropped)), (2, 2))


class TestWhichIdsChange(unittest.TestCase):
    def test_a_row_with_no_event_and_no_destination_keeps_its_id(self):
        # The id v2.0.0 gave this row: an ordinary trade's never moves.
        t = TaxTransaction(action='BUYSELL', date='2025-01-02', symbol='QZRX',
                           quantity=150, currency='USD', net_amount=-250,
                           account='b', date_settle='2025-01-03')
        self.assertEqual(t.id, _V2_TRADE_ID)
        self.assertEqual(compute_tt_id(t.to_dict()), t.id)

    def test_a_plain_split_spelled_with_its_own_symbol_is_one_row(self):
        # IB stamps symbol_new = symbol on a plain split, RBC and
        # Questrade leave it empty: one event, one id, as before.
        own = TaxTransaction(action='SPLIT', date='2025-03-03',
                             symbol='QZO.US', quantity=2.0, currency='USD',
                             account='m', symbol_new='QZO.US')
        bare = TaxTransaction(action='SPLIT', date='2025-03-03',
                              symbol='QZO.US', quantity=2.0, currency='USD',
                              account='m')
        self.assertEqual(own.id, bare.id)
        self.assertEqual(bare.id, _V2_SPLIT_ID)

    def test_the_event_and_the_destination_are_in_the_id(self):
        base = dict(action='BUYSELL', date='2025-04-01', symbol='QZSP',
                    quantity=10, currency='USD', net_amount=40, account='b')
        plain = TaxTransaction(**base)
        self.assertNotEqual(TaxTransaction(corp_event_id='ev-a', **base).id,
                            plain.id)
        self.assertNotEqual(TaxTransaction(corp_event_id='ev-a', **base).id,
                            TaxTransaction(corp_event_id='ev-b', **base).id)
        self.assertNotEqual(_rename('QZA.US').id, _rename('QZB.US').id)
        # The election is the user's choice for one event, never identity.
        self.assertEqual(
            TaxTransaction(corp_event_id='ev-a', corp_election='x', **base).id,
            TaxTransaction(corp_event_id='ev-a', corp_election='y', **base).id)

    def test_a_tt_row_and_a_json_row_agree(self):
        for t in (_rename('QZA.US'),
                  TaxTransaction(action='ADJUST', date='2025-04-01',
                                 symbol='QZRX', currency='USD',
                                 net_amount=-40, account='b',
                                 corp_event_id='ev-a')):
            self.assertEqual(compute_tt_id(t.to_dict()), t.id)


class TestABookRowAndItsTtLineAreOneRow(unittest.TestCase):
    """Every kind of row whose id holds an event or a destination writes
    them on its `.tt` line and reads them back: one id both ways."""

    _EVENT = '20250401-qzrx-qzsp-1a2b'

    def _rows(self):
        base = dict(date='2025-04-01', time='09:30:00', date_settle='2025-04-01',
                    account='m')
        ev = dict(corp_event_id=self._EVENT, corp_election='tax_free_355')
        cur = dict(currency='USD')
        return [
            TaxTransaction(action='ADJUST', symbol='QZRX.US', net_amount=-40,
                           **base, **ev, **cur),
            TaxTransaction(action='BUYSELL', symbol='QZSP.US', quantity=10,
                           price=4, net_amount=40, **base, **ev, **cur),
            TaxTransaction(action='BUYSELL', symbol='QZSP.US', quantity=-10,
                           price=4, net_amount=40, **base, **ev, **cur),
            TaxTransaction(action='ASSIGN', symbol='QZSP.US', quantity=10,
                           price=4, net_amount=40, **base, **ev, **cur),
            TaxTransaction(action='DIVIDEND', symbol='QZRX.US', net_amount=12,
                           gross_amount=12, type='dividend', **base, **ev, **cur),
            TaxTransaction(action='DIVIDEND_IN_LIEU', symbol='QZRX.US',
                           net_amount=12, gross_amount=12,
                           type='dividend_in_lieu', **base, **ev, **cur),
            TaxTransaction(action='TAX', symbol='QZRX.US', net_amount=-2,
                           gross_amount=-2, type='tax', **base, **ev, **cur),
            # a rename a corporate action made, one a broker reported, a
            # plain split spelled both ways
            TaxTransaction(action='SPLIT', symbol='QZO.US', quantity=1.0,
                           symbol_new='QZA.US', currency='', **base, **ev),
            TaxTransaction(action='SPLIT', symbol='QZO.US', quantity=0.5,
                           symbol_new='QZA.US', currency='', **base, **ev),
            TaxTransaction(action='SPLIT', symbol='QZO.US', quantity=1.0,
                           symbol_new='QZA.US', currency='', **base),
            TaxTransaction(action='SPLIT', symbol='QZO.US', quantity=2.0,
                           symbol_new='QZO.US', currency='', **base),
            TaxTransaction(action='SPLIT', symbol='QZO.US', quantity=2.0,
                           currency='', **base),
            # actions whose line has no corp= token: the event is not in
            # their id
            TaxTransaction(action='TRANSFER', symbol='QZO.US', quantity=5,
                           **base, **ev, **cur),
            TaxTransaction(action='DISALLOW', symbol='QZO.US', net_amount=5,
                           **base, **ev, **cur),
        ]

    def test_the_line_reads_back_to_the_rows_id(self):
        from taxjson.lib.tt_format import parse_tt_line, tx_to_tt_line
        for t in self._rows():
            with self.subTest(action=t.action, new=t.symbol_new,
                              event=t.corp_event_id):
                line = tx_to_tt_line(t.to_dict())
                back = parse_tt_line(line, 'm')
                self.assertEqual(back['id'], t.id, line)
                self.assertEqual(back.get('corp_event_id') or '',
                                 t.corp_event_id if 'corp=' in line else '')

    def test_a_split_line_carries_its_event(self):
        from taxjson.lib.tt_format import parse_tt_line, tx_to_tt_line
        made = self._rows()[7]
        line = tx_to_tt_line(made.to_dict())
        self.assertEqual(line.split()[6:], [f'corp={self._EVENT}',
                                            'election=tax_free_355'])
        back = parse_tt_line(line, 'm')
        self.assertEqual((back['corp_event_id'], back['corp_election']),
                         (self._EVENT, 'tax_free_355'))
        # ... and is another row than the broker's own rename row.
        self.assertNotEqual(made.id, self._rows()[9].id)
        with self.assertRaises(ValueError) as cm:
            parse_tt_line('SPLIT 2025-04-01 09:30:00 QZO.US QZA.US 1 cash=7.5:USD',
                          'm')
        self.assertIn('a SPLIT line takes corp=<event id> and election=<name>',
                      str(cm.exception))

    def test_the_actions_the_event_is_in_are_the_lines_that_say_it(self):
        from taxjson.lib import tt_format
        from taxjson.model.transaction import EVENT_ID_ACTIONS
        self.assertEqual(set(EVENT_ID_ACTIONS),
                         set(tt_format._CORP_ACTIONS) | {'SPLIT'})


class TestARenameToTwoDestinationsIsRefused(unittest.TestCase):
    def test_two_exports_that_disagree_are_refused_naming_both(self):
        rows = [_rename('QZA.US', 'one.csv'), _rename('QZB.US', 'two.csv')]
        self.assertNotEqual(rows[0].id, rows[1].id)
        with self.assertRaises(RenameDestinationConflict) as cm:
            deduplicate(list(rows), report=io.StringIO())
        self.assertIsInstance(cm.exception, InputContentError)
        text = str(cm.exception)
        for part in ('QZO.US', '2025-03-03', 'account m', 'QZA.US (in one.csv)',
                     'QZB.US (in two.csv)', 'refused', 'delete or correct'):
            self.assertIn(part, text)

    def test_rows_with_no_source_are_refused_too(self):
        rows = [_rename('QZA.US'), _rename('QZB.US', corp_event_id='ev-r')]
        with self.assertRaises(RenameDestinationConflict) as cm:
            deduplicate(list(rows))
        self.assertIn('QZA.US (a row with no source file)', str(cm.exception))
        self.assertIn('QZB.US (corporate action ev-r)', str(cm.exception))

    def test_dict_rows_are_read_the_same_way(self):
        rows = [_rename('QZA.US', 'one.csv').to_dict(),
                _rename('QZB.US', 'two.csv').to_dict()]
        self.assertEqual(len(rename_conflicts(rows)), 1)

    def test_copies_that_agree_are_one_row(self):
        rows = [_rename('QZA.US'), _rename('QZA.US')]
        self.assertEqual(rename_conflicts(rows), [])
        self.assertEqual(len(deduplicate(list(rows))), 1)

    def test_what_is_no_conflict(self):
        other_day = TaxTransaction(
            action='SPLIT', date='2025-06-02', symbol='QZO.US', quantity=1.0,
            currency='USD', account='m', symbol_new='QZB.US')
        other_account = TaxTransaction(
            action='SPLIT', date='2025-03-03', symbol='QZO.US', quantity=1.0,
            currency='USD', account='n', symbol_new='QZB.US')
        plain = TaxTransaction(
            action='SPLIT', date='2025-03-03', symbol='QZO.US', quantity=2.0,
            currency='USD', account='m', symbol_new='QZO.US')
        rows = [_rename('QZA.US'), other_day, other_account, plain]
        self.assertEqual(rename_conflicts(rows), [])
        self.assertEqual(len(plan_dedup(rows).keep), 4)


class TestTheRunStopsOnARenameToTwoDestinations(unittest.TestCase):
    def test_tjs_run_refuses_and_names_both_files(self):
        import tempfile
        from _support.tt_fx_project import _cli, _tt_project
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 QZO.TO 100 CAD 10 -1000 0",
                "SPLIT 2026-03-02 09:30:00 QZO.TO QZA.TO 1"])
            (root / "inputs" / "m" / "manual.tt").write_text(
                "SPLIT 2026-03-02 09:30:00 QZO.TO QZB.TO 1\n")
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 1, r.stdout[-2000:])
            for part in ("QZO.TO is renamed on 2026-03-02 in account m to 2 "
                         "different symbols", "QZA.TO (in a.tt)",
                         "QZB.TO (in manual.tt)", "delete or correct"):
                self.assertIn(part, r.stderr)
            self.assertFalse((root / "work" / "m_gains.json").exists())


# What v2.0.0 computed for the two rows above (their ids do not change).
_V2_TRADE_ID = 'a124f54153c178df'
_V2_SPLIT_ID = 'ece6187f62c950ee'


if __name__ == "__main__":
    unittest.main()
