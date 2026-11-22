"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene.

Formerly: test_cli_audit_2026_09"""

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from taxjson.cli import books as cli_books
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()



def _call(fn, **kw):
    out, err = io.StringIO(), io.StringIO()
    code = None
    # A command run in this process (load_config) sets TAXJSON_PROJECT_ROOT,
    # TAXJSON_LOCAL_TZ and TAXJSON_TICKER_MAP in os.environ, which every
    # later test's `tjs` child would inherit: put the environment back.
    saved = dict(os.environ)
    with redirect_stdout(out), redirect_stderr(err):
        try:
            fn(argparse.Namespace(**kw))
        except SystemExit as e:
            code = e.code
        finally:
            os.environ.clear()
            os.environ.update(saved)
    return code, out.getvalue(), err.getvalue()


_TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         '[accounts.margin]\ntype = "taxable"\n'
         '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')

_GAINS = {"summary": {"year": "2024", "total_gain": 100.0},
          "transactions": [{"action": "BUYSELL", "symbol": "AAA.TO",
                            "date": "2024-03-01",
                            "date_settle": "2024-03-04",
                            "quantity": -10, "qty": 10, "gain": 100.0,
                            "proceeds": 1100.0, "cost": 1000.0,
                            "id": "abc", "account": "margin"}]}


def _project(td, year=2024, gains_year="2024"):
    root = Path(td)
    (root / "taxjson.toml").write_text(_TOML.format(year=year))
    (root / "work").mkdir()
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "crypto").mkdir(parents=True)
    g = json.loads(json.dumps(_GAINS))
    g["summary"]["year"] = gains_year
    (root / "work" / "margin_gains.json").write_text(json.dumps(g))
    return root


class TestFindMissingHistoryCountry(unittest.TestCase):   # B16
    """Formerly: test_cli_audit_2026_09.TestFindMissingHistoryCountry"""
    def test_write_missing_history_passes_country(self):
        seen = []

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake(cmd, **kw):
            seen.append(cmd)
            Path(cmd[cmd.index("--suggest-missing-history") + 1]
                 ).write_text("[]")
            return _P()

        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "taxjson.toml").write_text(
                _TOML.format(year=2024).replace('"canada"', '"us"')
                .replace('"CAD"', '"USD"'))
            (root / "work" / "margin_base.json").write_text("{}")
            with mock.patch("taxjson.pipeline.dispatch.run_cmd", fake):
                _call(cli_books.cmd_find_missing_history, dir=str(root),
                      account=None, year=None, include_options=False,
                      write_missing_history=str(Path(td) / "mh.json"),
                      all_history=False)
        self.assertTrue(seen)
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index("--country") + 1], "usa")
        self.assertEqual(cmd[cmd.index("--year") + 1], "2024")


if __name__ == "__main__":
    unittest.main()
