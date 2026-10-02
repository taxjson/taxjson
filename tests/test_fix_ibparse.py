"""Regression tests for the medium-round `ibparse` audit findings
(IB statement parser, shared broker helpers, taxjson-brokerage, the
transaction schema and broker detection). Every fixture is synthetic:
invented tickers and ISINs, fake account ids marked pii-ok."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

HEAD = ('Statement,Header,Field Name,Field Value\n'
        'Statement,Data,BrokerName,Interactive Brokers\n'
        'Statement,Data,Title,Activity Statement\n')
TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
            'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
XFER_H = ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
          'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
          'Market Value,Realized P/L,Cash Amount,Code\n')
CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
        'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
        'Code\n')


def _trade(sym, when, qty, price, proceeds, comm=0, code='O',
           cat='Stocks', cur='USD', acct='U5550001', disc='Order'):  # pii-ok
    return (f'Trades,Data,{disc},{cat},{cur},{acct},{sym},"{when}",{qty},'
            f'{price},0,{proceeds},{comm},0,0,0,{code}\n')


def _xfer(sym, date, qty, value, typ='ACATS', cat='Stocks', cur='USD',
          code=''):
    return (f'Transfers,Data,{cat},{cur},{sym},{date},{typ},In,'
            f'Other Broker,5550009,{qty},0,{value},0,0,{code}\n')  # pii-ok


def _ca(desc, qty, value=0, proceeds=0, cat='Stocks', cur='USD',
        when='2025-03-02, 20:25:00', code=''):
    return (f'Corporate Actions,Data,{cat},{cur},2025-03-02,"{when}",'
            f'"{desc}",{qty},{proceeds},{value},0,{code}\n')


def _parse_ib(text, name='ib.csv'):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text, encoding='utf-8')
        parser = IbBrokerage()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(p)
    return parser, txs, err.getvalue()


def _brokerage_cli(files, *extra, brokerage='ib'):
    """Run taxjson-brokerage over {name: text}; (rc, json-or-None,
    stderr, {sidecar})."""
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            if not name.endswith('.txt'):
                paths.append(str(p))
        args = [a.replace('{TD}', td) for a in extra]
        env = dict(os.environ)
        env['PYTHONPATH'] = str(REPO_ROOT / 'src')
        r = subprocess.run(
            [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
             '--brokerage', brokerage, *args, *paths],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env)
        side = Path(td) / 'side.json'
        sidecar = (json.loads(side.read_text()) if side.exists() else None)
    out = json.loads(r.stdout) if r.returncode == 0 and r.stdout else None
    return r.returncode, out, r.stderr, sidecar


# ------------------------------------------------ security overrides
class TestSecurityOverrides(unittest.TestCase):
    """R1-143, S001-00, S001-01, S001-02, S012-09, S027-01, S059-03."""

    def _load(self, text, raw=None):
        from taxjson.bin.taxjson_brokerage import load_security_overrides
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'ov.txt'
            if raw is not None:
                p.write_bytes(raw)
            else:
                p.write_text(text, encoding='utf-8')
            return load_security_overrides(p)

    def test_bom_is_stripped(self):
        # S001-00: a BOM made the first line's key '﻿global x ...'.
        ov = self._load(None, raw='﻿Global X US Dollar | USD | '
                                  'DLR.U.TO\n'.encode('utf-8'))
        self.assertEqual(ov, [('global x us dollar', 'USD', 'DLR.U.TO')])

    def test_malformed_line_is_an_error(self):
        # S001-01: a dropped currency field silently merged the ETF
        # into Digital Realty's pool.
        from taxjson.bin.taxjson_brokerage import SecurityOverrideError
        with self.assertRaises(SecurityOverrideError) as cm:
            self._load('# c\nUS DLR CURRENCY ETF | DLR.U.TO\n')
        self.assertIn('line 2', str(cm.exception))

    def test_lowercase_currency_is_normalized(self):
        # R1-143: 'usd' never matched 'USD', with no warning.
        ov = self._load('shopify | usd | SHOP.US\n')
        self.assertEqual(ov, [('shopify', 'USD', 'SHOP.US')])

    def test_bad_currency_is_an_error(self):
        from taxjson.bin.taxjson_brokerage import SecurityOverrideError
        with self.assertRaises(SecurityOverrideError):
            self._load('shopify | US Dollars | SHOP.US\n')

    def test_option_rows_are_not_rewritten(self):
        # R1-143: an issuer-name key rewrote the issuer's OPTION rows
        # to the share symbol (x100 lost, premium booked as a share).
        from taxjson.bin.taxjson_brokerage import apply_security_override
        ov = [('us dlr currency etf', 'USD', 'DLR.U.TO')]
        tx = {'symbol': 'DLR260116C00013000.US', 'currency': 'USD',
              'description': 'CALL .DLR 01/16/26 13 GLOBAL X US DLR '
                             'CURRENCY ETF'}
        apply_security_override(tx, ov)
        self.assertEqual(tx['symbol'], 'DLR260116C00013000.US')

    def test_key_matches_whole_words_only(self):
        # S012-09: 'BN | USD | BN.TO' rewrote ABNB and BNTX (IB's
        # description is the bare ticker).
        from taxjson.bin.taxjson_brokerage import apply_security_override
        ov = [('bn', 'USD', 'BN.TO')]
        for desc, want in (('BN', 'BN.TO'), ('ABNB', 'ABNB.US'),
                           ('BNTX', 'BNTX.US')):
            tx = {'symbol': f'{desc}.US', 'currency': 'USD',
                  'description': desc}
            apply_security_override(tx, ov)
            self.assertEqual(tx['symbol'], want, desc)

    def test_split_symbol_new_follows_the_override(self):
        # S001-02: IB SPLIT carries symbol_new == symbol; rewriting only
        # `symbol` turned the split into a rename back to ZZU.US.
        from taxjson.bin.taxjson_brokerage import apply_security_override
        ov = [('zzu', 'USD', 'ZZU.U.TO')]
        tx = {'action': 'SPLIT', 'symbol': 'ZZU.US', 'symbol_new': 'ZZU.US',
              'currency': 'USD', 'description': 'ZZU(US9990000001) Split '
                                                '2 for 1 (ZZU, ZZU CO, X)'}
        apply_security_override(tx, ov)
        self.assertEqual((tx['symbol'], tx['symbol_new']),
                         ('ZZU.U.TO', 'ZZU.U.TO'))

    IB_XFER = (HEAD + TRADES_H
               + _trade('QZDL', '2025-05-01, 09:30:00', 100, 10, -1000, -1)
               + XFER_H + _xfer('QZDL', '2025-06-02', 50, 500))

    def test_transfer_rows_carry_the_security_and_get_the_override(self):
        # S059-03 (IB TRANSFER description was only 'ACATS') and S027-01
        # (sidecar rows were split off before the override ran).
        files = {'ib.csv': self.IB_XFER,
                 'ov.txt': 'QZDL | USD | QZDL.U.TO\n'}
        for extra in (('--transfers',), ('--transfers-out',
                                          '{TD}/side.json')):
            rc, out, err, side = _brokerage_cli(
                files, '--security-overrides', '{TD}/ov.txt', *extra)
            self.assertEqual(rc, 0, err)
            rows = out['transactions'] + (side or {}).get(
                'transactions', [])
            xf = [t for t in rows if t['action'] == 'TRANSFER']
            self.assertEqual(len(xf), 1, extra)
            self.assertEqual(xf[0]['symbol'], 'QZDL.U.TO', extra)
            self.assertIn('ACATS', xf[0]['description'])

    def test_malformed_file_fails_the_parse(self):
        rc, _, err, _ = _brokerage_cli(
            {'ib.csv': self.IB_XFER, 'ov.txt': 'QZDL | QZDL.U.TO\n'},
            '--security-overrides', '{TD}/ov.txt')
        self.assertEqual(rc, 1)
        self.assertIn('line 1', err)


class TestTransferSidecarDedup(unittest.TestCase):
    """S026-23 / S027-00: an overlapping re-export doubled every
    sidecar TRANSFER row (the book itself is deduplicated)."""

    def test_overlapping_exports_keep_each_transfer_once(self):
        body = (HEAD + TRADES_H
                + _trade('QZOR', '2025-05-01, 09:30:00', 500, 10, -5000, -1)
                + XFER_H
                + _xfer('QZOR', '2025-07-02', -300, -3000, typ='InterDepot')
                + _xfer('QZOR', '2025-07-02', 300, 3000, typ='InterDepot',
                        cur='CAD'))
        rc, _, err, side = _brokerage_cli(
            {'a.csv': body, 'b.csv': body}, '--transfers-out',
            '{TD}/side.json')
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(side['transactions']), 2)


# ------------------------------------------------ detection, schema, numbers
class TestDetectUtf16(unittest.TestCase):
    """R1-70: detection opened every file as utf-8-sig, so a UTF-16
    export the parsers read could not be routed."""

    def test_utf16_ib_statement_is_detected(self):
        from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
        body = HEAD + TRADES_H + _trade('QZA', '2025-05-01, 09:30:00',
                                        10, 10, -100, -1)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'x.csv'
            p.write_bytes(body.encode('utf-16'))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(detect_brokerage(p), 'ib')


class TestSchemaTrades(unittest.TestCase):
    def _v(self, **tx):
        from taxjson.lib.brokerages.schema import validate_transactions
        base = {'action': 'BUYSELL', 'date': '2025-06-02',
                'symbol': 'ZZZ250620C00050000.TO', 'currency': 'CAD'}
        base.update(tx)
        return validate_transactions([base])

    def test_sell_whose_commission_exceeds_gross_is_legal(self):
        # S017-00: closing an option at 0.01 nets negative proceeds;
        # the engine models it, the schema refused it (every run rc 1).
        errs, _ = self._v(quantity=-1, price=0.01, net_amount=-9.95,
                          fee=10.95)
        self.assertEqual(errs, [])

    def test_negative_buy_net_is_still_an_error(self):
        errs, _ = self._v(quantity=1, price=0.01, net_amount=-9.95)
        self.assertTrue(errs)

    def test_assign_stock_leg_notional_is_checked(self):
        # S017-02: a Webull assignment stock leg with Proceeds x10 off
        # (or blank) booked silently — ASSIGN skipped the check.
        for net in (1900.0, 0.0):
            errs, warns = self._v(action='ASSIGN', symbol='ABBV.US',
                                  currency='USD', quantity=100,
                                  price=190.0, net_amount=net)
            self.assertTrue(any('far from' in w for w in warns + errs),
                            net)
        errs, warns = self._v(action='ASSIGN', symbol='ABBV.US',
                              currency='USD', quantity=100, price=190.0,
                              net_amount=19000.0)
        self.assertEqual((errs, warns), ([], []))


class TestCommaNumbers(unittest.TestCase):
    def test_leading_zero_group_is_not_a_thousands_separator(self):
        # S055-08: '0,125' is a decimal comma, never 125.
        from taxjson.lib.brokerages.base import (parse_strict_number,
                                                 check_comma_grouping)
        for bad in ('0,125', '00,125', '-0,500'):
            with self.assertRaises(BrokerageParseError, msg=bad):
                parse_strict_number(bad)
            with self.assertRaises(BrokerageParseError, msg=bad):
                check_comma_grouping(bad)
        self.assertEqual(parse_strict_number('1,250'), 1250.0)
        self.assertEqual(parse_strict_number('0.125'), 0.125)

    def test_sibling_helpers_refuse_leading_zero_group(self):
        from taxjson.lib.brokerages._crypto_common import strict_money
        with self.assertRaises(ValueError):
            strict_money('0,125')
        self.assertEqual(strict_money('1,250.5'), 1250.5)
        from taxjson.lib.brokerages.rbc_direct import rbc_number
        with self.assertRaises(Exception):
            rbc_number('0,125', path=Path('r.csv'), line=1, column='Price')
        from taxjson.bin.xlsx_to_csv import _GROUPED_NUMBER_RE
        self.assertIsNone(_GROUPED_NUMBER_RE.match('0,125'))

    def test_option_strike_with_thousands_separator(self):
        # R1-170: '5,000.00' was cut at the comma -> strike 5.
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        q = QuestradeBrokerage()
        o = q.parse_option_from_description('CALL SPX 12/19/25 5,000.00')
        self.assertEqual(q.format_occ_symbol(o['right'], o['base'],
                                             o['expiry'], o['strike']),
                         'SPX251219C05000000')
        from taxjson.lib.brokerages.webull import WebullBrokerage
        w = WebullBrokerage()
        a = w.parse_option_from_description('CALL BKNG06/20/25 5,025')
        self.assertEqual(a['strike'], '5025')

    def test_ib_split_ratio_with_thousands_separator(self):
        # S058-19: 'Split 1 for 1,000' was read as 1 for 1.
        body = (HEAD + TRADES_H
                + _trade('QZX', '2025-01-10, 10:00:00', 5500, 1, -5500, -1)
                + CA_H
                + _ca('QZX(US9990000301) Split 1 for 1,000 (QZX, QZX CORP, '
                      'US9990000301)', -5494.5))
        _, txs, _ = _parse_ib(body)
        sp = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(sp), 1)
        self.assertAlmostEqual(sp[0]['quantity'], 0.001)


# ------------------------------------------------ IB coverage / completeness
def _period(start, end):
    return f'Statement,Data,Period,"{start} - {end}"\n'


class TestIbStatementCoverage(unittest.TestCase):
    """R1-2 / R1-195: an IB statement that stops before Dec 31 of a
    finished year, or a gap between an account's statements, is
    reported (it used to parse silently; a Dec 28-31 sale vanished)."""

    BODY = TRADES_H + _trade('QZAC', '2024-06-03, 10:00:00', 100, 10,
                             -1000, -1)

    def test_statement_ending_early_is_flagged(self):
        ctx_err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'ib_2024.csv'
            p.write_text(HEAD + _period('January 1, 2024',
                                        'December 27, 2024') + self.BODY)
            with contextlib.redirect_stderr(ctx_err):
                IbBrokerage.prepare_files([p])
        self.assertIn('warning: ATTENTION:', ctx_err.getvalue())
        self.assertIn('2024-12-27', ctx_err.getvalue())

    def test_a_later_statement_closes_the_gap(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.csv'
            b = Path(td) / 'b.csv'
            a.write_text(HEAD + _period('January 1, 2024',
                                        'December 27, 2024') + self.BODY)
            b.write_text(HEAD + _period('December 28, 2024',
                                        'January 1, 2025'))
            with contextlib.redirect_stderr(err):
                IbBrokerage.prepare_files([a, b])
        self.assertNotIn('2024-12-27', err.getvalue())

    def test_gap_between_statements_is_flagged(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.csv'
            b = Path(td) / 'b.csv'
            a.write_text(HEAD + _period('January 1, 2023',
                                        'June 30, 2023'))
            b.write_text(HEAD + _period('August 1, 2023',
                                        'December 31, 2023'))
            with contextlib.redirect_stderr(err):
                IbBrokerage.prepare_files([a, b])
        self.assertIn('2023-07-01', err.getvalue())

    def test_full_year_and_mid_year_opening_are_quiet(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.csv'
            a.write_text(HEAD + _period('May 9, 2023', 'December 31, 2023'))
            with contextlib.redirect_stderr(err):
                IbBrokerage.prepare_files([a])
        self.assertNotIn('ATTENTION', err.getvalue())

    def test_run_echoes_attention_lines(self):
        import taxjson.bin.taxjson_run as run_mod
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'margin_ib.json'
            out.with_name(out.name + '.diag').write_text(
                'note: calm\nwarning: ATTENTION: x.csv ends early\n')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run_mod.echo_parse_stats(out)
        self.assertIn('ATTENTION: x.csv ends early', buf.getvalue())
        self.assertNotIn('calm', buf.getvalue())


class TestIbNoCashReport(unittest.TestCase):
    """R1-53: without a Cash Report the reconciliation was skipped
    without a word."""

    def test_missing_cash_report_is_said(self):
        _, _, err = _parse_ib(HEAD + TRADES_H + _trade(
            'QZAC', '2025-06-03, 10:00:00', 100, 10, -1000, -1))
        self.assertIn('warning: ATTENTION:', err)
        self.assertIn('no Cash Report', err)


class TestIbUnhandledAssetClass(unittest.TestCase):
    """S060-08: a Bonds / Mutual Funds trade carrying money was only a
    counted skip (fatal only through the Cash Report)."""

    def test_money_row_in_unknown_category_is_an_error(self):
        body = (HEAD + TRADES_H + _trade('QZBOND', '2025-03-01, 10:00:00',
                                         1000, 1, -1000, -1, cat='Bonds'))
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(body)
        self.assertIn("'Bonds'", str(cm.exception))


class TestIbOrderAndTradeRows(unittest.TestCase):
    """S060-10: every 'Trade' row after the first 'Order' row was
    skipped, for any symbol, and a Trade row listed BEFORE its Order row
    was booked twice."""

    def test_trade_only_fill_of_another_symbol_is_booked(self):
        body = (HEAD + TRADES_H
                + _trade('QZAA', '2025-03-03, 10:00:00', 100, 10, -1000, -1)
                + _trade('QZAA', '2025-03-03, 10:00:00', 100, 10, -1000,
                         -1, disc='Trade')
                + _trade('QZBB', '2025-03-04, 11:00:00', 50, 20, -1000, -1,
                         disc='Trade'))
        _, txs, _ = _parse_ib(body)
        self.assertEqual(sorted((t['symbol'], t['quantity']) for t in txs),
                         [('QZAA.US', 100.0), ('QZBB.US', 50.0)])

    def test_trade_row_before_its_order_row_is_not_doubled(self):
        body = (HEAD + TRADES_H
                + _trade('QZAA', '2025-03-03, 10:00:00', 100, 10, -1000, -1,
                         disc='Trade')
                + _trade('QZAA', '2025-03-03, 10:00:00', 100, 10, -1000, -1))
        _, txs, _ = _parse_ib(body)
        self.assertEqual([(t['symbol'], t['quantity']) for t in txs],
                         [('QZAA.US', 100.0)])

    def test_levels_that_disagree_are_an_error(self):
        body = (HEAD + TRADES_H
                + _trade('QZAA', '2025-03-03, 10:00:00', 100, 10, -1000, -1)
                + _trade('QZAA', '2025-03-03, 10:00:00', 60, 10, -600, -1,
                         disc='Trade'))
        with self.assertRaises(BrokerageParseError):
            _parse_ib(body)


class TestIbTransferWithoutQuantity(unittest.TestCase):
    """R1-55: a security transfer with a blank Qty was a calm non-event
    (the position change was lost)."""

    def test_blank_qty_on_a_stock_transfer_is_an_error(self):
        body = HEAD + XFER_H + _xfer('QZDD', '2025-07-02', '', 3000,
                                     typ='ATON', cur='CAD')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(body)
        self.assertIn('Qty', str(cm.exception))


# ------------------------------------------------ IB corporate actions
FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,'
         'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
         'Expiry,Delivery Month,Type,Strike,Code\n')


class TestIbOptionsExpirationsCategory(unittest.TestCase):
    """R1-56: the section's rows were all read as equity options, so a
    futures-option expiry became a phantom equity option."""

    def test_row_category_is_honoured(self):
        fii = (FII_H + 'Financial Instrument Information,Data,Options On '
               'Futures,QZCL JAN26 52 P,QZCL JAN26 52 P,990000071,,QZCL,'
               'NYMEX,1000,2025-12-16,2026-01,P,52,\n')
        body = (HEAD + TRADES_H
                + _trade('QZCL JAN26 52 P', '2025-11-03, 10:00:00', 1, 1.5,
                         -1500, -2, cat='Options On Futures')
                + 'Options Expirations,Header,Asset Category,Currency,'
                  'Symbol,Date/Time,Quantity,Code\n'
                + 'Options Expirations,Data,Options On Futures,USD,'
                  'QZCL JAN26 52 P,"2025-12-16, 16:20:00",-1,C;Ep\n'
                + fii)
        _, txs, _ = _parse_ib(body)
        self.assertEqual({t['symbol'] for t in txs},
                         {'F:QZCL251216P00052000.US'})


class TestIbCorporateActionsNotBooked(unittest.TestCase):
    """Rows neither this parser nor taxjson-corp-actions books are an
    UNBOOKED warning (console, fatal under run --strict), never an
    equity SPLIT on an invented symbol or a silent no-op."""

    def test_option_adjustment_row_is_not_an_equity_split(self):
        # S058-16
        body = (HEAD + CA_H + _ca(
            'QZNV 21JUN24 1200 C(QZNV 240621C01200000) Split 10 for 1 '
            '(QZNV 21JUN24 1200 C, QZNV CORP, US9990000401)', -1,
            cat='Equity and Index Options'))
        _, txs, err = _parse_ib(body)
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('Equity and Index Options', err)

    def test_stock_dividend_books_the_shares(self):
        # S058-24: the delivered shares were dropped (phantom short).
        body = (HEAD + CA_H + _ca(
            'QZSD(US9990000999) Stock Dividend US9990000999 1 for 20 '
            '(QZSD, QZSD CORP, US9990000999)', 5, value=100))
        _, txs, err = _parse_ib(body)
        self.assertEqual([(t['action'], t['symbol'], t['quantity'],
                           t['net_amount']) for t in txs],
                         [('BUYSELL', 'QZSD.US', 5.0, 0.0)])
        self.assertIn('stock dividend', err)
        self.assertIn('warning: ATTENTION:', err)

    def test_spinoff_on_a_short_parent_is_unbooked(self):
        # S058-22: the debit leg (qty < 0) was an "unhandled non-spinoff".
        body = (HEAD + CA_H + _ca(
            'QZPA(CA9990000001) Spinoff  1 for 5 (QZSP, SPINCO CORP, '
            'CA9990000002)', -20, value=-900, cur='CAD'))
        _, txs, err = _parse_ib(body)
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('short', err)

    def test_share_for_share_tender_is_unbooked(self):
        # S013-06: an allocation delivering ANOTHER security was a no-op.
        rows = (_ca('QZTG(CA9990000011) Tendered to 99999999 1 FOR 1 '
                    '(QZTG.TEN, QZTG CORP - TENDER, CA9990000011)', -1000,
                    cur='CAD')
                + _ca('QZTG.TEN(CA9990000011) Tendered to 99999999 1 FOR 1 '
                      '(QZTG.TEN, QZTG CORP - TENDER, CA9990000011)', 1000,
                      cur='CAD')
                + _ca('QZTG.TEN(99999999) Merged(Voluntary Offer Allocation)'
                      ' WITH CA8880000001 1 for 2 (QZAQ, ACQUIRER INC, '
                      'CA8880000001)', 500, value=25000, cur='CAD')
                + _ca('QZTG.TEN(99999999) Merged(Voluntary Offer Allocation)'
                      ' WITH CA8880000001 1 for 2 (QZTG.TEN, QZTG CORP - '
                      'TENDER, CA9990000011)', -1000, cur='CAD'))
        _, txs, err = _parse_ib(HEAD + CA_H + rows)
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('QZAQ', err)

    def test_same_security_tender_round_trip_stays_a_no_op(self):
        rows = (_ca('QZAU(CA9990000021) Tendered to 99999998 1 FOR 1 '
                    '(QZAU.TEN, QZAU CORP - TENDER, CA9990000021)', -100,
                    cur='CAD')
                + _ca('QZAU.TEN(CA9990000021) Tendered to 99999998 1 FOR 1 '
                      '(QZAU.TEN, QZAU CORP - TENDER, CA9990000021)', 100,
                      cur='CAD')
                + _ca('QZAU.TEN(99999998) Merged(Voluntary Offer Allocation)'
                      ' WITH CA9990000021 1 for 1 (QZAU, QZAU CORP, '
                      'CA9990000021)', 100, cur='CAD')
                + _ca('QZAU.TEN(99999998) Merged(Voluntary Offer Allocation)'
                      ' WITH CA9990000021 1 for 1 (QZAU.TEN, QZAU CORP - '
                      'TENDER, CA9990000021)', -100, cur='CAD'))
        _, txs, err = _parse_ib(HEAD + CA_H + rows)
        self.assertEqual(txs, [])
        self.assertNotIn('UNBOOKED', err)
        self.assertIn('recognized no-op', err)


# ------------------------------------------------ IB security identity
DIV_H = 'Dividends,Header,Currency,Date,Description,Amount\n'
WHT_H = 'Withholding Tax,Header,Currency,Date,Description,Amount\n'


def _fii_stock(sym, isin, conid='990000001', exch='NYSE'):
    return (f'Financial Instrument Information,Data,Stocks,"{sym}",{sym} '
            f'CORP,{conid},{isin},,{exch},1,,,COMMON,,\n')


def _parse_account(files):
    """prepare_files + parse_file over {name: text}, like
    taxjson-brokerage. -> (txs, stderr)."""
    err = io.StringIO()
    txs = []
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            paths.append(p)
        with contextlib.redirect_stderr(err):
            ctx = IbBrokerage.prepare_files(paths)
            for p in paths:
                b = IbBrokerage()
                b.account_context = ctx
                txs += b.parse_file(p)
    return txs, err.getvalue()


class TestIbIncomeRebindNeedsTheSameIsin(unittest.TestCase):
    """S059-24 / S060-19: a dividend was rebound to whatever listing of
    the same ROOT was held — AT&T's (US ISIN) onto Telus (T.TO)."""

    def _body(self, fii_isin):
        return (HEAD + TRADES_H
                + _trade('QZT', '2025-02-03, 10:00:00', 100, 30, -3000, -1,
                         cur='CAD')
                + DIV_H + 'Dividends,Data,USD,2025-05-01,QZT(US9990000701) '
                          'Cash Dividend USD 0.2775 per Share (Ordinary '
                          'Dividend),5550\n'
                + WHT_H + 'Withholding Tax,Data,USD,2025-05-01,QZT('
                          'US9990000701) Cash Dividend USD 0.2775 per Share '
                          '- US Tax,-832.50\n'
                + FII_H + _fii_stock('QZT', fii_isin, exch='TSE'))

    def test_other_issuer_keeps_its_isin_listing(self):
        _, txs, err = _parse_ib(self._body('CA9990000702'))
        inc = {(t['action'], t['symbol']) for t in txs
               if t['action'] in ('DIVIDEND', 'TAX')}
        self.assertEqual(inc, {('DIVIDEND', 'QZT.US'), ('TAX', 'QZT.US')})
        self.assertIn('ISIN', err)

    def test_same_issuer_is_still_rebound(self):
        _, txs, _ = _parse_ib(self._body('US9990000701'))
        inc = {(t['action'], t['symbol']) for t in txs
               if t['action'] in ('DIVIDEND', 'TAX')}
        self.assertEqual(inc, {('DIVIDEND', 'QZT.TO'), ('TAX', 'QZT.TO')})

    def test_no_temporary_keys_leak(self):
        _, txs, _ = _parse_ib(self._body('US9990000701'))
        self.assertFalse(any(k.startswith('_') for t in txs for k in t))


class TestIbIncomeRebindAcrossStatements(unittest.TestCase):
    """S060-00: a statement with only a ROC row (no trades, no Open
    Positions) kept the ISIN suffix: a gain on a phantom BTG.TO."""

    def test_holding_from_the_other_statement_rebinds(self):
        a = (HEAD + TRADES_H
             + _trade('QZBT', '2025-03-03, 10:00:00', 1000, 3, -3000, -1))
        b = (HEAD + DIV_H
             + 'Dividends,Data,USD,2026-06-19,QZBT(CA9990000801) Cash '
               'Dividend USD 0.30 per Share (Return of Capital),300\n')
        txs, _ = _parse_account({'a.csv': a, 'b.csv': b})
        roc = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual([t['symbol'] for t in roc], ['QZBT.US'])


class TestIbUsdClassTsxUnits(unittest.TestCase):
    """S010-06: an IB USD trade of a TSX '.U' unit (ZSP.U) became
    ZSP.U.US — a fictional US security; RBC books DLR.U.TO."""

    def test_u_unit_on_tsx_is_to(self):
        body = (HEAD + TRADES_H
                + _trade('QZSP.U', '2025-03-03, 10:00:00', 100, 50, -5000,
                         -1)
                + FII_H + _fii_stock('QZSP.U', 'CA9990000901', exch='TSE'))
        _, txs, _ = _parse_ib(body)
        self.assertEqual([t['symbol'] for t in txs], ['QZSP.U.TO'])

    def test_usd_trade_of_a_tsx_listed_ordinary_share_stays_us(self):
        body = (HEAD + TRADES_H
                + _trade('QZMD', '2025-03-03, 10:00:00', 100, 50, -5000, -1)
                + FII_H + _fii_stock('QZMD', 'CA9990000902', exch='TSE'))
        _, txs, _ = _parse_ib(body)
        self.assertEqual([t['symbol'] for t in txs], ['QZMD.US'])


def _fii_opt(syms, desc, conid, underlying):
    return (f'Financial Instrument Information,Data,Equity and Index '
            f'Options,"{syms}",{desc},{conid},,{underlying},CBOE,100,'
            f'2025-12-19,2025-12,P,60,\n')


class TestIbOptionRootAliases(unittest.TestCase):
    def test_rename_alias_canonical_is_the_underlying(self):
        # S059-11: SQ -> XYZ rename; the shortest root (SQ) won, so the
        # assigned put never met the delivered XYZ shares.
        body = (HEAD + TRADES_H
                + _trade('QZS 19DEC25 60 P', '2025-10-10, 10:00:00', -1, 4,
                         400, 0, cat='Equity and Index Options')
                + _trade('QZXYZ 19DEC25 60 P', '2025-12-19, 16:20:00', 1, 0,
                         0, 0, code='A;C', cat='Equity and Index Options')
                + _trade('QZXYZ', '2025-12-19, 16:20:00', 100, 60, -6000, 0,
                         code='A;O')
                + FII_H + _fii_opt('QZS   251219P00060000, QZXYZ 251219P'
                                   '00060000', 'QZXYZ 19DEC25 60 P',
                                   '990000011', 'QZXYZ'))
        _, txs, _ = _parse_ib(body)
        opts = {t['symbol'] for t in txs if 'P000' in t['symbol']}
        self.assertEqual(opts, {'QZXYZ251219P00060000.US'})

    def test_alias_learned_from_another_statement(self):
        # S059-15: the 2026 statement lists only the adjusted root.
        a = (HEAD + TRADES_H
             + _trade('QZD 19DEC25 60 P', '2025-10-10, 10:00:00', -1, 4, 400,
                      0, cat='Equity and Index Options')
             + FII_H + _fii_opt('QZD   251219P00060000, QZD1  251219P'
                                '00060000', 'QZD 19DEC25 60 P', '990000021',
                                'QZD'))
        b = (HEAD + TRADES_H
             + _trade('QZD1 19DEC25 60 P', '2025-11-10, 10:00:00', 1, 3,
                      -300, 0, code='C', cat='Equity and Index Options')
             + FII_H + _fii_opt('QZD1  251219P00060000', 'QZD1 19DEC25 60 P',
                                '990000021', 'QZD'))
        txs, _ = _parse_account({'a.csv': a, 'b.csv': b})
        self.assertEqual({t['symbol'] for t in txs},
                         {'QZD251219P00060000.US'})


class TestIbStockSymbolAliases(unittest.TestCase):
    """S059-13 / S060-17: one stock conid under two symbols split the
    position into two pools silently."""

    def test_one_conid_two_symbols_in_a_statement_is_flagged(self):
        body = (HEAD + TRADES_H
                + _trade('QZOL', '2025-02-05, 10:00:00', 500, 30, -15000, -1)
                + _trade('QZNW', '2025-09-10, 10:00:00', -500, 40, 20000, -1)
                + FII_H + _fii_stock('QZOL, QZNW', 'US9990001001',
                                     conid='990000031'))
        _, err = _parse_account({"a.csv": body})
        self.assertIn('warning: ATTENTION:', err)
        self.assertIn('QZOL', err)
        self.assertIn('QZNW', err)

    def test_conid_renamed_between_statements_is_flagged(self):
        a = (HEAD + TRADES_H
             + _trade('QZOL', '2025-02-05, 10:00:00', 500, 30, -15000, -1)
             + FII_H + _fii_stock('QZOL', 'US9990001001', conid='990000031'))
        b = (HEAD + TRADES_H
             + _trade('QZNW', '2026-03-10, 10:00:00', -500, 40, 20000, -1)
             + FII_H + _fii_stock('QZNW', 'US9990001001', conid='990000031'))
        _, err = _parse_account({'a.csv': a, 'b.csv': b})
        self.assertIn('warning: ATTENTION:', err)
        self.assertIn('GLOBAL', err)


# ------------------------------------------------ settlement pairing
class TestIbAssignLegsShareASettleDate(unittest.TestCase):
    """S058-01: before the T+1 cutover the option leg settled T+1 and
    the stock leg T+2, so a same-day trade could consume the premium."""

    @rule("CA-DATE-04")
    @rule("US-DATE-04")
    def test_pre_cutover_option_leg_takes_the_stock_leg_settle(self):
        body = (HEAD + TRADES_H
                + _trade('QZX 16JUN23 50 P', '2023-06-16, 16:20:00', 1, 0, 0,
                         0, code='A;C', cat='Equity and Index Options',
                         cur='CAD')
                + _trade('QZX', '2023-06-16, 16:20:00', 100, 50, -5000, 0,
                         code='A;O', cur='CAD'))
        _, txs, _ = _parse_ib(body)
        leg = next(t for t in txs if t['action'] == 'ASSIGN')
        stock = next(t for t in txs if t['action'] == 'BUYSELL')
        self.assertEqual(stock['date_settle'], '2023-06-20')
        self.assertEqual(leg['date_settle'], '2023-06-20')


WB_HEAD = (',,,,,,,,,\n'
           '"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\n'
           'Symbole","Security Description\nDescription des titres",'
           'Type Code of Securities Code de genre de titres,"Quantity of '
           'Securities Quantité\nde titres","Price\nPrix",,Proceeds of '
           'Disposition or Settlement Amount Produits de disposition\n'
           ',,,,,,,,,\n')


class TestExpiryClampAcrossFiles(unittest.TestCase):
    """S055-22: a Dec-31 0DTE trade was clamped to its expiry only when
    the expiry row sat in the same yearly export."""

    def test_expiry_in_the_other_export_still_clamps(self):
        a = WB_HEAD + 'USD,31-12-2025,SELL,,CALL QZQ12/31/25 511,,-4,,,\n'
        b = (WB_HEAD + 'USD,02-01-2026,BUY,,CALL QZQ12/31/25 511,,4,2.35,,'
                       '(943.96)\n')
        rc, out, err, _ = _brokerage_cli({'wb_2025.csv': a, 'wb_2026.csv': b},
                                         brokerage='webull')
        self.assertEqual(rc, 0, err)
        buy = next(t for t in out['transactions'] if t['quantity'] == 4)
        self.assertEqual(buy['date'], '2025-12-31')
        self.assertEqual(buy['date_settle'], '2025-12-31')


class TestUsProjectReturnOfCapital(unittest.TestCase):
    """S013-01: a US project booked a US issuer's return of capital as
    a dividend under Canada's s.90(1) default. Partition INPUTS-02 /
    SPEC-08: an explicit foreign_return_of_capital = "dividend" was then
    still honoured in a US project (and this test pinned it); the key is
    Canada-only now — refused by every config reader — and the resolver
    returns "acb" for a US project whatever the table says."""

    @rule("CA-ACB-08")
    def test_default_follows_the_country(self):
        from taxjson.bin.taxjson_run import ib_foreign_roc_mode
        self.assertEqual(ib_foreign_roc_mode({'country': 'usa'}), 'acb')
        self.assertEqual(ib_foreign_roc_mode({'country': 'canada'}),
                         'dividend')
        self.assertEqual(ib_foreign_roc_mode(
            {'country': 'usa', 'foreign_return_of_capital': 'dividend'}),
            'acb')


class TestGenericSymbolSpelling(unittest.TestCase):
    """S010-04: the generic importer only dotted spaces — BRK-B and
    BRK/B were separate pools from BRK.B, a padded OCC symbol became
    'XYZ...250321C...', and an option description was booked as a
    share."""

    HDR = 'Date,Type,Ticker,Shares,Price,Amount,Commission,Currency\n'
    TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
            'quantity="Shares"\nprice="Price"\namount="Amount"\n'
            'fee="Commission"\ncurrency="Currency"\n'
            '[actions]\n"BUY"="buy"\n"SELL"="sell"\n')

    def _parse(self, rows):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / 'generic_t.csv'
            c.write_text(self.HDR + rows)
            c.with_name(c.name + '.toml').write_text(self.TOML)
            with contextlib.redirect_stderr(io.StringIO()):
                return GenericBrokerage().parse_file(c)

    def test_class_separators_fold_to_a_dot(self):
        txs = self._parse('2025-03-10,BUY,QZK-B,10,300,3000,0,USD\n'
                          '2025-03-11,BUY,QZK/B,10,300,3000,0,USD\n'
                          '2025-03-12,BUY,QZK B,10,300,3000,0,USD\n')
        self.assertEqual({t['symbol'] for t in txs}, {'QZK.B.US'})

    def test_padded_occ_symbol_is_compacted(self):
        txs = self._parse('2025-03-10,BUY,QZY   250321C00050000,1,2,200,0,'
                          'USD\n')
        self.assertEqual(txs[0]['symbol'], 'QZY250321C00050000.US')

    def test_option_description_is_refused(self):
        with self.assertRaises(BrokerageParseError):
            self._parse('2025-03-10,BUY,QZY 21MAR25 50 C,1,200,200,0,USD\n')


class TestIbCorporateActionCancelAcrossStatements(unittest.TestCase):
    """S059-04: a split booked in the 2025 statement and cancelled (Ca)
    and rebooked in the 2026 statement stayed applied (x3 then x2)."""

    def test_cancel_in_the_next_statement_undoes_the_original(self):
        desc3 = ('QZX(US9990000301) Split 3 for 1 (QZX, QZX CORP, '
                 'US9990000301)')
        desc2 = ('QZX(US9990000301) Split 2 for 1 (QZX, QZX CORP, '
                 'US9990000301)')
        a = (HEAD + TRADES_H
             + _trade('QZX', '2025-06-02, 10:00:00', 100, 10, -1000, -1)
             + CA_H + _ca(desc3, 200, when='2025-12-30, 20:25:00'))
        b = (HEAD + CA_H
             + _ca(desc3, -200, when='2026-01-05, 20:25:00', code='Ca')
             + _ca(desc2, 100, when='2026-01-05, 20:25:00'))
        rc, out, err, _ = _brokerage_cli({'a.csv': a, 'b.csv': b})
        self.assertEqual(rc, 0, err)
        splits = [(t['date'], t['quantity']) for t in out['transactions']
                  if t['action'] == 'SPLIT']
        self.assertEqual(splits, [('2026-01-05', 2.0)])
        self.assertNotIn('nothing undone', err)


if __name__ == '__main__':
    unittest.main()
