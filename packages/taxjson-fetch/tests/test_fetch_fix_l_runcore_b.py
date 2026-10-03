"""Moved from the core's tests/test_fix_l_runcore_b.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Regression pins for the run-core LOW findings, second half (2026-09
audit, runcore-b).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no network (a
stub yfinance stands in where a command would call Yahoo).
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import tempfile
import unittest
from pathlib import Path
from test_fix_l_runcore_a import (_QT_HEADER,  # noqa: E402
                                  )


class TestFetchAndWatch(unittest.TestCase):
    """S046-12 (watch --state paths), S046-16 (trim refuses a swallowed
    record), S046-17 (Questrade number masked)."""

    def test_trim_refuses_a_record_spanning_lines(self):
        from taxjson_fetch.command import _qt_trim_file
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "qt_manual.csv"
            body = (_QT_HEADER
                    + '2025-06-30 09:30:00 AM,2025-07-02 12:00:00 AM,Buy,'
                      'ABC.TO,"ABC CORP "",10,1,10,0,-10,CAD,55500001,'
                      'Trades,Individual\n'
                    + '2025-07-15 09:30:00 AM,2025-07-16 12:00:00 AM,Buy,'
                      'DEF.TO,DEF,10,1,10,0,-10,CAD,55500001,Trades,'
                      'Individual\n')
            f.write_text(body)
            with self.assertRaises(ValueError):
                _qt_trim_file(f, "2025-06-01", "2025-06-30")
            self.assertEqual(f.read_text(), body)
            self.assertFalse((Path(tmp) / "qt_manual.csv.bak").exists())

    def test_questrade_number_is_masked(self):
        import io
        import urllib.error
        from taxjson_fetch import api as F
        self.assertEqual(F.mask_account_number("59998888"), "59***")  # pii-ok

        def boom(url):
            raise urllib.error.HTTPError(url, 400, "Bad Request", {},
                                         io.BytesIO(b'{"code": 1}'))
        with self.assertRaises(RuntimeError) as cm:
            F._qt_get("https://api01.iq.questrade.com/", "tok",
                      "/v1/accounts/59998888/activities?startTime=x",  # pii-ok
                      boom)
        self.assertNotIn("59998888", str(cm.exception))
        self.assertIn("/v1/accounts/59***/activities", str(cm.exception))
        src = (_support.PLUGIN_SRC / "command.py").read_text()
        self.assertNotIn('questrade #{number}', src)


if __name__ == "__main__":
    unittest.main()
