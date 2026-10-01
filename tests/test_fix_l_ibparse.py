"""Regression tests for the low-round `ibparse` audit findings (IB
statement parser, shared broker helpers, taxjson-brokerage, broker
detection, the transaction schema) and the owner's overnight-session
item. Every fixture is synthetic: invented tickers and ISINs, fake
account ids marked pii-ok."""
import inspect
import unittest

from taxjson.lib import country as C
from taxjson.lib.core import TaxTransaction
from tax_rules import rule
from tax_rules.dual import gains_both

from test_fix_ibparse import (HEAD, TRADES_H, XFER_H, CA_H, _trade, _xfer,
                              _ca, _parse_ib, _brokerage_cli)


def _book(txs):
    valid = set(inspect.signature(TaxTransaction).parameters)
    return [TaxTransaction(**{k: v for k, v in t.items() if k in valid})
            for t in txs]


def _trades(*rows):
    return HEAD + TRADES_H + ''.join(rows)


# ------------------------------------------- OWNER-OVERNIGHT / G5-1
class TestIbExchangeTradeDate(unittest.TestCase):
    """IB stamps US Eastern clock time. An overnight-session US fill
    trades on the next trading day; an ASX fill on the Sydney date."""

    def _one(self, when, sym='QZN', cur='USD', qty=10, price=10.0):
        _, txs, _ = _parse_ib(_trades(
            _trade(sym, when, qty, price, -qty * price, cur=cur)))
        self.assertEqual(len(txs), 1)
        return txs[0]

    @rule("CA-DATE-SESSION")
    def test_christmas_night_fill_trades_next_day(self):
        t = self._one('2025-12-25, 22:07:41')
        self.assertEqual(t['date'], '2025-12-26')
        self.assertEqual(t['time'], '00:00:00')
        # T+1 from Friday Dec 26: Monday Dec 29 (was Dec 26).
        self.assertEqual(t['date_settle'], '2025-12-29')
        self.assertEqual(t['broker_time'], '2025-12-25 22:07:41 ET')

    @rule("CA-DATE-SESSION")
    def test_sunday_night_fill_trades_monday(self):
        t = self._one('2026-07-05, 21:00:00')         # a Sunday
        self.assertEqual((t['date'], t['date_settle']),
                         ('2026-07-06', '2026-07-07'))

    @rule("CA-DATE-SESSION")
    def test_regular_and_after_midnight_fills_are_unchanged(self):
        for when, d in (('2026-07-08, 15:30:00', '2026-07-08'),
                        ('2026-07-08, 19:59:59', '2026-07-08'),
                        ('2026-07-09, 00:30:00', '2026-07-09')):
            with self.subTest(when=when):
                t = self._one(when)
                self.assertEqual(t['date'], d)
                self.assertNotIn('broker_time', t)

    @rule("CA-DATE-SESSION")
    def test_friday_night_fill_is_left_for_check_dates(self):
        # There is no Friday-night session: the row stays as stamped and
        # check-dates reports it.
        from datetime import date, time
        from taxjson.lib.check_dates import check_trade_time
        t = self._one('2026-07-10, 21:00:00')
        self.assertEqual((t['date'], t['time']), ('2026-07-10', '21:00:00'))
        hit = check_trade_time('us-equity', date(2026, 7, 10), time(21),
                               t['symbol'], 'USD')
        self.assertEqual(hit[1], 'friday-night-trade')

    @rule("CA-DATE-SESSION")
    def test_tsx_and_option_rows_are_not_shifted(self):
        t = self._one('2026-07-08, 21:00:00', cur='CAD')
        self.assertEqual(t['date'], '2026-07-08')

    @rule("CA-DATE-SESSION")
    def test_asx_fill_is_dated_in_sydney(self):
        # 18:22 EST on Tue Mar 3 is 10:22 AEDT on Wed Mar 4.
        t = self._one('2026-03-03, 18:22:08', sym='QZA', cur='AUD')
        self.assertEqual(t['symbol'], 'QZA.AX')
        self.assertEqual((t['date'], t['time']), ('2026-03-04', '10:22:08'))
        self.assertEqual(t['date_settle'], '2026-03-06')      # T+2
        self.assertEqual(t['broker_time'], '2026-03-03 18:22:08 ET')

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_dec30_overnight_sale_settles_in_january(self):
        # Wed Dec 30 2026, 20:30 ET: trade date Thu Dec 31, T+1 is Jan 1
        # (a holiday), then a weekend: settles Mon 2027-01-04.
        _, txs, _ = _parse_ib(_trades(
            _trade('QZN', '2026-06-01, 10:00:00', 10, 10.0, -100.0),
            _trade('QZN', '2026-12-30, 20:30:00', -10, 15.0, 150.0,
                   code='C')))
        sale = txs[1]
        self.assertEqual((sale['date'], sale['date_settle']),
                         ('2026-12-31', '2027-01-04'))
        book = _book(txs)
        r26 = gains_both(book, year=2026)
        r27 = gains_both(book, year=2027)
        # Canada (settle date): a 2027 disposition.
        self.assertAlmostEqual(r26[C.CANADA]['summary']['total_gain'], 0.0)
        self.assertAlmostEqual(r27[C.CANADA]['summary']['total_gain'], 50.0)
        # US (trade date): 2026, traded Dec 31.
        self.assertAlmostEqual(r26[C.USA]['summary']['total_gain'], 50.0)
        self.assertEqual([t['date'] for t in r26[C.USA]['transactions']],
                         ['2026-12-31'])

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_overnight_buy_sorts_before_the_days_regular_sale(self):
        # Thu 22:00 buy (trade date Fri) sold Fri 10:00: a long round
        # trip in both engines, not a short sale covered that night.
        _, txs, _ = _parse_ib(_trades(
            _trade('QZN', '2026-07-09, 22:00:00', 10, 10.0, -100.0),
            _trade('QZN', '2026-07-10, 10:00:00', -10, 12.0, 120.0,
                   code='C')))
        r = gains_both(_book(txs), year=2026)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                self.assertEqual([t.get('direction')
                                  for t in r[c]['transactions']], ['LONG'])
                self.assertAlmostEqual(r[c]['summary']['total_gain'], 20.0)

    def test_broker_time_survives_normalization(self):
        rc, out, err, _ = _brokerage_cli({'ib.csv': _trades(
            _trade('QZN', '2025-12-25, 22:07:41', 10, 10.0, -100.0))})
        self.assertEqual(rc, 0, err)
        t = out['transactions'][0]
        self.assertEqual(t['broker_time'], '2025-12-25 22:07:41 ET')
        self.assertNotIn('unknown field', err)


# ---------------------------------------------- R1-61 / R1-300 (Ca)
def _xfers(*rows):
    return HEAD + XFER_H + ''.join(rows)


class TestIbTransferCancellations(unittest.TestCase):
    """A Transfers `Ca` row consumes its original wherever it sits."""

    def _legs(self, *rows):
        p, txs, err = _parse_ib(_xfers(*rows))
        return [(t['date'], t['quantity']) for t in txs
                if t['action'] == 'TRANSFER'], err

    def test_ca_posted_days_later_consumes_its_original(self):
        # R1-61: Out 06-27, Ca 07-02, rebooked Out 07-03.
        legs, err = self._legs(
            _xfer('QZB', '2025-06-27', -300, -6000, typ='ATON', cur='CAD'),
            _xfer('QZB', '2025-07-02', 300, 6000, typ='ATON', cur='CAD',
                  code='Ca'),
            _xfer('QZB', '2025-07-03', -300, -6000, typ='ATON', cur='CAD'))
        self.assertEqual(legs, [('2025-07-03', -300)])
        self.assertNotIn('not in this statement', err)

    def test_ca_listed_before_its_original(self):
        # R1-300: the same chain with each Ca moved before its original.
        legs, err = self._legs(
            _xfer('QZB', '2026-09-01', 24, 960, typ='ATON', code='Ca'),
            _xfer('QZB', '2026-09-01', -24, -960, typ='ATON'),
            _xfer('QZB', '2026-09-01', 24, 960, typ='ATON', code='Ca'),
            _xfer('QZB', '2026-09-01', -24, -960, typ='ATON'),
            _xfer('QZB', '2026-09-01', -24, -960, typ='ATON'))
        self.assertEqual(legs, [('2026-09-01', -24)])
        self.assertNotIn('not in this statement', err)

    def test_original_in_an_earlier_statement_stays_a_netting_leg(self):
        legs, err = self._legs(
            _xfer('QZB', '2026-01-05', 50, 500, code='Ca'),
            _xfer('QZB', '2026-02-01', -50, -500))   # a later, real move
        self.assertEqual(sorted(legs), [('2026-01-05', 50),
                                        ('2026-02-01', -50)])
        self.assertIn('not in this statement', err)


class TestIbCorporateActionCancellationOrder(unittest.TestCase):
    """R1-300: a restated split keeps the REBOOKED date in every row
    order (the Ca and its original share a date; the rebook differs)."""
    ORIG = ('QZS(US0000000AA1) Split 2 for 1 (QZS, QZS INC, '
            'US0000000AA1)')

    def _rows(self):
        orig = [_ca(self.ORIG, -100, when='2026-03-02, 20:25:00'),
                _ca(self.ORIG, 200, when='2026-03-02, 20:25:00')]
        canc = [_ca(self.ORIG, 100, when='2026-03-02, 20:25:00', code='Ca'),
                _ca(self.ORIG, -200, when='2026-03-02, 20:25:00',
                    code='Ca')]
        rebook = [_ca(self.ORIG, -100, when='2026-03-03, 20:25:00'),
                  _ca(self.ORIG, 200, when='2026-03-03, 20:25:00')]
        return orig, canc, rebook

    def test_every_order_keeps_the_rebooked_split(self):
        import itertools
        orig, canc, rebook = self._rows()
        blocks = {'orig': orig, 'ca': canc, 'rebook': rebook}
        for order in itertools.permutations(blocks):
            with self.subTest(order=order):
                text = HEAD + CA_H + ''.join(
                    r for k in order for r in blocks[k])
                _, txs, _ = _parse_ib(text)
                splits = [(t['date'], round(t['quantity'], 6))
                          for t in txs if t['action'] == 'SPLIT']
                self.assertEqual(splits, [('2026-03-03', 2.0)])


# --------------------------- PIL back-fill and the open-accrual check
_ACC_H = ('Change in Dividend Accruals,Header,Asset Category,Currency,'
          'Account,Symbol,Date,Ex Date,Pay Date,Quantity,Tax,Fee,'
          'Gross Rate,Gross Amount,Net Amount,Code\n')
_DIV_H = 'Dividends,Header,Currency,Account,Date,Description,Amount\n'
_PERIOD = 'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'


def _acc(sym, date, ex, pay, qty, rate, gross, code, cur='USD'):
    return (f'Change in Dividend Accruals,Data,Stocks,{cur},U5550001,'  # pii-ok
            f'{sym},{date},{ex},{pay},{qty},0,0,{rate},{gross},{gross},'
            f'{code}\n')


def _div(sym, isin, date, amount, cur='USD', pil=False, rate=None):
    what = ('Payment in Lieu of Dividend' if pil else
            f'Cash Dividend {cur} {rate} per Share')
    return (f'Dividends,Data,{cur},U5550001,{date},'  # pii-ok
            f'"{sym}({isin}) {what}",{amount}\n')


def _pil(txs):
    return [(t['symbol'], t['quantity'], t['price']) for t in txs
            if t['action'] == 'DIVIDEND_IN_LIEU']


class TestIbPaymentInLieuBackfill(unittest.TestCase):

    def _parse(self, *rows):
        return _parse_ib(HEAD + _PERIOD + _ACC_H + ''.join(
            r for r in rows if r.startswith('Change'))
            + _DIV_H + ''.join(r for r in rows if r.startswith('Div')))

    def test_rate_of_the_paying_listing(self):
        # S057-22: same-root accruals in CAD (0.0375) and USD (0.50) on one
        # pay date; a USD PIL of 21.00 is 42 shares at 0.50.
        _, txs, _ = self._parse(
            _acc('QZE', '2025-08-20', '2025-08-20', '2025-09-15', 1000,
                 0.0375, 37.5, 'Po', cur='CAD'),
            _acc('QZE', '2025-08-20', '2025-08-20', '2025-09-15', 42, 0.5,
                 21, 'Po'),
            _div('QZE', 'US0000000QE1', '2025-09-15', 21, pil=True))
        self.assertEqual(_pil(txs), [('QZE.US', 42.0, 0.5)])

    def test_cad_pil_takes_the_cad_rate(self):
        # S058-05 (2): USD 0.25 and CAD 0.05 accruals, CAD PIL of 30.
        _, txs, _ = self._parse(
            _acc('QZQ', '2025-06-01', '2025-06-01', '2025-06-15', 100, 0.25,
                 25, 'Po'),
            _acc('QZQ', '2025-06-01', '2025-06-01', '2025-06-15', 600, 0.05,
                 30, 'Po', cur='CAD'),
            _div('QZQ', 'CA0000000QQ1', '2025-06-15', 30, cur='CAD',
                 pil=True))
        self.assertEqual(_pil(txs), [('QZQ.TO', 600.0, 0.05)])

    def test_pil_posted_after_the_pay_date(self):
        # S058-12: accrued pay 03-01, PIL cash posted 03-04.
        _, txs, _ = self._parse(
            _acc('QZX', '2025-02-20', '2025-02-20', '2025-03-01', 100, 0.5,
                 50, 'Po'),
            _acc('QZX', '2025-03-04', '2025-02-20', '2025-03-01', 100, 0.5,
                 -50, 'Re'),
            _div('QZX', 'US0000000QX1', '2025-03-04', 50, pil=True))
        self.assertEqual(_pil(txs), [('QZX.US', 100.0, 0.5)])

    def test_accrual_in_another_currency_uses_its_share_count(self):
        # S060-13 / G3-0: USD accrual (Po 0.0125 on 9000; Re at the CAD
        # rate), PIL paid in CAD. Either row order: 9000 shares.
        po = _acc('QZV', '2025-09-30', '2025-09-30', '2025-10-14', 9000,
                  0.0125, 112.5, 'Po')
        re_ = _acc('QZV', '2025-10-14', '2025-09-30', '2025-10-14', 9000,
                   0.01740125, -112.5, 'Re')
        pil = _div('QZV', 'CA0000000QV1', '2025-10-14', 156.61, cur='CAD',
                   pil=True)
        for rows in ((po, re_, pil), (re_, po, pil)):
            with self.subTest(first=rows[0][-3:-1]):
                _, txs, _ = self._parse(*rows)
                (sym, q, price), = _pil(txs)
                self.assertEqual(q, 9000.0)
                self.assertAlmostEqual(price, 156.61 / 9000, places=8)


class TestIbOpenAccrualWarning(unittest.TestCase):

    def test_other_weeks_dividend_does_not_hide_an_open_accrual(self):
        # S059-08: weekly payer; the 12-24 week is posted (Po, Re and
        # the Dividends row), the 12-31 week only accrued.
        text = (HEAD + _PERIOD + _ACC_H
                + _acc('QZW', '2025-12-17', '2025-12-17', '2025-12-24', 1000,
                       0.31, 310, 'Po')
                + _acc('QZW', '2025-12-24', '2025-12-17', '2025-12-24', 1000,
                       0.31, -310, 'Re')
                + _acc('QZW', '2025-12-29', '2025-12-29', '2025-12-31', 1000,
                       0.31, 310, 'Po')
                + _DIV_H + _div('QZW', 'US0000000QW1', '2025-12-24', 310,
                                rate=0.31))
        _, _, err = _parse_ib(text)
        self.assertIn('accrued but not yet booked', err)
        self.assertIn('QZW pay:2025-12-31', err)

    def test_posting_in_the_next_statement_counts(self):
        # R1-327: the 2025 statement accrues (Po), the 2026 statement
        # posts the cash dated 2025-12-31 and reverses the accrual.
        from test_fix_ibparse import _parse_account
        y25 = (HEAD + _PERIOD + _ACC_H
               + _acc('QZT', '2025-12-15', '2025-12-15', '2025-12-31', 1000,
                      0.35, 350, 'Po', cur='CAD'))
        y26 = (HEAD + 'Statement,Data,Period,"January 1, 2026 - '
               'January 31, 2026"\n' + _ACC_H
               + _acc('QZT', '2025-12-31', '2025-12-15', '2025-12-31', 1000,
                      0.35, -350, 'Re', cur='CAD')
               + _DIV_H + _div('QZT', 'CA0000000QT1', '2025-12-31', 350,
                               cur='CAD', rate=0.35))
        _, err = _parse_account({'ib_2025.csv': y25, 'ib_2026.csv': y26})
        self.assertNotIn('accrued but not yet booked', err)
        # A lone parse of the 2025 statement still warns.
        _, _, lone = _parse_ib(y25)
        self.assertIn('accrued but not yet booked', lone)


# --------------------------------------------- corporate actions (IB)
_CIL = ('QZX(US0000000QX1) Cash in Lieu of Fractional Shares (QZX, QZX '
        'CORP, US0000000QX1)')


def _split_desc(new, old):
    return (f'QZX(US0000000QX1) Split {new} for {old} (QZX, QZX CORP, '
            f'US0000000QX1)')


def _ratios(txs):
    return [(t['date'], round(t['quantity'], 7)) for t in txs
            if t['action'] == 'SPLIT']


class TestIbCashInLieu(unittest.TestCase):

    def test_zero_proceeds_books_no_cash(self):
        # S058-17: Proceeds 0 used to be replaced by Value (market value).
        _, txs, err = _parse_ib(HEAD + CA_H + _ca(
            _CIL, -0.5, value=11.8, proceeds=0,
            when='2026-03-02, 20:25:00'))
        cil = [t for t in txs if t['action'] == 'BUYSELL']
        self.assertEqual(cil[0]['net_amount'], 0.0)
        self.assertIn('Proceeds 0', err)

    def test_unrelated_fraction_is_not_folded_into_a_later_split(self):
        # S058-18 case A: a CIL in March, an unrelated 2-for-1 in
        # September: the September ratio comes from its own legs.
        _, txs, err = _parse_ib(HEAD + CA_H
                                + _ca(_CIL, -0.5, proceeds=5,
                                      when='2026-03-02, 20:25:00')
                                + _ca(_split_desc(2, 1), -999.5,
                                      when='2026-09-15, 20:25:00')
                                + _ca(_split_desc(2, 1), 1999,
                                      when='2026-09-15, 20:25:00'))
        self.assertEqual(_ratios(txs), [('2026-09-15', 2.0)])
        self.assertIn('matched no split', err)

    def test_fraction_joins_its_own_split_in_either_order(self):
        # S058-18 case B / S060-14: a January split, then the October
        # 1-for-3 whose CIL (dated the day before its legs) may come
        # first or last.
        jan = (_ca(_split_desc(2, 1), -1000, when='2026-01-15, 20:25:00')
               + _ca(_split_desc(2, 1), 2000, when='2026-01-15, 20:25:00'))
        cil = _ca(_CIL, -0.6667, proceeds=6, when='2026-10-01, 20:25:00')
        octl = (_ca(_split_desc(1, 3), -2000, when='2026-10-02, 20:25:00')
                + _ca(_split_desc(1, 3), 666, when='2026-10-02, 20:25:00'))
        got = []
        for body in (jan + cil + octl, jan + octl + cil):
            _, txs, _ = _parse_ib(HEAD + CA_H + body)
            got.append(_ratios(txs))
        self.assertEqual(got[0], got[1])
        self.assertEqual(got[0][0], ('2026-01-15', 2.0))
        # 2000 x ratio - 0.6667 sold = 666 whole shares.
        self.assertAlmostEqual(2000 * got[0][1][1] - 0.6667, 666.0, places=3)


class TestIbSplitOnAShortPosition(unittest.TestCase):

    def test_short_legs_give_the_text_ratio(self):
        # S058-20: short 1000, 'Split 102 for 100': legs +1000 / -1020.
        _, txs, _ = _parse_ib(HEAD + CA_H
                              + _ca(_split_desc(102, 100), 1000,
                                    when='2026-05-01, 20:25:00')
                              + _ca(_split_desc(102, 100), -1020,
                                    when='2026-05-01, 20:25:00'))
        self.assertEqual(_ratios(txs), [('2026-05-01', 1.02)])

    def test_long_legs_are_unchanged(self):
        _, txs, _ = _parse_ib(HEAD + CA_H
                              + _ca(_split_desc(102, 100), -1000,
                                    when='2026-05-01, 20:25:00')
                              + _ca(_split_desc(102, 100), 1020,
                                    when='2026-05-01, 20:25:00'))
        self.assertEqual(_ratios(txs), [('2026-05-01', 1.02)])


def _tender(root, isin, qty, kind, when, proceeds=0):
    if kind == 'out':
        d = (f'{root}({isin}) Tendered to 99999997 1 FOR 1 ({root}.TEN, '
             f'{root} CORP - TENDER, {isin})')
    elif kind == 'in':
        d = (f'{root}.TEN({isin}) Tendered to 99999997 1 FOR 1 '
             f'({root}.TEN, {root} CORP - TENDER, {isin})')
    elif kind == 'back':
        d = (f'{root}.TEN(99999997) Merged(Voluntary Offer Allocation) '
             f'WITH {isin} 1 for 1 ({root}, {root} CORP, {isin})')
    else:
        d = (f'{root}.TEN(99999997) Merged(Voluntary Offer Allocation) '
             f'WITH {isin} 1 for 1 ({root}.TEN, {root} CORP - TENDER, '
             f'{isin})')
    return _ca(d, qty, proceeds=proceeds, cur='CAD', when=when)


class TestIbTender(unittest.TestCase):

    def test_cash_tender_note_gives_followable_advice(self):
        # S059-05: no corp-actions election exists for a tender.
        _, txs, err = _parse_ib(HEAD + CA_H + _tender(
            'QZT', 'CA9990000031', -500, 'out', '2026-02-02, 20:25:00',
            proceeds=5250))
        self.assertIn('booked as a sale', err)
        self.assertNotIn('taxjson-corp-actions election', err)
        self.assertIn('.tt file', err)

    def test_tender_resolved_in_the_next_statement_is_quiet(self):
        # S059-06: parked in 2025, journaled back in 2026.
        from test_fix_ibparse import _parse_account
        y25 = HEAD + CA_H + (
            _tender('QZT', 'CA9990000031', -100, 'out',
                    '2025-12-22, 20:25:00')
            + _tender('QZT', 'CA9990000031', 100, 'in',
                      '2025-12-22, 20:25:00'))
        y26 = HEAD + CA_H + (
            _tender('QZT', 'CA9990000031', 100, 'back',
                    '2026-01-09, 20:25:00')
            + _tender('QZT', 'CA9990000031', -100, 'park',
                      '2026-01-09, 20:25:00'))
        txs, err = _parse_account({'ib_2025.csv': y25, 'ib_2026.csv': y26})
        self.assertEqual(txs, [])
        self.assertNotIn('still sit on the tender placeholder', err)
        self.assertIn("account's other statement completes", err)
        # One statement alone still warns.
        _, _, lone = _parse_ib(y25)
        self.assertIn('still sit on the tender placeholder', lone)


class TestIbCancelledUnhandledRowCount(unittest.TestCase):

    def test_skip_line_agrees_with_the_unbooked_tally(self):
        # R1-329: a cancelled untranslated row left the skip count high.
        desc = 'QZM(US0000000QM1) Name Change WITH US0000000QN1 1 for 1'
        _, _, err = _parse_ib(HEAD + CA_H
                              + _ca(desc, -10, when='2026-04-01, 20:25:00')
                              + _ca(desc, 10, when='2026-04-01, 20:25:00',
                                    code='Ca')
                              + _ca(desc, -10, when='2026-04-02, 20:25:00'))
        self.assertIn('1 unhandled Corporate Action row(s)', err)
        self.assertIn('Corporate Actions row not translated (see UNBOOKED '
                      'warning): 1', err)


class TestIbCurrencyTaggedSymbol(unittest.TestCase):

    def test_currency_tag_is_said_in_every_section(self):
        # R1-59 / S059-00: RGLD.CAD-style lines are their own pool.
        cil = ('QZG.CAD(US0000000QG1) Cash in Lieu of Fractional Shares '
               '(QZG.CAD, QZG CORP, US0000000QG1)')
        for body in (TRADES_H + _trade('QZG.CAD', '2026-03-02, 10:00:00',
                                       -1, 25, 25, cur='CAD', code='C'),
                     XFER_H + _xfer('QZG.CAD', '2026-03-02', 50, 1250,
                                    cur='CAD'),
                     CA_H + _ca(cil, -0.0026, proceeds=0.64, cur='CAD')):
            with self.subTest(section=body.split(',')[0]):
                _, txs, err = _parse_ib(HEAD + body)
                self.assertIn("IB symbol 'QZG.CAD' ends in the "
                              "currency/venue tag .CAD", err)
        _, _, err = _parse_ib(HEAD + TRADES_H + _trade(
            'QZB B', '2026-03-02, 10:00:00', 1, 25, -25, cur='CAD'))
        self.assertNotIn('currency/venue tag', err)


if __name__ == '__main__':
    unittest.main()
