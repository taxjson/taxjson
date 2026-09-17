"""Tests for TRANSFER handling.

Design:
  - Sheltered (default): TRANSFER rewritten to BUYSELL using net_amount as
    approximate cost basis. Lets the sheltered inventory report still
    track positions established by in-kind transfers; cost-basis
    approximation is acceptable because sheltered gains aren't reported.
  - --taxable: TRANSFER rows are rejected with TransferValidationError
    (a typed error, not sys.exit — the CLI catches it and exits 1).
    A taxable return must use the actual underlying buy/sell history.

The rewrite happens in the CLI before compute_gains; the engine never
sees a TRANSFER row.
"""
import io
import json
import unittest
from unittest.mock import patch

from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from taxjson.bin.taxjson_gains import TransferValidationError, _handle_transfers


def _tx(action, date, symbol, qty, price=0.0, net=0.0, account='Margin',
        currency='USD', **extra):
    return TaxTransaction(
        action=action, date=date, symbol=symbol, quantity=qty,
        price=price, net_amount=net or abs(qty * price),
        account=account, currency=currency, **extra,
    )


def _tx_dict(action, date, symbol, qty, price=0.0, net=0.0, account='Margin', currency='USD'):
    return {
        'action': action, 'date': date, 'symbol': symbol,
        'quantity': qty, 'price': price,
        'net_amount': net or abs(qty * price),
        'account': account, 'currency': currency,
    }


def _run_gains_cli(transactions, *, country='canada', year=None, taxable=False):
    """Helper invoking taxjson_gains.main() with an in-memory pipeline."""
    import sys as _sys
    from taxjson.bin.taxjson_gains import main
    input_data = {'transactions': transactions}
    stdin_buf = io.StringIO(json.dumps(input_data))
    stdout_buf = io.StringIO()
    argv = ['taxjson-gains', '--country', country]
    if year:
        argv.extend(['--year', str(year)])
    if taxable:
        argv.append('--taxable')
    with patch.object(_sys, 'argv', argv), \
         patch.object(_sys, 'stdin', stdin_buf), \
         patch.object(_sys, 'stdout', stdout_buf):
        main()
    return json.loads(stdout_buf.getvalue())


# ============================================================================
# CLI helper: _handle_transfers
# ============================================================================
class TestHandleTransfersSheltered(unittest.TestCase):
    def test_transfer_rewritten_to_buysell(self):
        txs = [
            _tx('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=15000.0, account='LIRA'),
            _tx('BUYSELL', '2024-06-20', 'AAPL.US', -100, 200.0, 19998, account='LIRA'),
        ]
        new_txs, _ = _handle_transfers(txs, [], taxable=False)
        # TRANSFER replaced with BUYSELL; net_amount preserved.
        actions = [t.action for t in new_txs]
        self.assertEqual(actions, ['BUYSELL', 'BUYSELL'])
        # The synthesized BUYSELL keeps qty and net.
        self.assertEqual(new_txs[0].quantity, 100)
        self.assertEqual(new_txs[0].net_amount, 15000.0)

    def test_sheltered_unmatched_transfer_is_a_wash_trigger(self):
        """A lone TRANSFER-in in the --sheltered context is a REAL
        acquisition (in-kind contribution) and must be visible to the
        wash walk — rewritten to BUYSELL, qty and net preserved. The
        old unconditional strip silently let the canonical
        permanently-denied superficial loss be claimed."""
        txs = []
        sheltered = [
            _tx('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=15000.0, account='RRSP'),
        ]
        new_txs, new_sheltered = _handle_transfers(txs, sheltered, taxable=False)
        self.assertEqual([t.action for t in new_sheltered], ['BUYSELL'])
        self.assertEqual(new_sheltered[0].quantity, 100)
        self.assertEqual(new_sheltered[0].net_amount, 15000.0)

    def test_no_transfer_rows_passthrough(self):
        txs = [_tx('BUYSELL', '2024-01-15', 'AAPL.US', 100, 150.0, 15009)]
        new_txs, _ = _handle_transfers(txs, [], taxable=False)
        # Same list returned (no rewrite needed).
        self.assertEqual([t.action for t in new_txs], ['BUYSELL'])


class TestHandleTransfersTaxable(unittest.TestCase):
    def test_taxable_with_transfer_exits(self):
        txs = [
            _tx('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=15000.0, account='Margin'),
            _tx('BUYSELL', '2024-06-20', 'AAPL.US', -100, 200.0, 19998, account='Margin'),
        ]
        with self.assertRaises(TransferValidationError) as cm:
            _handle_transfers(txs, [], taxable=True)
        self.assertIn('TRANSFER', str(cm.exception))

    def test_taxable_no_transfers_ok(self):
        txs = [_tx('BUYSELL', '2024-01-15', 'AAPL.US', 100, 150.0, 15009, account='Margin')]
        new_txs, _ = _handle_transfers(txs, [], taxable=True)
        self.assertEqual([t.action for t in new_txs], ['BUYSELL'])

    def test_taxable_transfer_only_in_sheltered_does_not_error(self):
        """TRANSFER rows in the --sheltered cross-context file never
        trip the --taxable hard-error; an unmatched one is rewritten to
        a sheltered acquisition (visible to the wash walk), not counted
        against the taxable book."""
        main = [_tx('BUYSELL', '2024-06-20', 'AAPL.US', -100, 200.0, 19998, account='Margin')]
        sheltered = [_tx('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=15000.0, account='LIRA')]
        new_main, new_sh = _handle_transfers(main, sheltered, taxable=True)
        self.assertEqual([t.action for t in new_main], ['BUYSELL'])
        self.assertEqual([t.action for t in new_sh], ['BUYSELL'])

    def test_taxable_transfer_in_main_still_errors_even_with_sheltered_transfers(self):
        """A TRANSFER in the main file still triggers the error; the
        --sheltered file's TRANSFERs are just stripped, not counted."""
        main = [_tx('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=15000.0, account='Margin')]
        sheltered = [_tx('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=15000.0, account='LIRA')]
        with self.assertRaises(TransferValidationError):
            _handle_transfers(main, sheltered, taxable=True)


# ============================================================================
# Self-cancelling TRANSFER pair auto-drop (e.g. AEM.TO out → AEM.US in,
# normalized to the same ticker by the upstream ticker map).
# ============================================================================
class TestSelfCancellingTransferAutoDrop(unittest.TestCase):
    def test_pair_dropped_in_taxable_account(self):
        """The motivating case: a cross-listing journal that the ticker
        map normalized to a same-symbol TRANSFER pair. Net qty is zero
        and no BUYSELL of that symbol fell between the two dates, so
        both TRANSFERs are dropped silently and the --taxable guard
        doesn't fire."""
        txs = [
            _tx('TRANSFER', '2024-03-01', 'AEM.TO', -100, net=8000.0, account='Margin'),
            _tx('TRANSFER', '2024-03-15', 'AEM.TO', +100, net=8000.0, account='Margin'),
            _tx('BUYSELL',  '2024-09-20', 'AEM.TO', -100, 95.0, 9500.0, account='Margin'),
        ]
        new_txs, _ = _handle_transfers(txs, [], taxable=True)
        actions = [t.action for t in new_txs]
        self.assertEqual(actions, ['BUYSELL'],
                         "Both TRANSFERs should be auto-dropped, leaving only the sale.")

    def test_pair_dropped_in_sheltered_account(self):
        """Same auto-drop in the sheltered path — without it, the pair
        would be rewritten to two BUYSELLs and pollute the pool with
        phantom activity."""
        txs = [
            _tx('TRANSFER', '2024-03-01', 'AEM.TO', -100, net=8000.0, account='RRSP'),
            _tx('TRANSFER', '2024-03-15', 'AEM.TO', +100, net=8000.0, account='RRSP'),
        ]
        new_txs, _ = _handle_transfers(txs, [], taxable=False)
        self.assertEqual(new_txs, [],
                         "Self-cancelling pair drops entirely in sheltered too.")

    def test_intervening_sell_blocks_drop(self):
        """If the user sold during the gap between out and in, the
        position genuinely went to zero — the engine must keep the
        TRANSFERs so the resulting accounting error surfaces (sale of
        shares the pool didn't hold). In --taxable that means the
        hard-error path is reached."""
        txs = [
            _tx('TRANSFER', '2024-03-01', 'AEM.TO', -100, net=8000.0, account='Margin'),
            _tx('BUYSELL',  '2024-03-10', 'AEM.TO',  -50,  85.0, 4250.0, account='Margin'),
            _tx('TRANSFER', '2024-03-15', 'AEM.TO', +100, net=8000.0, account='Margin'),
        ]
        with self.assertRaises(TransferValidationError):
            _handle_transfers(txs, [], taxable=True)

    def test_non_zero_net_qty_blocks_drop(self):
        """If the TRANSFER group doesn't sum to zero, something real
        happened (genuine transfer to/from another owner). Don't drop;
        fall through to the existing handling."""
        txs = [
            _tx('TRANSFER', '2024-03-01', 'AEM.TO', -100, net=8000.0, account='Margin'),
            _tx('TRANSFER', '2024-03-15', 'AEM.TO', +50,  net=4000.0, account='Margin'),
        ]
        with self.assertRaises(TransferValidationError):
            _handle_transfers(txs, [], taxable=True)

    def test_different_accounts_not_paired(self):
        """A TRANSFER out of Margin and an unrelated TRANSFER into RRSP
        of the same ticker are NOT self-cancelling — different beneficial
        contexts. Each group is evaluated per (symbol, account)."""
        txs = [
            _tx('TRANSFER', '2024-03-01', 'AEM.TO', -100, net=8000.0, account='Margin'),
            _tx('TRANSFER', '2024-03-15', 'AEM.TO', +100, net=8000.0, account='RRSP'),
        ]
        with self.assertRaises(TransferValidationError):
            _handle_transfers(txs, [], taxable=True)


# ============================================================================
# Transfer-netting refusal bypasses (2026-09 audit findings 2-4).
# Each pinned pair must SURVIVE netting on the sheltered side so the
# engine's AmbiguousTransferDateError guard can fire; genuine custody
# moves away from trade windows must still net silently.
# ============================================================================
class TestNettingRefusalBypasses(unittest.TestCase):
    def _pair(self, d1, d2, a1='RRSP', a2='RRSP2'):
        return [_tx('TRANSFER', d1, 'Q.TO', +100, net=8000.0, account=a1),
                _tx('TRANSFER', d2, 'Q.TO', -100, net=8000.0, account=a2)]

    def test_settle_lag_blocks_cross_account_netting(self):
        """Finding 2: the Canada engine's ±30-day window runs on SETTLE
        dates. A loss trading 06-10 / settling 06-12 with a transfer
        pair on 07-11 is 31 days from the trade date but only 29 from
        settle — collecting only t.date netted it silently while the
        engine (had it seen the rows) would have refused."""
        from taxjson.lib.pipeline import _net_cross_account_transfers
        shel = self._pair('2026-07-11', '2026-07-11')
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin', date_settle='2026-06-12')]
        out = _net_cross_account_transfers(shel, main_transactions=main)
        self.assertEqual(len(out), 2,
                         "pair must survive: settle date is in range")
        # Control: with no settle lag the pair is 31 days out and nets.
        main_no_lag = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100,
                           net=800.0, account='Margin')]
        self.assertEqual(
            _net_cross_account_transfers(shel,
                                         main_transactions=main_no_lag),
            [], "31 days past the trade date it still nets")

    def test_short_cover_buy_blocks_netting(self):
        """Finding 3: a SHORT-cover loss is realized by a BUY, but only
        qty<0 rows counted as sales — a disposition hid behind the
        positive sign and the nearby pair netted silently."""
        from taxjson.lib.pipeline import _net_cross_account_transfers
        shel = self._pair('2026-06-15', '2026-06-20')
        main = [_tx('BUYSELL', '2026-06-12', 'Q.TO', +100, net=1500.0,
                    account='Margin')]     # buy-to-cover, at a loss
        out = _net_cross_account_transfers(shel, main_transactions=main)
        self.assertEqual(len(out), 2,
                         "pair must survive near a BUY disposition")
        # Control: the same buy far from the pair does not block.
        far = [_tx('BUYSELL', '2026-01-12', 'Q.TO', +100, net=1500.0,
                   account='Margin')]
        self.assertEqual(
            _net_cross_account_transfers(shel, main_transactions=far), [])

    def test_same_account_pair_near_loss_survives_dropper(self):
        """Finding 4: the per-account dropper runs FIRST on the
        sheltered book and had no main-book visibility, so a
        same-account contribution+withdrawal pair inside a loss window
        was erased as a 'custody move' before the cross-account netter
        could refuse."""
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel = self._pair('2026-06-15', '2026-06-20', a1='RRSP', a2='RRSP')
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        out, dropped = _drop_self_cancelling_transfers(
            shel, main_transactions=main)
        self.assertEqual(len(out), 2, "pair must survive near the loss")
        self.assertEqual(dropped, [])

    def test_main_book_dropper_invocation_stays_unguarded(self):
        """Taxable custody moves near the account's own sales are
        normal (broker move mid-trading) — the MAIN-book invocation of
        the dropper (no main_transactions argument) must keep dropping;
        that book is guarded by TransferValidationError instead."""
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        pair = self._pair('2026-06-15', '2026-06-20',
                          a1='Margin', a2='Margin')
        out, _ = _drop_self_cancelling_transfers(pair)
        self.assertEqual(out, [])

    def test_chained_hops_survive_near_loss(self):
        """rrsp -> rrsp2 -> out: the rrsp2 in+out pair used to drop
        per-account, erasing a possible in-window contribution before
        the netter could see the chain."""
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel = [_tx('TRANSFER', '2026-06-15', 'Q.TO', -100, net=8000.0,
                    account='RRSP'),
                _tx('TRANSFER', '2026-06-16', 'Q.TO', +100, net=8000.0,
                    account='RRSP2'),
                _tx('TRANSFER', '2026-06-25', 'Q.TO', -100, net=8000.0,
                    account='RRSP2')]
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        out, _ = _drop_self_cancelling_transfers(
            shel, main_transactions=main)
        self.assertEqual(len(out), 3, "all hops must survive the dropper")

    def test_surviving_pair_reaches_engine_guard(self):
        """End-to-end: the surviving sheltered pair is rewritten to
        type='transfer_rewrite' BUYSELLs and the engine refuses to
        guess whether the in-window leg was an acquisition."""
        from taxjson.lib.core import AmbiguousTransferDateError, \
            get_tax_rules
        main = [_tx('BUYSELL', '2026-05-01', 'Q.TO', +100, net=10000.0,
                    account='Margin', currency='CAD'),
                _tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=8000.0,
                    account='Margin', currency='CAD',
                    date_settle='2026-06-12')]
        shel = [_tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=8000.0,
                    account='RRSP', currency='CAD'),
                _tx('TRANSFER', '2026-06-20', 'Q.TO', -100, net=8000.0,
                    account='RRSP', currency='CAD')]
        m, sh = _handle_transfers(main, shel, taxable=False)
        self.assertEqual([(t.action, t.type) for t in sh],
                         [('BUYSELL', 'transfer_rewrite')] * 2)
        with self.assertRaises(AmbiguousTransferDateError):
            get_tax_rules('canada').compute_gains(
                m, sheltered_transactions=sh)

    def test_declared_tt_counter_pair_nets_despite_near_loss(self):
        """The AmbiguousTransferDateError message tells the user to add
        a counter-TRANSFER in a .tt file so the pair nets out. That
        declared leg carries MANUAL_TRANSFER_DECLARATION, and the
        near-trade refusal must stand down for it — otherwise the
        documented resolution path is a dead end (2026-09 audit,
        found while declaring a real custody move)."""
        from taxjson.lib.pipeline import (MANUAL_TRANSFER_DECLARATION,
                                          _drop_self_cancelling_transfers)
        shel = [_tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=8000.0,
                    account='RRSP'),
                _tx('TRANSFER', '2026-06-15', 'Q.TO', -100, net=8000.0,
                    account='RRSP',
                    description=MANUAL_TRANSFER_DECLARATION)]
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        out, dropped = _drop_self_cancelling_transfers(
            shel, main_transactions=main)
        self.assertEqual(out, [], "declared pair must net out")
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 2)])

    def test_refused_zero_net_cluster_prints_attestation_note(self):
        """When a cluster that ALREADY nets to zero is refused only for
        being near a taxable trade, the engine guard's counter-TRANSFER
        prescription would unbalance it — the dropper must print the
        attestation form (a declared zero-net .tt pair) instead."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel = self._pair('2026-06-15', '2026-06-15', a1='RRSP', a2='RRSP')
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        err = io.StringIO()
        with redirect_stderr(err):
            out, _ = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        self.assertEqual(len(out), 2)
        self.assertIn('attest', err.getvalue())
        self.assertIn('TRANSFER  2026-06-15', err.getvalue())

    def test_attestation_pair_nets_broker_churn_cluster(self):
        """The prescribed resolution end-to-end: broker churn legs plus
        the user's declared zero-net pair all net out together."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import (MANUAL_TRANSFER_DECLARATION,
                                          _drop_self_cancelling_transfers)
        churn = [_tx('TRANSFER', '2026-06-15', 'Q.TO', -100, net=8000.0,
                     account='RRSP'),
                 _tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=8000.0,
                     account='RRSP'),
                 _tx('TRANSFER', '2026-06-18', 'Q.TO', -100, net=8100.0,
                     account='RRSP'),
                 _tx('TRANSFER', '2026-06-19', 'Q.TO', +100, net=0.0,
                     account='RRSP')]
        attest = [_tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=0.0,
                      account='RRSP',
                      description=MANUAL_TRANSFER_DECLARATION),
                  _tx('TRANSFER', '2026-06-15', 'Q.TO', -100, net=0.0,
                      account='RRSP',
                      description=MANUAL_TRANSFER_DECLARATION)]
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        with redirect_stderr(io.StringIO()):
            out, dropped = _drop_self_cancelling_transfers(
                churn + attest, main_transactions=main)
        self.assertEqual(out, [], "attested cluster must net out")
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 6)])

    def test_attestation_blessing_does_not_reach_gap_chained_legs(self):
        """Round-four audit finding 1: a declared June pair must not
        silently net a GENUINE July contribution/withdrawal that
        gap-chained (<=35d hops) into the same segment. Only rows
        within _ATTEST_BLESS_PAD_DAYS of a declared leg are netted;
        the July legs survive to the rewrite + engine guard."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import (MANUAL_TRANSFER_DECLARATION,
                                          _drop_self_cancelling_transfers)
        seg = [_tx('TRANSFER', '2026-06-15', 'Q.TO', -100, net=8000.0,
                   account='RRSP'),
               _tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=8000.0,
                   account='RRSP'),
               _tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=0.0,
                   account='RRSP',
                   description=MANUAL_TRANSFER_DECLARATION),
               _tx('TRANSFER', '2026-06-15', 'Q.TO', -100, net=0.0,
                   account='RRSP',
                   description=MANUAL_TRANSFER_DECLARATION),
               # Genuine, unattested events 33 days later — in the loss
               # window of the July main-book sale.
               _tx('TRANSFER', '2026-07-18', 'Q.TO', +100, net=8000.0,
                   account='RRSP'),
               _tx('TRANSFER', '2026-07-20', 'Q.TO', -100, net=8000.0,
                   account='RRSP')]
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin'),
                _tx('BUYSELL', '2026-07-19', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        with redirect_stderr(io.StringIO()):
            out, dropped = _drop_self_cancelling_transfers(
                seg, main_transactions=main)
        self.assertEqual(len(out), 2, "July legs must survive")
        self.assertTrue(all(t.date.startswith('2026-07') for t in out))
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 4)])

    def test_unbalanced_attestation_nets_nothing(self):
        """A declared pair whose 7-day reach does not net to zero
        (a real leg sits inside the reach, unbalanced) must refuse the
        WHOLE segment rather than guess."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import (MANUAL_TRANSFER_DECLARATION,
                                          _drop_self_cancelling_transfers)
        seg = [_tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=0.0,
                   account='RRSP',
                   description=MANUAL_TRANSFER_DECLARATION),
               _tx('TRANSFER', '2026-06-15', 'Q.TO', -100, net=0.0,
                   account='RRSP',
                   description=MANUAL_TRANSFER_DECLARATION),
               _tx('TRANSFER', '2026-06-18', 'Q.TO', +100, net=8000.0,
                   account='RRSP'),
               # Balancing leg OUTSIDE the 7-day reach (gap-chained).
               _tx('TRANSFER', '2026-07-10', 'Q.TO', -100, net=8000.0,
                   account='RRSP')]
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        err = io.StringIO()
        with redirect_stderr(err):
            out, dropped = _drop_self_cancelling_transfers(
                seg, main_transactions=main)
        self.assertEqual(len(out), 4, "nothing may be netted")
        self.assertEqual(dropped, [])
        self.assertIn('does not net to zero', err.getvalue())

    def test_account_wide_restatement_nets_without_attestation(self):
        """Three symbols in ONE account with zero-net clusters over a
        common envelope = a broker restatement event: all net despite
        nearby taxable trades, one NOTE names the event. No tax event
        journals an account's inventory out-and-back to zero."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel, main = [], []
        for i, sym in enumerate(('Q.TO', 'R.TO', 'S.TO')):
            shel += [_tx('TRANSFER', '2026-06-15', sym, -100,
                         net=8000.0, account='RRSP'),
                     _tx('TRANSFER', '2026-06-16', sym, +100,
                         net=0.0, account='RRSP')]
            main.append(_tx('BUYSELL', '2026-06-10', sym, -100,
                            net=800.0, account='Margin'))
        err = io.StringIO()
        with redirect_stderr(err):
            out, dropped = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        self.assertEqual(out, [], "whole event must net")
        self.assertEqual(len(dropped), 3)
        self.assertEqual(err.getvalue().count(
            'account-wide restatement detected'), 1)
        self.assertIn('3 symbols', err.getvalue())

    def test_in_first_pairs_never_classify_as_restatement(self):
        """Round-five adversarial finding 1: three GENUINE in-kind
        contribution+withdrawal pairs (IN-first) across 3 symbols must
        not be misread as a restatement — a broker restatement journals
        OUT and back in. All rows survive to the refusal/guard path."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel, main = [], []
        for sym in ('AAA.TO', 'BBB.TO', 'CCC.TO'):
            shel += [_tx('TRANSFER', '2026-06-12', sym, +100,
                         net=8000.0, account='RRSP'),
                     _tx('TRANSFER', '2026-06-20', sym, -100,
                         net=8000.0, account='RRSP')]
            main.append(_tx('BUYSELL', '2026-06-10', sym, -100,
                            net=800.0, account='Margin'))
        err = io.StringIO()
        with redirect_stderr(err):
            out, dropped = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        self.assertEqual(len(out), 6, "contribution pairs must survive")
        self.assertEqual(dropped, [])
        self.assertNotIn('restatement detected', err.getvalue())

    def test_event_span_cap_stops_pad_chaining(self):
        """Round-five finding 2: single-day out-first pairs weeks apart
        (each within chain-pad of the next) must not glue into one
        >= 3-symbol event — a restatement is a few days of churn."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel, main = [], []
        dates = ['2026-03-01', '2026-03-04', '2026-03-07', '2026-03-10',
                 '2026-03-13', '2026-03-16', '2026-03-19', '2026-03-22']
        for i, d in enumerate(dates):
            sym = f'S{i}.TO'
            shel += [_tx('TRANSFER', d, sym, -100, net=8000.0,
                         account='RRSP'),
                     _tx('TRANSFER', d, sym, +100, net=0.0,
                         account='RRSP')]
            main.append(_tx('BUYSELL', d, sym, -100, net=800.0,
                            account='Margin'))
        err = io.StringIO()
        with redirect_stderr(err):
            out, _ = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        # Within any 7-day window at most 3 pair-dates fit, so SOME
        # sub-events may still form — but the 03-01..03-22 span must
        # never be reported as ONE event.
        for line in err.getvalue().splitlines():
            if 'restatement detected' in line:
                self.assertNotIn('2026-03-01..2026-03-22', line)

    def test_declared_segments_keep_bless_pad_inside_event(self):
        """Round-five finding 3: a segment carrying DECLARED legs uses
        the attestation path (7-day bless reach) even when sibling
        symbols form a detected restatement event — the event bypass
        must not widen a declaration's reach onto gap-chained genuine
        legs weeks later."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import (MANUAL_TRANSFER_DECLARATION,
                                          _drop_self_cancelling_transfers)
        shel, main = [], []
        for sym in ('Q.TO', 'R.TO', 'S.TO'):
            shel += [_tx('TRANSFER', '2026-06-01', sym, -100,
                         net=8000.0, account='RRSP'),
                     _tx('TRANSFER', '2026-06-01', sym, +100,
                         net=0.0, account='RRSP'),
                     _tx('TRANSFER', '2026-06-01', sym, +100, net=0.0,
                         account='RRSP',
                         description=MANUAL_TRANSFER_DECLARATION),
                     _tx('TRANSFER', '2026-06-01', sym, -100, net=0.0,
                         account='RRSP',
                         description=MANUAL_TRANSFER_DECLARATION),
                     # Genuine out-first pair 25 days later, chained
                     # into the same segment by the 35-day gap rule.
                     _tx('TRANSFER', '2026-06-26', sym, -100,
                         net=8000.0, account='RRSP'),
                     _tx('TRANSFER', '2026-06-27', sym, +100,
                         net=8000.0, account='RRSP')]
            main.append(_tx('BUYSELL', '2026-06-24', sym, -100,
                            net=800.0, account='Margin'))
        with redirect_stderr(io.StringIO()):
            out, _ = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        # The June-26/27 genuine legs are outside every declared leg's
        # 7-day reach and near the 06-24 loss sales: they must survive.
        self.assertEqual(len(out), 6, [
            (t.date, t.symbol, t.quantity) for t in out])
        self.assertTrue(all(t.date >= '2026-06-26' for t in out))

    def test_two_symbol_churn_stays_below_restatement_threshold(self):
        """Two symbols' clusters are not account-level evidence — the
        per-symbol near-trade refusal (and DECLARED path) still
        applies."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel, main = [], []
        for sym in ('Q.TO', 'R.TO'):
            shel += [_tx('TRANSFER', '2026-06-15', sym, -100,
                         net=8000.0, account='RRSP'),
                     _tx('TRANSFER', '2026-06-16', sym, +100,
                         net=0.0, account='RRSP')]
            main.append(_tx('BUYSELL', '2026-06-10', sym, -100,
                            net=800.0, account='Margin'))
        with redirect_stderr(io.StringIO()):
            out, dropped = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        self.assertEqual(len(out), 4, "both clusters must survive")
        self.assertEqual(dropped, [])

    def test_restatement_event_is_per_account(self):
        """Three symbols spread across three ACCOUNTS are not one
        event — restatements are account-level broker operations."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel, main = [], []
        for sym, acct in (('Q.TO', 'RRSP'), ('R.TO', 'TFSA'),
                          ('S.TO', 'LIRA')):
            shel += [_tx('TRANSFER', '2026-06-15', sym, -100,
                         net=8000.0, account=acct),
                     _tx('TRANSFER', '2026-06-16', sym, +100,
                         net=0.0, account=acct)]
            main.append(_tx('BUYSELL', '2026-06-10', sym, -100,
                            net=800.0, account='Margin'))
        with redirect_stderr(io.StringIO()):
            out, dropped = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        self.assertEqual(len(out), 6, "no cross-account event")
        self.assertEqual(dropped, [])

    def test_distant_churn_not_chained_into_restatement(self):
        """A third symbol's cluster WEEKS away must not join the
        event envelope (chain pad is days, not the segment gap)."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel = []
        for sym in ('Q.TO', 'R.TO'):
            shel += [_tx('TRANSFER', '2026-06-15', sym, -100,
                         net=8000.0, account='RRSP'),
                     _tx('TRANSFER', '2026-06-16', sym, +100,
                         net=0.0, account='RRSP')]
        shel += [_tx('TRANSFER', '2026-07-20', 'S.TO', -100,
                     net=8000.0, account='RRSP'),
                 _tx('TRANSFER', '2026-07-21', 'S.TO', +100,
                     net=0.0, account='RRSP')]
        main = [_tx('BUYSELL', '2026-06-10', s, -100, net=800.0,
                    account='Margin')
                for s in ('Q.TO', 'R.TO')] + \
               [_tx('BUYSELL', '2026-07-15', 'S.TO', -100, net=800.0,
                    account='Margin')]
        with redirect_stderr(io.StringIO()):
            out, dropped = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        self.assertEqual(len(out), 6,
                         "2+1 distant symbols: no event, all refused")
        self.assertEqual(dropped, [])

    def test_unmapped_cross_listing_journal_candidate_noted(self):
        """An out-leg of X and an in-leg of DIFFERENT symbol Y, same
        account, equal qty, days apart — the fingerprint of an
        UNMAPPED dual-listing journal (mapped pairs were normalized to
        one symbol before this code runs). Must surface a ticker.map
        suggestion, and must NOT net anything."""
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel = [_tx('TRANSFER', '2026-06-15', 'BTG.US', -500,
                    net=2000.0, account='RRSP'),
                _tx('TRANSFER', '2026-06-16', 'BTO.TO', +500,
                    net=2700.0, account='RRSP')]
        err = io.StringIO()
        with redirect_stderr(err):
            out, dropped = _drop_self_cancelling_transfers(
                shel, main_transactions=[])
        self.assertEqual(len(out), 2)
        self.assertEqual(dropped, [])
        self.assertIn('possible unmapped cross-listing journal',
                      err.getvalue())
        self.assertIn('TOBASE BTG.US BTO.TO', err.getvalue())

    def test_declared_lone_leg_still_guarded(self):
        """A declared .tt TRANSFER that does NOT net to zero gets no
        special treatment — the bypass is only for a segment the user
        has fully cancelled; an unbalanced declaration still reaches
        the rewrite + engine guard."""
        from taxjson.lib.pipeline import (MANUAL_TRANSFER_DECLARATION,
                                          _drop_self_cancelling_transfers)
        shel = [_tx('TRANSFER', '2026-06-15', 'Q.TO', +100, net=8000.0,
                    account='RRSP',
                    description=MANUAL_TRANSFER_DECLARATION)]
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        out, dropped = _drop_self_cancelling_transfers(
            shel, main_transactions=main)
        self.assertEqual(len(out), 1)
        self.assertEqual(dropped, [])

    def test_custody_move_away_from_windows_still_nets(self):
        """The guard must not make every custody move loud: a
        same-account sheltered pair months from any main-book trade
        still nets to nothing."""
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        shel = self._pair('2026-10-01', '2026-10-04',
                          a1='RRSP', a2='RRSP')
        _, sh = _handle_transfers(main, shel, taxable=False)
        self.assertEqual(sh, [])

    def test_segment_span_cap_stops_gap_chaining(self):
        """Consecutive <=35-day gaps used to chain one segment across
        Feb..Dec, letting a real February disposition net against
        unrelated rows months later. Segments spanning more than 45
        days are now refused outright (not split — conservative: the
        rows fall through to the loud paths)."""
        from taxjson.lib.pipeline import (_drop_self_cancelling_transfers,
                                          _net_cross_account_transfers)
        chain = [_tx('TRANSFER', d, 'Q.TO', q, net=1000.0, account='RRSP')
                 for d, q in [('2026-02-10', -100), ('2026-03-10', 10),
                              ('2026-04-10', 10), ('2026-05-10', 10),
                              ('2026-06-10', 10), ('2026-07-10', 10),
                              ('2026-08-10', 10), ('2026-09-10', 10),
                              ('2026-10-10', 10), ('2026-11-10', 10),
                              ('2026-12-10', 10)]]
        out, dropped = _drop_self_cancelling_transfers(chain)
        self.assertEqual(len(out), len(chain))
        self.assertEqual(dropped, [])
        # Same refusal at the cross-account level.
        cross = [_tx('TRANSFER', t.date, 'Q.TO', t.quantity, net=1000.0,
                     account=('RRSP2' if i % 2 else 'RRSP'))
                 for i, t in enumerate(chain)]
        self.assertEqual(len(_net_cross_account_transfers(cross)),
                         len(cross))
        # A normal two-leg move (span well under the cap) still drops.
        pair = self._pair('2026-03-01', '2026-03-15',
                          a1='RRSP', a2='RRSP')
        out2, _ = _drop_self_cancelling_transfers(pair)
        self.assertEqual(out2, [])


# ============================================================================
# End-to-end gain calc with TRANSFER (sheltered path)
# ============================================================================
class TestShelteredTransferGainCalc(unittest.TestCase):
    def test_tt_declared_token_is_opt_in(self):
        """Only a TRANSFER carrying the trailing DECLARED token gets the
        attestation marker. NOT every .tt TRANSFER: .tt-only books
        record genuine in-kind moves as TRANSFER rows, and a
        json→tt→json round trip must never grant broker rows
        attestation (2026-09 round-four audit finding 2)."""
        from taxjson.bin.taxjson_convert_tt import (parse_tt_line,
                                                    tx_to_tt_line)
        from taxjson.lib.pipeline import MANUAL_TRANSFER_DECLARATION
        decl = parse_tt_line("TRANSFER  2026-04-22  09:30:00  XYZ.US  "
                             "-500  CAD  96.40  48200.00  DECLARED",
                             "rrsp")
        self.assertEqual(decl["description"],
                         MANUAL_TRANSFER_DECLARATION)
        plain = parse_tt_line("TRANSFER  2026-04-22  09:30:00  XYZ.US  "
                              "-500  CAD  96.40  48200.00", "rrsp")
        self.assertNotIn("description", plain)
        # Round trips preserve the declaration bit in both states.
        self.assertTrue(tx_to_tt_line(decl).endswith(" DECLARED"))
        self.assertNotIn("DECLARED", tx_to_tt_line(plain))
        rt = parse_tt_line(tx_to_tt_line(decl), "rrsp")
        self.assertEqual(rt["description"], MANUAL_TRANSFER_DECLARATION)
        self.assertEqual(rt["id"], decl["id"])
        rt_plain = parse_tt_line(tx_to_tt_line(plain), "rrsp")
        self.assertNotIn("description", rt_plain)

    def test_acquired_sugar_expands_to_the_custody_idiom(self):
        """`ACQUIRED true-date ... ARRIVED arrival-date` is one line
        for the lost-history resolution: a BUYSELL at the true
        acquisition (real cost, real date) + a DECLARED
        counter-TRANSFER at the arrival date netting the broker leg."""
        from taxjson.bin.taxjson_convert_tt import (expand_acquired,
                                                    parse_tt_line)
        from taxjson.lib.pipeline import MANUAL_TRANSFER_DECLARATION
        lines = expand_acquired(
            "ACQUIRED 2024-09-16 09:30:00 XYZ.US 500 CAD "
            "96.40 48200.00 ARRIVED 2025-04-22")
        self.assertEqual(len(lines), 2)
        buy = parse_tt_line(lines[0], "rrsp")
        ctr = parse_tt_line(lines[1], "rrsp")
        self.assertEqual((buy["action"], buy["date"], buy["quantity"]),
                         ("BUYSELL", "2024-09-16", 500.0))
        self.assertAlmostEqual(buy["net_amount"], 48200.00)
        self.assertEqual((ctr["action"], ctr["date"], ctr["quantity"]),
                         ("TRANSFER", "2025-04-22", -500.0))
        self.assertEqual(ctr["description"],
                         MANUAL_TRANSFER_DECLARATION)
        # Non-ACQUIRED lines pass through untouched.
        self.assertIsNone(expand_acquired(
            "BUYSELL 2024-09-16 09:30:00 A.TO 1 CAD 1 1 0"))

    def test_acquired_sugar_malformed_raises(self):
        from taxjson.bin.taxjson_convert_tt import expand_acquired
        with self.assertRaises(ValueError):
            expand_acquired("ACQUIRED 2024-09-16 09:30:00 XYZ.US 500 "
                            "CAD 96.40 48200.00")     # no ARRIVED
        with self.assertRaises(ValueError):
            expand_acquired("ACQUIRED 2024-09-16 09:30:00 XYZ.US -500 "
                            "CAD 96.40 48200.00 ARRIVED 2025-04-22")

    def test_transfer_in_basis_used_for_subsequent_sell(self):
        """TRANSFER +200 @ basis 33060 → SELL -100 @ proceeds 19998 should
        compute a real gain using the transferred basis as ACB."""
        txs = [
            _tx_dict('TRANSFER', '2024-01-15', 'AAPL.US', 200, net=33060.0, account='RRSP'),
            _tx_dict('BUYSELL', '2024-06-20', 'AAPL.US', -100, 200.0, 19998, account='RRSP'),
        ]
        result = _run_gains_cli(txs, year=2024)
        # No manual_reporting_required — TRANSFER is treated as a buy.
        self.assertEqual(result.get('manual_reporting_required', []), [])
        # One gain entry: avg cost = 33060/200 = 165.30 per share, cost basis
        # for 100 shares = 16530, proceeds 19998 → gain 3468.
        gains = [t for t in result['transactions'] if t.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 3468.0, delta=1)

    def test_transfer_with_zero_basis_falls_through(self):
        """A TRANSFER with net=0 is rewritten to BUYSELL net=0 → cost=0 →
        subsequent sale shows inflated 'gain'. Accepted as the sheltered-
        account approximation."""
        txs = [
            _tx_dict('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=0.0, account='RRSP'),
            _tx_dict('BUYSELL', '2024-06-20', 'AAPL.US', -100, 200.0, 19998, account='RRSP'),
        ]
        result = _run_gains_cli(txs, year=2024)
        gains = [t for t in result['transactions'] if t.get('action') != 'DIVIDEND']
        # cost=0, proceeds≈19998 → gain≈19998
        self.assertAlmostEqual(gains[0]['gain'], 19998.0, delta=2)


# ============================================================================
# Taxable: clear error
# ============================================================================
class TestTaxableTransferRejected(unittest.TestCase):
    def test_cli_errors_with_taxable_and_transfer(self):
        txs = [
            _tx_dict('TRANSFER', '2024-01-15', 'AAPL.US', 100, net=15000.0, account='Margin'),
        ]
        with self.assertRaises(SystemExit) as cm:
            _run_gains_cli(txs, year=2024, taxable=True)
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
