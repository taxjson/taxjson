"""The positions snapshots every year shares (backlog TJ-5367, TJ-5400 to
TJ-5408): one holdings/ beside inputs/ in `tjs init`'s layout
(`holdings_dir = "../holdings"`), each year comparing the snapshots dated
from January 1 of its year (through January 15 of the next when the next
year's folder is there, else every later one), the others counted in one
note that says how to compare one; close-year copies what the year
compares into the year's own holdings/ and comments the key out under its
freeze note, the filed year reading its copy from then on; new-year names
the shared folder again; `tjs migrate --shared-holdings` moves an
existing project's per-year folders there (the plan first, writing
nothing; --write to act), one copy of a file two years hold, the
accounts' `holdings` paths pointed at the moved files, two years'
undated or same-day snapshots of one account refused; `tjs migrate
--to-years` keeps holdings/ at the top; `tjs fetch --positions` is told
the shared folder, the year a snapshot of today is of and the year
folders that compare it; a single folder, or a holdings_dir set
anywhere else, compares every snapshot as before.

Synthetic data only: made-up tickers (QZQ.TO, ZZB.TO), broker id
99900001.
"""
import argparse
import io
import json
import os
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from _tmpfiles import private_dir
from _support.years_multi_projects import multi, run_ok, single, tjs

from taxjson.lib import fetchers as FP
from taxjson.lib import holdings_dir as HD
from taxjson.lib import project_layout as PL

_BROKER = "99900001"


def _settings(d: Path) -> dict:
    return tomllib.loads((d / "taxjson.toml").read_text())["settings"]


def _share(d: Path, *, account=True) -> None:
    """The year project `d` (years_multi_projects) reading ../holdings,
    its margin account the broker account _BROKER."""
    p = d / "taxjson.toml"
    text = p.read_text().replace(
        'exports_dir = "../exports"',
        'exports_dir = "../exports"\nholdings_dir = "../holdings"')
    if account:
        text = text.replace('type = "taxable"',
                            f'type = "taxable"\naccount = "{_BROKER}"', 1)
    p.write_text(text)


def _snap(folder: Path, name: str, as_of, rows) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    text = f'[meta]\nbroker_account = "{_BROKER}"\n'
    if as_of:
        text += f'as_of = "{as_of}"\n'
    for sym, qty in rows:
        text += (f'\n[[holding]]\nsymbol = "{sym}"\nquantity = {qty}\n'
                 f'currency = "CAD"\n')
    p = folder / name
    p.write_text(text)
    return p


def _names(files) -> list:
    return sorted(p.name for p in files)


def _bytes(top: Path) -> dict:
    """Every file below `top` but the years' work/ and the command logs
    (logs/ at the top or in a year folder: every command writes one),
    by its bytes."""
    def _kept(parts: tuple) -> bool:
        logs = parts[:1] == ("logs",) or (parts[1:2] == ("logs",)
                                          and parts[0].isdigit())
        return "work" not in parts and not logs
    return {p.relative_to(top).as_posix(): p.read_bytes()
            for p in sorted(top.rglob("*"))
            if p.is_file() and _kept(p.relative_to(top).parts)}


def _list_holdings(d: Path, account: str, value: str) -> None:
    """`[accounts.ACCOUNT] holdings = VALUE` in the year `d`."""
    p = d / "taxjson.toml"
    p.write_text(p.read_text().replace(
        f"[accounts.{account}]\n",
        f"[accounts.{account}]\nholdings = {value}\n", 1))


class TestTheYearsWindow(unittest.TestCase):
    """Which snapshots of a shared folder a year compares: those dated
    from January 1 of the year, through January 15 of the next when the
    next year's folder is there (else every later one), and every
    undated one."""

    def test_the_window_and_its_boundaries(self):
        self.assertEqual(HD.year_window(2024, True),
                         ("2024-01-01", "2025-01-15"))
        self.assertEqual(HD.year_window(2024, False), ("2024-01-01", None))
        self.assertTrue(HD.window_holds("2024-01-01", 2024, True))
        self.assertFalse(HD.window_holds("2023-12-31", 2024, True))
        self.assertTrue(HD.window_holds("2025-01-15", 2024, True))
        self.assertFalse(HD.window_holds("2025-01-16", 2024, True))
        self.assertTrue(HD.window_holds("2026-03-10", 2024, False))
        # The year a fetched file is named for: unchanged.
        self.assertEqual(HD.snapshot_year("2024-12-31"), 2024)
        self.assertEqual(HD.snapshot_year("2025-01-15"), 2024)
        self.assertEqual(HD.snapshot_year("2025-01-16"), 2025)
        self.assertEqual(HD.snapshot_year("2025-01-01"), 2024)

    def _years(self):
        top = multi("canada", years=(2023, 2024, 2025))
        for y in (2023, 2024, 2025):
            _share(top / str(y))
        return top, top / "holdings"

    def test_a_january_snapshot_is_compared_by_both_years(self):
        # (the old year's closing positions and the new year's opening
        # ones: an open year's books run on past its end, so the two
        # years agree on it; backlog TJ-5400)
        top, h = self._years()
        jan = _snap(h, "margin_jan.toml", "2025-01-10", [("QZQ.TO", 50)])
        for y in (2024, 2025):
            files, note = HD.year_files(top / str(y))
            self.assertEqual(files, [jan], y)
            self.assertIsNone(note, y)
        self.assertEqual(HD.compared_by(top / "2024", "2025-01-10"),
                         [2024, 2025])

    def test_a_snapshot_after_january_15_with_the_next_years_folder(self):
        top, h = self._years()
        mine = _snap(h, "margin_2024.toml", "2025-01-15", [("QZQ.TO", 50)])
        undated = _snap(h, "margin_now.toml", None, [("QZQ.TO", 50)])
        late = _snap(h, "margin_2025.toml", "2025-01-16", [("ZZB.TO", 40)])
        old = _snap(h, "margin_2023.toml", "2023-12-31", [("QZQ.TO", 50)])
        (h / "README.txt").write_text(HD.SHARED_README)
        files, note = HD.year_files(top / "2024")
        self.assertEqual(sorted(files), sorted([mine, undated]))
        self.assertIn("../holdings/: 2 snapshot(s) of other years not "
                      "compared (1 dated before 2024-01-01: "
                      "margin_2023.toml; 1 after 2025-01-15, 2025's: "
                      "margin_2025.toml) — 2024 compares those dated "
                      "2024-01-01 to 2025-01-15; to compare one anyway, "
                      "name it: `tjs sanity ACCOUNT=../holdings/"
                      "margin_2023.toml`", note)
        self.assertEqual(sorted(HD.other_years_files(top / "2024")),
                         sorted([late, old]))
        # 2025 compares the late one, the undated and the one of
        # January 1-15 (both years'), not the 2023 one.
        files, _note = HD.year_files(top / "2025")
        self.assertEqual(sorted(files), sorted([mine, late, undated]))
        self.assertEqual(HD.compared_by(top / "2024", "2025-03-01"), [2025])
        self.assertEqual(HD.compared_by(top / "2024", "2023-12-31"), [2023])
        self.assertTrue(PL.shared_holdings(top / "2024"))

    def test_a_snapshot_after_january_15_with_no_later_year_folder(self):
        # (the filing-season fetch, before the next year's folder is
        # made: the newest year compares it; backlog TJ-5400)
        top = multi("canada", years=(2024,))
        _share(top / "2024")
        h = top / "holdings"
        spring = _snap(h, "margin_live_holdings_2025.toml", "2025-03-10",
                       [("ZZB.TO", 40)])
        files, note = HD.year_files(top / "2024")
        self.assertEqual(files, [spring])
        self.assertIsNone(note)
        self.assertEqual(HD.compared_by(top / "2024", "2025-03-10"), [2024])

    def test_the_oldest_sharing_year_compares_its_opening_snapshot(self):
        # (no year before it reads the folder: no lower end, as the
        # newest year has no upper one; backlog TJ-5432)
        self.assertEqual(HD.year_window(2024, True, False),
                         (None, "2025-01-15"))
        self.assertEqual(HD.window_span(None, "2025-01-15"),
                         "through 2025-01-15")
        top = multi("canada", years=(2023, 2024, 2025))
        for y in (2024, 2025):
            _share(top / str(y))
        h = top / "holdings"
        opening = _snap(h, "margin_2023.toml", "2023-12-31", [("QZQ.TO", 50)])
        self.assertFalse(PL.shared_holdings(top / "2023"))
        self.assertFalse(HD.earlier_year_folder(top / "2024", 2024))
        self.assertEqual(HD.year_files(top / "2024"), ([opening], None))
        self.assertEqual(HD.compared_by(top / "2024", "2023-12-31"), [2024])
        # 2025 has 2024 before it: from January 1, as before.
        files, note = HD.year_files(top / "2025")
        self.assertEqual(files, [])
        self.assertIn("1 dated before 2025-01-01: margin_2023.toml", note)
        # Once 2023 reads it too, the snapshot is 2023's.
        _share(top / "2023")
        self.assertEqual(HD.compared_by(top / "2024", "2023-12-31"), [2023])
        self.assertIn("1 dated before 2024-01-01",
                      HD.year_files(top / "2024")[1])

    def test_a_next_year_reading_its_own_folder_cuts_nothing(self):
        # (only a next year that reads the shared folder compares the
        # snapshots after January 15: one with a holdings/ of its own
        # leaves them to this year, none compared by no year; backlog
        # TJ-5430)
        top = multi("canada", years=(2024, 2025))
        _share(top / "2024")
        h = top / "holdings"
        late = _snap(h, "margin_2025.toml", "2025-06-30", [("ZZB.TO", 40)])
        self.assertFalse(PL.shared_holdings(top / "2025"))
        self.assertFalse(HD.later_year_folder(top / "2024", 2024))
        self.assertEqual(HD.year_files(top / "2024"), ([late], None))
        self.assertEqual(HD.compared_by(top / "2024", "2025-06-30"), [2024])
        # Once 2025 reads it too, 2025 compares it, not 2024.
        _share(top / "2025")
        self.assertTrue(HD.later_year_folder(top / "2024", 2024))
        self.assertEqual(HD.year_files(top / "2024")[0], [])
        self.assertEqual(HD.compared_by(top / "2024", "2025-06-30"), [2025])

    def test_a_snapshot_an_account_names_is_never_left_out(self):
        # (its `holdings = [...]` reads it whatever its date: not counted
        # as another year's, and a redacted copy keeps it)
        top, h = self._years()
        _snap(h, "margin_named.toml", "2025-02-01", [("QZQ.TO", 50)])
        other = _snap(h, "margin_2025.toml", "2025-06-30", [("ZZB.TO", 40)])
        p = top / "2024" / "taxjson.toml"
        p.write_text(p.read_text().replace(
            f'account = "{_BROKER}"',
            f'account = "{_BROKER}"\n'
            'holdings = ["../holdings/margin_named.toml"]', 1))
        files, note = HD.year_files(top / "2024")
        self.assertEqual(files, [])
        self.assertIn("1 snapshot(s) of other years not compared", note)
        self.assertEqual(HD.other_years_files(top / "2024"), [other])

    def test_a_folder_of_the_years_own_keeps_every_snapshot(self):
        root = single("canada", 2024)
        h = root / "holdings"
        a = _snap(h, "margin_a.toml", "2026-06-30", [("QZQ.TO", 50)])
        b = _snap(h, "margin_b.toml", "2024-12-31", [("QZQ.TO", 50)])
        files, note = HD.year_files(root)
        self.assertEqual(sorted(files), sorted([a, b]))
        self.assertIsNone(note)
        self.assertFalse(PL.shared_holdings(root))
        self.assertIsNone(HD.holdings_year(root))

    def test_a_holdings_dir_set_elsewhere_compares_every_snapshot(self):
        # (only the value `tjs init` and `tjs migrate` write, in a year
        # folder, is the folder every year shares: a single folder or a
        # year reading another folder outside it compares every
        # snapshot, as before; backlog TJ-5404)
        root = single("canada", 2024)
        p = root / "taxjson.toml"
        p.write_text(p.read_text().replace(
            "option_grant_timing_since = 2023",
            'option_grant_timing_since = 2023\n'
            'holdings_dir = "../snaps_out"'))
        out = root.parent / "snaps_out"
        snaps = [_snap(out, "margin_a.toml", "2023-12-31", [("QZQ.TO", 50)]),
                 _snap(out, "margin_b.toml", "2025-06-01", [("ZZB.TO", 40)])]
        self.assertFalse(PL.shared_holdings(root))
        self.assertEqual(sorted(HD.year_files(root)[0]), sorted(snaps))
        self.assertIsNone(HD.year_files(root)[1])
        self.assertIsNone(HD.holdings_year(root))
        top = multi("canada", years=(2024,))
        p = top / "2024" / "taxjson.toml"
        p.write_text(p.read_text().replace(
            'exports_dir = "../exports"',
            'exports_dir = "../exports"\nholdings_dir = "../snaps"'))
        snaps = [_snap(top / "snaps", "margin_a.toml", "2023-12-31",
                       [("QZQ.TO", 50)])]
        self.assertFalse(PL.shared_holdings(top / "2024"))
        self.assertEqual(HD.year_files(top / "2024"), (snaps, None))
        self.assertIsNone(HD.fetch_years(top / "2024")[0])

    def test_the_fx_window_reads_only_the_years_snapshots(self):
        # (an old year's snapshot took the rates back to its date:
        # backlog TJ-5408)
        from taxjson.pipeline.stages import _rates_inputs
        top = multi("canada", years=(2023, 2024))
        _share(top / "2023")
        _share(top / "2024")
        h = top / "holdings"
        _snap(h, "margin_old.toml", "2001-03-01", [("QZQ.TO", 50)])
        mine = _snap(h, "margin_2024.toml", "2024-12-31", [("QZQ.TO", 50)])
        got = _rates_inputs(top / "2024", top / "inputs", {"margin": {}})
        self.assertIn(mine, got)
        self.assertNotIn("margin_old.toml", [p.name for p in got])
        # A folder of the year's own: every file, as before.
        root = single("canada", 2024)
        old = _snap(root / "holdings", "margin_old.toml", "2001-03-01",
                    [("QZQ.TO", 50)])
        self.assertIn(old, _rates_inputs(root, root / "inputs",
                                         {"margin": {}}))

    def test_snapshots_left_in_the_years_own_folder_are_named(self):
        # (holdings_dir set, the files not moved: nothing reads them;
        # backlog TJ-5406)
        top = multi("canada", years=(2024,))
        _share(top / "2024")
        self.assertIsNone(HD.stray_snapshots(top / "2024"))
        _snap(top / "2024" / "holdings", "margin_q4.toml", "2024-12-31",
              [("QZQ.TO", 50)])
        self.assertEqual(
            HD.stray_snapshots(top / "2024"),
            'holdings/ holds 1 positions snapshot(s) this year does not '
            'read (margin_q4.toml): holdings_dir = "../holdings" names '
            '../holdings/ — move them there (rename one whose name is '
            'taken)')
        # The folder's other files are named as the project names it.
        (top / "holdings").mkdir()
        (top / "holdings" / "notes.txt").write_text("mine\n")
        notes = HD.unread_notes(top / "holdings", {}, top / "2024")
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].startswith(
            "../holdings/notes.txt is not read"), notes)


class TestInitLayout(unittest.TestCase):
    """`tjs init`'s year folders share one holdings/ beside inputs/."""

    def test_init_makes_the_shared_folder_and_key(self):
        top = Path(private_dir()) / "taxes"
        r = tjs("init", "--country", "canada", "--year", "2025", str(top))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((top / "holdings" / "README.txt").read_text(),
                         HD.SHARED_README)
        self.assertIn("every year", HD.SHARED_README)
        self.assertFalse((top / "2025" / "holdings").exists())
        self.assertEqual(_settings(top / "2025")["holdings_dir"],
                         "../holdings")
        self.assertEqual(PL.holdings_folder(top / "2025"),
                         (top / "holdings").resolve())
        self.assertIn("positions snapshots in holdings/ (every year)",
                      r.stdout)

    def test_the_demo_too(self):
        top = Path(private_dir()) / "demo"
        r = tjs("init", "--demo", str(top))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((top / "holdings" / "README.txt").is_file())
        self.assertFalse((top / "2024" / "holdings").exists())
        self.assertEqual(_settings(top / "2024")["holdings_dir"],
                         "../holdings")

    def test_a_single_folder_keeps_no_key(self):
        root = Path(private_dir()) / "p"
        r = tjs("init", "--single", "--country", "canada", "--year",
                "2025", str(root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("holdings_dir", _settings(root))

    def test_a_holdings_link_out_of_the_folder_is_refused(self):
        top = Path(private_dir()) / "taxes"
        top.mkdir()
        elsewhere = Path(private_dir())
        (top / "holdings").symlink_to(elsewhere)
        r = tjs("init", "--country", "canada", "--year", "2025", str(top))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("holdings", r.stderr)
        self.assertFalse((top / "2025" / "taxjson.toml").exists())
        self.assertEqual(list(elsewhere.iterdir()), [])


class TestSanityReadsTheSharedFolder(unittest.TestCase):
    """The year's snapshots compared, the others named in one line that
    says how to compare one."""

    @classmethod
    def setUpClass(cls):
        cls.top = multi("canada", years=(2023, 2024, 2025))
        cls.y = cls.top / "2024"
        for y in (2023, 2024, 2025):
            _share(cls.top / str(y))
        h = cls.top / "holdings"
        # The books hold 50 QZQ.TO at the end of 2024, 40 ZZB.TO from
        # 2025-01-08 and no QZQ.TO from 2025-05-01 (years_multi_projects).
        _snap(h, "margin_holdings_2024.toml", "2024-12-31", [("QZQ.TO", 50)])
        _snap(h, "margin_open_2025.toml", "2025-01-10",
              [("QZQ.TO", 50), ("ZZB.TO", 40)])
        _snap(h, "margin_holdings_2025.toml", "2025-06-30", [("ZZB.TO", 40)])
        _snap(h, "margin_holdings_2023.toml", "2023-12-31", [("QZQ.TO", 99)])
        run_ok(unittest.TestCase(), cls.y)

    def test_the_years_snapshots_are_compared_and_the_others_named(self):
        r = tjs("-C", str(self.y), "sanity")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("margin_holdings_2024.toml", r.stdout)
        self.assertIn("margin_open_2025.toml", r.stdout)
        self.assertNotIn("margin_holdings_2025.toml", r.stdout)
        self.assertNotIn("result:    DIFF", r.stdout)
        self.assertIn("result:    OK", r.stdout)
        lines = [ln for ln in r.stderr.splitlines()
                 if "of other years not compared" in ln]
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertIn("../holdings/: 2 snapshot(s)", lines[0])
        self.assertIn("1 dated before 2024-01-01: margin_holdings_2023.toml;"
                      " 1 after 2025-01-15, 2025's: "
                      "margin_holdings_2025.toml", lines[0])
        self.assertIn("`tjs sanity ACCOUNT=../holdings/", lines[0])
        self.assertNotIn("warning:", r.stderr)

    def test_a_left_out_snapshot_is_compared_when_named(self):
        # (the form the note gives: ACCOUNT=FILE, the file as the
        # project names it)
        r = tjs("-C", str(self.y), "sanity",
                "margin=../holdings/margin_holdings_2025.toml")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("margin_holdings_2025.toml", r.stdout)
        self.assertIn("result:    OK", r.stdout)

    def test_the_run_names_sanity_for_the_shared_folder(self):
        self.assertEqual(HD.where_used(self.y, {})[0], "../holdings/")

    def test_a_redacted_copy_carries_only_the_years_snapshots(self):
        # The copy is a single-folder project (its own holdings/): the
        # other years' snapshots are left out, as the year's sanity
        # leaves them, and counted in one note.
        r = tjs("-C", str(self.y), "redact", "--check", "--details")
        # (exit 1: the snapshot's broker id is one it asks to review)
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        self.assertIn("2 positions snapshots of other years left out of "
                      "the copy (2024 compares those dated 2024-01-01 to "
                      "2025-01-15)", " ".join(r.stdout.split()))
        self.assertIn("margin_holdings_2024.toml", r.stdout)
        self.assertNotIn("margin_holdings_2025.toml", r.stdout + r.stderr)


class TestTheChecklistLineCountsTheOthers(unittest.TestCase):
    """The checklist's sanity step names the count of other years'
    snapshots left out and `tjs sanity`, as sanity's note does (backlog
    TJ-5431)."""

    def _ctx(self, root: Path):
        from taxjson.cli.checklist_run import default_run_sub
        from taxjson.lib import checklist as cl
        from taxjson.pipeline.config import load_config
        return cl.Ctx(root=root, cfg=load_config(root), year=2024,
                      today=date(2025, 3, 1), run_sub=default_run_sub(root))

    def _project(self):
        top = multi("canada", years=(2023, 2024, 2025))
        for d in (top / "2023", top / "2024", top / "2025"):
            _share(d)
        h = top / "holdings"
        _snap(h, "margin_holdings_2023.toml", "2023-12-31", [("QZQ.TO", 50)])
        _snap(h, "margin_holdings_2025.toml", "2025-06-30", [("ZZB.TO", 40)])
        return top / "2024"

    def test_only_other_years_snapshots(self):
        from taxjson.lib import checklist as cl
        y = self._project()
        # No books yet: the confirm step, saying the folder's are not
        # this year's.
        res = cl.d_sanity(self._ctx(y))
        self.assertEqual(res.status, "manual", res.detail)
        self.assertIn("no positions snapshot of this year in ../holdings/",
                      res.detail)
        self.assertTrue(res.detail.endswith(
            "; 2 snapshot(s) of other years not compared — `tjs sanity` "
            "names them"), res.detail)
        # The books hold positions: nothing compared, the count kept.
        run_ok(self, y)
        res = cl.d_sanity(self._ctx(y))
        self.assertEqual(res.status, "attention", res.detail)
        self.assertTrue(res.detail.startswith("UNCHECKED"), res.detail)
        self.assertIn("; 2 snapshot(s) of other years not compared — "
                      "`tjs sanity` names them", res.detail)

    def test_the_years_snapshot_tied_and_the_others_counted(self):
        from taxjson.lib import checklist as cl
        y = self._project()
        _snap(y.parent / "holdings", "margin_holdings_2024.toml",
              "2024-12-31", [("QZQ.TO", 50)])
        (y.parent / "holdings" / "tfsa_holdings_2024.toml").write_text(
            '[meta]\nas_of = "2024-12-31"\n\n[[holding]]\n'
            'symbol = "QZQ.TO"\nquantity = 10\ncurrency = "CAD"\n')
        run_ok(self, y)
        res = cl.d_sanity(self._ctx(y))
        self.assertEqual(res.detail, "positions tie to the holdings files; "
                         "2 snapshot(s) of other years not compared — "
                         "`tjs sanity` names them")
        # A folder of the year's own: the line as before.
        root = single("canada", 2024)
        res = cl.d_sanity(self._ctx(root))
        self.assertNotIn("other years", res.detail)


class TestSanitySaysWhatIsLeftInTheYearsFolder(unittest.TestCase):
    """holdings_dir set, the snapshots still in YEAR/holdings/: a
    warning, not silence (backlog TJ-5406)."""

    def test_the_warning(self):
        top = multi("canada", years=(2024,))
        y = top / "2024"
        _share(y)
        _snap(top / "holdings", "margin_holdings_2024.toml", "2024-12-31",
              [("QZQ.TO", 50)])
        _snap(y / "holdings", "margin_q4.toml", "2024-12-31",
              [("QZQ.TO", 50)])
        run_ok(self, y)
        r = tjs("-C", str(y), "sanity")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("tjs sanity: warning: holdings/ holds 1 positions "
                      "snapshot(s) this year does not read "
                      "(margin_q4.toml)", r.stderr)
        self.assertIn("result:    OK", r.stdout)


class TestCloseYearFreezesTheSnapshots(unittest.TestCase):
    """close-year copies what the year compares into the year's own
    holdings/ (the only year folder: every snapshot, of any date); the
    filed year reads its copy; new-year names the shared folder
    again."""

    @classmethod
    def setUpClass(cls):
        cls.top = multi("canada", years=(2024,))
        cls.y = cls.top / "2024"
        _share(cls.y)
        cls.shared = cls.top / "holdings"
        _snap(cls.shared, "margin_holdings_2024.toml", "2024-12-31",
              [("QZQ.TO", 50)])
        _snap(cls.shared, "margin_holdings_2025.toml", "2025-06-30",
              [("ZZB.TO", 40)])
        _snap(cls.shared, "margin_holdings_2023.toml", "2023-12-31",
              [("QZQ.TO", 50)])
        run_ok(unittest.TestCase(), cls.y)
        cls.close = tjs("-C", str(cls.y), "close-year", "--yes")

    def test_the_years_snapshots_are_copied_and_the_key_frozen(self):
        self.assertEqual(self.close.returncode, 0,
                         self.close.stdout + self.close.stderr)
        own = self.y / "holdings"
        self.assertEqual(_names(own.iterdir()),
                         ["margin_holdings_2023.toml",
                          "margin_holdings_2024.toml",
                          "margin_holdings_2025.toml"])
        text = (self.y / "taxjson.toml").read_text().splitlines()
        i = text.index('# holdings_dir = "../holdings"')
        self.assertTrue(text[i - 1].startswith(PL.FROZEN_NOTE_PREFIX),
                        text[i - 1])
        self.assertIn("positions snapshots", text[i - 1])
        self.assertNotIn("holdings_dir", _settings(self.y))
        lock = json.loads((self.y / "filed" / "2024.json").read_text())
        self.assertEqual(lock["frozen"]["holdings_dir"], "../holdings")
        self.assertEqual(lock["frozen"]["holdings_copied"], 3)
        self.assertIn("3 positions snapshot(s) of 2024 frozen from "
                      "../holdings", self.close.stdout)
        # The shared folder keeps every year's.
        self.assertEqual(len(list(self.shared.iterdir())), 3)

    def test_the_filed_year_reads_its_copy(self):
        _snap(self.shared, "margin_holdings_2024.toml", "2024-12-31",
              [("QZQ.TO", 999)])
        self.addCleanup(_snap, self.shared, "margin_holdings_2024.toml",
                        "2024-12-31", [("QZQ.TO", 50)])
        r = tjs("-C", str(self.y), "sanity")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("file:      holdings/margin_holdings_2024.toml",
                      r.stdout)
        self.assertIn("result:    OK", r.stdout)
        self.assertNotIn("of other years", r.stderr)

    def test_new_year_names_the_shared_folder_again(self):
        r = tjs("-C", str(self.top), "new-year", "2025")
        self.assertEqual(r.returncode, 0, r.stderr)
        nxt = self.top / "2025"
        self.assertEqual(_settings(nxt)["holdings_dir"], "../holdings")
        self.assertFalse((nxt / "holdings").exists())
        self.assertNotIn(PL.FROZEN_NOTE_PREFIX,
                         (nxt / "taxjson.toml").read_text())
        self.assertIn("holdings/ (shared)", r.stdout)
        # (the lock named it: no switch to say)
        self.assertNotIn("not a holdings/ of its own", r.stdout + r.stderr)


class TestCloseYearWithTheNextYearsFolder(unittest.TestCase):
    """With 2025's folder there, 2024 freezes the snapshots of its window
    (to 2025-01-15) only; a file an account names in the shared folder is
    said not frozen; a second close keeps the record; unclose-year takes
    back only snapshots (backlog TJ-5405)."""

    def test_close_close_again_and_reopen(self):
        top = multi("canada", years=(2024, 2025))
        y = top / "2024"
        for d in (y, top / "2025"):
            _share(d)
        h = top / "holdings"
        _snap(h, "margin_holdings_2024.toml", "2024-12-31", [("QZQ.TO", 50)])
        _snap(h, "margin_open_2025.toml", "2025-01-10",
              [("QZQ.TO", 50), ("ZZB.TO", 40)])
        _snap(h, "margin_holdings_2025.toml", "2025-06-30", [("ZZB.TO", 40)])
        # The tfsa's own `holdings` names a file in the shared folder.
        (h / "tfsa_named.toml").write_text(
            '[meta]\nas_of = "2024-12-31"\n\n[[holding]]\n'
            'symbol = "QZQ.TO"\nquantity = 10\ncurrency = "CAD"\n')
        _list_holdings(y, "tfsa", '["../holdings/tfsa_named.toml"]')
        run_ok(self, y)
        r = tjs("-C", str(y), "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_names((y / "holdings").iterdir()),
                         ["margin_holdings_2024.toml",
                          "margin_open_2025.toml"])
        self.assertIn("not frozen: tfsa_named.toml in ../holdings/, which "
                      "an account's `holdings = [...]` names", r.stdout)

        # A second close keeps what the first one froze, and says so.
        r = tjs("-C", str(y), "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lock = json.loads((y / "filed" / "2024.json").read_text())
        self.assertEqual(lock["frozen"]["holdings_copied"], 2)
        self.assertEqual(lock["frozen"]["holdings_dir"], "../holdings")
        self.assertIn("2 positions snapshot(s) of 2024 frozen from "
                      "../holdings", r.stdout)
        self.assertIn("not frozen: tfsa_named.toml", r.stdout)

        # A note of yours in the year's holdings/ is no snapshot: it
        # stays where it is, never counted as one.
        (y / "holdings" / "notes.txt").write_text("mine\n")
        r = tjs("-C", str(y), "unclose-year")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_names((y / "holdings").iterdir()), ["notes.txt"])
        self.assertNotIn("positions snapshot(s) not the same", r.stdout)
        self.assertEqual(_settings(y)["holdings_dir"], "../holdings")


class TestNewYearSaysTheSwitch(unittest.TestCase):
    """A year filed with a holdings/ of its own (its lock names no
    holdings_dir) and a holdings/ beside the year folders: the new year
    reads the shared one, and new-year says so (backlog TJ-5405)."""

    def test_the_info_line(self):
        top = multi("canada", years=(2024,))
        y = top / "2024"
        _snap(y / "holdings", "margin_holdings_2024.toml", "2024-12-31",
              [("QZQ.TO", 50)])
        run_ok(self, y)
        r = tjs("-C", str(y), "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (top / "holdings").mkdir()
        r = tjs("-C", str(top), "new-year", "2025")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_settings(top / "2025")["holdings_dir"],
                         "../holdings")
        # (the house note form: `tjs new-year: note: ...`)
        self.assertIn('tjs new-year: note: 2025/taxjson.toml: '
                      'holdings_dir = '
                      '"../holdings" — 2025 compares the positions '
                      'snapshots in the holdings/ beside the year folders',
                      r.stdout + r.stderr)


class TestALockBeforeTheYearEnds(unittest.TestCase):
    """close-year --force on a year that has not ended copies nothing:
    the year keeps reading the shared folder, as it does its inputs."""

    def test_the_key_stays(self):
        year = date.today().year
        top = multi("canada", years=(year,))
        y = top / str(year)
        _share(y)
        _snap(top / "holdings", "margin_now.toml", None, [("ZZB.TO", 0)])
        run_ok(self, y)
        r = tjs("-C", str(y), "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_settings(y)["holdings_dir"], "../holdings")
        self.assertFalse((y / "holdings").exists())
        lock = json.loads((y / "filed" / f"{year}.json").read_text())
        self.assertTrue(lock["frozen"]["partial"])
        self.assertNotIn("holdings_dir", lock["frozen"])


class TestTheFreezeRefuses(unittest.TestCase):
    """The copies are checked before anything is written: a holdings/
    link out of the project, a file already there."""

    def _project(self):
        top = multi("canada", years=(2024, 2025))
        y = top / "2024"
        _share(y)
        _share(top / "2025")
        _snap(top / "holdings", "margin_2024.toml", "2024-12-31",
              [("QZQ.TO", 50)])
        return y, {"accounts": {"margin": {"type": "taxable"}}}

    def _refusal(self, y, cfg):
        # (lib/holdings_freeze, its refusals close-year's errors)
        from taxjson.cli.file import _refuse
        from taxjson.lib.holdings_freeze import copies
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            copies(y, cfg, "Nothing was written.", _refuse)
        return str(cm.exception.code) + err.getvalue()

    def test_a_link_out_of_the_project_is_refused(self):
        y, cfg = self._project()
        elsewhere = Path(private_dir())
        (y / "holdings").symlink_to(elsewhere)
        why = self._refusal(y, cfg)
        self.assertIn("outside the project", why)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_snapshot_already_there_is_never_overwritten(self):
        y, cfg = self._project()
        mine = _snap(y / "holdings", "margin_2024.toml", "2024-06-30",
                     [("QZQ.TO", 7)])
        before = mine.read_bytes()
        why = self._refusal(y, cfg)
        self.assertIn("holds files this project does not read", why)
        self.assertEqual(mine.read_bytes(), before)

    def test_the_copies_are_the_years_snapshots(self):
        y, cfg = self._project()
        _snap(y.parent / "holdings", "margin_2025.toml", "2025-06-30",
              [("ZZB.TO", 40)])
        from taxjson.lib.holdings_freeze import copies
        got = copies(y, cfg, "", self.fail)
        self.assertEqual([(a.name, b) for a, b in got],
                         [("margin_2024.toml",
                           y / "holdings" / "margin_2024.toml")])


class TestMigrateSharedHoldings(unittest.TestCase):
    """An existing project's year folders, each with a holdings/ of its
    own, share one: the plan shown first, nothing written (the
    requires_taxjson stamp neither), done with --write."""

    def setUp(self):
        self.top = multi("canada", years=(2023, 2024, 2025))
        for y in (2024, 2025):
            h = self.top / str(y) / "holdings"
            h.mkdir()
            (h / "README.txt").write_text(HD.README)
            _snap(h, "margin_live_holdings.toml", f"{y}-12-31",
                  [("QZQ.TO", 50)])
        _snap(self.top / "2025" / "holdings", "margin_jan.toml",
              "2026-01-05", [("QZQ.TO", 50)])
        # 2023 is filed, frozen: it keeps its own folder.
        old = self.top / "2023"
        _snap(old / "holdings", "margin_2023.toml", "2023-12-31",
              [("QZQ.TO", 50)])
        (old / "filed").mkdir()
        (old / "filed" / "2023.json").write_text(
            json.dumps({"frozen": {"inputs": {}}}))

    def test_shown_first_then_done_with_write(self):
        before = _bytes(self.top)
        r = tjs("-C", str(self.top / "2025"), "migrate", "--shared-holdings")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Dry run: nothing was changed", r.stdout)
        self.assertIn("2025: would set holdings_dir, move 2 file(s)", r.stdout)
        self.assertIn("margin_live_holdings.toml -> "
                      "margin_live_holdings_2025.toml", r.stdout)
        self.assertIn("Not changed: 2023", r.stdout)
        # Nothing written: no folder, no key, no requires_taxjson stamp
        # (backlog TJ-5403).
        self.assertFalse((self.top / "holdings").exists())
        self.assertEqual(_bytes(self.top), before)
        self.assertNotIn("requires_taxjson", r.stdout + r.stderr)

        r = tjs("-C", str(self.top), "migrate", "--shared-holdings",
                "--write")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # The plan in the future tense, before it is done (TJ-5407).
        self.assertLess(r.stdout.index("2025: will set holdings_dir"),
                        r.stdout.index("Done: every open year"))
        shared = self.top / "holdings"
        self.assertEqual(_names(shared.iterdir()),
                         ["README.txt", "margin_jan.toml",
                          "margin_live_holdings.toml",
                          "margin_live_holdings_2025.toml"])
        self.assertEqual((shared / "README.txt").read_text(),
                         HD.SHARED_README)
        for y in (2024, 2025):
            st = _settings(self.top / str(y))
            self.assertEqual(st["holdings_dir"], "../holdings")
            self.assertIn("requires_taxjson", st)
            self.assertFalse((self.top / str(y) / "holdings").exists())
        # The filed year is left as it was.
        self.assertTrue((self.top / "2023" / "holdings"
                         / "margin_2023.toml").is_file())
        self.assertNotIn("holdings_dir", _settings(self.top / "2023"))
        # Each year compares its own: 2025's January snapshot is 2025's.
        files, _note = HD.year_files(self.top / "2025")
        self.assertEqual(_names(files), ["margin_jan.toml",
                                         "margin_live_holdings_2025.toml"])

    def test_a_sub_folder_stops_it_with_nothing_moved(self):
        (self.top / "2024" / "holdings" / "old").mkdir()
        r = tjs("-C", str(self.top), "migrate", "--shared-holdings",
                "--write")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("2024/holdings/old: a folder or link", r.stderr)
        self.assertFalse((self.top / "holdings").exists())
        self.assertTrue((self.top / "2024" / "holdings"
                         / "margin_live_holdings.toml").is_file())


class TestMigrateTwoYearsOfOneAccount(unittest.TestCase):
    """One folder for two years' files of one account: a file both years
    hold becomes one; undated or same-day snapshots that would be added
    up stop it before anything is written; an account's `holdings`
    paths follow the moved files (backlog TJ-5401, TJ-5402)."""

    def setUp(self):
        self.top = multi("canada", years=(2024, 2025))
        self.own = {y: self.top / str(y) / "holdings" for y in (2024, 2025)}

    def _write(self):
        return tjs("-C", str(self.top), "migrate", "--shared-holdings",
                   "--write")

    def test_one_file_in_two_years_becomes_one(self):
        a = _snap(self.own[2024], "margin_holdings.toml", "2024-12-31",
                  [("QZQ.TO", 50)])
        (self.own[2025]).mkdir()
        (self.own[2025] / a.name).write_bytes(a.read_bytes())
        r = self._write()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2025: will set holdings_dir, remove 1 the same as a "
                      "file there (margin_holdings.toml)", r.stdout)
        # (it is 2024's: 2025 is told which year compares it, and how)
        self.assertIn("dated outside 2025's window: margin_holdings.toml "
                      "(compared by 2024)", r.stdout)
        shared = self.top / "holdings"
        self.assertEqual(_names(shared.iterdir()),
                         ["README.txt", "margin_holdings.toml"])
        self.assertEqual(_names(HD.year_files(self.top / "2024")[0]),
                         ["margin_holdings.toml"])
        # Compared once: the books' 50, not 100.
        run_ok(self, self.top / "2024")
        r = tjs("-C", str(self.top / "2024"), "sanity")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("result:    OK", r.stdout)

    def _refused(self, why):
        before = _bytes(self.top)
        r = self._write()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("2024/holdings/margin_holdings.toml and "
                      "2025/holdings/margin_holdings.toml: account margin's",
                      r.stderr)
        self.assertIn(why, r.stderr)
        self.assertEqual(_bytes(self.top), before)
        self.assertFalse((self.top / "holdings").exists())

    def test_undated_snapshots_of_two_years_are_refused(self):
        _snap(self.own[2024], "margin_holdings.toml", None, [("QZQ.TO", 25)])
        _snap(self.own[2025], "margin_holdings.toml", None, [("QZQ.TO", 30)])
        self._refused('give each its date ([meta] as_of = "YYYY-MM-DD")')

    def test_same_day_snapshots_of_two_years_are_refused(self):
        _snap(self.own[2024], "margin_holdings.toml", "2024-12-31",
              [("QZQ.TO", 50)])
        _snap(self.own[2025], "margin_holdings.toml", "2024-12-31",
              [("QZQ.TO", 49)])
        self._refused("both dated 2024-12-31")

    def test_an_undated_beside_a_dated_one_is_refused(self):
        # (neither names its broker account: in one folder every year
        # compares the undated one beside the dated one, and sanity
        # refuses the pair; backlog TJ-5433)
        for y, name, date_line in ((2024, "margin_now.toml", ""),
                                   (2025, "margin_holdings.toml",
                                    'as_of = "2025-12-31"\n')):
            self.own[y].mkdir(parents=True, exist_ok=True)
            (self.own[y] / name).write_text(
                f"[meta]\n{date_line}\n[[holding]]\nsymbol = \"QZQ.TO\"\n"
                f"quantity = 50\ncurrency = \"CAD\"\n")
        before = _bytes(self.top)
        r = self._write()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("2024/holdings/margin_now.toml and "
                      "2025/holdings/margin_holdings.toml: account margin's "
                      "snapshots of two years, one undated, neither naming "
                      "its broker account", r.stderr)
        self.assertIn('[meta] as_of = "YYYY-MM-DD"', r.stderr)
        self.assertEqual(_bytes(self.top), before)
        self.assertFalse((self.top / "holdings").exists())

    def test_an_accounts_holdings_paths_follow_the_moved_files(self):
        _snap(self.own[2024], "q4.toml", "2024-12-31", [("QZQ.TO", 50)])
        _list_holdings(self.top / "2024", "margin", '["holdings/q4.toml"]')
        r = self._write()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("point [accounts.margin] holdings at the moved "
                      "file(s)", r.stdout)
        cfg = tomllib.loads((self.top / "2024" / "taxjson.toml").read_text())
        self.assertEqual(cfg["accounts"]["margin"]["holdings"],
                         ["../holdings/q4.toml"])
        self.assertTrue((self.top / "holdings" / "q4.toml").is_file())
        run_ok(self, self.top / "2024")
        r = tjs("-C", str(self.top / "2024"), "sanity")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("q4.toml", r.stdout)
        self.assertIn("result:    OK", r.stdout)


class TestToYearsKeepsHoldingsShared(unittest.TestCase):
    """`tjs migrate --to-years` lays the project out as `tjs init` does:
    holdings/ stays at the top, every year's."""

    def test_holdings_stays_beside_inputs(self):
        root = single("canada", 2024)
        snap = _snap(root / "holdings", "margin_2024.toml", "2024-12-31",
                     [("QZQ.TO", 50)])
        (root / "holdings" / "README.txt").write_text(HD.README)
        r = tjs("-C", str(root), "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(snap.is_file())
        self.assertEqual((root / "holdings" / "README.txt").read_text(),
                         HD.SHARED_README)
        self.assertFalse((root / "2024" / "holdings").exists())
        self.assertEqual(_settings(root / "2024")["holdings_dir"],
                         "../holdings")
        self.assertNotIn("Left at the top", r.stdout)


class _Fetcher:
    """A fetcher plugin, in process: it keeps the request."""
    brokerages = ("fakebroker",)
    description = "test fetcher"
    seen: list = []

    def fetch(self, request):
        self.seen.append(request)
        return {a: {} for a in request.accounts}


class TestFetchPositionsIntoTheSharedFolder(unittest.TestCase):
    """`tjs fetch` tells a fetcher the shared folder, the year a snapshot
    taken today is of (its file is named for it) and the year folders
    that compare it (never a year with no folder)."""

    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("TAXJSON_OFFLINE", None)
        ep = SimpleNamespace(name="fake", dist=None, load=lambda: _Fetcher)
        p = mock.patch.object(FP, "_entry_points", return_value=[ep])
        p.start()
        self.addCleanup(p.stop)
        FP.discover(refresh=True)
        self.addCleanup(FP.discover, True)
        _Fetcher.seen.clear()

    def _fetch(self, root: Path):
        from taxjson.cli import setup as cli_setup
        args = argparse.Namespace(account=[], list=False, json=False,
                                  dry_run=False, fetcher=None, dir=str(root))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cli_setup.cmd_fetch(args)
        return _Fetcher.seen[-1]

    def _year(self, top: Path, extra: str) -> Path:
        (top / "inputs" / "a").mkdir(parents=True, exist_ok=True)
        root = top / "2026"
        root.mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\ninputs_dir = "../inputs"\n' + extra
            + '[accounts.a]\ntype = "taxable"\nbrokerage = "fakebroker"\n')
        return root

    def test_the_shared_folder_and_the_year(self):
        top = (Path(private_dir()) / "taxes").resolve()
        (top / "holdings").mkdir(parents=True)
        root = self._year(top, 'holdings_dir = "../holdings"\n')
        req = self._fetch(root)
        today = date.today().isoformat()
        self.assertEqual(req.holdings, top / "holdings")
        self.assertEqual(req.holdings_year, HD.snapshot_year(today))
        self.assertEqual(req.holdings_compared_by,
                         HD.compared_by(root, today))
        # (the newest year folder compares every later snapshot)
        self.assertEqual(HD.compared_by(root, "2027-06-01"), [2026])

    def test_a_folder_of_the_years_own_names_no_year(self):
        top = (Path(private_dir()) / "taxes").resolve()
        root = self._year(top, "")
        req = self._fetch(root)
        self.assertEqual(req.holdings, root / "holdings")
        self.assertIsNone(req.holdings_year)
        self.assertIsNone(req.holdings_compared_by)


if __name__ == "__main__":
    unittest.main()
