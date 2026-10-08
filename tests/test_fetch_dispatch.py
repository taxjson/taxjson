"""`taxjson fetch` in the core: the thin dispatcher over fetcher plugins.

The core holds no broker client and reads no broker credential; the
Questrade / IBKR Flex fetcher is the separate taxjson-fetch package
(packages/taxjson-fetch, its own tests). These tests pin the plugin
interface (lib/fetchers, entry-point group `taxjson.fetchers`) with a
fake in-process fetcher, the one-line install hint when none is
installed, and the config check either way. Hermetic whether or not a
real fetcher is installed in the running environment: discovery is
patched in-process, and the CLI test runs `python -S` (no site-packages,
so no installed plugin) on the checkout's src/.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import EntryPoint
from pathlib import Path
from unittest import mock

from taxjson.lib import fetchers as FP
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

CALLS = []


class FakeFetcher:
    """A fetcher plugin as a third party would write one."""
    brokerages = ("fakebroker",)
    description = "Fake Broker test fetcher"
    account_keys = ("fake_key",)
    setup_hint = "Add brokerage = \"fakebroker\" under [accounts.x]."

    def add_arguments(self, p):
        p.add_argument("--fake-flag", action="store_true")

    def fetch(self, request):
        CALLS.append(request)
        request.say(f"fake: {','.join(request.accounts)}")
        return {a: {"source": "fakebroker", "dry": request.dry_run}
                for a in request.accounts}


class NoFetchMethod:
    brokerages = ("x",)


def _eps(*specs):
    return [EntryPoint(name, value, FP.ENTRY_POINT_GROUP)
            for name, value in specs]


class _Patched(unittest.TestCase):
    """Discovery sees exactly the entry points a test names."""
    EPS = ()

    def setUp(self):
        CALLS.clear()
        p = mock.patch.object(FP, "_entry_points",
                              return_value=_eps(*self.EPS))
        p.start()
        self.addCleanup(p.stop)
        FP.discover(refresh=True)
        self.addCleanup(FP.discover, True)

    def project(self, accounts):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n' + accounts)
        return root

    def fetch(self, root, *argv):
        from taxjson.bin import taxjson_run as R
        p = argparse.ArgumentParser()
        p.add_argument("account", nargs="*")
        for flag in ("--list", "--json", "--dry-run"):
            p.add_argument(flag, action="store_true")
        p.add_argument("--fetcher", default=None)
        FP.add_fetcher_arguments(p)
        args = p.parse_args(list(argv))
        args.dir = str(root)
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                R.cmd_fetch(args)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                if not isinstance(e.code, int) and e.code is not None:
                    err.write(str(e.code))
        return code, out.getvalue(), err.getvalue()


class TestNoFetcherInstalled(_Patched):
    EPS = ()

    def test_fetch_is_one_install_line_and_exit_2(self):
        root = self.project('[accounts.margin]\ntype = "taxable"\n'
                            'brokerage = "questrade"\naccount = "1"\n')
        code, out, err = self.fetch(root)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 1, err)
        # The installer or a checkout — never a PyPI name (security
        # review H1: taxjson-fetch is not published there).
        self.assertNotIn("pip install taxjson", lines[0])
        self.assertIn("installs taxjson-fetch by default", lines[0])
        self.assertIn("pip install -e packages/taxjson-fetch", lines[0])

    def test_list_json_is_empty(self):
        code, out, _err = self.fetch(self.project(""), "--list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"fetchers": []})

    def test_fetch_keys_are_accepted_with_a_note(self):
        from taxjson.bin.taxjson_run import (_fetch_plugin_note,
                                             validate_config)
        cfg = {"settings": {"year": 2026, "country": "canada"},
               "accounts": {"margin": {"type": "taxable",
                                       "brokerage": "questrade",
                                       "account": "12345678"},
                            "ibkr": {"type": "taxable",
                                     "brokerage": "ibkr_flex",
                                     "query_id": "42"}}}
        self.assertEqual(validate_config(cfg), [])
        note = _fetch_plugin_note(cfg)
        self.assertIn("[accounts.margin]", note)
        self.assertIn("[accounts.ibkr]", note)
        self.assertIn("installs taxjson-fetch by default", note)
        self.assertNotIn("pip install taxjson", note)
        self.assertIsNone(_fetch_plugin_note(
            {"accounts": {"m": {"type": "taxable"}}}))

    def test_retired_fetch_tables_point_at_the_account_keys(self):
        # The v0.3.0 [fetch.*] tables are no longer read (fetch config
        # lives on the account). Unknown top-level tables now warn
        # (R1-216); the retired one says where its settings went.
        from taxjson.bin.taxjson_run import validate_config
        warnings = validate_config(
            {"settings": {"year": 2026, "country": "canada"},
             "accounts": {"margin": {"type": "taxable"}},
             "fetch": {"margin": {"source": "questrade",
                                  "number": "1"}}})
        hits = [w for w in warnings if "fetch" in w]
        self.assertEqual(len(hits), 1, warnings)
        self.assertIn("brokerage", hits[0])


class TestNoFetcherCli(unittest.TestCase):
    """The real CLI with no site-packages (`python -S`): no installed
    plugin can be seen, whatever this environment holds."""

    def _cli(self, root, *args):
        env = dict(os.environ, PYTHONPATH=str(SRC))
        return subprocess.run(
            [sys.executable, "-S", "-m", "taxjson.bin.taxjson_run",
             "-C", str(root), *args], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_any_options_get_the_install_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                'brokerage = "questrade"\naccount = "1"\n')
            for argv in (["fetch"], ["fetch", "--year", "2025"],
                         ["fetch", "margin", "--refresh-token", "x"],
                         ["fetch", "run"]):
                with self.subTest(argv=argv):
                    r = self._cli(tmp, *argv)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertEqual(r.stdout, "")
                    self.assertEqual(len(r.stderr.strip().splitlines()),
                                     1, r.stderr)
                    self.assertIn("installs taxjson-fetch by default",
                                  r.stderr)
                    self.assertNotIn("pip install taxjson", r.stderr)
            r = self._cli(tmp, "fetch", "-h")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Download broker activity", r.stdout)
            self.assertIn("pip install -e packages/taxjson-fetch",
                          " ".join(r.stdout.split()))


class TestDispatch(_Patched):
    EPS = (("fake", "test_fetch_dispatch:FakeFetcher"),
           ("broken", "test_fetch_dispatch:NoSuchThing"),
           ("nofetch", "test_fetch_dispatch:NoFetchMethod"))

    ACCOUNTS = ('[accounts.b]\ntype = "taxable"\nbrokerage = "fakebroker"\n'
                'fake_key = "z"\n'
                '[accounts.a]\ntype = "taxable"\nbrokerage = "fakebroker"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')

    def test_discovery_skips_broken_plugins_with_a_reason(self):
        found, problems = FP.discover()
        self.assertEqual([f.name for f in found], ["fake"])
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("'broken'" in p for p in problems))
        self.assertTrue(any("no fetch(request)" in p for p in problems))

    def test_list(self):
        code, out, _err = self.fetch(self.project(""), "--list")
        self.assertEqual(code, 0)
        self.assertIn("fake", out)
        self.assertIn("fakebroker", out)
        self.assertIn("Fake Broker test fetcher", out)

    def test_every_declared_account_goes_to_its_fetcher(self):
        root = self.project(self.ACCOUNTS)
        code, out, err = self.fetch(root, "--fake-flag")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(CALLS), 1)
        req = CALLS[0]
        self.assertEqual(req.accounts, ["a", "b"])      # sorted
        self.assertEqual(req.root, root.resolve())
        self.assertEqual(req.work, root.resolve() / "work")
        self.assertTrue(req.args.fake_flag)
        self.assertIn("fake: a,b", out)
        self.assertIn("fetch complete", out)

    def test_json_document_and_progress_on_stderr(self):
        root = self.project(self.ACCOUNTS)
        code, out, err = self.fetch(root, "b", "--json", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {
            "accounts": {"b": {"source": "fakebroker", "dry": True}},
            "dry_run": True})
        self.assertIn("fake: b", err)

    def test_account_without_brokerage_named_explicitly(self):
        code, _out, err = self.fetch(self.project(self.ACCOUNTS), "rrsp")
        self.assertEqual(code, 1)
        self.assertIn("declares no `brokerage`", err)
        code, _out, err = self.fetch(self.project(self.ACCOUNTS), "ghost")
        self.assertIn("no [accounts.ghost]", err)
        self.assertEqual(CALLS, [])

    def test_brokerage_no_fetcher_serves_refuses_before_any_download(self):
        root = self.project(self.ACCOUNTS + '[accounts.q]\ntype = '
                            '"taxable"\nbrokerage = "questrade"\n')
        code, _out, err = self.fetch(root)
        self.assertEqual(code, 1)
        self.assertIn("[accounts.q] brokerage 'questrade' has no "
                      "installed fetcher", err)
        self.assertEqual(CALLS, [])

    def test_no_account_declares_a_brokerage_shows_the_setup_hint(self):
        code, _out, err = self.fetch(
            self.project('[accounts.m]\ntype = "taxable"\n'))
        self.assertEqual(code, 1)
        self.assertIn("no account declares a fetch source", err)
        self.assertIn('brokerage = "fakebroker"', err)

    def test_offline_refuses_before_any_fetcher_runs(self):
        # Security review L6: TAXJSON_OFFLINE forbids taxjson's network
        # egress, and `taxjson fetch` is egress — one line, no plugin
        # call (dry runs too: a dry run still downloads).
        root = self.project(self.ACCOUNTS)
        for argv in ((), ("--dry-run",), ("b", "--json")):
            with self.subTest(argv=argv), \
                    mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}):
                code, out, err = self.fetch(root, *argv)
                self.assertEqual(code, 1, err)
                self.assertEqual(out, "")
                # (this class's broken test plugins add their warnings)
                lines = [ln for ln in err.strip().splitlines()
                         if "warning: fetcher" not in ln]
                self.assertEqual(len(lines), 1, err)
                self.assertIn("TAXJSON_OFFLINE is set", lines[0])
        self.assertEqual(CALLS, [])
        # --list never touches the network: still allowed.
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "yes"}):
            code, out, _err = self.fetch(root, "--list")
        self.assertEqual(code, 0)
        self.assertIn("fakebroker", out)
        # TAXJSON_OFFLINE=0 is OFF (lib/offline): the fetch runs.
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}):
            code, _out, err = self.fetch(root)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(CALLS), 1)

    def test_unknown_fetcher_name(self):
        code, _out, err = self.fetch(self.project(self.ACCOUNTS),
                                     "--fetcher", "nope")
        self.assertEqual(code, 1)
        self.assertIn("no installed fetcher named 'nope'", err)

    def test_config_check_uses_the_installed_brokerages(self):
        from taxjson.bin.taxjson_run import (_fetch_plugin_note,
                                             validate_config)
        base = {"settings": {"year": 2026, "country": "canada"}}
        ok = dict(base, accounts={"m": {"type": "taxable",
                                        "brokerage": "fakebroker",
                                        "fake_key": "z"}})
        self.assertEqual(validate_config(ok), [])
        self.assertIsNone(_fetch_plugin_note(ok))
        typo = dict(base, accounts={"m": {"type": "taxable",
                                          "brokerage": "fakebrokr"}})
        w = validate_config(typo)
        self.assertEqual(len(w), 1, w)
        self.assertIn("is not a fetch source (fakebroker)", w[0])
        self.assertIn("did you mean 'fakebroker'", w[0])
        # A plugin's own key is unknown without a brokerage (no scan).
        bare = dict(base, accounts={"m": {"type": "taxable",
                                          "fake_key": "z"}})
        self.assertTrue(any("fake_key" in x for x in validate_config(bare)))


class TestCoreHoldsNoBrokerClient(unittest.TestCase):
    def test_no_broker_endpoint_or_credential_in_the_core(self):
        needles = ("questrade.com", "interactivebrokers.com",
                   "QUESTRADE_REFRESH_TOKEN", "IBKR_FLEX_TOKEN",
                   ".questrade_token", "FlexWebService")
        for path in sorted((SRC / "taxjson").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for n in needles:
                self.assertNotIn(n, text, f"{path.name}: {n}")


if __name__ == "__main__":
    unittest.main()
