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
from taxjson.cli import explain as cli_explain
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


class TestAudit(unittest.TestCase):                  # B15
    """Formerly: test_cli_audit_2026_09.TestAudit"""
    def test_unique_prefix_len(self):
        from taxjson.bin.taxjson_audit import unique_prefix_len
        self.assertEqual(unique_prefix_len(["abcdefghij1", "zz"]), 10)
        self.assertEqual(unique_prefix_len(
            ["abcdefghijKL1", "abcdefghijKL2"]), 13)

    def test_unknown_account_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            code, _o, _e = _call(cli_explain.cmd_audit, dir=str(root), account="nope",
                                 year=None, all_years=False)
        self.assertIn("not a taxable account", str(code))


if __name__ == "__main__":
    unittest.main()
