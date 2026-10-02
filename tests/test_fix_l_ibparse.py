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
    @rule("US-DATE-SESSION")
    def test_christmas_night_fill_trades_next_day(self):
        t = self._one('2025-12-25, 22:07:41')
        self.assertEqual(t['date'], '2025-12-26')
        self.assertEqual(t['time'], '00:00:00')
        # T+1 from Friday Dec 26: Monday Dec 29 (was Dec 26).
        self.assertEqual(t['date_settle'], '2025-12-29')
        self.assertEqual(t['broker_time'], '2025-12-25 22:07:41 ET')

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_sunday_night_fill_trades_monday(self):
        t = self._one('2026-07-05, 21:00:00')         # a Sunday
        self.assertEqual((t['date'], t['date_settle']),
                         ('2026-07-06', '2026-07-07'))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_regular_and_after_midnight_fills_are_unchanged(self):
        for when, d in (('2026-07-08, 15:30:00', '2026-07-08'),
                        ('2026-07-08, 19:59:59', '2026-07-08'),
                        ('2026-07-09, 00:30:00', '2026-07-09')):
            with self.subTest(when=when):
                t = self._one(when)
                self.assertEqual(t['date'], d)
                self.assertNotIn('broker_time', t)

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
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
    @rule("US-DATE-SESSION")
    def test_tsx_and_option_rows_are_not_shifted(self):
        t = self._one('2026-07-08, 21:00:00', cur='CAD')
        self.assertEqual(t['date'], '2026-07-08')

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
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


# --------------------------------------------- statement hardening (IB)
CASH_H = 'Cash Report,Header,Currency Summary,Currency,Total,\n'
FEES_H = 'Fees,Header,Subtitle,Currency,Date,Description,Amount\n'


class TestIbSubtotalByCurrencyCell(unittest.TestCase):
    """S058-03: 'Total' in a Description is not a subtotal."""

    def test_total_return_fund_dividend_is_booked(self):
        _, txs, _ = _parse_ib(HEAD + _DIV_H + _div(
            'QZF', 'US0000000QF1', '2025-06-02', 14, rate=0.14).replace(
            'per Share', 'per Share (Total Return Fund)'))
        self.assertEqual([t['net_amount'] for t in txs
                          if t['action'] == 'DIVIDEND'], [14.0])

    def test_totalview_market_data_fee_is_booked(self):
        _, txs, _ = _parse_ib(HEAD + FEES_H
                              + 'Fees,Data,Other Fees,USD,2025-03-03,'
                                'Nasdaq TotalView for Mar 2025,-10\n'
                              + 'Fees,Data,Total,,,,-10\n')
        self.assertEqual([t['net_amount'] for t in txs], [10.0])


class TestIbUnreadableStatementCells(unittest.TestCase):
    """S059-14."""

    def test_unreadable_cash_report_total_is_named(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        text = (HEAD + TRADES_H
                + _trade('QZM', '2025-03-03, 10:00:00', 100, 200, -20000)
                + CASH_H
                + 'Cash Report,Data,Trades (Purchase),USD,-20 000.00,\n')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(text)
        self.assertIn("Trades (Purchase) USD '-20 000.00'", str(cm.exception))

    def test_unread_line_that_is_not_reconciled_is_harmless(self):
        text = (HEAD + TRADES_H
                + _trade('QZM', '2025-03-03, 10:00:00', 100, 200, -20000)
                + CASH_H
                + 'Cash Report,Data,Trades (Purchase),USD,-20000,\n'
                + 'Cash Report,Data,Starting Cash,USD,1 000,\n')
        _, txs, _ = _parse_ib(text)
        self.assertEqual(len(txs), 1)

    def test_unreadable_option_multiplier_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from test_fix_ibparse import FII_H
        text = (HEAD + TRADES_H
                + _trade('QZM 16JAN26 100 C', '2025-03-03, 10:00:00', 1, 2,
                         -200, cat='Equity and Index Options')
                + FII_H + 'Financial Instrument Information,Data,Equity '
                          'and Index Options,QZM 16JAN26 100 C,QZM 16JAN26 '
                          '100 C,990000077,,QZM,CBOE,1OO,2026-01-16,,C,100,\n')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(text)
        self.assertIn("Multiplier '1OO'", str(cm.exception))


class TestIbUnknownRowType(unittest.TestCase):
    """S060-07: rows typed 'data' (lower case) were skipped unseen."""

    def test_no_data_rows_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        text = _trades(_trade('QZM', '2025-03-03, 10:00:00', 1, 10, -10)
                       ).replace(',Data,', ',data,')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(text)
        self.assertIn("no 'Data' rows", str(cm.exception))

    def test_some_unknown_rows_are_counted_and_said(self):
        text = _trades(_trade('QZM', '2025-03-03, 10:00:00', 1, 10, -10),
                       _trade('QZN', '2025-03-03, 10:00:00', 1, 10, -10
                              ).replace(',Data,', ',data,'))
        p, txs, err = _parse_ib(text)
        self.assertEqual(len(txs), 1)
        self.assertIn('rows of a type IB does not write', err)
        self.assertEqual(p._rows_seen,
                         p._rows_consumed + sum(p._skip_counts.values()))


class TestIbFuturesNotional(unittest.TestCase):
    """G7-3: futures rows have no Cash Report backing; cent tolerance."""

    def _fut(self, proceeds):
        from test_fix_ibparse import FII_H
        return (HEAD + TRADES_H
                + _trade('CLZ5', '2025-10-01, 10:00:00', -1, 57.40237,
                         proceeds, cat='Futures', code='C')
                + FII_H + 'Financial Instrument Information,Data,Futures,'
                          'CLZ5,CL DEC25,990000088,,CL,NYMEX,1000,2025-11-19,'
                          '202512,,,\n')

    def test_exact_futures_row_is_booked(self):
        _, txs, _ = _parse_ib(self._fut(57402.37))
        self.assertEqual(len(txs), 1)

    def test_a_dollar_off_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaises(BrokerageParseError):
            _parse_ib(self._fut(57403.37))


class TestIbLowerCaseCurrency(unittest.TestCase):
    """S055-17 (IB): 'usd' is USD, not a suffix of its own."""

    def test_lower_case_currency_is_normalized(self):
        _, txs, err = _parse_ib(_trades(_trade(
            'QZM', '2025-03-03, 10:00:00', 1, 10, -10, cur='usd')))
        self.assertEqual((txs[0]['symbol'], txs[0]['currency']),
                         ('QZM.US', 'USD'))
        self.assertNotIn('no exchange-suffix mapping', err)


class TestIbSecurityNameForTheLint(unittest.TestCase):
    """S057-24: the lint saw only 'META' on IB rows."""

    def test_cdr_name_reaches_the_lint(self):
        from test_fix_ibparse import FII_H
        from taxjson.bin.taxjson_lint_crosslistings import analyze
        text = (HEAD + TRADES_H
                + _trade('QZMT', '2025-03-03, 10:00:00', 10, 30, -300,
                         cur='CAD')
                + _trade('QZMT', '2025-03-03, 10:00:00', 1, 600, -600)
                + FII_H
                + 'Financial Instrument Information,Data,Stocks,QZMT,'
                  'QZMT PLATFORMS INC-CDR,990000091,CA0000000MT1,,AEQLIT,1,'
                  ',,COMMON,,\n')
        _, txs, _ = _parse_ib(text)
        self.assertEqual(txs[0]['description'], 'QZMT')   # overrides key
        self.assertEqual(txs[0]['security_name'], 'QZMT PLATFORMS INC-CDR')
        (f,) = analyze(txs, [], set(), set())
        self.assertEqual(f['severity'], 'OK')


class TestIbCommissionRefundFolds(unittest.TestCase):
    """R1-58: a refunded commission lowers the trade's cost/outlay."""
    ADJ_H = ('Commission Adjustments,Header,Currency,Date,Description,'
             'Amount,Code\n')

    @rule("CA-ACB-COMMREFUND")
    @rule("US-BASIS-COMMREFUND")
    def test_refunds_change_the_gain(self):
        text = (HEAD + TRADES_H
                + _trade('QZK', '2025-02-03, 10:00:00', 100, 10, -1000,
                         comm=-5)
                + _trade('QZK', '2025-05-01, 10:00:00', -100, 11, 1100,
                         comm=-5, code='C')
                + self.ADJ_H
                + 'Commission Adjustments,Data,USD,2025-02-10,'
                  '"Refund (QZK, 100, 2025-02-03)",4,\n'
                + 'Commission Adjustments,Data,USD,2025-05-08,'
                  '"Refund (QZK, -100, 2025-05-01)",2,\n')
        _, txs, err = _parse_ib(text)
        self.assertEqual([t['action'] for t in txs], ['BUYSELL', 'BUYSELL'])
        self.assertEqual([t['net_amount'] for t in txs], [1001.0, 1097.0])
        self.assertIn('2 commission adjustment(s) folded', err)
        r = gains_both(_book(txs), year=2025)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                # 1097 - 1001 (was 1095 - 1005 = 90).
                self.assertAlmostEqual(r[c]['summary']['total_gain'], 96.0)

    def test_unmatched_refund_stays_a_fee_row(self):
        _, txs, err = _parse_ib(HEAD + self.ADJ_H
                                + 'Commission Adjustments,Data,USD,'
                                  '2026-01-10,"Refund (QZK, 100, 2025-12-29)",'
                                  '1.25,\n')
        self.assertEqual([(t['action'], t['net_amount']) for t in txs],
                         [('FEE', -1.25)])
        self.assertIn('kept as a FEE row', err)


# ------------------------------------------- pinned behaviours (tests)
INT_H = 'Interest,Header,Currency,Date,Description,Amount\n'


class TestIbGuardsBothWays(unittest.TestCase):
    """S058-00 / S059-20: each refusal tested for every operand."""

    def _refused(self, text):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaises(BrokerageParseError):
            _parse_ib(text)

    def test_blank_currency_alone_is_refused(self):
        self._refused(_trades(
            'Trades,Data,Order,Stocks,,U5550001,QZX,'  # pii-ok
            '"2025-03-03, 10:00:00",100,10,0,-1000,-1,0,0,0,O\n'))

    def test_blank_symbol_alone_is_refused(self):
        self._refused(_trades(_trade('', '2025-03-03, 10:00:00', 100, 10,
                                     -1000)))

    def test_forex_symbol_without_quote_or_dot_is_refused(self):
        for sym in ('USD.', 'USDCAD'):
            with self.subTest(sym=sym):
                self._refused(_trades(_trade(
                    sym, '2025-03-03, 10:00:00', 1000, 1.38, -1380,
                    cat='Forex', cur='CAD')))

    def test_implausible_commission_either_sign_is_refused(self):
        for comm in (-600, 600):
            with self.subTest(comm=comm):
                self._refused(_trades(_trade(
                    'QZX', '2025-03-03, 10:00:00', 100, 10, -1000,
                    comm=comm)))


class TestIbSignsAsExported(unittest.TestCase):
    """S058-14 / S058-15 / S059-01: fixtures with IB's real signs."""

    def test_debit_interest_is_negative_and_nets(self):
        _, txs, _ = _parse_ib(HEAD + INT_H
                              + 'Interest,Data,CAD,2025-02-04,CAD Debit '
                                'Interest for Jan-2025,-24.64\n'
                              + 'Interest,Data,CAD,2025-02-04,CAD Credit '
                                'Interest for Jan-2025,1.10\n')
        self.assertEqual([t['net_amount'] for t in txs], [-24.64, 1.10])
        self.assertAlmostEqual(sum(t['net_amount'] for t in txs), -23.54)

    def test_fee_reversal_nets_its_charge(self):
        _, txs, _ = _parse_ib(HEAD + FEES_H
                              + 'Fees,Data,Other Fees,CAD,2025-03-03,'
                                'QZX Market Data for Mar 2025,-1.40\n'
                              + 'Fees,Data,Other Fees,CAD,2025-03-10,'
                                'Cancel[QZX Market Data for Mar 2025],1.40\n')
        self.assertEqual([t['net_amount'] for t in txs], [1.40, -1.40])
        self.assertAlmostEqual(sum(t['net_amount'] for t in txs), 0.0)

    def test_transfer_out_row_keeps_price_and_positive_net(self):
        _, txs, _ = _parse_ib(HEAD + XFER_H + _xfer(
            'QZX', '2025-08-13', -700, '"-27,650.00"', typ='Internal',
            cur='CAD'))
        t, = txs
        self.assertEqual(t['quantity'], -700)
        self.assertAlmostEqual(t['price'], 39.5)
        self.assertAlmostEqual(t['net_amount'], 27650.0)


# --------------------------------------------- shared broker helpers
class TestBackComputedFee(unittest.TestCase):
    """R1-22 / R1-88: signed residual; flat commissions survive."""

    def setUp(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        self.b = BaseBrokerage()

    def test_flat_commission_on_a_cheap_option_sale_is_kept(self):
        # 2 contracts @0.04 (gross 8.00) netting 6.01: fee 1.99.
        self.assertAlmostEqual(
            self.b.back_compute_fee(-2, 0.04, 6.01, True), 1.99)

    def test_rbc_option_buy_commission_is_kept(self):
        # RBC passes the signed Value (-43.95) for a buy of 4 @0.08.
        self.assertAlmostEqual(
            self.b.back_compute_fee(4, 0.08, -43.95, True), 11.95)

    def test_rounding_noise_is_not_a_charge(self):
        # A buy that cost 0.05 LESS than qty x the rounded price.
        self.assertEqual(
            self.b.back_compute_fee(500, 10.41, 5204.95, False), 0.0)

    def test_units_artifact_is_still_zeroed(self):
        # Net quoted per share (6.00) against a x100 gross (600): no fee.
        self.assertEqual(
            self.b.back_compute_fee(-1, 6.0, 6.0, True), 0.0)


class TestReturnOfCapitalWording(unittest.TestCase):
    """R1-76."""

    def test_only_a_real_return_of_capital_matches(self):
        from taxjson.lib.brokerages.base import is_roc_description
        for desc, want in (
                ('RETURN OF CAPITAL ON 100 SHS', True),
                ('QZRX(US0000000017) Cash Dividend USD 0.12 per Share '
                 '(Return of Capital)', True),
                ('RETURN OF CAPITAL ADJUSTMENT TO BOOK COST $5.00', True),
                ('CASH DIV ON 100 SHS NOT A RETURN OF CAPITAL', False),
                ('QZE ENHANCED RET OF CAPITAL ETF CASH DIV ON 100 SHS',
                 False),
                ('DIST ON 100 SHS RETURN OF CAPITAL GAINS', False),
                ('CASH DIV ON 100 SHS', False)):
            with self.subTest(desc=desc):
                self.assertEqual(is_roc_description(desc), want)


class TestStrictNumbers(unittest.TestCase):
    """S055-09."""

    def test_non_ascii_digits_and_overflow_are_refused(self):
        from taxjson.lib.brokerages.base import (BrokerageParseError,
                                                 parse_strict_number)
        for raw in ('1e400', '9' * 400, '\u0661\u0662\u0663',
                    '\uff11\uff12\uff13'):
            with self.subTest(raw=raw[:8]):
                with self.assertRaises(BrokerageParseError):
                    parse_strict_number(raw)
        self.assertEqual(parse_strict_number('1,234.5'), 1234.5)

    def test_rbc_number_too(self):
        from pathlib import Path
        from taxjson.lib.brokerages.rbc_direct import rbc_number
        for raw in ('9' * 400, '\u0661\u0662\u0663'):
            with self.subTest(raw=raw[:8]):
                with self.assertRaises(ValueError):
                    rbc_number(raw, path=Path('x.csv'), line=2,
                               column='Amount')


class TestCurrencyCase(unittest.TestCase):
    """S055-17: a lower-case currency is the same currency."""

    def test_apply_currency_suffix(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        self.assertEqual(BaseBrokerage().apply_currency_suffix('XYZ', 'usd'),
                         'XYZ.US')

    def test_questrade_row(self):
        from test_questrade_parse_hardening import H, row, _parse
        _, txs, _ = _parse(H + row(cur='usd'))
        t = [t for t in txs if t['action'] == 'BUYSELL'][0]
        self.assertEqual((t['symbol'], t['currency']), ('QZA.US', 'USD'))


class TestDescriptionNumbers(unittest.TestCase):
    """S055-20 / S055-23: valid thousands groups, never a decimal comma."""

    def test_dividend_rate_and_count(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        self.assertEqual(_parse_div_qty_rate(
            'SPECIAL CASH DIV $1,250.00 PER SHARE', 2500.0), (2.0, 1250.0))
        self.assertEqual(_parse_div_qty_rate(
            'QZX(US0000000QX1) Cash Dividend USD 1,250.00 (Ordinary '
            'Dividend)', 2500.0), (2.0, 1250.0))
        self.assertEqual(_parse_div_qty_rate('CASH DIV ON 12,5 SHS', 10.0),
                         (0.0, 0.0))
        self.assertEqual(_parse_div_qty_rate('CASH DIV ON 1,000 SHS', 50.0),
                         (1000.0, 0.05))

    def test_sibling_sites_refuse_a_decimal_comma(self):
        # RBC/Questrade validate the captured text (desc_number); the
        # corp-action spin-off parent count too (_num_text).
        from taxjson.lib.brokerages.base import (BrokerageParseError,
                                                 desc_number)
        from taxjson.lib import corp_actions as CA
        with self.assertRaises(BrokerageParseError):
            desc_number('1234,56')
        m = CA._RBC_SPINOFF_RE.search(
            'SPIN OFF ON 12,5 SHS FROM SEC# 123 QZS CORP')
        with self.assertRaises(BrokerageParseError):
            CA._num_text(m.group(1))


class TestSettleToTradeAtTheCutover(unittest.TestCase):
    """S056-01: the T+2 settles ON the cutover day round-trip."""

    def test_cutover_days(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.lib.dates import settlement_date
        b = BaseBrokerage()
        for settle, cur in (('2024-05-27', 'CAD'), ('2024-05-28', 'USD')):
            with self.subTest(cur=cur):
                t = b.trade_date_from_settlement(settle, cur, False,
                                                 '%Y-%m-%d')
                self.assertEqual(t, '2024-05-23')
                self.assertEqual(settlement_date(t, cur), settle)


# ------------------------------------- detection and taxjson-brokerage
from pathlib import Path as _P
_EX = _P(__file__).resolve().parent.parent / 'examples'


def _detect_text(text, encoding='utf-8', name='x.csv'):
    import tempfile
    from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
    with tempfile.TemporaryDirectory() as td:
        p = _P(td) / name
        p.write_bytes(text.encode(encoding) if isinstance(text, str)
                      else text)
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return detect_brokerage(p), err.getvalue()


class TestBrokerDetection(unittest.TestCase):
    """R1-57 / S029-14 / R1-90 / S059-17."""

    def setUp(self):
        self.ib = (_EX / 'ib_demo.csv').read_text(encoding='utf-8')
        self.rbc = (_EX / 'rbc_direct_demo.csv').read_text(encoding='utf-8')

    def test_ib_variants_the_parser_reads(self):
        lines = self.ib.splitlines(keepends=True)
        trades_first = ''.join(l for l in lines
                               if not l.startswith(('Statement,',
                                                    'Account Information,')))
        no_broker = ''.join(l for l in lines if 'BrokerName' not in l)
        title_first = ''.join(
            [lines[0], 'Statement,Data,Title,Activity Statement\n']
            + lines[1:])
        for label, text in (('trades-first', trades_first),
                            ('no BrokerName', no_broker),
                            ('BrokerName on row 3', title_first)):
            with self.subTest(label):
                self.assertEqual(_detect_text(text)[0], 'ib')

    def test_fetch_accepts_what_detection_routes(self):
        from taxjson.bin.taxjson_fetch import looks_like_ib_statement
        self.assertTrue(looks_like_ib_statement(self.ib))
        self.assertFalse(looks_like_ib_statement(
            'Statement,Header,Field Name,Field Value\n'
            'Statement,Data,Notes,LIBOR Rate Source\n'))

    def test_rbc_header_after_a_partial_preamble_or_blank_line(self):
        no_brand = self.rbc.split('\n', 1)[1]
        spacer = '\n' + self.rbc[self.rbc.index('Date,Activity'):]
        for label, text in (('no brand line', no_brand),
                            ('blank first line', spacer)):
            with self.subTest(label):
                self.assertEqual(_detect_text(text)[0], 'rbc_direct')

    def test_utf16_rbc_export(self):
        self.assertEqual(_detect_text(self.rbc, 'utf-16')[0], 'rbc_direct')

    def test_cp1252_file_gets_one_line(self):
        got, err = _detect_text(self.ib.replace('Synthetic Demo',
                                                'SOCI\u00c9T\u00c9'),
                                'cp1252')
        self.assertIsNone(got)
        self.assertIn('not UTF-8 or UTF-16 text', err)
        self.assertNotIn('Traceback', err)


class TestBrokerageCliRefusals(unittest.TestCase):
    """R1-262 / S059-17 / S026-21 / S027-02 / S059-10."""

    def test_rbc_refusal_is_one_line(self):
        rbc = (_EX / 'rbc_direct_demo.csv').read_text(encoding='utf-8')
        bad = rbc.replace('-13009.95', '-13O09.95')
        rc, out, err, _ = _brokerage_cli({'rbc.csv': bad},
                                         brokerage='rbc_direct')
        self.assertEqual(rc, 1)
        self.assertNotIn('Traceback', err)
        self.assertIn('taxjson-brokerage: error: rbc.csv', err)

    def test_unknown_brokerage_is_a_usage_error(self):
        rc, _, err, _ = _brokerage_cli({'x.csv': 'a,b\n'}, brokerage='nosuch')
        self.assertEqual(rc, 2)
        self.assertNotIn('Traceback', err)
        self.assertIn('Unknown brokerage: nosuch', err)

    def test_cp1252_questrade_file_is_one_line(self):
        import os, subprocess, sys, tempfile
        q = (_EX / 'questrade_demo.csv').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as td:
            p = _P(td) / 'qt.csv'
            p.write_bytes(q.replace('APPLE INC', 'SOCI\u00c9T\u00c9', 1)
                          .encode('cp1252'))
            env = dict(os.environ, PYTHONPATH=str(_EX.parent / 'src'))
            r = subprocess.run([sys.executable, '-m',
                                'taxjson.bin.taxjson_brokerage',
                                '--brokerage', 'questrade', str(p)],
                               capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('not UTF-8 or UTF-16 text', r.stderr)

    def test_alias_is_recorded_under_the_canonical_id(self):
        ib = (_EX / 'ib_demo.csv').read_text(encoding='utf-8')
        rc, out, err, _ = _brokerage_cli({'ib.csv': ib},
                                         brokerage='interactive_brokers')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out['metadata']['source_brokerage'], 'ib')

    def test_account_id_in_the_file_name_is_masked(self):
        ib = (_EX / 'ib_demo.csv').read_text(encoding='utf-8')
        rc, out, err, _ = _brokerage_cli(
            {'U5550001_20250101_20251231.csv': ib})  # pii-ok
        self.assertEqual(rc, 0, err)
        self.assertNotIn('U5550001', err)  # pii-ok
        self.assertIn('U5***_20250101_20251231.csv', err)


class TestTransfersViewInBookValue(unittest.TestCase):
    """S027-06: an in-book RBC transfer shows its BOOK VALUE."""

    def test_book_row_value_from_its_description(self):
        import argparse, contextlib, io, json, tempfile
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = _P(td)
            (root / 'taxjson.toml').write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n'
                '[accounts.tfsa]\ntype = "sheltered"\ntransfers = true\n')
            (root / 'work').mkdir()
            (root / 'work' / 'tfsa_base.json').write_text(json.dumps(
                {'transactions': [{
                    'action': 'TRANSFER', 'date': '2025-04-01',
                    'symbol': 'QZD.TO', 'quantity': 2000, 'currency': 'CAD',
                    'net_amount': 0.0, 'account': 'tfsa',
                    'description': 'TFI QZD ETF BOOK VALUE 16,506.95'}]}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                R.cmd_transfers_view(argparse.Namespace(
                    dir=str(root), account=None, json=True))
        rows = json.loads(out.getvalue())['transfers']
        self.assertEqual([r['value'] for r in rows], [16506.95])


# -------------------------------------------- schema / generator prompt
class TestSchemaContract(unittest.TestCase):
    """S053-13 / S065-10 / S065-13 / S033-20."""

    def _v(self, **kw):
        from taxjson.lib.brokerages.schema import validate_transactions
        tx = dict(action='BUYSELL', date='2025-12-31', currency='USD',
                  quantity=1, net_amount=10.0, price=10.0)
        tx.update(kw)
        return validate_transactions([tx])

    def test_negative_futures_price_is_not_an_error(self):
        errs, _ = self._v(symbol='F:CLK5.US', price=-5.0, net_amount=2.5,
                          multiplier=1000)
        self.assertFalse([e for e in errs if 'negative price' in e])
        errs, _ = self._v(symbol='XYZ.US', price=-5.0)
        self.assertTrue([e for e in errs if 'negative price' in e])

    def test_validate_cli_negative_futures_price(self):
        from taxjson.bin.taxjson_validate import validate_transactions as V
        rows = [{'action': 'BUYSELL', 'date': '2020-04-20',
                 'symbol': 'F:CLK0.US', 'quantity': 1, 'price': -5.0,
                 'net_amount': 2.5, 'currency': 'USD'}]
        flat = str(V(rows))
        self.assertNotIn('Price is negative', flat)

    def test_crypto_settling_after_its_trade_date_warns(self):
        _, warns = self._v(symbol='BTC', date_settle='2026-01-01')
        self.assertTrue([w for w in warns if 'bare (crypto) symbol' in w])
        _, warns = self._v(symbol='BTC', date_settle='2025-12-31')
        self.assertFalse([w for w in warns if 'bare (crypto) symbol' in w])

    def test_prompts_state_fee_sign_and_settle_helpers(self):
        from taxjson.lib.brokerages.schema import render_schema_prompt
        from taxjson.bin import taxjson_generate_parser as G
        prompt = render_schema_prompt()
        self.assertIn('FEE: net_amount POSITIVE = charged', prompt)
        self.assertIn('settle on the TRADE date', prompt)
        src = _P(G.__file__).read_text(encoding='utf-8')
        self.assertIn('equity_settlement_date for EQUITY', src)


if __name__ == '__main__':
    unittest.main()
