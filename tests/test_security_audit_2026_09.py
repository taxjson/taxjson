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


try:
    from fastapi.testclient import TestClient
    _HAVE_WEB = True
except Exception:
    _HAVE_WEB = False


@unittest.skipUnless(_HAVE_WEB, "web extra not installed")
class TestServeToken(unittest.TestCase):
    def _app(self, tmp, **kw):
        from taxjson.web.app import create_app
        from taxjson.web.context import ProjectContext
        root = Path(tmp)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        return create_app(ProjectContext.load(root), **kw)

    def test_non_loopback_requires_token_then_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            import secrets
            tok = secrets.token_urlsafe(16)
            app = self._app(tmp, allowed_hosts=["*"], auth_token=tok)
            c = TestClient(app)
            h = {"host": "192.168.1.5:8765"}
            self.assertEqual(c.get("/healthz", headers=h).status_code, 401)
            self.assertEqual(c.get("/healthz?token=wrong", headers=h).status_code, 401)
            r = c.get(f"/healthz?token={tok}", headers=h)
            self.assertEqual(r.status_code, 200)
            self.assertIn("httponly", r.headers.get("set-cookie", "").lower())
            # The cookie alone now authenticates.
            self.assertEqual(c.get("/healthz", headers=h).status_code, 200)
            c2 = TestClient(app)
            self.assertEqual(c2.get("/api/holdings", headers=h).status_code, 401)

    def test_healthz_has_no_filesystem_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = TestClient(self._app(tmp))
            doc = c.get("/healthz").json()
            self.assertNotIn("root", doc)
            self.assertNotIn(str(Path(tmp).resolve()), json.dumps(doc))

    def test_serve_generates_token_only_off_loopback(self):
        from taxjson.web import server
        for h in ("127.0.0.1", "localhost", "::1", "127.0.0.2", "[::1]"):
            self.assertTrue(server.is_loopback(h), h)
        for h in ("0.0.0.0", "::", "192.168.1.5", "myhost.lan"):
            self.assertFalse(server.is_loopback(h), h)
        seen = {}

        def fake_create_app(ctx, allowed_hosts=None, auth_token=None):
            seen["token"] = auth_token
            return object()
        import taxjson.web.app as app_mod
        import uvicorn
        orig_create, orig_run = app_mod.create_app, uvicorn.run
        app_mod.create_app, uvicorn.run = fake_create_app, lambda *a, **k: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "taxjson.toml").write_text(
                    '[settings]\nyear = 2025\ncountry = "canada"\n')
                import contextlib
                import io
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    server.serve(tmp, host="0.0.0.0", port=1)
                self.assertTrue(seen["token"] and len(seen["token"]) >= 24)
                self.assertIn(f"?token={seen['token']}", err.getvalue())
                with contextlib.redirect_stderr(io.StringIO()):
                    server.serve(tmp, host="127.0.0.1", port=1)
                self.assertIsNone(seen["token"])
        finally:
            app_mod.create_app, uvicorn.run = orig_create, orig_run


class TestDependencyFloors(unittest.TestCase):
    def test_python_multipart_floor_past_cve_2024_53981(self):
        doc = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        extras = doc["project"]["optional-dependencies"]
        for group in ("web", "all"):
            spec = next(d for d in extras[group] if d.startswith("python-multipart"))
            self.assertEqual(spec.replace(" ", ""), "python-multipart>=0.0.18", group)


if __name__ == "__main__":
    unittest.main()
