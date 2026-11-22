"""The suite never reads the developer's machine (tests/_hermetic,
scripts/ci_no_extras.sh).

GitHub's `tests` workflow failed on every push for 60 runs while the
local gate passed: the style projects and the US-engine tests read
their exchange rates from the developer's ~/.currency_price_cache.json,
which a fresh runner does not have (nor the [fx] extra, nor a cache of
crypto prices). These pin the switch that makes every test process
hermetic, and the gate stage that runs the extras-sensitive tests as a
core install (no extras) runs them.
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

import _hermetic

import tomllib
import _paths

REPO = _paths.REPO


class TestSyntheticHome(unittest.TestCase):
    def test_home_is_the_synthetic_one(self):
        home = os.environ["HOME"]
        self.assertEqual(home, os.environ[_hermetic.MARK])
        self.assertTrue(Path(home, ".currency_price_cache.json").is_file())
        for var in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            self.assertTrue(os.environ[var].startswith(home), var)
        self.assertEqual(os.environ["TAXJSON_OFFLINE"], "1")
        self.assertEqual(os.path.expanduser("~"), home)

    def test_the_rates_stage_reads_the_synthetic_cache(self):
        from taxjson.bin import to_base_curr as T
        from taxjson.lib import rate_cache
        self.assertEqual(Path(rate_cache.CACHE_FILE).parent,
                         Path(os.environ["HOME"]))
        today = rate_cache._s(T.date.today())
        for frm, to, rate in (("USD", "CAD", "1.3500"),
                              ("CAD", "USD", "0.7407")):
            with self.subTest(pair=frm + to):
                rows, errors, _notes = T.build_rates(
                    frm, to, "2003-06-02", today, offline=True)
                self.assertEqual(errors, [])
                got = {d: v for d, v, _s in rows}
                # Every day, the oldest and today included (weekends
                # take the Friday's rate).
                self.assertEqual(float(got["2003-06-02"]), float(rate))
                self.assertEqual(float(got[today]), float(rate))

    def test_install_is_idempotent(self):
        home = os.environ["HOME"]
        self.assertEqual(_hermetic.install(), home)


    def test_cache_covers_every_pair_through_next_week(self):
        import datetime
        today = datetime.date(2026, 3, 4)
        cache = _hermetic.rate_cache(today)
        for pair in _hermetic.SYNTHETIC_RATES:
            key = (f"boc:{pair}" if pair.endswith("CAD")
                   else f"yahoo:{pair}")
            self.assertEqual(cache["_coverage"][key][0][1], "2026-03-11",
                             pair)
        json.dumps(cache)


# A process whose first import of the setup is inside a patch of the
# environment, and whose test then patches HOME and is the first to import
# the two caches and the QA helper.
_FIRST_UNDER_PATCH = """
import json, os, sys
from unittest import mock
with mock.patch.dict(os.environ):
    import _hermetic
loaded = sorted(m for m in sys.modules if m.split(".")[0] == "taxjson")
with mock.patch.dict(os.environ, {"HOME": sys.argv[1]}):
    from taxjson.lib import crypto_prices, rate_cache
    import _qa_project
    inside = os.environ["HOME"]
print(json.dumps({"home": _hermetic.HOME, "loaded": loaded, "inside": inside,
                  "rates": rate_cache.CACHE_FILE, "crypto": crypto_prices.CACHE_FILE,
                  "qa": _qa_project.ENV["HOME"]}))
"""


class TestCachesTakeTheSetupsHome(unittest.TestCase):
    """The caches name their file from HOME at import: in a test process
    they take the setup's HOME, whatever HOME the first test to import one
    has patched in (TJ-3889); the setup imports nothing of taxjson itself
    (TJ-3891); `_qa_project` reads the setup's HOME from the setup, not
    from its record in the environment, which a patch can take (TJ-3894)."""

    def test_first_import_under_a_patched_home(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            given, patched = Path(td, "given"), Path(td, "patched")
            given.mkdir()
            patched.mkdir()
            env = {k: v for k, v in os.environ.items() if k != _hermetic.MARK}
            env.update(HOME=str(given), PYTHONPATH=os.pathsep.join(
                [str(_paths.TESTS), str(_paths.SRC)]))
            r = subprocess.run(
                [sys.executable, "-c", _FIRST_UNDER_PATCH, str(patched)],
                cwd=td, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = json.loads(r.stdout)
            home = got["home"]
            self.assertNotIn(home, (str(given), str(patched)))
            self.assertEqual(got["loaded"], [])
            self.assertEqual(got["inside"], str(patched))
            self.assertEqual(Path(got["rates"]), Path(home, ".currency_price_cache.json"))
            self.assertEqual(Path(got["crypto"]), Path(home, ".crypto_price_cache.json"))
            self.assertEqual(Path(got["qa"]), Path(home))
            self.assertEqual(sorted(os.listdir(patched)), [])

    def test_the_loader_puts_back_only_what_it_set(self):
        """A key another import (or the test) changed while the module code
        ran keeps that change (TJ-3915); one it did not is put back."""
        from unittest import mock

        class Loader:
            def __init__(self, during=None):
                self.during, self.seen = during, None

            def create_module(self, spec):
                return None

            def exec_module(self, module):
                self.seen = os.environ["HOME"]
                if self.during:
                    os.environ["HOME"] = self.during

        for during, after in ((None, "/patched"), ("/changed", "/changed")):
            with self.subTest(during=during), mock.patch.dict(os.environ, {"HOME": "/patched"}):
                inner = Loader(during)
                _hermetic._UnderHome(inner, "/setup").exec_module(None)
                self.assertEqual(inner.seen, "/setup")
                self.assertEqual(os.environ["HOME"], after)

    def test_the_loader_copies(self):
        import copy
        loader = _hermetic._UnderHome(object(), "/setup")
        self.assertEqual(copy.copy(loader)._home, "/setup")



class TestGateStage(unittest.TestCase):
    """scripts/ci.sh runs the suite in an empty HOME, then the
    extras-sensitive tests with the extras hidden."""

    def test_gate_runs_the_no_extras_stage(self):
        ci = (REPO / "scripts" / "ci.sh").read_text()
        self.assertIn("stage no-extras bash scripts/ci_no_extras.sh", ci)
        self.assertIn('HOME="$CI_TMP/home"', ci)

    def test_hidden_extras_are_the_all_extra(self):
        # The stage hides every optional extra; a new one added to
        # pyproject's `all` is hidden too. Import names that differ
        # from the distribution's name are listed here.
        import_name = {"google-generativeai": "google.generativeai"}
        doc = tomllib.loads((REPO / "pyproject.toml").read_text())
        want = set()
        for req in doc["project"]["optional-dependencies"]["all"]:
            dist = re.split(r"[\s<>=!~;\[]", req, maxsplit=1)[0]
            want.add(import_name.get(dist.lower(),
                                     dist.lower().replace("-", "_")))
        sh = (REPO / "scripts" / "ci_no_extras.sh").read_text()
        got = set(re.search(r'^EXTRAS="([^"]*)"', sh, re.MULTILINE).group(1).split())
        self.assertEqual(got, want)

    def test_stage_environment_is_a_runners(self):
        sh = (REPO / "scripts" / "ci_no_extras.sh").read_text()
        run = sh.rsplit("env -i", 1)[1]          # no TAXJSON_* / proxies
        self.assertIn('HOME="$ROOT/home"', run)   # an empty HOME
        self.assertNotIn("TAXJSON_WIDTH", run)    # as the workflow runs
        # Every module the stage names exists; a class named in one
        # (`module.Class`, as a split module's classes are, PLAN.md step 9)
        # is defined at its top level.
        names = re.search(r'EXTRAS_TESTS="([^"]*)"', sh).group(1).split()
        self.assertTrue(names)
        for m in names:   # a package's module named dotted
            path = REPO / "tests" / (m.replace(".", "/") + ".py")
            if path.is_file():
                continue
            mod, _, cls = m.rpartition(".")
            path = REPO / "tests" / (mod.replace(".", "/") + ".py")
            self.assertTrue(path.is_file(), m)
            self.assertRegex(path.read_text(encoding="utf-8"), rf"(?m)^class {cls}\b", m)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux only")
class TestHiddenExtraStillAllowsATestsFake(unittest.TestCase):
    def test_fake_on_pythonpath_imports_installed_copy_does_not(self):
        import importlib.util
        import subprocess
        import tempfile
        spec = importlib.util.spec_from_file_location(
            "hide_optional", REPO / "scripts" / "hide_optional.py")
        hide_optional = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hide_optional)
        with tempfile.TemporaryDirectory() as td:
            hide, fake = Path(td, "hide"), Path(td, "fake", "json5x")
            hide.mkdir()
            fake.mkdir(parents=True)
            (fake / "__init__.py").write_text("FAKE = True\n")
            # `json5x` stands in for an extra: hidden from site-packages,
            # served from the test's own folder.
            (hide / "sitecustomize.py").write_text(hide_optional.render(
                "json5x unittest pip", "taxjson_fetch"))
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(
                [str(hide), str(fake.parent)]))
            r = subprocess.run(
                [sys.executable, "-c", "import json5x; print(json5x.FAKE)"],
                env=env, capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "True", r.stderr)
            # `unittest` lives in the stdlib, not site-packages: imports.
            r = subprocess.run([sys.executable, "-c", "import unittest"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            # An installed package (pip, in site-packages) is hidden.
            import sysconfig
            spec = importlib.util.find_spec("pip")
            purelib = os.path.realpath(sysconfig.get_paths()["purelib"])
            if spec is None or not os.path.realpath(
                    spec.origin or "").startswith(purelib + os.sep):
                self.skipTest("pip is not installed in site-packages")
            r = subprocess.run([sys.executable, "-c", "import pip"],
                               env=env, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("optional extra, hidden", r.stderr)


if __name__ == "__main__":
    unittest.main()
