"""Batch D of the external audit: IB's dividends report reader
(lib/ib_dividends) and the CRA slip page's text parser (lib/cra_slips).

- #54: a payment's components are checked against its Summary row for
  the tax withheld and the base-currency amounts too, not only Gross.
- #55: a component with a Gross and a blank GrossInBase is refused
  (read as 0, it dropped out of a CAD-base account's slip boxes).
- #56: an impossible date (20250230) is refused naming the line, and
  `tjs slip-audit` says so in an Error: line, not a traceback.
- #90: a malformed comma ('1,5') is refused, not read as 15.
- #111: a known amount box whose value has a digit but is not an
  amount refuses the slip; a wrapped label word is still skipped.

Synthetic data only (a made-up account U1234567, ticker SAMP).
"""
import csv
import io
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from test_slip_audit import _home, _ib_project, _ib_report, tj

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_HDR = ["DataDiscriminator", "Currency", "Symbol", "Conid", "Country",
        "ReportDate", "ExDate", "Shares", "RevenueComponent", "Gross",
        "GrossInBase", "Withhold", "WithholdInBase"]


def _report(components, summary=("100", "100", "-15", "-15"),
            day="20250203", shares="10"):
    """A one-payment report: the Summary row (Gross, GrossInBase,
    Withhold, WithholdInBase) on line 4, its components from line 5."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Account", "Header", "AccountNumber", "BaseCurrency"])
    w.writerow(["Account", "Data", "U1234567", "CAD"])
    w.writerow(["DividendDetail", "Header", *_HDR])
    w.writerow(["DividendDetail", "Data", "Summary", "CAD", "SAMP", "1",
                "CA", day, "20250130", shares, "", *summary])
    for c in components:
        w.writerow(["DividendDetail", "Data", "RevenueComponent", "CAD",
                    "SAMP", "1", "CA", day, "20250130", "", *c])
    return out.getvalue()


def _read(text):
    from taxjson.lib.ib_dividends import read_report
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "U1234567.2025.dividends.csv"
        p.write_text(text)
        return read_report(p)


_ELIG = "T5: Eligible Dividend Income"


class TestSummaryReconciled(unittest.TestCase):
    """#54."""

    def test_withheld_and_base_amounts_checked(self):
        rep = _read(_report([(_ELIG, "100", "100", "0", "0")]))
        self.assertEqual(rep.payments[0].withheld, 0.0)
        self.assertEqual(rep.problems, [
            "SAMP 2025-02-03: the components add up to 0.00 CAD "
            "withheld, the payment to 15.00",
            "SAMP 2025-02-03: the components add up to 0.00 CAD "
            "withheld (WithholdInBase), the payment to 15.00"])
        rep = _read(_report([(_ELIG, "100", "90", "-15", "-15")]))
        self.assertEqual(rep.problems, [
            "SAMP 2025-02-03: the components add up to 90.00 CAD "
            "(GrossInBase), the payment to 100.00"])

    def test_consistent_report_has_no_problem(self):
        rep = _read(_report([(_ELIG, "60", "60", "-9", "-9"),
                             ("T5: Capital Gains", "40", "40", "-6",
                              "-6")]))
        self.assertEqual(rep.problems, [])
        # A cent per component of IB's base rounding is not one.
        rep = _read(_report([(_ELIG, "60", "60.01", "-9", "-9"),
                             ("T5: Capital Gains", "40", "40.01", "-6",
                              "-6")]))
        self.assertEqual(rep.problems, [])
        self.assertEqual(_read(_ib_report()).problems, [])


class TestBlankGrossInBase(unittest.TestCase):
    """#55."""

    def test_refused_naming_file_and_line(self):
        from taxjson.lib.ib_dividends import IBReportError
        with self.assertRaises(IBReportError) as cm:
            _read(_report([(_ELIG, "100", "", "0", "0")],
                          summary=("100", "100", "0", "0")))
        msg = str(cm.exception)
        self.assertIn("U1***", msg)
        self.assertNotIn("U1234567", msg)
        self.assertIn("line 5", msg)
        self.assertIn("blank GrossInBase", msg)

    def test_blank_withhold_is_zero(self):
        rep = _read(_report([(_ELIG, "100", "100", "", "")],
                            summary=("100", "100", "", "")))
        (p,) = rep.payments
        self.assertEqual((p.gross_base, p.withheld, p.withheld_base),
                         (100.0, 0.0, 0.0))
        self.assertEqual(rep.problems, [])
        # A zero Gross (a withholding-only row) needs no base amount.
        rep = _read(_report([("Other", "0", "", "-1", "-1")],
                            summary=("0", "0", "-1", "-1")))
        self.assertEqual(rep.payments[0].withheld, 1.0)


class TestImpossibleDate(unittest.TestCase):
    """#56."""

    def test_reader_refuses(self):
        from taxjson.lib.ib_dividends import IBReportError
        with self.assertRaises(IBReportError) as cm:
            _read(_report([(_ELIG, "100", "100", "0", "0")],
                          summary=("100", "100", "0", "0"),
                          day="20250230"))
        msg = str(cm.exception)
        self.assertIn("line 4", msg)
        self.assertIn("'20250230' is not a calendar day", msg)
        rep = _read(_report([(_ELIG, "100", "100", "0", "0")],
                            summary=("100", "100", "0", "0"),
                            day="20240229"))
        self.assertEqual(rep.payments[0].pay_date, "2024-02-29")

    def test_slip_audit_shows_an_error_line(self):
        # A USD-base report: before, its conversion raised ValueError
        # from date.fromisoformat and the CLI printed a traceback.
        usd = _ib_report().replace(",CAD,\n", ",USD,\n", 1)
        usd = usd.replace(",ZZQ,111,CA,20250314,", ",ZZQ,111,CA,20250230,")
        self.assertIn("20250230", usd)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp, report=usd)
            tj(root, home, "run", "--no-input")
            r = tj(root, home, "slip-audit")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("tjs slip-audit: error: ", r.stderr)
        self.assertIn("line 4: date '20250230' is not a calendar day",
                      r.stderr)


class TestStrictNumbers(unittest.TestCase):
    """#90."""

    def test_malformed_comma_refused(self):
        from taxjson.lib.ib_dividends import IBReportError
        with self.assertRaises(IBReportError) as cm:
            _read(_report([(_ELIG, "1,5", "1,5", "0", "0")],
                          summary=("1,5", "1,5", "0", "0")))
        msg = str(cm.exception)
        self.assertIn("line 4", msg)
        self.assertIn("Gross '1,5'", msg)

    def test_thousands_comma_reads(self):
        rep = _read(_report([(_ELIG, "1,500.25", "1,500.25", "0", "0")],
                            summary=("1,500.25", "1,500.25", "0", "0"),
                            shares="1,000"))
        (p,) = rep.payments
        self.assertEqual((p.gross, p.gross_base, p.shares),
                         (1500.25, 1500.25, 1000.0))
        self.assertEqual(rep.problems, [])


def _page(rows):
    body = ("  2025 T5 slip (original) from SAMPLE BANK\n\n"
            "    Box        Box name                  Box value\n"
            "    number\n")
    return body + "".join(f"    {ln}\n" for ln in rows)


class TestCraMalformedBox(unittest.TestCase):
    """#111."""

    def test_digit_value_not_an_amount_refused(self):
        from taxjson.lib.cra_slips import CraSlipError, parse_slips
        text = _page(["13         Interest from Canadian sources    100.00",
                      "15         Foreign income                    1,50.00"])
        with self.assertRaises(CraSlipError) as cm:
            parse_slips(text, "x.pdf")
        msg = str(cm.exception)
        self.assertIn("box 15", msg)
        self.assertIn("cannot be read cleanly; not imported", msg)

    def test_wrapped_label_word_still_skipped(self):
        from taxjson.lib.cra_slips import parse_slips
        text = _page([
            "                Actual amount of dividends other than",
            "10                                               1,000.25",
            "                eligible dividends",
            "24         Actual amount of eligible",
            "13         Interest from Canadian sources    12.34"])
        (s,) = parse_slips(text, "x.pdf")
        self.assertEqual(s.boxes, {"10": 1000.25, "13": 12.34})


if __name__ == "__main__":
    unittest.main()
