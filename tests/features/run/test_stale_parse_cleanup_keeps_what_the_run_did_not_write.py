"""Backlog TJ-5071 (older TJ-4275): the run's stale-parse cleanup deletes
only what it wrote, and reads nothing it did not.

Each account's run removes the parse of an export group gone from
inputs/<account>/ (work/<account>_<group>.json, its .diag, its _corp and
_transfers files). It took every work/<account>_*.json that no export of
the account named: a file of yours there (work/margin_notes.json), or a
link to a file outside the project, was deleted with "removed stale
work/... (its input files are gone)", a link's target kept but the link
gone. Now a file goes only when taxjson can vouch for it: with records in
work/, what its record vouches for (and the _transfers.json beside a parse
that goes); with none at all (an older taxjson's work/), a file named as
an export group's parse. Anything else is moved to work/kept/ with its
.diag (a link as the link, still leading where it led), where no reader of
work/<account>_*.json (the fees report, the audit's source join, the .sum
lines) sees it, and named once in one Info line (captured: `note:`) with
where it went: a file that is no transactions JSON stops no step. A
folder is left where it is, and named. A work/ that is a link to a folder
in the project (`.w`) is that folder: the run writes through it, and the
cleanup removes and moves there as in a plain work/ (the review's m4: the
Kraken export's parse was left in place and read, its fees counted in
fees.rpt's TOTAL).

On the style-canada golden project (synthetic). The controls: an export
group removed still has its parse files removed, with records and
without, and a run that stopped after rewriting a parse leaves the
reports as they are when the parse goes: the parse is removed as one
taxjson wrote (TJ-5273: its bytes are noted as the run's as the parse is
written, the run completed or not)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import _golden          # the synthetic HOME (tests/_hermetic) and the books
import _style
from taxjson.lib.dispatch import python_module_argv

BOOK = "style-canada"
IB = ("ib_demo.csv", "ib_extra.csv")        # inputs/margin's IB exports
KRAKEN_PARSE = ("crypto_kraken.json", "crypto_kraken.json.diag",
                "crypto_kraken_transfers.json")
IB_PARSE = ("margin_ib.json", "margin_ib.json.diag", "margin_ib_corp.json",
            "margin_ib_transfers.json")
NO_RECORD = "taxjson has no record of writing it"
OTHER_BYTES = "its bytes are not the ones taxjson's record of it holds"
_MOVED = ("note: moved {n} named like a parse of account margin's exports, "
          "which no export in inputs/margin/ makes now, out of the books: "
          "{files}; taxjson reads nothing in work/kept/ and deletes nothing there")
# The IB extra export's PARN buy with its commission doubled (the parse's
# bytes change), and a .tt line that stops the run after margin's parse.
_PARN = ',PARN,"2024-02-01, 10:00:00",100,50.00,50.00,-5000.00,-1.00,5001.00,'
_PARN2 = ',PARN,"2024-02-01, 10:00:00",100,50.00,50.00,-5000.00,-2.00,5002.00,'
_BAD_TT = "BUYSELL not-a-date QZZZ.TO 1 CAD 1 1 0\n"


def _tjs(root: Path, *argv: str, ok: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(python_module_argv("taxjson.bin.taxjson_run", list(argv)),
                       cwd=root, capture_output=True, text=True,
                       env=_style.env(TAXJSON_WIDTH=0), timeout=900, umask=0o077,
                       stdin=subprocess.DEVNULL, check=False)
    if ok and r.returncode != 0:
        raise AssertionError(f"tjs {' '.join(argv)} exited {r.returncode}:\n"
                             f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def _mine(root: Path) -> None:
    """Files of yours under the account's names, with the books' records
    there: a note, one that is no JSON, a link to a file outside the
    project, a link to nothing, and three named as a parse with no record
    of one (another broker's export group with its .diag, a generic
    group, a corp file of no group)."""
    w = root / "work"
    _write(w / "margin_notes.json", '{"note": "mine"}\n')
    _write(w / "margin_todo.json", "not json at all\n")
    _write(root.parent / f"{root.name}-elsewhere.json", "[1, 2]\n")
    os.symlink(f"../../{root.name}-elsewhere.json", w / "margin_link.json")
    os.symlink(str(root.parent / f"{root.name}-nothing"), w / "margin_dangling.json")
    _write(w / "margin_webull.json", '{"note": "mine"}\n')
    _write(w / "margin_webull.json.diag", "mine\n")
    _write(w / "margin_generic-foo.json", '{"n": 1}\n')
    _write(w / "margin_ib_extra_corp.json", '{"n": 1}\n')


def _folder(root: Path) -> None:
    (root / "work/margin_dir.json").mkdir()


def _ib_gone(root: Path, *, records: bool = True, edited: bool = False) -> None:
    """inputs/margin's IB exports removed (moved out of the project)."""
    out = root.parent / f"{root.name}-gone"
    out.mkdir()
    for n in IB:
        shutil.move(root / "inputs/margin" / n, out / n)
    if not records:                 # an older taxjson's work/: no records
        shutil.rmtree(root / "work/.deps")
    if edited:
        with open(root / "work/margin_ib.json", "a", encoding="utf-8") as f:
            f.write("\n")


def _stopped(root: Path) -> None:
    """The IB extra export changed, a run stopped by a .tt line after it
    rewrote margin's parse (its record still the last run's), then the .tt
    line taken out and the IB exports removed."""
    csv = root / "inputs/margin/ib_extra.csv"
    text = csv.read_text(encoding="utf-8")
    assert text.count(_PARN) == 1, "the golden IB extra export changed"
    _write(csv, text.replace(_PARN, _PARN2))
    tt = root / "inputs/tfsa/tfsa_extra.tt"
    keep = tt.read_text(encoding="utf-8")
    _write(tt, keep + _BAD_TT)
    r = _tjs(root, "run", "--no-input", ok=False)
    assert r.returncode != 0, "the .tt line did not stop the run"
    _write(tt, keep)
    _ib_gone(root)


def _older_tt(root: Path) -> None:
    """An older taxjson's work/ (no records), the .tt file removed: its
    parse is named as no export group's."""
    shutil.move(root / "inputs/margin/margin_extra.tt", root.parent / f"{root.name}.tt")
    shutil.rmtree(root / "work/.deps")


def _linked(root: Path) -> None:
    """work/ a link to the folder .w in the project (`mv work .w; ln -s .w
    work`)."""
    os.rename(root / "work", root / ".w")
    os.symlink(".w", root / "work")


def _kraken_gone(root: Path, *, linked: bool = False) -> None:
    """inputs/crypto's Kraken export removed; work/ a link when `linked`."""
    if linked:
        _linked(root)
    shutil.move(root / "inputs/crypto/kraken_demo.csv", root.parent / f"{root.name}.csv")


def _linked_mine(root: Path) -> None:
    _linked(root)
    _write(root / "work/margin_notes.json", '{"note": "mine"}\n')


CASES = {"mine": _mine, "folder": _folder, "records": _ib_gone,
         "no records": lambda r: _ib_gone(r, records=False),
         "edited": lambda r: _ib_gone(r, edited=True), "stopped": _stopped,
         "older tt": _older_tt, "kraken": _kraken_gone,
         "linked kraken": lambda r: _kraken_gone(r, linked=True),
         "linked mine": _linked_mine}


def _fees(root: Path, name: str = "fees.rpt") -> list:
    return [ln for ln in (root / "reports" / name).read_text(
        encoding="utf-8").splitlines() if not ln.startswith("Generated:")]


class TheStaleParseCleanup(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.built = built = tmp / "built"
        shutil.copytree(_golden.GOLDEN / BOOK / "project", built,
                        ignore=shutil.ignore_patterns(*_golden._GENERATED))
        _golden._make_private(built)
        _tjs(built, "run", "--no-input")
        cls.roots = {}
        for name in CASES:
            root = cls.roots[name] = tmp / name.replace(" ", "-")
            shutil.copytree(built, root, symlinks=True)

        def one(name):
            CASES[name](cls.roots[name])
            r = _tjs(cls.roots[name], "run", "--no-input", ok=False)
            if name == "mine":      # and once more, the files moved
                again = _tjs(cls.roots[name], "run", "--no-input", ok=False)
                cls.again = again.stdout + again.stderr
            return r
        with ThreadPoolExecutor(4) as pool:
            done = dict(zip(CASES, pool.map(one, CASES)))
        cls.rc = {n: r.returncode for n, r in done.items()}
        cls.out = {n: r.stdout + r.stderr for n, r in done.items()}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_files_of_yours_are_moved_aside_and_named_once(self):
        # The entry's repro: each was deleted ("removed stale
        # work/margin_notes.json (its input files are gone)"), the link
        # dangling or not; a kept one stopped the fees step.
        root, out = self.roots["mine"], self.out["mine"]
        w, kept = root / "work", root / "work/kept"
        self.assertEqual(self.rc["mine"], 0, out[-2000:])
        names = ("margin_dangling.json", "margin_generic-foo.json",
                 "margin_ib_extra_corp.json", "margin_link.json",
                 "margin_notes.json", "margin_todo.json", "margin_webull.json")
        self.assertIn(_MOVED.format(n="7 files", files=", ".join(
            f"work/{n} -> work/kept/{n} ({NO_RECORD})" for n in names)), out)
        for n in names:
            self.assertFalse(os.path.lexists(w / n), n)
            self.assertTrue(os.path.lexists(kept / n), n)
            self.assertNotIn(f"removed stale work/{n}", out)
            self.assertEqual(out.count(f"work/kept/{n} "), 1, n)
        self.assertEqual(json.loads((kept / "margin_notes.json").read_text(
            encoding="utf-8")), {"note": "mine"})
        self.assertEqual((kept / "margin_todo.json").read_text(encoding="utf-8"),
                         "not json at all\n")
        self.assertEqual((kept / "margin_webull.json.diag").read_text(encoding="utf-8"),
                         "mine\n")
        self.assertFalse(os.path.lexists(w / "margin_webull.json.diag"))
        # The links moved as links, each leading where it led.
        self.assertTrue((kept / "margin_link.json").is_symlink())
        self.assertEqual(Path(os.path.realpath(kept / "margin_link.json")),
                         Path(os.path.realpath(root.parent / "mine-elsewhere.json")))
        self.assertEqual((kept / "margin_link.json").read_text(encoding="utf-8"), "[1, 2]\n")
        self.assertEqual(os.readlink(kept / "margin_dangling.json"),
                         str(root.parent / "mine-nothing"))
        # No step read them: the fees are the books'.
        self.assertEqual(_fees(root), _fees(self.built))

    def test_moved_once_a_later_run_says_nothing(self):
        self.assertNotIn("note: moved", self.again)
        self.assertNotIn("margin_notes.json", self.again)
        self.assertTrue((self.roots["mine"] / "work/kept/margin_notes.json").is_file())

    def test_a_folder_is_left_and_named(self):
        root, out = self.roots["folder"], self.out["folder"]
        self.assertTrue((root / "work/margin_dir.json").is_dir())
        self.assertIn("note: left 1 entry named like a parse of account margin's "
                      "exports, which no export in inputs/margin/ makes now, where "
                      "it is (a folder, or reached through a link): "
                      "work/margin_dir.json; move it out of work/", out)
        self.assertEqual(self.rc["folder"], 2)

    def test_a_parse_edited_since_the_run_wrote_it_is_moved_aside(self):
        root, out = self.roots["edited"], self.out["edited"]
        self.assertEqual(self.rc["edited"], 0, out[-2000:])
        self.assertIn(_MOVED.format(n="2 files", files=(
            f"work/margin_ib.json -> work/kept/margin_ib.json ({OTHER_BYTES}), "
            f"work/margin_ib_transfers.json -> work/kept/margin_ib_transfers.json "
            f"({NO_RECORD})")), out)
        for n in ("margin_ib.json", "margin_ib.json.diag", "margin_ib_transfers.json"):
            self.assertTrue((root / "work/kept" / n).is_file(), n)
        self.assertFalse((root / "work/margin_ib_corp.json").exists())
        self.assertIn("note: removed stale work/margin_ib_corp.json (its input "
                      "files are gone)", out)

    def test_a_parse_a_stopped_run_rewrote_is_removed_as_taxjsons(self):
        # The stopped run rewrote margin_ib.json and stopped before its
        # commit. It was moved aside ("its bytes are not the ones taxjson's
        # record of it holds"), though taxjson wrote it (TJ-5273); its bytes
        # are noted as the run's when the parse is written, so it goes with
        # its companions, as a clean run's parse does. No report reads it:
        # the fees are those of the IB exports removed after a clean run.
        root, out = self.roots["stopped"], self.out["stopped"]
        self.assertEqual(self.rc["stopped"], 0, out[-2000:])
        for n in IB_PARSE:
            self.assertFalse((root / "work" / n).exists(), n)
            self.assertIn(f"note: removed stale work/{n} (its input files are gone)", out)
        self.assertNotIn("note: moved", out)
        self.assertFalse((root / "work/kept").exists())
        self.assertEqual(_fees(root), _fees(self.roots["records"]))

    def test_with_no_records_a_parse_of_no_export_group_is_moved_aside(self):
        root, out = self.roots["older tt"], self.out["older tt"]
        self.assertEqual(self.rc["older tt"], 0, out[-2000:])
        self.assertIn(_MOVED.format(n="1 file", files=(
            f"work/margin_tt_margin_extra.json -> work/kept/margin_tt_margin_extra.json "
            f"({NO_RECORD})")), out)
        self.assertNotIn("not written by taxjson", out)
        self.assertTrue((root / "work/kept/margin_tt_margin_extra.json").is_file())

    def test_a_removed_export_groups_parse_goes_as_before(self):
        # Control: the IB exports removed, the books' own record or none.
        for case in ("records", "no records"):
            with self.subTest(case=case):
                root, out = self.roots[case], self.out[case]
                self.assertEqual(self.rc[case], 0, out[-2000:])
                for n in IB_PARSE:
                    self.assertFalse((root / "work" / n).exists(), n)
                    self.assertIn(f"note: removed stale work/{n} (its input files "
                                  f"are gone)", out)
                self.assertNotIn("note: moved", out)
                self.assertFalse((root / "work/kept").exists())

    def test_a_linked_work_folder_is_swept_as_work(self):
        # The review's m4: work/ a link to .w, the Kraken export removed.
        # Its parse was left where it was ("left 2 entries ... reached
        # through a link") and read: fees.rpt's TOTAL and crypto.sum
        # counted it. It goes as from a plain work/, and nothing else is said.
        root, out = self.roots["linked kraken"], self.out["linked kraken"]
        self.assertEqual(self.rc["linked kraken"], 0, out[-2000:])
        self.assertTrue((root / "work").is_symlink())
        for n in KRAKEN_PARSE:
            self.assertFalse(os.path.lexists(root / ".w" / n), n)
            self.assertIn(f"note: removed stale work/{n} (its input files are gone)", out)
        self.assertNotIn("note: moved", out)
        self.assertNotIn("note: left", out)
        self.assertFalse(os.path.lexists(root / ".w/kept"))
        plain = self.roots["kraken"]
        self.assertEqual(self.rc["kraken"], 0, self.out["kraken"][-2000:])
        for n in KRAKEN_PARSE:
            self.assertFalse(os.path.lexists(plain / "work" / n), n)
        self.assertEqual(_fees(root), _fees(plain))
        self.assertEqual(_fees(root, "crypto.sum"), _fees(plain, "crypto.sum"))

    def test_in_a_linked_work_folder_a_file_of_yours_is_moved_into_it(self):
        root, out = self.roots["linked mine"], self.out["linked mine"]
        self.assertEqual(self.rc["linked mine"], 0, out[-2000:])
        self.assertIn(_MOVED.format(n="1 file", files=(
            f"work/margin_notes.json -> work/kept/margin_notes.json ({NO_RECORD})")), out)
        self.assertTrue((root / "work").is_symlink())
        self.assertFalse(os.path.lexists(root / ".w/margin_notes.json"))
        self.assertEqual(json.loads((root / ".w/kept/margin_notes.json").read_text(
            encoding="utf-8")), {"note": "mine"})
        self.assertFalse(os.path.lexists(root / "kept"))
        self.assertEqual(_fees(root), _fees(self.built))


if __name__ == "__main__":
    unittest.main()
