"""Shared test helpers: two IB account ids, a statement period, a buy row and the taxjson-brokerage fixture.

Shared from: test_fix_1029_r7d_ib
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from _support.ib_statement_rows import _trade


_A, _B = 'U1234567', 'U9990002'


def _period(text):
    return f'Statement,Data,Period,"{text}"\n'


_Y2025 = _period('January 1, 2025 - December 31, 2025')


class _Tmp(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)

    def _write(self, text, name='ib.csv'):
        p = self.dir / name
        p.write_text(text)
        return p


class _Brokerage(_Tmp):

    def _run(self, files, *extra):
        paths = [str(self._write(t, n)) for n, t in files.items()]
        r = subprocess.run(
            python_module_argv('taxjson.bin.taxjson_brokerage',
                               ['--brokerage', 'ib', '--country', 'canada',
                                '--account', 'margin', '--account-type',
                                'taxable', '--combined-broker-accounts',
                                '--strict', *extra, *paths]),
            capture_output=True, text=True,
            env=dict(os.environ, TAXJSON_WIDTH='0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)['transactions'], r.stderr


def _buy(acct, when='2025-01-06, 10:00:00', qty=10, comm=-1):
    return _trade('SYN', when, qty, 10, -10 * qty, comm, cur='CAD', acct=acct)
