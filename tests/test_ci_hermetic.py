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

from taxjson.lib.tomlcompat import tomllib

REPO = Path(__file__).resolve().parent.parent


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
        self.assertEqual(Path(T.CACHE_FILE).parent,
                         Path(os.environ["HOME"]))
        today = T._s(T.date.today())
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
        # Every module the stage names exists.
        names = re.search(r'EXTRAS_TESTS="([^"]*)"', sh).group(1).split()
        self.assertTrue(names)
        for m in names:
            self.assertTrue((REPO / "tests" / f"{m}.py").is_file(), m)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux only")
class TestHiddenExtraStillAllowsATestsFake(unittest.TestCase):
    def test_fake_on_pythonpath_imports_installed_copy_does_not(self):
        import subprocess
        import tempfile
        sh = (REPO / "scripts" / "ci_no_extras.sh").read_text()
        body = sh.split('cat > "$ROOT/hide/sitecustomize.py" <<EOF\n', 1)[1]
        body = body.split("\nEOF\n", 1)[0]
        with tempfile.TemporaryDirectory() as td:
            hide, fake = Path(td, "hide"), Path(td, "fake", "json5x")
            hide.mkdir()
            fake.mkdir(parents=True)
            (fake / "__init__.py").write_text("FAKE = True\n")
            # `json5x` stands in for an extra: hidden from site-packages,
            # served from the test's own folder.
            (hide / "sitecustomize.py").write_text(
                body.replace("$EXTRAS", "json5x unittest pip"))
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
            import importlib.util
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
