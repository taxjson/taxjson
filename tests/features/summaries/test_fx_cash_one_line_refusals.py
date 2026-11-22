"""Re-audit-2 error paths (errors lists, crypto / FX caches, crypto-sends,
rates files). Synthetic data only; caches live under temporary paths and
nothing touches the network.

A2-0772 / A2-0773 / A2-1403 / A2-1446 / A2-0464: a damaged entry in
  ~/.crypto_price_cache.json (null, "abc", true, Infinity) or a cache
  that is not a JSON object is a cache miss, never a traceback or a
  price of 1.0 / inf.
A2-0474 / A2-0800 / A2-1446: a damaged block of
  ~/.currency_price_cache.json (_coverage / _boc / _boc_noon of the
  wrong shape) is dropped with a warning naming the cache.
A2-0465 / A2-0467 / A2-0775 / A2-1407: duplicate_lines reads a .tt with
  a BOM like convert-tt does.
A2-1405: a generated crypto_sends.tt re-saved with a BOM is still ours.
A2-1406: a wrong-shape transfer sidecar is one line naming the file.
A2-1404 / A2-1415 / A2-0776 / A2-1449: sends.json as a directory, a
  wrong-type note, a BOM.
A2-0790 / A2-1434 / A2-1411: an unreadable or BOM'd rates file.
A2-1437: convert-currency --rates naming a missing file.
A2-1423: fees-sum with a NaN rate.
A2-1424: fx-cash on a book a futures row refuses.

Formerly: test_fix_a2_errA_c
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO = _paths.REPO


def _py(*args, cwd=None, env=None):
    e = dict(os.environ)
    e["PYTHONPATH"] = str(REPO / "src")
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    return subprocess.run([sys.executable, *args], capture_output=True,
                          text=True, cwd=cwd or REPO, env=e,
                          stdin=subprocess.DEVNULL)


class TestFxCashOneLine(unittest.TestCase):
    """Formerly: test_fix_a2_errA_c.TestFxCashOneLine"""
    def _proj(self, td, rows):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "work").mkdir()
        (root / "work" / "margin_raw.json").write_text(
            json.dumps({"transactions": rows}))
        (root / "work" / "to_base.csv").write_text(
            "2024-11-01 12:00:00 USD CAD 1.3900 boc\n")
        return root

    def _fx(self, root):
        return _py("-m", "taxjson.bin.taxjson_run", "-C", str(root),
                   "fx-cash")

    def test_futures_refusal_is_one_line(self):
        # A2-1424.
        row = {"date": "2024-11-01", "date_settle": "2024-11-01",
               "time": "10:00:00", "symbol": "F:ESZ4", "action": "TRANSFER",
               "quantity": 1, "price": 0.0, "currency": "USD",
               "net_amount": 0.0, "multiplier": 50}
        with tempfile.TemporaryDirectory() as td:
            r = self._fx(self._proj(td, [row]))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("tjs fx-cash: error:", r.stderr)

    def test_unreadable_rates_is_one_line(self):
        # A2-1434.
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td, [])
            (root / "work" / "to_base.csv").write_bytes(b"\xe9\xe9\n")
            r = self._fx(root)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("to_base.csv", r.stderr)


if __name__ == "__main__":
    unittest.main()
