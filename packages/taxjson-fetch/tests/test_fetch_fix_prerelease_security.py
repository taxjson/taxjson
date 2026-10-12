"""v0.17.0 pre-release security review pins (taxjson-fetch side).

L2  the Questrade account number from taxjson.toml is digits only before
    it goes into an API path (and is percent-quoted there).
L4  a login response whose access token holds CR/LF (or anything a bearer
    token cannot hold) is a one-line error that never prints the token.

Synthetic values only; HTTP is injected, nothing touches the network.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from taxjson_fetch import api as F

_SESSION = {"api_server": "https://api01.iq.questrade.com/",
            "access_token": "AbC-123_x.y~z+/=", "refresh_token": "r"}


class TestAccountNumberInPath(unittest.TestCase):
    BAD = ("5550/../../v1/accounts", "55500001?x=1", "5550 0001",  # pii-ok
           "55500001#f", "../55500001", "", "5550\r\n0001", "٥٥٥")

    def test_non_digit_numbers_never_reach_the_api(self):
        seen = []

        def http(url):
            seen.append(url)
            return b'{"activities": [], "positions": []}'

        for bad in self.BAD:
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError) as cm:
                    F.qt_activities(_SESSION, bad, date(2025, 1, 2),
                                    date(2025, 1, 3), http)
                self.assertIn("digits only", str(cm.exception))
                with self.assertRaises(RuntimeError):
                    F.qt_positions(_SESSION, bad, http)
                # never echoed in full
                if len(bad) > 2:
                    self.assertNotIn(bad, str(cm.exception))
        self.assertEqual(seen, [])

    def test_digit_number_is_used_quoted(self):
        seen = []

        def http(url):
            seen.append(url)
            return b'{"activities": [], "positions": []}'

        F.qt_activities(_SESSION, "55500001", date(2025, 1, 2),  # pii-ok
                        date(2025, 1, 3), http)
        F.qt_positions(_SESSION, "55500001", http)  # pii-ok
        self.assertEqual(len(seen), 2)
        self.assertIn("/v1/accounts/55500001/activities?", seen[0])  # pii-ok
        self.assertTrue(seen[1].endswith("/v1/accounts/55500001/positions"))  # pii-ok

    def test_command_refuses_before_login(self):
        # The config check happens before the refresh token is spent.
        from taxjson_fetch import command as C
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = {"settings": {"year": 2025, "country": "canada"},
                   "accounts": {"margin": {"type": "taxable",
                                           "brokerage": "questrade",
                                           "account": "5550-0001"}}}
            req = SimpleNamespace(
                args=SimpleNamespace(days=None, year=None, from_date=None,
                                     refresh_token="tok", positions=False,
                                     trim_overlap=False),
                root=root, work=root / "work", config=cfg,
                accounts=["margin"], say=lambda m: None, json=False,
                dry_run=False)
            # Never the real token file, whatever happens (a regression
            # would reach the token write after the mocked login).
            env = {"QUESTRADE_TOKEN_FILE": str(root / "qt_token"),
                   "QUESTRADE_REFRESH_TOKEN": "", "HOME": str(root)}
            with mock.patch.dict("os.environ", env), \
                    mock.patch.object(F, "qt_refresh") as login, \
                    mock.patch.object(F, "qt_activities") as acts, \
                    redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                C.run(req)
            login.assert_not_called()
            acts.assert_not_called()
        msg = str(cm.exception.code)
        self.assertIn("[accounts.margin]", msg)
        self.assertIn("digits only", msg)
        self.assertNotIn("5550-0001", msg)


class TestBearerTokenCharacters(unittest.TestCase):
    TOKEN = "SECRETtok3n"

    def _login(self, access):
        doc = {"api_server": "https://api01.iq.questrade.com/",
               "access_token": access, "refresh_token": "Refresh123",
               "expires_in": 1800}
        return F.qt_refresh("seed", http_get=lambda url: json.dumps(
            doc).encode())

    def test_cr_lf_in_the_access_token_is_one_clean_line(self):
        for bad in (self.TOKEN + "\r\nX-Evil: 1", self.TOKEN + "\n",
                    self.TOKEN + " x", "\x00" + self.TOKEN):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError) as cm:
                    self._login(bad)
                msg = str(cm.exception)
                self.assertNotIn(self.TOKEN, msg)
                self.assertEqual(len(msg.splitlines()), 1)
                self.assertIsNone(cm.exception.__cause__)
                self.assertIn("access token", msg)

    def test_a_bad_refresh_token_is_refused_too(self):
        doc = {"api_server": "https://api01.iq.questrade.com/",
               "access_token": "Good123", "refresh_token": "R\r\nX",
               "expires_in": 1800}
        with self.assertRaises(RuntimeError) as cm:
            F.qt_refresh("seed", http_get=lambda url: json.dumps(
                doc).encode())
        self.assertNotIn("R\r\nX", str(cm.exception))

    def test_ordinary_tokens_pass(self):
        s = self._login("AbC-123_x.y~z+/==")
        self.assertEqual(s["access_token"], "AbC-123_x.y~z+/==")

    def test_request_with_a_bad_session_token_is_refused_without_it(self):
        # A session built elsewhere: the header is never attempted.
        sess = dict(_SESSION, access_token=self.TOKEN + "\r\n")
        with mock.patch.object(F, "_opener") as op:
            with self.assertRaises(RuntimeError) as cm:
                F._qt_get(sess["api_server"], sess["access_token"],
                          "/v1/accounts/55500001/positions",  # pii-ok
                          F.default_http_get)
            op.open.assert_not_called()
        self.assertNotIn(self.TOKEN, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
