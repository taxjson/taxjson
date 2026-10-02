"""Re-audit-2 filing-lock fixes (close-year, handoff, option-boundary):
the prior-year lock reached through [settings] prior_year_record, the
close-year gates, and the hand-off record/check."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(root, year, tt="", extra="", country="canada", cur="CAD"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')
    if tt:
        (root / "inputs" / "margin" / "a.tt").write_text(tt)
    return root


class TestProjectLocks(unittest.TestCase):
    """taxjson_filed.project_locks: the local locks plus the one
    prior_year_record names (A2-0036, A2-0335, A2-0338, A2-0664)."""

    def test_prior_record_is_a_lock_of_its_year(self):
        from taxjson.bin import taxjson_filed as tf
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "p25" / "filed").mkdir(parents=True)
            (base / "p25" / "filed" / "2025.json").write_text(
                json.dumps({"year": 2025, "totals": {}}))
            (base / "p26").mkdir()
            st = {"year": 2026,
                  "prior_year_record": "../p25/filed/2025.json"}
            locks = tf.project_locks(base / "p26", st)
            self.assertEqual([(y, w) for y, _p, w in locks],
                             [(2025, "prior_year_record")])
            # A local lock of the same year wins; no duplicate.
            (base / "p26" / "filed").mkdir()
            (base / "p26" / "filed" / "2025.json").write_text(
                json.dumps({"year": 2025}))
            self.assertEqual([(y, w) for y, _p, w in
                              tf.project_locks(base / "p26", st)],
                             [(2025, "local")])
            self.assertIsNone(tf.lock_for_year(base / "p26", st, 2024))
            with self.assertRaises(tf.PriorRecordError):
                tf.project_locks(base / "p26",
                                 dict(st, prior_year_record=5))


class TestHandoffPriorRecordType(unittest.TestCase):
    def test_non_string_prior_year_record_is_refused_like_run(self):
        # A2-1135: handoff read 5 as the path <root>/5.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2026,
                         extra="prior_year_record = 5\n")
            r = _run_cli(p, "handoff")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("prior_year_record must be a path string",
                          r.stderr)
            self.assertNotIn("/5", r.stderr)


TT25 = ("BUYSELL 2025-03-03 09:30:00 KEEP.TO 40 CAD 25 1000 0\n"
        "BUYSELL 2025-03-03 09:30:00 SOLD.TO 10 CAD 100 1000 0\n"
        "BUYSELL 2025-06-02 09:30:00 SOLD.TO -10 CAD 90 900 0\n")


class _Base(unittest.TestCase):
    """One clean 2025 Canada project, run once; tests copy it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.p25 = _project(cls.base / "p25", 2025, TT25)
        r = _run_cli(cls.p25, "run", "--no-input")
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy(self, name):
        import shutil
        dst = self.base / name
        shutil.copytree(self.p25, dst)
        return dst


@rule("CA-RPT-08")
class TestCloseYearRefusesBrokenBooks(_Base):
    def test_no_reports_is_not_locked(self):
        # A2-0035: work/ books but no reports/ (a run that died before
        # writing them): run-clean is "blocked" and close-year locked
        # anyway with rc 0.
        import shutil
        p = self.copy("noreports")
        shutil.rmtree(p / "reports")
        r = _run_cli(p, "close-year")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("no reports", r.stderr)
        self.assertIn("Nothing was written", r.stderr)
        self.assertFalse((p / "filed" / "2025.json").exists())

    def test_unreadable_base_is_refused(self):
        # A2-0346: a truncated margin_base.json gave a lock with
        # year_end {"equity": {}} at rc 0.
        import os
        p = self.copy("truncbase")
        b = p / "work" / "margin_base.json"
        st = b.stat()
        b.write_text(b.read_text()[:50])
        os.utime(b, (st.st_atime, st.st_mtime))
        r = _run_cli(p, "close-year")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("work/margin_base.json", r.stderr)
        self.assertIn("Nothing was written", r.stderr)
        self.assertFalse((p / "filed" / "2025.json").exists())


@rule("CA-RPT-08")
class TestHandoffBrokenBooks(_Base):
    def test_unreadable_base_names_the_file(self):
        # A2-1137: handoff reported every lot as "missing from the
        # opening file". A2-1143: a damaged row was reported against a
        # deleted /tmp merge file.
        import os
        p = self.copy("hb")
        r = _run_cli(p, "close-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = p / "filed" / "2025.json"
        q = _project(self.base / "hb26", 2026, TT25,
                     f'prior_year_record = "{rec}"\n')
        r = _run_cli(q, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = q / "work" / "margin_base.json"
        good = b.read_text()
        st = b.stat()
        b.write_text(good[:50])
        os.utime(b, (st.st_atime, st.st_mtime))
        r = _run_cli(q, "handoff")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("could not read work/margin_base.json", r.stderr)
        self.assertNotIn("A lot or a sale is missing", r.stdout)
        doc = json.loads(good)
        doc["transactions"][0]["quantity"] = "abc"
        b.write_text(json.dumps(doc))
        r = _run_cli(q, "handoff")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("work/margin_base.json", r.stderr)
        self.assertNotIn("/tmp/taxjson-handoff-", r.stderr)


OPT_TT25 = ("BUYSELL 2025-03-03 09:30:00 ZZZ.TO 100 CAD 25 2500 0\n"
            "BUYSELL 2025-03-03 09:30:00 SOLD.TO 10 CAD 100 1000 0\n"
            "BUYSELL 2025-06-02 09:30:00 SOLD.TO -10 CAD 90 900 0\n"
            "BUYSELL 2025-12-15 09:30:00 ZZZ260116C00030000.TO -1 CAD "
            "3.99 399 0\n")
OPT_TT26 = OPT_TT25 + ("BUYSELL 2026-01-12 09:30:00 ZZZ260116C00030000.TO "
                       "1 CAD 1.01 101 0\n")


class TestPriorLockTiming(unittest.TestCase):
    """A 2025 project locked on one premium timing, a 2026 project
    (per-year layout, prior_year_record) on another."""

    def _pair(self, td, since25, extra26):
        base = Path(td)
        p25 = _project(base / "p25", 2025, OPT_TT25,
                       f"option_premium_timing = \"{since25[0]}\"\n"
                       + (f"option_grant_timing_since = {since25[1]}\n"
                          if since25[1] else ""))
        r = _run_cli(p25, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run_cli(p25, "close-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        p26 = _project(base / "p26", 2026, OPT_TT26,
                       'prior_year_record = "../p25/filed/2025.json"\n'
                       + extra26)
        r = _run_cli(p26, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        return p26

    @rule("CA-OPT-01", "CA-RPT-08")
    def test_handoff_flags_grant_then_close(self):
        # A2-0037: 2025 taxed the 399 premium (grant since 2025); the
        # 2026 project defaults since to 2026 and taxes 298 at the
        # close. handoff said "Everything ... here, once" at rc 0.
        with tempfile.TemporaryDirectory() as td:
            p26 = self._pair(td, ("grant", 2025), "")
            r = _run_cli(p26, "handoff", "--json")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual([i["symbol"] for i in doc["timing"]],
                             ["ZZZ260116C00030000.TO"])
            self.assertIn("option_grant_timing_since = 2025",
                          doc["timing"][0]["why"])
            # Once the since matches the record, the hand-off is clean.
            t = (p26 / "taxjson.toml").read_text().replace(
                "[accounts.margin]",
                "option_grant_timing_since = 2025\n[accounts.margin]")
            (p26 / "taxjson.toml").write_text(t)
            r = _run_cli(p26, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(p26, "handoff")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @rule("CA-OPT-01")
    def test_option_boundary_reads_prior_year_record(self):
        # A2-0036: the 2025 lock (close timing) is reached only through
        # prior_year_record; the 2026 project is on grant since 2025, so
        # the 399 premium is in no return. option-boundary printed "no
        # filed-year locks" and "No amended return is required".
        with tempfile.TemporaryDirectory() as td:
            p26 = self._pair(td, ("close", None),
                             "option_grant_timing_since = 2025\n")
            r = _run_cli(p26, "option-boundary")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("no filed-year locks", r.stdout)
            self.assertNotIn("No amended return is required", r.stdout)
            self.assertIn("records CLOSE timing", r.stdout)
            self.assertIn("../p25/filed/2025.json", r.stdout)
            r = _run_cli(p26, "handoff", "--json")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(len(json.loads(r.stdout)["timing"]), 1)

    @rule("CA-OPT-01")
    def test_since_hint_uses_the_locked_since(self):
        # A2-1142: the hint said "e.g. 2026"; following it taxes the
        # 2025-written call twice.
        with tempfile.TemporaryDirectory() as td:
            p26 = self._pair(td, ("grant", 2025), "")
            r = _run_cli(p26, "option-boundary")
            self.assertIn("(e.g. 2025, the year ../p25/filed/2025.json "
                          "records)", r.stderr)


if __name__ == "__main__":
    unittest.main()
