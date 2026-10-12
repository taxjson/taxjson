"""work/<account>_report.json — the machine twin of the text reports.

Additive artifact: build_account_report's schema, and `taxjson summary`
parity (identical output whether it recomputes or reads the fresh
report.json)."""

import argparse
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from taxjson.lib.report_model import build_account_report

GAINS = {
    "summary": {"year": 2026, "total_gain": 1500.0},
    "transactions": [
        {"symbol": "AAPL.US", "date": "2026-05-02", "qty": -100,
         "currency": "CAD", "proceeds": 12000.0, "cost": 10500.0,
         "gain": 1500.0, "days_held": 200, "commission": 5.0, "fee": 0.0},
        {"action": "DIVIDEND", "symbol": "AAPL.US", "date": "2026-03-01",
         "currency": "CAD", "dividend": 55.0, "gain": 0.0, "qty": 0,
         "gross_amount": 55.0, "net_amount": 55.0, "type": "dividend"},
    ],
    "wash_sales": [{"symbol": "AAPL.US", "amount": 250.0}],
}


class TestBuildAccountReport(unittest.TestCase):
    def test_schema(self):
        rep = build_account_report(GAINS, "margin")
        self.assertEqual(rep["schema_version"], 1)
        self.assertEqual(rep["account"], "margin")
        self.assertEqual(rep["year"], 2026)
        self.assertIn("ticker_stats", rep["gains"])
        self.assertIn("AAPL.US", rep["gains"]["ticker_stats"])
        self.assertIsInstance(rep["income"], dict)
        self.assertEqual(rep["wash"]["count"], 1)
        self.assertAlmostEqual(rep["wash"]["total_disallowed"], 250.0)
        json.dumps(rep)                         # serializable end to end


class TestSummaryParity(unittest.TestCase):
    def _summary_output(self, root):
        from taxjson.bin.taxjson_run import cmd_summary
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cmd_summary(argparse.Namespace(dir=str(root)))
        return out.getvalue()

    def test_report_json_path_matches_recompute(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            work = root / "work"
            work.mkdir()
            gains_p = work / "margin_gains.json"
            gains_p.write_text(json.dumps(GAINS))

            baseline = self._summary_output(root)      # recompute path

            report_p = work / "margin_report.json"
            report_p.write_text(json.dumps(
                build_account_report(GAINS, "margin")))
            now = time.time()
            os.utime(gains_p, (now - 50, now - 50))
            os.utime(report_p, (now, now))             # fresh twin
            via_report = self._summary_output(root)

            self.assertEqual(baseline, via_report)

            # A STALE report.json (older than the gains file) is ignored.
            report_p.write_text(json.dumps({"schema_version": 1,
                                            "gains": {}, "year": 1999}))
            os.utime(report_p, (now - 100, now - 100))
            os.utime(gains_p, (now, now))
            self.assertEqual(baseline, self._summary_output(root))


if __name__ == "__main__":
    unittest.main()
