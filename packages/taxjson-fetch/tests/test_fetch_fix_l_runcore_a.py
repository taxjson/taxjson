"""Moved from the core's tests/test_fix_l_runcore_a.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Regression pins for the run-core LOW findings, first half (2026-09
audit, runcore-a).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no FX fetch.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import os
import tempfile
import unittest
from pathlib import Path


class TestScanSanityFetch(unittest.TestCase):
    """R1-353 (Questrade auth hint)."""

    def test_questrade_auth_hint_names_the_skipped_env_token(self):
        from taxjson_fetch.command import _qt_auth_hint
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "tok"
            cache.write_text("DEADOLD\n")
            old = os.environ.get("QUESTRADE_REFRESH_TOKEN")
            os.environ["QUESTRADE_REFRESH_TOKEN"] = "NEWENV"
            try:
                h = _qt_auth_hint("DEADOLD", cache)
            finally:
                if old is None:
                    os.environ.pop("QUESTRADE_REFRESH_TOKEN", None)
                else:
                    os.environ["QUESTRADE_REFRESH_TOKEN"] = old
            self.assertIn("NOT $QUESTRADE_REFRESH_TOKEN", h)
            self.assertIn("--refresh-token", h)
            self.assertNotIn("DEADOLD", h)
            self.assertNotIn("NEWENV", h)
            h = _qt_auth_hint("ARG", cache, explicit=True)
            self.assertIn("--refresh-token", h)


if __name__ == "__main__":
    unittest.main()
