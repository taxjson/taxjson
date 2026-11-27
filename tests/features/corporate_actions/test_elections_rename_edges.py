"""TJ-5368's edges (review rv_e1; backlog TJ-5410 to TJ-5419): the old
command name where help and chains read it, every odd thing at the old
file name, a linked account folder, an older work/ copy, a filed year
holding both names, `tjs years` before the rename, the version a
migrated project asks for, and no user-facing text naming the old
command outside the history pages.

Every fixture is SYNTHETIC: `tjs init --demo`'s made-up exports and a
one-account project of made-up trades (ZZQ.TO).
"""
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import _paths
from _support.cli_command_runs import _tjs
from _support.suite_history_pages import history_releases, renamed_ok

_CA = ('[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
       'source_currencies = []\n[accounts.margin]\ntype = "taxable"\n')
_TWO = _CA + '[accounts.cash]\ntype = "taxable"\n'
_TRADES = "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 100 100 0\n"
_EMPTY = '{"elections": {}}\n'


def _ok(r, code=0):
    if r.returncode != code:
        raise AssertionError((r.returncode, r.stdout[-2000:],
                              r.stderr[-2000:]))
    return r


def _flat(r) -> str:
    return " ".join((r.stdout + r.stderr).split())


def _demo(tmp) -> Path:
    _ok(_tjs(None, "init", "--demo", str(Path(tmp) / "demo")))
    return Path(tmp) / "demo" / "2024"


def _shared(year: Path, name: str = "elections.json") -> Path:
    return year.parent / "inputs" / "margin" / name


def _project(tmp, toml=_CA, accounts=("margin",)) -> Path:
    root = Path(tmp) / "p"
    root.mkdir()
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "trades.tt").write_text(_TRADES)
    (root / "taxjson.toml").write_text(toml)
    return root


def _key(root: Path):
    m = re.search(r'^requires_taxjson\s*=\s*"([^"]*)"',
                  (root / "taxjson.toml").read_text(), re.M)
    return m[1] if m else None


class TestTheOldNameInHelpAndChains(unittest.TestCase):

    def test_help_elect_names_the_new_command(self):
        r = _ok(_tjs(None, "help", "elect"), 2)
        self.assertIn("tjs help: error: tjs elect was renamed: tjs "
                      "elections", r.stderr)
        self.assertIn("\n  tjs help elections", r.stderr)
        self.assertNotIn("no such command", r.stderr)

    def test_a_chained_elect_names_the_new_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = _ok(_tjs(root, "sum", "--", "elect", "margin", "--",
                         "checklist"), 2)
            self.assertIn("tjs elect was renamed: tjs elections", r.stderr)
            # The ready line is the whole chain, the new name in it.
            self.assertRegex(r.stderr, r"(?m)^  tjs -C \S+ sum -- elections "
                                       r"margin -- checklist$")
            self.assertNotIn("not a valid command in this chain", r.stderr)

    def test_the_old_name_after_a_command_without_a_separator(self):
        # It splits as a command name does: the new name, ready.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = _ok(_tjs(root, "years", "elect", "margin"), 2)
            self.assertIn("tjs elect was renamed: tjs elections", r.stderr)
            self.assertRegex(r.stderr, r"(?m)^  tjs -C \S+ years elections "
                                       r"margin$")
            # A later segment that is no command: shown as typed.
            r = _ok(_tjs(root, "sum", "--", "bogus", "x"), 1)
            self.assertIn("'bogus x' is not a valid command in this chain",
                          r.stderr)


class TestOddThingsAtTheOldName(unittest.TestCase):
    """A link, a link to nothing, a file that cannot be read and a
    folder at inputs/<account>/manifest.json: each refused by the run
    naming migrate; migrate renames the link as a link (the next
    command names its problem) and refuses a folder; with both names, a
    file that cannot be read is its own refusal."""

    def test_a_link_is_refused_and_renamed_as_a_link(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            acct = root / "inputs" / "margin"
            (root / "kept.json").write_text(_EMPTY)
            os.symlink("../../kept.json", acct / "manifest.json")
            self.assertIn("the elections file is now named elections.json",
                          _flat(_ok(_tjs(root, "run", "--no-input"), 2)))
            _ok(_tjs(root, "migrate"))
            self.assertTrue((acct / "elections.json").is_symlink())
            self.assertEqual(os.readlink(acct / "elections.json"),
                             "../../kept.json")
            _ok(_tjs(root, "run", "--no-input"))

    def test_a_link_to_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            acct = root / "inputs" / "margin"
            os.symlink("gone.json", acct / "manifest.json")
            self.assertIn("the elections file is now named elections.json",
                          _flat(_ok(_tjs(root, "run", "--no-input"), 2)))
            _ok(_tjs(root, "migrate"))
            self.assertTrue((acct / "elections.json").is_symlink())
            said = _flat(_ok(_tjs(root, "run", "--no-input"), 2))
            self.assertIn("elections.json is a symlink to gone.json, which "
                          "does not exist", said)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_file_that_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            acct = root / "inputs" / "margin"
            old = acct / "manifest.json"
            old.write_text(_EMPTY)
            old.chmod(0)
            self.addCleanup(lambda: [p.chmod(0o600) for p in (
                old, acct / "elections.json") if p.exists() or
                os.path.lexists(p)])
            self.assertIn("the elections file is now named elections.json",
                          _flat(_ok(_tjs(root, "run", "--no-input"), 2)))
            # Beside an elections.json: its own refusal, never "other
            # contents".
            (acct / "elections.json").write_text(_EMPTY)
            said = _flat(_ok(_tjs(root, "migrate"), 2))
            self.assertIn("inputs/margin/manifest.json: cannot be read "
                          "(Permission denied), with the other name beside "
                          "it", said)
            self.assertNotIn("other contents", said)
            (acct / "elections.json").unlink()
            _ok(_tjs(root, "migrate"))
            self.assertTrue(os.path.lexists(acct / "elections.json"))

    def test_a_folder_is_refused_not_renamed(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            acct = root / "inputs" / "margin"
            (acct / "manifest.json").mkdir()
            said = _flat(_ok(_tjs(root, "run", "--no-input"), 2))
            self.assertIn("inputs/margin/manifest.json: a folder at the "
                          "elections file's old name", said)
            self.assertIn("move what it holds out (elections go in "
                          "elections.json), then remove it", said)
            self.assertNotIn("rename", said)
            said = _flat(_ok(_tjs(root, "migrate"), 2))
            self.assertIn("inputs/margin/manifest.json: a folder, not an "
                          "elections file", said)
            self.assertIn("Nothing was changed.", said)
            self.assertTrue((acct / "manifest.json").is_dir())
            self.assertFalse(os.path.lexists(acct / "elections.json"))


class TestAFolderAtTheOldName(unittest.TestCase):
    """A folder at inputs/<account>/manifest.json is no elections file:
    the run never calls it one of two names, nor one migrate moves."""

    def test_beside_elections_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            acct = root / "inputs" / "margin"
            (acct / "manifest.json").mkdir()
            (acct / "elections.json").write_text(_EMPTY)
            said = _flat(_ok(_tjs(root, "run", "--no-input"), 2))
            self.assertIn("inputs/margin/manifest.json: a folder at the "
                          "elections file's old name", said)
            self.assertNotIn("two names", said)
            self.assertNotIn("when the two are the same", said)

    def test_among_the_files_migrate_renames(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, _TWO, ("margin", "cash"))
            m, c = root / "inputs" / "margin", root / "inputs" / "cash"
            (m / "manifest.json").write_text(_EMPTY)
            (m / "elections.json").write_text(_EMPTY)
            (c / "manifest.json").mkdir()
            said = _flat(_ok(_tjs(root, "run", "--no-input"), 2))
            self.assertIn("inputs/margin/manifest.json and elections.json "
                          "beside it: elections under two names", said)
            self.assertNotIn("moves inputs/cash", said)
            self.assertIn("inputs/cash/manifest.json: a folder at the old "
                          "name, no elections file: move what it holds out",
                          said)


class TestMigratePlansEveryAccount(unittest.TestCase):

    def test_every_account_is_named_and_the_dry_run_shows_all(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, _TWO, ("margin", "cash"))
            m, r = root / "inputs" / "margin", root / "inputs" / "cash"
            (m / "manifest.json").write_text(_EMPTY)
            (m / "elections.json").write_text('{"elections": {"a": {}}}\n')
            (r / "manifest.json").write_text(_EMPTY)
            said = _flat(_ok(_tjs(root, "run", "--no-input"), 2))
            self.assertIn("inputs/margin/manifest.json and elections.json "
                          "beside it: elections under two names", said)
            self.assertIn("and moves inputs/cash/manifest.json too", said)
            r_ = _ok(_tjs(root, "migrate", "--dry-run"), 2)
            self.assertIn("would rename inputs/cash/manifest.json -> "
                          "elections.json", r_.stdout)
            self.assertIn("refused: inputs/margin/manifest.json: "
                          "elections.json of other contents beside it",
                          r_.stdout)
            self.assertIn("each file marked refused above", r_.stderr)
            self.assertTrue((r / "manifest.json").is_file())


class TestALinkedAccountFolder(unittest.TestCase):
    """An account folder that is a link inside the project (load_config
    allows it): migrate renames through it, as the refusal looks."""

    def test_migrate_renames_through_the_link(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, accounts=())
            data = root / "data" / "margin"
            data.mkdir(parents=True)
            (data / "trades.tt").write_text(_TRADES)
            (data / "manifest.json").write_text(_EMPTY)
            (root / "inputs").mkdir()
            os.symlink("../data/margin", root / "inputs" / "margin")
            self.assertIn("inputs/margin/manifest.json: the elections file "
                          "is now named elections.json",
                          _flat(_ok(_tjs(root, "run", "--no-input"), 2)))
            r = _ok(_tjs(root, "migrate"))
            self.assertIn("renamed inputs/margin/manifest.json -> "
                          "elections.json", r.stdout)
            self.assertTrue((data / "elections.json").is_file())
            self.assertTrue((root / "inputs" / "margin").is_symlink())
            _ok(_tjs(root, "run", "--no-input"))


class TestAnOlderWorkCopy(unittest.TestCase):
    """work/<account>_manifest.json (a layout older still) is read by no
    command: every one refuses it naming migrate, which moves it into
    inputs/<account>/ (or beside itself as .migrated when the account
    has its elections file there)."""

    def test_moved_into_the_inputs_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "work").mkdir()
            legacy = root / "work" / "margin_manifest.json"
            legacy.write_text(_EMPTY)
            said = _flat(_ok(_tjs(root, "run", "--no-input"), 2))
            self.assertIn("work/margin_manifest.json: the elections file is "
                          "now named elections.json", said)
            self.assertIn("a work/ copy moves into inputs/<account>/", said)
            r = _ok(_tjs(root, "migrate"))
            self.assertIn("moved work/margin_manifest.json -> "
                          "inputs/margin/elections.json", r.stdout)
            self.assertEqual((root / "inputs" / "margin" / "elections.json")
                             .read_text(), _EMPTY)
            self.assertFalse(legacy.exists())
            _ok(_tjs(root, "run", "--no-input"))

    def test_kept_aside_when_the_inputs_folder_has_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "elections.json").write_text(_EMPTY)
            (root / "work").mkdir()
            (root / "work" / "margin_manifest.json").write_text(
                '{"elections": {"old": {}}}\n')
            _ok(_tjs(root, "run", "--no-input"), 2)
            r = _ok(_tjs(root, "migrate"))
            self.assertIn("moved work/margin_manifest.json -> "
                          "margin_manifest.json.migrated", r.stdout)
            self.assertTrue((root / "work" /
                             "margin_manifest.json.migrated").is_file())
            _ok(_tjs(root, "run", "--no-input"))


class TestAFiledYearHoldingBothNames(unittest.TestCase):
    """A year filed before v3.0.0 reads its frozen manifest.json; a
    corrected copy put in beside it as elections.json is refused naming
    both, never chosen without a word; the advice names the frozen
    name."""

    def test_refused_naming_both(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _ok(_tjs(year, "run", "--no-input"))
            _ok(_tjs(year, "close-year", "--yes"))
            frozen = year / "inputs" / "margin"
            (frozen / "elections.json").rename(frozen / "manifest.json")
            lock = year / "filed" / "2024.json"
            lock.write_text(lock.read_text().replace(
                '"inputs/margin/elections.json"',
                '"inputs/margin/manifest.json"'))
            _ok(_tjs(year.parent, "new-year", "2025"))
            r = _ok(_tjs(year.parent / "2025", "elections", "margin", "--set",
                         "20240603-parnt-spnco-3509=taxable_deemed_dividend"))
            self.assertIn("put the corrected file in its inputs/<account>/ "
                          "(the elections as manifest.json, the name it was "
                          "filed under)", _flat(r))
            shutil.copy(_shared(year), frozen / "elections.json")
            for args in (("run", "--no-input"), ("check-filed",)):
                said = _flat(_ok(_tjs(year, *args), 2))
                self.assertIn("inputs/margin/manifest.json and elections.json "
                              "beside it: a filed year's elections under two "
                              "names", said)


class TestAPartialLock(unittest.TestCase):
    """A lock taken before the year ended (close-year --force) records
    the inputs' digests and copies nothing: the year reads the shared
    folder, so its manifest.json is refused there as in any open year;
    after `tjs migrate` the digest keyed by the old name is the same
    file — check-filed passes, `tjs years` reports nothing."""

    def test_migrate_then_check_filed(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            top = year.parent
            _ok(_tjs(top, "new-year", "2025"))
            _ok(_tjs(top, "new-year", "2026"))
            y26 = top / "2026"
            _ok(_tjs(y26, "run", "--no-input"))
            _ok(_tjs(y26, "close-year", "--force", "--yes"))
            lock = y26 / "filed" / "2026.json"
            text = lock.read_text()
            self.assertIn('"inputs/margin/elections.json"', text)
            lock.write_text(text.replace('"inputs/margin/elections.json"',
                                         '"inputs/margin/manifest.json"'))
            _shared(year).rename(_shared(year, "manifest.json"))
            self.assertIn("../inputs/margin/manifest.json: the elections "
                          "file is now named elections.json",
                          _flat(_ok(_tjs(y26, "run", "--no-input"), 2)))
            _ok(_tjs(top, "migrate"))
            _ok(_tjs(y26, "run", "--no-input"))
            said = _flat(_ok(_tjs(y26, "check-filed")))
            self.assertIn("filed 2026: OK", said)
            doc = json.loads(_ok(_tjs(top, "years", "--json")).stdout)
            y = next(x for x in doc["years"] if x["year"] == 2026)
            self.assertEqual(y.get("shared_exports_changed") or [], [])
            self.assertIsNone(y["problem"])


class TestYearsBeforeTheRename(unittest.TestCase):

    def test_years_names_migrate_not_a_changed_input(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _ok(_tjs(year, "run", "--no-input"))
            _shared(year).rename(_shared(year, "manifest.json"))
            doc = json.loads(_ok(_tjs(year.parent, "years", "--json")).stdout)
            y = next(x for x in doc["years"] if x["year"] == 2024)
            self.assertIsNone(y["changed"])
            self.assertIsNone(y["stale"])
            self.assertIn("../inputs/margin/manifest.json: the elections file "
                          "is now named elections.json — run `tjs migrate`",
                          y["problem"])
            said = _flat(_ok(_tjs(year.parent, "years")))
            self.assertIn("2024: ../inputs/margin/manifest.json: the "
                          "elections file is now named", said)
            self.assertNotIn("INPUTS changed", said)
            said = _flat(_ok(_tjs(year.parent, "years", "--details")))
            self.assertIn("— run `tjs migrate`", said)

    def test_the_checklist_ends_the_headline_before_its_details(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "manifest.json").write_text(_EMPTY)
            said = _flat(_tjs(root, "checklist", "--run", "--only",
                              "configure"))
            self.assertIn("is now named elections.json. Run `tjs migrate`",
                          said)


class TestTheVersionAMigratedProjectAsks(unittest.TestCase):
    """Migrate and init write the release that first reads elections.json
    (lib/requires.FEATURES, 3.0.0): v2.2.0 and older refuse the project;
    this tree, whose install may still report the last release, runs it."""

    def test_migrate_writes_it_and_this_tree_runs_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "manifest.json").write_text(_EMPTY)
            _ok(_tjs(root, "migrate"))
            self.assertEqual(_key(root), ">=3.0.0")
            _ok(_tjs(root, "run", "--no-input"))


_SPIN_SET = "20240603-parnt-spnco-3509=taxable_deemed_dividend"


def _lower(d: Path, to: str = ">=0.27.1") -> None:
    """The project `d` as an older taxjson made it: requires_taxjson `to`."""
    p = d / "taxjson.toml"
    text, n = re.subn(r'^requires_taxjson\s*=\s*"[^"]*"',
                      f'requires_taxjson = "{to}"', p.read_text(), flags=re.M)
    assert n == 1, p
    p.write_text(text)


def _filed_with_manifest(year: Path) -> None:
    """`year` (the demo's) filed as v2.2.0 left it: its frozen elections
    as manifest.json, requires_taxjson below 3.0.0 (lowered before the
    close, which keeps it)."""
    _lower(year)
    _ok(_tjs(year, "run", "--no-input"))
    _ok(_tjs(year, "close-year", "--yes"))
    frozen = year / "inputs" / "margin"
    (frozen / "elections.json").rename(frozen / "manifest.json")
    lock = year / "filed" / f"{year.name}.json"
    lock.write_text(lock.read_text().replace(
        '"inputs/margin/elections.json"', '"inputs/margin/manifest.json"'))


class TestEveryElectionsWriterRaisesTheVersion(unittest.TestCase):
    """An elections.json saved in an open year raises requires_taxjson to
    3.0.0 in each project that reads it (lib/requires
    raise_for_elections, from corp_actions Manifest.update, the one
    writer: `tjs elections --set` / `--redo` / `--reset`, the run's
    prompt and auto-elections, taxjson-corp-actions): an older taxjson
    there would see no elections, and book a sheltered account's event
    by the zero default. A year frozen with manifest.json is not
    raised."""

    def test_set_raises_every_year_reading_the_shared_file(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            top = year.parent
            _ok(_tjs(top, "new-year", "2025"))
            for d in (year, top / "2025"):
                _lower(d)
            r = _ok(_tjs(top / "2025", "elections", "margin", "--set",
                         _SPIN_SET))
            self.assertEqual([_key(year), _key(top / "2025")],
                             [">=3.0.0", ">=3.0.0"])
            for y in (2024, 2025):
                self.assertIn('tjs elections: note: [settings] '
                              'requires_taxjson = ">=3.0.0" (the oldest '
                              'taxjson that reads elections.json) in '
                              f'{y}/taxjson.toml', _flat(r))
            r = _ok(_tjs(top / "2025", "elections", "margin", "--set",
                         _SPIN_SET))
            self.assertNotIn("requires_taxjson", r.stdout + r.stderr)

    def test_a_year_frozen_with_manifest_json_is_not_raised(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _filed_with_manifest(year)
            frozen = year / "inputs" / "margin"
            was = _key(year)
            self.assertNotEqual(was, ">=3.0.0")
            r = _ok(_tjs(year, "elections", "margin", "--set", _SPIN_SET))
            self.assertIn("inputs/margin/manifest.json", _flat(r))
            self.assertFalse((frozen / "elections.json").exists())
            self.assertEqual(_key(year), was)
            self.assertNotIn("requires_taxjson", r.stdout + r.stderr)

    def test_the_writer_itself(self):
        # lib/elections_save `update`: every command saves through it
        # (the corp tool's run prompt and auto-elections too); no other
        # module of the package saves an elections file.
        from taxjson.lib import elections_save
        from taxjson.lib.corp_actions import ManifestError
        src = _paths.REPO / "src" / "taxjson"
        self.assertEqual(sorted(
            p.relative_to(src).as_posix() for p in src.rglob("*.py")
            if re.search(r"Manifest\.update\(|\.save\(path\)",
                         p.read_text(encoding="utf-8"))),
            ["lib/corp_actions.py", "lib/elections_save.py"])
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            path = root / "inputs" / "margin" / "elections.json"
            self.assertEqual(elections_save.update(path, lambda m: False)[2],
                             [])
            self.assertEqual((_key(root), path.exists()), (None, False))
            self.assertEqual(elections_save.update(path, lambda m: True)[2],
                             [(root, ">=3.0.0")])
            self.assertEqual(_key(root), ">=3.0.0")
            self.assertEqual(elections_save.update(path, lambda m: True)[2],
                             [])
            # A taxjson.toml it cannot raise: an error, nothing saved.
            path.unlink()
            _lower(root, "3")
            with self.assertRaises(ManifestError):
                elections_save.update(path, lambda m: True)
            self.assertFalse(path.exists())

    def test_a_year_whose_taxjson_toml_cannot_be_read_stops_the_save(self):
        # Which file it reads cannot be told: an error naming it, nothing
        # saved (never the year skipped, left at its old minimum).
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            top = year.parent
            _ok(_tjs(top, "new-year", "2025"))
            for d in (year, top / "2025"):
                _lower(d)
            shared = _shared(year).read_bytes()
            toml = top / "2025" / "taxjson.toml"
            good = toml.read_text()
            for broken in ("parse", "read"):
                if broken == "parse":
                    toml.write_text(good + "[settings\n")
                else:
                    toml.write_text(good)
                    toml.chmod(0)
                try:
                    r = _ok(_tjs(year, "elections", "margin", "--set",
                                 _SPIN_SET), 2)
                finally:
                    toml.chmod(0o644)
                self.assertIn("tjs elections: error: cannot record the "
                              "taxjson version 2025/taxjson.toml needs:",
                              r.stderr)
                self.assertIn("nothing was saved", r.stderr)
                if broken == "read":
                    self.assertIn("Permission denied", r.stderr)
                self.assertEqual(_shared(year).read_bytes(), shared)
                self.assertEqual(_key(year), ">=0.27.1")

    def test_a_filed_years_raise_is_recorded_in_its_lock(self):
        # Amending a filed year: the save raises its frozen taxjson.toml,
        # the lock records it as the save's own edit (as migrate's,
        # TJ-0231): check-filed names the elections change alone.
        from taxjson.lib.elections_save import LOCK_EDIT
        from taxjson.pipeline.stages import _MIGRATED
        self.assertEqual(LOCK_EDIT, _MIGRATED)
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _lower(year)
            _ok(_tjs(year, "run", "--no-input"))
            _ok(_tjs(year, "close-year", "--yes"))
            _ok(_tjs(year, "elections", "margin", "--set",
                     "20240603-parnt-spnco-3509=ignore"))
            self.assertEqual(_key(year), ">=3.0.0")
            fz = json.loads((year / "filed" / "2024.json").read_text())[
                "frozen"]
            self.assertEqual((fz[LOCK_EDIT]["by"], fz[LOCK_EDIT]["keys"]),
                             ("tjs elections",
                              ["settings.requires_taxjson"]))
            _ok(_tjs(year, "run", "--no-input"))
            said = _flat(_tjs(year, "check-filed"))
            self.assertIn("frozen input(s) changed since the year was "
                          "closed: inputs/margin/elections.json —", said)
            self.assertIn("taxjson.toml is as `tjs elections` rewrote it",
                          said)

    def test_a_formatted_taxjson_toml_stays_formatted(self):
        # The raise no command asked for keeps the template layout: `tjs
        # format` has nothing to say after it.
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _lower(year)
            _ok(_tjs(year, "format", "--write"))
            self.assertIn("already formatted", _flat(_ok(_tjs(year,
                                                              "format"))))
            _ok(_tjs(year, "elections", "margin", "--set", _SPIN_SET))
            self.assertEqual(_key(year), ">=3.0.0")
            self.assertIn("already formatted", _flat(_ok(_tjs(year,
                                                              "format"))))

    def test_a_run_whose_writer_raised_it_keeps_its_books_fresh(self):
        # The run's prompt (or an auto-election) raised the key while the
        # run read taxjson.toml: no input edited (lib/checklist
        # _requirement_raised_alone); another key edited meanwhile is.
        from taxjson.lib import checklist as C
        from taxjson.lib.requires import raise_requirement
        from taxjson.pipeline.config import load_config
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            cfg = load_config(root)

            def started():
                return (C.input_fingerprint(root, cfg),
                        C.run_config_state(root / "taxjson.toml")[1])
            fp, part = started()
            raise_requirement(root, ("elections_json",))
            self.assertEqual(C.record_input_fingerprint(
                root, cfg, started=fp, config_started=part), [])
            fp, part = started()
            toml = root / "taxjson.toml"
            _lower(root)
            toml.write_text(toml.read_text().replace(
                'type = "taxable"', 'type = "sheltered"'))
            edited = C.record_input_fingerprint(root, cfg, started=fp,
                                                config_started=part)
            self.assertEqual([k for k in edited
                              if k.endswith("taxjson.toml")],
                             ["taxjson.toml"], edited)


class TestEveryPathToTheSharedFileRaises(unittest.TestCase):
    """A command that points a year at an elections.json without saving
    one raises its requires_taxjson as a save does: `tjs unclose-year`
    (the year reads the shared file again), `tjs new-year` (from a year
    filed with manifest.json), `tjs align --write` (inputs_dir brought
    over), `tjs migrate --to-years`. close-year's own
    copy is of a file the year read already, so its taxjson.toml was
    raised then: the close keeps it and raises nothing (a raise there
    would change the file the books were built from)."""

    def test_unclose_year(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _filed_with_manifest(year)
            self.assertEqual(_key(year), ">=0.27.1")
            r = _ok(_tjs(year, "unclose-year"))
            self.assertEqual(_key(year), ">=3.0.0")
            self.assertIn('[settings] requires_taxjson = ">=3.0.0"',
                          r.stdout)

    def test_new_year_from_a_year_filed_with_manifest_json(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            _filed_with_manifest(year)
            _ok(_tjs(year.parent, "new-year", "2025"))
            self.assertEqual(_key(year.parent / "2025"), ">=3.0.0")
            self.assertEqual(_key(year), ">=0.27.1")

    def test_align_write_bringing_the_shared_inputs_over(self):
        # inputs_dir brought over (requires_taxjson declined, or the same
        # in both years): the year then reads the shared elections.json.
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            top = year.parent
            _ok(_tjs(top, "new-year", "2025"))
            for d in (year, top / "2025"):
                _lower(d)
            toml = top / "2025" / "taxjson.toml"
            text, n = re.subn(r'(?m)^inputs_dir\s*=.*$', "", toml.read_text())
            self.assertEqual(n, 1)
            toml.write_text(text)
            _ok(_tjs(top / "2025", "align", "--from", "2024", "--write",
                     "--all"))
            self.assertIn('inputs_dir = "../inputs"', toml.read_text())
            self.assertEqual(_key(top / "2025"), ">=3.0.0")

    def test_migrate_to_years(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "manifest.json").write_text(_EMPTY)
            _ok(_tjs(root, "migrate", "--to-years"))
            self.assertEqual(_key(root / "2025"), ">=3.0.0")

    def test_close_year_keeps_the_raise_and_its_books(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            self.assertEqual(_key(year), ">=3.0.0")
            _ok(_tjs(year, "run", "--no-input"))
            _ok(_tjs(year, "close-year", "--yes"))
            self.assertTrue((year / "inputs" / "margin"
                             / "elections.json").is_file())
            self.assertEqual(_key(year), ">=3.0.0")
            self.assertIn("filed 2024: OK", _flat(_ok(_tjs(year,
                                                           "check-filed"))))


class TestYearFolderMigrateNamesWhatItChecked(unittest.TestCase):

    def test_nothing_to_migrate(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            r = _ok(_tjs(year, "migrate"))
            self.assertIn("nothing to migrate in 2024/ or beside it (no old "
                          "per-purpose file in 2024/, no year folder holds a "
                          "tobase.map of its own, no account folder an old "
                          "elections file)", _flat(r))


class TestMigrateAsksEachFeaturesOwnRelease(unittest.TestCase):
    """Migrate raises each project for what it holds: 3.0.0 where an
    account folder holds an elections.json (renamed or there before);
    a tobase.map merge alone keeps its own release (0.27.1)."""

    @staticmethod
    def _years(td, elections: bool) -> Path:
        top = Path(td) / "top"
        acct = top / "inputs" / "margin"
        acct.mkdir(parents=True)
        (acct / "trades.tt").write_text(_TRADES)
        if elections:
            (acct / "elections.json").write_text(_EMPTY)
        (top / "2025").mkdir()
        (top / "2025" / "taxjson.toml").write_text(_CA.replace(
            "[accounts.margin]", 'inputs_dir = "../inputs"\n[accounts.margin]'))
        (top / "2025" / "tobase.map").write_text("TOBASE QZZQ.US QZZQ.TO\n")
        return top

    def test_a_tobase_map_merge_alone(self):
        with tempfile.TemporaryDirectory() as td:
            top = self._years(td, elections=False)
            _ok(_tjs(top, "migrate"))
            self.assertEqual(_key(top / "2025"), ">=0.27.1")

    def test_a_project_holding_elections_json(self):
        with tempfile.TemporaryDirectory() as td:
            top = self._years(td, elections=True)
            _ok(_tjs(top, "migrate"))
            self.assertEqual(_key(top / "2025"), ">=3.0.0")


# The old command line, as a person would type or read it.
_OLD = re.compile(r"\b(?:tjs|taxjson)(?:\s+-C\s+\S+)?\s+elect(?![\w-])"
                  r"|<code>elect</code>|\{_PROG\} elect\b")
# The renamed-command table and its message (cli/parser.RENAMED_COMMANDS).
_TABLE = re.compile(r"RENAMED_COMMANDS|was renamed|became `tjs elections`")


class TestNoUserFacingTextNamesTheOldCommand(unittest.TestCase):
    """The code, the help page, the docs (but the history pages' entries
    fixed before v3.0.0, and CHANGELOG.md) and the deck name `tjs
    elections`, never `tjs elect`."""

    def test_code_docs_and_deck(self):
        repo = _paths.REPO
        files = [repo / n for n in ("README.md", "AGENTS.md",
                                    "KNOWN_ISSUES.md")]
        files += sorted((repo / "src").rglob("*.py"))
        files += sorted((repo / "docs").rglob("*.md"))
        files += [repo / "docs" / "deck" / "taxjson-deck.html"]
        hits = []
        for p in files:
            if not p.is_file():
                continue
            rel = p.relative_to(repo).as_posix()
            lines = p.read_text(encoding="utf-8").splitlines()
            fixed = history_releases(rel, lines)
            for n, line in enumerate(lines, 1):
                if (_OLD.search(line) and not _TABLE.search(line)
                        and not renamed_ok("elect", fixed[n - 1])):
                    hits.append(f"{rel}:{n}: {line.strip()[:80]}")
        self.assertEqual(hits, [])

    def test_the_help_page(self):
        from taxjson.cli.parser import _build_parser
        p, sub = _build_parser()
        self.assertNotIn("elect", sub.choices)
        texts = [p.format_help()] + [c.format_help()
                                     for c in sub.choices.values()]
        self.assertEqual([t for t in texts if _OLD.search(t)], [])

    def test_the_pattern(self):
        for old in ("tjs elect margin", "`tjs -C 2025 elect --pending`",
                    "taxjson elect", "<code>elect</code>"):
            self.assertRegex(old, _OLD, old)
        for new in ("tjs elections", "tjs elections margin", "elect it",
                    "elect the event", "tjs election"):
            self.assertNotRegex(new, _OLD, new)


if __name__ == "__main__":
    unittest.main()
