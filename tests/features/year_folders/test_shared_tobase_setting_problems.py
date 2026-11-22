"""v0.27.1: one tobase.map every year of a multi-year project reads
(`[settings] tobase_map = "../tobase.map"`), and no DISTINCT line for a
depositary receipt (look-alike listings are never joined; the
interlisted master keeps a CDR apart from its US share with no line).
Tax-logic CA-XLIST-06 / US-XLIST-05.

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented names.

Formerly: test_fix_shared_tobase
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli
from taxjson.lib import project_layout as _PL
from taxjson.lib import tobase_map as TB
from _support.symbols_tobase_years import _toml, _years

F1, F4 = "BBG000000Q01", "BBG000000Q04"
SEC = {F1: {"name": "QZALPHA", "kind": "share", "share_class_figi": F1,
            "ca": ["QZA.TO"], "us": ["QZAB.US"], "us_exchange": ["NYSE"]}}


def _master(security=None, distinct=None, generated="2026-10-01"):
    return TB.Master({"schema_version": 1, "generated": generated,
                      "sources": [{"name": "synthetic", "as_of": generated}]},
                     dict(security or {}), dict(distinct or {}))


# ------------------------------------------------------------ the setting

class TestSetting(unittest.TestCase):
    """Formerly: test_fix_shared_tobase.TestSetting"""

    def test_resolves_beside_the_year_folders(self):
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase=TB.render(_master(SEC)), shared=True)
            d = top / "2025"
            self.assertTrue(_PL.shared_tobase(d))
            self.assertEqual(_PL.tobase_map_path(d),
                             (top / "tobase.map").resolve())
            self.assertEqual(TB.tobase_path(d), _PL.tobase_map_path(d))
            self.assertEqual(_PL.tobase_shown(d),
                             "../tobase.map, shared by every year")
            self.assertEqual(_PL.setting_problems(d, _PL._settings(d)), [])
            # Read through it: the overlay of the year's ticker.map.
            ov = TB.overlay_for(d / "ticker.map", "")
            self.assertIn("TOBASE QZAB.US QZA.TO", ov.lines)
            # A single-folder project: beside ticker.map, as before.
            single = Path(td) / "single"
            single.mkdir()
            (single / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n')
            self.assertFalse(_PL.shared_tobase(single))
            self.assertEqual(_PL.tobase_map_path(single),
                             single / "tobase.map")

    def test_path_safety(self):
        with tempfile.TemporaryDirectory() as td:
            top = _years(td)
            d = top / "2025"
            outside = Path(td) / "elsewhere"
            outside.mkdir()
            (outside / "tobase.map").write_text("")
            (top / "link.map").symlink_to(outside / "tobase.map")
            (top / "2024" / "tobase.map").write_text("")
            for v, want in (("../../elsewhere/tobase.map", "outside"),
                            ("../link.map", "outside"),
                            ("tobase.map", "inside this project"),
                            ("../2024/tobase.map", "another year"),
                            ("..", "names a folder"),
                            ("../inputs", "names a folder"),
                            ("", "must be a file path")):
                with self.subTest(v=v):
                    st = {"tobase_map": v}
                    with self.assertRaises(_PL.LayoutError) as cm:
                        _PL.tobase_setting(d, st)
                    self.assertIn(want, str(cm.exception))
                    probs = _PL.setting_problems(d, st)
                    self.assertTrue(any(want in p for p in probs), probs)
                    # A reader that runs anyway: the project's own file.
            (d / "taxjson.toml").write_text(_toml(
                2025, extra='tobase_map = "../../elsewhere/tobase.map"\n'))
            self.assertEqual(_PL.tobase_map_path(d), d / "tobase.map")

    def test_own_copy_beside_the_setting_is_refused_naming_both(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, tobase=text, shared=True)
            d = top / "2025"
            (d / "tobase.map").write_text(text)
            why = _PL.tobase_both_problem(d, _PL._settings(d))
            self.assertIn("two tobase.map files", why)
            self.assertIn("'../tobase.map'", why)
            self.assertIn("tobase.map of its own", why)
            self.assertIn("tjs migrate", why)
            r = cli(d, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("two tobase.map files", r.stderr)
            r = cli(d, "update-tobase-map")
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("two tobase.map files", r.stderr)
            self.assertFalse((top / "tobase.map.bak").exists())


if __name__ == "__main__":
    unittest.main()
