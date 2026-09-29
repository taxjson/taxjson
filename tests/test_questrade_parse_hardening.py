"""Regression pins for the 2026-09 Questrade parse hardening.

Same failure class as the IB pins: a missing column read as 0, a
default currency, a rebate read as a charge, a garbage date passed
through, a first-match symbol rebind. Every fixture here is SYNTHETIC
(a fake account number, see ACCT; invented tickers).
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.questrade import QuestradeBrokerage

REPO_ROOT = Path(__file__).resolve().parent.parent
H = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
     'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
     'Activity Type,Account Type\n')
ACCT = '55500001'  # pii-ok


def row(td='2026-02-02 12:00:00 AM', sd='2026-02-03 12:00:00 AM',
        action='Buy', sym='QZA', desc='QZA CORP WE ACTED AS AGENT', qty='10',
        price='50', gross='-500', comm='-4.95', net='-504.95', cur='USD',
        act='Trades', atype='Individual margin'):
    cells = [td, sd, action, sym, desc, qty, price, gross, comm, net, cur,
             ACCT, act, atype]
    return ','.join(f'"{c}"' if ',' in c else c for c in cells) + '\n'


def _parse(text, taxable=None, encoding='utf-8'):
    with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False,
                                     encoding=encoding) as f:
        f.write(text)
        name = f.name
    p = QuestradeBrokerage()
    if taxable is not None:
        p.account_taxable = taxable
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            txs = p.parse_file(Path(name))
    finally:
        os.remove(name)
    return p, txs, err.getvalue()


class TestRequiredColumns(unittest.TestCase):
    def test_each_of_the_14_columns_is_required(self):
        cols = H.rstrip('\n').split(',')
        for c in cols:
            with self.subTest(col=c):
                hdr = ','.join('Renamed' if x == c else x for x in cols)
                with self.assertRaises(BrokerageParseError) as cm:
                    _parse(hdr + '\n' + row())
                self.assertIn(repr(c), str(cm.exception))

    def test_blank_currency_is_not_assumed_usd(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(H + row(cur=''))
        self.assertIn('Currency', str(cm.exception))

    def test_garbage_money_fails_instead_of_zero(self):
        for kw in ({'net': '-504,95'}, {'comm': 'n/a'}, {'qty': '1 000'},
                   {'gross': ''}):
            with self.subTest(**kw):
                with self.assertRaises(BrokerageParseError):
                    _parse(H + row(**kw))

    def test_parentheses_and_unicode_minus_are_negative(self):
        _, txs, _ = _parse(H + row(gross='(500.00)', comm='−4.95',
                                   net='(504.95)'))
        self.assertAlmostEqual(txs[0]['net_amount'], 504.95)
        self.assertAlmostEqual(txs[0]['commission'], 4.95)


class TestMoneyCheck(unittest.TestCase):
    def test_header_only_swap_of_gross_and_commission_fails(self):
        swapped = H.replace('Gross Amount,Commission', 'Commission,Gross Amount')
        with self.assertRaises(BrokerageParseError):
            _parse(swapped + row())

    def test_net_that_is_not_gross_plus_commission_fails(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(H + row(net='-600'))
        self.assertIn('Commission', str(cm.exception))

    def test_option_multiplier_declared(self):
        _, txs, _ = _parse(H + row(sym='QZA15Jan27C50.00',
                                   desc='CALL QZA 01/15/27 50 QZA CORP',
                                   qty='2', price='1.5', gross='-300',
                                   comm='-2', net='-302'))
        self.assertEqual(txs[0]['multiplier'], 100.0)
        self.assertAlmostEqual(txs[0]['net_amount'], 302.0)


class TestCommissionRebate(unittest.TestCase):
    def test_positive_commission_is_a_rebate(self):
        _, txs, _ = _parse(H + row(comm='0.50', net='-499.50'))
        self.assertAlmostEqual(txs[0]['commission'], -0.5)
        self.assertAlmostEqual(txs[0]['net_amount'], 499.5,
                               msg="was 500.50: abs() made the rebate a "
                                   "charge")

    def test_sell_rebate(self):
        _, txs, _ = _parse(H + row(action='Sell', qty='-10', gross='500',
                                   comm='0.50', net='500.50'))
        self.assertAlmostEqual(txs[0]['commission'], -0.5)
        self.assertAlmostEqual(txs[0]['net_amount'], 500.5)


class TestDates(unittest.TestCase):
    def test_iso_forms_from_an_xlsx_export(self):
        for td, sd in (('2025-12-31 00:00:00', '2026-01-02 00:00:00'),
                       ('2025-12-31', '2026-01-02')):
            with self.subTest(td=td):
                _, txs, _ = _parse(H + row(td=td, sd=sd))
                self.assertEqual(txs[0]['date'], '2025-12-31')
                self.assertEqual(txs[0]['date_settle'], '2026-01-02')

    def test_present_but_unparseable_settlement_is_an_error(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(H + row(sd='31/12/2025'))
        self.assertIn('Settlement Date', str(cm.exception))

    def test_blank_settlement_falls_back_to_the_cycle(self):
        _, txs, _ = _parse(H + row(td='2026-02-02 12:00:00 AM', sd=''))
        self.assertEqual(txs[0]['date_settle'], '2026-02-03')

    def test_unparseable_trade_date_is_an_error(self):
        with self.assertRaises(BrokerageParseError):
            _parse(H + row(td='02/02/2026'))


class TestSymbolRebind(unittest.TestCase):
    TRADE = row(sym='QZB', desc='QZB MINING LTD WE ACTED AS AGENT')

    def _div(self, sym, desc='QZB MINING LTD CASH DIV ON 10 SHS REC '
                             '01/15/26 PAY 02/01/26'):
        return row(action='DIV', sym=sym, desc=desc, qty='0', price='0',
                   gross='0', comm='0', net='5.00', act='Dividends')

    def test_internal_code_rebinds_to_the_traded_symbol(self):
        _, txs, _ = _parse(H + self.TRADE + self._div('Q012345'))
        self.assertEqual(txs[1]['symbol'], 'QZB.US')

    def test_dotted_dividend_code_rebinds(self):
        _, txs, _ = _parse(H + self.TRADE + self._div('.QZBT'))
        self.assertEqual(txs[1]['symbol'], 'QZB.US')

    def test_a_real_ticker_is_kept_and_the_mismatch_reported(self):
        _, txs, err = _parse(H + self.TRADE + self._div('QZBX'))
        self.assertEqual(txs[1]['symbol'], 'QZBX.US')
        self.assertIn("'QZBX' is booked under its own symbol", err)

    def test_description_mapping_to_two_symbols_is_not_guessed(self):
        body = (H + self.TRADE
                + row(sym='QZC', desc='QZB MINING LTD WE ACTED AS AGENT',
                      qty='5', gross='-250', net='-254.95')
                + self._div('Q012345'))
        _, txs, err = _parse(body)
        self.assertEqual(txs[-1]['symbol'], 'Q012345.US')
        self.assertIn('matches several traded symbols', err)


class TestQuantityBearingDis(unittest.TestCase):
    def test_spinoff_legs_are_corporate_action_legs(self):
        leg = ('WTS QZD CORP WT EXP PENDING SPINOFF ON 1000 SHS FROM SEC# '
               'J000001 QZD CORP RE')
        body = (H + row(action='DIS', sym='QZDW', desc=leg, qty='100',
                        price='0', gross='0', comm='0', net='0',
                        act='Dividends')
                + row(action='DIS', sym='QZDW', desc=leg, qty='-100',
                      price='0', gross='0', comm='0', net='0',
                      act='Dividends'))
        p, txs, err = _parse(body)
        self.assertEqual(txs, [])
        self.assertEqual(p._skip_counts.get(
            'non-event DIS corporate-action leg (quantity-bearing; booked '
            'by taxjson-corp-actions)'), 2)
        self.assertIn('corporate-action leg(s)', err)
        self.assertNotIn('zero-net dividend', str(p._skip_counts))

    def test_other_quantity_dis_is_loud(self):
        p, _, _ = _parse(H + row(action='DIS', sym='QZE',
                                 desc='QZE CORP SOMETHING ON 10 SHS',
                                 qty='5', price='0', gross='0', comm='0',
                                 net='0', act='Dividends'))
        loud = [k for k in p._skip_counts if not k.startswith('non-event')]
        self.assertEqual(len(loud), 1)


class TestTaxableAccountCaveats(unittest.TestCase):
    NONRES = row(action='DIV', sym='QZF', desc='QZF PLC CASH DIV ON 20 SHS '
                 'REC 05/08/26 PAY 05/29/26 NON-RES TAX WITHHELD', qty='0',
                 price='0', gross='0', comm='0', net='16.50',
                 act='Dividends')
    NO_BV = row(action='TFI', sym='QZG', desc='QZG INC TRANSFER IN OTHER '
                'BROKER', qty='12', price='0', gross='0', comm='0', net='0',
                act='Transfers')

    def test_warns_in_a_taxable_account(self):
        _, txs, err = _parse(H + self.NONRES + self.NO_BV, taxable=True)
        self.assertIn('NON-RES TAX WITHHELD are booked at the NET', err)
        self.assertIn('no TRANSFER BOOK VALUE', err)
        self.assertAlmostEqual(txs[0]['net_amount'], 16.5)

    def test_quiet_in_a_sheltered_account(self):
        _, _, err = _parse(H + self.NONRES + self.NO_BV, taxable=False)
        self.assertNotIn('NON-RES', err)
        self.assertNotIn('BOOK VALUE', err)

    def test_registered_account_type_column_is_enough(self):
        body = H + self.NONRES.replace('Individual margin',
                                       'Individual TFSA')
        _, _, err = _parse(body)
        self.assertNotIn('NON-RES', err)

    def test_cli_account_type_reaches_the_parser(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'qt.csv'
            p.write_text(H + self.NONRES.replace('Individual margin', 'API'))
            outs = {}
            for kind in ('taxable', 'sheltered'):
                r = subprocess.run(
                    [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
                     '--brokerage', 'questrade', '--account-type', kind,
                     str(p)], cwd=REPO_ROOT, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                outs[kind] = r.stderr
        self.assertIn('NON-RES', outs['taxable'])
        self.assertNotIn('NON-RES', outs['sheltered'])


class TestFileHygiene(unittest.TestCase):
    def test_repeated_header_and_blank_trailer_are_quiet(self):
        body = H + row() + H + row(td='2026-02-03 12:00:00 AM') + ',' * 13 + '\n\n'
        p, txs, _ = _parse(body)
        self.assertEqual(len(txs), 2)
        loud = [k for k in p._skip_counts if not k.startswith('non-event')]
        self.assertEqual(loud, [])

    def test_utf16_export_decodes(self):
        _, txs, _ = _parse(H + row(), encoding='utf-16')
        self.assertEqual(len(txs), 1)


if __name__ == '__main__':
    unittest.main()
