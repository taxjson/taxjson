"""TJ-2554: a taxjson-fetch of another version than the core's is never loaded;
`tjs fetch --list` names it with the reason, and a fetch it would serve refuses
in one line naming the upgrade (synthetic: made-up installed distributions).

taxjson-fetch is released with the core (scripts/release.sh gives both one
version) and runs only at the core's own X.Y.Z (lib/fetchers
`LOCKSTEP_DISTRIBUTION`, `lockstep_problem`): the published 0.41.0 imports
core names 1.0.0 no longer has, so once the core alone was upgraded
`tjs fetch` stopped with an ImportError traceback. Each test installs fake
distributions (dist-info folders of their own) whose plugin records its
import, adds a fetch option and fails in fetch() as 0.41.0 did; another
broker's made-up fetcher (`zz-broker-fetch`) serves a brokerage of its own.

The suite runs with the real plugin hidden (scripts/hide_optional.py
`--plugin taxjson_fetch`, first on PYTHONPATH: scripts/ci.sh and
scripts/ci_no_extras.sh), which hides every distribution named
taxjson-fetch, these fakes too. So the entry points are read from the fake
dist-info folders directly (`metadata.PathDistribution`), and a `tjs fetch`
child gets a PYTHONPATH without that folder (`_without_the_hide`): the fake
then shadows a taxjson-fetch the environment has, being first on its path.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest import mock

from taxjson.lib import fetchers as FP
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.install_hint import INSTALLER
import _paths

# The plugin a fake taxjson-fetch ships: 0.41.0's shape (TJ-2554).
_OLD_PLUGIN = '''\
LOADED = True
class Fetcher:
    brokerages = ("questrade",)
    description = "an older taxjson-fetch (made up)"
    def add_arguments(self, p):
        p.add_argument("--trim-overlap", action="store_true")
    def fetch(self, request):
        # 0.41.0: a reader the core has since moved.
        from taxjson.lib.brokerages.questrade import questrade_cash_events
        return {}
'''
# Another broker's fetcher (made up): it fetches nothing.
_OTHER_PLUGIN = '''\
LOADED = True
class Fetcher:
    brokerages = ("zzbroker",)
    description = "another broker's fetcher (made up)"
    def fetch(self, request):
        request.say("zzbroker: nothing to fetch")
        return {a: {"files": []} for a in request.accounts}
'''
_OTHER = ("zz-broker-fetch", "0.1.0", "zzbroker", _OTHER_PLUGIN)
_LINE = ("taxjson-fetch 0.41.0 is installed with taxjson 1.0.0: the two are released "
         "together and run only at the same version, so it is not loaded — upgrade both "
         f"by re-running the installer (`{INSTALLER}`), or, from a checkout, "
         "`pip install --no-deps -e packages/taxjson-fetch` into taxjson's own environment "
         "after the core, from the same checkout — ")


def _install(folder: Path, version: str, name: str = "taxjson-fetch",
             module: str = "zzfetchold", plugin: str = _OLD_PLUGIN) -> Path:
    """A distribution `name` `version` installed in `folder`: its dist-info
    (METADATA, entry_points.txt) and the plugin package `module`. -> the
    dist-info folder."""
    info = folder / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(
        f"[{FP.ENTRY_POINT_GROUP}]\n{name} = {module}.plugin:Fetcher\n", encoding="utf-8")
    (folder / module).mkdir()
    (folder / module / "__init__.py").write_text("", encoding="utf-8")
    (folder / module / "plugin.py").write_text(plugin, encoding="utf-8")
    return info


def _without_the_hide(pythonpath: str) -> list:
    """PYTHONPATH's folders but scripts/hide_optional.py's (its
    sitecustomize.py names the script)."""
    def hides(p):
        try:
            return "hide_optional" in (Path(p) / "sitecustomize.py").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
    return [p for p in pythonpath.split(os.pathsep) if p and not hides(p)]


class TestTheLockstepRule(unittest.TestCase):
    """lib/fetchers' discovery over fake installed distributions, the core's
    version patched (importlib.metadata.version)."""

    CORE = "1.0.0"

    def install(self, *dists):
        """`dists`: (name, version, module, plugin) each. -> discover()."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        infos = [_install(folder, version, name, module, plugin)
                 for name, version, module, plugin in dists]
        sys.path.insert(0, str(folder))
        self.addCleanup(sys.path.remove, str(folder))
        for _name, _version, module, _plugin in dists:
            self.addCleanup(sys.modules.pop, module, None)
            self.addCleanup(sys.modules.pop, f"{module}.plugin", None)

        def entry_points(group=None, **_kw):
            # Read from the folders themselves: the hidden-plugin finder
            # (scripts/hide_optional.py) would drop a taxjson-fetch found
            # by a search.
            return [ep for info in infos for ep in metadata.PathDistribution(info).entry_points
                    if ep.group == group]

        real_version = metadata.version

        def version_of(dist):
            if dist == "taxjson":
                if self.CORE is None:
                    raise metadata.PackageNotFoundError(dist)
                return self.CORE
            return real_version(dist)

        for target, fake in (("importlib.metadata.entry_points", entry_points),
                             ("importlib.metadata.version", version_of)):
            p = mock.patch(target, fake)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(FP.discover, True)
        return FP.discover(refresh=True)

    def old(self, version="0.41.0"):
        return ("taxjson-fetch", version, "zzfetchold", _OLD_PLUGIN)

    def test_an_older_plugin_is_refused_and_never_loaded(self):
        found, problems = self.install(self.old())
        self.assertEqual((found, problems), ([], []))
        self.assertNotIn("zzfetchold.plugin", sys.modules)
        self.assertEqual(len(FP.mismatched()), 1)
        self.assertTrue(FP.mismatched()[0].startswith(_LINE), FP.mismatched()[0])
        self.assertTrue(FP.mismatched()[0].endswith("is not ours"), FP.mismatched()[0])
        self.assertEqual(FP.not_loaded(), [{"name": "taxjson-fetch",
                                            "distribution": "taxjson-fetch 0.41.0",
                                            "problem": FP.mismatched()[0]}])

    def test_a_newer_plugin_is_refused(self):
        found, _problems = self.install(self.old("1.1.0"))
        self.assertEqual(found, [])
        self.assertIn("taxjson-fetch 1.1.0 is installed with taxjson 1.0.0", FP.mismatched()[0])

    def test_the_same_version_loads(self):
        # A dev or local suffix aside: the X.Y.Z are compared.
        found, problems = self.install(self.old("1.0.0.dev3+g1a2b"))
        self.assertEqual(([f.name for f in found], problems), (["taxjson-fetch"], []))
        self.assertEqual((FP.mismatched(), FP.not_loaded()), ([], []))
        self.assertTrue(sys.modules["zzfetchold.plugin"].LOADED)

    def test_another_brokers_fetcher_is_not_held_to_it(self):
        found, _problems = self.install(self.old(), _OTHER)
        self.assertEqual([f.name for f in found], ["zz-broker-fetch"])
        self.assertEqual([r["name"] for r in FP.not_loaded()], ["taxjson-fetch"])

    def test_a_core_version_that_cannot_be_read_is_no_match(self):
        self.CORE = None
        found, _problems = self.install(self.old("1.0.0"))
        self.assertEqual(found, [])
        self.assertIn("taxjson-fetch 1.0.0 is installed with taxjson (version unknown)",
                      FP.mismatched()[0])

    def test_the_run_note_names_the_mismatch_not_a_missing_plugin(self):
        from taxjson.pipeline.config import _fetch_plugin_note
        self.install(self.old())
        note = _fetch_plugin_note({"accounts": {"qt": {"type": "taxable",
                                                       "brokerage": "questrade"}}})
        self.assertIn("taxjson-fetch 0.41.0 is installed with taxjson 1.0.0", note)
        self.assertNotIn("no fetcher is installed", note)


class _Fetching(unittest.TestCase):
    """`tjs fetch` in a child process with fake fetchers installed (first on
    its path: they shadow a taxjson-fetch the environment has)."""

    DISTS: tuple = ()

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.site = td / "site"
        cls.site.mkdir()
        _install(cls.site, "0.41.0")
        # The core's own version as the child reads it, held to the one the
        # expected line names: first on the path, this metadata is found before
        # the environment's (whatever release it is at; backlog TJ-4970).
        core = cls.site / "taxjson-1.0.0.dist-info"
        core.mkdir()
        (core / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: taxjson\nVersion: 1.0.0\n", encoding="utf-8")
        for name, version, module, plugin in cls.DISTS:
            _install(cls.site, version, name, module, plugin)
        cls.root = td / "p"
        cls.root.mkdir()
        (cls.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\nbase_currency = "CAD"\n\n'
            '[accounts.qt]\ntype = "taxable"\nbrokerage = "questrade"\n\n'
            '[accounts.zz]\ntype = "taxable"\nbrokerage = "zzbroker"\n', encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def tjs(self, *args):
        env = dict(os.environ, TAXJSON_WIDTH="0")
        # The fakes fetch nothing: the offline switch would refuse before
        # a plugin runs.
        env["TAXJSON_OFFLINE"] = "0"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(self.site), str(_paths.SRC)] + _without_the_hide(env.get("PYTHONPATH", "")))
        return subprocess.run(python_module_argv("taxjson.bin.taxjson_run", args),
                              cwd=self.root, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=300, check=False)

    def assertOneLine(self, r, start=r"^tjs( fetch)?: error: "):
        # (`tjs:` before the command runs, as with no fetcher)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        lines = r.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertRegex(lines[0], start)
        self.assertIn(_LINE, lines[0])
        self.assertTrue(lines[0].endswith("is not ours."), lines[0])
        return lines[0]


class TestOnlyTheOlderPlugin(_Fetching):

    def test_a_fetch_says_so_in_one_line(self):
        for args in (("fetch", "--trim-overlap"), ("fetch",), ("fetch", "--list")):
            with self.subTest(args=args):
                line = self.assertOneLine(self.tjs(*args))
                self.assertRegex(line, r"^tjs( fetch)?: error: taxjson-fetch 0\.41\.0 is ")

    def test_the_json_list_is_a_document_naming_it(self):
        r = self.tjs("fetch", "--list", "--json")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        doc = json.loads(r.stdout)
        self.assertEqual(doc["fetchers"], [])
        self.assertEqual([(g["name"], g["distribution"]) for g in doc["not_loaded"]],
                         [("taxjson-fetch", "taxjson-fetch 0.41.0")])
        self.assertTrue(doc["not_loaded"][0]["problem"].startswith(_LINE))


class TestBesideAnotherBrokersFetcher(_Fetching):
    """The older taxjson-fetch beside a fetcher of another broker: only what
    it would serve refuses."""

    DISTS = (_OTHER,)

    def test_the_list_names_both(self):
        r = self.tjs("fetch", "--list")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "zz-broker-fetch (zz-broker-fetch 0.1.0): zzbroker")
        self.assertIn("taxjson-fetch (taxjson-fetch 0.41.0): not loaded", lines)
        self.assertIn(_LINE.split(" — ")[0], r.stdout)
        doc = json.loads(self.tjs("fetch", "--list", "--json").stdout)
        self.assertEqual([f["name"] for f in doc["fetchers"]], ["zz-broker-fetch"])
        self.assertEqual([g["name"] for g in doc["not_loaded"]], ["taxjson-fetch"])

    def test_the_other_brokers_account_is_fetched(self):
        r = self.tjs("fetch", "zz")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("zzbroker: nothing to fetch", r.stdout)
        self.assertNotIn("taxjson-fetch", r.stdout + r.stderr)

    def test_an_account_only_it_would_serve_refuses(self):
        line = self.assertOneLine(self.tjs("fetch", "qt"), r"^tjs fetch: error: ")
        self.assertIn("[accounts.qt] brokerage 'questrade': no loaded fetcher serves it, "
                      "and taxjson-fetch 0.41.0", line)
        # Every account: the first that cannot be served stops it, before
        # any fetcher runs.
        self.assertOneLine(self.tjs("fetch"), r"^tjs fetch: error: \[accounts\.qt\]")

    def test_naming_it_refuses(self):
        self.assertOneLine(self.tjs("fetch", "--fetcher", "taxjson-fetch"),
                           r"^tjs fetch: error: taxjson-fetch 0\.41\.0 is ")


if __name__ == "__main__":
    unittest.main()
