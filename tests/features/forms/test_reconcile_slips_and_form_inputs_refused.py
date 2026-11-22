"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_reconcile_slips as RS
from taxjson.lib import slip_symbols, tax_forms
import _paths
from _support.filing_bin_reports import _run, _sell, _gains, _slip, _g

REPO = _paths.REPO


# ---------------------------------------------------------------------------
# reconcile-slips
# ---------------------------------------------------------------------------

class TestReconcileSlipCurrency(unittest.TestCase):
    """R1-20: the slip's Box 13 currency was ignored, so Webull's USD
    T5008 gave '0 OK, 267 mismatch' (every amount off by the FX rate).

    Formerly: test_fix_l_filing_b.TestReconcileSlipCurrency"""

    def test_foreign_currency_slip_is_refused_naming_box_13(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, "symbol,quantity,proceeds,box 13\n"
                          "AAPL,100,12000,USD\n")
            rc, out, err = _run(RS.main, ["--country", "canada", str(s), "--gains", str(g),
                                          "--country", "canada"])
        self.assertEqual(rc, 2, out + err)
        self.assertIn("Box 13", err)
        self.assertIn("USD", err)
        self.assertIn("CAD", err)
        self.assertNotIn("MISMATCH", out)

    def test_blank_or_base_currency_reconciles(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, "symbol,quantity,proceeds,currency\n"
                          "AAPL,50,6000,CAD\nAAPL,50,6000,\n")
            rc, out, err = _run(RS.main, ["--country", "canada", str(s), "--gains", str(g),
                                          "--country", "canada"])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("1 OK", out)

    def test_usd_slip_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, "symbol,quantity,proceeds,currency\n"
                          "AAPL,100,12000,USD\n")
            rc, out, err = _run(RS.main, ["--country", "canada", str(s), "--gains", str(g),
                                          "--country", "usa",
                                          "--date-basis", "trade"])
        self.assertEqual(rc, 0, out + err)


class TestReconcileSlipUnreadableCells(unittest.TestCase):
    """R1-335 / S035-19 / S036-07: an unreadable (decimal-comma, 'nan',
    unit-suffixed) cost cell was dropped silently — the cost comparison
    vanished or was computed from a partial sum, at exit 0.

    Formerly: test_fix_l_filing_b.TestReconcileSlipUnreadableCells"""

    def _rc(self, slip_text):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell(qty=-200, proceeds=18000.0,
                                  cost=18000.0, commission=0.0)])
            s = _slip(td, slip_text)
            return _run(RS.main, ["--country", "canada", str(s), "--gains", str(g), "--json"])

    def test_decimal_comma_proceeds_is_unreadable_not_100x(self):
        rc, out, err = self._rc('symbol,quantity,proceeds\n'
                                'AAPL,200,"18000,00"\n')
        self.assertEqual(rc, 1)
        self.assertIn("unreadable proceeds", err)
        self.assertNotIn("1,800,000", out)

    def test_unreadable_cost_cell_fails_the_check(self):
        for bad in ('"9000,00"', "nan", "1 500.00 CAD"):
            with self.subTest(cost=bad):
                rc, out, err = self._rc(
                    "symbol,quantity,proceeds,cost\n"
                    f"AAPL,100,9000.00,{bad}\nAAPL,100,9000.00,9000.00\n")
                self.assertEqual(rc, 1, out + err)
                self.assertIn("unreadable cost", err)
                rep = json.loads(out)
                self.assertEqual(rep["unreadable_rows"], 1)
                self.assertFalse(rep["clean"])

    def test_nan_quantity_fails_the_check(self):
        rc, out, err = self._rc("symbol,quantity,proceeds\n"
                                "AAPL,nan,18000.00\n")
        self.assertEqual(rc, 1)
        self.assertIn("unreadable quantity", err)


class TestReconcileSlipUnreadableFile(unittest.TestCase):
    """S036-09: a directory, and a CSV neither UTF-8 nor cp1252 can
    decode, crashed with tracebacks.

    Formerly: test_fix_l_filing_b.TestReconcileSlipUnreadableFile"""

    def test_directory_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            rc, out, err = _run(RS.main, ["--country", "canada", td, "--gains", str(g)])
        self.assertEqual(rc, 2)
        self.assertIn("not a file", err)
        self.assertNotIn("Traceback", err)

    def test_undecodable_bytes_are_read(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, b"symbol,quantity,proceeds\n"
                          b"AAPL,100,11990\n\x81junk,,\n")
            rc, out, err = _run(RS.main, ["--country", "canada", str(s), "--gains", str(g)])
        self.assertNotIn("Traceback", err)
        self.assertIn("AAPL", out)


class TestReconcileSlipTolerance(unittest.TestCase):
    """S036-00: --tolerance accepted nan / negative / inf (every symbol a
    MISMATCH 'off by +0.00'; nan hid the cost note); the wrapper forwarded
    '-inf' as its own token.

    Formerly: test_fix_l_filing_b.TestReconcileSlipTolerance"""

    def test_standalone_refuses_non_finite_or_negative(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, "symbol,quantity,proceeds\nAAPL,100,12000\n")
            for bad in ("nan", "-1", "inf", "-inf"):
                with self.subTest(tol=bad):
                    rc, out, err = _run(RS.main, ["--country", "canada", 
                        str(s), "--gains", str(g), f"--tolerance={bad}"])
                    self.assertEqual(rc, 2, out + err)
            rc, out, err = _run(RS.main, ["--country", "canada", str(s), "--gains", str(g),
                                          "--tolerance=0"])
            self.assertEqual(rc, 0, out + err)

    def test_wrapper_refuses_and_forwards_as_one_token(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _gains(root / "work", [_sell()])
            s = _slip(td, "symbol,quantity,proceeds\nAAPL,100,12000\n")
            env = {**os.environ, "TAXJSON_OFFLINE": "1",
                   "PYTHONPATH": str(REPO / "src")}
            bad = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "reconcile-slips", str(s), "--tolerance=-inf"],
                capture_output=True, text=True, env=env)
            ok = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "reconcile-slips", str(s), "--tolerance=0.5"],
                capture_output=True, text=True, env=env)
        self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
        self.assertIn("tolerance", bad.stderr)
        self.assertNotIn("expected one argument", bad.stderr)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("tolerance ±0.50", ok.stdout)


class TestReconcileSlipHelp(unittest.TestCase):
    """S035-24: --help pointed at 'header docs' it never showed.

    Formerly: test_fix_l_filing_b.TestReconcileSlipHelp"""

    def test_help_lists_the_accepted_spellings(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            RS.main(["--help"])
        text = out.getvalue()
        for spelling in ("box 16", "box 21", "box 20", "box 13"):
            self.assertIn(spelling, text)


class TestReconcileSlipPins(unittest.TestCase):
    """S035-22 / S035-23: the computed-side outlays and quantity
    accumulators and the note's signed cost difference and tainted count
    were unpinned (mutants survived).

    Formerly: test_fix_l_filing_b.TestReconcileSlipPins"""

    def test_two_lots_with_commission_and_fee(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [
                _sell(qty=-100, proceeds=1000.0, cost=800.0,
                      commission=5.0, fee=2.0),
                _sell(qty=-50, proceeds=600.0, cost=400.0,
                      commission=3.0, fee=1.0, date="2025-06-02")])
            c = slip_symbols.load_computed([g], 2025)["AAPL"]
        self.assertAlmostEqual(c["qty"], 150.0)
        self.assertAlmostEqual(c["proceeds_net"], 1600.0)
        self.assertAlmostEqual(c["proceeds_gross"], 1611.0)
        self.assertAlmostEqual(c["cost"], 1200.0)
        self.assertEqual(c["rows"], 2)

    def test_cost_note_sign_and_tainted_count(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell(cost=10000.0, tainted=True)])
            computed = slip_symbols.load_computed([g], 2025)
        slip = {"AAPL": {"qty": 100.0, "proceeds": 12000.0,
                         "cost": 9000.0, "rows": 1, "listings": {}}}
        rep = RS.reconcile(slip, computed, 1.0)
        detail = rep["rows"][0]["detail"]
        self.assertIn("slip cost differs by -1,000.00", detail)
        self.assertIn("1 disposition(s) with an unknown cost", detail)


if __name__ == "__main__":
    unittest.main()


from taxjson.bin import taxjson_t1135 as T1  # noqa: E402


def _tx(action="BUYSELL", date="2025-01-15", symbol="AAPL.US", qty=0.0,
        net=0.0, settle=None, time="09:30:00", currency="CAD", **extra):
    d = {"action": action, "date": date, "date_settle": settle or date,
         "time": time, "symbol": symbol, "quantity": qty,
         "net_amount": net, "symbol_new": "", "currency": currency}
    d.update(extra)
    return d


def _base(td, rows, name="margin_base.json", **meta):
    p = Path(td) / name
    doc = {"transactions": rows}
    if meta:
        doc["metadata"] = meta
    p.write_text(json.dumps(doc))
    return p


class TestT1135Inputs(unittest.TestCase):
    """S051-15 / S052-17: rows in another currency than --base-currency
    were summed as base currency (80,000 USD read as 80,000 CAD, 'no
    T1135 required'); S052-18: --threshold nan/inf/-1 accepted.

    Formerly: test_fix_l_filing_b.TestT1135Inputs"""

    def test_native_currency_rows_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            b = _base(td, [_tx(qty=200, net=-80000.0, symbol="MSFT.US",
                               currency="USD")])
            rc, out, err = _run(T1.main, [str(b), "--year", "2025"])
        self.assertEqual(rc, 2, out + err)
        self.assertIn("USD", err)
        self.assertIn("CAD", err)
        self.assertNotIn("no T1135 required", out)

    def test_book_target_currency_must_match(self):
        with tempfile.TemporaryDirectory() as td:
            b = _base(td, [_tx(qty=900, net=-90000.0, symbol="AAA.US",
                               currency=None)], target_currency="USD")
            rc, out, err = _run(T1.main, [str(b), "--year", "2025",
                                          "--base-currency", "CAD"])
        self.assertEqual(rc, 2, out + err)
        self.assertIn("USD", err)

    def test_non_cad_base_is_refused(self):
        # Re-audit A2-0660: a warning then 'below the 100,000.00 USD  # pii-ok
        # threshold' at rc 0 was the S051-15 verdict again; the T1135
        # test is a CAD test, so a non-CAD base is refused.
        with tempfile.TemporaryDirectory() as td:
            b = _base(td, [_tx(qty=900, net=-90000.0, symbol="AAA.US",
                               currency="USD")])
            rc, out, err = _run(T1.main, [str(b), "--year", "2025",
                                          "--base-currency", "USD"])
        self.assertEqual(rc, 2, out + err)
        self.assertIn("in CAD", err)
        self.assertNotIn("no T1135 required", out)

    def test_threshold_flags_are_validated(self):
        with tempfile.TemporaryDirectory() as td:
            b = _base(td, [_tx(qty=1000, net=-500000.0, symbol="AAA.US")])
            for flag in ("--threshold", "--detailed-threshold"):
                for bad in ("nan", "inf", "-1"):
                    with self.subTest(flag=flag, value=bad):
                        rc, out, err = _run(T1.main, [
                            str(b), "--year", "2025", f"{flag}={bad}"])
                        self.assertEqual(rc, 2, out + err)


# ---------------------------------------------------------------------------
# form-export, sum FOR THE RETURN, stand-alone layout checks
# ---------------------------------------------------------------------------

from taxjson.bin import taxjson_form_export as FE  # noqa: E402


class TestForm8949Footing(unittest.TestCase):
    """S032-16: (d), (e), (g), (h) rounded separately — rows and part
    totals did not foot, and the TXF gain differed from the printed (h).

    Formerly: test_fix_l_filing_b.TestForm8949Footing"""

    def test_rows_and_totals_foot(self):
        ents = [_g(symbol="AAPL.US", qty=-1.0, proceeds=10.006, cost=5.004,
                   gain=5.002, term="SHORT_TERM", date=f"2025-03-{i:02d}")
                for i in range(1, 29)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rep = tax_forms.build_8949(ents)
        for r in rep["part_I"]:
            self.assertAlmostEqual(r["proceeds"] - r["cost"]
                                   + r["adjustment"], r["gain"], places=9)
        t = rep["part_I_totals"]
        self.assertAlmostEqual(t["proceeds"] - t["cost"] + t["adjustment"],
                               t["gain"], places=9)
        txf = FE.build_txf(rep, "A")
        cost = sum(float(l[1:]) for l in txf.splitlines()[4:]
                   if l.startswith("$"))
        self.assertGreater(cost, 0)


class TestFormExportInputs(unittest.TestCase):
    """Formerly: test_fix_l_filing_b.TestFormExportInputs"""
    def _fe(self, *files, extra=()):
        return _run(FE.main, [*map(str, files), "--form", "schedule3",
                              "--country", "canada", "--year", "2025",
                              *extra])

    def test_native_currency_gains_are_refused(self):
        # S032-13: USD + CAD rows summed under a CAD label.
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_g(currency="USD", proceeds=10000.0),
                            _g(symbol="BBB.TO", proceeds=5000.0)],
                       "margin_raw_gains.json")
            rc, out, err = self._fe(g)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("USD", err)
        self.assertIn("raw_gains", err)

    def test_stage_file_and_foreign_layout_are_refused(self):
        # S033-01: a base stage file or {'Transactions': ...} gave a
        # $0 Schedule 3 at exit 0.
        with tempfile.TemporaryDirectory() as td:
            base = _base(td, [_tx(qty=10, net=-1000.0)])
            other = Path(td) / "other.json"
            other.write_text(json.dumps({"Transactions": [_g()]}))
            for f in (base, other):
                with self.subTest(file=f.name):
                    rc, out, err = self._fe(f)
                    self.assertEqual(rc, 2, out + err)
                    self.assertNotIn("Line 13199", out)
            # an income-only gains file is still a gains file
            inc = _gains(td, [{"action": "DIVIDEND", "symbol": "AAA.TO",
                               "qty": 0, "gain": 0.0, "dividend": 5.0,
                               "date": "2025-03-03",
                               "date_settle": "2025-03-03"}], "inc.json")
            rc, out, err = self._fe(inc)
            self.assertEqual(rc, 0, out + err)

    def test_sibling_tools_refuse_a_non_gains_document(self):
        env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
        with tempfile.TemporaryDirectory() as td:
            base = _base(td, [_tx(qty=10, net=-1000.0)])
            other = Path(td) / "other.json"
            other.write_text(json.dumps({"Transactions": [_g()]}))
            for mod, argv, stdin in (
                    ("taxjson_sum_gains", [str(base)], None),
                    ("taxjson_sum_gains", [str(other)], None),
                    ("taxjson_ccd_gains", [str(base)], None),
                    ("taxjson_leaps_gains", [str(other)], None),
                    ("taxjson_merge", [str(other)], None),
                    ("taxjson_gains", ["--country", "canada"],
                     json.dumps({"Transactions": [_tx(qty=1, net=-1)]})),
                    ("taxjson_t1135", [str(other), "--year", "2025"],
                     None)):
                with self.subTest(mod=mod, argv=argv[0]):
                    r = subprocess.run(
                        [sys.executable, "-m", f"taxjson.bin.{mod}",
                         *argv], input=stdin or "", capture_output=True,
                        text=True, env=env, timeout=120)
                    self.assertNotEqual(r.returncode, 0,
                                        r.stdout + r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    low = r.stderr.lower()
                    self.assertTrue("transactions" in low
                                    or "'gain'" in low, r.stderr)
