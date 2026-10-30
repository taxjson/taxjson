"""The 2026-10-29 fix round 7 (wave C), close-year group: project files
and filed years.

- TJ-0922 align no longer says the order of the [accounts.NAME] tables
  decides how same-moment trades in two accounts book: the engine takes
  them in the accounts' name order;
- TJ-0939 identical comment-only .tt files in two accounts are not
  "the same export booked twice": a strict run calculates;
- TJ-0937 `format --write` on a taxjson.toml that is a link inside the
  project formats the link's target and keeps the link;
- TJ-0923 a kept checklist verdict is stale after an edit to the
  taxjson.toml settings only the checks read ([estimate] other_income);
- TJ-0917 close-year checks the lock's folder before it freezes the
  inputs, and undoes the freeze when the lock cannot be written: a
  close that says "Nothing was written" leaves the project reading its
  shared exports;
- TJ-0918 close-year's freeze refuses a read-only taxjson.toml before
  anything is copied, and writes through a taxjson.toml that is a link
  inside the project (the link kept);
- TJ-0924 close-year holds the run lock: a `taxjson run` started while
  it closes refuses, so the lock describes one set of books;
- TJ-0929 an input edited while a full run reads the inputs leaves the
  books stale (the run's record holds the version it started with),
  never "the result of the current inputs";
- TJ-0941 RENAME lines of one change are one event only when all of
  them fall within the seven-day window: a middle line no longer joins
  two lines twelve days apart;
- TJ-0945 update-tobase-map orders the master's generations by number
  (`.10` after `.9`), and a pair added in the file's own generation and
  deleted by the user stays out (recorded as an opt-out);
- TJ-0938 format-map refuses two accounts' .tt files that are links to
  one file before anything is written (the second write dropped the
  first's RENAME line);
- TJ-0940 format-map before the first run refuses to move a legacy
  RENAME to one account kind while an account of the other kind has
  exports but no books yet (its scope is unknown): run first.

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).
"""
import json
import os
import stat
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (a synthetic HOME, offline)
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent

CFG = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
       'country = "canada"\nbase_currency = "CAD"\n'
       'source_currencies = []\n')


def tjs(root, *args, env_extra=None):
    """`taxjson -C root <args>`, captured at width 0, offline."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               NO_COLOR="1",
               PYTHONPATH=str(REPO_ROOT / "src") + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    env.update(env_extra or {})
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=root, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


def out(r) -> str:
    return (r.stdout or "") + (r.stderr or "")


def project(p: Path, year=2025, accounts=("margin",), extra="",
            kinds=None):
    """A single-folder Canadian project in `p` (each account with an
    inputs/<name>/ folder) and an empty ticker.map."""
    kinds = kinds or {}
    p.mkdir(parents=True, exist_ok=True)
    (p / "taxjson.toml").write_text(
        CFG.format(year=year) + extra + "".join(
            f'[accounts.{a}]\ntype = "{kinds.get(a, "taxable")}"\n'
            for a in accounts))
    for a in accounts:
        (p / "inputs" / a).mkdir(parents=True, exist_ok=True)
    (p / "ticker.map").write_text("")


def tt(p: Path, account: str, text: str, name="trades.tt"):
    (p / "inputs" / account / name).write_text(text)


class AlignOrderAdvice(unittest.TestCase):
    """TJ-0922."""

    def test_order_note_names_the_engine_order(self):
        with tempfile.TemporaryDirectory() as d:
            top = Path(d)
            project(top / "2024", 2024, accounts=("b", "a"))
            project(top / "2025", 2025, accounts=("a", "b"))
            r = tjs(top / "2025", "align", "--from", "2024", "--write",
                    "--all")
            self.assertEqual(r.returncode, 0, out(r))
            text = " ".join(out(r).split())
            self.assertIn("another order", text)
            self.assertNotIn("move the tables by hand", text)
            self.assertNotIn("warning: the accounts are in another", text)
            self.assertIn("name order", text)
            r = tjs(top / "2025", "align", "--from", "2024", "--write",
                    "--all", "--json")
            self.assertEqual(r.returncode, 0, out(r))
            doc = json.loads(r.stdout)
            self.assertFalse(any("another order" in w
                                 for w in doc["warnings"]), doc)


class CommentOnlyTtFiles(unittest.TestCase):
    """TJ-0939."""

    def test_identical_comment_only_files_do_not_stop_strict(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            project(p, accounts=("a", "b"))
            tt(p, "a", "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                       "BUYSELL 2025-06-02 10:00:00 SYN.TO -10 CAD 12 120 0\n")
            tt(p, "b", "BUYSELL 2025-01-03 10:00:00 SYM.TO 10 CAD 10 100 0\n"
                       "BUYSELL 2025-06-03 10:00:00 SYM.TO -10 CAD 12 120 0\n")
            for a in ("a", "b"):
                tt(p, a, "# Manual adjustments, none yet\n\n",
                   name="manual.tt")
            r = tjs(p, "run", "--strict", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            self.assertNotIn("booked twice", out(r))

    def test_identical_rows_still_stop_strict(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            project(p, accounts=("a", "b"))
            rows = ("# copied\n"
                    "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n")
            tt(p, "a", rows)
            tt(p, "b", rows)
            r = tjs(p, "run", "--strict", "--no-input")
            self.assertNotEqual(r.returncode, 0, out(r))


class FormatConfigLink(unittest.TestCase):
    """TJ-0937."""

    def test_format_writes_through_an_internal_link(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            project(p)
            cfg = p / "taxjson.toml"
            real = p / "config-real.toml"
            real.write_text("# my settings\n" + cfg.read_text())
            cfg.unlink()
            cfg.symlink_to("config-real.toml")
            r = tjs(p, "format", "--write")
            self.assertEqual(r.returncode, 0, out(r))
            self.assertTrue(cfg.is_symlink(), out(r))
            self.assertEqual(os.readlink(cfg), "config-real.toml")
            self.assertIn("[settings]", real.read_text())
            self.assertNotEqual(real.read_text(),
                                "# my settings\n" + CFG.format(year=2025)
                                + '[accounts.margin]\ntype = "taxable"\n')
            r = tjs(p, "format", "--check")
            self.assertEqual(r.returncode, 0, out(r))


class ChecklistPlanningFreshness(unittest.TestCase):
    """TJ-0923."""

    def test_estimate_edit_makes_the_kept_verdict_stale(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            project(p)
            tt(p, "margin",
               "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
               "BUYSELL 2025-06-02 10:00:00 SYN.TO -10 CAD 20 200 0\n")
            r = tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            r = tjs(p, "checklist", "--run", "--only", "estimate", "--json")
            self.assertEqual(json.loads(r.stdout)["stale"], [], out(r))
            # A comment alone changes nothing.
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text() + "# a note\n")
            r = tjs(p, "checklist", "--only", "estimate", "--json")
            self.assertEqual(json.loads(r.stdout)["stale"], [], out(r))
            cfg.write_text(cfg.read_text()
                           + "\n[estimate]\nother_income = 500\n")
            r = tjs(p, "checklist", "--only", "estimate", "--json")
            stale = json.loads(r.stdout)["stale"]
            self.assertTrue(stale, out(r))
            self.assertIn("planning settings", " ".join(stale))
            # The run's own record is untouched: the books are current.
            from taxjson.lib.checklist import inputs_changed
            from taxjson.bin.taxjson_run import load_config
            self.assertEqual(inputs_changed(p, load_config(p)), "")
            r = tjs(p, "checklist", "--run", "--only", "estimate", "--json")
            self.assertEqual(json.loads(r.stdout)["stale"], [], out(r))


BOOK = ("BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
        "BUYSELL 2025-06-02 10:00:00 SYN.TO -10 CAD 15 150 0\n")


def shared_year(top: Path) -> Path:
    """top/2025, a year reading the shared top/inputs/ (one account,
    a gain of 50), its books built."""
    p = top / "2025"
    project(p, extra='inputs_dir = "../inputs"\n')
    (p / "inputs").rename(top / "inputs")
    (top / "inputs" / "margin" / "trades.tt").write_text(BOOK)
    r = tjs(p, "run", "--no-input")
    assert r.returncode == 0, out(r)
    return p


def driver(tmp: Path, root: Path, body: str, *args: str) -> Path:
    """A script that patches taxjson_run (`body`, with `tr` the module)
    and then runs `taxjson -C root <args>`."""
    d = tmp / "driver.py"
    d.write_text(
        "import sys, time\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
        "from taxjson.bin import taxjson_run as tr\n"
        + body +
        f"\nsys.argv = ['tjs', '-C', {str(root)!r}, *{list(args)!r}]\n"
        "tr.main()\n")
    return d


def run_driver(d: Path, **kw):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               NO_COLOR="1", PYTHONPATH=str(REPO_ROOT / "src"))
    return subprocess.Popen([sys.executable, str(d)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, stdin=subprocess.DEVNULL,
                            **kw)


class CloseYearPublication(unittest.TestCase):
    """TJ-0917."""

    def test_a_file_at_filed_is_refused_before_the_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            p = shared_year(Path(d))
            cfg = p / "taxjson.toml"
            before = cfg.read_bytes()
            (p / "filed").write_text("a note\n")
            r = tjs(p, "close-year", "--yes")
            self.assertNotEqual(r.returncode, 0, out(r))
            self.assertIn("Nothing was written", out(r))
            self.assertEqual(cfg.read_bytes(), before)
            self.assertFalse((p / "inputs" / "margin" / "trades.tt")
                             .exists(), out(r))

    def test_a_failed_lock_write_undoes_the_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            p = shared_year(Path(d))
            cfg = p / "taxjson.toml"
            before = cfg.read_bytes()
            body = ("from taxjson.bin import taxjson_filed as tf\n"
                    "def boom(*a, **kw):\n"
                    "    from taxjson.lib.out import fail\n"
                    "    fail('cannot write filed/2025.json: disk full',\n"
                    "         prog='taxjson close-year',\n"
                    "         details=['Nothing was written.'])\n"
                    "tf.write_snapshot = boom\n")
            proc = run_driver(driver(Path(d), p, body, "close-year",
                                     "--yes"))
            so, se = proc.communicate(timeout=600)
            self.assertNotEqual(proc.returncode, 0, so + se)
            self.assertIn("Nothing was written", so + se)
            self.assertEqual(cfg.read_bytes(), before)
            self.assertFalse((p / "inputs").exists(), so + se)
            self.assertFalse((p / "filed" / "2025.json").exists())
            # The year still reads the shared exports: a correction
            # there reaches the books.
            book = Path(d) / "inputs" / "margin" / "trades.tt"
            book.write_text(BOOK.replace("15 150", "18 180"))
            r = tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            r = tjs(p, "form-export", "--json")
            self.assertAlmostEqual(json.loads(r.stdout)["gain_unrounded"],
                                   80.0, places=2)


class CloseYearConfigWriter(unittest.TestCase):
    """TJ-0918."""

    def test_a_read_only_config_is_refused_before_the_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            p = shared_year(Path(d))
            cfg = p / "taxjson.toml"
            before = cfg.read_bytes()
            cfg.chmod(0o444)
            try:
                r = tjs(p, "close-year", "--yes")
                self.assertNotEqual(r.returncode, 0, out(r))
                self.assertIn("read-only", out(r))
                self.assertEqual(cfg.read_bytes(), before)
                self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o444)
                self.assertFalse((p / "inputs").exists(), out(r))
                self.assertFalse((p / "filed" / "2025.json").exists())
            finally:
                cfg.chmod(0o600)

    def test_a_linked_config_keeps_its_link(self):
        with tempfile.TemporaryDirectory() as d:
            p = shared_year(Path(d))
            cfg = p / "taxjson.toml"
            real = p / "actual.toml"
            cfg.rename(real)
            cfg.symlink_to("actual.toml")
            r = tjs(p, "close-year", "--yes")
            self.assertEqual(r.returncode, 0, out(r))
            self.assertTrue(cfg.is_symlink())
            self.assertNotIn('\ninputs_dir = "../inputs"', real.read_text())
            self.assertIn("frozen by `taxjson close-year`", real.read_text())


class CloseYearRunLock(unittest.TestCase):
    """TJ-0924."""

    def test_a_run_during_close_year_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = tmp / "p"
            project(p)
            tt(p, "margin", BOOK)
            r = tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            gate, release = tmp / "gate", tmp / "release"
            body = ("old = tr._freeze_inputs\n"
                    "def pause(*a, **kw):\n"
                    f"    Path({str(gate)!r}).write_text('ready')\n"
                    "    stop = time.monotonic() + 300\n"
                    f"    while not Path({str(release)!r}).exists():\n"
                    "        if time.monotonic() > stop:\n"
                    "            raise RuntimeError('barrier timeout')\n"
                    "        time.sleep(0.02)\n"
                    "    return old(*a, **kw)\n"
                    "tr._freeze_inputs = pause\n")
            proc = run_driver(driver(tmp, p, body, "close-year", "--yes"))
            try:
                until = time.monotonic() + 300
                while (not gate.exists() and proc.poll() is None
                       and time.monotonic() < until):
                    time.sleep(0.05)
                self.assertTrue(gate.exists(), proc.poll())
                book = p / "inputs" / "margin" / "trades.tt"
                book.write_text(BOOK.replace("15 150", "18 180"))
                r = tjs(p, "run", "--no-input")
                self.assertNotEqual(r.returncode, 0, out(r))
                self.assertIn("in progress", out(r))
            finally:
                release.write_text("go")
                so, se = proc.communicate(timeout=600)
            self.assertEqual(proc.returncode, 0, so + se)
            lock = json.loads((p / "filed" / "2025.json").read_text())
            self.assertAlmostEqual(lock["totals"]["realized"], 50.0, 2)
            self.assertAlmostEqual(lock["return_row"]["realized"], 50.0, 2)


class RunFingerprintRace(unittest.TestCase):
    """TJ-0929."""

    def test_an_edit_during_the_run_leaves_the_books_stale(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = tmp / "p"
            project(p)
            book = p / "inputs" / "margin" / "trades.tt"
            tt(p, "margin", BOOK)
            body = ("from taxjson.lib import checklist as cl\n"
                    "old = cl.record_input_fingerprint\n"
                    "def edit_first(*a, **kw):\n"
                    f"    b = Path({str(book)!r})\n"
                    "    b.write_text(b.read_text().replace('15 150',"
                    " '18 180'))\n"
                    "    return old(*a, **kw)\n"
                    "cl.record_input_fingerprint = edit_first\n")
            proc = run_driver(driver(tmp, p, body, "run", "--no-input"))
            so, se = proc.communicate(timeout=600)
            self.assertEqual(proc.returncode, 0, so + se)
            self.assertIn("changed while this run read them", so + se)
            self.assertIn("inputs/margin/trades.tt", so + se)
            from taxjson.lib.checklist import inputs_changed
            from taxjson.bin.taxjson_run import load_config
            self.assertIn("trades.tt", inputs_changed(p, load_config(p)))
            r = tjs(p, "close-year", "--yes")
            self.assertNotEqual(r.returncode, 0, out(r))
            self.assertFalse((p / "filed" / "2025.json").exists())
            # The next run reads the corrected file and is current.
            r = tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            self.assertNotIn("changed while this run", out(r))
            self.assertEqual(inputs_changed(p, load_config(p)), "")
            r = tjs(p, "form-export", "--json")
            self.assertAlmostEqual(json.loads(r.stdout)["gain_unrounded"],
                                   80.0, places=2)


class RenameWindowSpan(unittest.TestCase):
    """TJ-0941."""

    @staticmethod
    def _decls(days):
        from taxjson.lib.renames import SOURCE_TT, DatedRename
        return [DatedRename("SYN.TO", "NEW.TO", f"2025-06-{d:02d}",
                            where=f"inputs/margin/renames.tt:{n}",
                            line=f"RENAME 2025-06-{d:02d} SYN.TO NEW.TO",
                            source=SOURCE_TT, account="margin",
                            kind="securities")
                for n, d in enumerate(days, 1)]

    def test_a_middle_line_does_not_bridge_the_window(self):
        from taxjson.lib.dated_events import resolve_renames
        for days in ([2, 14], [2, 8, 14], [14, 8, 2]):
            _ev, _notes, problems = resolve_renames(self._decls(days), [])
            self.assertTrue(any("one change has one date" in p
                                for p in problems), (days, problems))

    def test_lines_within_the_window_are_one_event(self):
        from taxjson.lib.dated_events import resolve_renames
        ev, _notes, problems = resolve_renames(self._decls([2, 5, 9]), [])
        self.assertEqual(problems, [])
        self.assertEqual([e.date for e in ev], ["2025-06-02"])

    def test_strict_run_refuses_the_bridged_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            project(p)
            tt(p, "margin",
               "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
               "BUYSELL 2025-07-02 10:00:00 NEW.TO -10 CAD 12 120 0\n"
               + "".join(f"RENAME 2025-06-{x:02d} SYN.TO NEW.TO\n"
                         for x in (2, 8, 14)))
            r = tjs(p, "run", "--strict", "--no-input")
            self.assertNotEqual(r.returncode, 0, out(r))
            self.assertIn("one change has one date", " ".join(
                out(r).split()))


class TobaseGenerations(unittest.TestCase):
    """TJ-0945. Synthetic FIGI and listings."""

    FIGI = "BBG000SYNTH1"

    def _entry(self, **kw):
        e = {"name": "Synthetic security", "kind": "Common Stock",
             "ca": ["SYN.TO"], "us": ["OLD.US", "NEW.US"], "us_otc": [],
             "first_seen": "2025-01-01",
             "added": [{"listing": "NEW.US", "since": "2025-06-02.2"}]}
        e.update(kw)
        return e

    def test_generation_order(self):
        from taxjson.lib.tobase_map import generation_key as k
        self.assertLess(k("2025-06-02"), k("2025-06-02.2"))
        self.assertLess(k("2025-06-02.9"), k("2025-06-02.10"))
        self.assertLess(k("2025-06-02.10"), k("2025-06-03"))

    def test_a_pair_deleted_from_its_own_generation_stays_out(self):
        from taxjson.lib import tobase_map as TB
        e = self._entry()
        old = TB.Master({"generated": "2025-06-02.2"}, {self.FIGI: e}, {})
        new = TB.Master({"generated": "2025-06-02.3"}, {self.FIGI: e}, {})
        text = "".join(ln + "\n" for ln in TB.render(old).splitlines()
                       if not ln.startswith("TOBASE NEW.US "))
        plan = TB.plan_update(new, TB.parse_tobase(text), set(), "")
        self.assertFalse(any(g.a == "NEW.US" or g.b == "NEW.US"
                             for g in plan.added), plan.added)
        self.assertTrue(any("NEW.US" in g.rule for g in plan.removed_now),
                        plan.removed_now)

    def test_a_tenth_generation_entry_is_added(self):
        from taxjson.lib import tobase_map as TB
        old = TB.Master({"generated": "2025-06-02.9"}, {}, {})
        e = self._entry(us=["NEW.US"], added=[],
                        first_seen="2025-06-02.10")
        new = TB.Master({"generated": "2025-06-02.10"}, {self.FIGI: e}, {})
        plan = TB.plan_update(new, TB.parse_tobase(TB.render(old)), set(),
                              "")
        self.assertTrue(any("NEW.US" in g.rule for g in plan.added),
                        plan.added)
        self.assertEqual(plan.removed_now, [])


def mixed(p: Path):
    """A project with a securities account `margin` and a crypto
    account `coins` (each a buy, a ticker change and a sale of the new
    symbol, a gain of 20 each)."""
    project(p, accounts=("margin", "coins"))
    with (p / "taxjson.toml").open("a") as f:
        f.write("crypto = true\n")
    tt(p, "margin",
       "BUYSELL 2025-01-02 10:00:00 SYNA.TO 10 CAD 10 100 0\n"
       "BUYSELL 2025-07-02 10:00:00 NEWA.TO -10 CAD 12 120 0\n")
    tt(p, "coins",
       "BUYSELL 2025-01-02 10:00:00 COIN.KR 10 CAD 10 100 0\n"
       "BUYSELL 2025-07-02 10:00:00 NEWC.KR -10 CAD 12 120 0\n")


class FormatMapSharedTarget(unittest.TestCase):
    """TJ-0938."""

    def test_two_links_to_one_file_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            mixed(p)
            tm = p / "ticker.map"
            tm.write_text("RENAME SYNA.TO NEWA.TO 2025-06-02\n"
                          "RENAME COIN.KR NEWC.KR 2025-06-02\n")
            r = tjs(p, "run", "--strict", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            target = p / "shared-renames.tt"
            target.write_text("")
            for a in ("margin", "coins"):
                (p / "inputs" / a / "renames.tt").symlink_to(target)
            before = tm.read_text()
            r = tjs(p, "format-map", "--write")
            self.assertNotEqual(r.returncode, 0, out(r))
            self.assertIn("are one file", " ".join(out(r).split()))
            self.assertEqual(tm.read_text(), before)
            self.assertEqual(target.read_text(), "")

    def test_separate_files_keep_both_events(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            mixed(p)
            (p / "ticker.map").write_text(
                "RENAME SYNA.TO NEWA.TO 2025-06-02\n"
                "RENAME COIN.KR NEWC.KR 2025-06-02\n")
            r = tjs(p, "run", "--strict", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            r = tjs(p, "format-map", "--write")
            self.assertEqual(r.returncode, 0, out(r))
            self.assertIn("RENAME 2025-06-02 SYNA.TO NEWA.TO",
                          (p / "inputs/margin/renames.tt").read_text())
            self.assertIn("RENAME 2025-06-02 COIN.KR NEWC.KR",
                          (p / "inputs/coins/renames.tt").read_text())


class FormatMapUnbuiltCrypto(unittest.TestCase):
    """TJ-0940."""

    def test_an_unbuilt_account_with_inputs_needs_a_run_first(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p"
            mixed(p)
            tt(p, "margin",
               "BUYSELL 2025-01-02 10:00:00 SYNA.TO 10 CAD 10 100 0\n"
               "BUYSELL 2025-07-02 10:00:00 SYNA.TO -10 CAD 12 120 0\n")
            tm = p / "ticker.map"
            tm.write_text("RENAME COIN.KR NEWC.KR 2025-06-02\n")
            r = tjs(p, "format-map", "--write")
            self.assertNotEqual(r.returncode, 0, out(r))
            text = " ".join(out(r).split())
            self.assertIn("account coins has no books yet", text)
            self.assertIn("run `taxjson run`", text)
            self.assertEqual(tm.read_text(),
                             "RENAME COIN.KR NEWC.KR 2025-06-02\n")
            self.assertFalse((p / "inputs/margin/renames.tt").exists())
            # After a run the crypto account's books name the symbols:
            # the line goes there, and the books are the same.
            r = tjs(p, "run", "--strict", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            r = tjs(p, "format-map", "--write")
            self.assertEqual(r.returncode, 0, out(r))
            self.assertIn("RENAME 2025-06-02 COIN.KR NEWC.KR",
                          (p / "inputs/coins/renames.tt").read_text())
            r = tjs(p, "run", "--strict", "--no-input")
            self.assertEqual(r.returncode, 0, out(r))
            r = tjs(p, "form-export", "--json")
            self.assertAlmostEqual(json.loads(r.stdout)["gain_unrounded"],
                                   40.0, places=2)


if __name__ == "__main__":
    unittest.main()
