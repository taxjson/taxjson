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


if __name__ == '__main__':
    unittest.main()
