"""Backlog fix round 3 of 2026-10-29, group `commands`: exit codes and
messages.

TJ-0353  sanity's refusals of books it cannot read (and of a missing
         gains file) exit 2, an input, not 1, the verdict "positions
         differ" (the global exit-code table, docs/commands.md).
TJ-0596  the query views' refusal of a damaged work/ file exits 2 and
         names it work/<file>.
TJ-0601  `wash-radar <sheltered account>` is a usage error: exit 2.
TJ-0602  corp-actions' note names the pending elections file
         work/<account>_pending_elections.json, not its absolute path.
TJ-0604  align's failed write names the file from the shell's folder
         (2025/ticker.map), with the fix.
TJ-0605  the checklist's handoff step over a closed year's deleted lock
         says to restore it or close that year again (in its folder),
         never to set the prior_year_record that is set.
TJ-0610  check-filed and `years` compare a year closed as a single
         folder, then migrated, with the inputs/ beside the year
         folders (its lock names no shared folder).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account names (margin, rrsp), ids like 99900001,
amounts under 1,000.
"""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]

_CA = ('[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
       'source_currencies = []\n')
_TRADES = "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 100 100 0\n"


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _run_project(tmp: Path, trades: str = _TRADES, extra: str = "",
                 more_inputs=None) -> Path:
    """A Canada project of one taxable account, `margin` (and the
    accounts `extra` declares, whose .tt `more_inputs` gives), built by
    `tjs run`."""
    root = tmp / "p"
    (root / "inputs/margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        _CA + '[accounts.margin]\ntype = "taxable"\n' + extra)
    (root / "inputs/margin/trades.tt").write_text(trades)
    for name, text in (more_inputs or {}).items():
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "trades.tt").write_text(text)
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return root


def _gains_file(root: Path) -> Path:
    for name in ("margin_gains_wash.json", "margin_gains.json"):
        if (root / "work" / name).exists():
            return root / "work" / name
    raise AssertionError("no gains file")


class TestTJ0353SanityUnreadableBooksExit2(unittest.TestCase):
    """TJ-0353: sanity refused books it could not read with exit 1, the
    status of its verdict "positions differ"; docs/commands.md's table
    ("the same for every command") says 2 for an input that cannot be
    read. Every such refusal is now exit 2, and the run and the
    checklist still show the Error and what to do."""

    def test_every_unreadable_books_refusal_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td))
            h = Path(td) / "margin_positions.toml"
            h.write_text('[[holding]]\nsymbol = "ZZQ.TO"\nquantity = 1\n')
            r = _tjs(root, "sanity", f"margin={h}")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # A difference in positions stays the verdict's exit 1.
            h.write_text('[[holding]]\nsymbol = "ZZQ.TO"\nquantity = 2\n')
            r = _tjs(root, "sanity", f"margin={h}")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            g = _gains_file(root)
            good = g.read_text()
            g.write_text("{ broken")
            # Named in the arguments, and from taxjson.toml (no
            # arguments).
            for args in (("sanity", f"margin={h}"), ("sanity",),
                         ("sanity", "--json")):
                with self.subTest(args=args):
                    r = _tjs(root, *args)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertEqual(r.stdout, "")
                    self.assertIn("the books of 'margin' cannot be read",
                                  r.stderr)
            # A dated snapshot reads the base books: one that is not
            # JSON is refused the same way (B18).
            g.write_text(good)
            h.write_text('[meta]\nas_of = "2025-01-03"\n[[holding]]\n'
                         'symbol = "ZZQ.TO"\nquantity = 1\n')
            (root / "work" / "margin_base.json").write_text("{")
            r = _tjs(root, "sanity", f"margin={h}")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("the books of 'margin' cannot be read", r.stderr)
            # No gains file at all: an input missing.
            for p in (root / "work").glob("margin_*gains*.json"):
                p.unlink()
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("run `tjs run`", r.stderr)

    def test_the_run_and_the_checklist_still_name_it(self):
        from taxjson.bin import taxjson_run as tr
        from taxjson.bin.taxjson_run import load_config
        from taxjson.lib import checklist as cl
        from datetime import date
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td))
            _gains_file(root).write_text("{ broken")
            # The run's holdings check on a terminal: the Error is on
            # stderr, and the run's warning points at it.
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                tr._sanity_console(root)
            self.assertEqual(cm.exception.code, tr._SANITY_REFUSED)
            self.assertIn("the books of 'margin' cannot be read",
                          err.getvalue())
            self.assertIn("re-run `tjs run`", err.getvalue())
            # The checklist's step: attention, naming the books, and its
            # Next is the run that rebuilds them.
            ctx = cl.Ctx(root=root, cfg=load_config(root), year=2025,
                         today=date(2026, 3, 1),
                         run_sub=cl.default_run_sub(root))
            step = cl.d_sanity(ctx)
            self.assertEqual(step.status, "attention", step.detail)
            self.assertIn("the books of 'margin' cannot be read",
                          step.detail)
            r = _tjs(root, "checklist", "--run", "--only", "sanity")
            text = " ".join(r.stdout.split())
            self.assertIn("the books of 'margin' cannot be read", text)
            self.assertIn("Next (step", text)
            self.assertIn("sanity): `tjs run`", text)


class TestTJ0596QueryViewsExit2OnADamagedWorkFile(unittest.TestCase):
    """TJ-0596: `tjs shares`, `tjs list` and `tjs wash-sales` stopped on
    a work/ gains file that is not JSON (or `{}`) with exit 1, the
    status of a command's finding; an input that cannot be read is 2.
    The file is named by its project-relative path."""

    def test_views_exit_2_naming_the_work_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td))
            g = _gains_file(root)
            for text in ("{", "{}"):
                g.write_text(text)
                for argv in (("shares", "--json"), ("list",),
                             ("wash-sales",)):
                    with self.subTest(text=text, argv=argv):
                        r = _tjs(root, *argv)
                        self.assertEqual(r.returncode, 2,
                                         r.stdout + r.stderr)
                        err = " ".join(r.stderr.split())
                        self.assertIn(f"could not read work/{g.name}", err)
                        self.assertNotIn(str(root), err)
                        self.assertIn("rerun `tjs run`", err)


class TestTJ0601WashRadarShelteredAccountExit2(unittest.TestCase):
    """TJ-0601: `tjs wash-radar rrsp` (rrsp declared sheltered) refused
    the account with exit 1, a verdict's status; an unknown account
    exits 2 (TJ-0157). A wrong account for the radar is a usage error:
    exit 2 too."""

    def test_a_sheltered_account_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(
                Path(td),
                extra='[accounts.rrsp]\ntype = "sheltered"\n',
                more_inputs={"rrsp": _TRADES})
            self.assertTrue((root / "work" / "rrsp_base.json").is_file())
            r = _tjs(root, "wash-radar", "rrsp")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertIn("rrsp is a sheltered account", r.stderr)
            self.assertIn("TAXABLE loss sales", r.stderr)
            # An unknown account: 2 as before; the taxable one runs.
            r = _tjs(root, "wash-radar", "nosuch")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            r = _tjs(root, "wash-radar", "margin")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# A 2024 single-folder project (a loss of 25), made yearly by
# `migrate --to-years`, run and closed, then 2025 started with new-year
# (its prior_year_record names ../2024/filed/2024.json) and run.
_CA24_TOML = ('[settings]\nyear = 2024\ncountry = "canada"\n'
              'base_currency = "CAD"\nsource_currencies = []\n'
              'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
_LOSS24 = ("# made-up trades\n"
           "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
           "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 5.00 25.00 0.00\n")


def _closed_2024_and_2025(tmp: Path) -> Path:
    top = tmp / "p"
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "inputs" / "margin" / "trades.tt").write_text(_LOSS24)
    (top / "taxjson.toml").write_text(_CA24_TOML)
    for d, args in ((top, ("migrate", "--to-years")),
                    (top / "2024", ("run", "--no-input")),
                    (top / "2024", ("close-year", "--yes")),
                    (top / "2024", ("new-year", "2025")),
                    (top / "2025", ("run", "--no-input"))):
        r = _tjs(d, *args)
        if r.returncode != 0:
            raise AssertionError((args, r.stdout[-2000:], r.stderr[-2000:]))
    return top


class TestTJ0605HandoffStepFitsAMissingLock(unittest.TestCase):
    """TJ-0605: with [settings] prior_year_record set and the 2024 lock
    deleted (TJ-0272's case), the checklist's handoff step showed the
    TJ-0272 message, then its standing advice "Set [settings]
    prior_year_record to last year's filed/<year>.json", a setting
    already there; its `! ` line named `tjs close-year`, which in 2025/
    closes 2025. The step now says to restore the lock or close 2024
    again, naming that year's folder."""

    def test_the_step_says_restore_or_reclose_not_set_the_key(self):
        with tempfile.TemporaryDirectory() as td:
            top = _closed_2024_and_2025(Path(td))
            (top / "2024" / "filed" / "2024.json").unlink()
            r = _tjs(top / "2025", "checklist", "--run", "--only",
                     "handoff")
            text = " ".join(r.stdout.split())
            self.assertIn("the 2024 close-year lock is missing", text)
            self.assertNotIn("Set [settings] prior_year_record", text)
            self.assertIn("— tjs -C ../2024 close-year", text)
            self.assertIn("Next (step", text)
            nxt = text[text.index("Next (step"):]
            self.assertIn("restore ../2024/filed/2024.json from git or a "
                          "backup, or close 2024 again: `tjs -C ../2024 "
                          "close-year`", nxt)

    def test_no_setting_keeps_the_advice_to_set_it(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                _CA24_TOML.replace("2024", "2025"))
            it = next(i for i in cl.items(2025, "canada")
                      if i.id == "handoff")
            self.assertIn("Set [settings] prior_year_record",
                          cl._project_cmds(it, root, 2025).how)
            (root / "taxjson.toml").write_text(
                _CA24_TOML.replace("2024", "2025").replace(
                    'province = "ON"\n',
                    'province = "ON"\nprior_year_record = '
                    '"../2024/filed/2024.json"\n'))
            how = cl._project_cmds(it, root, 2025).how
            self.assertNotIn("Set [settings] prior_year_record", how)
            self.assertIn("prior_year_record", how)


class TestTJ0610CheckFiledAfterAMigratedFreeze(unittest.TestCase):
    """TJ-0610: a year closed as a single-folder project, then moved
    into its year folder by `migrate --to-years`, has a lock that names
    no shared folder (frozen.inputs_dir null), while the years after it
    read the inputs/ beside the year folders (new-year, check-dates:
    TJ-0435). check-filed's and `years`' comparisons of the shared
    exports (TJ-0091, TJ-0421, TJ-0210) and decisions (TJ-0211) skipped
    such a year: a late correction saved in ../inputs was named
    nowhere."""

    _TRADES = ("# made-up trades\n"
               "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 "
               "0.00\n"
               "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 20.00 100.00 "
               "0.00\n")

    def _migrated(self, tmp: Path) -> Path:
        top = tmp / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(self._TRADES)
        (top / "taxjson.toml").write_text(_CA24_TOML)
        for d, args in ((top, ("run", "--no-input")),
                        (top, ("close-year", "--yes")),
                        (top, ("migrate", "--to-years")),
                        (top / "2024", ("run", "--no-input"))):
            r = _tjs(d, *args)
            if r.returncode != 0:
                raise AssertionError((args, r.stdout[-2000:],
                                      r.stderr[-2000:]))
        return top

    def test_a_late_correction_in_the_shared_folder_is_named(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            top = self._migrated(Path(td))
            fz = json.loads((top / "2024" / "filed" / "2024.json")
                            .read_text())["frozen"]
            self.assertIsNone(fz["inputs_dir"])
            r = _tjs(top / "2024", "check-filed")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("shared export", r.stdout + r.stderr)
            shared = top / "inputs" / "margin" / "trades.tt"
            shared.write_text(self._TRADES.replace(
                "-5 CAD 20.00 100.00", "-5 CAD 30.00 150.00"))
            (top / "inputs" / "margin" / "manifest.json").write_text(
                json.dumps({"elections": {"ZZQ.TO-2024-03-01-merger": {
                    "summary": "ZZQ.TO merger 2024-03-01",
                    "election": "taxable"}}}))
            r = _tjs(top / "2024", "check-filed")
            said = " ".join((r.stdout + r.stderr).split())
            self.assertIn("note: filed 2024: 1 shared export(s) gained, "
                          "changed or lost a row of 2024 or earlier since "
                          "the year was closed: ../inputs/margin/trades.tt",
                          said)
            self.assertIn("note: filed 2024: ../inputs/margin/manifest.json "
                          "(1 entry) in the shared folder holds a decision "
                          "on 2024", said)
            # `years` marks the year as well (TJ-0210).
            r = _tjs(top, "years", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            y = next(x for x in json.loads(r.stdout)["years"]
                     if x["year"] == 2024)
            self.assertEqual(sorted(y["shared_exports_changed"]),
                             ["inputs/margin/manifest.json",
                              "inputs/margin/trades.tt"])

    def test_a_later_years_row_is_no_difference(self):
        with tempfile.TemporaryDirectory() as td:
            top = self._migrated(Path(td))
            shared = top / "inputs" / "margin" / "trades.tt"
            shared.write_text(self._TRADES + "BUYSELL 2025-02-03 10:00:00 "
                              "ZZQ.TO -5 CAD 20.00 100.00 0.00\n")
            r = _tjs(top / "2024", "check-filed")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("shared export", r.stdout + r.stderr)


class TestTJ0602PendingElectionsNoteIsProjectRelative(unittest.TestCase):
    """TJ-0602: with an election needed and no terminal, corp-actions'
    note in work/<account>_<broker>_corp.json.diag named the pending
    file by the absolute path the run passed; the run names its work/
    files from the project."""

    def test_the_note_names_work_file(self):
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "D"
            r = _tjs(None, "init", "--demo", str(demo))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # The demo's saved spin-off election gone: it is needed.
            (demo / "inputs" / "margin" / "manifest.json").write_text(
                '{"elections": {}}\n')
            r = _tjs(demo / "2024", "run", "--account", "margin",
                     "--no-input")
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            diags = sorted((demo / "2024" / "work").glob(
                "margin_*corp.json.diag"))
            self.assertTrue(diags)
            text = "".join(p.read_text() for p in diags)
            self.assertIn("taxjson-corp-actions: note: pending elections "
                          "written to work/margin_pending_elections.json",
                          text)
            self.assertNotIn(str(Path(td)), text)
            self.assertNotIn(str(Path(td).resolve()), text)


class TestTJ0604AlignNamesTheFileFromHere(unittest.TestCase):
    """TJ-0604: `tjs -C 2025 align --from 2024 --write --all` run from
    the folder holding the years, with 2025/ticker.map read-only, said
    "could not write ticker.map: …" — the bare name, as if in the shell's
    folder. Named from where the command was run (2025/ticker.map), with
    the fix, as format and format-map do (cli_diag.cannot_write)."""

    def _years(self, d: Path) -> Path:
        top = d / "p"
        for year in (2024, 2025):
            y = top / str(year)
            y.mkdir(parents=True)
            (y / "taxjson.toml").write_text(
                f'[settings]\nyear = {year}\ncountry = "canada"\n'
                f'base_currency = "CAD"\nsource_currencies = []\n'
                f'inputs_dir = "../inputs"\n'
                f'[accounts.margin]\ntype = "taxable"\n')
            (y / "ticker.map").write_text(
                "GLOBAL ZZOLD.TO ZZNEW.TO\n" if year == 2024 else "")
        (top / "inputs" / "margin").mkdir(parents=True)
        return top

    def test_a_read_only_map_is_named_from_here(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root writes a read-only file")
        with tempfile.TemporaryDirectory() as td:
            top = self._years(Path(td))
            tm = top / "2025" / "ticker.map"
            before = tm.read_bytes()
            tm.chmod(0o444)
            r = _tjs(top / "2025", "align", "--from", "2024", "--write",
                     "--all", cwd=top)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            err = " ".join(r.stderr.split())
            self.assertIn("cannot write 2025/ticker.map: the file is "
                          "read-only", err)
            self.assertIn("chmod u+w 2025/ticker.map", err)
            self.assertIn("Nothing was changed.", err)
            self.assertEqual(tm.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
