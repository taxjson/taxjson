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


def _run(tc, root):
    r = _cli(root, "run", "--no-input")
    tc.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
    return r


# A sale of 100 shares bought before the data (missing_history.json), then a
# 500 loss superficial by a rebuy. Without the phantom opening the first
# sale is a short covered by the later buy: +1000 - 100 = 900.
_BOOK_2025 = (
    "BUYSELL 2025-01-10 10:00:00 ZZZ.TO -100 CAD 30.00 3000.00 0.00\n"
    "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 20.00 2000.00 0.00\n"
    "BUYSELL 2025-03-10 10:00:00 ZZZ.TO -100 CAD 15.00 1500.00 0.00\n"
    "BUYSELL 2025-03-20 10:00:00 ZZZ.TO 100 CAD 16.00 1600.00 0.00\n")


@rule(R.CA_ACB_11)
class TestRunAndCarryoverApplyPhantoms(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_phantoms.TestRunAndCarryoverApplyPhantoms"""
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
        # A2-0530: the per-account gains stage gets missing_history.json.
        d = self._gains("margin_gains.json")
        self.assertEqual([(m["date"], m["symbol"])
                          for m in d["manual_reporting_required"]],
                         [("2025-01-10", "ZZZ.TO")])
        self.assertAlmostEqual(d["summary"]["total_gain"], 0.0, places=2)
        txt = (self.root / "reports" / "margin.sum").read_text()
        self.assertRegex(txt, r"TOTAL REALIZED GAIN:\s+0\.00 CAD")

    def test_blended_pass_routes_the_sale_to_manual_reporting(self):
        # A2-0048 / A2-0524: the blended taxable pass (which feeds
        # Schedule 3) gets missing_history.json.
        d = self._gains("margin_gains_wash.json")
        self.assertEqual([(m["date"], m["symbol"])
                          for m in d["manual_reporting_required"]],
                         [("2025-01-10", "ZZZ.TO")])
        txt = (self.root / "reports" / "margin_wash.sum").read_text()
        self.assertRegex(txt, r"TOTAL REALIZED GAIN:\s+0\.00 CAD")
        r = _cli(self.root, "form-export")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("Line 13200 (gain/loss): 0.00",
                      " ".join(r.stdout.split()))
        self.assertIn("MANUAL REPORTING REQUIRED", r.stdout)

    def test_no_phantom_short_is_left_open(self):
        # A2-0524: `list` shows the 100 shares held, not a -100 short.
        r = _cli(self.root, "list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        rows = {x["symbol"]: x for x in json.loads(r.stdout)["rows"]}
        self.assertEqual(rows["ZZZ.TO"]["qty"], 100.0)

    def test_carryover_excludes_the_tainted_sale(self):
        # A2-0503 / A2-0513: the carryover wrapper passes missing_history.json.
        r = _cli(self.root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        row = {x["year"]: x for x in json.loads(r.stdout)["rows"]}[2025]
        self.assertEqual(row["dispositions"], 1)
        self.assertAlmostEqual(row["net_gain"], 0.0, places=2)
        self.assertIn("disposition(s) with an unknown cost", r.stderr)


class TestDistributionsSizedWithPhantomsThroughRun(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_phantoms.TestDistributionsSizedWithPhantomsThroughRun"""
    @rule(R.CA_DIST_01, R.CA_ACB_11)
    def test_run_sizes_the_record_date_with_phantom_openings(self):
        # A2-0180 / A2-0903 (S000-08): 100 shares from before the data
        # (missing_history.json) plus 200 bought, 100 sold: 200 held on the
        # record date, so the distribution is 200 x 0.50 = 100, not 50.
        tt = ("BUYSELL 2025-02-03 10:00:00 XAW.TO -100 CAD 30.00 3000.00 0\n"
              "BUYSELL 2025-03-03 10:00:00 XAW.TO 200 CAD 31.00 6200.00 0\n"
              "BUYSELL 2025-12-30 10:00:00 XAW.TO -200 CAD 32.00 6400.00 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, 2025, tt, settings='tax_date = "settle"\n',
                     phantoms='[{"symbol": "XAW.TO", "account": "margin"}]')
            with (root / "taxjson.toml").open("a") as f:
                f.write('\n[[distributions]]\nsymbol = "XAW.TO"\n'
                        'record_date = 2025-12-29\nper_share = 0.50\n')
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


if __name__ == "__main__":
    unittest.main()
