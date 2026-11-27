"""TJ-5368: the elections command and file are renamed (v3.0.0).

`tjs elect` is `tjs elections` and an account's elections file
inputs/<account>/manifest.json is inputs/<account>/elections.json. The
old command name is refused (exit 2) with the command line under the new
name; a manifest.json in an open year's inputs stops every command that
reads the project until `tjs migrate` renames it; a filed year keeps its
frozen copy under the name it was frozen with, and that copy and the
shared elections.json are one file to every comparison of the two.

Every fixture is SYNTHETIC: `tjs init --demo`'s made-up exports and a
one-account project of made-up trades (ZZQ.TO).
"""
import json
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

from _support.cli_command_runs import _tjs

SPIN = "20240603-parnt-spnco-3509"      # the demo's spin-off (synthetic)
ELECTION = "taxable_deemed_dividend"    # the demo's saved election
_CA = ('[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
       'source_currencies = []\n[accounts.margin]\ntype = "taxable"\n')
_TRADES = "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 100 100 0\n"


def _ok(r, code=0):
    if r.returncode != code:
        raise AssertionError((r.returncode, r.stdout[-2000:],
                              r.stderr[-2000:]))
    return r


def _flat(r) -> str:
    return " ".join((r.stdout + r.stderr).split())


def _demo(tmp) -> Path:
    """`tjs init --demo` (exports shared in inputs/, the spin-off's
    election saved there): the 2024 year folder."""
    _ok(_tjs(None, "init", "--demo", str(Path(tmp) / "demo")))
    return Path(tmp) / "demo" / "2024"


def _shared(year: Path, name: str = "elections.json") -> Path:
    return year.parent / "inputs" / "margin" / name


def _one_folder(tmp) -> Path:
    """A one-account project whose inputs/ is its own."""
    root = Path(tmp) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_CA)
    (root / "inputs" / "margin" / "trades.tt").write_text(_TRADES)
    return root


class TestTheOldCommandNameIsRefused(unittest.TestCase):
    """`tjs elect` is no alias: exit 2, the new name, and the same
    command line under it to copy; nothing runs."""

    def test_refused_with_the_command_line_under_the_new_name(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            before = _shared(year).read_bytes()
            r = _ok(_tjs(year, "elect", "margin", "--set",
                         f"{SPIN}=ignore"), 2)
            self.assertEqual(r.stdout, "")
            lines = r.stderr.splitlines()
            self.assertEqual(lines[0], "tjs: error: tjs elect was renamed: "
                                       "tjs elections")
            self.assertEqual(lines[1], "  " + shlex.join(
                ["tjs", "-C", str(year), "elections", "margin", "--set",
                 f"{SPIN}=ignore"]))
            self.assertEqual(len(lines), 2, r.stderr)
            self.assertEqual(_shared(year).read_bytes(), before)

    def test_shown_the_ready_line_is_flush_left_and_whole(self):
        # A ready line is a command to copy: never wrapped, even past
        # the width; outside a project too (nothing is read).
        note = "note=" + "z" * 110
        with tempfile.TemporaryDirectory() as td:
            r = _ok(_tjs(None, "elect", "--set", "x=a b", "--hint", note,
                         cwd=td, env_extra={"TAXJSON_WIDTH": "100"}), 2)
            self.assertEqual(r.stderr.splitlines(), [
                "Error: tjs elect was renamed: tjs elections",
                f"tjs elections --set 'x=a b' --hint {note}"])

    def test_the_new_name_is_the_command(self):
        r = _ok(_tjs(None, "elections", "--help"))
        self.assertIn("usage: tjs elections", r.stdout)
        from taxjson.cli.parser import _COMMAND_GROUPS, RENAMED_COMMANDS
        names = {c for _g, cmds in _COMMAND_GROUPS for c in cmds}
        self.assertIn("elections", names)
        self.assertNotIn("elect", names)
        self.assertEqual(RENAMED_COMMANDS["elect"], "elections")


class TestElectionsTakesWhatElectTook(unittest.TestCase):
    """The list, --json (schema `elections`), --reset, --pending
    (schema `elections --pending`) and --set, on elections.json."""

    def test_list_json_reset_pending_and_set(self):
        from taxjson.lib.json_contract import SCHEMAS
        self.assertEqual((SCHEMAS.get("elections"),
                          SCHEMAS.get("elections --pending")), (1, 2))
        self.assertNotIn("elect", SCHEMAS)
        self.assertNotIn("elect --pending", SCHEMAS)
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            r = _ok(_tjs(year, "elections"))
            self.assertIn("margin — ../inputs/margin/elections.json",
                          r.stdout)
            self.assertIn(f"{SPIN}: {ELECTION}", r.stdout)
            doc = json.loads(_ok(_tjs(year, "elections", "margin",
                                      "--json")).stdout)
            self.assertEqual(doc["schema_version"], SCHEMAS["elections"])
            self.assertEqual(doc["accounts"]["margin"][SPIN]["election"],
                             ELECTION)
            r = _ok(_tjs(year, "elections", "margin", "--reset", "--event",
                         SPIN))
            self.assertIn("Cleared 1 election(s) from "
                          "../inputs/margin/elections.json.", r.stdout)
            _ok(_tjs(year, "run", "--no-input"), 3)
            doc = json.loads(_ok(_tjs(year, "elections", "--pending",
                                      "--json")).stdout)
            self.assertEqual(doc["schema_version"],
                             SCHEMAS["elections --pending"])
            self.assertEqual([e["event_id"] for e in
                              doc["accounts"]["margin"]["pending"]], [SPIN])
            # schema_version 2: the key `manifest` is `elections_file`.
            self.assertNotIn("manifest", doc["accounts"]["margin"])
            self.assertTrue(doc["accounts"]["margin"]["elections_file"]
                            .endswith("inputs/margin/elections.json"))
            said = _flat(_ok(_tjs(year, "elections", "--pending")))
            self.assertIn(f"tjs elections margin --set {SPIN}=OPTION", said)
            _ok(_tjs(year, "elections", "margin", "--set",
                     f"{SPIN}={ELECTION}"))
            saved = json.loads(_shared(year).read_text())["elections"]
            self.assertEqual(saved[SPIN]["election"], ELECTION)
            _ok(_tjs(year, "run", "--no-input"))
            self.assertEqual(sorted(year.parent.rglob("manifest.json")), [])
            self.assertEqual(sorted(year.parent.rglob(".manifest.json.lock")),
                             [])

    def test_the_demo_writes_elections_json(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            self.assertTrue(_shared(year).is_file())
            self.assertIn(SPIN, json.loads(_shared(year).read_text())
                          ["elections"])
            self.assertEqual(sorted(year.parent.rglob("manifest.json")), [])


class TestTheOldFileStopsAnOpenYear(unittest.TestCase):
    """A manifest.json in an open year's inputs (the shared folder, the
    project's own inputs/) is refused, exit 2, naming `tjs migrate`."""

    def test_the_shared_folder(self):
        # Books built before the upgrade: the commands reading them stop
        # too, not only the run.
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _ok(_tjs(year, "run", "--no-input"))
            _shared(year).rename(_shared(year, "manifest.json"))
            for args in (("run", "--no-input"), ("elections",), ("sum",),
                         ("spinoffs",)):
                said = _flat(_ok(_tjs(year, *args), 2))
                self.assertIn("../inputs/margin/manifest.json: the elections "
                              "file is now named elections.json", said)
                self.assertIn("Run `tjs migrate` to rename it", said)
                self.assertIn("Nothing was run.", said)

    def test_the_projects_own_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = _one_folder(td)
            (root / "inputs" / "margin" / "manifest.json").write_text(
                '{"elections": {}}\n')
            said = _flat(_ok(_tjs(root, "run", "--no-input"), 2))
            self.assertIn("inputs/margin/manifest.json: the elections file "
                          "is now named elections.json", said)
            self.assertIn("`tjs migrate`", said)

    def test_both_names_in_one_folder_are_named_both(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            shutil.copy(_shared(year), _shared(year, "manifest.json"))
            said = _flat(_ok(_tjs(year, "run", "--no-input"), 2))
            self.assertIn("../inputs/margin/manifest.json and elections.json "
                          "beside it: elections under two names", said)


class TestMigrateRenamesTheFile(unittest.TestCase):
    """`tjs migrate` renames each open manifest.json elections.json, the
    shared folder's included; `--dry-run` shows the plan."""

    def test_dry_run_then_write_in_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            text = _shared(year).read_bytes()
            old = _shared(year, "manifest.json")
            _shared(year).rename(old)
            lock = _shared(year, ".manifest.json.lock")
            lock.write_text("")
            r = _ok(_tjs(year.parent, "migrate", "--dry-run"))
            self.assertIn("would rename inputs/margin/manifest.json -> "
                          "elections.json", r.stdout)
            self.assertIn("dry run: nothing was written", r.stdout)
            self.assertTrue(old.is_file())
            self.assertFalse(_shared(year).exists())
            r = _ok(_tjs(year, "migrate"))
            self.assertIn("renamed ../inputs/margin/manifest.json -> "
                          "elections.json", r.stdout)
            self.assertEqual(_shared(year).read_bytes(), text)
            self.assertFalse(old.exists())
            self.assertFalse(lock.exists())
            _ok(_tjs(year, "run", "--no-input"))

    def test_the_projects_own_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = _one_folder(td)
            acct = root / "inputs" / "margin"
            (acct / "manifest.json").write_text('{"elections": {}}\n')
            r = _ok(_tjs(root, "migrate"))
            self.assertIn("renamed inputs/margin/manifest.json -> "
                          "elections.json", r.stdout)
            self.assertEqual((acct / "elections.json").read_text(),
                             '{"elections": {}}\n')
            _ok(_tjs(root, "run", "--no-input"))

    def test_the_same_file_under_both_names(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            shutil.copy(_shared(year), _shared(year, "manifest.json"))
            r = _ok(_tjs(year, "migrate"))
            self.assertIn("renamed ../inputs/margin/manifest.json -> "
                          "manifest.json.migrated (the same as the "
                          "elections.json beside it)", r.stdout)
            self.assertTrue(_shared(year, "manifest.json.migrated").is_file())
            self.assertFalse(_shared(year, "manifest.json").exists())
            _ok(_tjs(year, "run", "--no-input"))

    def test_two_files_that_differ_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            old = _shared(year, "manifest.json")
            shutil.copy(_shared(year), old)
            _shared(year).write_text('{"elections": {}}\n')
            before = (old.read_bytes(), _shared(year).read_bytes())
            said = _flat(_ok(_tjs(year, "migrate"), 2))
            self.assertIn("an elections file cannot be renamed as it is: "
                          "../inputs/margin/manifest.json: elections.json of "
                          "other contents beside it: keep the one that "
                          "holds your elections, delete the other", said)
            self.assertIn("Nothing was changed.", said)
            self.assertEqual((old.read_bytes(), _shared(year).read_bytes()),
                             before)


class TestAFiledYearKeepsItsFrozenName(unittest.TestCase):
    """A year closed before v3.0.0 froze its elections as
    inputs/<account>/manifest.json, its lock's input digests keyed by
    that name. It is never touched: its run, `tjs elections` and
    check-filed read that copy; `tjs migrate` renames only the shared
    file; and the two names are one file to every comparison — no
    drift, no changed input, no late election, no
    `shared_exports_changed` from the rename alone, while an election
    saved later in the shared elections.json is still named."""

    def _closed_before_the_rename(self, td) -> Path:
        year = _demo(td)
        _ok(_tjs(year, "run", "--no-input"))
        _ok(_tjs(year, "close-year", "--yes"))
        frozen = year / "inputs" / "margin"
        (frozen / "elections.json").rename(frozen / "manifest.json")
        lock = year / "filed" / "2024.json"
        text = lock.read_text()
        self.assertEqual(text.count('"inputs/margin/elections.json"'), 1)
        lock.write_text(text.replace('"inputs/margin/elections.json"',
                                     '"inputs/margin/manifest.json"'))
        _shared(year).rename(_shared(year, "manifest.json"))
        return year

    def _shared_changed(self, year: Path) -> list:
        doc = json.loads(_ok(_tjs(year.parent, "years", "--json")).stdout)
        y = next(x for x in doc["years"] if x["year"] == 2024)
        return y["shared_exports_changed"]

    def test_the_frozen_copy_is_read_and_the_rename_moves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            year = self._closed_before_the_rename(td)
            frozen = year / "inputs" / "margin" / "manifest.json"
            # Its run reads the frozen copy: the election is made (no
            # exit 3) and the figures are the filed ones.
            r = _ok(_tjs(year, "run", "--no-input"))
            self.assertNotIn("DRIFTED", r.stdout + r.stderr)
            r = _ok(_tjs(year, "elections"))
            self.assertIn("margin — inputs/margin/manifest.json", r.stdout)
            r = _ok(_tjs(year.parent, "migrate", "--dry-run"))
            self.assertIn("would rename inputs/margin/manifest.json -> "
                          "elections.json", r.stdout)
            self.assertIn("Not changed: 2024 reads its own frozen copy",
                          r.stdout)
            _ok(_tjs(year.parent, "migrate"))
            self.assertTrue(_shared(year).is_file())
            self.assertFalse(_shared(year, "manifest.json").exists())
            self.assertTrue(frozen.is_file())
            self.assertFalse(frozen.with_name("elections.json").exists())
            r = _ok(_tjs(year, "run", "--no-input"))
            self.assertNotIn("DRIFTED", r.stdout + r.stderr)
            r = _ok(_tjs(year, "check-filed"))
            said = _flat(r)
            self.assertIn("filed 2024: OK", said)
            self.assertNotIn("holds a decision", said)
            self.assertNotIn("changed since 2024 was closed", said)
            self.assertEqual(self._shared_changed(year), [])
            # An election saved later in the shared file is still one
            # the filed year's copy lacks.
            doc = json.loads(_shared(year).read_text())
            doc["elections"]["ZZQ.TO-2024-03-01-merger"] = {
                "summary": "ZZQ.TO merger 2024-03-01", "election": "taxable"}
            _shared(year).write_text(json.dumps(doc))
            said = _flat(_ok(_tjs(year, "check-filed")))
            self.assertIn("note: filed 2024: ../inputs/margin/elections.json "
                          "(1 entry) in the shared folder holds a decision "
                          "on 2024", said)
            self.assertEqual(self._shared_changed(year),
                             ["inputs/margin/elections.json"])


class TestALockRecordedUnderTheOldName(unittest.TestCase):
    """A fingerprint (a lock's frozen inputs, a run's record) keyed by
    manifest.json and one keyed by elections.json, the same bytes: no
    change; other bytes: a change, under the new name."""

    def test_a_rename_alone_is_no_change(self):
        from taxjson.lib import checklist as CL
        a, b = "a" * 64, "b" * 64
        before = {"inputs/margin/manifest.json": a, "inputs/margin/x.csv": b}
        now = {"inputs/margin/elections.json": a, "inputs/margin/x.csv": b}
        self.assertEqual(CL._fingerprint_diff(before, now), "")
        self.assertEqual(CL.fingerprint_delta(before, now), ([], [], []))
        self.assertEqual(CL.fingerprint_changes(before, now), [])
        moved = dict(now, **{"inputs/margin/elections.json": b})
        self.assertEqual(CL.fingerprint_delta(before, moved),
                         (["inputs/margin/elections.json"], [], []))


if __name__ == "__main__":
    unittest.main()
