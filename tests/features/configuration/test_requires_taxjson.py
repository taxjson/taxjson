"""`[settings] requires_taxjson`: a project records the oldest taxjson its
layout needs, and an older taxjson refuses every command on it with one
line (lib/requires). init / new-year / migrate / align write the right
minimum and never lower it; a malformed value is a clear error; an
unknown [settings] key is one warning line in every command.

Synthetic projects only (made-up accounts, no inputs).

Formerly: test_fix_requires
"""
import re
import unittest
from pathlib import Path
from unittest import mock

import _paths
from _tmpfiles import private_dir
from tax_rules.dual import cli

from taxjson.lib import requires as REQ

INSTALLED = REQ.running_version() or "0.0.0"
HAVE = REQ.version_tuple(INSTALLED) or (0, 0, 0)
# What this code runs: its install's version, or the newest feature it
# reads when that is newer (a development tree between releases).
TOP = max(HAVE, REQ.newest_read())
CURRENT = REQ.shown(TOP)
NEWER = f"{TOP[0]}.{TOP[1]}.{TOP[2] + 1}"
# What init and migrate write now: the newest layout feature's release.
LAYOUT = REQ.spec(REQ.newest_read())
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
        # A release of its day: the features up to 0.27.0.
        old = tuple(f for f in REQ.FEATURES
                    if REQ.version_tuple(f.version) <= (0, 27, 0))
        st = {"requires_taxjson": ">=0.27.1"}
        with mock.patch.object(REQ, "FEATURES", old), \
                mock.patch.object(REQ, "running_version",
                                  return_value="0.27.0"):
            why = REQ.problem(st)
        self.assertEqual(why, "this project needs taxjson 0.27.1 or newer "
                              "(installed: 0.27.0) — " + UPGRADE)
        for have in ("0.27.1", "0.28.0", "1.0.0", "0.27.1.dev3+gabc"):
            with mock.patch.object(REQ, "FEATURES", old), \
                    mock.patch.object(REQ, "running_version",
                                      return_value=have):
                self.assertIsNone(REQ.problem(st), have)
        # No key, or a version that cannot be read: runs.
        self.assertIsNone(REQ.problem({}))
        with mock.patch.object(REQ, "running_version", return_value=None):
            self.assertIsNone(REQ.problem(st))

    def test_the_development_tree_runs_the_elections_layout(self):
        # Between releases the install reports the last release (the
        # version commit is made at the release): this tree reads
        # elections.json, so it runs a project asking for 3.0.0; v2.2.0,
        # which reads none of it, refuses that project.
        self.assertEqual(REQ._BY_NAME["elections_json"].version, "3.0.0")
        st = {"requires_taxjson": ">=3.0.0"}
        with mock.patch.object(REQ, "running_version",
                               return_value="2.2.0"):
            self.assertIsNone(REQ.problem(st))
            v220 = tuple(f for f in REQ.FEATURES
                         if f.name != "elections_json")
            with mock.patch.object(REQ, "FEATURES", v220):
                self.assertEqual(
                    REQ.problem(st), "this project needs taxjson 3.0.0 or "
                    "newer (installed: 2.2.0) — " + UPGRADE)
        # Released, the version decides: 3.0.0 runs it, 3.0.1 asked for
        # is refused.
        with mock.patch.object(REQ, "running_version",
                               return_value="3.0.0"):
            self.assertIsNone(REQ.problem(st))
            self.assertIn("needs taxjson 3.0.1 or newer", REQ.problem(
                {"requires_taxjson": ">=3.0.1"}))

    def test_release_script_reads_every_feature_line(self):
        # scripts/release.sh --prepare reads the FEATURES lines a release
        # adds with this pattern (one line each: name, version).
        src = Path(REQ.__file__).read_text(encoding="utf-8")
        read = re.findall(r'^ *Feature\("([a-z_]+)", "([0-9.]+)"', src, re.M)
        self.assertEqual(read, [(f.name, f.version) for f in REQ.FEATURES])
        script = (_paths.REPO / "scripts" / "release.sh").read_text(
            encoding="utf-8")
        self.assertIn(r"""FEAT_RE='s/^ *Feature\("([a-z_]+)", "([0-9.]+)".*/""",
                      script)

    def test_release_script_flags_only_a_feature_new_since_the_tag(self):
        # --prepare compares the names of the tag's FEATURES with HEAD's:
        # a new one naming another release than the one prepared is
        # flagged; a released feature's line, edited, is not.
        import shutil
        import subprocess
        if not (shutil.which("sed") and shutil.which("awk")):
            self.skipTest("no sed / awk")
        script = (_paths.REPO / "scripts" / "release.sh").read_text(
            encoding="utf-8")
        pat = re.search(r"FEAT_RE='([^']*)'", script)[1]
        prog = re.search(r"awk -v v=\"\$V\" -v old=\"\$OLDF\" '([^']*)'",
                         script)[1]

        def feats(text):
            return subprocess.run(["sed", "-nE", pat], input=text, text=True,
                                  capture_output=True, check=True).stdout

        def flagged(tag, head, v):
            old = " ".join(ln.split()[0] for ln in feats(tag).splitlines())
            return subprocess.run(["awk", "-v", f"v={v}", "-v", f"old={old}",
                                   prog], input=feats(head), text=True,
                                  capture_output=True, check=True).stdout
        tag = ('    Feature("shared_exports", "0.26.0"),\n'
               '    Feature("tobase_map", "0.27.1"),\n')
        head = tag.replace('"0.27.1"', '"0.27.2"') + (
            '    Feature("new_one", "9.0.0"),\n')
        self.assertEqual(flagged(tag, head, "9.0.0"), "")
        self.assertEqual(flagged(tag, head, "8.1.0"), "new_one 9.0.0\n")
        self.assertEqual(flagged(tag, tag, "8.1.0"), "")

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
        for cmd in (("sum",), ("checklist", "--run",), ("format", "--check"),
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
                (("--country", "canada"), "ca/2025", LAYOUT),
                (("--country", "usa"), "us/2025", LAYOUT),
                (("--country", "canada", "--single"), "cas", LAYOUT),
                (("--country", "usa", "--single"), "uss", LAYOUT)):
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
            f'"{LAYOUT}"', f'">={CURRENT}"'))
        r = cli(root.parent, "init", "--country", "usa", "--single",
                "--year", "2025", "--force", str(root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_key(cfg), f">={CURRENT}")

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
        # Per-year tobase.map copies -> one shared file: 0.27.1 each (no
        # elections.json: not 3.0.0, each feature its own release).
        top = _years(tobase="")
        r = cli(top, "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for y in (2024, 2025):
            self.assertEqual(_key(top / str(y) / "taxjson.toml"),
                             ">=0.27.1", y)
        self.assertIn('requires_taxjson = ">=0.27.1"', r.stdout)
        # Nothing else to migrate: a single folder is in the .tt
        # missing-history form (0.27.0); one holding an elections.json
        # asks for the release that reads it.
        root = _single(country="usa")
        r = cli(root, "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_key(root / "taxjson.toml"), ">=0.27.0")
        root = _single(country="usa")
        (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
        (root / "inputs" / "margin" / "elections.json").write_text(
            '{"elections": {}}\n')
        r = cli(root, "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_key(root / "taxjson.toml"), LAYOUT)
        # A dry run writes nothing.
        root = _single(country="usa")
        cli(root, "migrate", "--dry-run")
        self.assertIsNone(_key(root / "taxjson.toml"))

    def test_migrate_never_lowers(self):
        root = _single(f'requires_taxjson = ">={CURRENT}"\n')
        r = cli(root, "migrate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_key(root / "taxjson.toml"), f">={CURRENT}")

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

    def test_new_year_legacy_tobase_copy_raises_in_the_staged_copy(self):
        # A year from before v0.27.1 with its own tobase.map passes it
        # on; the copy's requires_taxjson covers it, written with the
        # rest of the folder (no staging folder left behind).
        top = _years(tobase="")
        r = cli(top, "new-year", "2026")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((top / "2026" / "tobase.map").is_file())
        self.assertEqual(_key(top / "2026" / "taxjson.toml"), ">=0.27.0")
        self.assertEqual(sorted(p.name for p in top.iterdir()
                                if p.name.startswith(".")), [])

    def test_align_raises_for_a_layout_key_brought_over(self):
        top = _years(extra={2025: 'tobase_map = "../tobase.map"\n'})
        (top / "tobase.map").write_text("")
        r = cli(top / "2024", "align", "--from", "2025", "--write", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = top / "2024" / "taxjson.toml"
        self.assertIn('tobase_map = "../tobase.map"', cfg.read_text())
        self.assertEqual(_key(cfg), ">=0.27.1")


if __name__ == "__main__":
    unittest.main()
