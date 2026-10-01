"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.
"""
import contextlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_reconcile_slips as RS

REPO = Path(__file__).resolve().parent.parent


def _run(main, argv):
    """(rc, stdout, stderr) of a bin tool's main(argv)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (
                0 if e.code is None else 1)
            if not isinstance(e.code, int) and e.code is not None:
                err.write(str(e.code))
    return rc, out.getvalue(), err.getvalue()


def _sell(symbol="AAPL.US", date="2025-05-02", qty=-100, proceeds=11990.0,
          cost=10000.0, commission=10.0, fee=0.0, direction="LONG", **extra):
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
         "disallowed_amount": 0.0, "days_held": 100,
         "direction": direction, "commission": commission, "fee": fee,
         "account": "margin"}
    e.update(extra)
    return e


def _gains(td, entries, name="margin_gains.json"):
    p = Path(td) / name
    p.write_text(json.dumps({"transactions": entries}))
    return p


def _slip(td, text, name="t5008.csv", mode="w"):
    p = Path(td) / name
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# reconcile-slips
# ---------------------------------------------------------------------------

class TestReconcileSlipCurrency(unittest.TestCase):
    """R1-20: the slip's Box 13 currency was ignored, so Webull's USD
    T5008 gave '0 OK, 267 mismatch' (every amount off by the FX rate)."""

    def test_foreign_currency_slip_is_refused_naming_box_13(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, "symbol,quantity,proceeds,box 13\n"
                          "AAPL,100,12000,USD\n")
            rc, out, err = _run(RS.main, [str(s), "--gains", str(g),
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
            rc, out, err = _run(RS.main, [str(s), "--gains", str(g),
                                          "--country", "canada"])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("1 OK", out)

    def test_usd_slip_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, "symbol,quantity,proceeds,currency\n"
                          "AAPL,100,12000,USD\n")
            rc, out, err = _run(RS.main, [str(s), "--gains", str(g),
                                          "--country", "usa",
                                          "--date-basis", "trade"])
        self.assertEqual(rc, 0, out + err)


class TestReconcileSlipUnreadableCells(unittest.TestCase):
    """R1-335 / S035-19 / S036-07: an unreadable (decimal-comma, 'nan',
    unit-suffixed) cost cell was dropped silently — the cost comparison
    vanished or was computed from a partial sum, at exit 0."""

    def _rc(self, slip_text):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell(qty=-200, proceeds=18000.0,
                                  cost=18000.0, commission=0.0)])
            s = _slip(td, slip_text)
            return _run(RS.main, [str(s), "--gains", str(g), "--json"])

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
    decode, crashed with tracebacks."""

    def test_directory_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            rc, out, err = _run(RS.main, [td, "--gains", str(g)])
        self.assertEqual(rc, 2)
        self.assertIn("not a file", err)
        self.assertNotIn("Traceback", err)

    def test_undecodable_bytes_are_read(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, b"symbol,quantity,proceeds\n"
                          b"AAPL,100,11990\n\x81junk,,\n")
            rc, out, err = _run(RS.main, [str(s), "--gains", str(g)])
        self.assertNotIn("Traceback", err)
        self.assertIn("AAPL", out)


class TestReconcileSlipTolerance(unittest.TestCase):
    """S036-00: --tolerance accepted nan / negative / inf (every symbol a
    MISMATCH 'off by +0.00'; nan hid the cost note); the wrapper forwarded
    '-inf' as its own token."""

    def test_standalone_refuses_non_finite_or_negative(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell()])
            s = _slip(td, "symbol,quantity,proceeds\nAAPL,100,12000\n")
            for bad in ("nan", "-1", "inf", "-inf"):
                with self.subTest(tol=bad):
                    rc, out, err = _run(RS.main, [
                        str(s), "--gains", str(g), f"--tolerance={bad}"])
                    self.assertEqual(rc, 2, out + err)
            rc, out, err = _run(RS.main, [str(s), "--gains", str(g),
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
    """S035-24: --help pointed at 'header docs' it never showed."""

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
    were unpinned (mutants survived)."""

    def test_two_lots_with_commission_and_fee(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [
                _sell(qty=-100, proceeds=1000.0, cost=800.0,
                      commission=5.0, fee=2.0),
                _sell(qty=-50, proceeds=600.0, cost=400.0,
                      commission=3.0, fee=1.0, date="2025-06-02")])
            c = RS.load_computed([g], 2025)["AAPL"]
        self.assertAlmostEqual(c["qty"], 150.0)
        self.assertAlmostEqual(c["proceeds_net"], 1600.0)
        self.assertAlmostEqual(c["proceeds_gross"], 1611.0)
        self.assertAlmostEqual(c["cost"], 1200.0)
        self.assertEqual(c["rows"], 2)

    def test_cost_note_sign_and_tainted_count(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell(cost=10000.0, tainted=True)])
            computed = RS.load_computed([g], 2025)
        slip = {"AAPL": {"qty": 100.0, "proceeds": 12000.0,
                         "cost": 9000.0, "rows": 1, "listings": {}}}
        rep = RS.reconcile(slip, computed, 1.0)
        detail = rep["rows"][0]["detail"]
        self.assertIn("slip cost differs by -1,000.00", detail)
        self.assertIn("1 tainted disposition(s)", detail)


if __name__ == "__main__":
    unittest.main()
