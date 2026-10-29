"""2026-10 workflow audit: the fixes the audit's confirmed findings asked
for (CHANGELOG "Fixed"): interest paid shown apart, a negative futures
price in a generic CSV, the audit over a pending election, the
checklist's hints, init's timezone, the single-folder year hints,
sanity with no holdings file, the headless election message, no empty
manifest.json, compare's exit codes (tests/test_compare.py)."""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from _qa_project import console, project, tj
from test_fix_a2_generic import _HDR, _parse, _toml
from test_fix_a2_pins2_corp import _SPIN, _ca, _ib_files

REPO_ROOT = Path(__file__).resolve().parent.parent

QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")
QT_ROWS = (QT_HEAD
           + "2024-01-02,2024-01-04,Buy,ZZQ,ZZQ CORP,100,50.00,-5000.00,0,"
             "-5000.00,USD,Trades,55500001,Margin\n"            # pii-ok
           + "2024-03-01,2024-03-05,Sell,ZZQ,ZZQ CORP,-100,60.00,6000.00,0,"
             "6000.00,USD,Trades,55500001,Margin\n")           # pii-ok


class TestInterestPaid(unittest.TestCase):
    def test_paid_is_its_own_line_and_out_of_the_total(self):
        from taxjson.bin.taxjson_sum_income import (format_report,
                                                    summarize_income)
        txs = [{"action": "INTEREST", "date": "2025-01-15", "symbol": "CASH",
                "currency": "CAD", "net_amount": 25.50},
               {"action": "INTEREST", "date": "2025-02-15", "symbol": "CASH",
                "currency": "CAD", "net_amount": -140.25}]
        rep = summarize_income(txs, target_year=2025)
        self.assertEqual(rep["interest_totals"], {"CAD": 25.5})
        self.assertEqual(rep["interest_paid_totals"], {"CAD": 140.25})
        out = format_report(rep)
        lines = {ln.split("  ")[0].strip(): ln for ln in out.splitlines()}
        self.assertIn("25.50", lines["CASH INTEREST"])
        self.assertIn("140.25", lines["INTEREST PAID (not in total)"])
        self.assertIn("25.50", lines["TOTAL NET INCOME"])
        self.assertNotIn("-114.75", out)

    def test_no_paid_line_without_interest_charged(self):
        from taxjson.bin.taxjson_sum_income import (format_report,
                                                    summarize_income)
        rep = summarize_income([{"action": "INTEREST", "date": "2025-01-15",
                                 "symbol": "CASH", "currency": "USD",
                                 "net_amount": 10.0}], target_year=2025)
        self.assertNotIn("INTEREST PAID", format_report(rep))


class TestNegativeFuturesPrice(unittest.TestCase):
    def test_generic_keeps_the_notional_sign(self):
        rows = (_HDR + "2020-04-20,BUY,F:CLK0,1,-37.63,37620.5,9.5,USD\n"
                + "2020-04-21,SELL,F:CLK0,-1,10.01,10000.5,9.5,USD\n")
        txs, _, _ = _parse(rows, _toml())
        self.assertLess(txs[0]["net_amount"], 0)          # the buy received
        self.assertAlmostEqual(txs[0]["net_amount"], -37620.5)
        self.assertGreater(txs[1]["net_amount"], 0)
        # A positive price is booked as before.
        rows = (_HDR + "2025-12-01,BUY,F:ESZ5,1,6000,-300009.5,9.5,USD\n")
        txs, _, _ = _parse(rows, _toml())
        self.assertAlmostEqual(txs[0]["net_amount"], 300009.5)


class TestHeadlessElectionMessage(unittest.TestCase):
    def _run(self, tmp, *flags):
        a, b = _ib_files(tmp, [_ca(_SPIN, 20, 400)], [])
        pend = Path(tmp) / "pending.json"
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
             "--country", "canada", "--brokerage", "ib",
             "--manifest", str(Path(tmp) / "manifest.json"),
             *flags, "--pending-json", str(pend), str(a), str(b)],
            cwd=REPO_ROOT, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=120)

    def test_says_which_kept_it_from_asking(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("need an election — not asked: --no-input", r.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("need an election — not asked: no terminal to "
                          "answer on", r.stderr)


class TestSystemTimezone(unittest.TestCase):
    def test_a_real_localtime_link_wins_over_a_utc_timezone_file(self):
        from taxjson.lib import config_template as CT
        env = {k: v for k, v in os.environ.items() if k != "TZ"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("builtins.open",
                           mock.mock_open(read_data="Etc/UTC\n")), \
                mock.patch("os.readlink",
                           return_value="/usr/share/zoneinfo/America/Toronto"):
            self.assertEqual(CT.system_timezone(), "America/Toronto")
        with mock.patch.dict(os.environ, dict(env, TZ="UTC"), clear=True), \
                mock.patch("os.readlink",
                           return_value="/usr/share/zoneinfo/America/Toronto"):
            self.assertIsNone(CT.system_timezone())


class TestSingleFolderProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "single",
                           {"margin/questrade_2024.csv": QT_ROWS})
        cls.r = tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_years_and_new_year_point_at_migrate(self):
        r = tj(self.root, "years")
        self.assertIn("single-folder project", r.stdout)
        self.assertIn("tjs migrate --to-years", r.stdout)
        self.assertNotIn("tjs new-year", r.stdout)
        r = tj(self.root, "years", "--details")
        self.assertIn("migrate --to-years", r.stdout)
        r = tj(self.root, "new-year", "2025", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("single-folder project", r.stderr)
        self.assertIn("migrate --to-years", r.stderr)

    def test_no_empty_manifest_is_written(self):
        self.assertFalse((self.root / "inputs" / "margin"
                          / "manifest.json").exists())
        self.assertEqual(self.r.returncode, 0)

    def test_sanity_without_holdings_names_the_unchecked(self):
        r = tj(self.root, "sanity", check=False)
        self.assertEqual(r.returncode, 0, console(r))
        # The books hold no open position: none is unchecked (positions,
        # as the compare counts them), and a single-folder project has
        # no holdings/README.txt to point at (backlog TJ-0135).
        self.assertIn("No account holds a position in the books, and no "
                      "holdings file — nothing to compare with", r.stdout)
        self.assertNotIn("README", r.stdout)

    def test_audit_is_blocked_on_a_pending_election(self):
        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        pend = self.root / "work" / "margin_pending_elections.json"
        pend.write_text('{"pending": []}')
        try:
            ctx = cl.Ctx(root=self.root, cfg=cfg, year=2024,
                         today=date(2025, 3, 1),
                         run_sub=lambda argv, timeout=900: (0, "", ""))
            res = cl.d_audit(ctx)
            self.assertEqual(res.status, "blocked", res.detail)
            self.assertIn("corp-action elections pending for margin", res.detail)
            r = tj(self.root, "audit", "--summary", check=False)
            self.assertIn("corp-action elections pending for margin",
                          console(r))
            self.assertNotIn("MISMATCHED", r.stdout.split("tie-out")[-1][:40]
                             if "tie-out" in r.stdout else "")
        finally:
            pend.unlink()


class TestChecklistHints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.top = Path(cls.tmp.name) / "demo"
        r = tj(cls.top, "init", "--demo", str(cls.top))
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_next_follows_the_blocked_step_then_names_the_shared_folder(self):
        year = self.top / "2024"
        r = tj(year, "checklist", "--run", "--only", "inputs-frozen", check=False)
        text = " ".join(r.stdout.split())
        self.assertIn("Next (step 4, inputs-frozen): no books yet — "
                      "run `taxjson run`", text)
        self.assertNotIn("download the exports", text)
        tj(year, "run", "--no-input")
        r = tj(year, "checklist", "--run", "--only", "inputs-frozen", check=False)
        text = " ".join(r.stdout.split())
        self.assertIn("download the exports into ../inputs/<account>/", text)

    def test_configure_names_the_file_not_migrate(self):
        root = project(self.tmp.name, "crypto",
                       {"margin/questrade_2024.csv": QT_ROWS},
                       extra_accounts='[accounts.cb]\ntype = "taxable"\n'
                                      'crypto = true\n')
        toml = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(
            "\n".join(ln for ln in toml.splitlines()
                      if not ln.startswith("local_timezone")) + "\n")
        r = tj(root, "checklist", "--run", check=False)
        line, = [ln for ln in r.stdout.splitlines()
                 if ln.startswith("! 3 configure:")]
        self.assertTrue(line.endswith("— edit taxjson.toml"), line)
        self.assertNotIn("tjs migrate", line)


if __name__ == "__main__":
    unittest.main()
