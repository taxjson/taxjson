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
from taxjson.cli import common as cli_common
from taxjson.cli import estimate as cli_estimate
from taxjson.cli import file as cli_file
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


class TestWrongYearLock(unittest.TestCase):          # B2
    """Formerly: test_cli_audit_2026_09.TestWrongYearLock"""
    def test_close_year_refuses_books_of_another_year(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, year=2025, gains_year="2024")
            code, _o, _e = _call(cli_file.cmd_close_year, dir=str(root),
                                 year=None, force=False)
            self.assertIn("rebuild with `tjs run` first", str(code).lower())
            self.assertIn("margin: 2024", str(code))
            self.assertFalse((root / "filed").exists())

    def test_sum_refuses_books_of_another_year(self):
        # It warned and printed them. Books no full run built from this
        # taxjson.toml (its year) are refused, as close-year refuses them
        # (TJ-4347).
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, year=2025, gains_year="2024")
            code, out, err = _call(cli_estimate.cmd_summary, dir=str(root),
                                   account=None, json=False)
            self.assertIn("no full `tjs run` recorded", str(code))
            self.assertEqual(out, "")

    def test_matching_year_is_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            self.assertEqual(cli_common._artifact_year_mismatch(
                {"margin": root / "work" / "margin_gains.json"}, 2024), {})


if __name__ == "__main__":
    unittest.main()
