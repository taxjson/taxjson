"""`[settings] requires_taxjson`: a project records the oldest taxjson its
layout needs, and an older taxjson refuses every command on it with one
line (lib/requires). init / new-year / migrate / align write the right
minimum and never lower it; a malformed value is a clear error; an
unknown [settings] key is one warning line in every command.

Synthetic projects only (made-up accounts, no inputs).
"""
import re
import unittest
from pathlib import Path
from unittest import mock

from _tmpfiles import private_dir
from tax_rules.dual import cli

from taxjson.lib import requires as REQ

INSTALLED = REQ.running_version() or "0.0.0"
HAVE = REQ.version_tuple(INSTALLED) or (0, 0, 0)
NEWER = f"{HAVE[0]}.{HAVE[1]}.{HAVE[2] + 1}"
UPGRADE = "upgrade: `tjs deploy` or re-run the installer"


def _toml(country="canada", year=2025, extra=""):
    return (f'[settings]\ncountry = "{country}"\nyear = {year}\n{extra}\n'
            f'[accounts.margin]\ntype = "taxable"\n')


def _single(extra="", country="canada"):
    root = Path(private_dir()) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_toml(country, 2025, extra))
    return root


def _years(years=(2024, 2025), extra=None, tobase=None):
    """Year folders reading shared exports; `extra` {year: settings
    lines}; `tobase` the text of each year's own tobase.map copy."""
    top = Path(private_dir()) / "taxes"
    (top / "inputs" / "margin").mkdir(parents=True)
    for y in years:
        d = top / str(y)
        d.mkdir()
        (d / "taxjson.toml").write_text(_toml(
            "canada", y, 'inputs_dir = "../inputs"\n'
            + (extra or {}).get(y, "")))
        (d / "ticker.map").write_text("")
        if tobase is not None:
            (d / "tobase.map").write_text(tobase)
    return top


def _key(path: Path):
    m = re.search(r'^requires_taxjson\s*=\s*"([^"]*)"', path.read_text(),
                  re.M)
    return m[1] if m else None


class TestModule(unittest.TestCase):

    def test_parse_spec(self):
        self.assertEqual(REQ.parse_spec(">=0.27.1"), (0, 27, 1))
        self.assertEqual(REQ.parse_spec(" >= 1.2.3 "), (1, 2, 3))
        for bad in ("0.27.1", ">0.27.1", ">=0.27", "==0.27.1", 27, None,
                    ">=0.27.1,<1"):
            with self.assertRaises(REQ.RequiresError, msg=repr(bad)):
                REQ.parse_spec(bad)

    def test_older_refused_equal_and_newer_run(self):
        st = {"requires_taxjson": ">=0.27.1"}
        with mock.patch.object(REQ, "running_version",
                               return_value="0.27.0"):
            why = REQ.problem(st)
        self.assertEqual(why, "this project needs taxjson 0.27.1 or newer "
                              "(installed: 0.27.0) — " + UPGRADE)
        for have in ("0.27.1", "0.28.0", "1.0.0", "0.27.1.dev3+gabc"):
            with mock.patch.object(REQ, "running_version",
                                   return_value=have):
                self.assertIsNone(REQ.problem(st), have)
        # No key, or a version that cannot be read: runs.
        self.assertIsNone(REQ.problem({}))
        with mock.patch.object(REQ, "running_version", return_value=None):
            self.assertIsNone(REQ.problem(st))

    def test_malformed_is_named(self):
        why = REQ.problem({"requires_taxjson": "0.27.1"})
        self.assertIn('[settings] requires_taxjson must be ">=X.Y.Z"', why)
        self.assertIn("'0.27.1'", why)

    def test_features_and_minimum(self):
        self.assertEqual(REQ.features_of({"country": "canada"}), set())
        self.assertEqual(REQ.features_of({"country": "canada"},
                                         own_tobase=True), {"tobase_map"})
        # A US project reads no tobase.map.
        self.assertEqual(REQ.features_of({"country": "usa"},
                                         own_tobase=True), set())
        f = REQ.features_of({"country": "canada", "inputs_dir": "../inputs",
                             "tobase_map": "../tobase.map"})
        self.assertEqual(f, {"shared_exports", "shared_tobase_map"})
        self.assertEqual(REQ.minimum(f), (0, 27, 1))
        self.assertEqual(REQ.minimum({"shared_exports"}), (0, 26, 0))
        # The single list: every feature has a release.
        for feat in REQ.FEATURES:
            self.assertIsNotNone(REQ.version_tuple(feat.version), feat)

    def test_never_lowered(self):
        self.assertIsNone(REQ.raised(">=0.30.0", {"shared_tobase_map"}))
        self.assertEqual(REQ.raised(">=0.26.0", {"shared_tobase_map"}),
                         ">=0.27.1")
        self.assertEqual(REQ.raised(None, {"shared_exports"}), ">=0.26.0")
        text = _toml(extra='requires_taxjson = ">=0.30.0"\n'
                           'inputs_dir = "../inputs"\n'
                           'tobase_map = "../tobase.map"\n')
        self.assertEqual(REQ.raised_text(text), text)
        self.assertTrue(REQ.lowers("settings.requires_taxjson", ">=0.28.0",
                                   ">=0.26.0"))
        self.assertTrue(REQ.lowers("settings.requires_taxjson", ">=0.28.0",
                                   None))
        self.assertFalse(REQ.lowers("settings.requires_taxjson", ">=0.26.0",
                                    ">=0.28.0"))

    def test_raise_requirement_writes_the_layout_minimum(self):
        root = _single()
        (root / "tobase.map").write_text("")
        self.assertEqual(REQ.raise_requirement(root), ">=0.27.0")
        self.assertEqual(_key(root / "taxjson.toml"), ">=0.27.0")
        self.assertIsNone(REQ.raise_requirement(root))       # unchanged


class TestRefused(unittest.TestCase):

    def test_older_taxjson_refuses_every_command_first(self):
        root = _single(f'requires_taxjson = ">={NEWER}"\n')
        # Checked before anything else: an old missing_history.json
        # (refused by every command, naming migrate) is not reached.
        (root / "missing_history.json").write_text("{}")
        for cmd in (("sum",), ("checklist",), ("format", "--check"),
                    ("migrate",), ("run", "--no-input")):
            r = cli(root, *cmd)
            self.assertEqual(r.returncode, 2, (cmd, r.stdout, r.stderr))
            self.assertIn(f"this project needs taxjson {NEWER} or newer "
                          f"(installed: {INSTALLED}) — {UPGRADE}",
                          r.stderr, cmd)
            self.assertNotIn("missing_history.json", r.stderr, cmd)
        self.assertEqual(_key(root / "taxjson.toml"), f">={NEWER}")

    def test_equal_runs(self):
        root = _single(f'requires_taxjson = ">={INSTALLED}"\n')
        r = cli(root, "format", "--check")
        self.assertNotEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("needs taxjson", r.stderr)

    def test_malformed_value_stops_with_the_key(self):
        root = _single('requires_taxjson = "0.27.1"\n')
        r = cli(root, "sum")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('requires_taxjson must be ">=X.Y.Z"', r.stderr)

    def test_another_year_folder_counts_for_the_years_commands(self):
        top = _years(extra={2025: f'requires_taxjson = ">={NEWER}"\n'})
        for where, cmd in ((top, ("years",)),
                           (top / "2024", ("new-year", "2026")),
                           (top / "2024", ("align", "--from", "2025"))):
            r = cli(where, *cmd)
            self.assertEqual(r.returncode, 2, (cmd, r.stderr))
            self.assertIn(f"2025/ needs taxjson {NEWER} or newer", r.stderr)
        self.assertFalse((top / "2026").exists())

    def test_unknown_setting_is_one_warning_in_any_command(self):
        root = _single('bogus_key = 1\n')
        r = cli(root, "format", "--check")
        self.assertEqual(r.stderr.count("unknown [settings] key "
                                        "'bogus_key' is ignored"), 1,
                         r.stderr)


class TestWriters(unittest.TestCase):

    def test_init_writes_the_layout_minimum(self):
        base = Path(private_dir())
        for args, rel, want in (
                (("--country", "canada"), "ca/2025", ">=0.27.1"),
                (("--country", "usa"), "us/2025", ">=0.27.0"),
                (("--country", "canada", "--single"), "cas", ">=0.27.0"),
                (("--country", "usa", "--single"), "uss", ">=0.27.0")):
            top = base / rel.split("/")[0]
            r = cli(base, "init", *args, "--year", "2025", str(top))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_key(base / rel / "taxjson.toml"), want, args)

    def test_init_force_never_lowers(self):
        root = Path(private_dir()) / "p"
        r = cli(root.parent, "init", "--country", "usa", "--single",
                "--year", "2025", str(root))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = root / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            '">=0.27.0"', f'">={INSTALLED}"'))
        r = cli(root.parent, "init", "--country", "usa", "--single",
                "--year", "2025", "--force", str(root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_key(cfg), f">={INSTALLED}")

    def test_new_year_copies_and_raises(self):
        top = _years(extra={2025: 'requires_taxjson = ">=0.26.0"\n'
                                  'tobase_map = "../tobase.map"\n'})
        (top / "tobase.map").write_text("")
        r = cli(top, "new-year", "2026")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_key(top / "2026" / "taxjson.toml"), ">=0.27.1")
        # A higher value is copied as it is.
        top = _years(extra={2025: f'requires_taxjson = ">={INSTALLED}"\n'})
        r = cli(top, "new-year", "2026")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_key(top / "2026" / "taxjson.toml"),
                         f">={INSTALLED}")

    def test_migrate_sets_the_layout_it_migrated_to(self):
        # Per-year tobase.map copies -> one shared file: 0.27.1 each.
        top = _years(tobase="")
        r = cli(top, "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for y in (2024, 2025):
            self.assertEqual(_key(top / str(y) / "taxjson.toml"),
                             ">=0.27.1", y)
        self.assertIn('requires_taxjson = ">=0.27.1"', r.stdout)
        # Nothing else to migrate: a single folder is in the .tt
        # missing-history form (0.27.0).
        root = _single(country="usa")
        r = cli(root, "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_key(root / "taxjson.toml"), ">=0.27.0")
        # A dry run writes nothing.
        root = _single(country="usa")
        cli(root, "migrate", "--dry-run")
        self.assertIsNone(_key(root / "taxjson.toml"))

    def test_migrate_never_lowers(self):
        root = _single(f'requires_taxjson = ">={INSTALLED}"\n')
        r = cli(root, "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_key(root / "taxjson.toml"), f">={INSTALLED}")

    def test_update_tobase_map_write_raises(self):
        root = _single()
        r = cli(root, "update-tobase-map", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((root / "tobase.map").is_file())
        self.assertEqual(_key(root / "taxjson.toml"), ">=0.27.0")
        # The dry run writes nothing.
        root = _single()
        cli(root, "update-tobase-map")
        self.assertIsNone(_key(root / "taxjson.toml"))

    def test_align_never_lowers(self):
        top = _years(extra={2024: f'requires_taxjson = ">={INSTALLED}"\n',
                            2025: 'requires_taxjson = ">=0.26.0"\n'
                                  'province = "ON"\n'})
        r = cli(top / "2024", "align", "--from", "2025", "--write", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = top / "2024" / "taxjson.toml"
        self.assertIn('province = "ON"', cfg.read_text())
        self.assertEqual(_key(cfg), f">={INSTALLED}")


if __name__ == "__main__":
    unittest.main()
