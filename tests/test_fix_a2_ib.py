"""Regression tests for the second re-audit's IB-parser findings (lists
parsers-ib-01 / -03): overlapping statements of one account, adjustments
whose row is in another statement, corporate-action undo leftovers, the
delivered security of a corporate action, statement coverage, and the
console channel. Every fixture is synthetic: invented tickers and ISINs,
fake account ids marked pii-ok."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_sort import plan_dedup
from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from tax_rules import rule
from tax_rules.dual import gains_both
from taxjson.lib import country as C

from test_fix_l_ibparse import _book, _ACC_H, _DIV_H as _DIVA_H, _acc, _div
from test_fix_ibparse import _parse_account

from test_fix_ibparse import (HEAD, TRADES_H, XFER_H, CA_H, FII_H, DIV_H,
                              _trade, _xfer, _ca, _parse_ib, _brokerage_cli,
                              _period)

A1 = 'U5550001'   # pii-ok
A2 = 'U5550002'   # pii-ok
ADJ_H = 'Commission Adjustments,Header,Currency,Date,Description,Amount\n'


def _acct(*ids):
    return ('Account Information,Header,Field Name,Field Value\n'
            + ''.join(f'Account Information,Data,Account,{a}\n'
                      for a in ids))


def _stmt(start, end, *sections, accts=(A1,)):
    return HEAD + _period(start, end) + _acct(*accts) + ''.join(sections)


def _adj(cur, date, desc, amount):
    return f'Commission Adjustments,Data,{cur},{date},"{desc}",{amount}\n'


def _booked(files, *extra):
    """taxjson-brokerage over {name: text}, then the books' cross-file
    dedup (taxjson-sort): the rows the books keep, and stderr."""
    rc, out, err, side = _brokerage_cli(files, *extra)
    assert rc == 0, err
    rows = out['transactions']
    plan = plan_dedup(rows, (out.get('metadata') or {}).get(
        'source_accounts'))
    return [rows[i] for i in plan.keep], err


def _position(rows, sym):
    q = 0.0
    for t in rows:
        if t['symbol'] == sym and t['action'] in ('BUYSELL', 'ASSIGN'):
            q += t['quantity']
    return q


# ------------------------------------- overlapping statements: Ca / refunds
SPLIT3 = 'QZX(US9990000301) Split 3 for 1 (QZX, QZX CORP, US9990000301)'
SPLIT2 = 'QZX(US9990000301) Split 2 for 1 (QZX, QZX CORP, US9990000301)'


class TestOverlappingStatementCancellations(unittest.TestCase):
    """A2-0023 / A2-0024 / A2-0259 / A2-0088 / A2-1038: a statement that
    pairs a Ca with its original drops both, but an overlapping download
    taken before the cancellation still held the original, and dedup
    kept it (the cancelled split applied twice, the cancelled sale
    booked)."""

    BUY = TRADES_H + _trade('QZX', '2025-01-10, 10:00:00', 100, 10, -1000)

    @rule("CA-ACB-04")
    def test_split_cancelled_in_the_newer_overlapping_statement(self):
        h1 = _stmt('January 1, 2025', 'June 30, 2025', self.BUY,
                   CA_H, _ca(SPLIT3, 200, when='2025-03-03, 20:25:00'))
        fy = _stmt('January 1, 2025', 'December 31, 2025', self.BUY,
                   CA_H, _ca(SPLIT3, 200, when='2025-03-03, 20:25:00'),
                   _ca(SPLIT3, -200, when='2025-03-03, 20:25:00', code='Ca'),
                   _ca(SPLIT2, 100, when='2025-03-03, 20:25:00'))
        for files in ({'h1.csv': h1, 'fy.csv': fy},
                      {'fy.csv': fy, 'h1.csv': h1}):
            with self.subTest(order=list(files)):
                rows, err = _booked(files)
                self.assertEqual([t['quantity'] for t in rows
                                  if t['action'] == 'SPLIT'], [2.0], err)

    @rule("CA-ACB-04")
    def test_ca_only_statement_undoes_every_overlapping_copy(self):
        # Two copies of the original (a full-year statement and a
        # December re-download); the Ca and the rebook are in 2026.
        a = _stmt('January 1, 2025', 'December 31, 2025', self.BUY,
                  CA_H, _ca(SPLIT3, 200, when='2025-12-30, 20:25:00'))
        a2 = _stmt('December 1, 2025', 'December 31, 2025',
                   CA_H, _ca(SPLIT3, 200, when='2025-12-30, 20:25:00'))
        b = _stmt('January 1, 2026', 'February 27, 2026',
                  CA_H, _ca(SPLIT3, -200, when='2026-01-05, 20:25:00',
                            code='Ca'),
                  _ca(SPLIT2, 100, when='2026-01-05, 20:25:00'))
        rows, err = _booked({'a.csv': a, 'a2.csv': a2, 'b.csv': b})
        self.assertEqual([(t['date'], t['quantity']) for t in rows
                          if t['action'] == 'SPLIT'],
                         [('2026-01-05', 2.0)], err)
        self.assertNotIn('original row is not in', err)

    def test_trade_cancelled_in_the_newer_overlapping_statement(self):
        buy = _trade('QZY', '2025-01-10, 10:00:00', 200, 40, -8000)
        sell = _trade('QZY', '2025-06-02, 10:00:00', -100, 50, 5000,
                      code='C')
        a = _stmt('January 1, 2025', 'June 2, 2025', TRADES_H, buy, sell)
        b = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H, buy,
                  sell,
                  _trade('QZY', '2025-06-02, 10:00:00', 100, 50, -5000,
                         code='Ca'),
                  _trade('QZY', '2025-06-03, 10:00:00', -100, 51, 5100,
                         code='C'))
        rows, err = _booked({'a.csv': a, 'b.csv': b})
        sells = [t['price'] for t in rows
                 if t['symbol'] == 'QZY.US' and t['quantity'] < 0]
        self.assertEqual(sells, [51.0], err)
        self.assertEqual(_position(rows, 'QZY.US'), 100.0)

    def test_trade_of_another_broker_account_is_not_dropped(self):
        # The same fill in a DIFFERENT IB account's statement is a real
        # trade of that account, not a copy.
        sell = _trade('QZY', '2025-06-02, 10:00:00', -100, 50, 5000,
                      code='C')
        a = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H, sell,
                  _trade('QZY', '2025-06-02, 10:00:00', 100, 50, -5000,
                         code='Ca'))
        b = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H,
                  sell.replace(A1, A2), accts=(A2,))
        rc, out, err, _ = _brokerage_cli({'a.csv': a, 'b.csv': b})
        self.assertEqual(rc, 0, err)
        self.assertEqual(len([t for t in out['transactions']
                              if t['symbol'] == 'QZY.US']), 1, err)

    def test_transfer_cancelled_in_the_newer_overlapping_statement(self):
        x1 = _xfer('QZX', '2025-03-03', 100, 1000)
        h1 = _stmt('January 1, 2025', 'June 30, 2025', XFER_H, x1)
        fy = _stmt('January 1, 2025', 'December 31, 2025', XFER_H, x1,
                   _xfer('QZX', '2025-03-03', -100, -1000, code='Ca'),
                   _xfer('QZX', '2025-03-05', 100, 1100))
        rows, err = _booked({'h1.csv': h1, 'fy.csv': fy}, '--transfers')
        self.assertEqual([(t['date'], t['quantity']) for t in rows
                          if t['action'] == 'TRANSFER'],
                         [('2025-03-05', 100.0)], err)


class TestCommissionRefundAcrossStatements(unittest.TestCase):
    """A2-0258 / A2-0088 / A2-0605 / A2-1032 / A2-1036 / A2-0604."""

    @rule("CA-ACB-COMMREFUND")
    def test_refund_fold_is_repeated_on_the_overlapping_copy(self):
        buy = _trade('QZK', '2025-01-15, 10:00:00', 100, 10, -1000, -5)
        early = _stmt('January 1, 2025', 'February 5, 2025', TRADES_H, buy)
        full = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H, buy,
                     _trade('QZK', '2025-06-02, 10:00:00', -100, 11, 1100,
                            -1, code='C'),
                     ADJ_H, _adj('USD', '2025-02-10',
                                 'Refund (QZK, 100, 2025-01-15)', 4.0))
        rows, err = _booked({'early.csv': early, 'full.csv': full})
        buys = [t for t in rows if t['symbol'] == 'QZK.US'
                and t['quantity'] > 0]
        self.assertEqual(len(buys), 1, err)
        self.assertAlmostEqual(buys[0]['net_amount'], 1001.0)
        self.assertFalse([t for t in rows if t['action'] == 'FEE'])

    @rule("CA-ACB-COMMREFUND")
    def test_refund_in_the_next_statement_folds_into_its_trade(self):
        a = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H,
                  _trade('QZM', '2025-12-30, 10:00:00', 10, 400, -4000, -30))
        b = _stmt('January 1, 2026', 'December 31, 2026', TRADES_H,
                  _trade('QZM', '2026-02-10, 10:00:00', -10, 410, 4100, -1,
                         code='C'),
                  ADJ_H, _adj('USD', '2026-01-10',
                              'Refund (QZM, 10, 2025-12-30)', 29.0))
        rows, err = _booked({'a.csv': a, 'b.csv': b})
        buy = [t for t in rows if t['symbol'] == 'QZM.US'
               and t['quantity'] > 0]
        self.assertAlmostEqual(buy[0]['net_amount'], 4001.0, msg=err)
        self.assertAlmostEqual(buy[0]['fee'], 1.0)
        self.assertFalse([t for t in rows if t['action'] == 'FEE'])
        self.assertIn('folded into its trade', err)

    @rule("US-BASIS-COMMREFUND")
    def test_refund_naming_an_execution_of_the_order_row(self):
        body = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H,
                     _trade('QZW', '2025-06-15, 10:00:00', -440, 30, 13200,
                            -2.53, code='C'),
                     ADJ_H, _adj('USD', '2025-06-20',
                                 'Refund (QZW, -400, 2025-06-15)', 2.30))
        _, txs, err = _parse_ib(body)
        sale = [t for t in txs if t['symbol'] == 'QZW.US']
        self.assertEqual(len(sale), 1)
        self.assertAlmostEqual(sale[0]['net_amount'], 13200 - 2.53 + 2.30)
        self.assertFalse([t for t in txs if t['action'] == 'FEE'], err)

    @rule("CA-ACB-COMMREFUND")
    def test_refund_naming_an_option_trade(self):
        body = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H,
                     _trade('QZK 21MAR25 10 C', '2025-03-03, 10:00:00', 2,
                            1.5, -300, -2.6, cat='Equity and Index Options'),
                     ADJ_H, _adj('USD', '2025-03-10',
                                 'Refund (QZK 21MAR25 10 C, 2, 2025-03-03)',
                                 1.3))
        _, txs, err = _parse_ib(body)
        opt = [t for t in txs if t['action'] == 'BUYSELL']
        self.assertEqual(opt[0]['symbol'], 'QZK250321C00010000.US')
        self.assertAlmostEqual(opt[0]['net_amount'], 300 + 2.6 - 1.3)
        self.assertFalse([t for t in txs if t['action'] == 'FEE'], err)


class TestCashInLieuInTheNextStatement(unittest.TestCase):
    """A2-1037: a Dec 30 reverse split's cash in lieu paid Jan 5 never
    joined the split, so the pool lost the fraction twice."""

    @rule("CA-ACB-04")
    def test_cil_joins_the_split_of_the_previous_statement(self):
        rs = 'ZZR(US9990000501) Split 1 for 3 (ZZR, ZZR CORP, US9990000501)'
        cil = ('ZZR(US9990000501) Cash in Lieu of Fractional Shares '
               '(ZZR, ZZR CORP, US9990000501)')
        split = (CA_H + _ca(rs, -1000, when='2025-12-30, 20:25:00')
                 + _ca(rs, 333, when='2025-12-30, 20:25:00'))
        buy = TRADES_H + _trade('ZZR', '2025-06-02, 10:00:00', 1000, 3,
                                -3000)
        one = _stmt('January 1, 2025', 'December 31, 2025', buy, split,
                    _ca(cil, -0.3333, proceeds=1.0,
                        when='2025-12-31, 20:25:00'))
        a = _stmt('January 1, 2025', 'December 31, 2025', buy, split)
        b = _stmt('January 1, 2026', 'December 31, 2026', CA_H,
                  _ca(cil, -0.3333, proceeds=1.0,
                      when='2026-01-05, 20:25:00'))
        for files in ({'one.csv': one}, {'a.csv': a, 'b.csv': b}):
            with self.subTest(files=list(files)):
                rows, err = _booked(files)
                sp = [t for t in rows if t['action'] == 'SPLIT']
                self.assertAlmostEqual(sp[0]['quantity'], 0.3333333, 6, err)
                self.assertNotIn('matched no split', err)


class TestCorporateActionCaIdentity(unittest.TestCase):
    """A2-0603: a CAD-leg Ca undid a USD-leg split of the same
    description."""

    def test_ca_in_another_currency_does_not_undo_the_split(self):
        d = 'ZZT(US9990000601) Split 2 for 1 (ZZT, ZZT CORP, US9990000601)'
        _, txs, err = _parse_ib(HEAD + CA_H + _ca(d, 100)
                                + _ca(d, -100, cur='CAD', code='Ca'))
        self.assertEqual([t['symbol'] for t in txs
                          if t['action'] == 'SPLIT'], ['ZZT.US'])
        self.assertIn('original row is not in', err)


# --------------------------------------- Ca undo leaves no tally or note
def _tender(root, isin, qty, kind, when, proceeds=0):
    if kind == 'out':
        d = (f'{root}({isin}) Tendered to 99999998 1 FOR 1 ({root}.TEN, '
             f'{root} CORP - TENDER, {isin})')
    elif kind == 'in':
        d = (f'{root}.TEN({isin}) Tendered to 99999998 1 FOR 1 '
             f'({root}.TEN, {root} CORP - TENDER, {isin})')
    elif kind == 'back':
        d = (f'{root}.TEN(99999998) Merged(Voluntary Offer Allocation) '
             f'WITH {isin} 1 for 1 ({root}, {root} CORP, {isin})')
    else:
        d = (f'{root}.TEN(99999998) Merged(Voluntary Offer Allocation) '
             f'WITH {isin} 1 for 1 ({root}.TEN, {root} CORP - TENDER, '
             f'{isin})')
    return _ca(d, qty, proceeds=proceeds, cur='CAD', when=when)


class TestCancelledCorporateActionLeavesNoTrace(unittest.TestCase):
    """A2-0600 / A2-1030 / A2-0606 / A2-1029 / A2-1031 / A2-1034: a row
    IB cancelled (Ca) stayed in a tally, a NOTE or a warning."""

    def test_cancelled_merger_row_leaves_the_tally(self):
        old = ('QZM(US9990000701) Merged(Acquisition) WITH US9990000702 1 '
               'for 1 (QZM, QZM INC, US9990000701)')
        new = ('QZM(US9990000701) Merged(Acquisition) WITH US9990000702 1 '
               'for 1 (QZN, QZN INC, US9990000702)')
        _, _, err = _parse_ib(HEAD + CA_H + _ca(old, -10) + _ca(old, 10,
                              code='Ca') + _ca(old, -10) + _ca(new, 10))
        self.assertIn('2 merger/spin-off', err)
        self.assertIn('merger row (booked by taxjson-corp-actions after '
                      'the election): 2', err)

    def test_cancelled_spinoff_row_leaves_the_tally(self):
        d = ('QZPA(CA9990000001) Spinoff  1 for 5 (QZSP, SPINCO CORP, '
             'CA9990000002)')
        _, _, err = _parse_ib(HEAD + CA_H + _ca(d, 20, cur='CAD')
                              + _ca(d, -20, cur='CAD', code='Ca')
                              + _ca(d, 20, cur='CAD',
                                    when='2025-03-03, 20:25:00'))
        self.assertIn('1 merger/spin-off', err)
        self.assertIn('spin-off row (booked by taxjson-corp-actions after '
                      'the election): 1', err)

    def test_cancelled_cash_takeover_prints_no_note(self):
        d = 'QZCT(US9990000801) Merged(Acquisition) FOR USD 30.00 PER SHARE'
        d31 = 'QZCT(US9990000801) Merged(Acquisition) FOR USD 31.00 PER SHARE'
        _, txs, err = _parse_ib(HEAD + CA_H + _ca(d, -100, proceeds=3000)
                                + _ca(d, 100, proceeds=-3000, code='Ca')
                                + _ca(d31, -100, proceeds=3100))
        self.assertEqual([t['net_amount'] for t in txs], [3100.0])
        self.assertNotIn('for 3000.00', err)
        self.assertIn('for 3100.00', err)

    def test_cancelled_positive_takeover_row_leaves_the_skip_count(self):
        d = 'QZCT(US9990000801) Merged(Acquisition) FOR USD 30.00 PER SHARE'
        _, _, err = _parse_ib(HEAD + CA_H + _ca(d, 100)
                              + _ca(d, -100, code='Ca'))
        self.assertNotIn('cash-takeover row with a positive quantity', err)

    def test_cancelled_tender_leg_leaves_the_journal_tally(self):
        out = ('QZAU(CA9990000021) Tendered to 99999998 1 FOR 1 '
               '(QZAU.TEN, QZAU CORP - TENDER, CA9990000021)')
        cancelled = (_ca(out, -100, cur='CAD', when='2025-06-02, 20:25:00')
                     + _ca(out, 100, cur='CAD', when='2025-06-02, 20:25:00',
                           code='Ca'))
        trip = ''.join(_tender('QZAU', 'CA9990000021', q, k,
                               '2025-06-03, 20:25:00')
                       for q, k in ((-100, 'out'), (100, 'in'),
                                    (-100, 'alloc'), (100, 'back')))
        _, txs, err = _parse_ib(HEAD + CA_H + cancelled + trip)
        self.assertEqual(txs, [])
        self.assertIn('QZAU.TO: 4 tender/voluntary-offer journal row(s) '
                      '(2025-06-03)', err)

    def test_cancelled_zero_proceeds_cil_prints_no_warning(self):
        d = ('QZF(US9990000901) Cash in Lieu of Fractional Shares (QZF, '
             'QZF CORP, US9990000901)')
        _, txs, err = _parse_ib(HEAD + CA_H + _ca(d, -0.5)
                                + _ca(d, 0.5, code='Ca'))
        self.assertEqual(txs, [])
        self.assertNotIn('Proceeds 0', err)


# ----------------------------------- the security a corporate action delivers
def _fii(sym, isin, conid, exch):
    return (f'Financial Instrument Information,Data,Stocks,"{sym}",{sym} '
            f'FUND,{conid},{isin},,{exch},1,,,COMMON,,\n')


class TestCorporateActionListingAndDeliveredLine(unittest.TestCase):

    def test_tsx_usd_unit_split_takeover_and_refund_stay_on_the_tsx(self):
        # A2-0086: the S010-06 listing rule (ZSP.U.TO) for every
        # corporate action and the refund FEE row.
        body = (HEAD + TRADES_H
                + _trade('ZSP.U', '2025-02-03, 10:00:00', 100, 30, -3000)
                + _trade('HXU.U', '2025-02-03, 10:00:00', 100, 20, -2000)
                + CA_H
                + _ca('ZSP.U(CA9990001001) Split 2 for 1 (ZSP.U, ZSP FUND, '
                      'CA9990001001)', 100)
                + _ca('HXU.U(CA9990001002) Merged(Acquisition) FOR USD '
                      '25.00 PER SHARE', -100, proceeds=2500)
                + ADJ_H + _adj('USD', '2025-03-10',
                               'Refund (ZSP.U, 50, 2025-01-02)', 0.5)
                + FII_H + _fii('ZSP.U', 'CA9990001001', '990001001', 'TSE')
                + _fii('HXU.U', 'CA9990001002', '990001002', 'TSE'))
        _, txs, err = _parse_ib(body)
        syms = {(t['action'], t['symbol']) for t in txs}
        self.assertIn(('SPLIT', 'ZSP.U.TO'), syms, err)
        self.assertIn(('FEE', 'ZSP.U.TO'), syms)
        self.assertEqual([t['symbol'] for t in txs
                          if t['action'] == 'BUYSELL'
                          and t['quantity'] < 0], ['HXU.U.TO'])
        self.assertFalse([s_ for _a, s_ in syms if s_.endswith('.U.US')])

    @rule("CA-CORP-02")
    @rule("US-CORP-02")
    def test_split_that_renames_moves_the_pool(self):
        # A2-0085 / A2-0257: 'OLDT ... Split 1 for 10 (NEWT, ...)'.
        new = ('QZOLD(US9990001101) Split 1 for 10 (QZNEW, QZNEW INC, '
               'US9990001102)')
        old = ('QZOLD(US9990001101) Split 1 for 10 (QZOLD.OLD, QZOLD INC, '
               'US9990001101)')
        body = (HEAD + TRADES_H
                + _trade('QZOLD', '2025-02-03, 10:00:00', 100, 10, -1000)
                + _trade('QZNEW', '2025-06-02, 10:00:00', -10, 120, 1200,
                         code='C')
                + CA_H + _ca(old, -100, when='2025-03-03, 20:25:00')
                + _ca(new, 10, when='2025-03-03, 20:25:00'))
        _, txs, err = _parse_ib(body)
        sp = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual([(t['symbol'], t['symbol_new'], t['quantity'])
                          for t in sp], [('QZOLD.US', 'QZNEW.US', 0.1)])
        r = gains_both(_book(txs), year=2025)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                self.assertAlmostEqual(r[c]['summary']['total_gain'], 200.0)

    def test_cancelled_renaming_leg_restores_the_symbol(self):
        new = ('QZOLD(US9990001101) Split 1 for 10 (QZNEW, QZNEW INC, '
               'US9990001102)')
        old = ('QZOLD(US9990001101) Split 1 for 10 (QZOLD.OLD, QZOLD INC, '
               'US9990001101)')
        _, txs, _ = _parse_ib(HEAD + CA_H + _ca(old, -100) + _ca(new, 10)
                              + _ca(new, -10, code='Ca'))
        self.assertEqual([(t['symbol'], t['symbol_new']) for t in txs
                          if t['action'] == 'SPLIT'],
                         [('QZOLD.US', 'QZOLD.US')])

    @rule("CA-STKDIV-01")
    def test_stock_dividend_in_another_security_is_unbooked(self):
        # A2-0093: GOOGL-style row paying GOOG-style shares.
        body = (HEAD + TRADES_H
                + _trade('QZPAR', '2025-02-03, 10:00:00', 100, 10, -1000)
                + CA_H + _ca('QZPAR(US9990001201) Stock Dividend '
                             'US9990001202 1 for 10 (QZNEWC, QZNEWC INC, '
                             'US9990001202)', 10, value=500))
        _, txs, err = _parse_ib(body)
        self.assertEqual([(t['symbol'], t['quantity']) for t in txs],
                         [('QZPAR.US', 100.0)])
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('QZNEWC', err)

    @rule("US-STKDIV-02")
    def test_stock_dividend_in_another_security_is_unbooked_us(self):
        _, txs, err = _parse_ib(HEAD + CA_H + _ca(
            'QZPAR(US9990001201) Stock Dividend US9990001202 1 for 10 '
            '(QZNEWC, QZNEWC INC, US9990001202)', 10, value=500))
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)

    @rule("CA-STKDIV-01")
    def test_same_security_stock_dividend_is_still_booked(self):
        _, txs, err = _parse_ib(HEAD + CA_H + _ca(
            'QZPAR(US9990001201) Stock Dividend US9990001201 1 for 10 '
            '(QZPAR, QZPAR INC, US9990001201)', 10, value=500))
        self.assertEqual([(t['symbol'], t['type']) for t in txs],
                         [('QZPAR.US', 'stock_dividend')])
        self.assertNotIn('UNBOOKED', err)

    def test_cash_in_lieu_of_the_delivered_security(self):
        # A2-1033: the fraction is of NEWC, not the parent.
        _, txs, _ = _parse_ib(HEAD + CA_H + _ca(
            'QZPAR(US9990001201) Cash in Lieu of Fractional Shares '
            '(QZNEWC, QZNEWC INC, US9990001202)', -0.5, proceeds=15))
        self.assertEqual([t['symbol'] for t in txs], ['QZNEWC.US'])


# ------------------------------------------------- statement coverage
def _prepare(files, tax_year=None):
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            paths.append(p)
        with contextlib.redirect_stderr(err):
            IbBrokerage.prepare_files(paths, tax_year=tax_year)
    return err.getvalue()


class TestStatementCoveragePerAccountAndYear(unittest.TestCase):
    """A2-0091 / A2-0261 / A2-0262 / A2-0609."""

    def test_other_accounts_statement_does_not_hide_a_gap(self):
        err = _prepare({
            'a.csv': _stmt('January 1, 2025', 'December 31, 2025'),
            'b.csv': _stmt('January 1, 2025', 'June 30, 2025', accts=(A2,))},
            tax_year=2025)
        self.assertIn('end 2025-06-30, before the end of 2025', err)
        self.assertIn('U5***', err)
        # ... and the label holding two IB accounts is said.
        self.assertIn('belong to 2 IB accounts', err)

    def test_prior_year_only_statement_is_flagged_for_the_project_year(self):
        err = _prepare({'a.csv': _stmt('January 1, 2024',
                                       'December 31, 2024')}, tax_year=2025)
        self.assertIn('none covers 2025', err)

    def test_weekend_split_and_a_statement_into_next_year_are_quiet(self):
        # The owner's 2024 download pattern: Jan 1 - Fri Dec 27 and
        # Mon Dec 30 - Jan 1 2025.
        files = {'a.csv': _stmt('January 1, 2024', 'December 27, 2024'),
                 'b.csv': _stmt('December 30, 2024', 'January 1, 2025')}
        self.assertNotIn('ATTENTION', _prepare(files, tax_year=2024))
        rc, _out, err, _ = _brokerage_cli(files, '--tax-year', '2024')
        self.assertEqual(rc, 0, err)
        self.assertNotIn('uncovered', err)
        self.assertNotIn('before the end of', err)

    def test_weekday_gap_is_still_flagged(self):
        err = _prepare({'a.csv': _stmt('January 1, 2024', 'December 24, 2024'),
                        'b.csv': _stmt('December 30, 2024',
                                       'December 31, 2024')}, tax_year=2024)
        self.assertIn('2024-12-25 .. 2024-12-29 uncovered', err)


# -------------------------------------------- dividend accruals, per account
P25 = 'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
P26 = 'Statement,Data,Period,"January 1, 2026 - January 31, 2026"\n'


class TestAccrualsAcrossStatementsAndAccounts(unittest.TestCase):

    def test_other_accounts_posting_does_not_pay_this_accrual(self):
        # A2-1035 / A2-1039.
        a = (HEAD + P25 + _acct(A1) + _ACC_H
             + _acc('QZT', '2025-12-15', '2025-12-15', '2025-12-31', 1000,
                    0.35, 350, 'Po', cur='CAD'))
        b = (HEAD + P25 + _acct(A2) + _DIVA_H
             + _div('QZT', 'CA9990001301', '2025-12-31', 350, cur='CAD',
                    rate=0.35).replace(A1, A2))
        _, err = _parse_account({'a.csv': a, 'b.csv': b})
        self.assertIn('accrued but not yet booked', err)
        # The open accrual is an ATTENTION line (run's console, A2-0264).
        self.assertIn('warning: ATTENTION: 1 dividend(s)', err)

    @rule("US-INC-DATE-RIC")
    def test_ex_date_from_the_previous_statements_accrual(self):
        # A2-0601: the Po/Re rows are in the 2025 statement, the
        # posting in 2026.
        y25 = (HEAD + P25 + _acct(A1) + _ACC_H
               + _acc('QZS', '2025-12-22', '2025-12-22', '2026-01-05', 400,
                      0.25, 100, 'Po'))
        y26 = (HEAD + P26 + _acct(A1) + _DIVA_H
               + _div('QZS', 'US9990001401', '2026-01-05', 100,
                      rate=0.25))
        txs, err = _parse_account({'a.csv': y25, 'b.csv': y26})
        div = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(div[0].get('ex_date'), '2025-12-22', err)

    @rule("US-INC-DATE-RIC")
    def test_ex_date_of_a_posting_a_day_late(self):
        # A2-0602: posted 01-16 for an accrual paying 01-15.
        body = (HEAD + P26 + _ACC_H
                + _acc('QZS', '2026-01-02', '2025-12-22', '2026-01-15', 400,
                       0.25, 100, 'Po')
                + _acc('QZS', '2026-01-16', '2025-12-22', '2026-01-15', 400,
                       0.25, -100, 'Re')
                + _DIVA_H + _div('QZS', 'US9990001401', '2026-01-16', 100,
                                 rate=0.25))
        _, txs, err = _parse_ib(body)
        div = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(div[0].get('ex_date'), '2025-12-22', err)


class TestConsoleAttention(unittest.TestCase):

    def test_consolidated_statement_is_an_attention_line(self):
        # A2-0610: two accounts in one statement reach the console.
        _, _, err = _parse_ib(_stmt('January 1, 2025', 'December 31, 2025',
                                    TRADES_H, _trade('QZA', '2025-03-03, '
                                                     '10:00:00', 1, 10, -10),
                                    accts=(A1, A2)))
        self.assertRegex(err, r'warning: ATTENTION: \S+: IB statement '
                              r'spans 2 accounts')


class TestChecklistIbStatementCoverage(unittest.TestCase):
    """A2-0262: checklist inputs-frozen said done for an IB account whose
    only statement is the prior year."""

    def test_prior_year_ib_statement_is_attention(self):
        from datetime import date
        from taxjson.lib import checklist as cl
        from test_checklist import _project, _ctx
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)                # another source runs to Feb 2026
            ib = root / 'inputs' / 'margin' / 'ib.csv'
            ib.write_text(_stmt('January 1, 2024', 'December 31, 2024'))
            r = cl.d_inputs_frozen(_ctx(root, {}, today=date(2026, 3, 1)))
            self.assertEqual(r.status, 'attention')
            self.assertIn('no IB statement', r.detail)
            ib.write_text(_stmt('January 1, 2025', 'December 31, 2025'))
            r = cl.d_inputs_frozen(_ctx(root, {}, today=date(2026, 3, 1)))
            self.assertEqual(r.status, 'done', r.detail)


if __name__ == '__main__':
    unittest.main()
