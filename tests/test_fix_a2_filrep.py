"""Re-audit-2 fixes, filing-reports list (form-export / sum FOR THE
RETURN / carryover currency): §1256 contracts off Form 8949, Schedule 3
footing cells, rebates, currency guards, manual rows, partial year,
footers and the work/ row funnel.

All data is synthetic (fake account names, invented tickers).
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule

from taxjson.bin import taxjson_form_export as FE

REPO_ROOT = Path(__file__).resolve().parent.parent


def us(symbol, proceeds, cost, gain=None, disallowed=0.0, term="SHORT_TERM",
       date_="2025-10-24", **extra):
    e = {"date": date_, "date_settle": date_, "symbol": symbol, "qty": -1,
         "proceeds": proceeds, "cost": cost,
         "gain": (proceeds - cost + disallowed) if gain is None else gain,
         "disallowed_amount": disallowed, "days_held": 4, "term": term,
         "direction": "LONG", "commission": 0.0, "fee": 0.0,
         "account": "margin", "currency": "USD", "is_option": False}
    e.update(extra)
    return e


def ca(symbol, proceeds, cost, gain=None, disallowed=0.0, commission=0.0,
       fee=0.0, date_="2025-06-10", qty=-10, direction="LONG", **extra):
    e = {"date": date_, "date_settle": date_, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost,
         "gain": (proceeds - cost + disallowed) if gain is None else gain,
         "disallowed_amount": disallowed, "days_held": 30, "term": None,
         "direction": direction, "commission": commission, "fee": fee,
         "account": "margin", "currency": "CAD", "is_option": False}
    e.update(extra)
    return e


def _main(argv):
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = FE.main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _write(td, name, rows, **doc):
    p = Path(td) / name
    p.write_text(json.dumps({"summary": {"year": 2025}, "transactions": rows,
                             **doc}))
    return p


def _project(td, country="canada", year=2025):
    root = Path(td)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{"USD" if country == "usa" else "CAD"}"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "work").mkdir()
    return root


def _cli(root, *a):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *a], cwd=REPO_ROOT,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


FUT_GAIN = us("F:CLZ5.US", 3000.0, 0.0)
FUT_LOSS = us("F:CLK6.US", -2000.0, 0.0)
SPX = us("SPX251219C06000000.US", 4998.0, 0.0, is_option=True)
FOPT = us("F:ES251219C06000000", 500.0, 100.0, is_option=True)
AAPL = us("AAPL.US", 1200.0, 1000.0)


class TestSection1256Off8949(unittest.TestCase):
    """A2-0118, A2-0322, A2-0323, A2-0324."""

    @rule("US-FUT-02", "US-OPT-04")
    def test_build_8949_lists_1256_for_form_6781(self):
        rep = FE.build_8949([FUT_GAIN, FUT_LOSS, SPX, FOPT, AAPL])
        self.assertEqual([r["description"] for r in rep["part_I"]],
                         ["1 AAPL.US"])
        self.assertEqual(rep["part_I_totals"]["proceeds"], 1200.0)
        kinds = {r["description"]: r["kind"] for r in rep["section_1256"]}
        self.assertEqual(kinds, {"F:CLZ5.US": "future",
                                 "F:CLK6.US": "future",
                                 "SPX251219C06000000.US": "index option",
                                 "F:ES251219C06000000": "futures option"})
        self.assertEqual(rep["section_1256_totals"],
                         {"gain": 6398.0, "dispositions": 4})
        # No negative proceeds anywhere (the futures loss went on (d)).
        for part in ("part_I", "part_II"):
            self.assertTrue(all(r["proceeds"] >= 0 for r in rep[part]))

    @rule("US-FUT-02", "US-OPT-04")
    def test_cli_8949_and_txf(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(td, "margin_gains.json",
                       [FUT_GAIN, FUT_LOSS, SPX, AAPL])
            rc, out, err = _main([str(g), "--form", "8949", "--country",
                                  "usa", "--year", "2025"])
            self.assertEqual(rc, 0, err)
            body, _, tail = out.partition("FORM 6781 BY HAND")
            self.assertNotIn("F:CL", body)
            self.assertNotIn("SPX", body)
            self.assertIn("F:CLK6.US", tail)
            self.assertIn("-2,000.00", tail)
            self.assertIn("Form 6781", err)
            rc, out, err = _main([str(g), "--form", "txf", "--country",
                                  "usa", "--year", "2025"])
            self.assertEqual(rc, 0, err)
            self.assertNotIn("F:CL", out)
            self.assertNotIn("SPX", out)
            self.assertIn("PAAPL", out.replace("P1 AAPL", "PAAPL"))
            self.assertIn("NOT in the TXF records", err)

    @rule("US-FUT-02")
    def test_sum_for_the_return_leaves_1256_out(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa")
            (root / "work" / "margin_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025},
                 "transactions": [FUT_GAIN, FUT_LOSS, AAPL]}))
            r = _cli(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            f = json.loads(r.stdout)["filing"]
            self.assertEqual(f["totals"]["proceeds"], 1200.0)
            self.assertEqual(f["totals"]["gain"], 200.0)
            self.assertEqual(f["totals"]["section_1256_gain"], 1000.0)
            self.assertEqual(f["engine_gain_unrounded"], 200.0)
            t = _cli(root, "sum")
            ret = t.stdout.split("FOR THE RETURN")[1]
            self.assertIn("Form 6781 by hand", ret)
            self.assertNotIn("Rows are rounded", ret)

    @rule("US-FUT-02")
    def test_lock_records_the_6781_total(self):
        from taxjson.bin.taxjson_filed import form_lines
        lines = form_lines([FUT_GAIN, AAPL])
        self.assertEqual(lines["I_proceeds"], 1200.0)
        self.assertEqual(lines["6781_gain"], 3000.0)


class TestScheduleThreeCells(unittest.TestCase):
    """A2-0649, A2-1107, A2-1104 (Schedule 3) and A2-1105 (8949)."""

    @rule("CA-DISP-08")
    def test_premium_only_write_has_no_negative_acb(self):
        # Grant-timing write: premium 29.652 gross, commission 14.826,
        # gain 14.826 -> separately rounded 29.65 / 14.83 / 14.83.
        w = ca("ABC250815P00045000.US", 0.0, -14.826, gain=14.826,
               commission=14.826, direction="SHORT", grant=True,
               is_option=True, qty=-1)
        rep = FE.build_schedule3([w], 2025)
        row = rep["rows"][0]
        self.assertGreaterEqual(row["acb"], 0.0)
        self.assertAlmostEqual(row["proceeds"] - row["acb"] - row["outlays"],
                               row["gain"], places=6)
        self.assertGreaterEqual(rep["lines"][0]["acb"], 0.0)

    @rule("CA-DISP-08")
    def test_filing_totals_acb_is_never_a_negative_residual(self):
        # net 9.02, fee 0.025 -> proceeds 9.045, outlays 0.025, gain 9.02
        rows = [ca(f"Z{i}.TO", 9.02, 0.0, fee=0.025) for i in range(3)]
        t = FE.filing_totals(rows, 2025)
        self.assertGreaterEqual(t["acb"], 0.0)
        self.assertAlmostEqual(t["proceeds"] - t["acb"] - t["outlays"],
                               t["gain"], places=6)
        lines = FE.filing_lines(rows, 2025)
        self.assertGreaterEqual(lines[0]["acb"], 0.0)

    @rule("CA-DISP-08")
    def test_half_cent_acb_matches_the_sum_report(self):
        # buy 2 for 2,000.00, sell for 939.15, half the loss denied:
        # adjusted ACB 1,469.575 -> 1,469.58 (the .sum's TOTAL COST).
        e = ca("AAA.TO", 939.15, 2000.0, gain=-530.425, disallowed=530.425,
               qty=-2)
        row = FE.build_schedule3([e], 2025)["rows"][0]
        self.assertEqual(row["acb"], 1469.58)
        self.assertEqual(row["proceeds"], 939.15)
        self.assertEqual(round(row["proceeds"] - row["acb"] - row["outlays"],
                               2), row["gain"])

    @rule("US-RPT-09")
    def test_8949_half_cent_adjustment_rounds_half_up(self):
        e = us("AAA.US", 939.15, 2000.0, gain=-530.425, disallowed=530.425)
        row = FE.build_8949([e])["part_I"][0]
        self.assertEqual(row["adjustment"], 530.43)
        self.assertEqual(row["gain"], -530.42)


class TestRebatesAreNotOutlays(unittest.TestCase):
    """A2-0653 (IB rebate), A2-1053 (Questrade rebate)."""

    @rule("CA-DISP-08")
    def test_questrade_rebate_stays_in_proceeds(self):
        # Gross 1,200, a 0.75 rebate: net 1,200.75, commission -0.75.
        e = ca("RBT.TO", 1200.75, 999.50, commission=-0.75)
        row = FE.build_schedule3([e], 2025)["rows"][0]
        self.assertEqual(row["outlays"], 0.0)
        self.assertEqual(row["proceeds"], 1200.75)
        self.assertEqual(row["acb"], 999.50)
        self.assertEqual(row["gain"], 201.25)

    @rule("CA-DISP-08", "CA-DISP-06")
    def test_ib_rebate_on_a_grant_write(self):
        # A write whose IB Comm/Fee is a +0.70 rebate (fee -0.70).
        w = ca("KVX251212P00085000.US", 0.0, -94.70, gain=94.70,
               fee=-0.70, direction="SHORT", grant=True, is_option=True,
               qty=-1)
        row = FE.build_schedule3([w], 2025)["rows"][0]
        self.assertEqual(row["outlays"], 0.0)
        self.assertEqual(row["proceeds"], 94.70)
        self.assertEqual(row["gain"], 94.70)
        # A real commission still splits out gross (R1-40 unchanged).
        w2 = ca("KVX251212P00085000.US", 0.0, -92.70, gain=92.70,
                commission=1.30, direction="SHORT", grant=True,
                is_option=True, qty=-1)
        row = FE.build_schedule3([w2], 2025)["rows"][0]
        self.assertEqual((row["proceeds"], row["outlays"]), (94.0, 1.30))


class TestCurrencyGuards(unittest.TestCase):
    """A2-0652, A2-1118."""

    @rule("CA-CTRY-03")
    def test_schedule3_refuses_a_non_cad_base(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(td, "g.json", [ca("XYZ.US", 10000.0, 9000.0,
                                         currency="USD")])
            rc, out, err = _main([str(g), "--form", "schedule3",
                                  "--country", "canada", "--year", "2025",
                                  "--base-currency", "USD"])
            self.assertEqual(rc, 2)
            self.assertIn("CAD", err)
            self.assertEqual(out, "")

    @rule("CA-CTRY-03")
    def test_row_with_no_currency_is_said(self):
        with tempfile.TemporaryDirectory() as td:
            nc = ca("XYZ.TO", 10000.0, 9000.0)
            del nc["currency"]
            g = _write(td, "g.json", [nc, ca("ABC.TO", 10000.0, 9000.0)])
            rc, _out, err = _main([str(g), "--form", "schedule3",
                                   "--country", "canada", "--year", "2025",
                                   "--json"])
            self.assertEqual(rc, 0, err)
            self.assertIn("1 disposition(s) carry no currency", err)

    @rule("US-CTRY-03")
    def test_8949_refuses_a_cad_base(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(td, "g.json", [us("XYZ.US", 1.0, 0.0,
                                         currency="CAD")])
            rc, _out, err = _main([str(g), "--form", "8949", "--country",
                                   "usa", "--base-currency", "CAD"])
            self.assertEqual(rc, 2)
            self.assertIn("USD", err)

    @rule("US-CTRY-03")
    def test_us_carryover_refuses_cad_books(self):
        from taxjson.bin import taxjson_carryover as CO
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2025-01-02",
                 "date_settle": "2025-01-03", "time": "10:00:00",
                 "symbol": "XYZ.TO", "quantity": 10, "price": 10.0,
                 "net_amount": -100.0, "currency": "CAD",
                 "account": "m"}]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    rc = CO.main([str(b), "--country", "usa",
                                  "--base-currency", "CAD"])
                except SystemExit as e:
                    rc = e.code
            self.assertEqual(rc, 2)
            self.assertIn("USD", err.getvalue())


class TestManualRowsOnce(unittest.TestCase):
    """A2-0113: a crypto file passed positionally AND as --crypto."""

    @rule("CA-DISP-03")
    def test_crypto_manual_row_listed_once(self):
        with tempfile.TemporaryDirectory() as td:
            man = {"symbol": "BTC", "date": "2025-07-10",
                   "date_settle": "2025-07-10", "qty": -1.0,
                   "proceeds": 148500.0, "account": "kr1"}
            g = _write(td, "kr1_gains.json", [],
                       manual_reporting_required=[man])
            rc, out, err = _main([str(g), "--crypto", str(g), "--form",
                                  "schedule3", "--country", "canada",
                                  "--year", "2025", "--json"])
            self.assertEqual(rc, 0, err)
            rep = json.loads(out)
            self.assertEqual(len(rep["manual_reporting_required"]), 1)
            self.assertEqual(rep["manual_proceeds"], 148500.0)
            self.assertIn("1 tainted", err)


class TestRoundingNote(unittest.TestCase):
    """A2-1108: form-export says what sum says about per-row rounding."""

    @rule("CA-DISP-08")
    def test_schedule3_note(self):
        with tempfile.TemporaryDirectory() as td:
            rows = [ca(f"Q{i}.TO", 100.004, 0.0) for i in range(100)]
            g = _write(td, "g.json", rows)
            rc, out, err = _main([str(g), "--form", "schedule3",
                                  "--country", "canada", "--year", "2025"])
            self.assertEqual(rc, 0, err)
            self.assertIn("unrounded total gain is 10,000.40", out)

    @rule("US-RPT-09")
    def test_8949_note(self):
        with tempfile.TemporaryDirectory() as td:
            rows = [us(f"Q{i}.US", 100.004, 0.0) for i in range(100)]
            g = _write(td, "g.json", rows)
            rc, out, err = _main([str(g), "--form", "8949", "--country",
                                  "usa", "--year", "2025"])
            self.assertEqual(rc, 0, err)
            self.assertIn("unrounded total gain is 10,000.40", out)


class TestYearNotEnded(unittest.TestCase):
    """A2-1103."""

    def test_note_only_before_the_year_ends(self):
        self.assertIn("has not ended",
                      FE.year_not_ended_note(2026, date(2026, 10, 2)))
        self.assertIn("has not ended",
                      FE.year_not_ended_note(2026, date(2026, 12, 31)))
        self.assertEqual(FE.year_not_ended_note(2025, date(2026, 1, 1)), "")
        self.assertEqual(FE.year_not_ended_note(None), "")

    @rule("CA-DISP-03")
    def test_schedule3_and_sum_say_year_to_date(self):
        y = date.today().year
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, year=y)
            e = ca("XYZ.TO", 100.0, 50.0, date_=f"{y}-01-15")
            (root / "work" / "margin_gains.json").write_text(json.dumps(
                {"summary": {"year": y}, "transactions": [e]}))
            r = _cli(root, "form-export")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("YEAR-TO-DATE", r.stdout)
            r = _cli(root, "sum")
            self.assertIn("YEAR-TO-DATE", r.stdout.split("FOR THE RETURN")[1])


class TestReturnFooters(unittest.TestCase):
    """A2-0647 (US IRA carve-out) and A2-0659 (Canada affiliated)."""

    @rule("US-WASH-11")
    def test_us_footer_names_the_ira_denial(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa")
            e = us("XYZ.US", 800.0, 1000.0, disallowed=200.0,
                   permanently_disallowed=200.0)
            (root / "work" / "margin_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025}, "transactions": [e]}))
            r = _cli(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            f = json.loads(r.stdout)["filing"]
            self.assertEqual(f["totals"]["permanently_denied"], 200.0)
            t = _cli(root, "sum").stdout.split("FOR THE RETURN")[1]
            self.assertIn("IRA, which is lost for good", t)

    @rule("CA-SL-09")
    def test_canada_footer_affiliated_wording(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            e = ca("XYZ.TO", 900.0, 1000.0, disallowed=100.0,
                   permanently_disallowed=100.0)
            (root / "work" / "margin_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025}, "transactions": [e]}))
            t = _cli(root, "sum").stdout.split("FOR THE RETURN")[1]
            t = " ".join(t.split())
            self.assertIn("affiliated person's acquisition is permanent "
                          "for this return (that person adds it to their "
                          "own ACB", t)
            self.assertNotIn("(affiliated) acquisition is lost for good", t)
            with tempfile.TemporaryDirectory() as td2:
                g = _write(td2, "g.json", [e])
                _rc, out, _err = _main([str(g), "--form", "schedule3",
                                        "--country", "canada"])
                notes = out.split("Notes:")[1]
                self.assertIn("affiliated-person", notes)


class TestRowFunnel(unittest.TestCase):
    """A2-0330: a wrong-typed field in a work/ row is a one-line error
    naming the file and row, never a traceback."""

    def test_form_export_int_date(self):
        with tempfile.TemporaryDirectory() as td:
            e = ca("XYZ.TO", 100.0, 50.0)
            e["date"] = 20250310
            g = _write(td, "g.json", [e])
            rc, _out, err = _main([str(g), "--form", "schedule3",
                                   "--country", "canada"])
            self.assertEqual(rc, 2)
            self.assertIn("row 0", err)
            self.assertNotIn("Traceback", err)

    def test_read_work_doc_checks_numbers(self):
        from taxjson.lib.json_input import InputFileError, read_work_doc
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_base.json"
            p.write_text(json.dumps({"transactions": [
                {"date": "2025-01-02", "symbol": "X", "quantity": "abc"}]}))
            with self.assertRaises(InputFileError) as cm:
                read_work_doc(p)
            self.assertIn("quantity", str(cm.exception))
            p.write_text(json.dumps({"transactions": [
                {"date": "2025-01-02", "symbol": "X", "quantity": 1,
                 "price": None}]}))
            read_work_doc(p)

    def test_view_gives_one_line_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [
                    {"action": "BUYSELL", "date": 20250310,
                     "symbol": "XYZ.TO", "quantity": 1.0,
                     "net_amount": -10.0, "account": "margin"}]}))
            (root / "work" / "margin_raw.json").write_text(
                (root / "work" / "margin_base.json").read_text())
            r = _cli(root, "trades")
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("Traceback", r.stderr + r.stdout)


class TestDocstrings(unittest.TestCase):
    """A2-0643."""

    def test_usage_has_country_and_short_convention(self):
        doc = FE.__doc__
        self.assertNotIn("absolute amounts", doc)
        for line in doc.splitlines():
            if line.strip().startswith("taxjson-form-export --form"):
                self.assertIn("--country", line)
        self.assertNotIn("|amounts|", FE.filing_totals.__doc__)


if __name__ == "__main__":
    unittest.main()
