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


class TestHandoffAppliesPhantoms(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_phantoms.TestHandoffAppliesPhantoms"""
    @rule(R.CA_ACB_11)
    def test_prior_year_snapshot_opens_the_phantom_shares(self):
        # A2-0517: handoff rebuilds the closed year's Dec-31 positions
        # with missing_history.json; without it the 2025 books hold 200, not
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
            c = _cli(p25, "close-year", "--yes")
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
