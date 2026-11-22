"""v0.17.0 pre-release security review pins (core side).

H1  install hints never name a PyPI package (taxjson / taxjson-fetch are
    not published there: a name-squatter's package would be installed).
M1  write-then-rename writers never write through a symlink planted at
    their temp name (`<out>.part`, `.migrate.part`) or at the final name.
L5  fill-crypto quotes the symbol into the Yahoo URL and closes the
    response.
I1  the [fx] extra's comment dates the Yahoo fallback like the code.

(L6, TAXJSON_OFFLINE stopping `tjs fetch`: test_fetch_dispatch.py.)
Synthetic data only; nothing here touches the network.

Formerly: test_fix_prerelease_security
"""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from taxjson.lib.fingerprint_record import FINGERPRINT_FILE
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

VICTIM = "victim: must never change\n"


# ----------------------------------------------------------------- M1

class _Victims(unittest.TestCase):
    """A temp dir holding an 'outside' folder of victim files."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    def victim(self, name):
        v = self.outside / name
        v.write_text(VICTIM)
        return v

    def plant(self, link: Path, name=None):
        v = self.victim(name or (link.name + ".victim"))
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(v)
        return v

    def assertUntouched(self, *victims):
        for v in victims:
            self.assertFalse(v.is_symlink(), v)
            self.assertEqual(v.read_text(), VICTIM, v)


class TestSafeWrite(_Victims):
    """Formerly: test_fix_prerelease_security.TestSafeWrite"""
    def test_planted_temp_symlink_is_not_followed(self):
        from taxjson.lib.safe_write import write_atomic
        out = self.tmp / "proj" / "work" / "m_base.json"
        v = self.plant(out.with_name(out.name + ".part"))
        write_atomic(out, "new\n")
        self.assertUntouched(v)
        self.assertEqual(out.read_text(), "new\n")
        self.assertFalse(out.is_symlink())
        # Issue #9: each write has its own uniquely named temp; the
        # planted link at the old fixed name is never used (left as it
        # is), and the writer leaves no temp of its own.
        self.assertTrue(out.with_name(out.name + ".part").is_symlink())
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()),
                         ["m_base.json", "m_base.json.part"])
        self.assertEqual(stat.S_IMODE(out.stat().st_mode) & 0o077, 0)

    def test_symlink_at_the_final_name_is_replaced_not_written_through(self):
        from taxjson.lib.safe_write import write_atomic
        out = self.tmp / "proj" / "reports" / "m.sum"
        v = self.plant(out)
        write_atomic(out, b"bytes\n")
        self.assertUntouched(v)
        self.assertFalse(out.is_symlink())
        self.assertEqual(out.read_bytes(), b"bytes\n")

    def test_failed_write_keeps_the_old_file_and_leaves_no_temp(self):
        from taxjson.lib.safe_write import atomic_open
        out = self.tmp / "f.txt"
        out.write_text("old\n")
        with self.assertRaises(RuntimeError):
            with atomic_open(out) as f:
                f.write("half")
                raise RuntimeError("boom")
        self.assertEqual(out.read_text(), "old\n")
        self.assertFalse(os.path.lexists(self.tmp / "f.txt.part"))

    def test_cli_diag_write_text_atomic(self):
        from taxjson.lib.cli_diag import write_text_atomic
        out = self.tmp / "taxjson.toml"
        v = self.plant(out.with_name(out.name + ".part"))
        write_text_atomic(out, "[settings]\n")
        self.assertUntouched(v)
        self.assertEqual(out.read_text(), "[settings]\n")

    def test_run_to_file_stage_writer(self):
        from taxjson.pipeline import tools as pipeline_tools
        out = self.tmp / "proj" / "work" / "m_raw.json"
        v1 = self.plant(out.with_name(out.name + ".part"))
        v2 = self.plant(out)
        pipeline_tools.run_to_file([sys.executable, "-c", "print('stage output')"], out)
        self.assertUntouched(v1, v2)
        self.assertFalse(out.is_symlink())
        self.assertEqual(out.read_text(), "stage output\n")

    def test_other_part_writers(self):
        from taxjson.bin import taxjson_watch as W
        from taxjson.lib import checklist as CL
        proj = self.tmp / "proj"
        (proj / "work").mkdir(parents=True)
        vs = [self.plant(proj / "work" / "watch.json.part"),
              self.plant(proj / "work" / (FINGERPRINT_FILE + ".part")),
              self.plant(proj / f"{CL.STATE_FILE}.{os.getpid()}.part")]
        W.save_state(proj / "work" / "watch.json", {}, None, "2025-01-02")
        CL.record_input_fingerprint(proj, {"settings": {
            "year": 2025, "country": "canada"}, "accounts": {}})
        CL.save_state(proj, {}, 2025)
        self.assertUntouched(*vs)
        self.assertTrue((proj / "work" / "watch.json").is_file())
        self.assertTrue((proj / "work" / FINGERPRINT_FILE).is_file())
        self.assertTrue((proj / CL.STATE_FILE).is_file())

    def test_form_export_csv_writer(self):
        from taxjson.bin import taxjson_form_export as FE
        out = self.tmp / "s3.csv"
        v = self.plant(out.with_name(out.name + ".part"))
        rep = {"form": "8949", "part_I": [], "part_II": []}
        FE.write_csv(rep, out)
        self.assertUntouched(v)
        self.assertTrue(out.read_text().startswith("part,"))


class TestMigrateWriter(_Victims):
    """Formerly: test_fix_prerelease_security.TestMigrateWriter"""
    def _project(self):
        root = self.tmp / "proj"
        root.mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            '[accounts.m]\ntype = "taxable"\n')
        (root / "yf_ticker.map").write_text("ZZS.TO ZZS-A.TO\n")
        return root

    def test_planted_migrate_part_is_not_followed(self):
        from taxjson.lib import migrate as M
        root = self._project()
        v = self.plant(root / "ticker.map.migrate.part")
        M.apply(M.plan(root))
        self.assertUntouched(v)
        self.assertIn("QUOTE ZZS.TO ZZS-A.TO",
                      (root / "ticker.map").read_text())
        # Never used (issue #9: each write has its own temp name).
        self.assertTrue((root / "ticker.map.migrate.part").is_symlink())
        self.assertEqual([p.name for p in root.iterdir()
                          if p.name.endswith(".part")],
                         ["ticker.map.migrate.part"])

    def test_ticker_map_linked_outside_the_project_is_refused(self):
        from taxjson.lib import migrate as M
        root = self._project()
        v = self.plant(root / "ticker.map", "shared-ticker.map")
        with self.assertRaises(M.MigrateError) as cm:
            M.plan(root)
        self.assertIn("symlink", str(cm.exception))
        self.assertIn("outside the project", str(cm.exception))
        self.assertUntouched(v)
        self.assertTrue((root / "yf_ticker.map").is_file())   # nothing moved

    def test_ticker_map_linked_inside_the_project_updates_the_target(self):
        from taxjson.lib import migrate as M
        root = self._project()
        real = root / "maps" / "ticker.map"
        real.parent.mkdir()
        real.write_text("# shared\n")
        (root / "ticker.map").symlink_to(Path("maps") / "ticker.map")
        M.apply(M.plan(root))
        self.assertTrue((root / "ticker.map").is_symlink())
        self.assertIn("QUOTE ZZS.TO ZZS-A.TO", real.read_text())


if __name__ == "__main__":
    unittest.main()
