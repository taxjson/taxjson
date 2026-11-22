"""Re-audit-2 tests-pins (pins3, group g6b): fixes in taxjson_run.py's
reports, guards, wording gates and settings that held but that no test
failed on when reverted. Each TestCase names the A2 finding and the code
it pins; every test was checked to fail with that code reverted.

All data is synthetic (invented tickers, round amounts).

Formerly: test_fix_a2_pins3_g6b
"""
import json
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _doc(path, txs, **extra):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"transactions": txs}
    d.update(extra)
    path.write_text(json.dumps(d))
    return path


# ------------------------------------------------------------- A2-1565
class TestLeapsWalkCarriesTheSplitRatio(unittest.TestCase):
    """_leaps_contracts carries an option rename-SPLIT (an OCC
    adjustment) WITH its ratio: short 1, renamed 2-for-1, a buy of 2 is
    a buy-to-close, not a LEAPS entry; a long LEAPS renamed 2-for-1 is
    2 contracts open.

    Formerly: test_fix_a2_pins3_g6b.TestLeapsWalkCarriesTheSplitRatio"""

    OLD, NEW = "QZY270618C00045000.US", "QZY1270618C00022500.US"

    def _leaps(self, rows):
        from taxjson.cli import common as cli_common
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _doc(root / "work" / "margin_raw.json", rows)
            return cli_common._leaps_contracts(root, "margin", "leaps")

    def _r(self, day, act, sym, q, new=""):
        return {"date": day, "date_settle": day, "time": "10:00:00",
                "action": act, "symbol": sym, "symbol_new": new,
                "quantity": q, "account": "margin", "net_amount": 0.0,
                "currency": "USD"}

    def test_buy_to_close_after_a_ratio_rename_is_not_a_leaps(self):
        self.assertEqual(self._leaps([
            self._r("2025-01-06", "BUYSELL", self.OLD, -1),
            self._r("2025-03-03", "SPLIT", self.OLD, 2.0, self.NEW),
            self._r("2025-04-01", "BUYSELL", self.NEW, 2)]), {})

    def test_renamed_leaps_open_quantity_scales(self):
        got = self._leaps([
            self._r("2025-01-06", "BUYSELL", self.OLD, 1),
            self._r("2025-03-03", "SPLIT", self.OLD, 2.0, self.NEW)])
        self.assertEqual(got.get(self.NEW), 2.0)


if __name__ == "__main__":
    unittest.main()
