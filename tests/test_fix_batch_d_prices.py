"""Batch D fixes (price chain, compare, holdings): review findings 46,
57, 61, 66, 69, 84. Synthetic data and made-up tickers only."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from taxjson.lib import compare as CMP
from taxjson.lib import price_chain as PC
from taxjson.lib.positions_check import held_as
from taxjson.lib.positions_reports import (COST_AVERAGE,
                                           PositionsReportError,
                                           read_positions)
from _style import CapturedWidth

# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------- 46: RBC cost labels

def _rbc(label, value):
    return ("Holdings Export as of Jan 5, 2026 at 9:00:28 am ET\n\n"
            f'"Account","Symbol","Name","Quantity","Currency",'
            f'"Last Price","{label}","Total Market Value"\n'
            f'"55500001","ZZR","ZZR CORP","10","CAD","6.00",'  # pii-ok
            f'"{value}","60.00"\n')


class TestRbcCostLabels(unittest.TestCase):
    def read(self, label, value):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.csv"
            p.write_text(_rbc(label, value))
            rep = read_positions(p)
        (row,) = rep.rows
        self.assertEqual(row.cost_kind, COST_AVERAGE)
        return row.cost

    def test_book_cost_per_share_is_per_share(self):
        self.assertAlmostEqual(self.read("Book Cost Per Share", "5.00"), 50.0)

    def test_average_book_cost_is_per_share(self):
        self.assertAlmostEqual(self.read("Average Book Cost", "5.00"), 50.0)

    def test_book_cost_slash_share_is_per_share(self):
        self.assertAlmostEqual(self.read("Book Cost/Share", "5.00"), 50.0)

    def test_average_cost_is_per_share(self):
        self.assertAlmostEqual(self.read("Average Cost", "5.00"), 50.0)

    def test_total_book_cost_is_a_total(self):
        self.assertAlmostEqual(self.read("Total Book Cost", "50.00"), 50.0)

    def test_book_value_cad_is_a_total(self):
        self.assertAlmostEqual(self.read("Book Value (CAD)", "50.00"), 50.0)

    def test_avg_price_is_per_share(self):
        self.assertAlmostEqual(self.read("Avg Price", "5.00"), 50.0)

    def test_market_price_per_share_is_not_a_cost(self):
        # A market price column is not read as the cost: the total
        # beside it is.
        text = _rbc("Price Per Share", "6.00").replace(
            '"Total Market Value"', '"Total Book Cost"')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.csv"
            p.write_text(text)
            (row,) = read_positions(p).rows
        self.assertAlmostEqual(row.cost, 60.0)

    def test_ambiguous_cost_label_is_refused_naming_it(self):
        for label in ("Cost Basis", "Total Average Cost"):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "h.csv"
                    p.write_text(_rbc(label, "50.00"))
                    with self.assertRaises(PositionsReportError) as cm:
                        read_positions(p)
                self.assertIn(repr(label), str(cm.exception))


# ------------------------------------------- 57 / 69: the price chain

def tier(results):
    def _fetch(remaining):
        return {s: results[s] for s in remaining if s in results}
    return _fetch


class _FakeIB:
    """An ib_insync IB whose snapshot's last price is +inf."""
    def connect(self, *a, **k):
        pass

    def reqMarketDataType(self, *a):
        pass

    def qualifyContracts(self, *cs):
        for c in cs:
            c.conId = 1

    def reqMktData(self, *a):
        return SimpleNamespace(last=float("inf"), close=10.0,
                               marketPrice=lambda: 10.0)

    def sleep(self, *a):
        pass

    def cancelMktData(self, *a):
        pass

    def disconnect(self):
        pass


def _contract(*a, **k):
    return SimpleNamespace(symbol=a[0], conId=0)


_FAKE_IB = SimpleNamespace(IB=_FakeIB, Stock=_contract, Option=_contract)


class TestNonFinitePrice(unittest.TestCase):
    def test_infinite_tier_price_falls_through_and_is_not_cached(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            err = io.StringIO()
            with redirect_stderr(err):
                q = PC.fetch_prices(
                    {"ZZQ.US": "ZZQ", "ZZW.US": "ZZW"}, cache_path=cache,
                    fetchers=[tier({"ZZQ.US": (float("inf"), "ibkr"),
                                    "ZZW.US": (float("nan"), "ibkr")}),
                              tier({"ZZQ.US": (7.0, "yfinance")})])
            self.assertEqual((q["ZZQ.US"].price, q["ZZQ.US"].source),
                             (7.0, "yfinance"))
            self.assertNotIn("ZZW.US", q)
            doc = json.loads(cache.read_text())
        self.assertEqual(doc["ZZQ.US"]["price"], 7.0)
        self.assertNotIn("ZZW.US", doc)
        self.assertIn("not a usable price", err.getvalue())

    def test_ibkr_fetchers_skip_an_infinite_last(self):
        with mock.patch.dict(sys.modules, {"ib_insync": _FAKE_IB}):
            got = PC._ibkr_fetcher(["ZZQ.US"], host="h", port=1,
                                   verbose=False)
            opt = PC._ibkr_option_fetcher(["ZZQ250620C00010000.US"],
                                          host="h", port=1, verbose=False)
        self.assertEqual(got, {"ZZQ.US": 10.0})
        self.assertEqual(opt, {"ZZQ250620C00010000.US": 10.0})


class TestIbkrCacheSpelling(unittest.TestCase):
    def run_chain(self, cache, pairs):
        with mock.patch.object(PC, "_ibkr_fetcher",
                               return_value={"ZZQ.US": 12.0}), \
                mock.patch.object(PC, "_yfinance_fetcher", return_value={}):
            return PC.fetch_prices(pairs, cache_path=cache, offline=False)

    def test_remapped_ibkr_hit_is_cached_under_its_own_spelling(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            q = self.run_chain(cache, {"ZZQ.US": "ZZOTHER"})
            self.assertEqual(q["ZZQ.US"].source, "ibkr")
            rec = json.loads(cache.read_text())["ZZQ.US"]
            self.assertEqual(rec["quote_symbol"], "ZZQ")
            # The next run (no live tier) wants ZZOTHER: a miss, not
            # ZZQ's price served under ZZOTHER's spelling.
            err = io.StringIO()
            with redirect_stderr(err):
                again = PC.fetch_prices({"ZZQ.US": "ZZOTHER"},
                                        cache_path=cache,
                                        fetchers=[tier({})])
        self.assertNotIn("ZZQ.US", again)
        self.assertIn("cached for ZZQ, wanted ZZOTHER", err.getvalue())

    def test_unmapped_ibkr_hit_still_serves_from_the_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            self.run_chain(cache, {"ZZQ.US": "ZZQ"})
            again = PC.fetch_prices({"ZZQ.US": "ZZQ"}, cache_path=cache,
                                    fetchers=[tier({})])
        self.assertEqual(again["ZZQ.US"].price, 12.0)
        self.assertTrue(again["ZZQ.US"].source.startswith("cache"))

    def test_yahoo_hit_keeps_the_remapped_spelling(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            PC.fetch_prices({"ZZQ.US": "ZZOTHER"}, cache_path=cache,
                            fetchers=[tier({"ZZQ.US": (5.0, "yfinance")})])
            rec = json.loads(cache.read_text())["ZZQ.US"]
        self.assertEqual(rec["quote_symbol"], "ZZOTHER")


# ------------------------------------------------ 61: held_as by date

def _buy(day, sym, qty):
    return {"action": "BUYSELL", "date": day, "symbol": sym,
            "quantity": qty}


def _rename(day, old, new, ratio=1.0):
    return {"action": "SPLIT", "date": day, "symbol": old,
            "symbol_new": new, "quantity": ratio}


class TestHeldAs(unittest.TestCase):
    REUSE = [_buy("2025-01-10", "ZZOLD.TO", 10),
             _rename("2025-02-03", "ZZOLD.TO", "ZZNEW.TO"),
             _buy("2025-03-01", "ZZOLD.TO", 5),
             _rename("2025-04-03", "ZZOLD.TO", "ZZOTH.TO")]

    def test_reused_ticker_is_not_followed_across_an_earlier_rename(self):
        self.assertEqual(held_as(self.REUSE, "ZZOTH.TO", "2025-01-31"), 0)
        self.assertEqual(held_as(self.REUSE, "ZZNEW.TO", "2025-01-31"), 10)
        self.assertEqual(held_as(self.REUSE, "ZZOTH.TO", "2025-03-15"), 5)
        self.assertEqual(held_as(self.REUSE, "ZZNEW.TO", "2025-03-15"), 10)

    def test_own_position_and_ratio_and_chain(self):
        rows = [_buy("2025-01-10", "ZZA.TO", 10),
                _rename("2025-02-03", "ZZA.TO", "ZZB.TO", 2.0),
                _rename("2025-03-03", "ZZB.TO", "ZZC.TO")]
        # A dividend under the old ticker before the change: its own.
        self.assertEqual(held_as(rows, "ZZA.TO", "2025-01-31"), 10)
        self.assertEqual(held_as(rows, "ZZB.TO", "2025-01-31"), 20)
        self.assertEqual(held_as(rows, "ZZC.TO", "2025-01-31"), 20)
        self.assertEqual(held_as(rows, "ZZC.TO", "2025-02-15"), 20)
        self.assertEqual(held_as(rows, "ZZA.TO", "2025-02-15"), 0)


# --------------------------------------------------- 66 / 84: compare

_CFG = {"settings": {"year": 2024}, "accounts": {}}


class _Project(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        (self.root / "reports").mkdir()
        (self.root / "work").mkdir()
        (self.root / "reports" / "a.rpt").write_text("line one\n")

    def take_prev(self, figures):
        with mock.patch.object(CMP, "aggregates", return_value=figures):
            return CMP.take(self.root, _CFG, CMP.snap_dir(self.root,
                                                          CMP.PREV),
                            "previous run")


class TestCompareFiguresMoved(_Project):
    def test_moved_figure_counts_when_the_reports_match(self):
        self.take_prev({"taxable": {"realized": 100.0}})
        with mock.patch.object(CMP, "aggregates",
                               return_value={"taxable": {"realized": 150.0}}):
            doc = CMP.compare(self.root, _CFG)
        self.assertTrue(doc["different"])
        self.assertEqual(doc["reports"]["changed"], [])
        lines = CMP.render(doc)
        self.assertIn("the 1 report file(s) match, but 1 account "
                      "figure(s) moved", lines[0])
        self.assertTrue(any("ACCOUNT" in ln and "FIGURE" in ln
                            for ln in lines))
        self.assertTrue(any("+50.00" in ln for ln in lines))

    def test_nothing_moved_is_no_difference(self):
        self.take_prev({"taxable": {"realized": 100.0}})
        with mock.patch.object(CMP, "aggregates",
                               return_value={"taxable": {"realized": 100.0}}):
            doc = CMP.compare(self.root, _CFG)
        self.assertFalse(doc["different"])
        self.assertTrue(CMP.render(doc)[0].startswith("No difference"))


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                 "root reads a file whatever its mode")
class TestCompareUnreadable(_Project):
    def chmod(self, p, mode):
        os.chmod(p, mode)
        self.addCleanup(os.chmod, p, 0o700)

    def test_unreadable_report_file_is_an_error_not_removed(self):
        self.take_prev({})
        self.chmod(self.root / "reports" / "a.rpt", 0)
        with mock.patch.object(CMP, "aggregates", return_value={}):
            with self.assertRaises(CMP.CompareError) as cm:
                CMP.compare(self.root, _CFG)
        self.assertIn("reports/a.rpt cannot be read", str(cm.exception))
        # The run's rotation refuses too, and keeps the old prev.
        with mock.patch.object(CMP, "aggregates", return_value={}):
            with self.assertRaises(CMP.CompareError):
                CMP.rotate_previous(self.root, _CFG)
        self.assertTrue((CMP.snap_dir(self.root, CMP.PREV) / "reports"
                         / "a.rpt").is_file())

    def test_unreadable_reports_folder_is_an_error(self):
        self.chmod(self.root / "reports", 0)
        with self.assertRaises(CMP.CompareError) as cm:
            CMP.report_files(self.root / "reports")
        self.assertIn("reports/ cannot be read", str(cm.exception))
        with mock.patch.object(CMP, "aggregates", return_value={}):
            with self.assertRaises(CMP.CompareError):
                CMP.save(self.root, _CFG, "base")

    def test_missing_reports_folder_is_no_files(self):
        (self.root / "reports" / "a.rpt").unlink()
        (self.root / "reports").rmdir()
        self.assertEqual(CMP.report_files(self.root / "reports"), {})
        self.assertFalse(CMP.rotate_previous(self.root, _CFG))


if __name__ == "__main__":
    unittest.main()
