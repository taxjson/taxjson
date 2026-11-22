"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene.

Formerly: test_cli_audit_2026_09"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from taxjson.cli import common as cli_common
from taxjson.cli import file as cli_file
from taxjson.pipeline import config as pipeline_config
from taxjson.pipeline import stages as pipeline_stages
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()



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


class TestInputsFolders(unittest.TestCase):          # B6 + subfolders
    """Formerly: test_cli_audit_2026_09.TestInputsFolders"""
    def _cfg(self):
        return {"settings": {"year": 2025, "country": "canada"},
                "accounts": {"margin": {"type": "taxable"}}}

    def test_slips_folder_is_not_an_orphan_account(self):
        with tempfile.TemporaryDirectory() as td:
            inputs = Path(td) / "inputs"
            (inputs / "slips").mkdir(parents=True)
            (inputs / "slips" / "t5008.csv").write_text("a,b\n")
            self.assertEqual(pipeline_config.validate_config(self._cfg(), inputs), [])

    def test_csv_in_account_subfolder_warns(self):
        with tempfile.TemporaryDirectory() as td:
            inputs = Path(td) / "inputs"
            (inputs / "margin" / "2025").mkdir(parents=True)
            (inputs / "margin" / "2025" / "q.csv").write_text("a,b\n")
            w = pipeline_config.validate_config(self._cfg(), inputs)
        self.assertTrue(any("inputs/margin/2025/" in m and "NOT read" in m
                            for m in w), w)


class TestSkippedAccounts(unittest.TestCase):        # B7
    """Formerly: test_cli_audit_2026_09.TestSkippedAccounts"""
    def setUp(self):
        # _taxable_gains_argv reads the config in this process (load_config),
        # which sets TAXJSON_* variables: put the environment back.
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))

    def test_record_and_read_back(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "crypto").mkdir(parents=True)
            (root / "inputs" / "rrsp").mkdir(parents=True)
            pipeline_stages._record_skipped_accounts(root / "work", ["crypto", "rrsp"])
            self.assertEqual(cli_common._accounts_skipped_for_no_inputs(root),
                             {"crypto", "rrsp"})
            # Files added since the run: the hint IS needed again.
            (root / "inputs" / "rrsp" / "x.csv").write_text("a\n")
            self.assertEqual(cli_common._accounts_skipped_for_no_inputs(root),
                             {"crypto"})
            # A single-account run only updates its own entry.
            pipeline_stages._record_skipped_accounts(root / "work", [], only="crypto")
            doc = json.loads((root / "work" / "skipped_accounts.json")
                             .read_text())
            self.assertEqual(doc["accounts"], ["rrsp"])

    def test_form_export_argv_quiet_for_skipped_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            pipeline_stages._record_skipped_accounts(root / "work", ["crypto"])
            err = io.StringIO()
            with redirect_stderr(err):
                argv = cli_file._taxable_gains_argv(root, root / "work")
            self.assertEqual(len(argv), 2)
            self.assertNotIn("crypto", err.getvalue())

    def test_unskipped_missing_account_still_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            err = io.StringIO()
            with redirect_stderr(err):
                cli_file._taxable_gains_argv(root, root / "work")
            self.assertIn("'crypto'", err.getvalue())


if __name__ == "__main__":
    unittest.main()
