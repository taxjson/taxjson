"""Re-audit-2 planning fixes: `tjs harvest` and the price chain.

Offline throughout: prices come from injected fetchers (the price
chain's `fetchers` test hook) or a seeded price cache, never the
network. Synthetic books only.

Formerly: test_fix_a2_planning_harvest
"""

import tempfile
import unittest
from pathlib import Path

from _support.planning_harvest_runs import _iso, _write, _gains, _run, _json_rows


class TestUsShortNeverLongTerm(unittest.TestCase):
    """A2-1176: a stand-alone short sale is short-term (US-HOLD-03) — the
    LT_IN cell never says LT for an open short.

    Formerly: test_fix_a2_planning_harvest.TestUsShortNeverLongTerm"""

    def test_short_shows_st(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "brk_gains_wash.json", _gains([
                {"symbol": "SHT.US", "qty": -10, "total_cost": -1000.0,
                 "position_start_date": _iso(-500)},
                {"symbol": "LNG.US", "qty": 10, "total_cost": 1000.0,
                 "position_start_date": _iso(-500)}]))
            argv = [str(g), "--no-ibkr", "--country", "usa",
                    "--base-currency", "USD"]
            fx = [lambda rem: {s: (90.0, "fake", "USD") for s in rem}]
            rc, out, err = _run(argv, fetchers=fx)
            rc2, jout, _ = _run(argv + ["--json"], fetchers=fx)
        self.assertEqual(rc, 0, err)
        sht = next(ln for ln in out.splitlines() if "SHT.US" in ln)
        lng = next(ln for ln in out.splitlines() if "LNG.US" in ln)
        self.assertNotIn(" LT ", f" {sht} ")
        self.assertIn(" ST ", f" {sht} ")
        self.assertIn(" LT ", f" {lng} ")
        rows = {r["symbol"]: r for r in _json_rows(jout)["rows"]}
        self.assertIsNone(rows["SHT.US"]["days_to_long_term"])
        self.assertEqual(rows["LNG.US"]["days_to_long_term"], 0)


if __name__ == "__main__":
    unittest.main()
