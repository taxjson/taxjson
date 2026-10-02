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
        self.assertIn("reports/", r.stderr)
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


@rule("CA-RPT-08")
class TestCloseYearForce(_Base):
    def test_force_keeps_filed_dispositions_and_warns_on_moved_totals(self):
        # A2-0119: --force (check-filed's DRIFTED advice) dropped the
        # dispositions another tool filed. A2-0345: it replaced the
        # filed totals without a word.
        p = self.copy("force")
        csvp = p / "filed.csv"
        csvp.write_text("symbol,date,qty,proceeds,cost,gain\n"
                        "SOLD,2025-06-02,10,900,1000,-100\n")
        r = _run_cli(p, "close-year", "--filed-dispositions", str(csvp))
        self.assertEqual(r.returncode, 0, r.stderr)
        lock = p / "filed" / "2025.json"
        doc = json.loads(lock.read_text())
        doc["totals"]["realized"] = -90.0          # what was filed
        lock.write_text(json.dumps(doc))
        r = _run_cli(p, "close-year", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        new = json.loads(lock.read_text())
        self.assertEqual(len(new["filed_dispositions"]), 1)
        self.assertEqual(new["filed_totals"]["gain"], -100.0)
        self.assertIn("kept the 1 filed disposition", r.stdout)
        self.assertIn("realized -90.00 -> -100.00", r.stderr)

    def test_filed_dispositions_csv_funnel(self):
        p = self.copy("csvf")
        good = ("symbol,date,qty,proceeds,cost,gain\n"
                "AAA,2025-03-01,10,1000,900,100\n"
                "BBB,2025-03-02,10,1000,1100,-100\n"
                "CCC,2025-03-03,10,1000,990,10\n")
        # A2-1138: a UTF-16 (Excel "Unicode Text") save is read.
        u = p / "u16.csv"
        u.write_bytes(good.replace(",", "\t").encode("utf-16"))
        r = _run_cli(p, "close-year", "--filed-dispositions", str(u))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads((p / "filed" / "2025.json").read_text())
                         ["filed_totals"]["gain"], 10.0)
        # A directory: one line, exit 2, no traceback.
        r = _run_cli(p, "close-year", "--force", "--filed-dispositions",
                     str(p / "inputs"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        # A2-1136: a stray quote merged rows and dropped one in silence.
        bad = p / "bad.csv"
        bad.write_text(good.replace("AAA,", '"AAA,', 1))
        r = _run_cli(p, "close-year", "--force", "--filed-dispositions",
                     str(bad))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unescaped quote", r.stderr)


@rule("CA-RPT-08")
class TestPartialYearRecord(unittest.TestCase):
    def test_handoff_flags_a_record_closed_before_year_end(self):
        # A2-0349: a record from close-year --force during the year.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            p = _project(base / "p", 2025, TT25)
            self.assertEqual(_run_cli(p, "run", "--no-input").returncode, 0)
            self.assertEqual(_run_cli(p, "close-year").returncode, 0)
            lock = p / "filed" / "2025.json"
            doc = json.loads(lock.read_text())
            doc["closed_at"] = "2025-10-01T20:30:42"
            lock.write_text(json.dumps(doc))
            q = _project(base / "q", 2026, TT25,
                         f'prior_year_record = "{lock}"\n')
            self.assertEqual(_run_cli(q, "run", "--no-input").returncode, 0)
            r = _run_cli(q, "handoff", "--json")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            rep = json.loads(r.stdout)
            self.assertEqual(len(rep["partial"]), 1)
            self.assertIn("partial-year snapshot", rep["partial"][0]["why"])


GENERIC_MAP = """[columns]
date="Date"
settle="Settle"
action="Type"
symbol="Ticker"
quantity="Shares"
price="Price"
amount="Amount"
currency="Currency"
[actions]
"BUY"="buy"
"SELL"="sell"
"""


def _year_end(p, year):
    r = _run_cli(p, "run", "--no-input")
    assert r.returncode == 0, r.stderr
    r = _run_cli(p, "close-year")
    assert r.returncode == 0, r.stderr
    return json.loads((p / "filed" / f"{year}.json").read_text()
                      )["year_end"]["equity"]


class TestYearEndLandings(unittest.TestCase):
    """close-year's year-end cost places each s.53(1)(f) addition where
    the engine lands it (wash_sales[].adjusts), not the adjust_cmd lump."""

    @rule("CA-RPT-08")
    def test_january_landing_is_not_in_the_dec31_cost(self):
        # A2-0669: 1,000 of the 2,000 denied loss lands on the Jan 5
        # replacement; the lump put all 2,000 on the Dec 22 lot.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, (
                "BUYSELL 2025-06-02 09:30:00 FOO.TO 100 CAD 50 5000 0\n"
                "BUYSELL 2025-12-19 09:30:00 FOO.TO -100 CAD 30 3000 0\n"
                "BUYSELL 2025-12-22 09:30:00 FOO.TO 50 CAD 30 1500 0\n"
                "BUYSELL 2026-01-05 09:30:00 FOO.TO 50 CAD 30 1500 0\n"))
            ye = _year_end(p, 2025)
            self.assertEqual(ye["FOO.TO"]["qty"], 50.0)
            self.assertAlmostEqual(ye["FOO.TO"]["acb"], 2500.0, places=2)
            self.assertAlmostEqual(ye["FOO.TO"]["deferred"], 1000.0,
                                   places=2)

    @rule("CA-RPT-08")
    def test_multi_symbol_allocation_lands_per_symbol(self):
        # A2-0352: the loss is replaced by shares AND a call; the lump
        # booked all of it on the shares.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2024, (
                "BUYSELL 2024-11-01 10:00:00 XYZ.TO 200 CAD 20 4000 0\n"
                "BUYSELL 2024-12-02 10:00:00 XYZ.TO -200 CAD 10 2000 0\n"
                "BUYSELL 2024-12-05 10:00:00 XYZ.TO 100 CAD 10 1000 0\n"
                "BUYSELL 2024-12-05 10:01:00 XYZ250321C00012000.TO 1 CAD "
                "1 100 0\n"))
            ye = _year_end(p, 2024)
            inv = {i["symbol"]: i["total_cost"] for i in json.loads(
                (p / "work" / "margin_gains_wash.json").read_text()
            )["inventory"]}
            for sym in ("XYZ.TO", "XYZ250321C00012000.TO"):
                self.assertAlmostEqual(ye[sym]["acb"], inv[sym], places=2)
            self.assertGreater(ye["XYZ250321C00012000.TO"]["deferred"], 0)

    @rule("CA-RPT-08")
    def test_settle_basis_bump_lands_after_the_losing_sale(self):
        # A2-1140: the pre-loss bump was dated settle = trade day, so on
        # settle basis it was applied before the sale settled (1,650
        # instead of the engine's 1,800).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p"
            p.mkdir()
            (p / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n[accounts.zeta]\n'
                'type = "taxable"\n\n[accounts.alpha]\ntype = "taxable"\n')
            rows = {"zeta": ["2025-01-06,2025-01-07,BUY,XYZ.TO,100,10,"
                             "-1000,CAD",
                             "2025-03-03,2025-03-04,SELL,XYZ.TO,-100,12,"
                             "1200,CAD"],
                    "alpha": ["2025-03-03,2025-03-04,BUY,XYZ.TO,100,20,"
                              "-2000,CAD"]}
            for a, rs in rows.items():
                (p / "inputs" / a).mkdir(parents=True)
                (p / "inputs" / a / "generic.toml").write_text(GENERIC_MAP)
                (p / "inputs" / a / "generic_t.csv").write_text(
                    "Date,Settle,Type,Ticker,Shares,Price,Amount,Currency\n"
                    + "\n".join(rs) + "\n")
            ye = _year_end(p, 2025)
            self.assertAlmostEqual(ye["XYZ.TO"]["acb"], 1800.0, places=2)

    @rule("US-RPT-06")
    def test_us_year_end_basis_carries_the_1091_addition(self):
        # A2-0353: US wash_sales carry no adjust_cmd; the record dropped
        # the disallowed loss from the replacement's basis.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2024, (
                "BUYSELL 2024-01-15 09:30:00 MSFT.US 100 USD 100 10000 0\n"
                "BUYSELL 2024-12-10 09:30:00 MSFT.US -100 USD 98 9800 0\n"
                "BUYSELL 2024-12-20 09:30:00 MSFT.US 60 USD 97.5 5850 0\n"),
                country="usa", cur="USD")
            ye = _year_end(p, 2024)
            self.assertAlmostEqual(ye["MSFT.US"]["acb"], 5970.0, places=2)
            self.assertAlmostEqual(ye["MSFT.US"]["deferred"], 120.0,
                                   places=2)


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if action == "Buy" else gross
    return (f"{trade} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,55500001,"  # pii-ok
            f"Trades,Individual\n")


def _qt_project(root, year, csv, extra=""):
    p = _project(root, year, "", extra)
    (p / "inputs" / "margin" / "questrade.csv").write_text(_QT_HEADER + csv)
    r = _run_cli(p, "run", "--no-input")
    assert r.returncode == 0, r.stderr
    return p


@rule("CA-RPT-08")
class TestHandoffMatching(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _closed(self, csv, extra=""):
        p = _qt_project(self.base / "p25", 2025, csv, extra)
        r = _run_cli(p, "close-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        return p / "filed" / "2025.json"

    def _handoff(self, rec, csv, extra=""):
        q = _qt_project(self.base / "p26", 2026, csv,
                        f'prior_year_record = "{rec}"\n' + extra)
        r = _run_cli(q, "handoff", "--json")
        return r.returncode, json.loads(r.stdout)

    KEEP = (_qt("2025-03-03", "2025-03-04", "Buy", "KEEP.TO", 10, 10.0)
            + _qt("2025-06-03", "2025-06-04", "Sell", "KEEP.TO", -10, 11.0))
    HIST = (KEEP
            + _qt("2025-03-03", "2025-03-04", "Buy", "AAA.TO", 200, 10.0)
            + _qt("2025-12-31", "2026-01-02", "Sell", "AAA.TO", -100, 15.0))

    def test_distinct_january_sale_does_not_hide_a_missed_one(self):
        # A2-0354: the Dec-31 sale (net 1,500) is in neither year; a
        # distinct 100-unit sale on Jan 2 (net 1,200) cleared it.
        rec = self._closed(self.HIST)
        rc, rep = self._handoff(rec, (
            self.KEEP
            + _qt("2025-03-03", "2025-03-04", "Buy", "AAA.TO", 200, 10.0)
            + _qt("2026-01-02", "2026-01-05", "Sell", "AAA.TO", -100,
                  12.0)))
        self.assertEqual(rc, 1)
        self.assertEqual([m["symbol"] for m in rep["missed"]], ["AAA.TO"])

    def test_trade_basis_record_into_settle_project_one_item(self):
        # A2-0356 / A2-0673: 3 problems per straddling sale (positions +
        # two doubles), the double quoting the settle date.
        rec = self._closed(self.HIST, 'tax_date = "trade"\n')
        rc, rep = self._handoff(rec, self.HIST)
        self.assertEqual(rc, 1)
        self.assertEqual(rep["positions"], [])
        self.assertEqual(len(rep["double"]), 1)
        self.assertIn("date-basis change", rep["double"][0]["why"])
        self.assertEqual(rep["problems"], 1)

    def test_settle_basis_record_into_trade_project_one_item(self):
        rec = self._closed(self.HIST)
        rc, rep = self._handoff(rec, self.HIST, 'tax_date = "trade"\n')
        self.assertEqual(rc, 1)
        self.assertEqual(rep["positions"], [])
        self.assertEqual(len(rep["missed"]), 1)
        self.assertEqual(rep["problems"], 1)

    def test_same_size_sales_either_side_of_dec31_are_distinct(self):
        # A2-0122: the Dec-29 sale is its own 2025 row in this project;
        # the Jan-2 sale of the same size is a different sale.
        h = (_qt("2025-03-03", "2025-03-04", "Buy", "XYZ.TO", 200, 50.0)
             + _qt("2025-12-29", "2025-12-30", "Sell", "XYZ.TO", -100,
                   50.0))
        rec = self._closed(h)
        rc, rep = self._handoff(rec, h + _qt(
            "2026-01-02", "2026-01-05", "Sell", "XYZ.TO", -100, 50.2))
        self.assertEqual(rep["double"], [], rep)
        self.assertEqual(rc, 0, rep)

    def test_short_cover_against_filed_dispositions(self):
        # A2-0674: another tool reported the short in 2025 (proceeds
        # 5,000); this project books the cover on Jan 2 (the engine's
        # proceeds are the negated cover cost) — never matched.
        h = (_qt("2025-03-03", "2025-03-04", "Buy", "KEEP.TO", 10, 10.0)
             + _qt("2025-06-03", "2025-06-04", "Sell", "KEEP.TO", -10,
                   11.0))
        p = _qt_project(self.base / "p25", 2025, h)
        csvp = self.base / "filed.csv"
        csvp.write_text("symbol,date,qty,proceeds,cost,gain\n"
                        "ABC.TO,2025-12-31,100,5000,4000,1000\n")
        r = _run_cli(p, "close-year", "--filed-dispositions", str(csvp))
        self.assertEqual(r.returncode, 0, r.stderr)
        rc, rep = self._handoff(p / "filed" / "2025.json", h + (
            _qt("2025-12-31", "2026-01-02", "Sell", "ABC.TO", -100, 50.0)
            + _qt("2026-01-02", "2026-01-05", "Buy", "ABC.TO", 100, 40.0)))
        self.assertEqual([d["symbol"] for d in rep["double"]], ["ABC.TO"])


class TestHandoffDust(unittest.TestCase):
    def test_sub_unit_quantity_difference_is_reported(self):
        # A2-1133: 9.4e-7 BTC against 5.6e-7 (or 0) was "no difference".
        from taxjson.lib import handoff
        cfg = {"settings": {"country": "canada", "year": 2026},
               "accounts": {"crypto": {"type": "taxable", "crypto": True}}}
        rec = {"year": 2025, "year_end": {"crypto": {
            "BTC": {"qty": 9.4e-7, "acb": 0.05, "deferred": 0.0}}}}
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "work").mkdir()
            for now in (5.6e-7, 0.0):
                op = {"crypto": {"BTC": {"qty": now, "acb": 0.05,
                                         "deferred": 0.0}}}
                rep = handoff.check(Path(td), cfg, rec, op)
                self.assertEqual(len(rep["positions"]), 1, now)
            op = {"crypto": {"BTC": {"qty": 9.4e-7, "acb": 0.05,
                                     "deferred": 0.0}}}
            self.assertEqual(handoff.check(Path(td), cfg, rec, op)
                             ["positions"], [])


def _qrow(d, s_, act, sym, desc, q, p, g, c, n, act_type):
    return (f"{d} 12:00:00 AM,{s_} 12:00:00 AM,{act},{sym},{desc},{q},{p},"
            f"{g},{c},{n},CAD,55500001,{act_type},Individual margin\n")  # pii-ok


class TestHandoffAcrossDec31(unittest.TestCase):
    """Rows the closed and the next project date on different sides of
    Dec 31 (boundary_rows in the record)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    @rule("CA-INC-DATE-TRUST", "CA-INC-DATE-ROC-TRUST", "CA-RPT-08")
    def test_trust_income_dated_back_into_the_closed_year(self):
        # A2-0120: the 2025 project (2025 export only) never had the
        # distribution / ROC; the 2026 project dates them on their Dec
        # record dates, so they are in neither return.
        f25 = (_QT_HEADER
               + _qrow("2025-03-03", "2025-03-04", "Buy", "XIC.TO",
                       "ISHARES CORE S&P/TSX CAPPED COMPOSITE INDEX ETF",
                       100, 30, -3000, 0, -3000, "Trades")
               + _qrow("2025-03-03", "2025-03-04", "Buy", "REI.UN.TO",
                       "RIOCAN REAL ESTATE INVESTMENT TRUST", 100, 20,
                       -2000, 0, -2000, "Trades")
               + _qrow("2025-03-03", "2025-03-04", "Buy", "ZZZ.TO",
                       "SYNTHETIC CORP", 10, 10, -100, 0, -100, "Trades")
               + _qrow("2025-06-03", "2025-06-04", "Sell", "ZZZ.TO",
                       "SYNTHETIC CORP", -10, 12, 120, 0, 120, "Trades"))
        f26 = (_QT_HEADER
               + _qrow("2026-01-05", "2026-01-05", "   ", "XIC.TO",
                       "ISHARES CORE S&P/TSX CAPPED COMPOSITE INDEX ETF "
                       "DIST ON 100 SHS REC 12/30/25 PAY 01/05/26", 0,
                       0.28, 0, 0, 28.00, "Dividends")
               + _qrow("2026-01-15", "2026-01-15", "RTC", "REI.UN.TO",
                       "RIOCAN REAL ESTATE INVESTMENT TRUST RETURN OF "
                       "CAPITAL REC 12/31/25 PAY 01/15/26", 0, 0, 0, 0,
                       40.00, "Dividends"))
        p25 = _project(self.base / "p25", 2025)
        (p25 / "inputs" / "margin" / "q25.csv").write_text(f25)
        self.assertEqual(_run_cli(p25, "run", "--no-input").returncode, 0)
        r = _run_cli(p25, "close-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        p26 = _project(self.base / "p26", 2026, "",
                       'prior_year_record = "../p25/filed/2025.json"\n')
        (p26 / "inputs" / "margin" / "q25.csv").write_text(f25)
        (p26 / "inputs" / "margin" / "q26.csv").write_text(f26)
        self.assertEqual(_run_cli(p26, "run", "--no-input").returncode, 0)
        r = _run_cli(p26, "handoff", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(sorted(i["symbol"] for i in rep["boundary"]),
                         ["REI.UN.TO", "XIC.TO"])
        self.assertIn("neither return", rep["boundary"][0]["why"])
        # Both projects with the same inputs: nothing to report.
        p25b = _project(self.base / "p25b", 2025)
        (p25b / "inputs" / "margin" / "q25.csv").write_text(f25)
        (p25b / "inputs" / "margin" / "q26.csv").write_text(f26)
        self.assertEqual(_run_cli(p25b, "run", "--no-input").returncode, 0)
        self.assertEqual(_run_cli(p25b, "close-year").returncode, 0)
        t = (p26 / "taxjson.toml").read_text().replace("../p25/", "../p25b/")
        (p26 / "taxjson.toml").write_text(t)
        r = _run_cli(p26, "handoff", "--json")
        self.assertEqual(json.loads(r.stdout)["boundary"], [], r.stdout)

    @rule("CA-RPT-08")
    def test_rows_redated_into_the_closed_year_or_moved_past_it(self):
        # A2-0343 / A2-0344: a round trip and an earn reward that
        # local_timezone re-dates from Jan 1 UTC to Dec 31 are in the
        # next project's books only. A2-0670: the closed project's
        # inputs hold a fill the overnight shift moved to Jan 2 that the
        # next project lacks.
        p25 = _project(self.base / "p25", 2025, TT25
                       + "BUYSELL 2026-01-02 09:30:00 QZQ.TO 50 CAD 10 500 0\n")
        self.assertEqual(_run_cli(p25, "run", "--no-input").returncode, 0)
        r = _run_cli(p25, "close-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        p26 = _project(self.base / "p26", 2026, TT25 + (
            "BUYSELL 2025-12-31 19:00:00 ETH.TO 1 CAD 4000 4000 0\n"
            "BUYSELL 2025-12-31 20:00:00 ETH.TO -1 CAD 4980 4980 0\n"
            "DIVIDEND 2025-12-31 20:30:00 USD.HOLD.TO 0 CAD 0 342.65 0\n"),
            'prior_year_record = "../p25/filed/2025.json"\n')
        r = _run_cli(p26, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run_cli(p26, "handoff", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        got = sorted((i["symbol"], i["action"])
                     for i in json.loads(r.stdout)["boundary"])
        self.assertEqual(got, [("ETH.TO", "BUYSELL"),
                               ("QZQ.TO", "BUYSELL"),
                               ("USD.HOLD.TO", "DIVIDEND")])

    @rule("US-RPT-06")
    def test_us_ric_january_dividend_counted_again(self):
        # A2-0675: the 2025 project moves VTI.US's Jan 5 dividend into
        # 2025 (ric_january_dividends); the 2026 project, without the
        # entry, counts it again in 2026.
        tt = ("BUYSELL 2025-02-03 09:30:00 VTI.US 10 USD 200 2000 0\n"
              "BUYSELL 2025-02-03 09:30:00 SOLD.US 10 USD 100 1000 0\n"
              "BUYSELL 2025-06-02 09:30:00 SOLD.US -10 USD 90 900 0\n"
              "DIVIDEND 2026-01-05 00:00:00 VTI.US 0 USD 0 100.00 0\n")
        p25 = _project(self.base / "p25", 2025, tt,
                       'ric_january_dividends = ["VTI.US 2026-01-05"]\n',
                       country="usa", cur="USD")
        self.assertEqual(_run_cli(p25, "run", "--no-input").returncode, 0)
        r = _run_cli(p25, "close-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        p26 = _project(self.base / "p26", 2026, tt,
                       'prior_year_record = "../p25/filed/2025.json"\n',
                       country="usa", cur="USD")
        self.assertEqual(_run_cli(p26, "run", "--no-input").returncode, 0)
        r = _run_cli(p26, "handoff", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        b = json.loads(r.stdout)["boundary"]
        self.assertEqual([(i["symbol"], i["action"]) for i in b],
                         [("VTI.US", "DIVIDEND")])
        self.assertIn("again", b[0]["why"])


class TestHandoffOwnerShapes(unittest.TestCase):
    """Shapes from a measurement on real books (no false problems)."""

    CFG = {"settings": {"country": "canada", "year": 2026},
           "accounts": {"margin": {"type": "taxable"}}}

    def _cache(self, td, base_rows, gains_rows):
        work = Path(td) / "work"
        work.mkdir()
        (work / "margin_base.json").write_text(
            json.dumps({"transactions": base_rows}))
        (work / "margin_gains_wash.json").write_text(
            json.dumps({"transactions": gains_rows}))
        return work

    @rule("CA-RPT-08")
    def test_grant_premium_row_is_not_a_double_of_a_buy_back(self):
        # A grant-timed premium row (proceeds 0) in the record and a
        # January buy-back here (proceeds -250, cost 0) are not one sale.
        from taxjson.lib import handoff
        opt = "PSX260116C00065000.US"
        with tempfile.TemporaryDirectory() as td:
            self._cache(td, [], [
                {"symbol": opt, "date": "2025-12-31",
                 "date_settle": "2026-01-02", "qty": 1.0,
                 "proceeds": -250.31, "cost": -0.0, "gain": -250.31,
                 "direction": "SHORT"}])
            rec = {"year": 2025, "year_end": {}, "boundary_rows": [],
                   "dispositions": [
                       {"symbol": opt, "date": "2025-12-30",
                        "date_settle": "2025-12-31", "qty": 1.0,
                        "proceeds": 0.0, "cost": -328.44,
                        "gain": 328.44}]}
            rep = handoff.check(Path(td), self.CFG, rec, {})
            self.assertEqual(rep["double"], [])

    @rule("CA-RPT-08")
    def test_boundary_rows_keep_full_quantity_precision(self):
        from taxjson.lib import handoff
        rows = [{"action": "DIVIDEND", "symbol": "SOL",
                 "date": "2025-12-30", "date_settle": "2025-12-30",
                 "time": "14:56:03", "quantity": 0.000135313637,
                 "net_amount": 0.02307, "currency": "CAD", "id": "d1"}]
        with tempfile.TemporaryDirectory() as td:
            work = self._cache(td, rows, [])
            rec = {"year": 2025, "year_end": {}, "dispositions": [],
                   "boundary_rows": json.loads(json.dumps(
                       handoff.boundary_rows(work, self.CFG, 2025)))}
            rep = handoff.check(Path(td), self.CFG, rec, {})
            self.assertEqual(rep["boundary"], [])


if __name__ == "__main__":
    unittest.main()
