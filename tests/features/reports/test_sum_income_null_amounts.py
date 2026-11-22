"""Fixes for the 2026-10-25 workflow audit, group parsers: the FX-rate
window scan (lib/rates_window), the income summer
(bin/taxjson_sum_income) and the crypto broker readers (Coinbase,
Kraken). One test per finding, each named for it. Synthetic data only.

Formerly: test_fix_audit_1025_parsers"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


def _tool(module, *args):
    """Run `taxjson.bin.<module>` as the CLI does (a child Python)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv(f"taxjson.bin.{module}", [str(a) for a in args]),
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=300)


class TestSumIncome(unittest.TestCase):
    """Formerly: test_fix_audit_1025_parsers.TestSumIncome"""
    def test_diff_tooling_3_null_amounts_are_refused_not_booked_as_zero(self):
        """diff-tooling#3: taxjson-sum-income books an income row with
        null amounts as $0 at exit 0 (the S051-14 'carry no amount'
        refusal is lost). load_income_data copied the funnel's
        normalized row over the file's, and for an explicit null
        gross_amount / net_amount that value is TaxTransaction's 0.0
        default. A null amount keeps the row's None, so the row is
        refused (exit 2) as on v0.34.0; a row with one amount null and
        the other given still sums the given one."""
        from taxjson.bin import taxjson_sum_income as si
        with tempfile.TemporaryDirectory() as td:
            for action, typ in (("DIVIDEND", "dividend"), ("TAX", "tax"),
                                ("INTEREST", "interest")):
                p = Path(td) / f"{action.lower()}.json"
                rows = [
                    {"action": "DIVIDEND", "type": "dividend",
                     "symbol": "ZZQX.US", "date": "2024-03-14",
                     "currency": "USD", "gross_amount": 50.0,
                     "net_amount": 42.5},
                    {"action": action, "type": typ, "symbol": "ZZQY.US",
                     "date": "2024-06-14", "currency": "USD",
                     "gross_amount": None, "net_amount": None}]
                p.write_text(json.dumps({"transactions": rows}))
                loaded = si.load_income_data(p)
                self.assertIsNone(loaded[1]["gross_amount"])
                self.assertIsNone(loaded[1]["net_amount"])
                r = _tool("taxjson_sum_income", p)
                self.assertEqual(r.returncode, 2, (action, r.stdout,
                                                   r.stderr))
                self.assertIn("1 income row(s) carry no amount", r.stderr)
                self.assertIn(f"{action} ZZQY.US 2024-06-14", r.stderr)
                self.assertNotIn("TOTAL NET INCOME", r.stdout)
            # One amount null, the other given: the given one is summed.
            p = Path(td) / "partial.json"
            p.write_text(json.dumps({"transactions": [
                {"action": "DIVIDEND", "type": "dividend",
                 "symbol": "ZZQY.US", "date": "2024-06-14",
                 "currency": "USD", "gross_amount": None,
                 "net_amount": 42.5}]}))
            r = _tool("taxjson_sum_income", p)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("42.50", r.stdout)


if __name__ == "__main__":
    unittest.main()
