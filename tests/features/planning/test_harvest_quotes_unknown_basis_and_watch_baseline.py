"""Fix round 7 (wave B), 2026-10-29: harvest, watch's harvest baseline,
tips, div-estimate and fees (backlog TJ-0908, TJ-0709, TJ-0710,
TJ-0911, TJ-0912, TJ-0913, TJ-0914, TJ-0900, TJ-0897, TJ-0776).

Synthetic data only; offline throughout (injected fetchers or a cache).

Formerly: test_fix_1029_r7b_harvest
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_harvest import main as harvest_main
from _support.planning_watch_harvest import _watch_project, _watch_harvest, _harvest_doc


def _run_harvest(argv, prices):
    """harvest_main with a fake live tier keyed by the QUOTE spelling."""
    def _fetch(remaining):
        return {s: (prices[y], "fake") for s, y in remaining.items()
                if y in prices}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=[_fetch])
    return rc, out.getvalue(), err.getvalue()


def _gains(root: Path, inventory, name="margin_gains_wash.json"):
    p = root / name
    p.write_text(json.dumps({"summary": {"year": 2026}, "transactions": [],
                             "inventory": inventory}))
    return p


class TestQuoteRatio(unittest.TestCase):
    """TJ-0709: a QUOTE line's QTY_RATIO converts the held quantity into
    the quoted ticker's units — 100 ZZOLD.TO quoted as ZZNEW.TO at 0.25
    is 25 ZZNEW.TO shares.

    Formerly: test_fix_1029_r7b_harvest.TestQuoteRatio"""

    def test_ratio_applied_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZOLD.TO", "qty": 100,
                               "total_cost": 500.0,
                               "last_acq_date": "2026-01-02"}])
            (root / "ticker.map").write_text(
                "QUOTE ZZOLD.TO ZZNEW.TO 0.25\n")
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                {"ZZNEW.TO": 20.0})
        self.assertEqual(rc, 0, err)
        row = json.loads(out)["rows"][0]
        self.assertAlmostEqual(row["value"], 500.0)
        self.assertAlmostEqual(row["unrealized"], 0.0)
        self.assertEqual(row["verdict"], "FLAT")
        self.assertAlmostEqual(row["quote_ratio"], 0.25)

    def test_ratio_in_breakeven_and_price_per_held_share(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZOLD.TO", "qty": 100,
                               "total_cost": 600.0,
                               "last_acq_date": "2026-01-02"}])
            (root / "ticker.map").write_text(
                "QUOTE ZZOLD.TO ZZNEW.TO 0.25\n")
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                {"ZZNEW.TO": 20.0})
        self.assertEqual(rc, 0, err)
        row = json.loads(out)["rows"][0]
        self.assertAlmostEqual(row["unrealized"], -100.0)
        # PRICE per BOOK share (beside COST/SH): 20 x 0.25.
        self.assertAlmostEqual(row["price_base"], 5.0)
        # EXIT@ in the quoted listing's units: 600 / 25 x 1.02.
        self.assertAlmostEqual(row["breakeven_exit_native"], 24.48)


class TestExplicitTickerMap(unittest.TestCase):
    """TJ-0911: --ticker-map's QUOTE (and CRYPTO) lines pick the quote
    identity, not a ticker.map found beside the inputs.

    Formerly: test_fix_1029_r7b_harvest.TestExplicitTickerMap"""

    def test_explicit_map_quote_used(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 150.0,
                               "last_acq_date": "2026-01-02"}])
            custom = root / "custom_rules.map"
            custom.write_text("QUOTE ZZSYN.TO ZZALT.TO\n")
            prices = {"ZZSYN.TO": 10.0, "ZZALT.TO": 20.0}
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--ticker-map", str(custom)], prices)
        self.assertEqual(rc, 0, err)
        row = json.loads(out)["rows"][0]
        self.assertAlmostEqual(row["value"], 200.0)
        self.assertEqual(row["verdict"], "GAIN")

    def test_explicit_map_wins_over_the_found_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 150.0,
                               "last_acq_date": "2026-01-02"}])
            (root / "ticker.map").write_text("QUOTE ZZSYN.TO ZZOTH.TO\n")
            custom = root / "custom_rules.map"
            custom.write_text("QUOTE ZZSYN.TO ZZALT.TO\n")
            prices = {"ZZOTH.TO": 1.0, "ZZALT.TO": 20.0}
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--ticker-map", str(custom)], prices)
        self.assertEqual(rc, 0, err)
        self.assertAlmostEqual(json.loads(out)["rows"][0]["value"], 200.0)

    def test_explicit_map_unpriced_alt_is_omitted_not_old_quote(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 150.0,
                               "last_acq_date": "2026-01-02"}])
            custom = root / "custom_rules.map"
            custom.write_text("QUOTE ZZSYN.TO ZZALT.TO\n")
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--ticker-map", str(custom)], {"ZZSYN.TO": 10.0})
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["rows"], [])
        self.assertEqual(doc["omitted"][0]["symbol"], "ZZSYN.TO")


class TestUnknownBasis(unittest.TestCase):
    """TJ-0710: a holding whose basis is partly unknown has no definite
    unrealized result: it is omitted from the quantified rows with the
    reason, never a GAIN on the known fraction's cost.

    Formerly: test_fix_1029_r7b_harvest.TestUnknownBasis"""

    def test_unknown_cost_omitted_with_reason(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [
                {"symbol": "ZZSYN.TO", "qty": 101, "total_cost": 5.0,
                 "unknown_cost": True, "last_acq_date": "2026-01-02"},
                {"symbol": "ZZOK.TO", "qty": 10, "total_cost": 100.0,
                 "last_acq_date": "2026-01-02"}])
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                {"ZZSYN.TO": 5.0, "ZZOK.TO": 9.0})
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual([r["symbol"] for r in doc["rows"]], ["ZZOK.TO"])
        self.assertEqual(doc["omitted"][0]["symbol"], "ZZSYN.TO")
        self.assertIn("unknown", doc["omitted"][0]["reason"])
        self.assertAlmostEqual(doc["totals"]["unrealized"], -10.0)
        self.assertIn("ZZSYN.TO", err)

    def test_unknown_cost_text_names_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [
                {"symbol": "ZZSYN.TO", "qty": 101, "total_cost": 5.0,
                 "unknown_cost": True, "last_acq_date": "2026-01-02"}])
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada"],
                {"ZZSYN.TO": 5.0})
        self.assertEqual(rc, 0, err)
        self.assertNotIn("GAIN", out)
        self.assertIn("unknown", err)


class TestWatchHarvestCoverage(unittest.TestCase):
    """TJ-0912: watch compares harvest totals only when they leave out
    the same positions. A run that leaves out a position the baseline
    valued (its FX rate missing) keeps the baseline and says so.

    Formerly: test_fix_1029_r7b_harvest.TestWatchHarvestCoverage"""

    _project = _watch_project
    _watch = staticmethod(_watch_harvest)
    _hdoc = staticmethod(_harvest_doc)

    def test_left_out_and_coverage(self):
        from taxjson.bin.taxjson_watch import (harvest_coverage,
                                               harvest_left_out)
        left = harvest_left_out({"omitted": [
            {"account": "margin", "symbol": "ZZB.TO",
             "reason": "no USD->CAD rate"}]})
        self.assertEqual(left, [{"key": "margin|ZZB.TO",
                                 "shown": "ZZB.TO (no USD->CAD rate)"}])
        self.assertEqual(harvest_coverage([], ["margin|ZZB.TO"]), "worse")
        self.assertEqual(harvest_coverage(["margin|ZZB.TO"], []), "better")
        self.assertEqual(harvest_coverage(["a|X"], ["a|X"]), "same")
        self.assertEqual(harvest_coverage(None, ["a|X"]), "same")

    def test_partial_total_keeps_the_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            doc, _ = self._watch(root, self._hdoc(200.0))
            self.assertTrue(doc["baseline"])
            doc, err = self._watch(root, self._hdoc(100.0, ["ZZB.TO"]))
            self.assertEqual(doc["changes"], [])
            self.assertEqual(doc["incomplete"],
                             ["harvest: ZZB.TO (no USD->CAD rate)"])
            self.assertIn("not compared", err)
            state = json.loads(
                (root / "work" / ".watch_state.json").read_text())
            self.assertEqual(state["harvest_now"], 200.0)
            # The rate is back: same coverage, same total — no change.
            doc, err = self._watch(root, self._hdoc(200.0))
            self.assertEqual(doc["changes"], [])
            self.assertEqual(doc["incomplete"], [])
            # A real move with the same coverage is still reported.
            doc, _ = self._watch(root, self._hdoc(500.0))
            self.assertEqual([c["kind"] for c in doc["changes"]],
                             ["harvest_now"])

    def test_fuller_total_becomes_the_baseline_uncompared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            self._watch(root, self._hdoc(100.0, ["ZZB.TO"]))
            doc, _ = self._watch(root, self._hdoc(400.0))
            self.assertEqual(doc["changes"], [])
            state = json.loads(
                (root / "work" / ".watch_state.json").read_text())
            self.assertEqual(state["harvest_now"], 400.0)
            self.assertEqual(state["harvest_omitted"], [])



if __name__ == "__main__":
    unittest.main()
