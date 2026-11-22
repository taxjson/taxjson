"""A warrant's or a right's exercise leg and the share leg it delivers
(lib/brokerages/base.py `pair_warrant_legs`; the reviews of step 5's
helpers branch, TJ-2537, TJ-2538, TJ-2547 to TJ-2550). Made-up tickers
only."""
import unittest

from _support.rbc_warrants import _RBC_BUY, _RBC_SLEG, _RBC_WLEG, _parse_rbc, _rbc


def _leg(sym, qty, day='2025-05-02', cur='CAD'):
    return {'symbol': sym, 'quantity': qty, 'date': day, 'currency': cur,
            'date_settle': day}


class TestPairing(unittest.TestCase):

    def _pair(self, warrants, shares):
        from taxjson.lib.brokerages.base import pair_warrant_legs
        return [(w['symbol'], st and st['symbol'])
                for w, st, _c in pair_warrant_legs(warrants, shares)]

    def test_a_lone_share_leg_goes_to_its_own_warrant(self):
        # XYZ.WT, listed first, used to take ZZQ's lone share leg.
        got = self._pair([_leg('XYZ.WT', -10), _leg('ZZQ.WT', -10)],
                         [_leg('ZZQ', 10)])
        self.assertEqual(got, [('XYZ.WT', None), ('ZZQ.WT', 'ZZQ')])

    def test_the_longest_root_names_the_shares(self):
        got = self._pair([_leg('ABCD.WT', -10)],
                         [_leg('ABC', 10), _leg('ABCD', 10)])
        self.assertEqual(got, [('ABCD.WT', 'ABCD')])

    def test_a_blank_symbol_names_no_warrant(self):
        got = self._pair([_leg('XYZ.WT', -10)], [_leg('', 10), _leg('ZZQ', 10)])
        self.assertEqual(got, [('XYZ.WT', None)])

    def test_a_lone_leg_of_another_root_still_pairs(self):
        # A SPAC's warrant exercises into the new company's shares.
        got = self._pair([_leg('QZPW', -10)], [_leg('NWCO', 10)])
        self.assertEqual(got, [('QZPW', 'NWCO')])

    def test_a_share_leg_left_over_is_not_guessed_at(self):
        # TJ-2547: XYZ's own share leg is missing; QQQ, left once ZZQ.WT
        # took ZZQ, is not its (the day is not one exercise).
        got = self._pair([_leg('XYZ.WT', -10), _leg('ZZQ.WT', -10)],
                         [_leg('ZZQ', 10), _leg('QQQ', 10)])
        self.assertEqual(got, [('XYZ.WT', None), ('ZZQ.WT', 'ZZQ')])

    def test_the_closest_root_claims_first_across_warrants(self):
        # TJ-2548: AB is AB.WT's (WT left over) before ABC.WT's (CWT).
        got = self._pair([_leg('ABC.WT', -10), _leg('AB.WT', -10)],
                         [_leg('AB', 10)])
        self.assertEqual(got, [('ABC.WT', None), ('AB.WT', 'AB')])
        got = self._pair([_leg('ABC.WT', -10), _leg('AB.WT', -10)],
                         [_leg('AB', 10), _leg('NWCO', 10)])
        self.assertEqual(got, [('ABC.WT', None), ('AB.WT', 'AB')])

    def test_a_lone_blank_symbol_names_no_warrant(self):
        # TJ-2549: the day's one exercise, its share leg's symbol blank.
        got = self._pair([_leg('QZPW', -10)], [_leg('', 10)])
        self.assertEqual(got, [('QZPW', None)])

    def test_two_blocks_of_one_warrant_pair_in_order(self):
        # TJ-2550: two blocks into one listing were both left unpaired.
        got = self._pair([_leg('ZZQ.WT', -10), _leg('ZZQ.WT', -5)],
                         [_leg('ZZQ', 10), _leg('ZZQ', 5)])
        self.assertEqual(got, [('ZZQ.WT', 'ZZQ'), ('ZZQ.WT', 'ZZQ')])

    def test_the_paired_leg_carries_the_listing_and_settlement(self):
        from taxjson.lib.brokerages.base import pair_warrant_legs
        w, st = _leg('QZW.WT', -10), _leg('QZ', 10, day='2025-05-02')
        st['date_settle'] = '2025-05-05'
        pair_warrant_legs([w], [st])
        self.assertEqual((w['exercise_of'], w['date_settle']), ('QZ', '2025-05-05'))


class TestTwoWarrantsOneShareLeg(unittest.TestCase):

    def test_ib(self):
        from _support.ib_statement_rows import HEAD, TRADES_H, _parse_ib, _trade
        body = (HEAD + TRADES_H
                + _trade('XYZW', '2025-02-03, 10:00:00', 100, 2.0, -200,
                         comm=-1, cat='Warrants')
                + _trade('ZZQW', '2025-02-03, 10:00:00', 100, 2.0, -200,
                         comm=-1, cat='Warrants')
                + _trade('XYZW', '2025-05-02, 16:20:00', -100, 0, 0,
                         code='C;Ex', cat='Warrants')
                + _trade('ZZQW', '2025-05-02, 16:20:00', -100, 0, 0,
                         code='C;Ex', cat='Warrants')
                + _trade('ZZQ', '2025-05-02, 16:20:00', 100, 11.5, -1150,
                         code='Ex;O'))
        _, txs, err = _parse_ib(body)
        got = {t['symbol']: t.get('exercise_of') for t in txs
               if t['action'] == 'ASSIGN'}
        self.assertEqual(got, {'XYZW.US': None, 'ZZQW.US': 'ZZQ.US'}, err)
        self.assertIn('warrant XYZW', err)

    def test_rbc_refuses_naming_the_warrant_with_no_share_leg(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        rows = [_rbc('2025-02-03', 'Buy', 'XYZ.WT', 'XYZ CORP WTS', '100',
                     '-100.00', 'XYZ CORP WTS BUY', '1.00'),
                _RBC_BUY,
                _rbc('2025-05-05', 'Exercise', 'XYZ.WT', 'XYZ CORP WTS', '-100',
                     '0', 'EXERCISE XYZ CORP WTS', '0'),
                _RBC_WLEG, _RBC_SLEG]
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_rbc(rows)
        self.assertIn("Exercise of XYZ.WT", str(cm.exception))


if __name__ == '__main__':
    unittest.main()
