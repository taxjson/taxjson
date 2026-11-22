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
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from taxjson.bin import to_base_curr as T
from taxjson.lib import rate_cache
from taxjson.lib import cli_diag
from taxjson.lib import base_rates as BR
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


class TestCurrencyCacheShapes(unittest.TestCase):
    """Formerly: test_fix_a2_errA_c.TestCurrencyCacheShapes"""
    CASES = {
        "coverage list": {"_coverage": [1]},
        "boc list": {"_boc": ["x"]},
        "boc str": {"_boc": "x"},
        "boc pair str": {"_boc": {"USDCAD": "x"}},
        "noon str": {"_boc_noon": "x"},
        "noon obs str": {"_boc_noon": {"USDCAD": {"obs": "x"}}},
        "boc obs str": {"_boc": {"USDCAD": {"obs": "x"}}},
        "coverage entry str": {"_coverage": {"boc:USDCAD": "2017"},
                               "_boc": {"USDCAD": {"obs": {
                                   "2024-01-02": "1.33"}}}},
    }

    def test_damaged_blocks_are_dropped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as td:
            for name, cache in self.CASES.items():
                p = Path(td) / "cur.json"
                p.write_text(json.dumps(cache))
                with mock.patch.object(rate_cache, "CACHE_FILE", str(p)):
                    rows, errors, _n = T.build_rates(
                        "USD", "CAD", "2024-01-01", "2024-01-05",
                        today="2024-02-01", offline=True)
                self.assertEqual(rows, [], name)
                self.assertTrue(errors, name)
                self.assertTrue(all(isinstance(e, T.CacheProblem)
                                    for e in errors), name)
                self.assertIn(str(p), errors[0], name)
                self.assertIn("damaged", errors[0], name)

    def test_good_blocks_survive_a_damaged_neighbour(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cur.json"
            p.write_text(json.dumps({
                "_coverage": {"boc:USDCAD": [["2024-01-01", "2024-01-05"]],
                              "boc:EURCAD": "junk"},
                "_boc": {"USDCAD": {"obs": {"2024-01-02": "1.3300"}},
                         "EURCAD": 7}}))
            with mock.patch.object(rate_cache, "CACHE_FILE", str(p)):
                rows, errors, _n = T.build_rates(
                    "USD", "CAD", "2024-01-02", "2024-01-03",
                    today="2024-02-01", offline=True)
        self.assertEqual([r[1] for r in rows], ["1.3300", "1.3300"])
        self.assertEqual(len(errors), 2)


class TestRatesFiles(unittest.TestCase):
    """Formerly: test_fix_a2_errA_c.TestRatesFiles"""
    def test_bom_keeps_the_first_rate(self):
        # A2-1411.
        from taxjson.lib.exchange_rates import load_exchange_rates
        from taxjson.bin.taxjson_convert_currency import load_rate_sources
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rates.txt"
            p.write_text("﻿2025-01-02 00:00:00 USD CAD 1.4400 boc\n",
                         encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                h = load_exchange_rates(p, "CAD")
            self.assertEqual(str(h["USD"]["2025-01-02"]), "1.4400")
            self.assertEqual(err.getvalue(), "")
            self.assertEqual(load_rate_sources(p, "CAD"),
                             {"USD": {"2025-01-02": "boc"}})
            self.assertEqual(BR.load_rates(p)["USD"]["2025-01-02"],
                             (1.44, "boc"))

    def test_unreadable_rates_are_one_line(self):
        # A2-0790 / A2-1434: non-UTF-8 and directory were tracebacks.
        from taxjson.lib.exchange_rates import load_exchange_rates
        from taxjson.lib.price_chain import load_fx_history
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "to_base.csv"
            bad.write_bytes(b"2024-01-01 12:00:00 USD CAD 1.3\xe9 boc\n")
            d = Path(td) / "dir.csv"
            d.mkdir()
            for p in (bad, d):
                with self.assertRaises(cli_diag.InputReadError) as cm:
                    load_exchange_rates(p, "CAD")
                self.assertIn(str(p), str(cm.exception))
                with self.assertRaises(cli_diag.InputReadError):
                    load_fx_history(p, "CAD")
                with self.assertRaises(ValueError) as cm:
                    BR.load_rates(p)
                self.assertIn(str(p), str(cm.exception))
            # Absent stays "no rates" (a brand-new project).
            self.assertEqual(load_exchange_rates(Path(td) / "no", "CAD"),
                             {})
            self.assertEqual(BR.load_rates(Path(td) / "no"), {})

    def test_convert_currency_refuses_a_missing_rates_file(self):
        # A2-1437: it converted every row at --default-rate, rc 0.
        with tempfile.TemporaryDirectory() as td:
            tx = Path(td) / "tx.json"
            tx.write_text(json.dumps({"transactions": [{
                "date": "2025-01-02", "time": "10:00:00", "symbol": "A.US",
                "action": "BUYSELL", "quantity": 1, "price": 1000.0,
                "currency": "USD", "net_amount": -1000.0}]}))
            r = _py("-m", "taxjson.bin.taxjson_convert_currency", "--to",
                    "CAD", "--rates", str(Path(td) / "nope.txt"),
                    "--default-rate", "1.35", "--country", "canada",
                    str(tx))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no such file: --rates", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_harvest_with_an_unreadable_rates_file(self):
        from taxjson.bin.taxjson_harvest import main as harvest_main
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains_wash.json"
            g.write_text(json.dumps({"summary": {}, "transactions": [],
                                     "inventory": [{
                                         "symbol": "AAA.US", "qty": 1,
                                         "total_cost": 100.0,
                                         "position_start_date":
                                             "2024-01-02"}]}))
            (Path(td) / "to_base.csv").mkdir()
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = harvest_main([str(g), "--no-ibkr", "--country",
                                   "canada"],
                                  fetchers=[lambda rem: {}])
        self.assertEqual(rc, 2)
        self.assertIn("cannot read the rates file", err.getvalue())

    def test_fees_sum_nan_rate_is_one_line(self):
        # A2-1423: a ValueError traceback; audit says it in one line.
        with tempfile.TemporaryDirectory() as td:
            rates = Path(td) / "to_base.csv"
            rates.write_text("2024-01-01 12:00:00 USD CAD nan synth\n")
            book = Path(td) / "m_raw.json"
            book.write_text(json.dumps({"transactions": []}))
            r = _py("-m", "taxjson.bin.taxjson_fees", "--to", "CAD",
                    "--rates", str(rates), str(book))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("taxjson-fees-sum: error: rates file", r.stderr)


if __name__ == "__main__":
    unittest.main()
