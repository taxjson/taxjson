"""Backlog group `closeyear`: close-year, its checklist gate, check-filed
and check-dates.

- TJ-0013 a year close-year froze from the shared exports reads their
  copy, later years' rows and all: check-dates counts them, never calls
  them impossible (the checklist and the re-close gate read it);
- TJ-0015 a run that did not finish is refused even with --force;
- TJ-0035 on a terminal the open items are listed above the question;
- TJ-0089 the crypto_sends.tt the year generated in work/ is frozen into
  its inputs/: no "frozen input changed" alarm after the close;
- TJ-0090 uncommitted inputs (inputs-committed "todo") stop the close;
- TJ-0091 a shared export that changed a row of a filed year is named
  by that year's check-filed;
- TJ-0092 check-filed at the years root, or in an open year, names the
  filed year folders;
- TJ-0117 the handoff step says what handoff found (carry-forwards);
- TJ-0148 a deferred account's stale wash names the pending election,
  and the first refusal does not offer a --force that cannot help;
- TJ-0189 the replaced-lock warning prints the disposition count whole;
- TJ-0192 close-year and `years` show realized and disallowed as the
  RETURN row files them; the lock's totals (the drift check) unchanged.

Synthetic data only (made-up tickers, amounts under a thousand).
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.bin import taxjson_run as run
from taxjson.lib import check_dates
from taxjson.lib import checklist as cl
from taxjson.lib.dispatch import python_module_argv

from test_checklist import _ctx, _project

REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))

# Settlement-dated .tt rows, a margin account read from the shared
# inputs/ beside the year folders. 2023: a gain on ZZQB.TO and one unit
# sold of each of three 3-unit lots (each symbol's row rounds its gain up
# a third of a cent, so the RETURN row is a cent above the engine's
# unrounded total). Later years' rows follow, as in a cumulative export.
_TT = """# made-up trades
BUYSELL 2023-02-01 09:30:00 ZZQB.TO 50 CAD 10.00 -500.00 0.00
BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD 12.00 600.00 0.00
BUYSELL 2023-03-01 09:30:00 ZZQR.TO 3 CAD 3.3333 -10.00 0.00
BUYSELL 2023-03-01 09:30:00 ZZQT.TO 3 CAD 3.3333 -10.00 0.00
BUYSELL 2023-03-01 09:30:00 ZZQU.TO 3 CAD 3.3333 -10.00 0.00
BUYSELL 2023-04-03 09:30:00 ZZQR.TO -1 CAD 5.00 5.00 0.00
BUYSELL 2023-05-01 09:30:00 ZZQT.TO -1 CAD 5.00 5.00 0.00
BUYSELL 2023-06-01 09:30:00 ZZQU.TO -1 CAD 5.00 5.00 0.00
BUYSELL 2023-12-29 09:30:00 ZZQD.TO 10 CAD 10.00 -100.00 0.00
BUYSELL 2024-02-01 09:30:00 ZZQC.TO 10 CAD 40.00 -400.00 0.00
BUYSELL 2025-03-03 09:30:00 ZZQD.TO -10 CAD 12.00 120.00 0.00
BUYSELL 2026-01-05 09:30:00 ZZQF.TO 10 CAD 20.00 -200.00 0.00
"""
_TOML = ('[settings]\nyear = 2023\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         'tax_date = "settle"\nprovince = "ON"\n'
         'inputs_dir = "../inputs"\n\n'
         '[accounts.margin]\ntype = "taxable"\n\n'
         '[estimate]\nother_income = 50000\n')


def _env(**extra):
    """This checkout's code, offline (the rates cached), captured width
    unless `extra` says otherwise."""
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _cli(root, *args, env=None, stdin=subprocess.DEVNULL):
    """`tjs -C ROOT ARGS` as a child of THIS tree's code."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *map(str, args)])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=stdin, env=env or _env(),
                          timeout=900)


def _ok(test, r):
    test.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
    return r


class _Years(unittest.TestCase):
    """A years-layout project (inputs/ beside 2023/), run once for the
    class; each test works on its own copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "base"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(_TT)
        (top / "2023").mkdir()
        (top / "2023" / "taxjson.toml").write_text(_TOML)
        r = _cli(top / "2023", "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.base = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.top = Path(self._t.name) / "p"
        shutil.copytree(self.base, self.top, symlinks=True)
        self.y = self.top / "2023"

    def close(self, *extra):
        return _ok(self, _cli(self.y, "close-year", "--yes", *extra))


class TestCheckDatesFrozenYear(_Years):
    def test_tj0013_later_rows_of_the_frozen_copy_are_not_impossible(self):
        """TJ-0013: after close-year froze 2023, check-dates called every
        2025/2026 row of the copied shared export "far outside the
        project year" (exit 1), and so did the checklist and the
        re-close gate. The frozen copy is the shared folder's: its later
        rows are counted, as before the freeze."""
        before = _ok(self, _cli(self.y, "check-dates", "--json"))
        self.assertEqual(json.loads(before.stdout)["errors"], 0)
        self.close()
        self.assertIn("## frozen by", (self.y / "taxjson.toml").read_text())
        r = _cli(self.y, "check-dates")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("cannot be right", r.stdout)
        doc = json.loads(_ok(self, _cli(self.y, "check-dates",
                                        "--json")).stdout)
        self.assertEqual(doc["errors"], 0, doc["issues"])
        self.assertGreaterEqual(doc["later_years"], 2)
        d = _ok(self, _cli(self.y, "check-dates", "--details")).stdout
        self.assertIn("copy close-year froze", d)

    def test_tj0013_a_single_folder_project_still_errs(self):
        """TJ-0013: the rule is the frozen SHARED copy only — a project
        that never read a shared folder keeps the out-of-range error."""
        root = self.top / "single"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "t.tt").write_text(_TT)
        (root / "taxjson.toml").write_text(
            _TOML.replace('inputs_dir = "../inputs"\n', ""))
        cfg = run.load_config(root)
        _ok(self, _cli(root, "run", "--no-input"))
        doc = check_dates.analyze(root, cfg)
        self.assertGreater(doc["errors"], 0)
        self.assertFalse(doc["later_frozen"])


class TestCloseYearRefusals(_Years):
    def test_tj0015_unfinished_run_refused_even_with_force(self):
        """TJ-0015: close-year --force locked books whose last run did not
        finish (no reports/): an empty filed/<year>/reports/ that
        `compare filed` never matched. Refused, --force not offered."""
        shutil.rmtree(self.y / "reports")
        for extra in ((), ("--force",)):
            r = _cli(self.y, "close-year", "--yes", *extra)
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("did not finish", r.stderr)
            self.assertNotIn("--force", r.stderr)
        self.assertFalse((self.y / "filed" / "2023.json").exists())

    def test_tj0090_uncommitted_inputs_stop_the_close(self):
        """TJ-0090: inputs-committed is "todo" for a dirty git tree, and
        the gate kept only "attention" steps: the year was locked with
        its inputs uncommitted, though the docs list them as a stop."""
        git = shutil.which("git")
        if not git:
            self.skipTest("no git")
        subprocess.run([git, "init", "-q"], cwd=self.top, check=True)
        subprocess.run([git, "add", "-A"], cwd=self.top, check=True)
        r = _cli(self.y, "close-year")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("- inputs-committed:", r.stderr)
        self.assertIn("uncommitted", r.stderr)
        self.assertFalse((self.y / "filed" / "2023.json").exists())

    def test_tj0189_replaced_lock_counts_dispositions_whole(self):
        """TJ-0189: the replaced-lock warning printed `dispositions 4.00
        -> 5.00`; a count is a count."""
        self.close()
        with open(self.y / "inputs" / "margin" / "trades.tt", "a") as f:
            f.write("BUYSELL 2023-09-01 09:30:00 ZZQS.TO 10 CAD 5.00 "
                    "-50.00 0.00\nBUYSELL 2023-10-02 09:30:00 ZZQS.TO -10 "
                    "CAD 6.00 60.00 0.00\n")
        _ok(self, _cli(self.y, "run", "--no-input"))
        r = self.close("--force")
        m = re.search(r"dispositions (\S+) -> (\S+?)[;\s]", r.stderr + " ")
        self.assertIsNotNone(m, r.stderr)
        self.assertEqual(m.groups(), ("4", "5"))

    def test_tj0192_lock_records_the_return_row_shown_by_close_and_years(self):
        """TJ-0192: close-year and `years` showed the engine's unrounded
        total rounded once, a cent off `sum`'s RETURN row (what is
        filed). They show the RETURN row now; the lock's totals, which
        check-filed compares, are unchanged."""
        ret = json.loads(_ok(self, _cli(self.y, "sum", "--json")).stdout)
        ret = ret["filing"]["totals"]
        r = self.close()
        lock = json.loads((self.y / "filed" / "2023.json").read_text())
        rr, tot = lock["return_row"], lock["totals"]
        self.assertEqual(rr["realized"], ret["gain"])
        self.assertEqual(rr["disallowed"], ret["denied"])
        # The case: the per-row cents differ from the unrounded sum.
        self.assertNotEqual(tot["realized"], rr["realized"])
        line = next(ln for ln in r.stdout.splitlines()
                    if ln.strip().startswith("Realized:"))
        self.assertTrue(line.rstrip().endswith(f"{rr['realized']:,.2f}"),
                        line)
        yrs = _ok(self, _cli(self.top, "years")).stdout
        self.assertIn(f"{rr['realized']:,.2f}", yrs)
        self.assertNotIn(f"{tot['realized']:,.2f}", yrs)
        doc = json.loads(_ok(self, _cli(self.top, "years", "--json")).stdout)
        y23 = next(y for y in doc["years"] if y["year"] == 2023)
        self.assertEqual(y23["totals"]["realized"], rr["realized"])
        cf = _ok(self, _cli(self.y, "check-filed"))
        self.assertIn("filed 2023: OK", cf.stdout)
        d = self.close("--force", "--details")
        self.assertIn("Rounding:", d.stdout)


class TestCheckFiled(_Years):
    def test_tj0092_check_filed_names_the_filed_year_folders(self):
        """TJ-0092: at the years root check-filed sent the user to the
        newest (open) year, which answered "No filed/<year>.json
        snapshots" although 2023 is filed."""
        self.close()
        _ok(self, _cli(self.top, "new-year", "2024"))
        r = _cli(self.top, "check-filed")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("filed year's folder", r.stderr)
        self.assertIn(f"-C {self.top / '2023'} check-filed", r.stderr)
        self.assertNotIn(f"-C {self.top / '2024'} check-filed", r.stderr)
        o = _ok(self, _cli(self.top / "2024", "check-filed")).stdout
        self.assertNotIn("No filed/<year>.json snapshots", o)
        self.assertIn("2024 is not closed", o)
        self.assertIn("2023 check-filed`", o)

    def test_tj0091_a_shared_export_correcting_a_filed_row_is_named(self):
        """TJ-0091: a corrected export in the shared folder that changes
        only a filed year's sale was shown nowhere (the filed year reads
        its copy; next year's handoff compares Dec 31 positions and
        cost). The filed year's check-filed names the shared file; rows
        only added (later years') are no difference."""
        self.close()
        shared = self.top / "inputs" / "margin" / "trades.tt"
        with open(shared, "a") as f:
            f.write("BUYSELL 2026-02-02 09:30:00 ZZQF.TO -10 CAD 21.00 "
                    "210.00 0.00\n")
        r = _ok(self, _cli(self.y, "check-filed"))
        self.assertNotIn("shared export", r.stdout)
        shared.write_text(shared.read_text().replace(
            "ZZQB.TO -50 CAD 12.00 600.00", "ZZQB.TO -50 CAD 13.00 650.00"))
        r = _ok(self, _cli(self.y, "check-filed"))
        self.assertIn("1 shared export(s) gained, changed or lost a row "
                      "of 2023",
                      r.stdout)
        self.assertIn("../inputs/margin/trades.tt", r.stdout)
        self.assertIn("filed 2023: OK", r.stdout)

    def test_tj0091_line_year(self):
        """TJ-0091: a line's year is its earliest date (ISO, YYYYMMDD,
        M/D/YYYY); a header with no date has none."""
        self.assertEqual(run._line_year(
            "BUYSELL 2023-12-29 09:30:00 ZZQ.TO 1 CAD 1 1 0"), 2023)
        self.assertEqual(run._line_year("U1234567,20240105,20240102,X"),
                         2024)
        self.assertEqual(run._line_year("12/30/2023,01/03/2024,BUY"), 2023)
        self.assertIsNone(run._line_year("Date,Symbol,Quantity"))
        self.assertIsNone(run._line_year("99900001,20241399,X"))


class TestGate(unittest.TestCase):
    def test_tj0090_gate_keeps_inputs_committed_todo_only(self):
        """TJ-0090: of the steps still "todo", only inputs-committed
        stops close-year (a slip file not in yet does not)."""
        found = [cl.Result("inputs-committed", "todo",
                           "3 uncommitted change(s) under inputs/config"),
                 cl.Result("t5008", "todo", "no slip file"),
                 cl.Result("fx-cash", "attention", "NOT RELIABLE")]
        with tempfile.TemporaryDirectory() as td, \
                patch.object(cl, "evaluate", return_value=found), \
                patch.object(cl, "default_run_sub", return_value=None):
            got = dict(run._close_year_attention(Path(td), {}, 2025))
        self.assertEqual(sorted(got), ["fx-cash", "inputs-committed"])

    def test_tj0117_handoff_step_names_carry_forward_problems(self):
        """TJ-0117: close-year's gate listed `- handoff: — tjs
        handoff`: d_handoff labelled every problem kind but `carry`."""
        rep = json.dumps({"year": 2024, "problems": 1, "positions": [],
                          "missed": [], "double": [], "timing": [],
                          "boundary": [], "partial": [],
                          "carry": [{"key": "other_losses"}]})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            (root / "filed").mkdir()
            (root / "filed" / "2024.json").write_text("{}")
            r = cl.d_handoff(_ctx(root, {"handoff": (1, rep, "")}))
        self.assertEqual(r.status, "attention")
        self.assertTrue(r.detail.startswith(
            "1 carry-forward input(s) differ from the 2024 record"), r.detail)


@unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
class TestTerminalGate(_Years):
    def test_tj0035_items_listed_above_the_question(self):
        """TJ-0035: on a terminal close-year asked "Lock 2023 anyway?"
        under a one-line warning; the items it asks about showed only
        with --details. They are listed in the default view."""
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        try:
            os.write(master, b"n\n")
            r = _cli(self.y, "close-year", stdin=slave,
                     env=_env(TAXJSON_WIDTH="120"))
        finally:
            os.close(slave)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Lock 2023 anyway?", r.stdout)
        self.assertIn("- inputs-committed: not a git repository", r.stderr)
        self.assertIn("2023 not locked", r.stderr)


class _Demo(unittest.TestCase):
    """`tjs init --demo` (years layout, 2024), run once; each test
    works on its own copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "demo"
        r = _cli(Path(cls._td.name), "init", "--demo", str(top))
        assert r.returncode == 0, r.stdout + r.stderr
        r = _cli(top / "2024", "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.base = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.top = Path(self._t.name) / "demo"
        shutil.copytree(self.base, self.top, symlinks=True)
        self.y = self.top / "2024"


class TestDemo(_Demo):
    def test_tj0089_generated_crypto_sends_is_frozen_with_the_year(self):
        """TJ-0089: with a crypto gift decided, the year's crypto_sends.tt
        lived in work/ when close-year froze it; every later run and
        check-filed said the frozen input inputs/crypto/crypto_sends.tt
        changed ("restore them"), compare filed listed it added."""
        cb = next((self.top / "inputs" / "crypto").glob("coinbase*.csv"))
        with open(cb, "a") as f:
            f.write("CB-DEMO-0099,2024-11-20 15:00:00 UTC,Send,ETH,"
                    "0.10000000,USD,3100.00,310.00,310.00,0.00,Sent 0.10 "
                    "ETH to 0xZZZZ0000\n")
        _ok(self, _cli(self.y, "run", "--no-input"))
        _ok(self, _cli(self.y, "crypto-sends", "crypto", "--set",
                       "cb-20241120T100000-ETH-0.1=gift", "--write"))
        self.assertTrue((self.y / "work" / "crypto_sends" / "crypto"
                         / "crypto_sends.tt").is_file())
        _ok(self, _cli(self.y, "run", "--no-input"))
        _ok(self, _cli(self.y, "close-year", "--yes"))
        lock = json.loads((self.y / "filed" / "2024.json").read_text())
        self.assertIn("inputs/crypto/crypto_sends.tt",
                      lock["frozen"]["inputs"])
        r = _ok(self, _cli(self.y, "run", "--no-input"))
        self.assertNotIn("frozen input", r.stdout + r.stderr)
        r = _ok(self, _cli(self.y, "check-filed"))
        self.assertNotIn("frozen input", r.stdout + r.stderr)
        r = _ok(self, _cli(self.y, "compare", "filed"))
        self.assertNotIn("added", r.stdout)
        # A lock an older close-year wrote (no record of the file): the
        # generated copy is no changed input either.
        del lock["frozen"]["inputs"]["inputs/crypto/crypto_sends.tt"]
        (self.y / "filed" / "2024.json").write_text(json.dumps(lock))
        r = _ok(self, _cli(self.y, "check-filed"))
        self.assertNotIn("frozen input", r.stdout + r.stderr)
        r = _ok(self, _cli(self.y, "compare", "filed"))
        self.assertNotIn("added", r.stdout)

    def test_tj0148_pending_election_named_and_force_not_offered(self):
        """TJ-0148: with margin deferred at a pending election, close-year
        said "pass --force to lock them anyway"; --force then refused on
        STALE wash gains blaming "A --account rerun" that never ran."""
        _ok(self, _cli(self.y, "elect", "margin", "--reset"))
        r = _cli(self.y, "run", "--no-input")
        self.assertEqual(r.returncode, 3, r.stdout[-1500:] + r.stderr[-1500:])
        r = _cli(self.y, "close-year", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pending elections", r.stderr)
        self.assertNotIn("--force", r.stderr)
        r = _cli(self.y, "close-year", "--yes", "--force")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("deferred margin at pending elections", r.stderr)
        self.assertNotIn("--account rerun", r.stderr)
        self.assertFalse((self.y / "filed" / "2024.json").exists())


class TestHelpers(unittest.TestCase):
    def test_tj0089_freeze_copies_the_generated_file(self):
        """TJ-0089: _freeze_inputs copies work/crypto_sends/<acct>/
        crypto_sends.tt into inputs/<acct>/ and the record hashes it; an
        older taxjson's generated copy in the shared folder (never read
        by the books) is not copied."""
        from taxjson.lib import crypto_sends as cs
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            sh = top / "inputs" / "c"
            sh.mkdir(parents=True)
            (sh / "x.tt").write_text("BUYSELL 2023-02-01 09:30:00 ZZQ.TO "
                                     "1 CAD 1 -1.00 0.00\n")
            (sh / cs.TT_NAME).write_text(cs.GENERATED_MARK + "\n# old\n")
            root = top / "2023"
            gen = root / "work" / cs.GENERATED_DIR / "c" / cs.TT_NAME
            gen.parent.mkdir(parents=True)
            gen.write_text(cs.GENERATED_MARK + "\n# this year's\n")
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2023\ncountry = "canada"\n'
                'base_currency = "CAD"\ninputs_dir = "../inputs"\n'
                'local_timezone = "America/Toronto"\n\n'
                '[accounts.c]\ntype = "taxable"\ncrypto = true\n')
            cfg = run.load_config(root)
            with contextlib.redirect_stdout(io.StringIO()):
                rec = run._freeze_inputs(root, cfg, cfg["settings"], 2023,
                                         nothing="")
            own = root / "inputs" / "c" / cs.TT_NAME
            self.assertIn("this year's", own.read_text())
            self.assertIn("inputs/c/crypto_sends.tt", rec["inputs"])
            self.assertEqual(rec["copied"], 1)
