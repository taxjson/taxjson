"""Backlog fixes, group redact: `taxjson redact` and the ticker.map /
tobase.map messages and `format-map`. One test class per backlog item,
each named for its id. Synthetic data only."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_redact as R
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent

QT_ACCT = "55500001"            # pii-ok (synthetic)
LEAK_ACCT = "55512345"          # pii-ok (synthetic)
QT_CSV = (
    "Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
    "Price,Gross Amount,Commission,Net Amount,Currency,Activity Type,"
    "Account #,Account Type\n"
    "2024-02-01,2024-02-05,Buy,MSFT,MICROSOFT CORP,5,400.00,-2000.00,-4.95,"
    "-2004.95,USD,Trades,55500001,Margin\n"                      # pii-ok
    "2024-04-01,2024-04-03,Sell,MSFT,MICROSOFT CORP,-5,420.00,2100.00,-4.95,"
    "2095.05,USD,Trades,55500001,Margin\n")                      # pii-ok
# A deposit row planted in a redacted copy: an e-mail address, a SIN
# and an account number the copy must not carry.
LEAK_ROW = (
    "2024-03-16,2024-03-16,Deposits,,FROM SAM SAMPLE sam.sample@example.org "
    "SIN 046 454 286,0,0,500.00,0,500.00,CAD,Deposits,55512345,"  # pii-ok
    "Margin\n")


def _env(**extra):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env.update({k: str(v) for k, v in extra.items()})
    return env


def tjs(*args, cwd=REPO_ROOT, **env):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(args)),
        cwd=cwd, capture_output=True, text=True, env=_env(**env),
        stdin=subprocess.DEVNULL, timeout=600)


def flat(r) -> str:
    return " ".join((r.stdout + "\n" + r.stderr).split())


class TestTJ0097CheckReadsARedactedCopy(unittest.TestCase):
    """`redact --check` of a copy it wrote scans it (a leak planted in
    the copy is found, exit 1) and takes the copy's own placeholders as
    redacted already (a clean copy passes, exit 0); writing still skips
    a NAME.redacted.EXT input."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _copy(self) -> Path:
        src = self.tmp / "q.csv"
        src.write_text(QT_CSV)
        r = tjs("redact", "--no-denylist", "--out", str(self.tmp / "out"),
                str(src))
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.tmp / "out" / "q.redacted.csv"

    def test_a_clean_copy_passes_the_check(self):
        dst = self._copy()
        self.assertIn("99900001", dst.read_text())
        r = tjs("redact", "--no-denylist", "--check", str(dst))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("already a redacted copy", r.stderr)
        self.assertIn("q.redacted.csv (check only)", r.stdout)
        self.assertIn("account ids: none found", r.stdout)
        self.assertNotIn("NOT replaced", r.stdout)

    def test_a_leak_in_the_copy_is_found(self):
        dst = self._copy()
        leak = self.tmp / "leak.redacted.csv"
        leak.write_text(dst.read_text() + LEAK_ROW)
        r = tjs("redact", "--no-denylist", "--check", "--details",
                str(leak))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        out = r.stdout
        self.assertIn("leak.redacted.csv (check only)", out)
        self.assertIn("account ids: 1 distinct", out)
        self.assertIn("e-mail addresses: 1", out)
        self.assertIn("SIN/SSN-shaped numbers: 1", out)
        self.assertNotIn(LEAK_ACCT, out + r.stderr)
        # Writing never makes NAME.redacted.redacted.EXT.
        r = tjs("redact", "--no-denylist", str(leak))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already a redacted copy — skipped", r.stderr)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()),
                         ["leak.redacted.csv", "out", "q.csv"])

    def test_an_original_still_counts_a_placeholder_shaped_id(self):
        # Only a copy's placeholders are taken as redacted: in an
        # export a 9990-shaped id is still an id.
        src = self.tmp / "orig.csv"
        src.write_text(QT_CSV.replace(QT_ACCT, "99900007"))
        r = tjs("redact", "--no-denylist", "--check", str(src))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("account ids: 1 distinct", r.stdout)

    def test_project_check_of_its_own_copy(self):
        root = self.tmp / "proj"
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "qt" / f"{QT_ACCT}.csv").write_text(QT_CSV)
        r = tjs("-C", str(root), "redact", "--no-denylist", cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        # The copy as a project of its own: its inputs/ is the copy.
        again = self.tmp / "again"
        again.mkdir()
        (root / "inputs_redact").rename(again / "inputs")
        self.assertTrue((again / "inputs" / R.TREE_MARKER).is_file())
        r = tjs("-C", str(again), "redact", "--no-denylist", "--check",
                "--details", cwd=again)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("not replaced everywhere", r.stderr)
        self.assertIn("Done. Nothing to redact.", r.stdout)
        # Each file of the copy, checked on its own (the marker of the
        # tree it sits in), passes too.
        (f,) = (again / "inputs" / "qt").iterdir()
        r = tjs("redact", "--no-denylist", "--check", str(f))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # A row planted in the copy is found.
        f.write_text(f.read_text() + LEAK_ROW)
        r = tjs("-C", str(again), "redact", "--no-denylist", "--check",
                cwd=again)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("1 account id", r.stdout)
        self.assertIn("1 e-mail address", r.stdout)

    def test_stand_ins_unit(self):
        self.assertTrue(R._is_placeholder_id("U9990001"))
        self.assertTrue(R._is_placeholder_id("99900001"))
        self.assertFalse(R._is_placeholder_id(QT_ACCT))
        self.assertTrue(R._is_pseudonym(R._pseudonym("ABCDEF-12345-GHIJKL", 1)))
        self.assertTrue(R._is_pseudonym(R._pseudonym("0x" + "ab" * 20, 3)))
        self.assertFalse(R._is_pseudonym("ABCDEF-12345-GHIJKL"))
        # The stand-in e-mail address is not a finding.
        self.assertEqual(R._emails_found(
            "to redacted@example.com and sam.sample@example.org"), 1)


class TestTJ0098SettingsCommentsReviewed(unittest.TestCase):
    """A name in a comment of a year's taxjson.toml or ticker.map is
    listed for review (`Read line N of the copy`), as an export's free
    text is; the comment lines taxjson writes itself are not."""

    def test_a_name_in_a_comment_is_listed(self):
        toml = ('[accounts.ib]\ntype = "taxable"\n'
                'account = "U1234567"  # Jane Sample main, '  # pii-ok
                'jane.sample@example.org\n')
        text, rep = R.redact_project_text(toml, [], {},
                                          (["U1234567"], []))  # pii-ok
        self.assertNotIn("U1234567", text)                     # pii-ok
        self.assertEqual(rep.emails, 1)
        self.assertEqual([n for n, _w in rep.review], [3])
        self.assertIn("in a comment", rep.review[0][1])
        text, rep = R.redact_project_text(
            "GLOBAL ZZQ.US ZZQA.US\n# Jane Sample personal map\n", [], {})
        self.assertEqual([n for n, _w in rep.review], [2])

    def test_taxjsons_own_comments_are_not_listed(self):
        from taxjson.lib import ticker_map_format, tobase_map
        _text, rep = R.redact_project_text(
            ticker_map_format.init_template(), [], {})
        self.assertEqual(rep.review, [])
        master = tobase_map.load_master()
        for shared in (False, True):
            _text, rep = R.redact_project_text(
                "\n".join(tobase_map.header(master, shared)) + "\n", [], {})
            self.assertEqual(rep.review, [], shared)

    def test_year_folder_copy_lists_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp) / "top"
            r = tjs("init", "--country", "canada", "--year", "2024",
                    str(top))
            self.assertEqual(r.returncode, 0, r.stderr)
            y = top / "2024"
            with open(y / "ticker.map", "a") as fh:
                fh.write("# Jane Sample personal map\n")
            n = len((y / "ticker.map").read_text().splitlines())
            r = tjs("-C", str(y), "redact", "--no-denylist", "--check",
                    "--details", cwd=y)
            self.assertIn(f"ticker.map: nothing to redact Read line "
                          f"{n} of the copy", flat(r))
            # The init templates' own comments: nothing to read.
            self.assertIn("tobase.map: nothing to redact", flat(r))
            self.assertIn("taxjson.toml: nothing to redact", flat(r))
            self.assertNotIn("tobase.map: nothing to redact Read", flat(r))
            self.assertNotIn("taxjson.toml: nothing to redact Read", flat(r))


class TestTJ0100TJ0110TheCopysPath(unittest.TestCase):
    """The closing lines name the copy where the shell finds it: `--out
    DIR` (a relative DIR is the shell's folder's) and `-C DIR` give its
    full path (`Review inputs/` named the project's own exports), the
    year is named in `.` too, and the run hint works from the shell."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.top = cls.tmp / "demo"
        r = tjs("init", "--country", "canada", "--year", "2024",
                str(cls.top))
        assert r.returncode == 0, r.stderr
        (cls.top / "inputs" / "margin" / f"{QT_ACCT}.csv").write_text(QT_CSV)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_in_the_year_folder(self):
        y = self.top / "2024"
        r = tjs("redact", "--no-denylist", "--force", cwd=y)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("==> Done. Review inputs_redact/ before sharing it. "
                      "It is a single-folder project of 2024: ", r.stdout)
        self.assertIn("`taxjson -C inputs_redact/ run` runs it.", r.stdout)

    def test_dash_C_from_the_parent(self):
        r = tjs("-C", "2024", "redact", "--no-denylist", "--force",
                cwd=self.top)
        self.assertEqual(r.returncode, 0, r.stderr)
        dst = os.path.abspath(self.top / "2024" / "inputs_redact")
        self.assertIn(f"==> Done. Review {dst}/ before sharing it.",
                      r.stdout)
        self.assertIn(f"`taxjson -C {dst}/ run` runs it.", r.stdout)
        self.assertTrue(Path(dst, "taxjson.toml").is_file())

    def test_out_relative_to_the_shell(self):
        other = self.tmp / "othercwd"
        other.mkdir(exist_ok=True)
        r = tjs("-C", str(self.top / "2024"), "redact", "--no-denylist",
                "--force", "--out", "inputs", cwd=other)
        self.assertEqual(r.returncode, 0, r.stderr)
        dst = os.path.abspath(other / "inputs")
        self.assertTrue(Path(dst, R.TREE_MARKER).is_file())
        self.assertIn(f"==> Copying inputs/ to {dst}/", r.stdout)
        self.assertIn(f"==> Done. Review {dst}/ before sharing it.",
                      r.stdout)
        self.assertNotIn("Review inputs/", r.stdout)


def _group_of(text: str, line: str) -> str:
    """The `## --- <Group> ---` heading `line` sits under."""
    group = ""
    for ln in text.splitlines():
        if ln.startswith("## --- ") and ln.endswith(" ---"):
            group = ln[len("## --- "):-len(" ---")]
        elif ln == line:
            return group
    raise AssertionError(f"{line!r} not in the map")


class TestTJ0047InitExamplesStayInTheirGroup(unittest.TestCase):
    """A line appended at the end of the ticker.map `taxjson init`
    writes goes to its own group; the template's Lookups examples above
    it stay under Lookups (they moved with it as its comment)."""

    def test_appended_line(self):
        from taxjson.lib import ticker_map_format as F
        init = F.init_template()
        r = F.format_map(init + "TOBASE ZZQ.US ZZQ.TO\n")
        self.assertEqual(_group_of(r.text, "TOBASE ZZQ.US ZZQ.TO"),
                         F.LISTINGS)
        for ex in F._INIT_EXAMPLES:
            self.assertEqual(_group_of(r.text, ex), _group_of(init, ex), ex)
        # Only the new line (and the blank line after it) is added.
        added = [ln for ln in r.text.splitlines()
                 if ln not in init.splitlines()]
        self.assertEqual(added, ["TOBASE ZZQ.US ZZQ.TO"])
        self.assertEqual(F.format_map(r.text).text, r.text)

    def test_a_note_of_yours_still_moves_with_the_line(self):
        from taxjson.lib import ticker_map_format as F
        r = F.format_map(F.init_template() + "# one company, two listings\n"
                         "TOBASE ZZQ.US ZZQ.TO\n")
        lines = r.text.splitlines()
        i = lines.index("TOBASE ZZQ.US ZZQ.TO")
        self.assertEqual(lines[i - 1], "# one company, two listings")
        self.assertEqual(_group_of(r.text, "# MULT ZZQ1 50"), F.LOOKUPS)
        # A switched-off rule of your own directly above still moves
        # with it, like a comment.
        r = F.format_map(F.init_template() + "# QUOTE ZZQ.TO ZZQ.V\n"
                         "TOBASE ZZQ.US ZZQ.TO\n")
        lines = r.text.splitlines()
        i = lines.index("TOBASE ZZQ.US ZZQ.TO")
        self.assertEqual(lines[i - 1], "# QUOTE ZZQ.TO ZZQ.V")


def _small_project(tmp: Path) -> Path:
    """A single-folder Canadian project with one Questrade export."""
    root = tmp / "p"
    r = tjs("init", "--single", "--country", "canada", "--year", "2024",
            str(root))
    assert r.returncode == 0, r.stderr
    (root / "inputs" / "margin" / f"{QT_ACCT}.csv").write_text(QT_CSV)
    return root


class TestTJ0126TJ0112AMapLineWarnedOnce(unittest.TestCase):
    """A map line's message is shown once per run (and once per
    checklist), not by every pass that reads the map; each stage's
    captured .diag still has it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _small_project(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_self_rename_is_warned_once_under_the_project_checks(self):
        with open(self.root / "ticker.map", "a") as fh:
            fh.write("GLOBAL MSFT.US MSFT.US\n")
        r = tjs("-C", str(self.root), "run", TAXJSON_WIDTH=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        both = r.stdout + r.stderr
        self.assertEqual(both.count("renames a symbol to itself"), 1, both)
        self.assertNotIn("more like", both)
        # Said under `==> Checking the project`, before any account.
        lines = r.stderr.splitlines()
        self.assertIn("renames a symbol to itself", lines[0])
        diags = [p for p in (self.root / "work").glob("*.diag")
                 if "to itself" in p.read_text()]
        self.assertTrue(diags)

    def test_checklist_says_each_map_problem_once(self):
        # (its checks read the map once there are books)
        r = tjs("-C", str(self.root), "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.root / "ticker.map", "a") as fh:
            fh.write("TOBASE ZZQ\nFOOBAR ZZQ.US ZZQ.TO\n")
        r = tjs("-C", str(self.root), "checklist", "--run",
                TAXJSON_WIDTH=120)
        both = r.stdout + r.stderr
        # The format step names them (its count and the first line): the
        # checks that read the map no longer repeat the loader's lines
        # (backlog TJ-0112, finished with TJ-0250).
        self.assertEqual(both.count("ticker.map problem: ticker.map:"), 0,
                         both)
        self.assertNotIn("more like", both)
        self.assertIn("2 ticker.map problem(s)", both)
        self.assertIn("refuses — ticker.map:", both)


class TestTJ0125TobaseMapProblemsNamed(unittest.TestCase):
    """A malformed tobase.map line is headed `tobase.map problem(s)`
    (it said `ticker.map problem(s)`), both files when both have one,
    and the fix no longer gives every keyword `KEYWORD FROM TO`."""

    def test_headline_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp) / "top"
            r = tjs("init", "--country", "canada", "--year", "2024",
                    str(top))
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(top / "tobase.map", "a") as fh:
                fh.write("TOBASE ZZQ.US\nFOO BAR BAZ\n")
            r = tjs("-C", str(top / "2024"), "run")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("error: 2 tobase.map problem(s)", r.stderr)
            self.assertNotIn("ticker.map problem(s)", r.stderr)
            self.assertNotIn("KEYWORD FROM TO", r.stderr)
            with open(top / "2024" / "ticker.map", "a") as fh:
                fh.write("FOOBAR ZZQ.US ZZQ.TO\n")
            r = tjs("-C", str(top / "2024"), "run")
            self.assertIn("error: 3 ticker.map and tobase.map problem(s)",
                          r.stderr)


class TestTJ0127AnUnreadableMapRefusedOnce(unittest.TestCase):
    """A ticker.map that is a folder: the refusal is printed once, under
    `==> Checking the project` (the help page's country lookup printed
    it first, then the run again)."""

    def test_refused_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _small_project(Path(tmp))
            (root / "ticker.map").unlink()
            (root / "ticker.map").mkdir()
            for cmd in ("run", "fees"):
                r = tjs("-C", str(root), cmd, TAXJSON_WIDTH=120)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                both = r.stdout + r.stderr
                self.assertEqual(both.count("ticker.map is a directory"), 1,
                                 both)
            # No step line after the error: the step, then the error.
            r = tjs("-C", str(root), "run", TAXJSON_WIDTH=120)
            self.assertEqual(r.stdout.strip(), "==> Checking the project")


if __name__ == "__main__":
    unittest.main()
