"""Low-round engine pins (fixl/engine-tests): end-to-end assertions on
engine output fields and boundaries that a mutant could change with
every other test still passing. Each class names the audit finding.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_engine_pins
"""
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import GainsRequest, run_gains
import _paths

REPO_ROOT = _paths.REPO


def T(action, date, sym, qty, net, *, time='10:00:00', settle=None,
      cur='CAD', acct='a', price=None, **kw):
    """One synthetic row; `net` is the fee-inclusive money magnitude."""
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action=action, date=date, time=time,
                          date_settle=settle or date, symbol=sym,
                          quantity=float(qty), price=float(price),
                          net_amount=float(net), currency=cur,
                          account=acct, **kw)


def U(*a, **kw):
    return T(*a, cur='USD', **kw)


def run(book, year, country='canada', sheltered=(), **kw):
    """pipeline.run_gains (what `tjs run` uses) on a taxable book."""
    req = GainsRequest(country=country, year=year, taxable=True, **kw)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)),
                        copy.deepcopy(list(sheltered)), [], req=req)
    return res


class TestInventoryCurrencyReachesHoldingsToml(unittest.TestCase):
    """S070-06: the engine inventory's currency drives holdings.toml's
    currency and base_* fields.

    Formerly: test_fix_l_engine_pins.TestInventoryCurrencyReachesHoldingsToml"""

    def test_usd_holding_with_cad_base(self):
        import tomllib
        native = run([U('BUYSELL', '2025-01-06', 'XYZ.US', 100, 1000)], 2025)
        base = run([T('BUYSELL', '2025-01-06', 'XYZ.US', 100, 1350)], 2025)
        self.assertEqual(native['inventory'][0]['currency'], 'USD')
        self.assertEqual(base['inventory'][0]['currency'], 'CAD')
        with tempfile.TemporaryDirectory() as tmp:
            n = Path(tmp) / 'native.json'
            b = Path(tmp) / 'base.json'
            n.write_text(json.dumps({'inventory': native['inventory']}))
            b.write_text(json.dumps({'inventory': base['inventory']}))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                 '--holdings-toml', '--base-gains', str(b), str(n)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        h = tomllib.loads(r.stdout)['holding'][0]
        self.assertEqual(h['currency'], 'USD')
        self.assertEqual(h['base_currency'], 'CAD')
        self.assertEqual(h['base_total_cost'], 1350.0)


if __name__ == '__main__':
    unittest.main()
