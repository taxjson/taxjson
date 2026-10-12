"""Moved from the core's tests/test_security_hardening.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

2026-09 security audit pins: filesystem/argv-safe account names,
credentialed-request hardening, offline guard, CSV limit handling.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import unittest
import urllib.error


class TestFetchHardening(unittest.TestCase):
    def test_redirects_refused_on_credentialed_requests(self):
        from taxjson_fetch.api import _NoRedirect
        h = _NoRedirect()
        with self.assertRaises(urllib.error.HTTPError):
            h.redirect_request(
                urllib.request.Request("https://api.example/x"),
                None, 302, "Found", {}, "http://evil.example/")

    def test_non_https_api_server_refused(self):
        from taxjson_fetch import api as tf
        fake = {"api_server": "http://api.example/",
                "access_token": "t", "refresh_token": "r",
                "expires_in": 1800}
        import json
        with self.assertRaises(RuntimeError):
            tf.qt_refresh("refresh", http_get=lambda url: json.dumps(fake).encode())


if __name__ == "__main__":
    unittest.main()
