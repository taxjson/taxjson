"""Re-audit-2 fixes: the .tt converter, the schema validator, Canadian
venue suffixes, bare rename targets and partial trade cancellations
(fix list parsers-common-03 + A2-0635). Synthetic data only.

Formerly: test_fix_a2_tt"""
import json
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestBareRenameTargets(unittest.TestCase):
    """A2-0304: a ticker.map GLOBAL or a security override that renames
    a listed symbol to a bare one is an ATTENTION line.

    Formerly: test_fix_a2_tt.TestBareRenameTargets"""

    def test_rule_detection(self):
        from taxjson.lib.ticker_map_rules import bare_rename_target, bare_target_warnings
        self.assertTrue(bare_rename_target('RY.TO', 'RY'))
        self.assertFalse(bare_rename_target('RY.TO', 'RY.US'))
        self.assertFalse(bare_rename_target('ETH2', 'ETH'))   # crypto
        self.assertFalse(bare_rename_target(
            'ABC250718C00045000.US', 'ABC'))
        self.assertEqual(len(bare_target_warnings(['RY.TO'],
                                                  {'RY.TO': 'RY'})), 1)
        self.assertEqual(bare_target_warnings(['AB.TO'],
                                              {'RY.TO': 'RY'}), [])

    def test_merge2_prints_attention(self):
        import subprocess
        import sys
        import os
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "b.json"
            book.write_text(json.dumps({"transactions": [{
                "action": "DIVIDEND", "date": "2025-03-01",
                "symbol": "RY.TO", "currency": "CAD",
                "net_amount": 148.0, "quantity": 100}]}))
            m = Path(d) / "ticker.map"
            m.write_text("GLOBAL RY.TO RY\n")
            env = dict(os.environ, PYTHONPATH=str(
                _paths.REPO / "src"))
            r = subprocess.run([sys.executable, "-m",
                                "taxjson.bin.taxjson_merge2", str(book),
                                "--map", str(m)],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("warning: ATTENTION: ticker.map: RY.TO -> RY",
                          r.stderr)

    def test_security_override_to_bare_symbol_warns(self):
        import subprocess
        import sys
        import os
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "qt.csv"
            csv.write_text(
                'Transaction Date,Settlement Date,Action,Symbol,'
                'Description,Quantity,Price,Gross Amount,Commission,'
                'Net Amount,Currency,Account #,Activity Type,Account Type\n'
                '2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,RY,'
                'ROYAL BANK OF CANADA,10,150.00,-1500.00,0,-1500.00,CAD,'
                '55500001,Trades,Individual\n')  # pii-ok
            ovr = Path(d) / "ticker.map"
            ovr.write_text("EXTRACT ROYAL BANK | CAD | RY\n")
            env = dict(os.environ, PYTHONPATH=str(
                _paths.REPO / "src"))
            r = subprocess.run([sys.executable, "-m",
                                "taxjson.bin.taxjson_brokerage",
                                "--brokerage", "questrade",
                                "--security-overrides", str(ovr), str(csv)],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("warning: ATTENTION: a ticker.map EXTRACT line",
                          r.stderr)


if __name__ == '__main__':
    unittest.main()
