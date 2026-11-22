"""v0.27.1: one tobase.map every year of a multi-year project reads
(`[settings] tobase_map = "../tobase.map"`), and no DISTINCT line for a
depositary receipt (look-alike listings are never joined; the
interlisted master keeps a CDR apart from its US share with no line).
Tax-logic CA-XLIST-06 / US-XLIST-05.

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented names.

Formerly: test_fix_shared_tobase
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli
from taxjson.rules import ids as R

from taxjson.lib import checklist as CL
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


# ------------------------------------------------------------ the commands

class TestCommands(unittest.TestCase):
    """Formerly: test_fix_shared_tobase.TestCommands"""

    def test_details_says_an_edited_retracted_line_as_one_message(self):
        rule_ = "DISTINCT QZD.US QZD.TO"
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase=TB.render(_master(SEC)), shared=True)
            p = top / "tobase.map"
            p.write_text(p.read_text() + f"DISTINCT QZD.US QZD.V  # {TB.MARKER}"
                         f"{F4}:{TB.line_hash(rule_)}\n")
            r = cli(top / "2024", "update-tobase-map", "--details")
            self.assertEqual(r.returncode, 0, r.stderr)
            text = " ".join(r.stdout.split())
            self.assertIn("1 line(s) you edited are lines the master RETRACTED", text)
            self.assertIn(f"! DISTINCT QZD.US QZD.V ({TB.TOBASE_MAP}:", text)

    def test_single_folder_init_keeps_it_beside_ticker_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            r = cli(Path(td), "init", str(root), "--single", "--country",
                    "canada", "--year", "2025")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((root / "tobase.map").is_file())
            self.assertFalse(_PL.shared_tobase(root))
            self.assertNotIn("Shared by every year",
                             (root / "tobase.map").read_text())

    def test_new_year_from_a_per_year_copy_keeps_one_and_says_migrate(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, years=(2025,), tobase=text)
            r = cli(top / "2025", "new-year", "2026")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((top / "2026" / "tobase.map").read_text(), text)
            self.assertIn("migrate", r.stdout + r.stderr)

    @rule(R.CA_XLIST_06)
    def test_migrate_identical_copies(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, years=(2024, 2025, 2026), tobase=text)
            r = cli(top, "migrate", "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("identical", r.stdout)
            self.assertFalse((top / "tobase.map").exists())
            r = cli(top, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((top / "tobase.map").read_text(), text)
            for y in (2024, 2025, 2026):
                d = top / str(y)
                self.assertFalse((d / "tobase.map").exists())
                self.assertEqual((d / "tobase.map.bak").read_text(), text)
                # Beside inputs_dir, the rest of the file as it was.
                self.assertIn('inputs_dir = "../inputs"\n'
                              'tobase_map = "../tobase.map"\n',
                              (d / "taxjson.toml").read_text())
                self.assertEqual(_PL.tobase_map_path(d),
                                 (top / "tobase.map").resolve())
                self.assertEqual(_PL.setting_problems(d, _PL._settings(d)),
                                 [])
            # Again: nothing left to do.
            r = cli(top, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("nothing to migrate", r.stdout)
            # years: the shared file, compared as a setting.
            r = cli(top, "years")
            self.assertIn("tobase.map: tobase.map, shared by 2024, 2025, "
                          "2026", r.stdout)
            c = _PL.compare(top / "2024", top / "2026")
            self.assertEqual((c["tobase_only_here"], c["tobase_only_there"],
                              c["keys"]), ([], [], []))

    def test_migrate_from_a_year_folder(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, tobase=text)
            r = cli(top / "2025", "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((top / "tobase.map").is_file())
            self.assertFalse((top / "2024" / "tobase.map").exists())

    def test_migrate_differing_copies_needs_write(self):
        with tempfile.TemporaryDirectory() as td:
            old = TB.render(_master(SEC)) + \
                "\n## --- Your own lines (kept as written) ---\n" \
                "TOBASE QZOWN.US QZOWN.TO\n"
            new = TB.render(_master(SEC, generated="2026-10-05"))
            top = _years(td, tobase=old)
            (top / "2025" / "tobase.map").write_text(new)
            r = cli(top, "migrate")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("differ", r.stdout + r.stderr)
            self.assertIn("TOBASE QZOWN.US QZOWN.TO", r.stdout)
            self.assertIn("2025/tobase.map", r.stdout)
            self.assertFalse((top / "tobase.map").exists())
            self.assertTrue((top / "2024" / "tobase.map").is_file())
            self.assertNotIn("tobase_map", (top / "2024" / "taxjson.toml")
                             .read_text())
            r = cli(top, "migrate", "--write")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((top / "tobase.map").read_text(), new)
            self.assertEqual((top / "2024" / "tobase.map.bak").read_text(),
                             old)
            self.assertIn("TOBASE QZOWN.US QZOWN.TO", r.stdout)

    def test_migrate_to_years_keeps_it_at_the_top(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            r = cli(Path(td), "init", str(root), "--single", "--country",
                    "canada", "--year", "2025")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = cli(root, "migrate", "--to-years")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((root / "tobase.map").is_file())
            self.assertFalse((root / "2025" / "tobase.map").exists())
            self.assertEqual(_PL.tobase_map_path(root / "2025"),
                             (root / "tobase.map").resolve())

    def test_update_writes_the_shared_file_from_a_year_or_the_top(self):
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase=TB.render(_master(SEC)), shared=True)
            p = top / "tobase.map"
            p.write_text(p.read_text().replace(
                TB.STAMP + " ", TB.STAMP + " 2020-01-01 # "))
            r = cli(top / "2024", "update-tobase-map")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("../tobase.map, shared by every year: read by "
                          "2024, 2025", " ".join(r.stdout.split()))
            r = cli(top / "2024", "update-tobase-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Shared by every year folder", p.read_text())
            self.assertTrue((top / "tobase.map.bak").is_file())
            self.assertFalse((top / "2024" / "tobase.map").exists())
            self.assertIn("in each year that reads it (2024, 2025)",
                          " ".join(r.stdout.split()))
            # At the top: the same file, as from the newest year.
            r = cli(top, "update-tobase-map", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(r.stdout)["exists"])
            # Ambiguous at the top: a year keeps a copy of its own.
            (top / "2025" / "taxjson.toml").write_text(_toml(2025))
            (top / "2025" / "tobase.map").write_text(p.read_text())
            r = cli(top, "update-tobase-map")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("ambiguous", r.stderr)

    def test_checklist_and_years_name_the_shared_file_that_changed(self):
        before = {"tobase.map": "h1", "ticker.map": "t"}
        # The year's own copy made the shared one, same content: no
        # change (`tjs migrate`).
        self.assertEqual(CL._fingerprint_diff(
            before, {"../tobase.map": "h1", "ticker.map": "t"}), "")
        self.assertEqual(CL._fingerprint_diff(
            {"../tobase.map": "h1"}, {"../tobase.map": "h2"}),
            "changed: ../tobase.map (shared by every year)")
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase=TB.render(_master(SEC)), shared=True)
            d = top / "2025"
            paths = CL._input_paths(d, {})
            self.assertIn((top / "tobase.map").resolve(), paths)
            self.assertEqual(CL._fp_key(d, (top / "tobase.map").resolve()),
                             "../tobase.map")
            # The setting itself is not a run input (the file is).
            a = CL._run_config_digest(d / "taxjson.toml")
            (d / "taxjson.toml").write_text(_toml(2025))
            self.assertEqual(a, CL._run_config_digest(d / "taxjson.toml"))


if __name__ == "__main__":
    unittest.main()
