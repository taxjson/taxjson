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

from taxjson.bin import fill_crypto_prices as F
from taxjson.bin import to_base_curr as T
from taxjson.lib import cli_diag
from taxjson.lib import crypto_sends as cs

REPO = Path(__file__).resolve().parents[1]


def _py(*args, cwd=None, env=None):
    e = dict(os.environ)
    e["PYTHONPATH"] = str(REPO / "src")
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    return subprocess.run([sys.executable, *args], capture_output=True,
                          text=True, cwd=cwd or REPO, env=e,
                          stdin=subprocess.DEVNULL)


class TestCryptoPriceCache(unittest.TestCase):
    BAD = ("null", '"abc"', "true", "Infinity", "[1]")

    def _cache(self, td, text):
        p = Path(td) / "cc.json"
        p.write_text(text)
        return p

    def test_cached_price_accepts_only_finite_positive_numbers(self):
        c = {"a": None, "b": "abc", "c": True, "d": float("inf"),
             "e": [1], "f": 0.0, "g": 12.5, "h": "7.5"}
        for k in "abcdef":
            self.assertIsNone(F.cached_price(c, k), k)
        self.assertEqual(F.cached_price(c, "g"), 12.5)
        self.assertEqual(F.cached_price(c, "h"), 7.5)
        self.assertIsNone(F.cached_price([], "g"))

    def test_non_object_cache_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as td:
            for text in ("[]", "5", '"x"', "﻿{}"):
                p = self._cache(td, text)
                with mock.patch.object(F, "CACHE_FILE", str(p)):
                    self.assertEqual(F.load_cache(), {}, text)

    def test_fill_crypto_treats_a_damaged_entry_as_a_miss(self):
        # A2-0773: null was a TypeError traceback; A2-1403: true booked a
        # price of 1.0 and Infinity a price of inf.
        tx = {"date": "2024-07-02", "time": "10:00:00", "symbol": "ETH",
              "action": "BUYSELL", "quantity": 0.5, "price": 0.0,
              "currency": "USD", "net_amount": 0.0,
              "description": "buy", "account": "c"}
        for bad in self.BAD:
            with tempfile.TemporaryDirectory() as td:
                p = self._cache(td, '{"ETH-2024-07-02": %s}' % bad)
                inp = Path(td) / "in.json"
                inp.write_text(json.dumps({"transactions": [tx]}))
                err, out = io.StringIO(), io.StringIO()
                with mock.patch.object(F, "CACHE_FILE", str(p)), \
                        mock.patch.object(F, "get_crypto_price",
                                          return_value=3000.0) as g, \
                        mock.patch.object(F.time, "sleep"), \
                        mock.patch.object(sys, "argv", ["fc", str(inp)]), \
                        redirect_stderr(err), redirect_stdout(out):
                    try:
                        F.main()
                    except SystemExit as e:
                        self.assertIn(e.code, (0, None), err.getvalue())
                self.assertEqual(g.call_count, 1, bad)
                self.assertIn("not a price", err.getvalue(), bad)
                row = json.loads(out.getvalue())["transactions"][0]
                self.assertEqual(row["price"], 3000.0, bad)
                self.assertEqual(json.loads(p.read_text())
                                 ["ETH-2024-07-02"], 3000.0, bad)

    def test_crypto_sends_lookup_treats_a_damaged_entry_as_a_miss(self):
        # A2-0464: null was a TypeError traceback in crypto-sends.
        with tempfile.TemporaryDirectory() as td:
            for text in [('{"SOL-2024-01-02": %s}' % b) for b in self.BAD] \
                    + ["5", "[]"]:
                p = self._cache(td, text)
                with mock.patch.object(F, "CACHE_FILE", str(p)), \
                        mock.patch("taxjson.lib.offline.offline_enabled",
                                   return_value=True):
                    self.assertEqual(
                        cs.yahoo_usd_price(Path(td))("SOL", "2024-01-02"),
                        (None, "SOL"), text)
            p = self._cache(td, '{"SOL-2024-01-02": 150.0}')
            with mock.patch.object(F, "CACHE_FILE", str(p)):
                self.assertEqual(
                    cs.yahoo_usd_price(Path(td))("SOL", "2024-01-02"),
                    (150.0, "SOL"))


class TestCurrencyCacheShapes(unittest.TestCase):
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
                with mock.patch.object(T, "CACHE_FILE", str(p)):
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
            with mock.patch.object(T, "CACHE_FILE", str(p)):
                rows, errors, _n = T.build_rates(
                    "USD", "CAD", "2024-01-02", "2024-01-03",
                    today="2024-02-01", offline=True)
        self.assertEqual([r[1] for r in rows], ["1.3300", "1.3300"])
        self.assertEqual(len(errors), 2)


class TestDuplicateLinesAndGeneratedTt(unittest.TestCase):
    def _acct(self, td):
        acct = Path(td) / "inputs" / "a"
        acct.mkdir(parents=True)
        return acct

    def test_bom_first_line_is_checked(self):
        # A2-0465 / A2-0467 / A2-0775 / A2-1407.
        e = {"id": "kr-x", "date": "2026-05-04", "time": "22:00:00",
             "symbol": "SOL", "quantity": 1000.0}
        with tempfile.TemporaryDirectory() as td:
            acct = self._acct(td)
            (acct / "hand.tt").write_text(
                "﻿BUYSELL 2026-05-04 22:00:00 SOL -1,000 CAD 1 1 0\n",
                encoding="utf-8")
            hits = cs.duplicate_lines(acct, [e])
        self.assertEqual([h["line"] for h in hits], [1])

    def test_generated_file_with_a_bom_is_still_generated(self):
        # A2-1405.
        with tempfile.TemporaryDirectory() as td:
            tt = self._acct(td) / cs.TT_NAME
            body = (cs.GENERATED_MARK + "\n# kr-20260504T220000-SOL-1: x\n"
                    "BUYSELL 2026-05-04 22:00:00 SOL -1 CAD 1 1 0\n")
            tt.write_text("﻿" + body, encoding="utf-8")
            self.assertEqual(cs.read_tt(tt), body)
            self.assertEqual(cs.tt_ids(tt), {"kr-20260504T220000-SOL-1"})
            self.assertEqual(cs.write_tt(tt, body), "unchanged")
            self.assertEqual(cs.write_tt(tt, body + "\n"), "written")
            self.assertEqual(cs.write_tt(tt, None), "removed")
            # A hand file is still refused.
            tt.write_text("﻿BUYSELL 2026-05-04 22:00:00 SOL -1 CAD "
                          "1 1 0\n")
            with self.assertRaises(ValueError):
                cs.write_tt(tt, body)

    def test_write_failure_is_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            tt = self._acct(td) / cs.TT_NAME
            tt.mkdir()
            with self.assertRaises(ValueError) as cm:
                cs.write_tt(tt, cs.GENERATED_MARK + "\n")
            self.assertIn(str(tt), str(cm.exception))


class TestTransferSidecarShape(unittest.TestCase):
    def test_wrong_shapes_are_one_line(self):
        # A2-1406: [] / ['x'] were AttributeError tracebacks.
        good = {"metadata": {"kind": "transfer_sidecar"},
                "transactions": [{"date": "2024-01-01", "quantity": -1.0,
                                  "symbol": "BTC"}]}
        with tempfile.TemporaryDirectory() as td:
            w = Path(td)
            f = w / "kr_kraken_transfers.json"
            for doc in ([], {"metadata": {"kind": "transfer_sidecar"},
                             "transactions": ["x"]},
                        {"metadata": {"kind": "transfer_sidecar"},
                         "transactions": 5},
                        {"metadata": {"kind": "transfer_sidecar"},
                         "transactions": [{"date": "2024-01-01",
                                           "quantity": "x"}]}):
                f.write_text(json.dumps(doc))
                with self.assertRaises(ValueError, msg=doc) as cm:
                    cs.load_transfer_rows(w, ["kr"])
                self.assertIn(f"could not read {f.name}", str(cm.exception))
            f.write_text(json.dumps(good))
            self.assertEqual(len(cs.load_transfer_rows(w, ["kr"])), 1)


class TestDecisionsFile(unittest.TestCase):
    def test_directory_is_refused_on_read_and_write(self):
        # A2-1404: read as "no decisions"; --set left sends.json.part.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            p.mkdir()
            with self.assertRaises(ValueError) as cm:
                cs.load_decisions(p)
            self.assertIn("not a regular file", str(cm.exception))
            with self.assertRaises(ValueError):
                cs.record_decision(p, "k", "gift")
            self.assertFalse((Path(td) / "sends.json.part").exists())

    def test_unwritable_save_is_one_line_and_leaves_no_part(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            from taxjson.lib import safe_write as _sw
            with mock.patch.object(_sw.os, "open",
                                   side_effect=PermissionError(
                                       13, "Permission denied")):
                # An OSError: one 'cannot write' line, exit 2 (A2-1416).
                with self.assertRaises(OSError) as cm:
                    cs.save_decisions(p, {"sends": {}})
            self.assertIn("cannot write", str(cm.exception))
            self.assertFalse((Path(td) / "sends.json.part").exists())

    def test_bom_is_accepted_and_note_is_checked(self):
        # A2-0776 / A2-1449: a BOM was 'not valid JSON ... delete it'.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            p.write_text("﻿" + json.dumps(
                {"sends": {"k": {"decision": "gift", "note": "hi"}}}),
                encoding="utf-8")
            self.assertEqual(cs.load_decisions(p)["sends"]["k"]["note"],
                             "hi")
            # A2-1415: a wrong-type field is one line naming file and id.
            for rec in ({"decision": "gift", "note": [1]},
                        {"decision": "gift", "price": [1]}):
                p.write_text(json.dumps({"sends": {"k": rec}}))
                with self.assertRaises(ValueError) as cm:
                    cs.load_decisions(p)
                self.assertIn("'k'", str(cm.exception))
                self.assertIn(str(p), str(cm.exception))
            p.write_text("{")
            with self.assertRaises(ValueError) as cm:
                cs.load_decisions(p)
            self.assertNotIn("delete", str(cm.exception))


class TestRatesFiles(unittest.TestCase):
    def test_bom_keeps_the_first_rate(self):
        # A2-1411.
        from taxjson.bin.taxjson_convert_currency import (
            load_exchange_rates, load_rate_sources)
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
            self.assertEqual(cs.load_rates(p)["USD"]["2025-01-02"],
                             (1.44, "boc"))

    def test_unreadable_rates_are_one_line(self):
        # A2-0790 / A2-1434: non-UTF-8 and directory were tracebacks.
        from taxjson.bin.taxjson_convert_currency import load_exchange_rates
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
                    cs.load_rates(p)
                self.assertIn(str(p), str(cm.exception))
            # Absent stays "no rates" (a brand-new project).
            self.assertEqual(load_exchange_rates(Path(td) / "no", "CAD"),
                             {})
            self.assertEqual(cs.load_rates(Path(td) / "no"), {})

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


class TestFxCashOneLine(unittest.TestCase):
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
        self.assertIn("taxjson fx-cash: error:", r.stderr)

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
