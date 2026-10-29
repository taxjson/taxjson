"""Audit 2026-10-24, group sanity-slips: `taxjson sanity` (B17, B18,
B19), IB's dividends report (B24, B25, B26, B31), the slip-key salt
(A43) and reconcile-slips (B47). Synthetic data and fake ids only."""
import csv
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from _style import CapturedWidth

from taxjson.bin import taxjson_reconcile_slips as rs
from taxjson.lib import ib_dividends as ibdiv
from taxjson.lib import positions_check as PC
from taxjson.lib import slip_audit as sa
from taxjson.lib.dispatch import python_module_argv

# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the stderr phrases asserted below are then never wrapped.
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------------ sanity
def _sanity_project(root: Path, *, inventory_qty=20, base_text=None,
                    snapshots=(("2026-01-15", 10), ("2026-02-15", 20))):
    """A one-account project with built books and holdings/ snapshots:
    10 ZZQ.US bought 2026-01-01 and 10 more 2026-02-01 (20 held now)."""
    (root / "work").mkdir()
    (root / "holdings").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\naccount = "99900001"\n')
    inv = [{"symbol": "ZZQ.US", "qty": inventory_qty, "total_cost": 200,
            "currency": "CAD"}]
    (root / "work" / "margin_gains.json").write_text(json.dumps(
        {"inventory": inv, "summary": {"year": "2026"}}))
    base = [dict(date=d, date_settle=d, time="10:00:00", symbol="ZZQ.US",
                 quantity=10, action="BUYSELL", account="margin")
            for d in ("2026-01-01", "2026-02-01")]
    (root / "work" / "margin_base.json").write_text(
        base_text if base_text is not None
        else json.dumps({"transactions": base}))
    for i, (day, q) in enumerate(snapshots):
        rows = (f'[[holding]]\nsymbol = "ZZQ.US"\nquantity = {q}\n'
                f'asset_type = "equity"\n' if q else "")
        (root / "holdings" / f"margin_{i}.toml").write_text(
            f'schema_version = "1.0"\n[meta]\naccount = "margin"\n'
            f'broker_account = "99900001"\nas_of = "{day}"\n' + rows)


def _sanity(root: Path):
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), "sanity", "--json"],
        capture_output=True, text=True, env=dict(os.environ))
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = None
    return r.returncode, doc, r.stderr


class TestSanity(unittest.TestCase):

    def test_b17_snapshots_of_one_account_at_two_dates_are_not_summed(self):
        """B17: Sanity sums snapshots from different dates instead of
        checking each date — two holdings/ snapshots of one broker
        account (10 on Jan 15, 20 on Feb 15) compared as 30 against 20.
        Each is now compared with the books on its own day."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _sanity_project(root)
            code, doc, err = _sanity(root)
            self.assertEqual(code, 0, err)
            self.assertTrue(doc["clean"])
            self.assertTrue(doc["complete"])
            self.assertEqual([g["as_of"] for g in doc["groups"]],
                             ["2026-01-15", None])
            self.assertTrue(all(g["clean"] for g in doc["groups"]))
            self.assertIn("each is compared with the books on its own day",
                          err)
        # A snapshot that disagrees with its day's books still fails.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _sanity_project(root, snapshots=(("2026-01-15", 15),
                                             ("2026-02-15", 20)))
            code, doc, err = _sanity(root)
            self.assertEqual(code, 1, err)
            self.assertFalse(doc["clean"])
            bad = [g for g in doc["groups"] if not g["clean"]]
            self.assertEqual([g["as_of"] for g in bad], ["2026-01-15"])
            self.assertEqual(bad[0]["discrepancies"][0]["holdings_qty"], 15)
            self.assertEqual(bad[0]["discrepancies"][0]["taxjson_qty"], 10)

    def test_b18_unreadable_base_book_is_refused_not_certified(self):
        """B18: Sanity certifies a historical snapshot despite an
        unreadable base book — work/margin_base.json that is not JSON
        read as an empty history, and the Jan 15 snapshot was compared
        with the latest books: clean, complete. Refused, naming the
        file."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _sanity_project(root, base_text="{",
                            snapshots=(("2026-01-15", 20),))
            code, doc, err = _sanity(root)
            # (exit 2, an input that cannot be read: backlog TJ-0353)
            self.assertEqual(code, 2)
            self.assertIsNone(doc, "no clean verdict on unreadable books")
            self.assertIn("work/margin_base.json", err)
            self.assertIn("cannot be read", err)
            self.assertIn("taxjson run", err)
        # The library call says why; the lenient read stays empty.
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "margin_base.json").write_text("{")
            self.assertEqual(PC.load_base_rows(cache, "margin"), [])
            with self.assertRaises(PC.BaseBookError) as cm:
                PC.book_rows(cache, "margin", strict=True)
            self.assertIn("margin_base.json", str(cm.exception))
            # A missing file is still no history, strict or not.
            self.assertEqual(PC.book_rows(cache, "other", strict=True), [])

    def test_b19_nonfinite_inventory_quantity_is_refused(self):
        """B19: Sanity silently discards a nonfinite inventory quantity
        and reports clean — a NaN qty failed every comparison, the
        position vanished and the empty book compared clean against an
        empty snapshot. The books are refused as unreadable."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _sanity_project(root, inventory_qty=float("nan"),
                            snapshots=(("2026-02-15", 0),))
            code, doc, err = _sanity(root)
            # (exit 2, an input that cannot be read: backlog TJ-0353)
            self.assertEqual(code, 2)
            self.assertIsNone(doc, "no clean verdict on a NaN quantity")
            self.assertIn("work/margin_gains.json", err)
            self.assertIn("ZZQ.US", err)
            self.assertIn("is not a finite number", err)


# ------------------------------------------------------------ IB report
def _ib_report(path: Path, *, base="CAD", pay="20260105", withheld="-2",
               withheld_base="-2") -> ibdiv.Report:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Account", "Header", "AccountNumber", "BaseCurrency"])
    w.writerow(["Account", "Data", "U1234567", base])
    w.writerow(["DividendDetail", "Header", "DataDiscriminator", "Currency",
                "Symbol", "Conid", "Country", "ReportDate", "ExDate",
                "Shares", "RevenueComponent", "Gross", "GrossInBase",
                "Withhold", "WithholdInBase"])
    w.writerow(["DividendDetail", "Data", "RevenueComponent", "USD", "ZZQ",
                "1", "US", pay, "20260102", "10", "T5: Foreign Income",
                "10", "10", withheld, withheld_base])
    path.write_text(buf.getvalue())
    return ibdiv.read_report(path)


class TestIBReport(unittest.TestCase):

    def test_b24_missing_base_currency_is_refused(self):
        """B24: IB dividend reports with no base currency silently
        default to CAD — GrossInBase of an unknown denomination was
        labelled CAD. Refused, naming the missing field."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "synthetic.csv"
            with self.assertRaises(ibdiv.IBReportError) as cm:
                _ib_report(p, base="")
            self.assertIn("no BaseCurrency", str(cm.exception))
            self.assertIn("GrossInBase", str(cm.exception))
            # A stated base currency reads as before.
            self.assertEqual(_ib_report(p, base="usd").base_currency, "USD")

    def test_b25_blank_withhold_in_base_against_withholding_is_refused(self):
        """B25: IB dividend reports silently zero missing base-currency
        withholding — a nonzero Withhold with a blank WithholdInBase
        read as 0 withheld, no problem said. Refused like the blank
        GrossInBase, naming the line."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "synthetic.csv"
            with self.assertRaises(ibdiv.IBReportError) as cm:
                _ib_report(p, withheld_base="")
            self.assertIn("line 4", str(cm.exception))
            self.assertIn("blank WithholdInBase", str(cm.exception))
            # Nothing withheld: a blank WithholdInBase is a 0.
            rep = _ib_report(p, withheld="0", withheld_base="")
            self.assertEqual(rep.payments[0].withheld_base, 0.0)
            self.assertEqual(rep.problems, [])

    def test_b26_blank_report_date_is_refused(self):
        """B26: IB dividend report accepts an undated payment that
        cannot enter any tax year — a blank ReportDate gave a payment
        with no year (Report.years empty) whose amounts still counted.
        Refused, naming the line and symbol."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "synthetic.csv"
            with self.assertRaises(ibdiv.IBReportError) as cm:
                _ib_report(p, pay="")
            self.assertIn("line 4", str(cm.exception))
            self.assertIn("ZZQ", str(cm.exception))
            self.assertIn("blank ReportDate", str(cm.exception))
            self.assertEqual(_ib_report(p).years, ["2026"])

    def test_b31_ib_slip_totals_are_scoped_to_the_year(self):
        """B31: IB slip aggregation includes payments from another year
        despite its year argument — a report with a 2025 and a 2026
        foreign payment totalled both for 2026. Only the year's
        payments are on the year's slip; a trust's (T3) payment by its
        ex-date year (CA-INC-DATE-TRUST)."""
        with tempfile.TemporaryDirectory() as td:
            rep = _ib_report(Path(td) / "synthetic.csv")
        other = deepcopy(rep.payments[0])
        other.pay_date = "2025-01-05"
        other.ex_date = "2025-01-02"
        rep.payments.append(other)
        slips = sa.ib_slips(rep, "margin", 2026, {})
        self.assertEqual(slips[0].amounts, {"foreign": 10.0,
                                            "foreign_tax": 2.0})
        # The other year's payment stays in the T5's payment list: the
        # per-payment match names it as another year's.
        self.assertEqual(len(slips[0].payments), 2)
        self.assertEqual(sa.ib_slips(rep, "margin", 2025, {})[0].amounts,
                         {"foreign": 10.0, "foreign_tax": 2.0})
        # No year: every payment, as before.
        self.assertEqual(sa.ib_slips(rep, "margin", None, {})[0].amounts,
                         {"foreign": 20.0, "foreign_tax": 4.0})
        # A trust's December distribution paid in January is on the
        # earlier year's T3 (its record date's year), not the later's.
        trust = ibdiv.Payment(symbol="ZZT", country="CA", currency="CAD",
                              pay_date="2026-01-15", ex_date="2025-12-30",
                              pil=False, components=[ibdiv.Component(
                                  slip="T3", category="eligible",
                                  label="T3: Eligible Dividend Income",
                                  gross=30.0, gross_base=30.0,
                                  withheld=0.0, withheld_base=0.0)])
        rep.payments.append(trust)
        t3_2025 = [s for s in sa.ib_slips(rep, "margin", 2025, {})
                   if s.type == "T3"]
        self.assertEqual([s.amounts for s in t3_2025], [{"ca_div": 30.0}])
        self.assertEqual([s for s in sa.ib_slips(rep, "margin", 2026, {})
                          if s.type == "T3"], [])
        self.assertEqual(sa.slip_year(trust, trust.components[0]), "2025")
        self.assertEqual(sa.slip_year(other, other.components[0]), "2025")


# ------------------------------------------------------------ slip key
class TestSlipKeySalt(unittest.TestCase):

    def test_a43_concurrent_first_salt_creation_returns_one_salt(self):
        """A43: Concurrent slip-key creation returns two incompatible
        salts — two first-time calls each made and returned a salt, the
        last write won, and keys made with the other could never be
        resolved. The salt is made under a lock and read back from the
        file: both calls return the one salt it holds."""
        import secrets
        real = secrets.token_hex
        calls = []

        def slow_first(n):
            calls.append(n)
            if len(calls) == 1:
                # The other call reaches the lock while this one holds it.
                time.sleep(0.5)
            return real(n)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results = {}

            def make(i):
                results[i] = sa.key_salt(root, True)
            threads = [threading.Thread(target=make, args=(i,))
                       for i in (1, 2)]
            with patch.object(secrets, "token_hex", side_effect=slow_first):
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=10)
            on_disk = (root / "work" / sa.KEY_SALT_FILE).read_text().strip()
            self.assertEqual(set(results.values()), {on_disk})
            self.assertEqual(len(calls), 1, "one salt made, the other read")
            self.assertEqual(sa.key_salt(root), on_disk)
            # The lock file is the project's own, beside the salt.
            self.assertTrue((root / "work"
                             / (sa.KEY_SALT_FILE + ".lock")).exists())


# ------------------------------------------------------------ reconcile
class TestReconcileSlips(unittest.TestCase):

    def test_b47_partial_quantity_column_is_not_a_total(self):
        """B47: Slip reconciliation treats a partially populated
        quantity column as a complete total — two 50-proceeds rows, one
        with quantity 5 and one blank, claimed a 5-share mismatch
        against a 10-share disposition. The quantity is not compared
        (said as a note); only a known part above the computed count
        is a mismatch."""
        computed = {"qty": 10, "proceeds_gross": 100, "proceeds_net": 100,
                    "cost": 80}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "slip.csv"
            p.write_text("symbol,quantity,proceeds,cost\n"
                         "ZZQ.US,5,50,40\nZZQ.US,,50,40\n")
            slip = rs.load_slip(p)["ZZQ"]
            self.assertEqual(slip["rows"], 2)
            self.assertEqual(slip["qty_rows"], 1)
            row = rs._compare("ZZQ.US", slip, computed, 0.01)
            self.assertEqual(row["status"], "OK")
            self.assertIn("quantity not compared", row["detail"])
            self.assertIn("1 of 2 slip row(s) state no quantity",
                          row["detail"])
            self.assertNotIn("quantity off by", row["detail"])
            # The known part alone above the computed count: a mismatch.
            p.write_text("symbol,quantity,proceeds,cost\n"
                         "ZZQ.US,15,50,40\nZZQ.US,,50,40\n")
            row = rs._compare("ZZQ.US", rs.load_slip(p)["ZZQ"], computed,
                              0.01)
            self.assertEqual(row["status"], "MISMATCH")
            self.assertIn("exceed the computed 10.0000", row["detail"])
            # Every row with a quantity: compared as before.
            p.write_text("symbol,quantity,proceeds,cost\n"
                         "ZZQ.US,5,50,40\nZZQ.US,4,50,40\n")
            row = rs._compare("ZZQ.US", rs.load_slip(p)["ZZQ"], computed,
                              0.01)
            self.assertEqual(row["status"], "MISMATCH")
            self.assertIn("quantity off by -1.0000", row["detail"])
            # Through reconcile(): the listing split keeps the count.
            p.write_text("symbol,quantity,proceeds,cost\n"
                         "ZZQ.US,5,50,40\nZZQ.US,,50,40\n")
            rep = rs.reconcile(rs.load_slip(p), {"ZZQ": dict(
                computed, listings={"US": dict(computed)})}, 0.01)
            self.assertTrue(rep["clean"])
            self.assertIn("quantity not compared", rep["rows"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
