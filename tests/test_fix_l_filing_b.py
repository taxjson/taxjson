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


# ---------------------------------------------------------------------------
# t1135
# ---------------------------------------------------------------------------

from unittest import mock  # noqa: E402

from taxjson.bin import taxjson_t1135 as T1  # noqa: E402
from tax_rules import rule  # noqa: E402


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


class TestT1135MapValidation(unittest.TestCase):
    """R1-212: a lower-case map symbol never matched; S051-16: a
    misspelt EXCLUDE / CA keyword became a bogus 'country'."""

    def _load(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t1135.map"
            p.write_text(text)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ov = T1.load_overrides(p)
        return ov, err.getvalue()

    @rule("CA-RPT-02")
    def test_lower_case_symbol_applies(self):
        ov, _ = self._load("btc CA\nshop.us ca\n")
        self.assertEqual(ov, {"BTC": None, "SHOP.US": None})
        self.assertIsNone(T1.classify_country("BTC", ov))

    def test_unknown_country_word_is_refused_with_a_suggestion(self):
        ov, err = self._load("ABC.US EXCLUDED\nDEF.US CDN\nGHI.US NOT-FOREIGN\n"
                             "JKL.TO usa\nMNO.US gbr\n")
        self.assertEqual(ov, {"JKL.TO": "USA", "MNO.US": "GBR"})
        self.assertIn("EXCLUDED", err)
        self.assertIn("EXCLUDE", err)
        self.assertIn("CDN", err)
        self.assertIn("NOT-FOREIGN", err)
        self.assertEqual(T1.classify_country("ABC.US", ov), "USA")


class TestT1135Inputs(unittest.TestCase):
    """S051-15 / S052-17: rows in another currency than --base-currency
    were summed as base currency (80,000 USD read as 80,000 CAD, 'no
    T1135 required'); S052-18: --threshold nan/inf/-1 accepted."""

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

    def test_non_cad_base_warns(self):
        with tempfile.TemporaryDirectory() as td:
            b = _base(td, [_tx(qty=900, net=-90000.0, symbol="AAA.US",
                               currency="USD")])
            rc, out, err = _run(T1.main, [str(b), "--year", "2025",
                                          "--base-currency", "USD"])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("thresholds are in CAD", err)

    def test_threshold_flags_are_validated(self):
        with tempfile.TemporaryDirectory() as td:
            b = _base(td, [_tx(qty=1000, net=-500000.0, symbol="AAA.US")])
            for flag in ("--threshold", "--detailed-threshold"):
                for bad in ("nan", "inf", "-1"):
                    with self.subTest(flag=flag, value=bad):
                        rc, out, err = _run(T1.main, [
                            str(b), "--year", "2025", f"{flag}={bad}"])
                        self.assertEqual(rc, 2, out + err)


class TestT1135Walk(unittest.TestCase):
    """R1-311 / S051-23 / S052-02 / S052-22: the cost walk's branches and
    year edges, pinned on synthetic books."""

    def walk(self, rows, year=2025, tax_date="settle"):
        return T1.walk_costs(rows, year, {}, tax_date)

    @rule("CA-RPT-01")
    def test_settle_order_counts_a_dec31_settled_buy(self):
        # Trade-date order would meet the 2026-settled row first and stop.
        rows = [_tx(date="2025-12-29", settle="2026-01-05", qty=10,
                    net=-1000.0, symbol="CCC.US"),
                _tx(date="2025-12-30", settle="2025-12-31", qty=100,
                    net=-150000.0, symbol="AAA.US")]
        w = self.walk(rows)
        self.assertEqual(w["max_total_cost"], 150000.0)
        self.assertEqual(w["max_total_date"], "2025-12-31")

    @rule("CA-RPT-01")
    def test_dec31_and_jan1_edges(self):
        # A buy settling Dec 31 is in the year.
        w = self.walk([_tx(date="2025-12-30", settle="2025-12-31",
                           qty=100, net=-150000.0)])
        self.assertEqual(w["max_total_cost"], 150000.0)
        # A position sold on Jan 1 still counts for the year (held at
        # the start of the year).
        w = self.walk([_tx(date="2024-06-03", qty=100, net=-150000.0),
                       _tx(date="2025-01-01", qty=-100, net=160000.0)])
        self.assertEqual(w["max_total_cost"], 150000.0)
        # A Jan-1 purchase on top of a standing position: both count.
        w = self.walk([_tx(date="2024-06-03", qty=100, net=-60000.0),
                       _tx(date="2025-01-01", qty=100, net=-60000.0,
                           symbol="BBB.US"),
                       _tx(date="2025-01-02", qty=-100, net=61000.0)])
        self.assertEqual(w["max_total_cost"], 120000.0)
        self.assertEqual(w["max_total_date"], "2025-01-01")

    def test_out_of_year_rows_are_not_walked(self):
        w = self.walk([_tx(date="2025-03-03", qty=10, net=-1000.0),
                       _tx(date="2026-01-05", qty=10, net=-500000.0)])
        self.assertEqual(w["max_total_cost"], 1000.0)
        self.assertEqual(w["per_symbol"]["AAPL.US"]["year_end_cost"],
                         1000.0)

    def test_non_capital_row_with_a_quantity_is_skipped(self):
        w = self.walk([_tx(date="2025-02-03", qty=1.0, net=-50000.0,
                           symbol="BTC"),
                       _tx(action="FEE", date="2025-03-03", qty=-0.1,
                           net=-25.0, symbol="BTC"),
                       _tx(action="INTEREST", date="2025-04-03", qty=0.5,
                           net=30.0, symbol="BTC")])
        self.assertEqual(w["per_symbol"]["BTC"]["year_end_cost"], 50000.0)
        self.assertEqual(w["max_total_cost"], 50000.0)

    def test_partial_close_keeps_average_cost(self):
        w = self.walk([_tx(date="2025-02-03", qty=100, net=-10000.0),
                       _tx(date="2025-03-03", qty=-40, net=5000.0)])
        s = w["per_symbol"]["AAPL.US"]
        self.assertEqual(s["max_cost"], 10000.0)
        self.assertEqual(s["year_end_cost"], 6000.0)

    def test_short_cover_crossing_to_long(self):
        # Short 100 (proceeds 5,000), buy 140 for 7,000: covers 100 and
        # opens a long 40 at 7,000 x 40/140 = 2,000.
        w = self.walk([_tx(date="2025-02-03", qty=-100, net=5000.0),
                       _tx(date="2025-03-03", qty=140, net=-7000.0)])
        s = w["per_symbol"]["AAPL.US"]
        self.assertEqual(s["year_end_cost"], 2000.0)
        self.assertEqual(w["max_total_cost"], 2000.0)

    def test_long_sold_past_zero_then_covered(self):
        # Long 100 at 5,000; sell 150 (closes the long, opens a short
        # 50); cover 50; buy 30 at 3,000.
        w = self.walk([_tx(date="2025-02-03", qty=100, net=-5000.0),
                       _tx(date="2025-03-03", qty=-150, net=9000.0),
                       _tx(date="2025-04-03", qty=50, net=-2000.0),
                       _tx(date="2025-05-05", qty=30, net=-3000.0)])
        s = w["per_symbol"]["AAPL.US"]
        self.assertEqual(s["max_cost"], 5000.0)
        self.assertEqual(s["year_end_cost"], 3000.0)

    def test_sub_micro_crypto_rows_add_cost(self):
        # S052-02: 20 staking rewards of 9e-7 BTC at 100,000 CAD/BTC were
        # skipped by the 1e-6 share epsilon (the engine books them).
        rows = [_tx(date="2025-02-03", qty=0.999995, net=-99999.50,
                    symbol="BTC")]
        rows += [_tx(date=f"2025-03-{d:02d}", qty=9e-7, net=-0.09,
                     symbol="BTC") for d in range(1, 21)]
        w = self.walk(rows)
        self.assertAlmostEqual(w["max_total_cost"], 100001.30, places=2)

    def test_expired_long_option_is_named(self):
        # S052-22: a long option still in the books after its expiry.
        rows = [_tx(date="2025-02-03", qty=100, net=-40001.0,
                    symbol="ZZQ250620C00015000.US")]
        w = self.walk(rows)
        self.assertEqual(w["expired_options_held"],
                         ["ZZQ250620C00015000.US"])


class TestT1135Report(unittest.TestCase):
    def _rep(self, rows, gains=None, today="2026-03-01", year=2025, **kw):
        with tempfile.TemporaryDirectory() as td:
            b = _base(td, rows)
            gp = []
            if gains is not None:
                g = Path(td) / "margin_gains.json"
                g.write_text(json.dumps({"transactions": gains}))
                gp = [g]
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rep = T1.build_report([b], gp, year, {}, "CAD",
                                      today=today, **kw)
        return rep, err.getvalue()

    @rule("CA-RPT-01")
    def test_threshold_boundaries(self):
        # R1-314: required only ABOVE 100,000; Part A only BELOW 250,000.
        for cost, required, simplified in (
                (100000.00, False, True), (100000.01, True, True),
                (249999.99, True, True), (250000.00, True, False)):
            with self.subTest(cost=cost):
                rep, _ = self._rep([_tx(qty=100, net=-cost)])
                self.assertEqual(rep["filing_required"], required)
                self.assertEqual(rep["simplified_method_available"],
                                 simplified)

    def test_gain_only_symbol_keeps_its_loss(self):
        # R1-311 (E434/F438): a foreign symbol with a disposition but no
        # walked cost is still listed, its LOSS kept, max cost 0.
        rep, _ = self._rep(
            [_tx(qty=10, net=-1000.0)],
            gains=[{"symbol": "XYZ.US", "date": "2025-05-01",
                    "date_settle": "2025-05-02", "qty": -10,
                    "gain": -766.47}])
        row = next(r for r in rep["properties"] if r["symbol"] == "XYZ.US")
        self.assertEqual(row["gain"], -766.47)
        self.assertEqual(row["max_cost"], 0.0)
        self.assertEqual(rep["by_country"]["USA"]["gain"], -766.47)

    def test_render_pins_threshold_text_and_notes(self):
        # S052-14: the Part A threshold figure and the per-row notes.
        rep, _ = self._rep([
            _tx(qty=100, net=-150000.0),
            _tx(action="OPENING_BALANCE", qty=5, net=0.0, symbol="QQQ.US"),
            _tx(qty=10, net=-500.0, symbol="SAP.DE")])
        text = T1.render_report(rep)
        self.assertIn("Simplified method (Part A) available (stayed under "
                      "250,000.00 CAD", text)
        self.assertIn("unknown ACB (phantom opening) — cost understated",
                      text)
        self.assertIn("unclassified — review / add to t1135.map", text)
        self.assertIn("T1135 instructions", text)

    @rule("CA-RPT-01")
    def test_open_year_verdict_is_provisional(self):
        # S051-22 / S052-15: books ending 2026-09-22, run before Dec 31.
        rep, _ = self._rep([_tx(date="2026-03-02", qty=100, net=-60000.0),
                            _tx(date="2026-09-22", qty=10, net=-1500.0,
                                symbol="MSFT.US")],
                           year=2026, today="2026-09-29")
        self.assertFalse(rep["year_complete"])
        self.assertEqual(rep["as_of"], "2026-09-22")
        text = T1.render_report(rep)
        self.assertNotIn("no T1135 required this year", text)
        self.assertIn("so far", text)
        self.assertIn("2026-09-22", text)
        self.assertIn("COST AT 2026-09-22", text)
        self.assertNotIn("COST AT DEC 31", text)
        # After the year end the verdict is final.
        rep, _ = self._rep([_tx(date="2026-03-02", qty=100, net=-60000.0)],
                           year=2026, today="2027-01-04")
        self.assertTrue(rep["year_complete"])
        text = T1.render_report(rep)
        self.assertIn("COST AT DEC 31", text)
        self.assertIn("no T1135 required", text)

    def test_property_outside_the_books_is_disclosed(self):
        # S052-13: the verdict is on the brokerage books alone.
        rep, _ = self._rep([_tx(qty=100, net=-90000.0)])
        text = T1.render_report(rep)
        self.assertIn("on these books", text)
        self.assertIn("bank account", text)

    def test_expired_option_warning_in_report(self):
        rep, err = self._rep([_tx(date="2025-02-03", qty=100,
                                  net=-40001.0,
                                  symbol="ZZQ250620C00015000.US")])
        self.assertEqual(rep["expired_options_held"],
                         ["ZZQ250620C00015000.US"])
        self.assertIn("ZZQ250620C00015000.US", T1.render_report(rep))
        self.assertIn("after their expiry date", err)

    def test_help_carries_the_caveats(self):
        # S052-04: the docstring promised the caveats in --help.
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            T1.main(["--help"])
        self.assertIn("FAIR MARKET VALUE", out.getvalue())
        self.assertIn("CRYPTO", out.getvalue())


class TestT1135Checklist(unittest.TestCase):
    """S051-22 / S052-15: the checklist marked t1135 done in an open
    year below the threshold."""

    def test_open_year_below_threshold_is_not_done(self):
        from datetime import date
        from taxjson.lib import checklist as CL
        rep = {"filing_required": False, "year_complete": False,
               "as_of": "2026-09-22", "max_total_cost": 61500.0}
        ctx = mock.Mock()
        ctx.settings = {"country": "canada"}
        ctx.year = 2026
        ctx.today = date(2026, 9, 29)
        ctx.sub.return_value = (0, json.dumps(rep), "")
        res = CL.d_t1135(ctx)
        self.assertNotEqual(res.status, "done")
        self.assertIn("2026-09-22", res.detail)
        ctx.today = date(2027, 2, 1)
        ctx.sub.return_value = (0, json.dumps(
            dict(rep, year_complete=True, as_of="2026-12-31")), "")
        self.assertEqual(CL.d_t1135(ctx).status, "done")


class TestInstalmentBoundaries(unittest.TestCase):
    """R1-314: the $3,000 and $25 instalment thresholds at the tie."""

    @rule("CA-RPT-11")
    def test_net_tax_threshold_is_strict(self):
        from datetime import date
        from taxjson.bin.taxjson_instalments import build
        kw = dict(year=2026, basis="current_year", payments=[],
                  annual_rate=0.07, as_of=date(2026, 3, 1))
        self.assertFalse(build(current_net_tax=3000.00, prior_net_tax=5000.0,
                               **kw)["required_at_all"])
        self.assertTrue(build(current_net_tax=3000.01, prior_net_tax=5000.0,
                              **kw)["required_at_all"])
        both = dict(kw, current_net_tax=9000.0)
        self.assertEqual(build(prior_net_tax=3000.00,
                               second_prior_net_tax=3000.00,
                               **both)["prior_year_test"], "not_met")
        self.assertEqual(build(prior_net_tax=3000.01,
                               second_prior_net_tax=3000.00,
                               **both)["prior_year_test"], "met")
        self.assertEqual(build(prior_net_tax=1000.00,
                               second_prior_net_tax=3000.01,
                               **both)["prior_year_test"], "met")

    @rule("CA-RPT-11")
    def test_interest_charged_only_above_25(self):
        from datetime import date
        from taxjson.bin import taxjson_instalments as I
        for charge, net in ((25.00, 0.0), (25.01, 25.01)):
            with self.subTest(charge=charge), \
                    mock.patch.object(I, "_accrue",
                                      return_value=(charge, 0.0)):
                got = I.interest_and_penalty(required=[], payments=[],
                                             annual_rate=0.07,
                                             end=date(2027, 4, 30))
                self.assertEqual(got["net_interest"], net)
