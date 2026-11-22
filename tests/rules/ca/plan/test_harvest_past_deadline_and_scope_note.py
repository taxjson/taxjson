"""Re-audit-2 planning fixes: `tjs harvest` and the price chain.

Offline throughout: prices come from injected fetchers (the price
chain's `fetchers` test hook) or a seeded price cache, never the
network. Synthetic books only.

Formerly: test_fix_a2_planning_harvest
"""

import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_harvest import _recovery_schedule
from _support.planning_harvest_runs import TODAY, _iso, _write, _gains, _run, _json_rows


class TestPastDeadlineViolation(unittest.TestCase):
    """A2-0365: a VIOLATION whose rescue deadline has passed is not
    'harvestable now' — a registered buy still inside the 30 days
    before a sale today denies it; it is claimable once that buy ages
    out (the radar's own LOCKED rule: last add + 31 days).

    Formerly: test_fix_a2_planning_harvest.TestPastDeadlineViolation"""

    def _row(self, clears, sh_add=None, sh_known=True):
        return {"verdict": "LOSS", "unrealized": -900.0,
                "radar": {"category": "VIOLATION", "clears_at": clears},
                "sheltered_last_add": sh_add,
                "sheltered_known": sh_known}

    def test_deadline_passed_with_recent_sheltered_buy_waits(self):
        # Registered buy 29 days ago: clear 2 days from now.
        s = _recovery_schedule([self._row(_iso(-1), _iso(-29))])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["7d"], 900.0)

    def test_deadline_passed_buy_aged_out_is_now(self):
        s = _recovery_schedule([self._row(_iso(-5), _iso(-40))])
        self.assertEqual(s["now"], 900.0)

    def test_deadline_passed_unknown_sheltered_side_is_no_clear(self):
        s = _recovery_schedule([self._row(_iso(-1), None, sh_known=False)])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["no_clear"], 900.0)

    def test_open_deadline_still_now(self):
        # Before the deadline a full exit rescues it: claimable now.
        s = _recovery_schedule([self._row(_iso(3), _iso(-10))])
        self.assertEqual(s["now"], 900.0)

    def test_cli_footer_not_now(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _write(root / "margin_gains_wash.json", _gains([
                {"symbol": "XYZ.TO", "qty": 100, "total_cost": 2000.0,
                 "position_start_date": _iso(-120),
                 "last_acq_date": _iso(-120)}]))
            sh = _write(root / "tfsa_gains_wash.json", _gains([
                {"symbol": "XYZ.TO", "qty": 100, "total_cost": 1100.0,
                 "position_start_date": _iso(-29),
                 "last_acq_date": _iso(-29),
                 "last_acq_settle": _iso(-29)}]))
            radar = _write(root / "wash_radar_margin.json", {"sections": [
                {"title": "act", "rows": [{
                    "ticker": "XYZ.TO", "category": "VIOLATION",
                    "advisory": "VIOLATION: Sell ...",
                    "clears_at": _iso(-1)}]}]})
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--radar", str(radar), "--sheltered", str(sh)],
                fetchers=[lambda rem: {s: (11.0, "fake", "CAD")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        h = _json_rows(out)["totals"]["harvestable"]
        self.assertEqual(h["now"], 0.0)
        self.assertEqual(h["7d"], 900.0)


class TestHarvestJsonScopeNote(unittest.TestCase):
    """A2-1171: harvest --json carries the CA-PLAN-04 / US-PLAN-04 scope
    disclosure, like the radar, buy-check and sell-check JSON.

    Formerly: test_fix_a2_planning_harvest.TestHarvestJsonScopeNote"""

    def test_scope_note_in_json(self):
        from taxjson.lib.wash_scope import scope_note
        for country in ("canada", "usa"):
            with tempfile.TemporaryDirectory() as td:
                g = _write(Path(td) / "margin_gains_wash.json", _gains([
                    {"symbol": "XYZ.US", "qty": 10, "total_cost": 100.0,
                     "position_start_date": _iso(-200)}]))
                (Path(td) / "to_base.csv").write_text(
                    f"{TODAY.isoformat()} 12:00:00 USD CAD 1.25\n")
                rc, out, err = _run(
                    [str(g), "--no-ibkr", "--country", country, "--json",
                     "--base-currency",
                     "USD" if country == "usa" else "CAD"],
                    fetchers=[lambda rem: {s: (9.0, "fake", "USD")
                                           for s in rem}])
            self.assertEqual(rc, 0, err)
            self.assertEqual(_json_rows(out)["scope_note"],
                             scope_note(country))

    def test_scope_note_when_no_positions(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", _gains([]))
            rc, out, err = _run([str(g), "--no-ibkr", "--country",
                                 "canada", "--json"],
                                fetchers=[lambda rem: {}])
        self.assertEqual(rc, 0, err)
        self.assertIn("scope_note", _json_rows(out))


if __name__ == "__main__":
    unittest.main()
