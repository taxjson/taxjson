"""taxjson watch — change detection over the radar (cron-quiet mode)."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.bin.taxjson_watch import (diff_harvest, diff_radar,
                                       flatten_radar, load_state,
                                       save_state)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rec(cat, advisory="advice", clears=None):
    return {"category": cat, "advisory": advisory, "clears_at": clears}


class TestDiffRadar(unittest.TestCase):
    def test_no_changes_is_empty(self):
        cur = {"AAA.TO": _rec("LOCKED", clears="2026-09-01")}
        self.assertEqual(diff_radar(dict(cur), dict(cur)), [])

    def test_new_actionable_reported_new_clear_not(self):
        ch = diff_radar({}, {"AAA.TO": _rec("VIOLATION"),
                             "BBB.TO": _rec("CLEAR")})
        self.assertEqual([c["ticker"] for c in ch], ["AAA.TO"])
        self.assertEqual(ch[0]["kind"], "new")

    def test_transition_to_clear_is_a_cleared_record(self):
        ch = diff_radar({"AAA.TO": _rec("LOCKED", clears="2026-09-01")},
                        {"AAA.TO": _rec("CLEAR")})
        self.assertEqual(ch[0]["kind"], "cleared")
        self.assertIn("safe to sell", ch[0]["line"])

    def test_category_change_reported(self):
        ch = diff_radar({"AAA.TO": _rec("COOLING")},
                        {"AAA.TO": _rec("BLOCKED")})
        self.assertEqual(ch[0]["kind"], "changed")
        self.assertEqual(ch[0]["was"], "COOLING")

    def test_clears_at_move_reported_when_category_same(self):
        ch = diff_radar({"AAA.TO": _rec("LOCKED", clears="2026-09-01")},
                        {"AAA.TO": _rec("LOCKED", clears="2026-09-15")})
        self.assertEqual(ch[0]["kind"], "clears_moved")
        self.assertIn("2026-09-15", ch[0]["line"])

    def test_gone_actionable_reported_gone_clear_not(self):
        ch = diff_radar({"AAA.TO": _rec("RISK"),
                         "BBB.TO": _rec("CLEAR")}, {})
        self.assertEqual([c["ticker"] for c in ch], ["AAA.TO"])
        self.assertEqual(ch[0]["kind"], "gone")

    def test_order_new_before_gone(self):
        ch = diff_radar({"ZZZ.TO": _rec("RISK")},
                        {"AAA.TO": _rec("VIOLATION")})
        self.assertEqual([c["kind"] for c in ch], ["new", "gone"])


class TestDiffHarvest(unittest.TestCase):
    def test_within_threshold_is_quiet(self):
        self.assertIsNone(diff_harvest(500.0, 550.0, 100.0))

    def test_beyond_threshold_reports(self):
        ch = diff_harvest(500.0, 700.0, 100.0)
        self.assertEqual(ch["kind"], "harvest_now")
        self.assertIn("+200.00", ch["line"])

    def test_no_baseline_is_quiet(self):
        self.assertIsNone(diff_harvest(None, 700.0, 100.0))


class TestState(unittest.TestCase):
    def test_roundtrip_and_version_gate(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            save_state(p, {"AAA.TO": _rec("RISK")}, 123.45, "2026-08-20")
            st = load_state(p)
            self.assertEqual(st["radar"]["AAA.TO"]["category"], "RISK")
            self.assertEqual(st["harvest_now"], 123.45)
            # A different schema version is a no-baseline, not a crash.
            doc = json.loads(p.read_text())
            doc["schema_version"] = 999
            p.write_text(json.dumps(doc))
            self.assertIsNone(load_state(p))

    def test_missing_or_garbage_state_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            self.assertIsNone(load_state(p))
            p.write_text("{not json")
            self.assertIsNone(load_state(p))


class TestFlatten(unittest.TestCase):
    def test_first_row_per_ticker_wins(self):
        doc = {"sections": [
            {"category": "LOCKED", "rows": [
                {"ticker": "AAA.TO", "category": "LOCKED",
                 "advisory": "x", "clears_at": "2026-09-01"}]},
            {"category": "CLEAR", "rows": [
                {"ticker": "AAA.TO", "category": "CLEAR",
                 "advisory": "y", "clears_at": None}]}]}
        flat = flatten_radar(doc)
        self.assertEqual(flat["AAA.TO"]["category"], "LOCKED")


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestWatchEndToEnd(unittest.TestCase):
    def _project(self, tmp):
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        root = Path(tmp)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = %d\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n' % date.today().year)
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            _QT_HEADER +
            f"{d(60)} 09:30:00 AM,{d(59)} 12:00:00 AM,Buy,XEI.TO,D,"
            f"100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n")
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr
        return root, d

    def test_baseline_then_quiet_then_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, d = self._project(tmp)
            # 1. Baseline run announces itself.
            r1 = _cli(root, "watch")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertIn("baseline recorded", r1.stdout)
            # 2. Nothing changed: SILENT, exit 0.
            r2 = _cli(root, "watch")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertEqual(r2.stdout, "")
            # 3. A fresh buy makes the position LOCKED (recent-buy
            #    advisory) — the change is reported; --exit-code
            #    makes it exit 1.
            with (root / "inputs" / "margin" / "questrade.csv").open(
                    "a") as f:
                f.write(f"{d(1)} 09:30:00 AM,{d(0)} 12:00:00 AM,Buy,"
                        f"XEI.TO,D,50,9.00,450.00,0.00,-450.00,CAD,1,"
                        f"Trades,Individual\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r3 = _cli(root, "watch", "--exit-code")
            self.assertEqual(r3.returncode, 1, r3.stdout + r3.stderr)
            self.assertIn("XEI.TO", r3.stdout)
            self.assertIn("change(s) since the last run", r3.stdout)
            # 4. And quiet again on the next run.
            r4 = _cli(root, "watch", "--exit-code")
            self.assertEqual(r4.returncode, 0, r4.stderr)
            self.assertEqual(r4.stdout, "")

    def test_json_mode_lists_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, d = self._project(tmp)
            r1 = _cli(root, "watch", "--json")
            doc1 = json.loads(r1.stdout)
            self.assertTrue(doc1["baseline"])
            r2 = _cli(root, "watch", "--json")
            doc2 = json.loads(r2.stdout)
            self.assertFalse(doc2["baseline"])
            self.assertEqual(doc2["changes"], [])




class TestVerifiedBugFixes(unittest.TestCase):
    """Fixes from the post-build adversarial pass."""

    def test_harvest_baseline_survives_a_run_without_harvest(self):
        # Alternating `watch` / `watch --harvest` cron lines must not
        # re-baseline the harvest total every plain run.
        from taxjson.bin.taxjson_watch import load_state, save_state
        with tempfile.TemporaryDirectory() as tmp:
            root, _d = TestWatchEndToEnd._project(
                TestWatchEndToEnd(), tmp)
            r1 = _cli(root, "watch")           # baseline (no harvest)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            state_path = root / "work" / ".watch_state.json"
            st = load_state(state_path)
            save_state(state_path, st["radar"], 500.0, st["as_of"])
            r2 = _cli(root, "watch")           # plain run, no --harvest
            self.assertEqual(r2.returncode, 0, r2.stderr)
            st2 = load_state(state_path)
        self.assertEqual(st2.get("harvest_now"), 500.0,
                         "a run without --harvest erased the "
                         "harvest baseline")

    def test_empty_category_rows_produce_no_transitions(self):
        # --all radar rows with an EMPTY category (e.g. fully exited
        # at a gain) are not states: CLEAR -> "" printed a garbled
        # line, "" -> CLEAR announced 'safe to sell at a loss'.
        ch = diff_radar({"AAA.TO": _rec("CLEAR")},
                        {"AAA.TO": _rec("")})
        self.assertEqual(ch, [])
        ch = diff_radar({"AAA.TO": _rec("")},
                        {"AAA.TO": _rec("CLEAR")})
        self.assertEqual(ch, [])
        # But actionable -> "" still reports the advisory as gone.
        ch = diff_radar({"AAA.TO": _rec("RISK")},
                        {"AAA.TO": _rec("")})
        self.assertEqual(ch[0]["kind"], "gone")




class TestStatePathOption(unittest.TestCase):
    def test_separate_cadences_keep_separate_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _d = TestWatchEndToEnd._project(
                TestWatchEndToEnd(), tmp)
            # Default cadence baselines and goes quiet.
            r1 = _cli(root, "watch")
            self.assertIn("baseline recorded", r1.stdout)
            r2 = _cli(root, "watch")
            self.assertEqual(r2.stdout, "")
            # A second cadence with its own state starts from ITS OWN
            # baseline instead of inheriting the default one.
            r3 = _cli(root, "watch", "--state", "work/.watch_weekly.json")
            self.assertIn("baseline recorded", r3.stdout)
            r4 = _cli(root, "watch", "--state", "work/.watch_weekly.json")
            self.assertEqual(r4.stdout, "")
            self.assertTrue((root / "work" / ".watch_weekly.json")
                            .exists())
            self.assertTrue((root / "work" / ".watch_state.json")
                            .exists())


if __name__ == "__main__":
    unittest.main()
