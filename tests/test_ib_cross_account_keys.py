"""IB's cross-statement cancellations (the reviews of step 5's IB cut).

The held `Ca` rows (passes 3b and 4b, `_ib_cross_held_cas`) are keyed by
their broker Account, as the Trades pass is (TJ-1130), one naming no
Account being a named one's copy. The Transfers `Ca` pairs (pass 4,
`_ib_cross_xfer_cas`) are counted, and matched without the parse's fill
mark. A transfer row carries no Account in the parse (a combined
statement books its accounts' transfers under one label), so pass 4 has
none to key by. Synthetic ids and tickers only."""
import json
import unittest

from _support.ib_brokerage_base import _A, _B, _Brokerage, _Y2025, _buy, _period
from _support.ib_statement_rows import HEAD, TRADES_H, XFER_H, _trade, _xfer

_H1 = _period('January 1, 2025 - June 30, 2025')
_H2 = _period('July 1, 2025 - December 31, 2025')


def _buys(rows):
    return sorted(t['net_amount'] for t in rows if t['action'] == 'BUYSELL'
                  and t['quantity'] > 0)


class TestHeldCancellationsOfTwoAccounts(_Brokerage):
    """Two accounts' held Trades `Ca` rows alike but for the Account: each
    cancels its own account's original in the earlier statement."""

    def _ca(self, acct, comm):
        return _trade('SYN', '2025-03-03, 10:00:00', -10, 10, 100, comm,
                      code='Ca', cur='CAD', acct=acct)

    def _both(self):
        return (_buy(_A, '2025-03-03, 10:00:00', comm=-1)
                + _buy(_B, '2025-03-03, 10:00:00', comm=-2))

    def test_each_held_cancellation_cancels_its_own_accounts_original(self):
        old = HEAD + _H1 + TRADES_H + self._both()
        new = HEAD + _H2 + TRADES_H + self._ca(_A, 1) + self._ca(_B, 2)
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(_buys(rows), [], err)

    def test_a_copy_naming_no_account_is_one_of_them(self):
        # blank.csv (listed first) holds one of the two cancellations
        # without an Account: it is a copy of a named one, so the named
        # ones are taken first and each cancels its own (TJ-2529).
        old = HEAD + _H1 + TRADES_H + self._both()
        blank = HEAD + _H2 + TRADES_H + self._ca('', 1)
        new = HEAD + _H2 + TRADES_H + self._ca(_A, 1) + self._ca(_B, 2)
        rows, err = self._run({'blank.csv': blank, 'new.csv': new,
                               'old.csv': old})
        self.assertEqual(_buys(rows), [], err)

    def test_one_accounts_held_cancellation_keeps_the_other(self):
        old = HEAD + _H1 + TRADES_H + self._both()
        new = HEAD + _H2 + TRADES_H + self._ca(_A, 1)
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(_buys(rows), [102.0], err)


class TestTwoCancelledTransfersAlike(_Brokerage):
    """Two transfers alike (symbol, description, date, quantity), both
    cancelled (`Ca`) in one statement: an overlapping statement that holds
    the two originals drops both, as the Trades pass counts its pairs."""

    _OUT = _xfer('QZB', '2025-03-03', -36, -1440, typ='ATON', cur='CAD')
    _CA = _xfer('QZB', '2025-03-03', 36, 1440, typ='ATON', cur='CAD', code='Ca')

    def _kept(self, n_ca):
        # The transfers are custody evidence, kept aside (the sidecar): the
        # rows each statement keeps, by its file.
        old = HEAD + _H1 + XFER_H + self._OUT + self._OUT
        new = HEAD + _Y2025 + XFER_H + self._OUT + self._OUT + self._CA * n_ca
        side = self.dir / 'transfers.json'
        _rows, err = self._run({'old.csv': old, 'new.csv': new},
                               '--transfers-out', str(side))
        doc = json.loads(side.read_text(encoding='utf-8'))
        rows = doc['transactions'] if isinstance(doc, dict) else doc
        return sorted(t['quantity'] for t in rows), err

    def test_both_copies_are_dropped(self):
        kept, err = self._kept(2)
        self.assertEqual(kept, [], err)

    def test_a_statement_holding_one_cancellation_drops_the_other(self):
        # The half year holds both and the first cancellation (its parse
        # pairs one); the full year holds both cancellations. The second
        # cancels the half year's other copy too (TJ-2530: a statement
        # holding the owner's first cancellation was skipped).
        ca1 = _xfer('QZB', '2025-03-05', 36, 1440, typ='ATON', cur='CAD', code='Ca')
        ca2 = _xfer('QZB', '2025-07-10', 36, 1440, typ='ATON', cur='CAD', code='Ca')
        half = HEAD + _H1 + XFER_H + self._OUT + self._OUT + ca1
        year = HEAD + _Y2025 + XFER_H + self._OUT + self._OUT + ca1 + ca2
        side = self.dir / 'transfers.json'
        _rows, err = self._run({'half.csv': half, 'year.csv': year},
                               '--transfers-out', str(side))
        doc = json.loads(side.read_text(encoding='utf-8'))
        rows = doc['transactions'] if isinstance(doc, dict) else doc
        self.assertEqual([t['quantity'] for t in rows], [], err)

    def test_one_cancelled_leaves_one_transfer(self):
        # The two statements' copies of the transfer left are one row. The
        # older statement dropped its unmarked copy and kept the one its
        # parse marked "[fill #2]", which no copy matched: two.
        kept, err = self._kept(1)
        self.assertEqual(kept, [-36.0], err)


class TestHeldTransferCancellations(_Brokerage):
    """A Transfers `Ca` row a later statement's parse holds: IB cancelled a
    transfer of an earlier statement and rebooked it in this one, both dated
    before its period. The Ca cancels the earlier statement's original and
    the rebook stays: two alike, the second of each marked by its parse as a
    fill of two (TJ-2528), and one held in an account's own statement and in
    a combined statement of it, whose copy names no Account (TJ-2529)."""

    _OUT = _xfer('QZB', '2025-03-03', -36, -1440, typ='ATON', cur='CAD')
    _CA = _xfer('QZB', '2025-03-03', 36, 1440, typ='ATON', cur='CAD', code='Ca')

    def _kept(self, files):
        side = self.dir / 'transfers.json'
        _rows, err = self._run(files, '--transfers-out', str(side))
        doc = json.loads(side.read_text(encoding='utf-8'))
        rows = doc['transactions'] if isinstance(doc, dict) else doc
        return sorted(t['quantity'] for t in rows), err

    def test_two_alike_held_cancellations_cancel_both_originals(self):
        old = HEAD + _H1 + XFER_H + self._OUT + self._OUT
        new = HEAD + _H2 + XFER_H + self._OUT + self._OUT + self._CA + self._CA
        kept, err = self._kept({'old.csv': old, 'new.csv': new})
        self.assertEqual(kept, [-36.0, -36.0], err)

    def test_the_combined_statements_copy_is_the_accounts(self):
        early = (HEAD + _H1 + TRADES_H + _buy(_A, '2025-02-03, 10:00:00')
                 + XFER_H + self._OUT)
        own = (HEAD + _H2 + TRADES_H + _buy(_A, '2025-08-01, 10:00:00')
               + XFER_H + self._OUT + self._CA)
        combined = (HEAD + _H2 + TRADES_H + _buy(_A, '2025-08-01, 10:00:00')
                    + _buy(_B, '2025-08-01, 10:00:00', comm=-2)
                    + XFER_H + self._OUT + self._CA)
        kept, err = self._kept({'early.csv': early, 'own.csv': own,
                                'combined.csv': combined})
        self.assertEqual(kept, [-36.0, -36.0], err)
        self.assertNotIn('no other statement holds its original', err)

if __name__ == '__main__':
    unittest.main()
