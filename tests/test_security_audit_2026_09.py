"""2026-09 security/privacy audit pins: owner-only file modes, the
network-serve token, credential handling in `taxjson fetch`, and the
dependency floor. All values synthetic."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.tomlcompat import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _run_loose(args, cwd):
    """Run a command under a permissive 022 umask, as a typical shell."""
    return subprocess.run(
        [sys.executable, "-c",
         "import os, runpy, sys; os.umask(0o022); sys.argv = sys.argv[1:]; "
         "runpy.run_module(sys.argv[0], run_name='__main__')", *args],
        cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)


class TestOwnerOnlyModes(unittest.TestCase):
    def test_init_creates_private_project_under_loose_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            r = _run_loose(["taxjson.bin.taxjson_run", "init", str(proj),
                            "--country", "canada"], cwd=REPO_ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_mode(proj / "taxjson.toml"), 0o600)
            dirs = [d for d in (proj / "inputs").iterdir() if d.is_dir()]
            self.assertTrue(dirs)
            for d in dirs:
                self.assertEqual(_mode(d), 0o700, d)
            self.assertEqual(_mode(proj / "inputs"), 0o700)

    def test_entry_trampoline_sets_umask_then_calls_main(self):
        code = ("import os, sys; os.umask(0o022); "
                "from taxjson.bin._entry import taxjson_validate as f; "
                "sys.argv=['taxjson-validate', '--help']\n"
                "try:\n    f()\nexcept SystemExit:\n    pass\n"
                "m = os.umask(0); print(oct(m))")
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "0o77")

    def test_every_console_script_resolves(self):
        doc = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        import importlib
        for name, target in doc["project"]["scripts"].items():
            mod, _, attr = target.partition(":")
            if mod == "taxjson.bin._entry":          # trampoline -> <attr>.main
                mod, attr = f"taxjson.bin.{attr}", "main"
            self.assertTrue(callable(getattr(importlib.import_module(mod), attr)), name)

    def test_write_private_tightens_existing_dir_and_file(self):
        from taxjson.bin.taxjson_fetch import write_private
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "inputs" / "margin"
            d.mkdir(parents=True)
            os.chmod(d, 0o755)
            out = d / "questrade_2026.csv"
            out.write_text("old")
            os.chmod(out, 0o644)
            # A symlink planted at the .part path is replaced, not followed.
            victim = Path(tmp) / "victim"
            victim.write_text("keep")
            (d / "questrade_2026.csv.part").symlink_to(victim)
            write_private(out, "Transaction Date\n")
            self.assertEqual(_mode(d), 0o700)
            self.assertEqual(_mode(out), 0o600)
            self.assertEqual(out.read_text(), "Transaction Date\n")
            self.assertEqual(victim.read_text(), "keep")
            self.assertFalse((d / "questrade_2026.csv.part").exists())

    def test_token_part_is_created_fresh_never_through_a_symlink(self):
        from taxjson.bin.taxjson_run import _questrade_token_write
        with tempfile.TemporaryDirectory() as tmp:
            tok = Path(tmp) / ".questrade_token"
            victim = Path(tmp) / "victim"
            victim.write_text("keep")
            (Path(tmp) / ".questrade_token.part").symlink_to(victim)
            _questrade_token_write(tok, "NEWTOKEN")
            self.assertEqual(tok.read_text(), "NEWTOKEN\n")
            self.assertEqual(_mode(tok), 0o600)
            self.assertEqual(victim.read_text(), "keep")


class TestQuestradeCredentialHandling(unittest.TestCase):
    def test_api_server_pinned_to_questrade(self):
        from taxjson.bin.taxjson_fetch import qt_api_server_ok, qt_refresh, _qt_get
        for ok in ("https://api01.iq.questrade.com/", "https://api05.iq.questrade.com",
                   "https://questrade.com/", "https://API01.IQ.QUESTRADE.COM:443/"):
            self.assertTrue(qt_api_server_ok(ok), ok)
        for bad in ("http://api01.iq.questrade.com/", "https://evilquestrade.com/",
                    "https://api01.iq.questrade.com.evil.example/",
                    "https://u" + chr(64) + "api01.iq.questrade.com/",   # userinfo
                    "https://api01.iq.questrade.com:8443/", "https://[::1]/",
                    "ftp://api01.iq.questrade.com/", "not a url"):
            self.assertFalse(qt_api_server_ok(bad), bad)

        def login(url):
            return json.dumps({"api_server": "https://api.example/",
                               "access_token": "AT", "refresh_token": "RT"}).encode()
        with self.assertRaises(RuntimeError) as cm:
            qt_refresh("OLD", login)
        self.assertIn("questrade.com", str(cm.exception))
        sent = []
        with self.assertRaises(RuntimeError):
            _qt_get("https://api.example/", "AT", "/v1/accounts",
                    lambda u: sent.append(u) or b"{}")
        self.assertEqual(sent, [])                 # nothing left the machine

    def test_live_holdings_toml_escapes_broker_strings(self):
        from taxjson.bin.taxjson_fetch import positions_to_holdings_toml, toml_str
        evil = 'X"\n[[holding]]\nsymbol = "PWN'
        text = positions_to_holdings_toml(
            [{"symbol": evil, "openQuantity": 5}], 'acct"x', "123\\",
            "2026-01-01 00:00:00")
        doc = tomllib.loads(text)
        self.assertEqual(len(doc["holding"]), 1)
        self.assertEqual(doc["meta"]["account"], 'acct"x')
        self.assertEqual(doc["meta"]["broker_account"], "123\\")
        self.assertTrue(doc["holding"][0]["symbol"].startswith('X"\n[[holding]]'))
        for ch in ("\x00", "\x1f", "\x7f", "é", "\t"):
            self.assertEqual(tomllib.loads(f"k = {toml_str('a' + ch)}")["k"], "a" + ch)


if __name__ == "__main__":
    unittest.main()
