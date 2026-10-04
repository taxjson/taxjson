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
    vanished or was computed from a partial sum, at exit 0."""

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
    decode, crashed with tracebacks."""

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
    '-inf' as its own token."""

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
from tax_rules import rule, rule  # noqa: E402


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
        # ticker.map T1135 lines (once t1135.map lines).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text("".join(f"T1135 {ln}\n"
                                 for ln in text.splitlines()))
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
        # A ticker.map T1135 line outside the vocabulary stops the tool
        # (the old t1135.map skipped it with a warning and the symbol
        # kept its listing-suffix country).
        from taxjson.lib.cli_diag import InputContentError
        for word in ("EXCLUDED", "CDN", "NOT-FOREIGN"):
            with self.subTest(word=word):
                with self.assertRaises(InputContentError) as cm:
                    self._load(f"ABC.US {word}\nJKL.TO usa\n")
                self.assertIn(word, str(cm.exception))
        with self.assertRaises(InputContentError) as cm:
            self._load("ABC.US EXCLUDED\n")
        self.assertIn("did you mean EXCLUDE", str(cm.exception))
        ov, _ = self._load("JKL.TO usa\nMNO.US gbr\n")
        self.assertEqual(ov, {"JKL.TO": "USA", "MNO.US": "GBR"})


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

    def test_non_cad_base_is_refused(self):
        # Re-audit A2-0660: a warning then 'below the 100,000.00 USD
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

    @rule("CA-RPT-12")
    def test_out_of_year_rows_are_not_walked(self):
        w = self.walk([_tx(date="2025-03-03", qty=10, net=-1000.0),
                       _tx(date="2026-01-05", qty=10, net=-500000.0)])
        self.assertEqual(w["max_total_cost"], 1000.0)
        self.assertEqual(w["per_symbol"]["AAPL.US"]["year_end_cost"],
                         1000.0)

    @rule("CA-RPT-12")
    def test_non_capital_row_with_a_quantity_is_skipped(self):
        w = self.walk([_tx(date="2025-02-03", qty=1.0, net=-50000.0,
                           symbol="BTC"),
                       _tx(action="FEE", date="2025-03-03", qty=-0.1,
                           net=-25.0, symbol="BTC"),
                       _tx(action="INTEREST", date="2025-04-03", qty=0.5,
                           net=30.0, symbol="BTC")])
        self.assertEqual(w["per_symbol"]["BTC"]["year_end_cost"], 50000.0)
        self.assertEqual(w["max_total_cost"], 50000.0)

    @rule("CA-RPT-12")
    def test_partial_close_keeps_average_cost(self):
        w = self.walk([_tx(date="2025-02-03", qty=100, net=-10000.0),
                       _tx(date="2025-03-03", qty=-40, net=5000.0)])
        s = w["per_symbol"]["AAPL.US"]
        self.assertEqual(s["max_cost"], 10000.0)
        self.assertEqual(s["year_end_cost"], 6000.0)

    @rule("CA-RPT-12")
    def test_short_cover_crossing_to_long(self):
        # Short 100 (proceeds 5,000), buy 140 for 7,000: covers 100 and
        # opens a long 40 at 7,000 x 40/140 = 2,000.
        w = self.walk([_tx(date="2025-02-03", qty=-100, net=5000.0),
                       _tx(date="2025-03-03", qty=140, net=-7000.0)])
        s = w["per_symbol"]["AAPL.US"]
        self.assertEqual(s["year_end_cost"], 2000.0)
        self.assertEqual(w["max_total_cost"], 2000.0)

    @rule("CA-RPT-12")
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

    @rule("CA-RPT-12")
    def test_sub_micro_crypto_rows_add_cost(self):
        # S052-02: 20 staking rewards of 9e-7 BTC at 100,000 CAD/BTC were
        # skipped by the 1e-6 share epsilon (the engine books them).
        rows = [_tx(date="2025-02-03", qty=0.999995, net=-99999.50,
                    symbol="BTC")]
        rows += [_tx(date=f"2025-03-{d:02d}", qty=9e-7, net=-0.09,
                     symbol="BTC") for d in range(1, 21)]
        w = self.walk(rows)
        self.assertAlmostEqual(w["max_total_cost"], 100001.30, places=2)

    @rule("CA-RPT-12")
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
        self.assertIn("unknown ACB (bought before the data) — cost understated",
                      text)
        self.assertIn("unclassified — review / add a ticker.map T1135 "
                      "line", text)
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


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

from taxjson.bin import taxjson_audit as AU  # noqa: E402


def _ttx(**kw):
    from taxjson.lib.core import TaxTransaction
    base = dict(action="BUYSELL", date="2025-01-06", symbol="AAA.TO",
                quantity=100.0, currency="CAD", price=10.0,
                net_amount=-1000.0, account="margin",
                date_settle="2025-01-07")
    base.update(kw)
    return TaxTransaction(**base).to_dict()


class _AuditBook:
    """A CAD base book, the engine's saved gains of it, and the audit."""

    def __init__(self, td, rows, timing="close", year=2025):
        from taxjson.lib.core import get_tax_rules, load_transactions
        self.td = Path(td)
        self.base = self.td / "margin_base.json"
        self.base.write_text(json.dumps({"transactions": rows}))
        res = get_tax_rules("canada").compute_gains(
            load_transactions(self.base), option_premium_timing=timing)
        self.records = res["transactions"]
        self.gains = self.td / "margin_gains.json"
        self.gains.write_text(json.dumps({"transactions": self.records},
                                         default=str))
        self.timing = timing
        self.year = year

    def audit(self, *extra, check=True, json_out=True):
        argv = ["--country", "canada", "--base", str(self.base),
                "--year", str(self.year), "--no-color",
                "--option-premium-timing", self.timing]
        if check:
            argv += ["--check", str(self.gains)]
        argv += list(extra)
        if json_out:
            argv.append("--json")
        rc, out, err = _run(AU.main, argv)
        return rc, (json.loads(out) if json_out and out.strip() else out), err


class TestAuditCheckFiles(unittest.TestCase):
    def _book(self, td):
        return _AuditBook(td, [
            _ttx(),
            _ttx(quantity=-100.0, net_amount=1200.0, date="2025-03-03",
                 date_settle="2025-03-04")])

    def test_check_file_named_twice_is_read_once(self):
        # S026-01: every tie-out failed as "the books changed".
        with tempfile.TemporaryDirectory() as td:
            b = self._book(td)
            rc, doc, err = b.audit("--check", str(b.gains))
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["reconciliation_failures"], [])
        self.assertIn("given more than once", err)

    def test_id_less_saved_disposition_fails(self):
        # S026-02: a record without an id passed the reverse sweep.
        with tempfile.TemporaryDirectory() as td:
            b = self._book(td)
            recs = json.loads(b.gains.read_text())["transactions"]
            fake = dict(recs[0], symbol="ZZZ.TO", gain=8000.0)
            fake.pop("id", None)
            b.gains.write_text(json.dumps({"transactions": recs + [fake]}))
            rc, doc, err = b.audit()
        self.assertEqual(rc, 1)
        self.assertTrue(any("NO id" in f
                            for f in doc["reconciliation_failures"]))

    def test_year_scope_and_sweep_use_the_settle_date(self):
        # S026-14: a Dec-31 trade settling in January is audited (and
        # swept) in the settle year.
        with tempfile.TemporaryDirectory() as td:
            b = _AuditBook(td, [
                _ttx(date="2025-06-02", date_settle="2025-06-03"),
                _ttx(quantity=-50.0, net_amount=710.0, date="2025-12-31",
                     date_settle="2026-01-02"),
                _ttx(quantity=-50.0, net_amount=1000.0, date="2026-02-02",
                     date_settle="2026-02-03")], year=2026)
            rc, doc, err = b.audit()
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(doc["events"]), 2)
        self.assertEqual(doc["events"][0]["date"], "2025-12-31")
        self.assertAlmostEqual(doc["total_gain"], 710.0 - 500.0
                               + 1000.0 - 500.0, places=2)
        self.assertEqual(doc["reconciliation_failures"], [])
        self.assertIn("unrounded", doc["totals_note"])


class TestAuditEventLabels(unittest.TestCase):
    def test_grant_write_is_headed_write_with_the_premium(self):
        # S026-04: a sell-to-open read 'COVER ... (short)', proceeds 0.
        with tempfile.TemporaryDirectory() as td:
            b = _AuditBook(td, [
                _ttx(symbol="XYZ250620C00055000.TO", quantity=-1.0,
                     price=2.0, net_amount=200.0, date="2025-03-04",
                     date_settle="2025-03-05")], timing="grant")
            rc, doc, err = b.audit()
            self.assertEqual(rc, 0, err)
            self.assertTrue(doc["events"][0]["grant"])
            rc, text, err = b.audit("--no-trace", json_out=False)
        self.assertIn("WRITE 1 XYZ250620C00055000.TO", text)
        self.assertNotIn("COVER", text)
        self.assertRegex(text, r"proceeds\s+200\.00")
        self.assertRegex(text, r"cost basis\s+0\.00")

    def _ev(self, **kw):
        ev = {"id": "abc123", "symbol": "SHORTCO.TO", "date": "2025-03-03",
              "date_settle": "2025-03-04", "account": "margin",
              "qty": 100.0, "direction": "LONG", "proceeds": 0.0,
              "cost": 0.0, "gain": 0.0, "disallowed_amount": 0.0,
              "permanently_disallowed": 0.0, "is_wash_sale": False,
              "sources": [], "warnings": [], "failures": [],
              "replacements": [], "tie_out": {}, "fx": None}
        ev.update(kw)
        return "\n".join(AU.render_event(ev, 1, 1, "canada",
                                         show_trace=False))

    def test_short_cover_shows_the_filed_legs(self):
        # S026-08: short 100 @ 50, cover @ 40.
        text = self._ev(direction="SHORT", proceeds=-4000.0, cost=-5000.0,
                        gain=1000.0)
        self.assertRegex(text, r"proceeds\s+5,000\.00\s+\(100 sh @ 50\.0000\)")
        self.assertRegex(text,
                         r"cost basis\s+4,000\.00\s+\(100 sh @ 40\.0000\)")

    def test_option_per_share_uses_the_multiplier(self):
        # S026-05: '(1 sh @ 398.7000)' beside a source @ 3.99.
        text = self._ev(symbol="ABC270115C00050000.TO", qty=1.0,
                        proceeds=398.70, cost=500.65, gain=-101.95,
                        is_option=True)
        self.assertIn("(1 contract × 100 sh @ 3.9870)", text)
        self.assertIn("(1 contract × 100 sh @ 5.0065)", text)

    def test_split_denial_names_both_destinations(self):
        # S026-10: 700 denied, 300 of it permanent.
        text = self._ev(symbol="MIX.TO", proceeds=500.0, cost=1500.0,
                        raw_gain=-1000.0, gain=-300.0,
                        disallowed_amount=700.0,
                        permanently_disallowed=300.0, is_wash_sale=True)
        flat = " ".join(text.split())
        self.assertIn("400.00 denied loss → replacement lot's ACB", flat)
        self.assertIn("300.00 denied loss PERMANENTLY lost", flat)
        self.assertIn("affiliated person", flat)
        # A fully deferred denial names no permanent loss.
        text = self._ev(symbol="S.TO", proceeds=500.0, cost=1500.0,
                        raw_gain=-1000.0, gain=0.0,
                        disallowed_amount=1000.0, is_wash_sale=True)
        self.assertNotIn("PERMANENTLY", text)
        self.assertIn("replacement lot's ACB", text)


class TestAuditMerge(unittest.TestCase):
    def test_us_lots_of_mixed_term_are_labelled_mixed(self):
        # S026-15: a sale over LT and ST lots read LONG_TERM.
        recs = [{"id": "s1", "symbol": "AAPL.US", "qty": 10.0,
                 "gain": 1000.0, "term": "LONG_TERM", "days_held": 609,
                 "direction": "LONG"},
                {"id": "s1", "symbol": "AAPL.US", "qty": 10.0,
                 "gain": 500.0, "term": "SHORT_TERM", "days_held": 92,
                 "direction": "LONG"}]
        (ev,) = AU.merge_lot_records(recs)
        self.assertEqual(ev["term"], "MIXED")
        self.assertEqual(ev["term_split"],
                         {"LONG_TERM": 1000.0, "SHORT_TERM": 500.0})
        self.assertIsNone(ev["days_held"])
        self.assertEqual(ev["days_held_range"], [92, 609])
        self.assertEqual(ev["lots"], 2)
        self.assertEqual(ev["qty"], 20.0)

    def test_cross_zero_close_and_write_stay_two_events(self):
        # S026-19: long 2 puts, one SELL of 5 under grant timing.
        with tempfile.TemporaryDirectory() as td:
            sym = "QQQ250919P00040000.TO"
            b = _AuditBook(td, [
                _ttx(symbol=sym, quantity=2.0, price=1.0, net_amount=-201.0,
                     date="2025-07-02", date_settle="2025-07-03"),
                _ttx(symbol=sym, quantity=-5.0, price=2.0,
                     net_amount=999.0, date="2025-08-01",
                     date_settle="2025-08-04")], timing="grant")
            rc, doc, err = b.audit()
        self.assertEqual(rc, 0, err)
        evs = sorted(doc["events"], key=lambda e: e["direction"])
        self.assertEqual(len(evs), 2)
        self.assertEqual([e["direction"] for e in evs], ["LONG", "SHORT"])
        self.assertEqual(evs[0]["qty"], 2.0)
        self.assertTrue(evs[1]["grant"])
        self.assertTrue(all(e["tie_out"]["ties"] for e in evs))


class TestAuditTotalsPinned(unittest.TestCase):
    """S026-11: the audit total gain (text and multi-book JSON) and the
    *.traces TOTAL row were unpinned."""

    def test_reconciliation_block_total(self):
        evs = [{"gain": 100.25, "disallowed_amount": 0.0, "tie_out": {}},
               {"gain": -40.5, "disallowed_amount": 12.0, "tie_out": {}}]
        text = "\n".join(AU.render_reconciliation(evs, [], False))
        self.assertRegex(text, r"total gain\s+59\.75")
        self.assertRegex(text, r"total disallowed\s+12\.00")

    def test_multi_book_json_sums_every_book(self):
        from taxjson.bin.taxjson_run import _merge_audit_json
        docs = [{"events": [{"id": "a"}], "total_gain": 100.0,
                 "total_disallowed": 1.0, "failed": False,
                 "reconciliation_failures": []},
                {"events": [{"id": "b"}], "total_gain": -30.5,
                 "total_disallowed": 2.0, "failed": True,
                 "reconciliation_failures": ["x"]}]
        doc = _merge_audit_json(docs, "CAD", "canada")
        self.assertEqual(doc["total_gain"], 69.5)
        self.assertEqual(doc["total_disallowed"], 3.0)
        self.assertEqual(len(doc["events"]), 2)
        self.assertTrue(doc["failed"])
        self.assertEqual(doc["reconciliation_failures"], ["x"])

    def test_traces_total_row(self):
        from taxjson.lib.trace_format import render_summary_table
        lines = render_summary_table([
            {"symbol": "AAA.TO", "qty": -10, "gain": 300.0,
             "disallowed_amount": 0.0},
            {"symbol": "AAA.TO", "qty": -5, "gain": -100.0,
             "disallowed_amount": 20.0},
            {"symbol": "BBB.TO", "qty": -1, "gain": 50.0,
             "disallowed_amount": 0.0}])
        total = next(l for l in lines if l.startswith("# TOTAL"))
        self.assertEqual(total.split()[2], "3")
        self.assertIn("+$250.00", total)
        self.assertIn("$20.00", total)


# ---------------------------------------------------------------------------
# form-export, sum FOR THE RETURN, stand-alone layout checks
# ---------------------------------------------------------------------------

from taxjson.bin import taxjson_form_export as FE  # noqa: E402


def _g(symbol="AAA.TO", qty=-100.0, proceeds=1000.0, cost=800.0,
       gain=None, direction="LONG", date="2025-05-02", settle=None, **kw):
    e = {"symbol": symbol, "qty": qty, "proceeds": proceeds, "cost": cost,
         "gain": (proceeds - cost) if gain is None else gain,
         "direction": direction, "date": date,
         "date_settle": settle or date, "commission": 0.0, "fee": 0.0,
         "disallowed_amount": 0.0, "permanently_disallowed": 0.0,
         "days_held": 30, "currency": "CAD", "account": "margin"}
    e.update(kw)
    return e


def _row(rep, symbol):
    return next(r for r in rep["rows"] if r["symbol"] == symbol)


class TestSchedule3Units(unittest.TestCase):
    """S003-04 / S033-02: a written option under grant timing counted its
    write AND its buy-back (5 contracts showed 10 units)."""

    @rule("CA-OPT-01")
    def test_grant_write_and_buyback_count_once(self):
        from taxjson.lib.core import get_tax_rules, TaxTransaction
        sym = "QQQ250620C00050000.TO"
        rows = [TaxTransaction(action="BUYSELL", date=d, date_settle=s,
                               symbol=sym, quantity=q, net_amount=n,
                               currency="CAD", account="margin", time=t)
                for d, s, q, n, t in (
                    ("2025-02-03", "2025-02-04", -5.0, 649.0, "10:00:00"),
                    ("2025-04-01", "2025-04-02", 5.0, -161.0, "10:00:00"))]
        for timing in ("grant", "close"):
            with self.subTest(timing=timing):
                res = get_tax_rules("canada").compute_gains(
                    rows, option_premium_timing=timing)
                rep = FE.build_schedule3(res["transactions"], 2025)
                r = _row(rep, sym)
                self.assertEqual(r["units"], 5.0)
                self.assertAlmostEqual(r["gain"], 488.0, places=2)

    def test_open_write_counts_at_the_write(self):
        rep = FE.build_schedule3([
            _g(symbol="XYZ250620P00040000.TO", qty=-4.0, proceeds=0.0,
               cost=-400.0, gain=400.0, direction="SHORT", grant=True)],
            2025)
        self.assertEqual(rep["rows"][0]["units"], 4.0)

    def test_crypto_units_keep_full_precision(self):
        # S032-21: 0.00003 BTC showed 0 units beside nonzero proceeds.
        rep = FE.build_schedule3(FE.mark_crypto([
            _g(symbol="BTC", qty=-0.00003, proceeds=2.70, cost=2.00),
            _g(symbol="ETH", qty=-0.34325885, proceeds=1000.0,
               cost=900.0)]), 2025)
        self.assertEqual(_row(rep, "BTC")["units"], 0.00003)
        self.assertEqual(_row(rep, "ETH")["units"], 0.34325885)
        text = FE.render_schedule3(rep, 2025, "CAD")
        self.assertIn("0.00003 | BTC", text)
        self.assertIn("0.34325885 | ETH", text)


class TestSchedule3Amounts(unittest.TestCase):
    def test_net_debit_write_shows_no_proceeds(self):
        # S032-19: a put written for a net debit showed proceeds 0.35
        # and an invented ACB of 0.70.
        rep = FE.build_schedule3([
            _g(symbol="ZZ250620P00010000.US", qty=-1.0, proceeds=-0.0,
               cost=0.3459, gain=-0.3459, direction="SHORT")], 2025)
        r = rep["rows"][0]
        self.assertEqual((r["proceeds"], r["acb"], r["outlays"], r["gain"]),
                         (0.0, 0.0, 0.35, -0.35))
        # A credit write of the same size: proceeds 0.35, ACB 0.
        rep = FE.build_schedule3([
            _g(symbol="ZZ250620P00010000.US", qty=-1.0, proceeds=-0.0,
               cost=-0.3459, gain=0.3459, direction="SHORT")], 2025)
        r = rep["rows"][0]
        self.assertEqual((r["proceeds"], r["acb"], r["gain"]),
                         (0.35, 0.0, 0.35))

    @rule("CA-ACB-02")
    def test_commission_and_fee_are_both_outlays(self):
        # S032-18: the outlays accumulator was unpinned.
        ents = [_g(proceeds=8993.0, cost=7000.0, commission=5.0, fee=2.0)]
        rep = FE.build_schedule3(ents, 2025)
        r = rep["rows"][0]
        self.assertEqual(r["proceeds"], 9000.0)
        self.assertEqual(r["outlays"], 7.0)
        self.assertEqual(r["acb"], 7000.0)
        self.assertEqual(r["gain"], 1993.0)
        tot = FE.filing_totals(ents, 2025)
        self.assertEqual((tot["proceeds"], tot["outlays"], tot["acb"]),
                         (9000.0, 7.0, 7000.0))
        self.assertEqual(rep["lines"][0]["proceeds"], 9000.0)

    @rule("CA-SL-09")
    def test_denials_sum_and_split_notes(self):
        # S032-20 / R1-312 / S033-03.
        rep = FE.build_schedule3([
            _g(proceeds=500.0, cost=600.0, gain=0.0, raw_gain=-100.0,
               disallowed_amount=100.0),
            _g(proceeds=500.0, cost=600.0, gain=0.0, raw_gain=-100.0,
               disallowed_amount=100.0, date="2025-06-02"),
            _g(symbol="MIX.TO", proceeds=300.0, cost=1000.0, gain=0.0,
               raw_gain=-700.0, disallowed_amount=700.0,
               permanently_disallowed=300.0),
            _g(symbol="PERM.TO", proceeds=300.0, cost=500.0, gain=0.0,
               raw_gain=-200.0, disallowed_amount=200.0,
               permanently_disallowed=200.0)], 2025)
        r = _row(rep, "AAA.TO")
        self.assertEqual(r["denied"], 200.0)
        self.assertEqual(r["dispositions"], 2)
        self.assertIn("superficial loss 200.00 denied", r["notes"])
        m = _row(rep, "MIX.TO")["notes"]
        self.assertIn("superficial loss 400.00 denied", m)
        self.assertIn("superficial loss 300.00 PERMANENTLY denied", m)
        p = _row(rep, "PERM.TO")["notes"]
        self.assertNotIn("add it to the ACB of the replacement", p)
        self.assertIn("affiliated person", p)


class TestSchedule3Render(unittest.TestCase):
    def test_crypto_note_follows_the_line_routing(self):
        # S032-23: the note's own year test was unpinned.
        ents = FE.mark_crypto([_g(symbol="BTC", qty=-1.0)])
        for year, want in ((2025, "crypto-assets on 15200/15301"),
                           (2023, "crypto-assets with the other "
                                  "properties (15199/15300)"),
                           # The 2024 form's two periods (A2-0166).
                           (2024, "crypto-assets and other properties "
                                  "on 10693/10694")):
            with self.subTest(year=year):
                rep = FE.build_schedule3(ents, year)
                self.assertIn(want, FE.render_schedule3(rep, year, "CAD"))

    def test_csv_write_is_atomic(self):
        # S032-24: a failed write left a truncated CSV in place.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.csv"
            out.write_text("good\n")
            rep = FE.build_schedule3([_g()], 2025)

            def boom(_rep, path):
                Path(path).write_text("partial")
                raise OSError(27, "File too large")
            with mock.patch.object(FE, "_write_csv", boom):
                with self.assertRaises(OSError):
                    FE.write_csv(rep, out)
            self.assertEqual(out.read_text(), "good\n")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["out.csv"])
            FE.write_csv(rep, out)
            self.assertIn("AAA.TO", out.read_text())


class TestForm8949Footing(unittest.TestCase):
    """S032-16: (d), (e), (g), (h) rounded separately — rows and part
    totals did not foot, and the TXF gain differed from the printed (h)."""

    def test_rows_and_totals_foot(self):
        ents = [_g(symbol="AAPL.US", qty=-1.0, proceeds=10.006, cost=5.004,
                   gain=5.002, term="SHORT_TERM", date=f"2025-03-{i:02d}")
                for i in range(1, 29)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rep = FE.build_8949(ents)
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


class TestDateKeysPinned(unittest.TestCase):
    """R1-303: the settle-vs-trade date key of form-export, carryover and
    fx-cash was unpinned (t1135: TestT1135Walk)."""

    @rule("CA-DATE-01")
    def test_form_export_year_by_settle_date(self):
        ents = [_g(date="2025-12-31", settle="2026-01-02"),
                _g(symbol="BBB.TO", date="2025-06-02")]
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, ents)
            rc, out, err = _run(FE.main, [
                str(g), "--form", "schedule3", "--country", "canada",
                "--year", "2026", "--date-basis", "settle", "--json"])
            self.assertEqual(rc, 0, err)
            self.assertEqual([r["symbol"] for r in json.loads(out)["rows"]],
                             ["AAA.TO"])
            rc, out, err = _run(FE.main, [
                str(g), "--form", "schedule3", "--country", "canada",
                "--year", "2025", "--date-basis", "settle", "--json"])
            self.assertEqual([r["symbol"] for r in json.loads(out)["rows"]],
                             ["BBB.TO"])

    @rule("CA-DATE-01")
    def test_carryover_buckets_by_settle_date(self):
        from taxjson.bin.taxjson_carryover import yearly_nets
        res = {"transactions": [_g(date="2025-12-31", settle="2026-01-02",
                                   proceeds=900.0, cost=1000.0)]}
        self.assertEqual(list(yearly_nets(res, "settle")), [2026])
        self.assertEqual(list(yearly_nets(res, "trade")), [2025])

    @rule("CA-SL-13")
    def test_carryover_blends_canadian_crypto_into_the_wash_pass(self):
        from taxjson.bin import taxjson_carryover as CO
        with tempfile.TemporaryDirectory() as td:
            eq = _base(td, [_tx(qty=10, net=-1000.0, symbol="AAA.TO")],
                       "margin_base.json")
            cr = _base(td, [
                _tx(date="2025-02-03", qty=1.0, net=-50000.0, symbol="BTC"),
                _tx(date="2025-03-03", qty=-1.0, net=40000.0, symbol="BTC"),
                _tx(date="2025-03-10", qty=1.0, net=-41000.0,
                    symbol="BTC")], "crypto_base.json")
            rc, out, err = _run(CO.main, [str(eq), "--crypto", str(cr),
                                          "--country", "canada", "--json"])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        rows = doc.get("ledger") or doc.get("years") or doc.get("rows")
        row = next(r for r in rows if int(r.get("year")) == 2025)
        # The BTC loss is superficial (repurchased within 30 days, still
        # held): the year's net is 0, not -10,000.
        net = next(row[k] for k in ("net_gain", "net", "realized")
                   if k in row)
        self.assertAlmostEqual(float(net), 0.0, places=2)

    @rule("CA-FX-07")
    def test_fx_cash_walks_in_settlement_order(self):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        rates = {("USD", "2026-02-11"): 1.30, ("USD", "2026-02-12"): 1.40}
        txs = [dict(action="BUYSELL", date="2026-02-09",
                    date_settle="2026-02-12", time="10:00:00",
                    symbol="AAA.US", quantity=10, currency="USD",
                    net_amount=1000.0, account="margin"),
               dict(action="BUYSELL", date="2026-02-10",
                    date_settle="2026-02-11", time="10:00:00",
                    symbol="BBB.US", quantity=-10, currency="USD",
                    net_amount=1000.0, account="margin")]
        doc = build_ledger(txs, "CAD", {}, 2026,
                           rate_of=lambda c, d: rates.get((c, d)))
        self.assertEqual(doc["overdrafts"], {})
        self.assertAlmostEqual(doc["net_gain"], 100.0, places=2)


class TestSumRoundingNote(unittest.TestCase):
    """R1-166: FOR THE RETURN (rows rounded to the cent) disagreed with
    the gains files' unrounded total by cents without a word."""

    def test_return_row_names_the_unrounded_total(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _gains(root / "work", [
                _g(symbol=f"S{i}.TO", proceeds=1001.004, cost=1000.0,
                   gain=1.004) for i in range(3)])
            env = {**os.environ, "TAXJSON_OFFLINE": "1",
                   "PYTHONPATH": str(REPO / "src")}
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "sum"], capture_output=True, text=True,
                env=env, timeout=300)
            j = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "sum", "--json"], capture_output=True,
                text=True, env=env, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("unrounded total gain is 3.01", r.stdout)
        doc = json.loads(j.stdout)
        self.assertEqual(doc["filing"]["totals"]["gain"], 3.0)
        self.assertEqual(doc["filing"]["engine_gain_unrounded"], 3.01)
