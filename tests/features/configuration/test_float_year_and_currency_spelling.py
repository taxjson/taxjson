"""Re-audit-2 tests-pins (pins3, group g6b): fixes in taxjson_run.py's
reports, guards, wording gates and settings that held but that no test
failed on when reverted. Each TestCase names the A2 finding and the code
it pins; every test was checked to fail with that code reverted.

All data is synthetic (invented tickers, round amounts).

Formerly: test_fix_a2_pins3_g6b
"""
import json
import os
import subprocess
import sys
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

REPO = _paths.REPO
SRC = REPO / "src"


def _run(root, *args, env=None):
    e = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=300)


def _doc(path, txs, **extra):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"transactions": txs}
    d.update(extra)
    path.write_text(json.dumps(d))
    return path


# ----------------------------------------------- A2-0934, A2-1586, A2-1587
def _t(action, date, sym, qty=0.0, net=0.0, cur="CAD", **kw):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": sym, "quantity": qty,
         "price": abs(net / qty) if qty else 0.0, "net_amount": net,
         "currency": cur}
    d.update(kw)
    return d


# ------------------------------------------------------------- A2-0929
class TestFloatYearRefused(unittest.TestCase):
    """S039-06: `year = 2024.0` passed the plausible-range test and the
    summaries printed 'No trades in tax year 2024.0' with exit 0. The
    shared check is lib/config_check.settings_problems (A2-1373), which
    every config reader runs; the summaries (not only `run`) refuse.

    Formerly: test_fix_a2_pins3_g6b.TestFloatYearRefused"""

    def test_float_year_is_refused_by_the_summaries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024.0\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _doc(root / "work" / "margin_raw.json",
                 [_t("BUYSELL", "2024-02-03", "AAA.TO", 10, -100.0)])
            for cmd in ("trades-sum", "divs-sum"):
                with self.subTest(cmd=cmd):
                    r = _run(root, cmd)
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    self.assertIn("year must be an integer tax year, got "
                                  "2024.0", r.stderr)

    def test_settings_problems_names_a_float_year(self):
        from taxjson.lib.config_check import settings_problems
        p = settings_problems({"settings": {"country": "canada",
                                            "year": 2024.0}})
        self.assertIn("[settings] year must be an integer tax year, got "
                      "2024.0", p)


# ------------------------------------------------------------- A2-1609
class TestCurrencySettingsSpelling(unittest.TestCase):
    """S030-20 siblings: source_currencies are upper-cased and stripped
    for every reader (_normalize_settings), and base_currency must be a
    3-letter code (lib/config_check) — 'CA' / 'C$D' get the spelling
    message, not a misleading country-currency one.

    Formerly: test_fix_a2_pins3_g6b.TestCurrencySettingsSpelling"""

    def test_source_currencies_are_canonical(self):
        from taxjson.pipeline import config as pipeline_config
        cfg = {"settings": {"country": "canada", "base_currency": "CAD",
                            "source_currencies": [" usd", "Eur "]}}
        pipeline_config._normalize_settings(cfg)
        self.assertEqual(cfg["settings"]["source_currencies"],
                         ["USD", "EUR"])

    def test_base_currency_must_be_three_letters(self):
        from taxjson.lib.config_check import settings_problems
        for bad in ("CA", "C$D", "CADD"):
            with self.subTest(base=bad):
                p = settings_problems({"settings": {
                    "country": "canada", "base_currency": bad}})
                self.assertEqual(len(p), 1, p)
                self.assertIn("base_currency must be a 3-letter currency "
                              "code", p[0])


if __name__ == "__main__":
    unittest.main()
