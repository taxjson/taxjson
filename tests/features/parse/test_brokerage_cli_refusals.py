"""Regression tests for the low-round `ibparse` audit findings (IB
statement parser, shared broker helpers, taxjson-brokerage, broker
detection, the transaction schema) and the overnight-session
item. Every fixture is synthetic: invented tickers and ISINs, fake
account ids marked pii-ok.

Formerly: test_fix_l_ibparse"""
import unittest

from _support.ib_statement_rows import _brokerage_cli
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------- detection and taxjson-brokerage
from pathlib import Path as _P
_EX = _paths.EXAMPLES


class TestBrokerageCliRefusals(unittest.TestCase):
    """R1-262 / S059-17 / S026-21 / S027-02 / S059-10.

    Formerly: test_fix_l_ibparse.TestBrokerageCliRefusals"""

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


if __name__ == '__main__':
    unittest.main()
