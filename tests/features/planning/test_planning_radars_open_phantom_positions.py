"""Re-audit-2 tests-pins-01: missing history (.tt OPENING cost=unknown
lines; missing_history.json before v0.27.0) reaches every command that
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

Formerly: test_fix_a2_pins1_01_phantoms
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
from taxjson.rules import ids as R
import _paths

REPO_ROOT = _paths.REPO

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
        # (the .tt OPENING cost=unknown lines the entries convert to)
        from taxjson.lib.tt_format import parse_tt_line
        from _mh import tt_lines, write_lines
        rows = [r for r in (parse_tt_line(ln, "margin")
                            for ln in tt.splitlines()) if r]
        write_lines(root / "inputs", tt_lines(
            rows, json.loads(phantoms), until=f"{year}-12-31"))


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


@rule(R.CA_ACB_11)
class TestChecksApplyPhantoms(unittest.TestCase):
    """The live radars of sell-check, buy-check, watch and harvest open
    the phantom position, as `wash-radar` does (S006-09).

    Formerly: test_fix_a2_pins1_01_phantoms.TestChecksApplyPhantoms"""

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
        # A2-0527 / A2-0182: without missing_history.json it said SAFE/COOLING.
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
            {"ZZZ.TO": {"price": 10.0, "asof": date.today().isoformat(),
                        "quote_symbol": "ZZZ.TO"}}))
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
    """Formerly: test_fix_a2_pins1_01_phantoms.TestOptionBoundaryAppliesPhantoms"""
    @rule(R.CA_ACB_11)
    def test_phantom_long_call_sold_to_close_is_not_a_write(self):
        # A2-0893 (S044-09): a long call bought before the data and sold
        # to close: with missing_history.json it is not an open written call.
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
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                f"OPENING 2025-12-14 {opt} 2 cost=unknown\n")
            r = _cli(root, "option-boundary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(json.loads(r.stdout)["rows"], [])


if __name__ == "__main__":
    unittest.main()
