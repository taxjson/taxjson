"""Filed-year lock: close-year snapshots + drift detection."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

_ROW = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,D,"
        "100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n"
        "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,D,"
        "-100,15.00,1500.00,0.00,1500.00,CAD,1,Trades,Individual\n")


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


def _project(tmp):
    root = Path(tmp)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT_HEADER + _ROW)
    return root


class TestFiledYearLock(unittest.TestCase):
    def test_close_check_drift_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            # Close the year.
            r = _run_cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr)
            snap_path = root / "filed" / "2025.json"
            self.assertTrue(snap_path.exists())
            snap = json.loads(snap_path.read_text())
            self.assertAlmostEqual(snap["totals"]["realized"], 500.0,
                                   places=2)
            # Clean check passes.
            r = _run_cli(root, "check-filed")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("filed 2025: OK", r.stdout)
            # The lock refuses accidental overwrite.
            r = _run_cli(root, "close-year")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--force", r.stderr)

            # Amend history: the 2025 sale grows by $200.
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER + _ROW.replace("15.00,1500.00,0.00,1500.00",
                                          "17.00,1700.00,0.00,1700.00"))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            # The full run auto-detects the drift (warn-only).
            self.assertIn("DRIFTED", r.stderr)
            self.assertIn("+200.00", r.stderr)
            # Explicit check exits 1.
            r = _run_cli(root, "check-filed")
            self.assertEqual(r.returncode, 1)
            # --strict makes the run abort on drift.
            r = _run_cli(root, "run", "--strict", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("filed year(s) drifted", r.stderr)
            # Refreshing the lock clears everything.
            r = _run_cli(root, "close-year", "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(root, "check-filed")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_snapshots_is_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "check-filed")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no filed/", r.stdout)




class TestGainPreservingDriftDetected(unittest.TestCase):
    """Round-five audit finding 2: two drift classes leave every
    pre-upgrade aggregate unchanged while moving ACTUALLY-FILED
    figures. The lock must catch both — and pre-upgrade snapshots
    without the new fields must stay clean (no schema-growth drift)."""

    def _doc(self, proceeds, cost, term):
        return {"transactions": [
            {"qty": -100, "gain": round(proceeds - cost, 2),
             "proceeds": proceeds, "cost": cost, "term": term,
             "account": "m", "date": "2025-05-01"}]}

    def test_proceeds_acb_shift_detected(self):
        from taxjson.bin.taxjson_filed import (aggregates_from_gains,
                                               diff_snapshot)
        filed = aggregates_from_gains(self._doc(9000.0, 10000.0, ""))
        # Same -1,000 gain, but proceeds/cost both shifted +500:
        # Schedule 3 13199 / 8949 (d)-(e) move.
        cur = aggregates_from_gains(self._doc(9500.0, 10500.0, ""))
        snap = {"accounts": {"m": filed}}
        lines = diff_snapshot(snap, {"m": cur})
        self.assertTrue(any("proceeds" in l for l in lines), lines)

    def test_term_flip_detected(self):
        from taxjson.bin.taxjson_filed import (aggregates_from_gains,
                                               diff_snapshot)
        filed = aggregates_from_gains(
            self._doc(12000.0, 10000.0, "LONG_TERM"))
        cur = aggregates_from_gains(
            self._doc(12000.0, 10000.0, "SHORT_TERM"))
        lines = diff_snapshot({"accounts": {"m": filed}}, {"m": cur})
        self.assertTrue(any("st_gain" in l or "lt_gain" in l
                            for l in lines), lines)

    def test_pre_upgrade_snapshot_stays_clean(self):
        from taxjson.bin.taxjson_filed import (aggregates_from_gains,
                                               diff_snapshot)
        cur = aggregates_from_gains(self._doc(9000.0, 10000.0, ""))
        old_lock = {k: cur[k] for k in ("realized", "disallowed",
                                        "dispositions", "income",
                                        "tainted")}
        lines = diff_snapshot({"accounts": {"m": old_lock}}, {"m": cur})
        self.assertEqual(lines, [])


class TestCryptoRecomputeWashPolicy(unittest.TestCase):
    """US crypto runs --taxable --no-wash in the pipeline (§1091 does
    not reach digital assets); the filed-year recompute previously
    omitted --no-wash, so any US crypto account with a wash-window
    loss showed nonzero 'disallowed' drift on every check."""

    def _capture(self, td, settings, no_wash=None):
        from taxjson.bin.taxjson_filed import recompute_year
        cache = Path(td)
        (cache / "crypto_base.json").write_text(
            json.dumps({"transactions": []}))
        seen = {}

        def fake_run(cmd, out):
            seen["cmd"] = list(cmd)
            Path(out).write_text(json.dumps(
                {"transactions": [], "summary": {}}))
        kw = {} if no_wash is None else {"no_wash": no_wash}
        recompute_year(cache, "crypto", 2025, settings, "", fake_run,
                       **kw)
        return seen["cmd"]

    def test_no_wash_threads_through(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = self._capture(td, {"country": "usa"}, no_wash=True)
        self.assertIn("--no-wash", cmd)

    def test_default_stays_wash_checked(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = self._capture(td, {"country": "canada"})
        self.assertNotIn("--no-wash", cmd)

    def test_recompute_accounts_applies_us_crypto_policy(self):
        from taxjson.bin.taxjson_filed import recompute_accounts
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "crypto_base.json").write_text(
                json.dumps({"transactions": []}))
            cmds = []

            def fake_run(cmd, out):
                cmds.append(list(cmd))
                Path(out).write_text(json.dumps(
                    {"transactions": [], "summary": {}}))
            recompute_accounts(cache, [], ["crypto"], 2025,
                               {"country": "usa"}, "", fake_run)
        self.assertEqual(len(cmds), 1)
        self.assertIn("--no-wash", cmds[0])


class TestSingleAccountRunNeverPrintsFiledOk(unittest.TestCase):
    """2026-09 audit: `run --account X` skipped the cross-account wash
    pass yet printed "filed YEAR: OK" from the very books that pass
    would have changed — a false all-clear the next full run
    contradicted with DRIFTED. Under --account the drift check must say
    "not checked". And `--account <sheltered>` must rebuild
    sheltered_base.json, or a later `wash-radar --account margin` read
    a sheltered book missing this run's buys."""

    _MARGIN = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,D,"
               "100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n"
               "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,D,"
               "-100,8.00,800.00,0.00,800.00,CAD,1,Trades,Individual\n")
    _RRSP_BEFORE = ("2025-01-10 09:30:00 AM,2025-01-13 12:00:00 AM,Buy,"
                    "ZZZ.TO,D,10,5.00,50.00,0.00,-50.00,CAD,2,Trades,"
                    "Individual\n")
    # An identical-property buy in the registered account 5 days after
    # the taxable loss: superficial loss, permanently denied.
    _RRSP_WINDOW_BUY = ("2025-06-25 09:30:00 AM,2025-06-26 12:00:00 AM,"
                        "Buy,XEI.TO,D,100,8.00,800.00,0.00,-800.00,CAD,2,"
                        "Trades,Individual\n")

    def test_account_run_says_not_checked_and_rebuilds_sheltered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "rrsp").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER + self._MARGIN)
            rrsp_csv = root / "inputs" / "rrsp" / "questrade.csv"
            rrsp_csv.write_text(_QT_HEADER + self._RRSP_BEFORE)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr)
            snap = json.loads((root / "filed" / "2025.json").read_text())
            self.assertAlmostEqual(snap["totals"]["realized"], -200.0,
                                   places=2)

            # The in-window sheltered buy arrives; only rrsp is re-run.
            rrsp_csv.write_text(_QT_HEADER + self._RRSP_BEFORE
                                + self._RRSP_WINDOW_BUY)
            r = _run_cli(root, "run", "--no-input", "--account", "rrsp")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("filed 2025: OK", r.stdout + r.stderr,
                             "single-account run must not certify a "
                             "filed year it cannot recompute")
            self.assertIn("filed 2025: not checked", r.stdout)
            self.assertIn("run without --account", r.stdout)
            # sheltered_base.json carries this run's buy, so a radar
            # run next reads a current sheltered book.
            sb = json.loads((root / "work" / "sheltered_base.json")
                            .read_text())
            self.assertTrue(
                any(t.get("symbol") == "XEI.TO"
                    and t.get("date", "").startswith("2025-06-25")
                    for t in sb["transactions"]),
                "sheltered_base.json was not rebuilt under --account rrsp")

            # The full run is the one that sees the drift.
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("DRIFTED", r.stderr)


if __name__ == "__main__":
    unittest.main()
