"""The 2026-10-29 fix round 4, developer tooling and tests: planted
exchange rates that survive a rebuild (backlog TJ-0635). Synthetic
data only, each test in a temp folder of its own."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import _planted_rates as PR
from _style import CapturedWidth

# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


REPO = Path(__file__).resolve().parents[1]

# Older than every taxjson source file: what an edit to one after the
# rates were planted makes them (the run then rebuilds work/to_base.csv
# from the HOME's currency cache).
AGED = (1_000_000_000, 1_000_000_000)


def _cli(root, *args, home=None):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
               PYTHONPATH=str(REPO / "src"))
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=str(REPO), capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _rates(path):
    return {ln.split()[0]: float(ln.split()[4])
            for ln in Path(path).read_text().splitlines() if ln.strip()}


def _usd_project(root: Path) -> None:
    """A Canadian project: 10 QZRA.US bought at 100 USD on 2026-03-02;
    rates USD->CAD 1.2500 (made up; the suite's synthetic HOME has
    1.35) from 2026-01-01 to today."""
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "inputs" / "m" / "a.tt").write_text(
        "BUYSELL 2026-03-02 10:00:00 QZRA.US 10 USD 100 -1000 0\n")
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        'option_grant_timing_since = 2026\n\n[accounts.m]\n'
        'type = "taxable"\n')


def _usd_rates_text() -> str:
    d, lines = date(2026, 1, 1), []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.2500 boc")
        d += timedelta(days=1)
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------- TJ-0635
class TestMergeIntoTheCache(unittest.TestCase):
    """_planted_rates writes a planted row where taxjson-to-base-curr
    reads it: a CAD target the Bank of Canada publishes in its series
    (from its first date), every other one as a Yahoo rate."""

    def test_sources_and_coverage(self):
        text = ("2025-01-02 12:00:00 USD CAD 1.2500 boc\n"
                "2025-01-03 12:00:00 USD CAD 1.2600 yahoo\n"
                "2016-06-01 12:00:00 USD CAD 1.3000 yahoo\n"
                "2025-01-02 12:00:00 CAD USD 0.8000 yahoo\n"
                "2025-01-05 12:00:00 CAD USD 0.8100 yahoo 2025-01-03\n"
                "not a row\n")
        cache = PR.merge_into({"_boc": {"USDCAD": {"obs": {
            "2025-01-02": "1.3500", "2025-01-06": "1.3500"}}},
            "_coverage": {"boc:USDCAD": [["2025-01-06", "2025-01-06"]]}},
            PR.rows(text))
        self.assertEqual(cache["_boc"]["USDCAD"]["obs"], {
            "2025-01-02": "1.2500", "2025-01-03": "1.2600",
            "2025-01-06": "1.3500"})
        self.assertEqual(cache["USDCAD-2016-06-01"], 1.3)
        self.assertEqual((cache["CADUSD-2025-01-02"],
                          cache["CADUSD-2025-01-05"]), (0.8, 0.81))
        self.assertEqual(cache["_coverage"], {
            "boc:USDCAD": [["2025-01-02", "2025-01-03"],
                           ["2025-01-06", "2025-01-06"]],
            "yahoo:USDCAD": [["2016-06-01", "2016-06-01"]],
            "yahoo:CADUSD": [["2025-01-02", "2025-01-02"],
                             ["2025-01-05", "2025-01-05"]]})

    def test_the_rebuilt_file_has_the_planted_rates(self):
        from taxjson.bin import to_base_curr as TB
        text = _usd_rates_text()
        cache = PR.merge_into({}, PR.rows(text))
        today = date.today().isoformat()
        got = {d: v for d, v, _s in TB.resolve_rows(
            cache, "USD", "CAD", "2026-01-01", today, today)}
        self.assertEqual(got, {r[0]: r[3] for r in PR.rows(text)})

    def test_the_suites_home_needs_a_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            p = PR.plant(Path(td) / "work" / "to_base.csv",
                         "2025-01-02 12:00:00 USD CAD 1.2500 boc\n",
                         Path(td) / "home")
            self.assertTrue(p.is_file())
            self.assertIn("2025-01-02", json.loads(
                (Path(td) / "home" / PR.CACHE).read_text())
                ["_boc"]["USDCAD"]["obs"])
            with self.assertRaises(TypeError):
                PR.mirror(p)


class TestPlantedRatesSurviveARebuild(unittest.TestCase):
    """A run that finds the planted work/to_base.csv older than a
    taxjson source file rebuilds it from the HOME's currency cache: a
    test's own empty HOME gave it no rate ("no exchange rate"), the
    suite's synthetic one its 1.35 (other figures). Planted through
    _planted_rates, the rebuild writes the same rates back."""

    def _check(self, root, home=None):
        rates = root / "work" / "to_base.csv"
        os.utime(rates, AGED)
        r = _cli(root, "run", "--no-input", home=home)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertGreater(rates.stat().st_mtime, AGED[0])
        self.assertEqual(_rates(rates)["2026-03-02"], 1.25)
        g = json.loads((root / "work" / "m_gains.json").read_text())
        held, = g["inventory"]
        self.assertAlmostEqual(held["total_cost"], 1250.0, places=2)

    def test_the_tests_own_home(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = Path(td) / "p", Path(td) / "home"
            _usd_project(root)
            PR.plant(root / "work" / "to_base.csv", _usd_rates_text(), home)
            self._check(root, home)

    def test_the_suites_home_is_put_back(self):
        cache = Path(os.environ["HOME"]) / PR.CACHE
        before = cache.read_bytes() if cache.exists() else None
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _usd_project(root)
            undo = []
            PR.plant(root / "work" / "to_base.csv", _usd_rates_text(),
                     cleanup=undo.append)
            try:
                self._check(root)
            finally:
                for f in reversed(undo):
                    f()
        self.assertEqual(cache.read_bytes() if cache.exists() else None,
                         before)

    def test_the_fixed_modules_projects(self):
        # Three of the modules whose runs a rebuild broke (each failed
        # with its planted file aged): their projects now carry the
        # rates in the HOME they run under.
        import test_fix_a2_pipecmd as P
        import test_fix_crypto as C
        import test_fix_tobase_transfer_pair as T
        with tempfile.TemporaryDirectory() as td:
            root, home = P._tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 QZRB.US 10 USD 10 -100 0"])
            os.utime(root / "work" / "to_base.csv", AGED)
            r = P._cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        with tempfile.TemporaryDirectory() as td:
            root = C._project(td)
            (root / "inputs" / "crypto" / "kr_ledgers_2026.csv").write_text(
                C.KR_LEDGER_H + "LX1,RX1,2026-03-02 12:00:00,earn,reward,"
                "currency,crypto,QZS,spot / main,1.0,0,1\n")
            (Path(td) / ".crypto_price_cache.json").write_text(
                json.dumps({"QZS-2026-03-02": 5.0}))
            os.utime(root / "work" / "to_base.csv", AGED)
            r = C._run_cli(root, "run", "--no-input",
                           env=C._env(Path(td), TAXJSON_OFFLINE="1"))
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            T._project(root, "canada", "taxable", T._Run.MAP, True,
                       cleanup=self.addCleanup)
            os.utime(root / "work" / "to_base.csv", AGED)
            r = T._run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_rates(root / "work" / "to_base.csv")
                             ["2026-06-01"], 1.25)


# ----------------------------------------------------------- TJ-0636
def _script(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code(path):
    """A shell script's lines, comments left out."""
    return [ln.strip() for ln in (REPO / path).read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


class TestEditableInstallsMatchTheCode(unittest.TestCase):
    """The dev venv kept taxjson-fetch 0.30.1 metadata while the code was
    0.37.0 (release.sh reinstalled only the core after each version
    bump), and stale <name>.egg-info folders sat in the main checkout's
    source folders, read by any run with them on PYTHONPATH."""

    def setUp(self):
        self.ec = _script("editable_check")
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        for rel, v in (("pyproject.toml", "0.37.0"),
                       ("packages/taxjson-fetch/pyproject.toml", "0.37.0")):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f'[project]\nname = "x"\nversion = "{v}"\n')

    def _versions(self, **have):
        from importlib import metadata

        def version(name):
            v = have.get(name.replace("-", "_"))
            if v is None:
                raise metadata.PackageNotFoundError(name)
            return v
        return version

    def test_a_stale_plugin_version_is_named(self):
        got = self.ec.problems(self.root, self._versions(
            taxjson="0.37.0", taxjson_fetch="0.30.1"))
        self.assertEqual(got, [
            "the venv's taxjson-fetch is 0.30.1, the code 0.37.0 "
            "(packages/taxjson-fetch/pyproject.toml): re-run "
            "scripts/dev-setup.sh"])

    def test_matching_versions_and_no_plugin_pass(self):
        for have in (dict(taxjson="0.37.0", taxjson_fetch="0.37.0"),
                     dict(taxjson="0.37.0")):
            self.assertEqual(self.ec.problems(self.root,
                                              self._versions(**have)), [])
        self.assertEqual(self.ec.problems(self.root, self._versions()),
                         ["taxjson is not installed in this venv: run "
                          "scripts/dev-setup.sh"])

    def test_clean_removes_the_in_tree_egg_infos_only(self):
        eggs = [self.root / "src" / "taxjson.egg-info",
                self.root / "packages" / "taxjson-fetch" / "src"
                / "taxjson_fetch.egg-info"]
        keep = [self.root / "src" / "taxjson",
                self.root / "packages" / "taxjson-fetch" / "src"
                / "taxjson_fetch"]
        for d in eggs + keep:
            d.mkdir(parents=True)
            (d / "PKG-INFO").write_text("Version: 0.30.1\n")
        self.assertEqual(sorted(self.ec.clean(self.root)), sorted(eggs))
        self.assertEqual([d.exists() for d in eggs + keep],
                         [False, False, True, True])
        self.assertEqual(self.ec.clean(self.root), [])

    def test_dev_setup_cleans_and_checks_after_both_installs(self):
        code = _code("scripts/dev-setup.sh")
        core = code.index('venv/bin/pip install -e ".[fx,dev]"')
        plugin = code.index("venv/bin/pip install --no-deps -e "
                            "packages/taxjson-fetch")
        check = code.index("venv/bin/python -I scripts/editable_check.py "
                           "--clean")
        self.assertLess(core, plugin)
        self.assertLess(plugin, check)

    def test_release_reinstalls_the_plugin_with_the_core(self):
        code = _code("scripts/release.sh")
        core = code.index('"$PY" -m pip install -e . --quiet')
        plugin = code.index('"$PY" -m pip install --no-deps -e '
                            'packages/taxjson-fetch --quiet')
        rm = code.index("rm -rf src/*.egg-info packages/*/src/*.egg-info")
        version = next(i for i, ln in enumerate(code)
                       if "taxjson_run --version" in ln and i > core)
        self.assertLess(core, plugin)
        self.assertLess(plugin, rm)
        self.assertLess(rm, version)
        self.assertIn('if "$PY" -m pip show -q taxjson-fetch >/dev/null '
                      '2>&1; then', code[plugin - 1])


# ----------------------------------------------------------- TJ-0625
class TestStyleRunsReadAsAPerson(unittest.TestCase):
    """The style projects' runs (_style.Project.run) are piped at width
    120, like tj()'s, in a temp folder whose path length differs between
    machines: their output is _qa_project.Text too, so a phrase a line
    break splits is still found."""

    def test_project_run_returns_text(self):
        from _qa_project import Text
        from _style import Project
        with tempfile.TemporaryDirectory() as td:
            r = Project(Path(td), "canada").run("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsInstance(r.stdout, Text)
        self.assertIsInstance(r.stderr, Text)
        self.assertIn("taxjson", r.stdout)
        self.assertIn("taxjson 0", Text("taxjson\n0"))


if __name__ == "__main__":
    unittest.main()
