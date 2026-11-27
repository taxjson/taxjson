"""The 2026-10-29 fix round 7, migrate group: `tjs migrate
--to-years`, `new-year`, `align` and the ticker-map hygiene advice.

- TJ-0797 an account named `slips` (its exports in inputs/slips/, the
  folder of the year's slips) is refused before anything moves;
- TJ-0798 new-year and align give the new year its own holdings
  folder for a `holdings_dir` naming the other year's own folder
  (`../2025/snapshots` -> `snapshots`): never a path the next run
  refuses;
- TJ-0799 migrate checks taxjson.toml and .gitignore before it moves
  anything: a read-only one is refused, a taxjson.toml that is a link
  inside the project stays a link (its target written);
- TJ-0800 a .gitignore that is not UTF-8 is read as bytes: its bytes
  are kept, `exports/` appended;
- TJ-0698 an absolute path into a moved folder (an account's holdings,
  prior_year_record) names the moved file;
- TJ-0699 the links inside the moved folders, and absolute ones, still
  name the same file after the move;
- TJ-0802 a ticker.map rule that maps a rename's destination
  (symbol_new) is used, never "unused".

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).

Formerly: test_fix_1029_r7_migrate
"""
import os
import stat
import unittest

from taxjson.lib import project_layout as PL
from _support.config_migrate_project import TT, SNAP, tjs, project, _Tmp


class MigrateSlipsAccount(_Tmp):
    """TJ-0797: inputs/slips/ is the year's slips folder: it moves into
    the year folder, so an account named slips lost its exports."""

    def test_refused_before_anything_moves(self):
        p = self.top / "proj"
        project(p, accounts=("slips",))
        (p / "inputs" / "slips" / "trades.tt").write_text(TT)
        self.ok(tjs(p, "run", "--no-input"))
        for dry in ((), ("--dry-run",)):
            r = tjs(p, "migrate", "--to-years", *dry)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("slips", r.stderr)
            self.assertIn("Nothing was moved", r.stderr)
        self.assertTrue((p / "taxjson.toml").is_file())
        self.assertTrue((p / "inputs" / "slips" / "trades.tt").is_file())
        self.assertFalse((p / "2025").exists())

    def test_other_account_names_still_migrate(self):
        p = self.top / "proj"
        project(p)
        (p / "inputs" / "margin" / "trades.tt").write_text(TT)
        self.ok(tjs(p, "migrate", "--to-years"))
        self.assertTrue((p / "inputs" / "margin" / "trades.tt").is_file())
        self.ok(tjs(p / "2025", "run", "--no-input"))


class NewYearOwnHoldingsDir(_Tmp):
    """TJ-0798: a year's holdings_dir naming its own folder by way of
    the year folder (`../2025/snapshots`) is the new year's own
    folder there, never 2025's (which the next run refuses)."""

    def _year(self, year, holdings_dir=None):
        d = self.top / str(year)
        project(d, year=year, extra=(f'holdings_dir = "{holdings_dir}"\n'
                                     if holdings_dir else ""))
        (d / "snapshots").mkdir()
        (d / "inputs" / "margin" / "trades.tt").write_text(
            f"BUYSELL {year}-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        return d

    def test_new_year_rebases_the_folder(self):
        old = self._year(2025, "../2025/snapshots")
        self.ok(tjs(old, "run", "--no-input"))
        self.ok(tjs(self.top, "new-year", "2026"))
        new = self.top / "2026"
        st = PL.parse_config_text((new / "taxjson.toml").read_text())["settings"]
        self.assertEqual(st.get("holdings_dir"), "snapshots")
        self.assertEqual(PL.setting_problems(new, st), [])
        r = tjs(new, "run", "--no-input")
        self.assertNotIn("holdings_dir", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)

    def test_new_year_keeps_other_values(self):
        old = self._year(2025, "snapshots")
        self.ok(tjs(self.top, "new-year", "2026"))
        st = PL.parse_config_text((self.top / "2026" / "taxjson.toml")
                           .read_text())["settings"]
        self.assertEqual(st.get("holdings_dir"), "snapshots")
        self.assertTrue(old.is_dir())

    def test_align_brings_the_folder_over_rebased(self):
        self._year(2025, "../2025/snapshots")
        new = self._year(2026)
        self.ok(tjs(new, "run", "--no-input"))
        self.ok(tjs(new, "align", "--from", "2025", "--write", "--all",
                    "--json"))
        st = PL.parse_config_text((new / "taxjson.toml").read_text())["settings"]
        self.assertEqual(st.get("holdings_dir"), "snapshots")
        self.ok(tjs(new, "run", "--no-input"))
        # The same folder both ways: no difference left to bring over.
        r = self.ok(tjs(new, "align", "--from", "2025", "--json"))
        self.assertNotIn("holdings_dir", r.stdout)

    def test_align_refuses_a_folder_setting_the_run_refuses(self):
        from taxjson.cli.setup import _align_new_config_problems
        new = self._year(2026)
        before = (new / "taxjson.toml").read_text()
        after = PL.set_key_text(before, ("settings", "holdings_dir"),
                                "../../elsewhere")
        probs = _align_new_config_problems(before, after, new)
        self.assertTrue(probs and "holdings_dir" in probs[0], probs)


class MigrateGuards(_Tmp):
    """TJ-0799 / TJ-0800: taxjson.toml and .gitignore are checked before
    anything moves, and written the way user files are."""

    def _proj(self):
        p = self.top / "proj"
        project(p)
        (p / "inputs" / "margin" / "trades.tt").write_text(TT)
        return p

    def _refused(self, p, what):
        r = tjs(p, "migrate", "--to-years")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(what, r.stderr)
        self.assertIn("read-only", r.stderr)
        self.assertIn("Nothing was moved", r.stderr)
        self.assertFalse((p / "2025").exists())
        self.assertTrue((p / "taxjson.toml").is_file())
        self.assertTrue((p / "ticker.map").is_file())

    def test_read_only_config_refused(self):
        p = self._proj()
        cfg = p / "taxjson.toml"
        cfg.chmod(0o444)
        self.addCleanup(cfg.chmod, 0o600)
        self._refused(p, "taxjson.toml")
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o444)

    def test_read_only_gitignore_refused(self):
        p = self._proj()
        gi = p / ".gitignore"
        gi.write_text("# notes\n")
        gi.chmod(0o444)
        self.addCleanup(gi.chmod, 0o600)
        self._refused(p, ".gitignore")
        self.assertEqual(gi.read_text(), "# notes\n")

    def test_read_only_gitignore_with_exports_needs_no_write(self):
        p = self._proj()
        gi = p / ".gitignore"
        gi.write_text("exports/\n")
        gi.chmod(0o444)
        self.addCleanup(gi.chmod, 0o600)
        self.ok(tjs(p, "migrate", "--to-years"))
        self.assertEqual(gi.read_text(), "exports/\n")

    def test_internal_config_link_kept(self):
        p = self._proj()
        (p / "config").mkdir()
        (p / "taxjson.toml").rename(p / "config" / "settings.toml")
        (p / "taxjson.toml").symlink_to("config/settings.toml")
        self.ok(tjs(p, "run", "--no-input"))
        self.ok(tjs(p, "migrate", "--to-years"))
        new = p / "2025" / "taxjson.toml"
        self.assertTrue(new.is_symlink())
        self.assertEqual(new.resolve(), (p / "config" / "settings.toml"))
        st = PL.parse_config_text(new.read_text())["settings"]
        self.assertEqual(st.get("inputs_dir"), "../inputs")
        self.ok(tjs(p / "2025", "run", "--no-input"))

    def test_gitignore_not_utf8_kept_as_bytes(self):
        p = self._proj()
        gi = p / ".gitignore"
        gi.write_bytes(b"# comment \xff\n")
        self.ok(tjs(p, "migrate", "--to-years"))
        self.assertEqual(gi.read_bytes(), b"# comment \xff\nexports/\n")
        self.assertTrue((p / "2025" / "taxjson.toml").is_file())


class MigrateRelocation(_Tmp):
    """TJ-0698 / TJ-0699: what the moved folders hold is named at its
    new place — absolute paths in taxjson.toml, absolute links and
    links inside a moved folder."""

    def _proj(self):
        p = self.top / "proj"
        # holdings/ as a folder that moves with the year: the person's
        # own `holdings_dir` (with none it stays at the top, shared by
        # every year: backlog TJ-5367).
        project(p, extra='holdings_dir = "holdings"\n')
        (p / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2025-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        (p / "holdings").mkdir()
        (p / "holdings" / "snap.toml").write_text(SNAP)
        return p

    def _set(self, p, path, value):
        cfg = p / "taxjson.toml"
        cfg.write_text(PL.set_key_text(cfg.read_text(), path, value))

    def test_absolute_holdings_file_follows_the_move(self):
        p = self._proj()
        self._set(p, ("accounts", "margin", "holdings"),
                  [str(p / "holdings" / "snap.toml")])
        self.ok(tjs(p, "run", "--no-input"))
        self.ok(tjs(p, "sanity"))
        self.ok(tjs(p, "migrate", "--to-years"))
        y = p / "2025"
        doc = PL.parse_config_text((y / "taxjson.toml").read_text())
        self.assertEqual(doc["accounts"]["margin"]["holdings"],
                         [str(y / "holdings" / "snap.toml")])
        self.ok(tjs(y, "run", "--no-input"))
        self.ok(tjs(y, "sanity"))

    def test_absolute_prior_year_record_follows_the_move(self):
        p = self._proj()
        (p / "filed").mkdir()
        (p / "filed" / "2024.json").write_text(
            '{"year": 2024, "country": "canada"}')
        self._set(p, ("settings", "prior_year_record"),
                  str(p / "filed" / "2024.json"))
        self.ok(tjs(p, "migrate", "--to-years"))
        y = p / "2025"
        st = PL.parse_config_text((y / "taxjson.toml").read_text())["settings"]
        self.assertEqual(st["prior_year_record"],
                         str(y / "filed" / "2024.json"))

    def test_absolute_path_outside_the_moved_folders_kept(self):
        p = self._proj()
        (p / "inputs" / "margin" / "pos.toml").write_text(SNAP)
        pos = str(p / "inputs" / "margin" / "pos.toml")
        self._set(p, ("accounts", "margin", "holdings"), [pos])
        self.ok(tjs(p, "migrate", "--to-years"))
        doc = PL.parse_config_text((p / "2025" / "taxjson.toml").read_text())
        self.assertEqual(doc["accounts"]["margin"]["holdings"], [pos])

    def test_absolute_map_link_follows_the_move(self):
        p = self._proj()
        (p / "ticker.map").unlink()
        (p / "holdings" / "tickers.txt").write_text("")
        (p / "ticker.map").symlink_to(p / "holdings" / "tickers.txt")
        self.ok(tjs(p, "run", "--no-input"))
        self.ok(tjs(p, "migrate", "--to-years"))
        y = p / "2025"
        self.assertTrue((y / "ticker.map").is_symlink())
        self.assertEqual((y / "ticker.map").resolve(),
                         y / "holdings" / "tickers.txt")
        self.ok(tjs(y, "run", "--no-input"))

    def test_nested_relative_link_follows_the_move(self):
        p = self._proj()
        (p / "inputs" / "margin" / "pos.toml").write_text(SNAP)
        (p / "holdings" / "current.toml").symlink_to(
            "../inputs/margin/pos.toml")
        # One that stays inside the moved folder keeps its text.
        (p / "holdings" / "same.toml").symlink_to("snap.toml")
        self._set(p, ("accounts", "margin", "holdings"),
                  ["holdings/current.toml"])
        self.ok(tjs(p, "run", "--no-input"))
        self.ok(tjs(p, "sanity"))
        self.ok(tjs(p, "migrate", "--to-years"))
        y = p / "2025"
        cur = y / "holdings" / "current.toml"
        self.assertTrue(cur.is_symlink())
        self.assertEqual(cur.resolve(), p / "inputs" / "margin" / "pos.toml")
        self.assertEqual(os.readlink(y / "holdings" / "same.toml"),
                         "snap.toml")
        self.ok(tjs(y, "run", "--no-input"))
        self.ok(tjs(y, "sanity"))


class RenameDestinationRule(_Tmp):
    """TJ-0802: GLOBAL NEW.TO FINAL.TO maps a RENAME's destination
    (symbol_new): it is used."""

    def test_rule_on_symbol_new_is_used(self):
        from taxjson.lib.map_hygiene import unused_rules
        p = self.top / "proj"
        project(p)
        (p / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2025-01-02 10:00:00 OLD.TO 10 CAD 10 100 0\n"
            "RENAME 2025-06-02 OLD.TO NEW.TO\n")
        (p / "ticker.map").write_text("GLOBAL NEW.TO FINAL.TO\n"
                                      "GLOBAL GONE.TO OTHER.TO\n")
        self.ok(tjs(p, "run", "--no-input"))
        unused, unread = unused_rules(p)
        self.assertEqual(unread, [])
        self.assertEqual([u.frm for u in unused], ["GONE.TO"])
        r = self.ok(tjs(p, "ticker-map", "--suggest", "--json"))
        self.assertNotIn("GLOBAL NEW.TO FINAL.TO", r.stdout)


if __name__ == "__main__":
    unittest.main()
