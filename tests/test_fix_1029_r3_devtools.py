"""The 2026-10-29 fix round 3, developer tooling and docs: tests that
match a message naming a temp path, the error a wrapper relays of a
failed child, and the docs and tools they pin (backlog TJ-0625).
Synthetic data only, each test in a temp folder of its own."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _qa_project import Text, flat
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv

# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite);
# a test that reads the person's form unsets it for its child.
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


REPO = Path(__file__).resolve().parents[1]


def _cli(root, *args, width=None):
    """`taxjson -C ROOT ARGS` in a child: captured output (width 0), or
    a person piping it (`width=""`: no TAXJSON_WIDTH, width 120)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
               PYTHONPATH=str(REPO / "src"))
    env.pop("TAXJSON_WIDTH", None)
    if width != "":
        env["TAXJSON_WIDTH"] = "0" if width is None else str(width)
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, cwd=str(root),
        stdin=subprocess.DEVNULL, timeout=600)


# ----------------------------------------------------------- TJ-0625
class TestPhraseAcrossALineBreak(unittest.TestCase):
    """tests/_qa_project.py tj() runs taxjson at width 120, so a message
    naming a temp path breaks where that path's length puts the break:
    CI's short /tmp paths split "outside the project", a longer TMPDIR
    "single-folder project" (test_workflow_audit_fixes). tj()'s output
    is a Text: a phrase is found across the break."""

    TEXT = ("Error: /tmp/x/tmpabcdefgh/single is a single-folder\n"
            "project, not a year folder\n\n  Warning: one line\n")

    def test_a_phrase_across_a_break_is_in_the_text(self):
        t = Text(self.TEXT)
        self.assertIn("single-folder project", t)
        self.assertIn("a single-folder project, not a year folder", t)
        self.assertNotIn("single-folder project", self.TEXT)
        self.assertNotIn("a year-folder project", t)
        self.assertEqual(flat(self.TEXT).count("\n"), 0)

    def test_a_word_or_a_layout_needle_matches_as_written(self):
        t = Text(self.TEXT)
        # Not phrases: a word, a line break, padding at either end.
        self.assertNotIn("\n\n\n", t)
        self.assertIn("\n\n", t)
        self.assertNotIn("folder project ", t)
        self.assertNotIn(" folder project", t)
        self.assertNotIn("folderproject", t)
        self.assertIn("  Warning", t)
        self.assertNotIn("one line\nproject", t)

    def test_joined_output_stays_a_text(self):
        from _qa_project import console
        r = subprocess.CompletedProcess([], 0, Text("a\nb\n"), Text("c d\ne"))
        both = console(r)
        self.assertIsInstance(both, Text)
        self.assertIn("b c", both)
        self.assertIn("d e", "x " + r.stderr)
        self.assertIsInstance("x " + r.stderr, Text)

    def test_tj_returns_texts(self):
        from _qa_project import tj
        with tempfile.TemporaryDirectory() as td:
            r = tj(Path(td) / "none", "--version", check=False)
        self.assertIsInstance(r.stdout, Text)
        self.assertIsInstance(r.stderr, Text)


class TestChildErrorKeepsTheErrorLine(unittest.TestCase):
    """`tjs instalments` and `tjs amt` relay the estimate's error when it
    fails. Over 400 characters, the relay kept the child's LAST lines:
    a long project path pushed the line that names the unreadable file
    out, leaving only the hint (test_fix_filing_b's truncated-gains case
    under a long TMPDIR)."""

    LONG = "/srv/taxes/" + "a-long-folder-name/" * 16 + "p"

    def test_the_error_line_and_its_hint_are_kept(self):
        from taxjson.bin.taxjson_run import _child_error
        for label in ("taxjson estimate: error: ", "Error: "):
            err = (f"Info: crypto account(s) excluded: cb\n"
                   f"{label}could not read work/margin_gains_wash.json: "
                   f"{self.LONG}/work/margin_gains_wash.json: not valid "
                   f"JSON (Unterminated string)\n  A taxable account's "
                   f"gains cannot be left out of the totals. Re-run "
                   f"`taxjson run` to rebuild it.\n")
            self.assertGreater(len(err), 400)
            msg = _child_error(err)
            self.assertIn("could not read work/margin_gains_wash.json", msg)
            self.assertIn("Re-run `taxjson run`", msg)
            self.assertNotIn("crypto account(s) excluded", msg)

    def test_a_long_message_without_an_error_line_keeps_the_last(self):
        from taxjson.bin.taxjson_run import _child_error
        msg = _child_error("\n".join(f"line {i} " + "x" * 60
                                     for i in range(20)))
        self.assertIn("line 19", msg)
        self.assertNotIn("line 0 ", msg)

    def test_instalments_and_amt_name_the_unreadable_file(self):
        from test_fix_filing_b import _tt_project
        with tempfile.TemporaryDirectory() as td:
            # A project path of about 300 characters, whatever TMPDIR is.
            root = Path(td) / ("a-long-project-folder-" * 10) / "p"
            root.mkdir(parents=True)
            _tt_project(root, instalments=True)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            p = root / "work" / "margin_gains_wash.json"
            p.write_bytes(p.read_bytes()[:len(p.read_bytes()) // 2])
            for cmd in ("instalments", "amt"):
                for width in (None, ""):
                    with self.subTest(cmd=cmd, width=width):
                        r = _cli(root, cmd, width=width)
                        self.assertNotEqual(r.returncode, 0)
                        self.assertIn("margin_gains_wash.json", r.stderr)
                        self.assertIn("not valid JSON", flat(r.stderr))
                        self.assertIn("Re-run `taxjson run`",
                                      flat(r.stderr))


# ----------------------------------------------------------- TJ-0222
class TestPendingElectionDocument(unittest.TestCase):
    """`tjs elect --pending --json`: an event's `summary` is its identity
    (saved manifests match on it), so a spin-off's ratio there counts
    the shares delivered; the cash-in-lieu fields carry the fraction
    paid in cash (decided 2026-10-29: documented in docs/commands.md,
    not changed)."""

    FIELDS = ("summary", "qty_received", "ratio_new", "ratio_old",
              "fraction_in_lieu", "cash_in_lieu", "cash_in_lieu_currency",
              "cash_in_lieu_date")

    def test_the_fields_the_doc_names(self):
        import json
        from test_fix_backlog_fxcorp import _qt_project
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _qt_project(root, "2024-06-14", "2024-06-18")
            r = _cli(root, "elect", "--pending", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        ev = json.loads(r.stdout)["accounts"]["margin"]["pending"][0]
        self.assertIn("(10.0-for-105.0,", ev["summary"])
        self.assertEqual((ev["qty_received"], ev["ratio_new"],
                          ev["ratio_old"]), (10.0, 10.0, 105.0))
        self.assertEqual(ev["qty_received"] + ev["fraction_in_lieu"], 10.5)
        self.assertEqual((ev["cash_in_lieu"], ev["cash_in_lieu_currency"],
                          ev["cash_in_lieu_date"]),
                         (12.0, "CAD", "2024-06-18"))
        doc = (REPO / "docs" / "commands.md").read_text(encoding="utf-8")
        elect = doc[doc.index("#### taxjson elect"):]
        elect = elect[:elect.index("\n#### ", 1)]
        for f in self.FIELDS:
            self.assertIn(f"`{f}`", elect, f)
        self.assertIn("10.0-for-105.0", elect)


# ----------------------------------------------------------- TJ-0606
class TestPlantedRatesSurviveARebuild(unittest.TestCase):
    """test_fix_a2_checklist's crypto-sends case failed once in a busy
    parallel batch ("run --no-input" exit 1). The crypto-sends tests
    plant work/to_base.csv; `taxjson run` rebuilds that file when a
    taxjson source file is newer than it (an edit made while the suite
    runs), from the HOME's currency cache, which the tests' own HOME did
    not have: the rebuild kept no rate and the conversion stopped. The
    HOME now holds the same rates (test_fix_sends._home_rates)."""

    def test_a_stale_planted_file_is_rebuilt_with_the_same_rates(self):
        from test_fix_sends import _cli, _project
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td)
            rates = root / "work" / "to_base.csv"
            # Older than every taxjson source file: what an edit to one
            # after the file was planted makes it.
            os.utime(rates, (1_000_000_000, 1_000_000_000))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("no exchange rate", r.stderr)
            rows = {ln.split()[0]: float(ln.split()[4])
                    for ln in rates.read_text().splitlines() if ln.strip()}
            self.assertGreater(rates.stat().st_mtime, 1_000_000_000)
        self.assertEqual(rows["2026-04-13"], 1.3611)
        self.assertEqual(rows["2025-03-01"], 1.45)
        self.assertEqual(rows["2025-06-02"], 1.35)


# ----------------------------------------------------------- TJ-0595
class TestStyleFixturesAreFormatted(unittest.TestCase):
    """The style projects (tests/fixtures/style) are a current project:
    `tjs format --check` passes on their taxjson.toml. They carried an
    older template's reference blocks (no custodian_country, no
    requires_taxjson) and two Canadian descriptions in an older wording,
    which `format` kept as the user's notes."""

    OLD_CANADA = (
        "## Contracts written before this year keep close timing (default: "
        "`year`). SET ONCE to the first\n"
        "## year you FILE under grant timing and keep it UNCHANGED in every "
        "later year's project (do not bump\n"
        "## it with `year`).\n"
        "# option_grant_timing_since       = 2024\n"
        "## Canadian issuers whose distributions are a corporation's, dated "
        "when paid (README \"Income\n"
        "## dating\"). No default.\n"
        "# corporate_distributions         = [\"XYZQ.TO\"]\n")

    def test_the_fixtures_need_no_formatting(self):
        from taxjson.lib import config_template as CT
        for country in ("canada", "usa"):
            p = REPO / "tests" / "fixtures" / "style" / country / "taxjson.toml"
            with self.subTest(country=country):
                r = CT.format_config(p.read_text(encoding="utf-8"))
                self.assertFalse(
                    r.changed, f"{p.relative_to(REPO)} is not formatted: "
                    f"`tjs -C {p.parent.relative_to(REPO)} format --write` "
                    f"(the template changed)")

    def test_the_older_wording_is_the_templates(self):
        from taxjson.lib import config_template as CT
        text = ('[settings]\ncountry = "canada"\nyear = 2024\n'
                + self.OLD_CANADA)
        r = CT.format_config(text)
        self.assertEqual((r.kept_comments, r.notes_lines), (0, 0), r.text)
        self.assertNotIn("SET ONCE", r.text)
        self.assertNotIn('README "Income', r.text)


# ----------------------------------------------------------- TJ-0617
_NO_PANDAS = r'''
import sys
class _NoPandas:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name == "pandas" or name.startswith("pandas."):
            raise ModuleNotFoundError("No module named 'pandas' (hidden)",
                                      name=name)
        return None
sys.meta_path.insert(0, _NoPandas)
sys.argv = ["taxjson-xlsx-to-csv", sys.argv[1]]
from taxjson.bin import xlsx_to_csv
xlsx_to_csv.main()
'''


class TestXlsxExtraIsOpenpyxl(unittest.TestCase):
    """The `xlsx` extra installed pandas, which taxjson-xlsx-to-csv no
    longer imports (it reads the sheet with openpyxl since TJ-0534)."""

    def test_the_extra_lists_openpyxl_only(self):
        try:
            import tomllib
        except ImportError:                      # Python < 3.11
            import tomli as tomllib
        py = tomllib.loads((REPO / "pyproject.toml").read_text())
        xlsx = py["project"]["optional-dependencies"]["xlsx"]
        self.assertEqual([r.split(">")[0] for r in xlsx], ["openpyxl"])
        lock = (REPO / "requirements" / "xlsx.txt").read_text()
        self.assertNotIn("\npandas==", lock)
        self.assertIn("\nopenpyxl==", lock)

    def test_the_converter_runs_without_pandas(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl not installed (the xlsx extra)")
        with tempfile.TemporaryDirectory() as td:
            x = Path(td) / "in.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["Symbol", "Quantity", "Amount"])
            ws.append(["SYN.TO", 10, "1,234.50"])
            wb.save(x)
            env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
            # (cwd: this test's own folder, which holds only in.xlsx)
            r = subprocess.run([sys.executable, "-c", _NO_PANDAS, str(x)],
                               capture_output=True, text=True, env=env,
                               cwd=td, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(),
                         ["Symbol,Quantity,Amount", "SYN.TO,10,1234.50"])


# ----------------------------------------------------------- TJ-0594
_SEES = r'''
import json
from importlib import metadata
eps = [e.name for e in metadata.entry_points(group="taxjson.fetchers")]
try:
    metadata.distribution("taxjson-fetch")
    dist = True
except metadata.PackageNotFoundError:
    dist = False
try:
    import taxjson_fetch
    mod = getattr(taxjson_fetch, "STAND_IN", "installed")
except ModuleNotFoundError:
    mod = None
print(json.dumps({"eps": eps, "dist": dist, "mod": mod}))
'''


def _hide_optional():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hide_optional", REPO / "scripts" / "hide_optional.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestTheGateSuiteHidesThePlugin(unittest.TestCase):
    """Every GitHub job runs the suite without taxjson-fetch (it is
    installed after the suite), while the dev venv has it, and the
    egg-info an editable build leaves in packages/taxjson-fetch/src made
    it a distribution to any run with that folder on PYTHONPATH: the
    gate's suite saw a plugin CI never has. scripts/ci.sh's suite stages
    now run with scripts/hide_optional.py's sitecustomize, as the
    no-extras stage does (the plugin only); the fetch-plugin stage still
    sees it."""

    def test_the_suite_stages_run_with_the_plugin_hidden(self):
        ci = (REPO / "scripts" / "ci.sh").read_text()
        self.assertIn('scripts/hide_optional.py "$CI_TMP/hide" --plugin '
                      'taxjson_fetch', ci)
        self.assertIn('NO_PLUGIN=(PYTHONPATH="$CI_TMP/hide', ci)
        lines = {ln.split()[1]: ln for ln in ci.splitlines()
                 if ln.strip().startswith("stage ")}
        for name in ("suite", "release-edit-tests"):
            self.assertIn('"${NO_PLUGIN[@]}"', lines[name], name)
        self.assertNotIn("NO_PLUGIN", lines["fetch-plugin"])
        sh = (REPO / "scripts" / "ci_no_extras.sh").read_text()
        self.assertIn('scripts/hide_optional.py "$ROOT/hide" --extras '
                      '"$EXTRAS"', sh)

    def _sees(self, td, *path):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, path)))
        r = subprocess.run([sys.executable, "-c", _SEES], capture_output=True,
                           text=True, env=env, cwd=td,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        return json.loads(r.stdout)

    def test_an_in_tree_egg_info_is_no_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            # What an editable build leaves beside the plugin's source:
            # its metadata, a fetcher entry point, and the module.
            src = Path(td) / "src"
            egg = src / "taxjson_fetch.egg-info"
            egg.mkdir(parents=True)
            (egg / "PKG-INFO").write_text(
                "Metadata-Version: 2.1\nName: taxjson-fetch\nVersion: 0.0.1\n")
            (egg / "entry_points.txt").write_text(
                "[taxjson.fetchers]\ntaxjson-fetch = taxjson_fetch:FETCHER\n")
            (src / "taxjson_fetch").mkdir()
            (src / "taxjson_fetch" / "__init__.py").write_text("STAND_IN = 1\n")
            hide = Path(td) / "hide"
            _hide_optional().main([str(hide), "--plugin", "taxjson_fetch"])
            # Without the sitecustomize the egg-info is a plugin.
            seen = self._sees(td, src)
            self.assertIn("taxjson-fetch", seen["eps"])
            self.assertTrue(seen["dist"])
            # With it: no distribution, no entry point; the module only
            # from a PYTHONPATH entry (as a test that names it gets it).
            seen = self._sees(td, hide, src)
            self.assertNotIn("taxjson-fetch", seen["eps"])
            self.assertFalse(seen["dist"])
            self.assertEqual(seen["mod"], 1)
            # The venv's own install (no PYTHONPATH entry): hidden too.
            seen = self._sees(td, hide)
            self.assertIsNone(seen["mod"])
            self.assertNotIn("taxjson-fetch", seen["eps"])

    def test_the_extras_stay_when_only_the_plugin_is_hidden(self):
        text = _hide_optional().render("", "taxjson_fetch")
        self.assertIn("_HIDDEN = ''.split()", text)
        self.assertIn("_PLUGIN = 'taxjson_fetch'", text)
        text = _hide_optional().render("yfinance  pandas", "")
        self.assertIn("_HIDDEN = 'yfinance pandas'.split()", text)
        self.assertIn("if _PLUGIN:", text)
        import contextlib
        import io
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            with tempfile.TemporaryDirectory() as td:
                _hide_optional().main([td])     # nothing to hide
        self.assertIn("nothing to hide", err.getvalue())


if __name__ == "__main__":
    unittest.main()
