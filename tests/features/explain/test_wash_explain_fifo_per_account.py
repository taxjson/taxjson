"""Re-audit-2 partition fixes, lists partition-07 / partition-08 (area
partD), data half: IB Asia-Pacific trade dates, the generic importer's
crypto advice and futures prefixes, Coinbase stablecoin quotes in
property mode, Kraken ledger de-pegs, the country-ownership tables'
completeness check and the US explain trace's per-account FIFO.

Every fixture is SYNTHETIC: fake account ids (55500001 / U5550001),  # pii-ok
invented tickers (QZ*, XYZ, ZZS).

Formerly: test_fix_a2_partd_data
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO = _paths.REPO
PY = sys.executable


def _env(home=None, **extra):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", NO_COLOR="1")
    if home:
        env["HOME"] = str(home)
    env.update(extra)
    return env


# ----------------------------------------------------- wash-sales --explain
class TestExplainPerAccount(unittest.TestCase):
    """A2-0155 / A2-0318: the US trace of the merged taxable books keeps
    FIFO per account, like the table it explains.

    Formerly: test_fix_a2_partd_data.TestExplainPerAccount"""

    @rule(R.US_BASIS_01)
    def test_wash_sales_explain_matches_the_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            for a, rows in (("eq1", "BUYSELL 2024-06-03 10:00:00 XYZ.US 50 USD "
                                    "20 1000 0\n"
                                    "BUYSELL 2025-03-03 10:00:00 XYZ.US -50 USD "
                                    "15 750 0\n"),
                            ("eq2", "BUYSELL 2024-02-01 10:00:00 XYZ.US 50 USD "
                                    "30 1500 0\n"
                                    "BUYSELL 2025-03-10 10:00:00 XYZ.US 10 USD "
                                    "15 150 0\n")):
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / f"{a}.tt").write_text(rows)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\n[accounts.eq1]\ntype = "taxable"\n'
                '[accounts.eq2]\ntype = "taxable"\n')
            base = [PY, "-m", "taxjson.bin.taxjson_run", "-C", str(root)]
            r = subprocess.run(base + ["run", "--no-input"],
                               capture_output=True, text=True, env=_env(td),
                               stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            x = subprocess.run(base + ["wash-sales", "--explain"],
                               capture_output=True, text=True, env=_env(td),
                               stdin=subprocess.DEVNULL)
        self.assertIn("Against Lot: 2024-06-03", x.stdout, x.stderr)
        self.assertNotIn("Against Lot: 2024-02-01", x.stdout)


if __name__ == "__main__":
    unittest.main()
