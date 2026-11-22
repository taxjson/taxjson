"""Audit 2026-10-24, group "reports-state": the recorded wash inputs
and a new account (D01), the T1135 cost walk and an assignment that
covers a short (D02), a damaged work/cross_listings.state read as "no
pending journals" (D05), an incomplete work/<acct>_report.json served
by `sum` (D07), and `migrate` sharing a tobase.map that is not UTF-8
text (A42). One test per finding, each the audit's probe.

Every fixture is synthetic: made-up tickers (ZZQ.US), account names
(a, b, c, margin, tfsa), no ids, no amounts of 1,000 or more with cents.

Formerly: test_fix_audit_1024_reports
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths
from _support.filing_run_record import record_full_run

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs -C root args...` from this worktree's sources (the
    audit's CLI probes; python_module_argv, never `-m` from the cwd)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=180)


def _project(root: Path) -> None:
    """A minimal Canadian project with an empty work/ (the probes')."""
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        '[accounts.margin]\ntype = "taxable"\n')


def _older(p: Path, than: Path, seconds: float) -> None:
    t = than.stat().st_mtime + seconds
    os.utime(p, (t, t))


class TestD07IncompleteSummaryCache(unittest.TestCase):
    """Formerly: test_fix_audit_1024_reports.TestD07IncompleteSummaryCache"""

    GAINS = {"summary": {"year": 2026}, "transactions": [
        {"symbol": "ZZQ.US", "date": "2026-01-02", "qty": 1, "cost": 10,
         "proceeds": 20, "gain": 10, "currency": "CAD"}]}

    def _sum(self, root: Path):
        run = _cli(root, "sum", "--json")
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        return json.loads(run.stdout), run.stderr

    def test_d07_report_without_an_aggregate_is_recomputed_and_said(self):
        """D07: An incomplete summary cache overrides valid gains and
        makes one report contradict itself. A newer margin_report.json
        with the accepted version, account, year and basis but an empty
        gains object was served as the aggregate: `sum --json` reported
        totals.realized 0 beside filing.totals.gain 10. The fast path
        now requires the aggregate's fields (ticker_stats, total_fees);
        otherwise the row is recomputed from the gains file, with a
        Warning naming both files, and the two totals agree."""
        from taxjson.lib.gain_summary import summarize_gains
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            # The wash-adjusted gains a full run leaves, and its record:
            # `tjs sum` refuses books no full run recorded (TJ-4347).
            gains_p = root / "work" / "margin_gains_wash.json"
            report_p = root / "work" / "margin_report.json"
            gains_p.write_text(json.dumps(self.GAINS))
            record_full_run(root)
            head = {"schema_version": 1, "year": 2026, "account": "margin",
                    "basis": "wash-adjusted"}
            report_p.write_text(json.dumps(dict(head, gains={})))
            _older(report_p, gains_p, 5)
            doc, err = self._sum(root)
            self.assertEqual(doc["totals"]["realized"], 10.0)
            self.assertEqual(doc["filing"]["totals"]["gain"], 10.0)
            self.assertEqual(doc["accounts"][0]["realized"], 10.0)
            self.assertIn("work/margin_report.json holds no usable gains "
                          "aggregate", err)
            self.assertIn("recomputed from margin_gains_wash.json", err)
            # A gains object that is not an object at all: the same.
            report_p.write_text(json.dumps(dict(head, gains=[1, 2])))
            _older(report_p, gains_p, 5)
            doc, err = self._sum(root)
            self.assertEqual(doc["totals"]["realized"], 10.0)
            self.assertIn("recomputed from", err)
            # The run's complete aggregate is served, without the Warning
            # (an account with no trades has empty objects, not none).
            report_p.write_text(json.dumps(dict(
                head, gains=summarize_gains(self.GAINS))))
            _older(report_p, gains_p, 5)
            doc, err = self._sum(root)
            self.assertEqual(doc["totals"]["realized"], 10.0)
            self.assertEqual(doc["filing"]["totals"]["gain"], 10.0)
            self.assertNotIn("recomputed", err)
            self.assertNotIn("usable gains aggregate", err)


if __name__ == "__main__":
    unittest.main()
