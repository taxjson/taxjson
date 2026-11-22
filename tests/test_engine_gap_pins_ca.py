"""Canadian engine branches no test executed (TJ-1896, the mutation triage
of 2026-11-09): each test runs one branch and pins a figure (or the
refusal) a mutant of the branch changes. Synthetic data only; tickers and
accounts are made up."""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import _drop_self_cancelling_transfers
from tax_rules import rule
from taxjson.rules import ids as R


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res, err.getvalue()


def _tx(action, date, qty, net, *, sym='SYQ.TO', account='b', typ='',
        desc='', settle=None, lot_date=None, mult=1.0):
    return TaxTransaction(action=action, date=date,
                          date_settle=settle or date, time='10:00:00',
                          symbol=sym, quantity=qty,
                          price=abs(net / qty / mult) if qty else 0.0,
                          net_amount=net, currency='CAD', account=account,
                          type=typ, description=desc,
                          **({'lot_date': lot_date} if lot_date else {}))


def _trade(date, qty, net, **kw):
    return _tx('BUYSELL', date, qty, net, **kw)


def _rename(date, old, new):
    return TaxTransaction(action='SPLIT', date=date, date_settle=date,
                          time='09:00:00', symbol=old, symbol_new=new,
                          quantity=1.0, net_amount=0.0, currency='CAD',
                          account='b')


def _sales(res, sym=None):
    return [(r['date'], r['symbol'], round(r['gain'], 2),
             round(r.get('disallowed_amount') or 0.0, 2))
            for r in res['transactions']
            if r.get('qty') and (sym is None or r['symbol'] == sym)]


class TestRenameMergeKeepsTheLaterOpeningLot(unittest.TestCase):
    """`_walk_split`, a rename onto a ticker that already has a pool, when
    the source pool holds `opening_acq` (an opening snapshot's lot date,
    CA-OPEN-01): the merged pool keeps the LATER lot date, the one the
    days held of a sale from an opening-only pool count from, as one
    symbol's two opening lots do (the owner's ruling on backlog TJ-4241,
    2026-11-18; this class kept the earlier before)."""

    def _sale(self, target_rows):
        res, _ = _run(target_rows + [
            _tx('BUYSELL', '2025-01-02', 10, -800, sym='SYQ.TO',
                typ='opening', lot_date='2023-06-01'),
            _rename('2025-03-03', 'SYQ.TO', 'ZZQ.TO'),
            _trade('2025-06-02', -20, 1500, sym='ZZQ.TO')])
        (sale,) = [r for r in res['transactions'] if r.get('qty')]
        return round(sale['gain'], 2), sale['days_held']

    @rule(R.CA_OPEN_01, R.CA_ACB_RENAME)
    def test_the_sources_later_lot_date_shows(self):
        # 2023-06-01 to 2025-06-02: 732 days (not the target's 2021 lot,
        # 1554).
        self.assertEqual(self._sale([
            _tx('BUYSELL', '2025-01-02', 10, -500, sym='ZZQ.TO',
                typ='opening', lot_date='2021-03-01')]), (200.0, 732))

    @rule(R.CA_OPEN_01, R.CA_ACB_RENAME)
    def test_the_targets_later_lot_date_stays(self):
        # 2024-09-03 to 2025-06-02: 272 days (not the source's 2023 lot).
        self.assertEqual(self._sale([
            _tx('BUYSELL', '2025-01-02', 10, -500, sym='ZZQ.TO',
                typ='opening', lot_date='2024-09-03')]), (200.0, 272))

    @rule(R.CA_OPEN_01, R.CA_ACB_RENAME)
    def test_a_bought_target_counts_from_its_purchase(self):
        # The target was bought: its days held count from the purchase.
        self.assertEqual(self._sale([
            _trade('2025-01-02', 10, -500, sym='ZZQ.TO')]), (200.0, 151))


class TestDustShortAtARenameIsNoPosition(unittest.TestCase):
    """`_walk_split`, a rename that brings a parked deferral (`pending_wash`,
    CA-SL-19) into a target that holds a short: a short of exactly the
    share tolerance (1e-6 shares) is no position (pool_is_flat), so the
    dollars stay parked for the next opening, as with no short at all.

    The deferral: NEW's 1000 loss is denied by the NEW rebuy, sold again;
    the units held at day 30 are OLD's (missing history), so the bump is
    parked on OLD's flat pool and rides the OLD -> NEW rename."""

    OLD, NEW = 'OLD.TO', 'NEW.TO'

    def _book(self, *extra):
        old, new = self.OLD, self.NEW
        return ([_trade('2025-01-02', 100, -2000, sym=new),
                 _trade('2025-02-03', -100, 1000, sym=new),     # loss 1000
                 _trade('2025-02-05', 100, -1000, sym=new),     # trigger,
                 _trade('2025-02-10', -100, 1000, sym=new),     # sold again
                 _tx('OPENING_BALANCE', '2025-02-20', 100, 500, sym=old),
                 _trade('2025-03-10', -100, 600, sym=old)]
                + list(extra)
                + [_rename('2025-04-01', old, new),
                   _trade('2025-06-02', 100, -1200, sym=new),
                   _trade('2025-07-02', -100, 1500, sym=new)])

    @rule(R.CA_SL_19, R.CA_ACB_RENAME)
    def test_a_short_at_the_tolerance_changes_nothing(self):
        plain, _ = _run(self._book())
        dust, _ = _run(self._book(
            _trade('2025-03-20', -1e-6, 0.0, sym=self.NEW)))
        self.assertEqual(_sales(dust), _sales(plain))
        # The year's total carries the denied 1000 once, whichever sale
        # recovers it: 600 (OLD) + 300 (NEW's June purchase) - 1000.
        self.assertAlmostEqual(dust['summary']['total_gain'], -100.0,
                               places=2)


class TestOnlyOwnMovesAreLegsInTheHeldWalk(unittest.TestCase):
    """`_own_move_held`'s `_leg`, the clause after `_own`: in a loss's window
    that holds a move of your own between two registered accounts (so the
    per-holder walk runs), only that move and a taxable lot move
    (LOT_MOVE_TYPE) are legs; a sheltered account's unmatched custody
    TRANSFER rows (TRANSFER_CUSTODY_TYPE, CA-SL-16) are not, so they carry
    no units acquired in the window. A move of OLDER units between two other
    registered accounts backs nothing (CA-SL-08): adding it to the book
    must leave the denial as it was."""

    @rule(R.CA_SL_08, R.CA_SL_16)
    def test_an_unrelated_own_move_changes_nothing(self):
        tax = [_trade('2024-01-08', 100, -1000, sym='SYN.TO', account='a'),
               _trade('2025-01-20', -100, 600, sym='SYN.TO', account='a')]
        custody = [
            _trade('2025-01-25', 100, -600, sym='SYN.TO', account='r1'),
            _tx('TRANSFER', '2025-02-01', -100, 0.0, sym='SYN.TO',
                account='r1', typ='transfer_custody'),
            _tx('TRANSFER', '2025-02-03', 60, 0.0, sym='SYN.TO',
                account='r3', typ='transfer_custody')]
        own = [_trade('2024-03-01', 50, -500, sym='SYN.TO', account='r5'),
               _tx('TRANSFER', '2025-01-15', -50, 0.0, sym='SYN.TO',
                   account='r5', typ='own_account_move'),
               _tx('TRANSFER', '2025-01-15', 50, 0.0, sym='SYN.TO',
                   account='r6', typ='own_account_move')]

        def denied(shel):
            res, _ = _run(tax, sheltered_transactions=shel)
            return (_sales(res),
                    sum(r.get('permanently_disallowed') or 0.0
                        for r in res['transactions']))

        self.assertEqual(denied(custody + own), denied(custody))
        # (with r1 keeping its purchase, the loss is denied for good)
        self.assertEqual(denied(custody[:1] + own),
                         ([('2025-01-20', 'SYN.TO', 0.0, 400.0)], 400.0))


class TestRestatementPoolLeavesGuardedSegmentsOut(unittest.TestCase):
    """`_drop_self_cancelling_transfers`' restatement pre-pass (the
    sheltered invocation with the near-trade guard): a zero-net,
    out-first segment with an in-span trade of the SAME symbol in the SAME
    account (a trade on a leg's own date) never counts toward the
    account-wide event's three symbols, and only such a trade keeps a
    segment out. The event's segments net despite nearby taxable trades;
    below three symbols they are kept for the guard (CA-SL-17)."""

    def _book(self, clean, guarded):
        shel, main = [], []
        for sym in clean + guarded:
            shel += [_tx('TRANSFER', '2026-06-15', -100, 8000.0, sym=sym,
                         account='rrsp'),
                     _tx('TRANSFER', '2026-06-16', 100, 0.0, sym=sym,
                         account='rrsp')]
            main.append(_trade('2026-06-10', -100, 800.0, sym=sym,
                               account='b'))
        for sym in guarded:
            shel.append(_trade('2026-06-16', 10, -800.0, sym=sym,
                               account='rrsp'))
        with contextlib.redirect_stderr(io.StringIO()):
            out, dropped = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        kept = sorted((t.symbol, t.quantity) for t in out
                      if t.action == 'TRANSFER')
        return kept, sorted(s for s, _a, _n in dropped)

    @rule(R.CA_SL_17)
    def test_a_guarded_segment_is_not_the_third_symbol(self):
        kept, dropped = self._book(['QQA.TO', 'QQB.TO'], ['QQC.TO'])
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 6)

    @rule(R.CA_SL_17)
    def test_another_symbols_trade_does_not_keep_a_segment_out(self):
        kept, dropped = self._book(['QQA.TO', 'QQB.TO', 'QQD.TO'],
                                   ['QQC.TO'])
        self.assertEqual(dropped, ['QQA.TO', 'QQB.TO', 'QQD.TO'])
        self.assertEqual(kept, [('QQC.TO', -100), ('QQC.TO', 100)])



class TestAdjustOnAShortWithNoLongPool(unittest.TestCase):
    """`_ca_stock_short_key`: an ADJUST on a symbol whose only open position
    is a marked short, and which never had a long pool, is the short's (the
    compensation payment lowers the cover's gain)."""

    @rule(R.CA_ACB_14, R.CA_DISP_04)
    def test_compensation_lowers_the_cover(self):
        def trade(date, qty, net, code):
            return TaxTransaction(action='BUYSELL', date=date,
                                  date_settle=date, time='10:00:00',
                                  symbol='SYQ.TO', quantity=qty,
                                  price=abs(net / qty), net_amount=net,
                                  currency='CAD', account='b',
                                  open_close=code)
        res, err = _run([trade('2025-03-03', -10, 150, 'O'),
                         _tx('ADJUST', '2025-04-01', 0.0, 20, typ='dist'),
                         trade('2025-05-01', 10, 120, 'C')])
        self.assertEqual([(r['date'], round(r['gain'], 2))
                          for r in res['transactions']],
                         [('2025-05-01', 10.0)])
        self.assertIn('lands on a SHORT position', err)


if __name__ == '__main__':
    unittest.main()
