"""Re-audit-2 test pins, group G2 (parsers and message fixes).

Each test pins a fix that held on the audited commit but that no test
failed on when reverted (A2-0176, A2-0521, A2-0538, A2-0897, A2-1569,
A2-1582, A2-1612, A2-0894, A2-1597). Every fixture is SYNTHETIC: fake
account ids, invented tickers and amounts.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from tax_rules import rule
from test_fix_rbcqt import q, qdiv, qt_parse, of


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _qt_refused(*bodies):
    """Parse Questrade rows; return the BrokerageParseError text (or
    fail when the rows are accepted)."""
    try:
        txs, err, _ = qt_parse(*bodies)
    except BrokerageParseError as e:
        return str(e)
    raise AssertionError(f"accepted: {[(t['action'], t['net_amount']) for t in txs]}")


# ------------------------------------------------- Questrade decimal commas

class TestQuestradeDecimalCommaRefused(unittest.TestCase):
    """A2-0176 (questrade.py TRANSFER BOOK VALUE parse_strict_number),
    A2-1612 (BRW journal BOOK VALUE parse_strict_number) and A2-0521
    (questrade.py _num): a decimal comma is refused, never read 100x by
    stripping the comma."""

    def test_transfer_book_value_decimal_comma_is_refused(self):
        msg = _qt_refused(q(td='2025-03-03', action='TF6', sym='QZG',
                            desc='QZG INC TRANSFER BOOK VALUE 1234,56',
                            qty='10', price='0', gross='0', comm='0',
                            net='0', act='Transfers'))
        self.assertIn('TRANSFER BOOK VALUE', msg)
        self.assertIn('1234,56', msg)

    def test_transfer_book_value_thousands_comma_is_read(self):
        txs, _, _ = qt_parse(q(td='2025-03-03', action='TF6', sym='QZG',
                               desc='QZG INC TRANSFER BOOK VALUE 1,234.56',
                               qty='10', price='0', gross='0', comm='0',
                               net='0', act='Transfers'))
        tr = of(txs, action='TRANSFER')
        self.assertEqual(len(tr), 1)
        self.assertAlmostEqual(tr[0]['net_amount'], 1234.56)

    def test_brw_journal_book_value_decimal_comma_is_refused(self):
        body = (q(action='BRW', sym='DLR.TO', desc='GLOBAL X US DLR CURRENCY '
                  'ETF UNIT CL A JOURNAL POSITION TO USD', qty='-300',
                  price='0', gross='0', comm='0', net='0', cur='CAD',
                  act='Other')
                + q(action='BRW', sym='DLR.U.TO', desc='GLOBAL X US DLR '
                    'CURRENCY ETF UNIT CL A JOURNAL POSITION FROM CAD BOOK '
                    'VALUE: $2468,13 CNV@ 1.3579', qty='300', price='0',
                    gross='0', comm='0', net='0', act='Other'))
        msg = _qt_refused(body)
        self.assertIn('BOOK VALUE', msg)
        self.assertIn('2468,13', msg)

    def test_dividend_net_amount_decimal_comma_is_refused(self):
        msg = _qt_refused(
            q(td='2025-01-10', sym='QZA', desc='QZA CORP WE ACTED AS AGENT')
            + qdiv('QZA', 'QZA CORP CASH DIV ON 10 SHS', '12,34'))
        self.assertIn('12,34', msg)


# ------------------------------------------------- Questrade padded symbols

class TestQuestradePaddedSymbol(unittest.TestCase):
    """A2-0897 (questrade.py trade Symbol .strip(), S062-22) and A2-1582
    (_resolve_symbol and _parse_fee .strip()): a Symbol cell with a
    trailing blank books the same pool as the clean symbol — never
    'XYZ..TO'."""

    def test_trade_symbol_with_trailing_blank(self):
        txs, _, _ = qt_parse(
            q(td='2025-02-03', sym='XYZ ', desc='XYZ CORP WE ACTED AS AGENT',
              qty='100', price='10', gross='-1000', comm='0', net='-1000',
              cur='CAD')
            + q(td='2025-03-03', action='Sell', sym='XYZ',
                desc='XYZ CORP WE ACTED AS AGENT', qty='-100', price='20',
                gross='2000', comm='0', net='2000', cur='CAD'))
        self.assertEqual({t['symbol'] for t in txs}, {'XYZ.TO'})

    def test_dividend_split_and_fee_symbol_with_trailing_blank(self):
        txs, _, _ = qt_parse(
            q(td='2025-02-03', sym='XYZ', desc='XYZ CORP WE ACTED AS AGENT',
              qty='100', price='10', gross='-1000', comm='0', net='-1000',
              cur='CAD')
            + qdiv('XYZ ', 'XYZ CORP CASH DIV ON 100 SHS', '12.50',
                   cur='CAD')
            + q(td='2025-07-02', action='DIS', sym='XYZ ',
                desc='XYZ CORP 2 FOR 1 STOCK SPLIT ON 100 SHS', qty='100',
                price='0', gross='0', comm='0', net='0', cur='CAD',
                act='Other')
            + q(td='2025-08-01', action='FCH', sym='XYZ ',
                desc='XYZ CORP ADR FEE', qty='0', price='0', gross='0',
                comm='0', net='-3.00', cur='CAD', act='Fees and rebates'))
        by_action = {t['action']: t['symbol'] for t in txs}
        self.assertEqual(by_action.get('DIVIDEND'), 'XYZ.TO')
        self.assertEqual(by_action.get('SPLIT'), 'XYZ.TO')
        self.assertEqual(by_action.get('FEE'), 'XYZ.TO')
        self.assertEqual({t['symbol'] for t in txs}, {'XYZ.TO'})


# ------------------------------------------------- Questrade fee rebate sign

class TestQuestradeFeeRebateSign(unittest.TestCase):
    """A2-1569 (questrade.py _parse_fee 'net_amount': -net): a 'Fees and
    rebates' charge is a positive fee, a rebate a negative one — abs()
    would book the rebate as a charge."""

    def test_rebate_is_a_negative_fee(self):
        txs, _, _ = qt_parse(
            q(td='2025-08-01', action='FCH', sym='', desc='ACCOUNT FEE',
              qty='0', price='0', gross='0', comm='0', net='-10.00',
              cur='CAD', act='Fees and rebates')
            + q(td='2025-08-05', action='FCH', sym='', desc='FEE REBATE',
                qty='0', price='0', gross='0', comm='0', net='4.00',
                cur='CAD', act='Fees and rebates'))
        fees = sorted(t['net_amount'] for t in of(txs, action='FEE'))
        self.assertEqual(fees, [-4.0, 10.0])


# ------------------------------------- IB / RBC / Webull decimal commas

_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n')


def _ib_parse(body):
    from taxjson.lib.brokerages.ib_extractor import IbBrokerage
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'ib.csv'
        p.write_text(_IB_HEAD + body, encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return IbBrokerage().parse_file(p)


class TestIbIncomeDecimalCommaRefused(unittest.TestCase):
    """A2-0521 (ib_extractor.py Dividends 'Amount' and Withholding Tax
    'Amount' parse_strict_number): a decimal comma in an income cell is
    refused, never booked 100x."""

    DIV = ('Dividends,Header,Currency,Account,Date,Description,Amount\n'
           'Dividends,Data,USD,U5550001,2025-03-15,'  # pii-ok
           'QZA(US0000000001) Cash Dividend USD 0.375 per Share '
           '(Ordinary Dividend),{amt}\n')
    WHT = ('Withholding Tax,Header,Currency,Account,Date,Description,Amount\n'
           'Withholding Tax,Data,USD,U5550001,2025-03-15,'  # pii-ok
           'QZA(US0000000001) Cash Dividend USD 0.375 per Share - US Tax,'
           '{amt}\n')

    def test_dividend_amount_reads_a_plain_number(self):
        txs = _ib_parse(self.DIV.format(amt='37.50')
                        + self.WHT.format(amt='-5.63'))
        self.assertEqual(sorted((t['action'], t['net_amount']) for t in txs
                                if t['action'] in ('DIVIDEND', 'TAX')),
                         [('DIVIDEND', 37.5), ('TAX', 5.63)])

    def test_dividend_amount_decimal_comma_is_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _ib_parse(self.DIV.format(amt='"37,50"'))
        self.assertIn('37,50', str(cm.exception))

    def test_withholding_amount_decimal_comma_is_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _ib_parse(self.DIV.format(amt='37.50')
                      + self.WHT.format(amt='"-5,63"'))
        self.assertIn('5,63', str(cm.exception))


class TestRbcReinvestPriceDecimalCommaRefused(unittest.TestCase):
    """A2-0521 (rbc_direct.py 'Reinvest @' price desc_number): a decimal
    comma in the reinvest price is refused, never read 100x."""

    def test_reinvest_price_decimal_comma_is_refused(self):
        from test_fix_rbcqt import rbc_parse, rrow
        with self.assertRaises(BrokerageParseError) as cm:
            rbc_parse(rrow(
                "April 7, 2025", "Dividends", "RBF8411", "RBC INTL EQUITY O",
                "5", "", "", "USD",
                "DIV - Rbc International Equity Series O U$ (8411) As Of "
                "04/07/25 Reinvest @ $20,50"))
        self.assertIn('20,50', str(cm.exception))


class TestWebullProceedsDecimalCommaRefused(unittest.TestCase):
    """A2-0521 (webull.py Proceeds parse_strict_number). A priced row is
    also caught by the qty x price fit check; a $0.00 option close whose
    commission exceeds the gross has nothing else to catch a decimal
    comma, so it must be the strict parse that refuses '(1,50)'."""

    def test_zero_price_close_debit_decimal_comma_is_refused(self):
        from test_fix_l_parsers2_webull import (_parse,
                                                TestSaleDebitKeepsItsSign)
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(TestSaleDebitKeepsItsSign._BUY
                   + 'USD,21-02-2025,SELL,,,,-2,0.00,,"(1,50)"\n')
        self.assertIn('(1,50)', str(cm.exception))


class TestQuestradeSpinoffLookupDecimalCommaRefused(unittest.TestCase):
    """A2-0521 (corp_actions.py parse_questrade_corporate_actions: the
    held-quantity lookup's parse_strict_number, the S072-17 sibling): a
    trade Quantity with a decimal comma is refused, never sized 10x."""

    def test_trade_quantity_decimal_comma_is_refused(self):
        from taxjson.lib.corp_actions import parse_questrade_corporate_actions
        from test_fix_rbcqt import QH
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'questrade_2025.csv'
            p.write_text(QH + q(qty='10,5', price='50', gross='-525',
                                comm='0', net='-525'), encoding='utf-8')
            with self.assertRaises(BrokerageParseError) as cm:
                parse_questrade_corporate_actions(p)
        self.assertIn('10,5', str(cm.exception))



class TestRbcCoverageBoundaries(unittest.TestCase):
    """A2-1569 (rbc_direct.py rbc_coverage_messages): the exact boundary
    days — today == Dec 31 (the year is still open: no finding), an
    export taken ON Dec 31 (the 'taken on Dec 31 itself' note), and an
    export taken exactly on the year-end posting day (June 30 of the
    next year: it holds the book-cost adjustments, no R1-85 note)."""

    @staticmethod
    def _msgs(as_of, today):
        from datetime import date
        from taxjson.lib.brokerages.rbc_direct import rbc_coverage_messages
        return rbc_coverage_messages(
            [('rbc_2025.csv', as_of, ['2025-03-03', '2025-11-03'])],
            2025, listings=None, today=date.fromisoformat(today))

    def test_today_is_dec_31_the_year_is_still_open(self):
        self.assertEqual(self._msgs('2025-12-01', '2025-12-31'), [])
        self.assertTrue(any('ATTENTION' in m for m in
                            self._msgs('2025-12-01', '2026-01-01')))

    def test_export_taken_on_dec_31_gets_the_same_day_note(self):
        msgs = self._msgs('2025-12-31', '2026-07-15')
        self.assertTrue(any('taken on 2025-12-31 itself' in m
                            for m in msgs), msgs)
        self.assertFalse(any('ATTENTION' in m for m in msgs), msgs)

    def test_export_taken_on_the_posting_day_holds_the_adjustments(self):
        on_day = self._msgs('2026-06-30', '2026-07-15')
        self.assertFalse(any('year-end book-cost' in m for m in on_day),
                         on_day)
        day_before = self._msgs('2026-06-29', '2026-07-15')
        self.assertTrue(any('year-end book-cost' in m for m in day_before),
                        day_before)



class TestCoinbaseNewlineGuard(unittest.TestCase):
    """A2-0894 (coinbase.py: a cell holding a line break is refused,
    S056-06). The swallowed span here keeps exactly the header's 11
    cells, so the width check cannot catch it — only the newline guard
    does; without it the ETH and SOL buys vanish silently."""

    def test_swallow_that_keeps_the_header_width_is_refused(self):
        from test_fix_m_parsers2_crypto import _parse_cb
        rows = ('cb1,2025-01-10 10:00:00 UTC,Buy,BTC,0.01,CAD,100000,1000,'
                '1010,10,"Bought 0.01 BTC ""\n'
                'cb2,2025-01-11 10:00:00 UTC,Buy,ETH,0.5,CAD,4000,2000,2010,'
                '10,Bought 0.5 ETH\n'
                'cb3,2025-01-12 10:00:00 UTC,Buy,SOL,1,CAD,200,200,201,1,'
                'Bought 1 SOL"\n')
        with self.assertRaisesRegex(ValueError, 'spans several lines'):
            _parse_cb(rows)



# ------------------------------------------------- day-window boundaries

class TestOptionExpiryBookingWindow(unittest.TestCase):
    """A2-0538 (base.py option_expiry_booking_date: at most 7 days): an
    expiry posted up to a week late is booked on the expiry date; one
    posted 8 days or more after the description's date keeps its
    posting date (a garbled description never relocates a row far)."""

    def test_seven_days_relocates_eight_does_not(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        f = BaseBrokerage.option_expiry_booking_date
        self.assertEqual(f('2026-01-02', '12/26/25'), '2025-12-26')
        self.assertEqual(f('2026-01-03', '12/26/25'), '2026-01-03')
        self.assertEqual(f('2026-01-02', '01/02/26'), '2026-01-02')


class TestIbCashInLieuWindow(unittest.TestCase):
    """A2-0538 (ib_extractor.py _IB_CIL_WINDOW = 7): a cash-in-lieu row
    joins the split of its symbol within 7 days in either row order; 8
    days apart it matches no split."""

    def _err(self, cil_when, split_when, cil_first):
        from test_fix_ibparse import HEAD, CA_H, _ca, _parse_ib
        from test_fix_l_ibparse import _CIL, _split_desc
        cil = _ca(_CIL, -0.5, proceeds=5, when=cil_when)
        split = (_ca(_split_desc(2, 1), -999.5, when=split_when)
                 + _ca(_split_desc(2, 1), 1999, when=split_when))
        _, _, err = _parse_ib(HEAD + CA_H + (cil + split if cil_first
                                             else split + cil))
        return err

    def test_seven_days_joins_eight_days_does_not(self):
        for first in (True, False):
            self.assertNotIn('matched no split', self._err(
                '2026-09-08, 20:25:00', '2026-09-15, 20:25:00', first))
            self.assertIn('matched no split', self._err(
                '2026-09-07, 20:25:00', '2026-09-15, 20:25:00', first))
            self.assertIn('matched no split', self._err(
                '2026-09-23, 20:25:00', '2026-09-15, 20:25:00', first))



@rule("CA-OPT-08")
@rule("US-OPT-05")
class TestAssignLegLagWindow(unittest.TestCase):
    """A2-0538 (core.py _MARKED_LEG_MAX_LAG_DAYS = 7): an assignment's
    marked stock leg dated 7 days after the option row takes its
    premium; 8 days after it is another assignment's leg, and the
    premium is reported unconsumed (both engines share the ledger)."""

    def _book(self, sx, leg_date):
        from test_fix_a2_engine_assign import _tx
        put, stk = f'XYZ251212P00012000.{sx}', f'XYZ.{sx}'
        return stk, [
            _tx('BUYSELL', '2025-11-03', put, -1, 1.99, 199),
            _tx('ASSIGN', '2025-12-12', put, 1, 0, 0, time='16:00:00'),
            _tx('ASSIGN', leg_date, stk, 100, 12, 1200, time='16:00:01'),
        ]

    def test_seven_days_pairs_eight_days_does_not(self):
        from test_fix_a2_engine_assign import _ENGINES, _run, _held_cost
        for country, eng, sx in _ENGINES:
            with self.subTest(country=country):
                stk, book = self._book(sx, '2025-12-19')
                res, err = _run(eng, book)
                self.assertEqual(_held_cost(res, stk), 1001.0)
                self.assertNotIn('unconsumed', err)
                stk, book = self._book(sx, '2025-12-20')
                res, err = _run(eng, book)
                self.assertEqual(_held_cost(res, stk), 1200.0)
                self.assertIn('unconsumed', err)



class TestRbcHeldWindow(unittest.TestCase):
    """A2-0538 (rbc_direct.py _HELD_WINDOW_DAYS = 45): a listing whose
    last activity (a sale to zero) was 45 days before an income row still
    counts as held for it; 46 days later it does not."""

    def test_forty_five_days_held_forty_six_not(self):
        from taxjson.lib.brokerages.rbc_direct import _Listing
        li = _Listing('QZR', 'CAD')
        li.events = [('2025-01-02', 0, 0, 10.0), ('2025-03-03', 0, 1, -10.0)]
        li.finish()
        self.assertTrue(li.held_on('2025-04-17'))
        self.assertFalse(li.held_on('2025-04-18'))


class TestSplitCopyDateWindow(unittest.TestCase):
    """A2-0538 (corporate_timeline.py SPLIT_DATE_WINDOW_DAYS = 7): two
    copies of one split (same symbol and ratio) dated up to 7 days apart
    are one event; 8 days apart they are two."""

    def test_seven_days_is_one_event_eight_is_two(self):
        from taxjson.lib.corporate_timeline import split_seen
        seen = set()
        self.assertIsNone(split_seen(seen, 'QZK.US', '2025-06-11', 10, ''))
        self.assertEqual(split_seen(seen, 'QZK.US', '2025-06-18', 10, ''),
                         '2025-06-11')
        seen = set()
        self.assertIsNone(split_seen(seen, 'QZK.US', '2025-06-11', 10, ''))
        self.assertIsNone(split_seen(seen, 'QZK.US', '2025-06-19', 10, ''))


class TestJournalCandidateDateWindow(unittest.TestCase):
    """A2-0538 (pipeline.py _JOURNAL_CANDIDATE_PAD_DAYS = 7): an out-leg
    of X and an in-leg of Y of equal quantity 7 days apart print the
    unmapped cross-listing journal NOTE; 8 days apart they do not."""

    NOTE = "possible unmapped cross-listing journal"

    def _err(self, in_date):
        from test_mutation_pins import _tx, _drop
        shel = [_tx('TRANSFER', '2026-06-15', 'BTG.US', -500.0),
                _tx('TRANSFER', in_date, 'BTO.TO', 500.0)]
        return _drop(shel, [])[2]

    def test_seven_days_pairs_eight_does_not(self):
        self.assertIn(self.NOTE, self._err('2026-06-22'))
        self.assertNotIn(self.NOTE, self._err('2026-06-23'))



# ------------------------------------------------- A2-1597 message fixes
#
# Each fix below changed only a user-facing message, and reverting it
# left the suite green. Hunk ids are the audit's (commit_hunk).

import json
import os
import subprocess
import sys


def _tool_help(module, *args):
    """`python -m taxjson.bin.<module> <args> --help`, unwrapped."""
    env = {**os.environ, 'COLUMNS': '1000'}
    r = subprocess.run([sys.executable, '-m', f'taxjson.bin.{module}', *args,
                        '--help'], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL)
    return ' '.join(r.stdout.split())


class TestKrakenRefusalsSayRemoveTheRow(unittest.TestCase):
    """A2-1597 hunks a81c3b9_096/_101/_102/_103 (kraken.py): every
    refusal that sends the user to a .tt file also says to remove the
    row from the export — the .tt alone never unblocks the run."""

    CASES = {
        'reward reversal': ('L1,R1,2025-03-01 00:00:00,staking,,currency,'
                            'DOT.S,spot,-0.5,0,1\n', False),
        'net income': ('L1,R1,2025-03-01 00:00:00,earn,reward,currency,'
                       'DOT,spot,1,0.01,1,,,SOL\n', True),
        'no defensible way': (
            'L1,R1,2025-03-01 00:00:00,spend,,currency,XETH,spot,-0.1,0,1\n'
            'L2,R1,2025-03-01 00:00:00,spend,,currency,SOL,spot,-1,0,1\n'
            'L3,R1,2025-03-01 00:00:00,receive,,currency,DOT,spot,10,0,10\n'
            'L4,R1,2025-03-01 00:00:00,receive,,currency,ADA,spot,100,0,'
            '100\n', False),
        'two spellings': (
            'L1,R1,2025-03-01 00:00:00,spend,,currency,DOT,spot,-1,0.001,1\n'
            'L2,R1,2025-03-01 00:00:00,receive,,currency,DOT28.S,spot,1,0,1\n',
            False),
    }

    def test_each_refusal_names_the_row_removal(self):
        from test_fix_l_parsers2_kraken import _parse, _KL_H, _KL_H2
        for what, (rows, wide) in self.CASES.items():
            with self.subTest(what):
                with self.assertRaises(ValueError) as cm:
                    _parse({'kr_ledgers.csv': (_KL_H2 if wide else _KL_H)
                            + rows}, 'kr_ledgers.csv')
                msg = str(cm.exception)
                self.assertIn(what, msg)
                self.assertIn('remove the row from the export', msg)


class TestKrakenCoinFeeNoteNamesTheFeeReports(unittest.TestCase):
    """A2-1597 hunk a81c3b9_095 (kraken.py): the coin-fee note says those
    fees are not in fees.rpt / fees-sum / the .sum FEES line."""

    def test_note_names_the_fee_reports(self):
        from test_crypto_parse_hardening import TestKrakenCoinFees as T
        from test_fix_l_parsers2_kraken import _parse
        _, err = _parse({'kr_trades.csv': T.TRADES,
                         'kr_ledgers.csv': T.LEDGER}, 'kr_trades.csv')
        self.assertIn('had the fee taken in the traded coin', err)
        self.assertIn('NOT in the fee reports (fees.rpt, fees-sum, the '
                      '.sum FEES line)', ' '.join(err.split()))


class TestReconcileSlipsUnreadableCellWording(unittest.TestCase):
    """A2-1597 hunk d038ef7_022 (taxjson_reconcile_slips.py): the text
    report's NOT RECONCILED line says 'an unreadable cell' (cost and
    quantity cells too), not 'unreadable proceeds'."""

    def test_unreadable_cost_says_cell(self):
        from taxjson.bin import taxjson_reconcile_slips as RS
        from test_fix_l_filing_b import _gains, _slip, _sell, _run
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell(qty=-200, proceeds=18000.0, cost=18000.0,
                                  commission=0.0)])
            s = _slip(td, 'symbol,quantity,proceeds,cost\n'
                          'AAPL,100,9000.00,nan\nAAPL,100,9000.00,9000.00\n')
            rc, out, err = _run(RS.main, ['--country', 'canada', str(s),
                                          '--gains', str(g)])
        self.assertEqual(rc, 1, out + err)
        self.assertIn('NOT RECONCILED: 1 slip row(s) had an unreadable '
                      'cell', out)


class TestSchedule3ShortNote(unittest.TestCase):
    """A2-1597 hunk 9bb6bb9_029 (taxjson_form_export.py build_schedule3):
    a short row's note says which side PROCEEDS and ACB are."""

    def test_short_note_names_the_sides(self):
        from taxjson.bin import taxjson_form_export as FE
        from test_fix_l_filing_b import _g
        rep = FE.build_schedule3([
            _g(symbol='XYZ250620P00040000.TO', qty=-4.0, proceeds=0.0,
               cost=-400.0, gain=400.0, direction='SHORT', grant=True)],
            2025)
        text = ' '.join(FE.render_schedule3(rep, 2025, 'CAD').split())
        self.assertIn('includes short position(s) — PROCEEDS is the short '
                      'sale or write, ACB the cover', text)


class TestQuestradeOwnSymbolWarningWording(unittest.TestCase):
    """A2-1597 hunk d3207b0_428 (questrade.py _resolve_symbol): the
    'booked under its own symbol' warning is moot when ticker.map already
    folds the two symbols, and says so."""

    def test_warning_says_unless_ticker_map_folds_them(self):
        _, err, _ = qt_parse(
            q(td='2025-02-03', sym='QZA', desc='QZA CORP WE ACTED AS AGENT')
            + qdiv('QZB', 'QZA CORP CASH DIV ON 10 SHS', '12.50'))
        err = ' '.join(err.split())
        self.assertIn('is booked under its own symbol', err)
        self.assertIn('if they are one security and ticker.map does not '
                      'already fold them, add a ticker.map rule', err)


class TestFeesNonOptionLabel(unittest.TestCase):
    """A2-1597 hunk 4c989a2_300 (taxjson_fees.py render_text, the
    converted split section): 'Non-option vs option fees' and the
    'non-option' column — a coin exchange's fees are not 'stocks'."""

    def test_converted_report_labels_non_option(self):
        from test_fixl_planning_reports import _tool, _row
        with tempfile.TemporaryDirectory() as td:
            w = Path(td) / 'work'
            w.mkdir()
            (w / 'c.json').write_text(json.dumps({
                'metadata': {'source_brokerage': 'questrade'},
                'transactions': [_row('2025-03-03', 'ENB.TO', 10, -500.0,
                                      commission=9.95)]}))
            rates = Path(td) / 'rates.txt'
            rates.write_text('2025-03-03 00:00:00 USD CAD 1.40 test\n')
            r = _tool('taxjson_fees', '--cache', w, '--to', 'CAD',
                      '--rates', rates)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Non-option vs option fees\n', r.stdout)
        self.assertRegex(r.stdout, r'\n  BROKERAGE +NON-OPTION +OPTIONS\n')
        self.assertNotIn(' stocks ', r.stdout)


class TestUsRadarBlockedDefinition(unittest.TestCase):
    """A2-1597 hunk 3a4e799_332 (taxjson_wash_radar.py _US_DEFINITIONS):
    BLOCKED disallows the loss on as many shares as you buy."""

    def test_blocked_definition_is_per_share(self):
        from taxjson.bin.taxjson_wash_radar import _US_DEFINITIONS
        blocked = [d for d in _US_DEFINITIONS if d.startswith('BLOCKED:')]
        self.assertEqual(len(blocked), 1)
        self.assertIn('disallows the loss on as many shares as you buy',
                      blocked[0])


class TestTaxLogicWording(unittest.TestCase):
    """A2-1597 hunks bafb310_001 and 1f7045d_573 (tax_logic.py): Canada
    no longer says PYUSD/GUSD are cash on Coinbase only (S060-24: cash on
    Kraken too), and US-FX-02 says how fx-cash counts an unrated event."""

    def test_no_canada_rule_says_coinbase_only(self):
        from taxjson.lib.tax_logic import catalog
        ca = catalog('canada')
        self.assertFalse([k for k, r in ca.items()
                          if 'Coinbase only' in r.text])
        self.assertIn('on Kraken and Coinbase alike', ca['CA-CRYPTO-02'].text)

    def test_us_fx_rule_names_unrated_fx_cash_events(self):
        from taxjson.lib.tax_logic import catalog
        self.assertIn('`taxjson fx-cash` counts a cash event with no rate',
                      catalog('usa')['US-FX-02'].text)


class TestHelpAndNoteWording(unittest.TestCase):
    """A2-1597 hunks 16def71_589 (fees-sum help), c4126f0_651
    (missing-history --include-options help), 3977c2f_1207 (to_base_curr
    help: the Bank of Canada legacy noon rate), bfa3d37_1169 (gains
    --option-buyback-wash: either premium timing), 213fa9a_288
    (generate-parser PRIVACY notice), f48933e_913 (checklist t5-t3: the
    TAXABLE line)."""

    def test_fees_sum_help(self):
        self.assertIn('(default: tax year; trade dates); the totals '
                      '`taxjson run` writes to reports/fees.rpt, by account '
                      'by default', _tool_help('taxjson_run', 'fees-sum'))

    def test_missing_history_include_options_help(self):
        self.assertIn('also include OCC option symbols and futures (a '
                      'negative position is normal sell-to-open, so skipped '
                      'by default — except a sale the broker codes CLOSING, '
                      'IB code C, which is always reported)',
                      _tool_help('taxjson_missing_history'))

    def test_to_base_curr_help(self):
        self.assertIn('(from 2017-01-03; its legacy noon rate before '
                      '2017-03-01, back to 2007-05-01); Yahoo Finance fills '
                      'earlier dates', _tool_help('to_base_curr'))

    def test_gains_buyback_wash_help(self):
        self.assertIn('Canada, either premium timing: treat the loss on '
                      'buying back a written option',
                      _tool_help('taxjson_gains'))

    def test_generate_parser_privacy_notice(self):
        self.assertIn('PRIVACY: the first --sample-lines lines of the file '
                      'are sent to the provider. Run `taxjson redact` on a '
                      'real export first', _tool_help('taxjson_generate_parser'))

    def test_checklist_t5_t3_names_the_taxable_line(self):
        from taxjson.lib.checklist import DETECTORS
        self.assertIn('the TAXABLE line of `taxjson divs-sum` / `roc-sum`',
                      DETECTORS['t5-t3'](None).detail)


class TestRunNoteWording(unittest.TestCase):
    """A2-1597 hunks f0c339f_814 (scan: unused ticker.map rules checked
    rename chains), d7a26de_462 (US estimate assumptions: the §988
    result is not included), 4dffc4b_1026 (init --force names the
    backup it actually wrote)."""

    def test_unused_rule_names_rename_chains(self):
        # `scan`'s unused-rule note is `ticker-map --suggest`'s list now.
        from test_fix_l_runcore_b import _scan_project, _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [('XEI.TO', 10)],
                                 ticker_map='TOBASE AAQ.US AAQ.TO\n')
            (root / 'work' / 'margin_questrade.json').write_text(json.dumps(
                {'transactions': [{'symbol': 'XEI.TO',
                                   'action': 'BUYSELL'}]}))
            r = _run_cli(root, 'ticker-map', '--suggest')
        # Each rule is listed as written, its reason under it.
        self.assertIn('Unused rules, delete? (1)', r.stdout)
        self.assertIn('\nTOBASE AAQ.US AAQ.TO\n', r.stdout)
        self.assertIn('(stock rows, option roots and rename chains '
                      'checked)', ' '.join(r.stdout.split()))

    def test_us_estimate_assumptions_exclude_section_988(self):
        from unittest import mock

        import taxjson.bin.taxjson_run as R
        from taxjson.bin.taxjson_run import _print_tax_estimate
        out = io.StringIO()
        # The assumptions are printed with --details (Essentials first).
        with contextlib.redirect_stdout(out), \
                mock.patch.object(R, "_CURRENT_DETAILS", True):
            _print_tax_estimate(
                {'settings': {'country': 'usa', 'year': 2025}},
                {'realized': 1000.0, 'st': 1000.0, 'lt': 0.0, 'div_ca': 0.0,
                 'div_foreign': 0.0, 'pil': 0.0}, 'USD',
                other_income=50000.0, other_losses=0.0, province=None)
        self.assertIn('interest income and the §988 result on foreign '
                      'currency (`taxjson fx-cash`) not included',
                      ' '.join(out.getvalue().split()))

    def test_init_force_names_the_numbered_backup(self):
        with tempfile.TemporaryDirectory() as td:
            base = [sys.executable, '-m', 'taxjson.bin.taxjson_run', 'init',
                    '--single', td, '--year', '2025']
            run = dict(capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
            subprocess.run(base + ['--country', 'ca'], check=True, **run)
            subprocess.run(base + ['--country', 'us', '--force'],
                           check=True, **run)
            r = subprocess.run(base + ['--country', 'ca', '--force'], **run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('wrote taxjson.toml.bak1', r.stdout)
        self.assertIn('re-add their sections (see taxjson.toml.bak1)',
                      " ".join(r.stdout.split()).lower())


if __name__ == '__main__':
    unittest.main()
