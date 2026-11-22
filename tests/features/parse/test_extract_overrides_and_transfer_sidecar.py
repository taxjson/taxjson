"""Regression tests for the medium-round `ibparse` audit findings
(IB statement parser, shared broker helpers, taxjson-brokerage, the
transaction schema and broker detection). Every fixture is synthetic:
invented tickers and ISINs, fake account ids marked pii-ok.

Formerly: test_fix_ibparse"""
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from _support.ib_statement_rows import HEAD, TRADES_H, XFER_H, _trade, _xfer, _brokerage_cli


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------ security overrides
class TestSecurityOverrides(unittest.TestCase):
    """R1-143, S001-00, S001-01, S001-02, S012-09, S027-01, S059-03.

    Formerly: test_fix_ibparse.TestSecurityOverrides"""

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
        ov = self._load(None, raw='﻿EXTRACT Global X US Dollar | USD | '
                                  'DLR.U.TO\n'.encode('utf-8'))
        self.assertEqual(ov, [('global x us dollar', 'USD', 'DLR.U.TO')])

    def test_malformed_line_is_an_error(self):
        # S001-01: a dropped currency field silently merged the ETF
        # into Digital Realty's pool.
        from taxjson.bin.taxjson_brokerage import SecurityOverrideError
        with self.assertRaises(SecurityOverrideError) as cm:
            self._load('# c\nEXTRACT US DLR CURRENCY ETF | DLR.U.TO\n')
        self.assertIn('ov.txt:2:', str(cm.exception))

    def test_lowercase_currency_is_normalized(self):
        # R1-143: 'usd' never matched 'USD', with no warning.
        ov = self._load('EXTRACT shopify | usd | SHOP.US\n')
        self.assertEqual(ov, [('shopify', 'USD', 'SHOP.US')])

    def test_bad_currency_is_an_error(self):
        from taxjson.bin.taxjson_brokerage import SecurityOverrideError
        with self.assertRaises(SecurityOverrideError):
            self._load('EXTRACT shopify | US Dollars | SHOP.US\n')

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
                 'ov.txt': 'EXTRACT QZDL | USD | QZDL.U.TO\n'}
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
            {'ib.csv': self.IB_XFER, 'ov.txt': 'EXTRACT QZDL | QZDL.U.TO\n'},
            '--security-overrides', '{TD}/ov.txt')
        self.assertEqual(rc, 1)
        self.assertIn('ov.txt:1:', err)


class TestTransferSidecarDedup(unittest.TestCase):
    """S026-23 / S027-00: an overlapping re-export doubled every
    sidecar TRANSFER row (the book itself is deduplicated).

    Formerly: test_fix_ibparse.TestTransferSidecarDedup"""

    def test_overlapping_exports_keep_each_transfer_once(self):
        body = (HEAD + TRADES_H
                + _trade('QZOR', '2025-05-01, 09:30:00', 450, 10, -4500, -1)
                + XFER_H
                + _xfer('QZOR', '2025-06-11', -240, -2400, typ='InterDepot')
                + _xfer('QZOR', '2025-06-11', 240, 2400, typ='InterDepot',
                        cur='CAD'))
        rc, _, err, side = _brokerage_cli(
            {'a.csv': body, 'b.csv': body}, '--transfers-out',
            '{TD}/side.json')
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(side['transactions']), 2)


if __name__ == '__main__':
    unittest.main()
