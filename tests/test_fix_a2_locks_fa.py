"""Re-audit-2 filing-lock fixes, check-filed / audit side: `audit --year`
honours the locked year's lock (wherever it lives) and its date basis;
check-filed refuses a damaged lock instead of saying OK, says when the
project's settings differ from the lock's, and names the real problem
(a bad setting, a broken input, a damaged base book)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if action == "Buy" else gross
    return (f"{trade} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,55500001,"  # pii-ok
            f"Trades,Individual\n")


# XYZ bought in June, sold Dec 31 2025 (settles Jan 2 2026): a 2025
# disposition on trade dates, a 2026 one on settlement dates. ABC is a
# plain 2025 sale either way.
Y2025 = (_QT_HEADER
         + _qt("2025-06-02", "2025-06-03", "Buy", "XYZ.TO", 100, 10.0)
         + _qt("2025-12-31", "2026-01-02", "Sell", "XYZ.TO", -100, 20.0)
         + _qt("2025-03-03", "2025-03-04", "Buy", "ABC.TO", 50, 20.0)
         + _qt("2025-05-01", "2025-05-02", "Sell", "ABC.TO", -50, 30.0))

CALL_TT = ("BUYSELL 2025-12-10 09:30:00 ABC261218C00050000.TO -1 CAD "
           "4.0 400.0 0\n"
           "BUYSELL 2026-01-10 09:30:00 ABC261218C00050000.TO 1 CAD "
           "1.0 -100.0 0\n")


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600)


def _toml(year, extra="", accounts=("margin",)):
    return (f'[settings]\nyear = {year}\ncountry = "canada"\n'
            f'base_currency = "CAD"\nsource_currencies = []\n{extra}'
            + "".join(f'[accounts.{a}]\ntype = "taxable"\n'
                      for a in accounts))


def _project(root, year, files, extra="", accounts=("margin",)):
    root.mkdir(parents=True, exist_ok=True)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_toml(year, extra, accounts))
    for name, text in files.items():
        acct, _, fn = name.rpartition("/")
        (root / "inputs" / (acct or accounts[0]) / fn).write_text(text)
    return root


def _set_settings(root, year, extra="", accounts=("margin",)):
    (root / "taxjson.toml").write_text(_toml(year, extra, accounts))


class TestAuditLockedYear(unittest.TestCase):
    """A2-0334 / A2-1129: audit --year recomputes a locked year on the
    lock's date basis; A2-0335 / A2-0664: a lock reached through
    [settings] prior_year_record counts as the year's lock."""

    def test_audit_uses_the_locks_date_basis(self):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2026, {"q.csv": Y2025},
                         extra='tax_date = "trade"\n')
            r = _run_cli(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            (p / "filed").mkdir()
            (p / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "date_basis": "settle",
                 "country": "canada"}))
            r = _run_cli(p, "audit", "--year", "2025", "--json",
                         "--no-trace")
            j = json.loads(r.stdout)
            # Settle basis: only ABC (gain 500) is a 2025 disposition.
            self.assertEqual(j["total_gain"], 500.0, r.stderr)
            self.assertIn("settle", r.stderr)
            self.assertIn("2025 is locked", r.stderr)

    def test_audit_reads_the_prior_year_record_lock(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "p25" / "filed").mkdir(parents=True)
            (base / "p25" / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada",
                 "option_timing": {"option_premium_timing": "grant",
                                   "option_grant_since": 2025}}))
            p = _project(base / "p26", 2026, {"a.tt": CALL_TT},
                         extra='prior_year_record = '
                               '"../p25/filed/2025.json"\n')
            r = _run_cli(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(p, "audit", "--year", "2025", "--json",
                         "--no-trace")
            self.assertIn("2025 is locked", r.stderr)
            self.assertIn("p25", r.stderr)
            self.assertEqual(json.loads(r.stdout)["total_gain"], 400.0)


class _Closed(unittest.TestCase):
    """A closed 2025 project (settle basis) to damage per test."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls.tmp.name) / "p25", 2025,
                         {"q.csv": Y2025})
        r = _run_cli(cls.p, "run", "--no-input")
        assert r.returncode == 0, r.stderr
        r = _run_cli(cls.p, "close-year")
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _copy(self, td):
        q = Path(td) / "p"
        shutil.copytree(self.p, q)
        return q

    def _edit_lock(self, q, fn):
        lp = q / "filed" / "2025.json"
        doc = json.loads(lp.read_text())
        fn(doc)
        lp.write_text(json.dumps(doc))


class TestDamagedLock(_Closed):
    """A2-0347 / A2-0668: a version-2 lock whose account entry records
    none of the locked totals, or whose form_lines is not a table, is
    damaged — never 'OK (matches)'."""

    def test_empty_account_entry_is_damaged(self):
        for bad in ({}, {"realized_typo": 1.0}):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as td:
                q = self._copy(td)
                self._edit_lock(q, lambda d: d["accounts"].update(
                    margin=bad))
                r = _run_cli(q, "check-filed")
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertNotIn("OK (matches", r.stdout)
                self.assertIn("damaged", r.stderr)

    def test_form_lines_not_a_table_is_damaged(self):
        for bad in ("x", None, [1]):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as td:
                q = self._copy(td)
                self._edit_lock(q, lambda d: d["accounts"]["margin"]
                                .update(form_lines=bad))
                r = _run_cli(q, "check-filed")
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertNotIn("OK (matches", r.stdout)
                self.assertIn("form_lines", r.stderr)


class TestSettingsDifferFromLock(_Closed):
    """A2-0348 / A2-0672: the lock is recomputed under the settings it
    recorded — say so when the project's now differ."""

    def test_tax_date_change_is_noted(self):
        with tempfile.TemporaryDirectory() as td:
            q = self._copy(td)
            _set_settings(q, 2025, 'tax_date = "trade"\n')
            r = _run_cli(q, "check-filed")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = r.stdout + r.stderr
            self.assertIn("OK", out)
            self.assertIn("date basis", out)
            self.assertIn("settle", out)

    def test_buyback_flag_change_is_noted(self):
        with tempfile.TemporaryDirectory() as td:
            q = self._copy(td)
            _set_settings(q, 2025,
                          "option_buyback_loss_superficial = true\n")
            r = _run_cli(q, "check-filed")
            out = r.stdout + r.stderr
            self.assertIn("option_buyback_loss_superficial", out)

    def test_unchanged_settings_print_no_note(self):
        with tempfile.TemporaryDirectory() as td:
            q = self._copy(td)
            r = _run_cli(q, "check-filed")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("note:", r.stdout + r.stderr)


class TestCheckFiledErrors(_Closed):
    def test_bad_setting_is_not_called_a_damaged_lock(self):
        # A2-1134
        with tempfile.TemporaryDirectory() as td:
            q = self._copy(td)
            _set_settings(q, 2025, "corporate_distributions = 5\n")
            r = _run_cli(q, "check-filed")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("corporate_distributions", r.stderr)
            self.assertNotIn("restore the lock", r.stderr)

    def test_child_failure_is_one_line_not_a_repr(self):
        # A2-0676
        with tempfile.TemporaryDirectory() as td:
            q = self._copy(td)
            # (a malformed missing-history line the recompute reads)
            acct = sorted(p for p in (q / "inputs").iterdir()
                          if p.is_dir() and p.name != "slips")[0]
            (acct / "missing_history.tt").write_text(
                "OPENING 2025-01-01 QZQ.TO 0 cost=unknown\n")
            r = _run_cli(q, "check-filed")
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("CalledProcessError", r.stderr)
            self.assertNotIn("Command '[", r.stderr)
            self.assertIn("could not be checked", r.stderr)
            self.assertIn("missing_history.tt", r.stderr)


class TestDamagedBaseNamed(unittest.TestCase):
    """A2-1143: audit and wash-sales --explain name the damaged
    work/<acct>_base.json, not a deleted /tmp merge file."""

    def test_names_the_user_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, {
                "a/q.csv": Y2025,
                "b/b.tt": "BUYSELL 2025-02-03 09:30:00 DEF.TO 10 CAD 5 "
                          "-50 0\n"}, accounts=("a", "b"))
            r = _run_cli(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            # Valid JSON, a bad row: the merge passes it through and
            # the engine then failed on the merged temp file.
            bp = p / "work" / "a_base.json"
            doc = json.loads(bp.read_text())
            doc["transactions"][1]["quantity"] = "abc"
            bp.write_text(json.dumps(doc))
            for args in (("audit", "--no-trace"),
                         ("wash-sales", "--explain")):
                with self.subTest(args=args):
                    r = _run_cli(p, *args)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertNotIn("/tmp/taxjson_", r.stderr)
                    self.assertIn("a_base.json", r.stderr)


if __name__ == "__main__":
    unittest.main()
