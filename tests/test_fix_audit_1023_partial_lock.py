"""Workflow audit 2026-10-23, item 7: a lock taken before the year ended
(`close-year --force`) froze the inputs, so the rest of the year never
reached the books, every check said OK, and re-closing after filing (as
the partial-lock note advises) locked the partial figures again.

- A partial lock hashes the inputs and copies nothing: the year keeps
  reading the shared exports, a later trade shows as drift, and a
  re-close locks it.
- A partial lock an earlier release froze is thawed by the re-close
  (taxjson.toml names the shared folders again, the unchanged copies go,
  the lock becomes a partial snapshot); the close after the year ended
  then freezes the whole year. A copy edited since is never thrown away.

Synthetic data only.
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_fix_multi_year import multi, tjs  # noqa: E402

_SHARED = 'inputs_dir = "../inputs"\ntobase_map = "../tobase.map"\n'
_LATER = ("BUYSELL {y}-{m}-02 10:00:00 ZZQF.TO 10 CAD 10.00 100.00 0\n"
          "BUYSELL {y}-{m}-05 10:00:00 ZZQF.TO -10 CAD 60.00 600.00 0\n")


def _project(year: int, extra: str = "") -> Path:
    """One year folder sharing inputs/ and tobase.map."""
    top = multi("canada", years=(year,))
    (top / "tobase.map").write_text("# synthetic shared tobase.map\n")
    p = top / str(year) / "taxjson.toml"
    p.write_text(p.read_text().replace('inputs_dir = "../inputs"\n',
                                       _SHARED, 1))
    if extra:
        with open(top / "inputs" / "margin" / "trades.tt", "a") as f:
            f.write(extra)
    return top


def _ok(tc, r):
    tc.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


def _lock(folder: Path, year: int) -> dict:
    return json.loads((folder / "filed" / f"{year}.json").read_text())


def _settings(folder: Path) -> dict:
    from taxjson.lib.tomlcompat import tomllib
    return tomllib.loads((folder / "taxjson.toml").read_text()).get(
        "settings") or {}


def _realized(folder: Path) -> float:
    doc = json.loads(_ok_raw(tjs("-C", str(folder), "sum", "--json")))
    return sum(a["gain"] for a in doc["filing"]["accounts"])


def _ok_raw(r):
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout


class TestPartialLockIsNotFrozen(unittest.TestCase):
    """The year has not ended: the lock hashes, never copies."""

    def setUp(self):
        today = date.today()
        if today < date(today.year, 3, 1):
            self.skipTest("needs trades earlier this year")
        self.y = today.year
        self.top = _project(self.y, _LATER.format(y=self.y, m="01"))
        self.d = self.top / str(self.y)
        _ok(self, tjs("-C", str(self.d), "run", "--no-input"))

    def test_later_trades_show_as_drift_and_reach_the_reclose(self):
        r = _ok(self, tjs("-C", str(self.d), "close-year", "--yes",
                          "--force"))
        self.assertIn("has not ended", r.stdout + r.stderr)
        self.assertIn(f"nothing — {self.y} has not ended", r.stdout)
        # Nothing copied, taxjson.toml untouched.
        self.assertFalse((self.d / "inputs").exists())
        self.assertFalse((self.d / "tobase.map").exists())
        st = _settings(self.d)
        self.assertEqual(st.get("inputs_dir"), "../inputs")
        self.assertEqual(st.get("tobase_map"), "../tobase.map")
        fz = _lock(self.d, self.y)["frozen"]
        self.assertTrue(fz["partial"])
        self.assertEqual(fz["copied"], 0)
        self.assertIn("inputs/margin/trades.tt", fz["inputs"])
        locked = _lock(self.d, self.y)["totals"]["realized"]
        # Not a frozen year anywhere.
        r = tjs("years", "--details", cwd=self.top)
        self.assertNotIn("frozen copy", r.stdout + r.stderr)

        with open(self.top / "inputs" / "margin" / "trades.tt", "a") as f:
            f.write(_LATER.format(y=self.y, m="02").replace("ZZQF", "ZZQG"))
        r = tjs("-C", str(self.d), "run", "--no-input")
        self.assertIn(f"filed {self.y} DRIFTED", r.stdout + r.stderr)
        self.assertAlmostEqual(_realized(self.d), locked + 500.0, delta=0.02)
        r = tjs("-C", str(self.d), "check-filed")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("changed since the partial lock was taken", out)
        self.assertIn(f"snapshot taken before {self.y} ended", out)
        self.assertNotIn("frozen copy", out)

        _ok(self, tjs("-C", str(self.d), "close-year", "--yes", "--force"))
        self.assertAlmostEqual(
            _lock(self.d, self.y)["totals"]["realized"], locked + 500.0,
            delta=0.02)
        self.assertTrue(_lock(self.d, self.y)["frozen"]["partial"])
        _ok(self, tjs("-C", str(self.d), "check-filed"))


class TestPartialFreezeThawed(unittest.TestCase):
    """A 2025 lock dated before Dec 31 that froze a copy (what v0.31.0
    and v0.31.1 wrote for close-year --force on an open year)."""

    def setUp(self):
        self.top = _project(2025)
        self.d = self.top / "2025"
        _ok(self, tjs("-C", str(self.d), "run", "--no-input"))
        _ok(self, tjs("-C", str(self.d), "close-year", "--yes"))
        self.assertTrue((self.d / "inputs" / "margin" / "trades.tt")
                        .is_file())
        lock = _lock(self.d, 2025)
        lock["closed_at"] = "2025-11-03T10:00:00"
        (self.d / "filed" / "2025.json").write_text(json.dumps(lock))
        self.locked = lock["totals"]["realized"]
        with open(self.top / "inputs" / "margin" / "trades.tt", "a") as f:
            f.write(_LATER.format(y=2025, m="12"))

    def test_reclose_thaws_then_freezes_the_whole_year(self):
        # The bug: the frozen copy never sees the December trade.
        _ok(self, tjs("-C", str(self.d), "run", "--no-input"))
        self.assertAlmostEqual(_realized(self.d), self.locked, delta=0.02)

        r = tjs("-C", str(self.d), "close-year", "--yes", "--force")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("Thawed", out)
        self.assertIn("Nothing was locked", out)
        st = _settings(self.d)
        self.assertEqual(st.get("inputs_dir"), "../inputs")
        self.assertEqual(st.get("tobase_map"), "../tobase.map")
        self.assertNotIn("## frozen by",
                         (self.d / "taxjson.toml").read_text())
        self.assertFalse((self.d / "inputs").exists())
        self.assertFalse((self.d / "tobase.map").exists())
        lock = _lock(self.d, 2025)
        self.assertTrue(lock["frozen"]["partial"])
        self.assertAlmostEqual(lock["totals"]["realized"], self.locked,
                               delta=0.02)

        r = tjs("-C", str(self.d), "run", "--no-input")
        self.assertIn("filed 2025 DRIFTED", r.stdout + r.stderr)
        self.assertNotIn("frozen copy", r.stdout + r.stderr)
        self.assertAlmostEqual(_realized(self.d), self.locked + 500.0,
                               delta=0.02)

        # 2025 has ended: this close freezes the whole year.
        _ok(self, tjs("-C", str(self.d), "close-year", "--yes", "--force"))
        lock = _lock(self.d, 2025)
        self.assertAlmostEqual(lock["totals"]["realized"],
                               self.locked + 500.0, delta=0.02)
        self.assertFalse(lock["frozen"].get("partial"))
        self.assertEqual(lock["frozen"]["inputs_dir"], "../inputs")
        self.assertIn("ZZQF.TO", (self.d / "inputs" / "margin" /
                                  "trades.tt").read_text())
        self.assertNotIn("inputs_dir", _settings(self.d))
        _ok(self, tjs("-C", str(self.d), "check-filed"))

    def test_an_edited_copy_is_never_thrown_away(self):
        own = self.d / "inputs" / "margin" / "trades.tt"
        own.write_text(own.read_text() + "# an amendment\n")
        toml = (self.d / "taxjson.toml").read_text()
        lock = (self.d / "filed" / "2025.json").read_text()
        r = tjs("-C", str(self.d), "close-year", "--yes", "--force")
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("inputs/margin/trades.tt (changed since the freeze)",
                      out)
        self.assertIn("Nothing was written", out)
        self.assertTrue(own.is_file())
        self.assertEqual((self.d / "taxjson.toml").read_text(), toml)
        self.assertEqual((self.d / "filed" / "2025.json").read_text(), lock)


if __name__ == "__main__":
    unittest.main()
