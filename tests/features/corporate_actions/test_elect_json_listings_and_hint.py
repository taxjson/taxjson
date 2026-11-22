"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene.

Formerly: test_cli_audit_2026_09"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _paths

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


class TestElect(unittest.TestCase):                  # B12 + polish
    """Formerly: test_cli_audit_2026_09.TestElect"""
    def _root(self, td):
        root = _project(td)
        return root

    def test_pending_json_when_nothing_pending(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "elect", "--pending", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout),
                         {"schema_version": 1, "accounts": {}})

    def test_account_listing_json(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "elect", "margin", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout),
                         {"schema_version": 1, "accounts": {}})

    def test_hint_without_set_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "elect", "margin", "--hint", "a=1")
        # A usage error: exit 2 (backlog TJ-0539).
        self.assertEqual(r.returncode, 2)
        self.assertIn("--hint only applies with --set", r.stderr)


if __name__ == "__main__":
    unittest.main()
