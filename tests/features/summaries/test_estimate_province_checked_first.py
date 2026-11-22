"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene.

Formerly: test_cli_audit_2026_09"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from taxjson.cli import estimate as cli_estimate
from _style import CapturedWidth
import _paths
from _support.filing_run_record import record_full_run

REPO_ROOT = _paths.REPO


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()



def _tj(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


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


class TestEstimateProvinceFirst(unittest.TestCase):  # B20
    """Formerly: test_cli_audit_2026_09.TestEstimateProvinceFirst"""
    def test_missing_province_fails_before_any_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            code, out, _e = _call(cli_estimate.cmd_summary, dir=str(root),
                                  account=None, json=False, estimate=True,
                                  other_income=None, other_losses=None,
                                  province=None, verbose=False)
        self.assertIn("needs a province", str(code))
        self.assertEqual(out, "")

    def test_unsupported_province_fails_before_any_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            code, out, _e = _call(cli_estimate.cmd_summary, dir=str(root),
                                  account=None, json=False, estimate=True,
                                  other_income=None, other_losses=None,
                                  province="QC", verbose=False)
        self.assertIn("unsupported province 'QC'", str(code))
        self.assertEqual(out, "")

    def test_estimate_error_names_the_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_TOML.format(year=2024))
            r = _tj(root, "estimate")
        # docs/output-style.md: `tjs <cmd>: error: <headline>`.
        self.assertIn("tjs estimate: error: no gains files", r.stderr)

    def test_province_without_estimate_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            record_full_run(root)       # (sum reads books a full run recorded)
            _c, _o, err = _call(cli_estimate.cmd_summary, dir=str(root), account=None,
                                json=False, province="ON")
        self.assertIn("--province is ignored", err)


if __name__ == "__main__":
    unittest.main()
