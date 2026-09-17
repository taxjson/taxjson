"""2026-09 security audit pins: filesystem/argv-safe account names,
credentialed-request hardening, offline guard, CSV limit handling."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestAccountNameValidation(unittest.TestCase):
    def _cfg(self, name):
        return (f'[settings]\nyear = 2026\ncountry = "canada"\n'
                f'base_currency = "CAD"\nsource_currencies = []\n'
                f'[accounts."{name}"]\ntype = "taxable"\n')

    def _validate(self, name):
        from taxjson.bin.taxjson_run import load_config
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "taxjson.toml").write_text(self._cfg(name))
            with redirect_stderr(io.StringIO()):
                return load_config(Path(td))

    def test_traversal_and_flag_names_refused(self):
        for bad in ("../../escape", "-x", "--help", ".hidden", "a/b"):
            with self.assertRaises(SystemExit, msg=bad):
                self._validate(bad)

    def test_ordinary_names_accepted(self):
        for ok in ("margin", "rrsp2", "my-acct", "acct.v2", "_x"):
            self._validate(ok)


class TestFetchHardening(unittest.TestCase):
    def test_redirects_refused_on_credentialed_requests(self):
        from taxjson.bin.taxjson_fetch import _NoRedirect
        h = _NoRedirect()
        with self.assertRaises(urllib.error.HTTPError):
            h.redirect_request(
                urllib.request.Request("https://api.example/x"),
                None, 302, "Found", {}, "http://evil.example/")

    def test_non_https_api_server_refused(self):
        from taxjson.bin import taxjson_fetch as tf
        fake = {"api_server": "http://api.example/",
                "access_token": "t", "refresh_token": "r",
                "expires_in": 1800}
        import json
        with self.assertRaises(RuntimeError):
            tf.qt_refresh("refresh", http_get=lambda url: json.dumps(fake).encode())


class TestOfflineGuard(unittest.TestCase):
    def test_crypto_price_lookup_refuses_when_offline(self):
        from taxjson.bin import fill_crypto_prices as f
        os.environ["TAXJSON_OFFLINE"] = "1"
        try:
            with self.assertRaises(SystemExit):
                f.get_crypto_price("ZZZTEST", "2026-01-05")
        finally:
            os.environ.pop("TAXJSON_OFFLINE", None)


class TestCsvFieldLimit(unittest.TestCase):
    def test_oversized_field_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "wb.csv"
            p.write_text("Symbol,Side,Status,Filled,Total Qty,Price,"
                         "Avg Price,Time-in-Force,Placed Time,"
                         "Filled Time,Description\n"
                         "AAPL,Buy,Filled,1,1,1,1,DAY,"
                         "09/01/2026 09:30:00 EDT,"
                         "09/01/2026 09:30:00 EDT," + "x" * 200000
                         + "\n", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "webull", str(p)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertNotIn("Traceback", r.stderr)
        if r.returncode != 0:
            self.assertIn("field", r.stderr.lower())


if __name__ == "__main__":
    unittest.main()
