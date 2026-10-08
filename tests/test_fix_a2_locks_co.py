"""Re-audit-2 filing-lock fixes, carryover side: the income-dating
settings in carryover's (and t1135's) full-history engine pass, the
filed-year locks it reads (unreadable, non-finite, reached through
prior_year_record, seeding the rebuilt prior years), rows after the
project year, box-18 slip gains, the base-currency check and the
standalone defaults."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tax_rules import rule
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = Path(__file__).resolve().parent.parent


def _row(i, date, sym, qty, net, action="BUYSELL", **kw):
    r = {"id": i, "action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": sym, "quantity": qty,
         "price": abs(net / qty) if qty else 0.0, "net_amount": net,
         "currency": kw.pop("currency", "CAD"), "account": "margin"}
    r.update(kw)
    return r


def _project(root, year, rows, extra="", country="canada", cur="CAD"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": rows}))
    return root


def _carryover(root, *extra):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "carryover", *extra], cwd=REPO_ROOT, capture_output=True,
        text=True, stdin=subprocess.DEVNULL, timeout=300)


def _rows_by_year(stdout):
    return {r["year"]: r for r in json.loads(stdout)["rows"]}


def _lock(path, year, realized, *, country="canada", form_lines=None,
          filed_gain=None, st=0.0, lt=0.0):
    acct = {"realized": realized, "st_gain": st, "lt_gain": lt}
    if form_lines is not None:
        acct["form_lines"] = form_lines
    doc = {"schema_version": 2, "year": year, "country": country,
           "accounts": {"margin": acct},
           "totals": {"realized": realized, "st_gain": st, "lt_gain": lt}}
    if filed_gain is not None:
        doc["filed_totals"] = {"gain": filed_gain, "dispositions": 1,
                               "proceeds": 0.0, "source": "x.csv"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    return path


# 100 ABC.TO for 1,000; a ROC of 500 recorded 2025-12-31, paid
# 2026-01-15; 50 sold 2026-01-06 for 450 (between record and pay date).
ROC_BOOK = [
    _row("b1", "2025-06-02", "ABC.TO", 100, -1000.0),
    _row("r1", "2026-01-15", "ABC.TO", 0, -500.0, action="ADJUST",
         type="roc", record_date="2025-12-31",
         description="ABC CORP RETURN OF CAPITAL REC 12/31/25 PAY 01/15/26"),
    _row("s1", "2026-01-06", "ABC.TO", -50, 450.0),
]


class TestCarryoverIncomeDating(unittest.TestCase):
    """A2-0123 / A2-0339 / A2-0341 / A2-1141 / A2-0337 / A2-0340: the
    ledger's engine pass ignored [settings] corporate_distributions."""

    @rule("CA-INC-DATE-ROC")
    def test_listed_corporation_roc_on_pay_date(self):
        with tempfile.TemporaryDirectory() as td:
            listed = _project(Path(td) / "listed", 2026, ROC_BOOK,
                              'corporate_distributions = ["ABC.TO"]\n')
            r = _carryover(listed, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            # ROC paid after the sale: the 50 cost 500, sold for 450.
            self.assertAlmostEqual(
                _rows_by_year(r.stdout)[2026]["net_gain"], -50.0, places=2)

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_unlisted_trust_roc_on_record_date(self):
        with tempfile.TemporaryDirectory() as td:
            trust = _project(Path(td) / "trust", 2026, ROC_BOOK)
            r = _carryover(trust, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertAlmostEqual(
                _rows_by_year(r.stdout)[2026]["net_gain"], 200.0, places=2)

    def test_standalone_accepts_corporate_distribution(self):
        from taxjson.bin import taxjson_carryover as C
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": ROC_BOOK}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = C.main([str(b), "--country", "canada", "--json",
                             "--corporate-distribution", "ABC.TO"])
            self.assertEqual(rc, 0)
            self.assertAlmostEqual(
                _rows_by_year(out.getvalue())[2026]["net_gain"], -50.0,
                places=2)

    def test_t1135_full_history_pass_gets_the_income_rules(self):
        from taxjson.bin import taxjson_t1135 as T
        seen = []

        def fake(txs, sh, aff, req):
            seen.append(req)
            return {"wash_sales": []}
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": ROC_BOOK}))
            with mock.patch("taxjson.lib.pipeline.run_gains", fake):
                T.full_history_wash_sales(
                    [b], income_rules={"corporate_distributions":
                                       ("ABC.TO",)})
        self.assertEqual(tuple(seen[0].corporate_distributions),
                         ("ABC.TO",))


class TestCarryoverLocks(unittest.TestCase):
    """The filed-year locks carryover compares with or seeds from."""

    BOOK = [_row("b1", "2024-02-01", "XYZ.TO", 100, -10000.0),
            _row("s1", "2025-03-03", "XYZ.TO", -100, 9000.0)]   # -1,000

    def test_unreadable_lock_is_named(self):
        # A2-0336 / A2-1130 / A2-1139: a truncated lock was skipped
        # without a word.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, self.BOOK)
            (p / "filed").mkdir()
            (p / "filed" / "2025.json").write_text('{"year": 2025, "tot')
            r = _carryover(p)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("2025.json could not be read", r.stderr)
            self.assertIn("not checked against the filed return", r.stderr)

    def test_non_finite_lock_total_is_unreadable(self):
        # A2-1131: NaN suppressed the differs warning.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, self.BOOK)
            (p / "filed").mkdir()
            (p / "filed" / "2025.json").write_text(
                '{"year": 2025, "totals": {"realized": NaN}}')
            r = _carryover(p, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("could not be read", r.stderr)
            self.assertNotIn("NaN", r.stdout)

    def test_standalone_refuses_non_finite_filed(self):
        from taxjson.bin import taxjson_carryover as C
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": self.BOOK}))
            err = io.StringIO()
            for bad in ("nan", "inf", "1e400"):
                with redirect_stdout(io.StringIO()), redirect_stderr(err):
                    rc = C.main([str(b), "--country", "canada",
                                 "--filed", f"2025={bad}"])
                self.assertEqual(rc, 2, bad)

    @rule("CA-RPT-10")
    def test_project_year_lock_compares_the_filed_form_line(self):
        # A2-1132 / A2-0666: the filed figure is the form line (or the
        # other tool's filed total), not the lock's unrounded realized.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, self.BOOK)
            _lock(p / "filed" / "2025.json", 2025, -1000.0,
                  form_lines={"13199": 9000.0, "13200": -1000.0},
                  filed_gain=-990.0)
            r = _carryover(p, "--json")
            row = _rows_by_year(r.stdout)[2025]
            self.assertAlmostEqual(row["filed_realized"], -990.0)
            self.assertEqual(row["filed_source"], "filed_totals")
            self.assertTrue(row.get("differs_from_filed"))
            self.assertIn("as filed with another tool", r.stderr)

    @rule("CA-RPT-10")
    def test_prior_record_lock_seeds_the_earlier_year(self):
        # A2-0121 (a) / A2-0338: the 2025 project's books have no 2024
        # dispositions; the 2024 lock (prior_year_record) holds a 4,000
        # net capital loss.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _lock(base / "p24" / "filed" / "2024.json", 2024, -4000.0,
                  form_lines={"13199": 1.0, "13200": -4000.0})
            gain_book = [_row("b1", "2025-02-01", "XYZ.TO", 100, -10000.0),
                         _row("s1", "2025-03-03", "XYZ.TO", -100, 13000.0)]
            p = _project(base / "p25", 2025, gain_book,
                         'prior_year_record = "../p24/filed/2024.json"\n')
            r = _carryover(p, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = _rows_by_year(r.stdout)
            self.assertAlmostEqual(rows[2024]["net_gain"], -4000.0)
            self.assertEqual(rows[2024]["filed_source"], "form_lines")
            self.assertTrue(rows[2024]["from_lock"])
            self.assertAlmostEqual(rows[2025]["available_to_apply"], 3000.0)

    @rule("CA-RPT-10")
    def test_prior_record_lock_offers_the_carryback(self):
        # A2-0121 (b): a 2024 gain locked, a 2025 loss rebuilt.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _lock(base / "p24" / "filed" / "2024.json", 2024, 10000.0)
            p = _project(base / "p25", 2025, self.BOOK,
                         'prior_year_record = "../p24/filed/2024.json"\n')
            r = _carryover(p, "--json")
            rows = _rows_by_year(r.stdout)
            self.assertEqual(rows[2025]["carryback_candidates"],
                             [{"year": 2024, "amount": 1000.0}])

    @rule("CA-RPT-10")
    def test_rebuilt_prior_year_replaced_by_the_lock_with_a_note(self):
        # A2-0338: a rebuilt 2025 row (-1,000) and its lock (-601).
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _lock(base / "p25" / "filed" / "2025.json", 2025, -601.0)
            book = self.BOOK + [_row("s2", "2026-02-02", "QQ.TO", 0, 0.0,
                                     action="FEE")]
            p = _project(base / "p26", 2026, book,
                         'prior_year_record = "../p25/filed/2025.json"\n')
            r = _carryover(p, "--json")
            rows = _rows_by_year(r.stdout)
            self.assertAlmostEqual(rows[2025]["net_gain"], -601.0)
            self.assertAlmostEqual(rows[2025]["rebuilt_net_gain"], -1000.0)
            self.assertIn("-1,000.00", r.stderr)
            self.assertIn("p25/filed/2025.json", r.stderr)

    @rule("US-RPT-08")
    def test_us_prior_record_lock_seeds_short_and_long_term(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _lock(base / "p24" / "filed" / "2024.json", 2024, -4000.0,
                  country="usa", st=-1000.0, lt=-3000.0,
                  form_lines={"I_gain": -1000.0, "II_gain": -3000.0})
            book = [dict(r, currency="USD", symbol="XYZ.US")
                    for r in self.BOOK]
            p = _project(base / "p25", 2025, book, country="usa",
                         cur="USD",
                         extra='prior_year_record = '
                               '"../p24/filed/2024.json"\n')
            r = _carryover(p, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = _rows_by_year(r.stdout)
            self.assertAlmostEqual(rows[2024]["net_st"], -1000.0)
            self.assertAlmostEqual(rows[2024]["net_lt"], -3000.0)
            self.assertAlmostEqual(rows[2024]["lt_carryover"], 1000.0)

    def test_unreadable_claimed_entry_is_refused(self):
        # A2-0355: a claim that cannot be read was read as "absent" (a
        # dangling claimed_losses.txt then). The claims are taxjson.toml's
        # [carryover] claimed now: a bad entry stops, naming it.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, self.BOOK)
            with (p / "taxjson.toml").open("a") as f:
                f.write('\n[carryover]\nclaimed = { 2024 = "lots" }\n')
            r = _carryover(p)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("[carryover] claimed", r.stderr)


class TestCarryoverAfterProjectYear(unittest.TestCase):
    @rule("CA-RPT-10")
    def test_rows_after_the_project_year_are_partial(self):
        # A2-0665: a January trade after the project year produced a T1A
        # suggestion and the "carryforward after" balance.
        book = [_row("b1", "2024-02-01", "XYZ.TO", 200, -20000.0),
                _row("s1", "2024-03-03", "XYZ.TO", -100, 18000.0),
                _row("s2", "2025-01-06", "XYZ.TO", -100, 6000.0)]
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2024, book)
            r = _carryover(p, "--json")
            doc = json.loads(r.stdout)
            rows = {x["year"]: x for x in doc["rows"]}
            self.assertTrue(rows[2025]["after_project_year"])
            self.assertEqual(rows[2025]["carryback_candidates"], [])
            self.assertAlmostEqual(doc["final_carryforward"], 0.0)
            t = _carryover(p)
            self.assertNotIn("carried back to 2024", t.stdout)
            self.assertIn("carryforward after 2024", t.stdout)
            self.assertIn("2025", t.stdout)
            self.assertIn("partial", t.stdout)


class TestCarryoverBox18(unittest.TestCase):
    @rule("CA-INC-06")
    def test_box18_gain_nets_the_year(self):
        # A2-0678: [[capital_gains_dividends]] names 2,500 of box-18
        # gains in 2025; the ledger's 2025 net is -5,000 + 2,500.
        book = [_row("b1", "2025-02-01", "AAA.TO", 100, -10000.0),
                _row("s1", "2025-03-03", "AAA.TO", -100, 5000.0),
                _row("d1", "2025-07-15", "ZZS.TO", 0, 2500.0,
                     action="DIVIDEND")]
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, book)
            (p / "work" / "margin_raw.json").write_text(
                json.dumps({"transactions": book}))
            with (p / "taxjson.toml").open("a") as f:
                f.write('\n[[capital_gains_dividends]]\nsymbol = "ZZS.TO"\n'
                        'year = 2025\namount = "all"\n')
            r = _carryover(p, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            row = _rows_by_year(r.stdout)[2025]
            self.assertAlmostEqual(row["net_gain"], -2500.0)
            self.assertAlmostEqual(row["slip_gains"], 2500.0)


class TestCarryoverStandalone(unittest.TestCase):
    BOOK = TestCarryoverLocks.BOOK

    def _main(self, *argv):
        from taxjson.bin import taxjson_carryover as C
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": self.BOOK,
                                     "metadata": self.meta}))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = C.main([str(b), *argv])
            return rc, out.getvalue(), err.getvalue()

    meta = {}

    def test_canada_book_in_usd_is_refused(self):
        # A2-0351: a Canada ledger labelled USD at rc 0.
        self.BOOK = [dict(r, currency="USD") for r in TestCarryoverLocks.BOOK]
        try:
            rc, _o, err = self._main("--country", "canada",
                                     "--base-currency", "USD")
        finally:
            self.BOOK = TestCarryoverLocks.BOOK
        self.assertEqual(rc, 2)
        self.assertIn("CAD", err)

    def test_metadata_target_currency_is_checked(self):
        self.meta = {"target_currency": "USD"}
        try:
            book = [{k: v for k, v in r.items() if k != "currency"}
                    for r in TestCarryoverLocks.BOOK]
            self.BOOK = book
            rc, _o, err = self._main("--country", "canada")
        finally:
            self.meta = {}
            self.BOOK = TestCarryoverLocks.BOOK
        self.assertEqual(rc, 2)
        self.assertIn("USD", err)

    def test_default_timing_note(self):
        # A2-0677
        rc, _o, err = self._main("--country", "canada")
        self.assertEqual(rc, 0)
        self.assertIn("--option-premium-timing not given", err)

    def test_claimed_amount_lead_group(self):
        # A2-0667
        from taxjson.bin.taxjson_carryover import _parse_amount
        self.assertEqual(_parse_amount("1,234.50"), 1234.5)
        self.assertEqual(_parse_amount("$12,345"), 12345.0)
        for bad in ("0,125", "00,125", "$0,500", "12,34"):
            with self.assertRaises(ValueError, msg=bad):
                _parse_amount(bad)


class TestAuditStandaloneNote(unittest.TestCase):
    def test_audit_default_timing_note(self):
        # A2-0677: standalone taxjson-audit defaulted to close timing on
        # a Canada book with no note.
        from taxjson.bin import taxjson_audit as A
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions":
                                     TestCarryoverLocks.BOOK}))
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                try:
                    A.main(["--country", "canada", "--base", str(b),
                            "--year", "2025"])
                except SystemExit:
                    pass
            self.assertIn("--option-premium-timing not given",
                          err.getvalue())


class TestT1135DanglingMap(unittest.TestCase):
    def test_dangling_t1135_map_is_refused(self):
        # A2-0355; the T1135 overrides are ticker.map lines now, and a
        # leftover t1135.map stops every command until migrated.
        for name in ("ticker.map", "t1135.map"):
            with self.subTest(name=name):
                self._dangling(name)

    def _dangling(self, name):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, TestCarryoverLocks.BOOK)
            os.symlink(Path(td) / "gone.map", p / name)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(p), "t1135"], cwd=REPO_ROOT, capture_output=True,
                text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(name, r.stderr)


if __name__ == "__main__":
    unittest.main()
