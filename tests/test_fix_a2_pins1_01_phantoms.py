"""Re-audit-2 tests-pins-01: phantoms.json reaches every command that
rebuilds the books (CA-ACB-11). Each test fails when its command's
--incomplete-history / phantom wiring is dropped:

- run, per-account gains stage (A2-0530) and blended taxable pass
  (A2-0048, A2-0524): the phantom-backed sale stays in MANUAL REPORTING,
  out of the .sum totals and Schedule 3, and no phantom short is open;
- run, apply-distributions record-date sizing (A2-0180, A2-0903; S000-08);
- carryover (A2-0503, A2-0513);
- sell-check / buy-check class context (A2-0527, A2-0182), watch and
  harvest's live radar (A2-0182);
- option-boundary (A2-0893; S044-09);
- handoff's prior-year snapshot (A2-0517).

Synthetic data only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

_PHANTOM = '[{"symbol": "ZZZ.TO", "account": "margin"}]'


def _cli(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _project(root, year, tt, *, settings="", phantoms=_PHANTOM):
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n'
        f'option_grant_timing_since = 2025\n{settings}'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "m.tt").write_text(tt)
    if phantoms is not None:
        (root / "phantoms.json").write_text(phantoms)


def _run(tc, root):
    r = _cli(root, "run", "--no-input")
    tc.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
    return r


# A sale of 100 shares bought before the data (phantoms.json), then a
# 500 loss superficial by a rebuy. Without the phantom opening the first
# sale is a short covered by the later buy: +1000 - 100 = 900.
_BOOK_2025 = (
    "BUYSELL 2025-01-10 10:00:00 ZZZ.TO -100 CAD 30.00 3000.00 0.00\n"
    "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 20.00 2000.00 0.00\n"
    "BUYSELL 2025-03-10 10:00:00 ZZZ.TO -100 CAD 15.00 1500.00 0.00\n"
    "BUYSELL 2025-03-20 10:00:00 ZZZ.TO 100 CAD 16.00 1600.00 0.00\n")


@rule("CA-ACB-11")
class TestRunAndCarryoverApplyPhantoms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        _project(cls.root, 2025, _BOOK_2025)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _gains(self, name):
        return json.loads((self.root / "work" / name).read_text())

    def test_gains_stage_routes_the_sale_to_manual_reporting(self):
        # A2-0530: the per-account gains stage gets phantoms.json.
        d = self._gains("margin_gains.json")
        self.assertEqual([(m["date"], m["symbol"])
                          for m in d["manual_reporting_required"]],
                         [("2025-01-10", "ZZZ.TO")])
        self.assertAlmostEqual(d["summary"]["total_gain"], 0.0, places=2)
        txt = (self.root / "reports" / "margin.sum").read_text()
        self.assertRegex(txt, r"TOTAL REALIZED GAIN:\s+0\.00 CAD")

    def test_blended_pass_routes_the_sale_to_manual_reporting(self):
        # A2-0048 / A2-0524: the blended taxable pass (which feeds
        # Schedule 3) gets phantoms.json.
        d = self._gains("margin_gains_wash.json")
        self.assertEqual([(m["date"], m["symbol"])
                          for m in d["manual_reporting_required"]],
                         [("2025-01-10", "ZZZ.TO")])
        txt = (self.root / "reports" / "margin_wash.sum").read_text()
        self.assertRegex(txt, r"TOTAL REALIZED GAIN:\s+0\.00 CAD")
        r = _cli(self.root, "form-export")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("Line 13200 (gain/loss): 0.00", r.stdout)
        self.assertIn("MANUAL REPORTING REQUIRED", r.stdout)

    def test_no_phantom_short_is_left_open(self):
        # A2-0524: `list` shows the 100 shares held, not a -100 short.
        r = _cli(self.root, "list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        rows = {x["symbol"]: x for x in json.loads(r.stdout)["rows"]}
        self.assertEqual(rows["ZZZ.TO"]["qty"], 100.0)

    def test_carryover_excludes_the_tainted_sale(self):
        # A2-0503 / A2-0513: the carryover wrapper passes phantoms.json.
        r = _cli(self.root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        row = {x["year"]: x for x in json.loads(r.stdout)["rows"]}[2025]
        self.assertEqual(row["dispositions"], 1)
        self.assertAlmostEqual(row["net_gain"], 0.0, places=2)
        self.assertIn("tainted disposition", r.stderr)


class TestDistributionsSizedWithPhantomsThroughRun(unittest.TestCase):
    @rule("CA-DIST-01", "CA-ACB-11")
    def test_run_sizes_the_record_date_with_phantom_openings(self):
        # A2-0180 / A2-0903 (S000-08): 100 shares from before the data
        # (phantoms.json) plus 200 bought, 100 sold: 200 held on the
        # record date, so the distribution is 200 x 0.50 = 100, not 50.
        tt = ("BUYSELL 2025-02-03 10:00:00 XAW.TO -100 CAD 30.00 3000.00 0\n"
              "BUYSELL 2025-03-03 10:00:00 XAW.TO 200 CAD 31.00 6200.00 0\n"
              "BUYSELL 2025-12-30 10:00:00 XAW.TO -200 CAD 32.00 6400.00 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, 2025, tt, settings='tax_date = "settle"\n',
                     phantoms='[{"symbol": "XAW.TO", "account": "margin"}]')
            (root / "distributions.map").write_text(
                "XAW.TO  2025-12-29  0.50\n")
            _run(self, root)
            base = json.loads((root / "work" / "margin_base.json")
                              .read_text())
            gains = json.loads((root / "work" / "margin_gains.json")
                               .read_text())
        adj = [(t["date"], t["net_amount"]) for t in base["transactions"]
               if t["action"] == "ADJUST"]
        self.assertEqual(adj, [("2025-12-29", 100.0)])
        self.assertAlmostEqual(gains["summary"]["total_gain"], 100.0,
                               places=2)


def _today_book():
    t = date.today()
    d = lambda n: (t + timedelta(days=n)).isoformat()  # noqa: E731
    # With the phantom opening: the 14-day-old 500 loss is superficial
    # (VIOLATION, act by its day 30). Without it the books differ and
    # the radar reads COOLING.
    return t.year, (
        f"BUYSELL {d(-60)} 10:00:00 ZZZ.TO -100 CAD 30.00 3000.00 0.00\n"
        f"BUYSELL {d(-22)} 10:00:00 ZZZ.TO 100 CAD 20.00 2000.00 0.00\n"
        f"BUYSELL {d(-14)} 10:00:00 ZZZ.TO -100 CAD 15.00 1500.00 0.00\n"
        f"BUYSELL {d(-7)} 10:00:00 ZZZ.TO 100 CAD 16.00 1600.00 0.00\n")


@rule("CA-ACB-11")
class TestChecksApplyPhantoms(unittest.TestCase):
    """The live radars of sell-check, buy-check, watch and harvest open
    the phantom position, as `wash-radar` does (S006-09)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        year, tt = _today_book()
        _project(cls.root, year, tt)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _result(self, cmd):
        r = _cli(self.root, cmd, "ZZZ.TO", "--json")
        self.assertIn(r.returncode, (0, 1), r.stderr[-1500:])
        return json.loads(r.stdout)["results"][0]

    def test_sell_check_sees_the_violation(self):
        # A2-0527 / A2-0182: without phantoms.json it said SAFE/COOLING.
        res = self._result("sell-check")
        self.assertEqual(res["verdict"], "ACTION")
        self.assertIn("VIOLATION", res["detail"][0])

    def test_buy_check_sees_the_violation(self):
        res = self._result("buy-check")
        self.assertEqual(res["verdict"], "UNSAFE")
        self.assertIn("VIOLATION", res["detail"][0])

    def test_watch_records_the_violation(self):
        with tempfile.TemporaryDirectory() as st:
            state = Path(st) / "watch.json"
            r = _cli(self.root, "watch", "--json", "--state", str(state))
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            doc = json.loads(state.read_text())
        self.assertEqual(doc["radar"]["ZZZ.TO"]["category"], "VIOLATION")

    def test_harvest_live_radar_sees_the_violation(self):
        # harvest builds its own radar when the run's sidecars are older
        # than the books; that radar must open the phantom position too.
        work = self.root / "work"
        (work / ".price_cache.json").write_text(json.dumps(
            {"ZZZ.TO": {"price": 10.0, "asof": date.today().isoformat()}}))
        sidecar = self.root / "reports" / "wash_radar_margin.json"
        old = sidecar.stat().st_mtime - 3600
        for p in (self.root / "reports").glob("wash_radar_*.json"):
            os.utime(p, (old, old))
        r = _cli(self.root, "harvest", "--no-ibkr", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("live radar", r.stderr)
        rows = {x["symbol"]: x for x in json.loads(r.stdout)["rows"]}
        self.assertEqual(rows["ZZZ.TO"]["radar"]["category"], "VIOLATION")


class TestOptionBoundaryAppliesPhantoms(unittest.TestCase):
    @rule("CA-ACB-11")
    def test_phantom_long_call_sold_to_close_is_not_a_write(self):
        # A2-0893 (S044-09): a long call bought before the data and sold
        # to close: with phantoms.json it is not an open written call.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\noption_premium_timing = "grant"\n'
                'option_grant_timing_since = 2025\n'
                '[accounts.margin]\ntype = "taxable"\n')
            opt = "ZZZ260116C00050000.US"
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [{
                    "action": "BUYSELL", "date": "2025-12-15",
                    "date_settle": "2025-12-16", "time": "10:00:00",
                    "symbol": opt, "quantity": -2, "net_amount": 900.0,
                    "price": 4.5, "currency": "CAD", "account": "margin",
                    "id": "o1"}]}))
            (root / "phantoms.json").write_text(json.dumps(
                [{"symbol": opt, "account": "margin"}]))
            r = _cli(root, "option-boundary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(json.loads(r.stdout)["rows"], [])


class TestHandoffAppliesPhantoms(unittest.TestCase):
    @rule("CA-ACB-11")
    def test_prior_year_snapshot_opens_the_phantom_shares(self):
        # A2-0517: handoff rebuilds the closed year's Dec-31 positions
        # with phantoms.json; without it the 2025 books hold 200, not
        # 300, and handoff reports a false 'position missing' (rc 1).
        tt = ("BUYSELL 2025-01-10 10:00:00 ZZZ.TO -100 CAD 30.00 3000.00 0\n"
              "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 300 CAD 20.00 6000.00 0\n"
              "BUYSELL 2025-04-03 10:00:00 YYY.TO 10 CAD 20.00 200.00 0\n"
              "BUYSELL 2025-05-03 10:00:00 YYY.TO -10 CAD 25.00 250.00 0\n")
        tt26 = "BUYSELL 2026-02-03 10:00:00 ZZZ.TO -50 CAD 25.00 1250.00 0\n"
        with tempfile.TemporaryDirectory() as tmp:
            p25, p26 = Path(tmp) / "p2025", Path(tmp) / "p2026"
            p25.mkdir()
            p26.mkdir()
            _project(p25, 2025, tt)
            _run(self, p25)
            c = _cli(p25, "close-year")
            self.assertEqual(c.returncode, 0, c.stderr[-1500:])
            _project(p26, 2026, tt + tt26,
                     settings=f'prior_year_record = '
                              f'"{p25 / "filed" / "2025.json"}"\n')
            _run(self, p26)
            h = _cli(p26, "handoff", "--json")
        self.assertEqual(h.returncode, 0, h.stdout[-1500:] + h.stderr[-800:])
        doc = json.loads(h.stdout)
        self.assertEqual(doc["problems"], 0)
        self.assertEqual(doc["positions"], [])


if __name__ == "__main__":
    unittest.main()
