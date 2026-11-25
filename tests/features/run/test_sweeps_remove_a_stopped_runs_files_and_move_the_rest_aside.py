"""Backlog TJ-5273, TJ-5274: the run's two sweeps of work/ remove what
taxjson wrote, a run that stopped included, and move what it cannot vouch
for to work/kept/, where no command that reads work/ finds it.

TJ-5273: a run that parsed an export whose bytes changed and then stopped
(a bad .tt line in another account) left the parse with its record still
the last run's. With the export taken out, the next run's stale-parse
cleanup moved work/margin_ib.json (its .diag, its _transfers.json) to
work/kept/ ("its bytes are not the ones taxjson's record of it holds"),
to be deleted by hand, though taxjson wrote it. A run's stage now notes the
bytes it wrote in the output's record as it writes them (`pending`:
pipeline/freshness `record_written`), the run's commit drops the note, and
the sweeps read it as taxjson's (pipeline/stage_table `_vouched`): the
parse goes with its companions. No ask reads the note: a stopped run still
vouches for nothing it built. A file whose bytes are neither the record's
nor the noted ones is moved aside, as before.

TJ-5274: an output the last run listed and this one does not build (an
account taken out of taxjson.toml), edited since taxjson wrote it or with
no record of taxjson's, was kept in place ("kept 1 file the last run listed
and this one does not build ... delete it yourself"), where every command
that globs work/<account>_base.json found it (cli/common, cli/plan,
lib/checklist, lib/missing_history), and the next run warned "work/ carries
artifacts for account(s) not in taxjson.toml". It is moved to work/kept/
with its .diag, as the stale-parse cleanup moves a file (`move_aside`),
named once with where it went; its record goes. A move that fails stops
the run, naming the file and the remedy.

On the style-canada golden project (synthetic). The controls: an account
removed with nothing edited has its books removed as before; a run that
completes after a stopped one leaves no note in a record."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import _golden          # the synthetic HOME (tests/_hermetic) and the books
import _style
from taxjson.cli.common import _discover_tx_accounts
from taxjson.lib.checklist import orphan_work_accounts
from taxjson.lib.dispatch import python_module_argv
from taxjson.pipeline import run_state
from taxjson.pipeline import stage_table as ST
from taxjson.pipeline.freshness import (
    commit_records, needs_rebuild, record_path, record_written,
)

BOOK = "style-canada"
IB = ("ib_demo.csv", "ib_extra.csv")        # inputs/margin's IB exports
IB_PARSE = ("margin_ib.json", "margin_ib.json.diag", "margin_ib_corp.json",
            "margin_ib_transfers.json")
OTHER_BYTES = "its bytes are not the ones taxjson's record of it holds"
NO_RECORD = "taxjson has no record of writing it"
EDITED = "edited since taxjson wrote it"
_MOVED = ("note: moved {n} the last run listed and this one does not build (an "
          "account, an export or a kind gone from the project) out of the books: "
          "{files}; taxjson reads nothing in work/kept/ and deletes nothing there")
# The IB extra export's PARN buy with its commission doubled (the parse's
# bytes change), and a .tt line that stops the run after margin's parse.
_PARN = ',PARN,"2024-02-01, 10:00:00",100,50.00,50.00,-5000.00,-1.00,5001.00,'
_PARN2 = ',PARN,"2024-02-01, 10:00:00",100,50.00,50.00,-5000.00,-2.00,5002.00,'
_BAD_TT = "BUYSELL not-a-date QZZZ.TO 1 CAD 1 1 0\n"
_RRSP = '[accounts.rrsp]\ntype      = "sheltered"\ntransfers = true\n'


def _tjs(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run", list(argv)),
                          cwd=root, capture_output=True, text=True,
                          env=_style.env(TAXJSON_WIDTH=0), timeout=900, umask=0o077,
                          stdin=subprocess.DEVNULL, check=False)


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _record(p: Path) -> dict:
    return json.loads(record_path(p).read_text(encoding="utf-8"))


def _stop_after_the_parse(root: Path) -> None:
    """The IB extra export changed, then a run stopped by a .tt line of the
    tfsa after it rewrote margin's parse (before its commit), the line
    taken out again."""
    csv = root / "inputs/margin/ib_extra.csv"
    text = csv.read_text(encoding="utf-8")
    assert text.count(_PARN) == 1, "the golden IB extra export changed"
    _write(csv, text.replace(_PARN, _PARN2))
    tt = root / "inputs/tfsa/tfsa_extra.tt"
    keep = tt.read_text(encoding="utf-8")
    _write(tt, keep + _BAD_TT)
    r = _tjs(root, "run", "--no-input")
    assert r.returncode != 0, "the .tt line did not stop the run"
    _write(tt, keep)


def _ib_gone(root: Path) -> None:
    out = root.parent / f"{root.name}-gone"
    out.mkdir()
    for n in IB:
        shutil.move(root / "inputs/margin" / n, out / n)


def _stopped(root: Path) -> None:
    _stop_after_the_parse(root)
    _ib_gone(root)


def _stopped_then_edited(root: Path) -> None:
    _stop_after_the_parse(root)
    with open(root / "work/margin_ib.json", "a", encoding="utf-8") as f:
        f.write("\n")
    _ib_gone(root)


def _rrsp_out(root: Path) -> None:
    """[accounts.rrsp] taken out of taxjson.toml (its folder left)."""
    toml = root / "taxjson.toml"
    text = toml.read_text(encoding="utf-8")
    assert text.count(_RRSP) == 1, "the golden taxjson.toml changed"
    _write(toml, text.replace(_RRSP, ""))


def _append(p: Path) -> None:
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n")


def _edited_book(root: Path) -> None:
    _append(root / "work/rrsp_base.json")
    _rrsp_out(root)


def _book_with_no_record(root: Path) -> None:
    record_path(root / "work/rrsp_base.json").unlink()
    _rrsp_out(root)


def _edited_report(root: Path) -> None:
    _append(root / "reports/rrsp.sum")
    _rrsp_out(root)


def _kept_is_a_file(root: Path) -> None:
    _edited_book(root)
    _write(root / "work/kept", "a file of yours\n")


CASES = {"stopped": _stopped, "stopped then edited": _stopped_then_edited,
         "completed after a stop": _stop_after_the_parse,
         "edited book": _edited_book, "no record": _book_with_no_record,
         "edited report": _edited_report, "kept is a file": _kept_is_a_file,
         "removed": _rrsp_out, "parse removed": _ib_gone}
# The cases run twice (the second run: nothing more to say).
AGAIN = ("edited book",)


def _fees(root: Path) -> list:
    return [ln for ln in (root / "reports/fees.rpt").read_text(
        encoding="utf-8").splitlines() if not ln.startswith("Generated:")]


class TheRunsOwnBytes(unittest.TestCase):
    """freshness `record_written` and stage_table `_vouched`, on files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name).resolve() / "p" / "work"
        self.work.mkdir(parents=True)
        patcher = mock.patch.object(run_state, "current", run_state.RunState())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_what_a_run_wrote_is_its_own_before_the_commit(self):
        out = self.work / "m_ib.json"
        _write(out, "[]\n")
        self.assertTrue(needs_rebuild(out, force=True))
        record_written(out)
        self.assertEqual(_record(out), {"format": 2, "pending": _sha(out)})
        self.assertIs(ST._vouched(out), True)
        # Who wrote it, not what it is built from: no ask reads the note.
        run_state.current.pending_records.clear()       # the run stopped
        self.assertTrue(needs_rebuild(out))
        _append(out)
        self.assertIs(ST._vouched(out), False)

    def test_the_commit_drops_the_note(self):
        out = self.work / "m_ib.json"
        _write(out, "[]\n")
        needs_rebuild(out, force=True)
        commit_records()
        committed = _record(out)
        self.assertNotIn("pending", committed)
        # The next run's build: noted beside the last commit's bytes, both
        # taxjson's; committed, the note goes.
        _write(out, "[1]\n")
        needs_rebuild(out, force=True)
        record_written(out)
        self.assertEqual(_record(out), {**committed, "pending": _sha(out)})
        self.assertIs(ST._vouched(out), True)
        commit_records()
        self.assertEqual(_record(out)["out"], _sha(out))
        self.assertNotIn("pending", _record(out))

    def test_bytes_the_record_holds_already_write_nothing(self):
        out = self.work / "m_ib.json"
        _write(out, "[]\n")
        needs_rebuild(out, force=True)
        commit_records()
        before = record_path(out).read_bytes()
        record_written(out)
        self.assertEqual(record_path(out).read_bytes(), before)


class TheSweeps(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.built = built = tmp / "built"
        shutil.copytree(_golden.GOLDEN / BOOK / "project", built,
                        ignore=shutil.ignore_patterns(*_golden._GENERATED))
        _golden._make_private(built)
        r = _tjs(built, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
        cls.roots = {}
        for name in CASES:
            root = cls.roots[name] = tmp / name.replace(" ", "-")
            shutil.copytree(built, root, symlinks=True)

        def one(name):
            CASES[name](cls.roots[name])
            r = _tjs(cls.roots[name], "run", "--no-input")
            again = _tjs(cls.roots[name], "run", "--no-input") if name in AGAIN else None
            return r, again
        with ThreadPoolExecutor(2) as pool:
            done = dict(zip(CASES, pool.map(one, CASES)))
        cls.rc = {n: r.returncode for n, (r, _a) in done.items()}
        cls.out = {n: r.stdout + r.stderr for n, (r, _a) in done.items()}
        cls.again = {n: a for n, (_r, a) in done.items() if a is not None}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def ok(self, case: str) -> tuple:
        self.assertEqual(self.rc[case], 0, self.out[case][-2000:])
        return self.roots[case], self.out[case]

    # ---- TJ-5273: the stale-parse cleanup after a run that stopped

    def test_a_parse_a_stopped_run_wrote_goes_with_its_companions(self):
        # The entry's repro: moved to work/kept/ ("its bytes are not the
        # ones taxjson's record of it holds"), with its .diag and
        # _transfers.json, though taxjson wrote it.
        root, out = self.ok("stopped")
        for n in IB_PARSE:
            self.assertFalse((root / "work" / n).exists(), n)
            self.assertIn(f"note: removed stale work/{n} (its input files are gone)", out)
        self.assertNotIn("note: moved", out)
        self.assertNotIn(OTHER_BYTES, out)
        self.assertFalse(os.path.lexists(root / "work/kept"))
        # No report read it: the fees are those of the exports removed
        # after a clean run.
        self.assertEqual(_fees(root), _fees(self.roots["parse removed"]))

    def test_a_parse_edited_after_the_stopped_run_is_moved_aside(self):
        # Control: bytes neither the record's nor the stopped run's.
        root, out = self.ok("stopped then edited")
        self.assertIn(f"work/margin_ib.json -> work/kept/margin_ib.json ({OTHER_BYTES})", out)
        self.assertFalse((root / "work/margin_ib.json").exists())
        self.assertTrue((root / "work/kept/margin_ib.json").is_file())

    def test_a_run_that_completes_leaves_no_note(self):
        # Control: the stopped run's note dropped by the next run's commit;
        # the record holds the parse as built.
        root, _out = self.ok("completed after a stop")
        rec = _record(root / "work/margin_ib.json")
        self.assertNotIn("pending", rec)
        self.assertEqual(rec["out"], _sha(root / "work/margin_ib.json"))
        self.assertEqual([p for p in (root / "work/.deps").glob("*.json")
                          if "pending" in json.loads(p.read_text(encoding="utf-8"))], [])

    def test_a_parse_removed_after_a_clean_run_goes_as_before(self):
        root, out = self.ok("parse removed")
        for n in IB_PARSE:
            self.assertFalse((root / "work" / n).exists(), n)
        self.assertNotIn("note: moved", out)

    # ---- TJ-5274: the sweep of what the last run listed

    def test_a_book_edited_since_is_moved_aside_and_named_once(self):
        # The entry's repro: "kept 1 file the last run listed and this one
        # does not build: work/rrsp_base.json (edited since taxjson wrote
        # it); delete it yourself", the book left in work/.
        root, out = self.ok("edited book")
        self.assertIn(_MOVED.format(n="1 file", files=(
            f"work/rrsp_base.json -> work/kept/rrsp_base.json ({EDITED})")), out)
        self.assertNotIn("delete it yourself", out)
        work = root / "work"
        for n in ("rrsp_base.json", "rrsp_base.json.diag"):
            self.assertFalse(os.path.lexists(work / n), n)
            self.assertTrue((work / "kept" / n).is_file(), n)
        self.assertFalse(record_path(work / "rrsp_base.json").exists())
        self.assertEqual(out.count("work/kept/rrsp_base.json"), 1)
        # No reader of work/ finds it: the books' glob each of cli/common,
        # cli/plan, lib/checklist and lib/missing_history reads, the views'
        # accounts, the orphaned-account check.
        self.assertNotIn("rrsp_base.json", [p.name for p in work.glob("*_base.json")])
        self.assertNotIn("rrsp", _discover_tx_accounts(work))
        self.assertEqual(orphan_work_accounts(root, ["margin", "qt", "tfsa", "crypto"]), [])

    def test_a_moved_book_says_nothing_the_next_run(self):
        # On base the next run warned "work/ carries artifacts for
        # account(s) not in taxjson.toml: rrsp" (the kept book).
        again = self.again["edited book"]
        self.assertEqual(again.returncode, 0, again.stderr[-2000:])
        said = again.stdout + again.stderr
        self.assertNotIn("carries artifacts", said)
        self.assertNotIn("rrsp_base.json", said)
        self.assertTrue((self.roots["edited book"] / "work/kept/rrsp_base.json").is_file())

    def test_a_book_with_no_record_is_moved_aside(self):
        root, out = self.ok("no record")
        self.assertIn(_MOVED.format(n="1 file", files=(
            f"work/rrsp_base.json -> work/kept/rrsp_base.json ({NO_RECORD})")), out)
        self.assertFalse((root / "work/rrsp_base.json").exists())
        self.assertTrue((root / "work/kept/rrsp_base.json").is_file())

    def test_a_report_edited_since_is_moved_aside(self):
        # reports/*.sum is what the checklist reads (lib/checklist).
        root, out = self.ok("edited report")
        self.assertIn(_MOVED.format(n="1 file", files=(
            f"reports/rrsp.sum -> work/kept/rrsp.sum ({EDITED})")), out)
        self.assertFalse((root / "reports/rrsp.sum").exists())
        self.assertTrue((root / "work/kept/rrsp.sum").is_file())
        self.assertNotIn("rrsp.sum", [p.name for p in (root / "reports").glob("*.sum")])

    def test_a_move_that_fails_stops_the_run_naming_the_file(self):
        root, out = self.roots["kept is a file"], self.out["kept is a file"]
        self.assertEqual(self.rc["kept is a file"], 1, out[-2000:])
        self.assertIn("error: cannot move work/rrsp_base.json to work/kept/:", out)
        self.assertIn(f"The last run listed it and this one does not build it ({EDITED}): "
                      f"taxjson reads and deletes only what it wrote. Move it out of "
                      f"work/, then run again.", out)
        self.assertTrue((root / "work/rrsp_base.json").is_file())
        self.assertEqual((root / "work/kept").read_text(encoding="utf-8"), "a file of yours\n")

    def test_a_removed_accounts_books_go_as_before(self):
        # Control: nothing edited, every file vouched for.
        root, out = self.ok("removed")
        self.assertIn("the last run built and this one does not", out)
        self.assertNotIn("note: moved", out)
        self.assertFalse(os.path.lexists(root / "work/kept"))
        self.assertEqual(sorted(p.name for p in (root / "work").glob("rrsp_*")), [])


if __name__ == "__main__":
    unittest.main()
