"""End-to-end `taxjson run` on a scaffolded project.

The 2026-07b audit found cmd_run itself — config→stage wiring, stage
ordering, cache markers, the wash second pass, artifact layout — was
never executed by any test (every stage is unit-tested individually).
This drives the real subcommand over a real broker CSV, offline
(`source_currencies = []`, all-CAD data → no FX fetch), and pins the
artifacts and one known gain number.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"

[accounts.rrsp]
type = "sheltered"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

# Buy 100 @ 10.00 (comm 9.95) → ACB 1009.95; sell 100 @ 15.00 (comm 9.95)
# → proceeds 1490.05; gain 480.10.
_MARGIN_CSV = _QT_HEADER + (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "100,10.00,1000.00,9.95,-1009.95,CAD,12345,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,ISHARES COMP,"
    "-100,15.00,1500.00,9.95,1490.05,CAD,12345,Trades,Individual\n")

_RRSP_CSV = _QT_HEADER + (
    "2025-02-03 09:30:00 AM,2025-02-04 12:00:00 AM,Buy,ZAG.TO,BMO AGG BOND,"
    "50,14.00,700.00,9.95,-709.95,CAD,67890,Trades,Individual\n")


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


class TestRunEndToEnd(unittest.TestCase):
    def _project(self, tmp):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(_CONFIG)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "rrsp").mkdir(parents=True)
        (root / "inputs" / "margin" / "questrade.csv").write_text(_MARGIN_CSV)
        (root / "inputs" / "rrsp" / "questrade.csv").write_text(_RRSP_CSV)
        return root

    def test_commission_free_project_runs_clean(self):
        # FUZZ-2026-07 #H (found by four agents independently): a
        # project with ZERO trading fees — every commission-free
        # broker — crashed the whole run with a raw traceback because
        # taxjson-fees returned 1 on "no fees" and the fees stage
        # treats nonzero as failure. No-data is success.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            for acct in ("margin", "rrsp"):
                csv = root / "inputs" / acct / "questrade.csv"
                csv.write_text(csv.read_text().replace(",9.95,", ",0.00,")
                               .replace("-1009.95", "-1000.00")
                               .replace("1490.05", "1500.00")
                               .replace("-709.95", "-700.00"))
            r = _run_cli(root, "run")
            rpt = (root / "reports" / "fees.rpt").read_text()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("No trading fees", rpt)     # honest empty report

    def test_full_run_produces_artifacts_and_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run_cli(root, "run")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

            # Artifact layout: per-account gains, the cross-account wash
            # pass (taxable equity account + sheltered book present),
            # the machine twin, and the reports.
            work, reports = root / "work", root / "reports"
            for p in ("margin_base.json", "margin_gains.json",
                      "margin_gains_wash.json", "margin_report.json",
                      "rrsp_base.json", "rrsp_gains.json",
                      "sheltered_base.json"):
                self.assertTrue((work / p).exists(), p)
            for p in ("margin.sum", "margin_wash.sum", "rrsp.sum",
                      "margin_holdings.toml"):
                self.assertTrue((reports / p).exists(), p)
            self.assertTrue(
                (reports / "wash_radar_margin.rpt").exists()
                or (reports / "wash_radar_margin.json").exists(),
                "wash radar report missing")

            # The known number: 1490.05 − 1009.95 = 480.10, in both the
            # gains JSON and the rendered .sum.
            gains = json.loads((work / "margin_gains.json").read_text())
            self.assertAlmostEqual(
                float(gains["summary"]["total_gain"]), 480.10, places=2)
            self.assertIn("480.10", (reports / "margin.sum").read_text())

            # The machine twin was rebuilt from the wash pass — same
            # basis the query commands resolve to.
            rep = json.loads((work / "margin_report.json").read_text())
            self.assertEqual(rep["schema_version"], 1)
            self.assertEqual(rep["account"], "margin")

            # Query commands work off the artifacts and name their basis.
            s = _run_cli(root, "sum")
            self.assertEqual(s.returncode, 0, s.stderr)
            self.assertIn("basis: wash-adjusted", s.stdout)
            self.assertIn("480.10", s.stdout)

    def test_run_ends_with_holdings_sanity_when_configured(self):
        """`holdings = [...]` on an account makes `run` finish with the
        broker-positions cross-check — a warning on mismatch, never a
        failing exit (same-day trades not yet in the CSVs differ
        routinely)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            ext = root / "ext"
            ext.mkdir()
            (ext / "rrsp_pos.toml").write_text(
                'schema_version = "1.0"\n[meta]\naccount = "67890"\n\n'
                '[[holding]]\nsymbol = "ZAG.TO"\nquantity = 50\n'
                'asset_type = "equity"\n\n')
            (root / "taxjson.toml").write_text(
                _CONFIG + 'holdings = ["ext/rrsp_pos.toml"]\n')
            r = _run_cli(root, "run")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("==> holdings sanity", r.stdout)
            self.assertIn("accounts: rrsp", r.stdout)
            self.assertIn("OK: tickers and quantities agree", r.stdout)
            self.assertNotIn("positions differ", r.stderr)
            # Drift the broker file: warning on stderr, exit still 0.
            (ext / "rrsp_pos.toml").write_text(
                'schema_version = "1.0"\n[meta]\naccount = "67890"\n\n'
                '[[holding]]\nsymbol = "ZAG.TO"\nquantity = 60\n'
                'asset_type = "equity"\n\n')
            r = _run_cli(root, "run")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("QTY_MISMATCH", r.stdout)
            self.assertIn("positions differ", r.stderr)
            # No `holdings` anywhere: no section at all.
            (root / "taxjson.toml").write_text(_CONFIG)
            r = _run_cli(root, "run")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertNotIn("holdings sanity", r.stdout)

    def test_second_run_rebuilds_by_default_and_is_idempotent(self):
        # Full rebuild is the DEFAULT (2026-07 flip): a plain re-run
        # must never serve cached artifacts — it re-parses everything
        # and lands on identical numbers.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            first = _run_cli(root, "run")
            self.assertEqual(first.returncode, 0, first.stderr)
            gains_before = (root / "work" / "margin_gains.json").read_text()
            second = _run_cli(root, "run")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("parse questrade", second.stdout)   # rebuilt
            # Idempotent: a no-change rebuild must not drift.
            self.assertEqual(
                gains_before,
                (root / "work" / "margin_gains.json").read_text())

    def test_fast_second_run_reuses_cache(self):
        # `--fast` opts into the mtime cache: unchanged stages are
        # skipped (the old default behaviour, now behind the flag).
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            first = _run_cli(root, "run")
            self.assertEqual(first.returncode, 0, first.stderr)
            gains_before = (root / "work" / "margin_gains.json").read_text()
            second = _run_cli(root, "run", "--fast")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertNotIn("parse questrade", second.stdout)  # cached
            self.assertEqual(
                gains_before,
                (root / "work" / "margin_gains.json").read_text())

    def test_force_flag_was_removed(self):
        # Full rebuild is simply the default; the development-era
        # `--force` spelling was purged pre-1.0 and must fail loudly
        # rather than be silently accepted as a no-op.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run_cli(root, "run", "--force")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--force", r.stderr)


if __name__ == "__main__":
    unittest.main()
