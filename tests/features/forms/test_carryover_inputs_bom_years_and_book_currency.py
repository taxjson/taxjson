"""Low-round fixes, carryover ledger and filed-year lock (filing-a):
S001-04, S027-18, S027-19, S027-23 (claimed_losses.txt), S027-10 /
S028-00 (per-row rounding of the filed loss), S027-11 / S027-09 (pins),
S028-02 (row currency vs --base-currency); R1-205 / R1-281 (form lines
locked), S031-19 (close-year errors), S031-20 (totals rounded once),
S032-11 (OK says what is not locked). Synthetic data only.

Formerly: test_fix_l_filing_a_co"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from taxjson.bin.taxjson_carryover import build_canada_ledger, main, render
from taxjson.lib import checklist as cl


def tx(date_, qty, net, price, symbol="XEI.TO", currency="CAD"):
    return {"action": "BUYSELL", "date": date_, "date_settle": date_,
            "time": "09:30:00", "symbol": symbol, "quantity": qty,
            "price": price, "net_amount": net, "currency": currency}


# 2023 loss of 5,000; 2024 gain of 3,000.
BOOK = [tx("2022-05-02", 200, 20000.0, 100.0),
        tx("2023-06-01", -100, 5000.0, 50.0),
        tx("2024-06-03", -100, 13000.0, 130.0)]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestClaimedFile(unittest.TestCase):
    """Formerly: test_fix_l_filing_a_co.TestClaimedFile"""

    def _ledger(self, text, *extra, encoding="utf-8"):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": BOOK}))
            c = Path(td) / "claimed_losses.txt"
            c.write_bytes(text.encode(encoding))
            return _run([str(b), "--country", "canada", "--claimed", str(c),
                         "--json", *extra])

    def test_bom_line_is_read(self):
        # S027-18: Notepad's BOM is not part of the year.
        rc, out, err = self._ledger("2024 1500.00\n", encoding="utf-8-sig")
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertAlmostEqual(doc["final_carryforward"], 3500.0)
        self.assertNotIn("line ignored", err)
        self.assertNotIn("claimed_ignored", doc)

    def test_implausible_year_is_not_a_phantom_row(self):
        # S027-23: '2205' and '0' are refused by name, not booked.
        rc, out, err = self._ledger("2205 1500\n0 100\n")
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual([r["year"] for r in doc["rows"]], [2023, 2024])
        self.assertIn("2205 is not a plausible tax year", err)
        self.assertEqual(len(doc["claimed_ignored"]), 2)

    def test_ignored_line_reaches_the_checklist(self):
        # S001-04: the carryover step is attention, not 'present'.
        rc, out, err = self._ledger("2024 1,50.00\n")
        doc = json.loads(out)
        self.assertEqual(doc["claimed_ignored"],
                         ["claimed_losses.txt:1: 2024 1,50.00"])

        class Sub:
            def __call__(self, argv, timeout=900):
                return 0, out, err
        with tempfile.TemporaryDirectory() as td:
            ctx = cl.Ctx(root=Path(td), cfg={"settings": {"year": 2024,
                                                          "country": "canada"},
                                             "accounts": {}},
                         year=2024, today=date(2025, 5, 1), run_sub=Sub())
            r = cl.d_carryover(ctx)
        self.assertEqual(r.status, "attention")
        self.assertIn("1 claimed line(s) ignored", r.detail)

    def test_ignored_line_is_in_the_text_report(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": BOOK}))
            c = Path(td) / "claimed_losses.txt"
            c.write_text("2024 abc\n")
            rc, out, err = _run([str(b), "--country", "canada",
                                 "--claimed", str(c)])
        self.assertIn("1 claimed line(s) could not be read and are NOT "
                      "applied", out)

    def test_directory_is_a_one_line_error(self):
        # S027-19
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": BOOK}))
            rc, out, err = _run([str(b), "--country", "canada",
                                 "--claimed", td])
        self.assertEqual(rc, 2)
        self.assertIn("cannot read claimed file", err)
        self.assertNotIn("Traceback", err)


class TestRowCurrency(unittest.TestCase):
    """S028-02: a native USD book is refused under --base-currency CAD.

    Formerly: test_fix_l_filing_a_co.TestRowCurrency"""

    def test_usd_book_refused(self):
        book = [tx("2024-01-02", 100, 10000.0, 100.0, "XYZ.US", "USD"),
                tx("2025-01-02", -100, 5000.0, 50.0, "XYZ.US", "USD")]
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "raw.json"
            b.write_text(json.dumps({"transactions": book}))
            rc, out, err = _run([str(b), "--country", "canada"])
            self.assertEqual(rc, 2)
            self.assertIn("rows in USD but --base-currency is CAD", err)
            rc, out, err = _run([str(b), "--country", "usa",
                                 "--base-currency", "USD", "--json"])
        self.assertEqual(rc, 0, err)


class TestNotePins(unittest.TestCase):
    """S027-11: the rendered note amounts.

    Formerly: test_fix_l_filing_a_co.TestNotePins"""

    def test_carryback_and_apply_amounts(self):
        nets = {2022: {"net": 10000.0, "dispositions": 1},
                2023: {"net": -4000.0, "dispositions": 1},
                2024: {"net": 2500.0, "dispositions": 1}}
        text = render(build_canada_ledger(nets, {}), "CAD", 2021, False)
        self.assertIn("4,000.00 can be carried back to 2022 via T1A", text)
        self.assertIn("carryforward available: apply up to 2,500.00", text)


if __name__ == "__main__":
    unittest.main()
