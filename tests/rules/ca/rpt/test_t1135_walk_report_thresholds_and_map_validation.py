"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.rules import ids as R


if __name__ == "__main__":
    unittest.main()


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
    misspelt EXCLUDE / CA keyword became a bogus 'country'.

    Formerly: test_fix_l_filing_b.TestT1135MapValidation"""

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

    @rule(R.CA_RPT_02)
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


class TestT1135Walk(unittest.TestCase):
    """R1-311 / S051-23 / S052-02 / S052-22: the cost walk's branches and
    year edges, pinned on synthetic books.

    Formerly: test_fix_l_filing_b.TestT1135Walk"""

    def walk(self, rows, year=2025, tax_date="settle"):
        return T1.walk_costs(rows, year, {}, tax_date)

    @rule(R.CA_RPT_01)
    def test_settle_order_counts_a_dec31_settled_buy(self):
        # Trade-date order would meet the 2026-settled row first and stop.
        rows = [_tx(date="2025-12-29", settle="2026-01-05", qty=10,
                    net=-1000.0, symbol="CCC.US"),
                _tx(date="2025-12-30", settle="2025-12-31", qty=100,
                    net=-150000.0, symbol="AAA.US")]
        w = self.walk(rows)
        self.assertEqual(w["max_total_cost"], 150000.0)
        self.assertEqual(w["max_total_date"], "2025-12-31")

    @rule(R.CA_RPT_01)
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

    @rule(R.CA_RPT_12)
    def test_out_of_year_rows_are_not_walked(self):
        w = self.walk([_tx(date="2025-03-03", qty=10, net=-1000.0),
                       _tx(date="2026-01-05", qty=10, net=-500000.0)])
        self.assertEqual(w["max_total_cost"], 1000.0)
        self.assertEqual(w["per_symbol"]["AAPL.US"]["year_end_cost"],
                         1000.0)

    @rule(R.CA_RPT_12)
    def test_non_capital_row_with_a_quantity_is_skipped(self):
        w = self.walk([_tx(date="2025-02-03", qty=1.0, net=-50000.0,
                           symbol="BTC"),
                       _tx(action="FEE", date="2025-03-03", qty=-0.1,
                           net=-25.0, symbol="BTC"),
                       _tx(action="INTEREST", date="2025-04-03", qty=0.5,
                           net=30.0, symbol="BTC")])
        self.assertEqual(w["per_symbol"]["BTC"]["year_end_cost"], 50000.0)
        self.assertEqual(w["max_total_cost"], 50000.0)

    @rule(R.CA_RPT_12)
    def test_partial_close_keeps_average_cost(self):
        w = self.walk([_tx(date="2025-02-03", qty=100, net=-10000.0),
                       _tx(date="2025-03-03", qty=-40, net=5000.0)])
        s = w["per_symbol"]["AAPL.US"]
        self.assertEqual(s["max_cost"], 10000.0)
        self.assertEqual(s["year_end_cost"], 6000.0)

    @rule(R.CA_RPT_12)
    def test_short_cover_crossing_to_long(self):
        # Short 100 (proceeds 5,000), buy 140 for 7,000: covers 100 and
        # opens a long 40 at 7,000 x 40/140 = 2,000.
        w = self.walk([_tx(date="2025-02-03", qty=-100, net=5000.0),
                       _tx(date="2025-03-03", qty=140, net=-7000.0)])
        s = w["per_symbol"]["AAPL.US"]
        self.assertEqual(s["year_end_cost"], 2000.0)
        self.assertEqual(w["max_total_cost"], 2000.0)

    @rule(R.CA_RPT_12)
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

    @rule(R.CA_RPT_12)
    def test_sub_micro_crypto_rows_add_cost(self):
        # S052-02: 20 staking rewards of 9e-7 BTC at 100,000 CAD/BTC were
        # skipped by the 1e-6 share epsilon (the engine books them).
        rows = [_tx(date="2025-02-03", qty=0.999995, net=-99999.50,
                    symbol="BTC")]
        rows += [_tx(date=f"2025-03-{d:02d}", qty=9e-7, net=-0.09,
                     symbol="BTC") for d in range(1, 21)]
        w = self.walk(rows)
        self.assertAlmostEqual(w["max_total_cost"], 100001.30, places=2)

    @rule(R.CA_RPT_12)
    def test_expired_long_option_is_named(self):
        # S052-22: a long option still in the books after its expiry.
        rows = [_tx(date="2025-02-03", qty=100, net=-40001.0,
                    symbol="ZZQ250620C00015000.US")]
        w = self.walk(rows)
        self.assertEqual(w["expired_options_held"],
                         ["ZZQ250620C00015000.US"])


class TestT1135Report(unittest.TestCase):
    """Formerly: test_fix_l_filing_b.TestT1135Report"""
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

    @rule(R.CA_RPT_01)
    def test_threshold_boundaries(self):
        # R1-314: required only ABOVE 100,000; Part A only BELOW 250,000
        # (no part where no filing is required: null, TJ-4956).
        for cost, required, simplified in (
                (100000.00, False, None), (100000.01, True, True),
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
        # A cost unknown under the Part A threshold leaves the part
        # open (TJ-4956); with every cost known it is Part A.
        self.assertIn("Part A or Part B open (the cost of QQQ.US is "
                      "unknown; the detailed method from 250,000.00 CAD",
                      text)
        known, _ = self._rep([_tx(qty=100, net=-150000.0)])
        self.assertIn("Simplified method (Part A) available (stayed under "
                      "250,000.00 CAD", T1.render_report(known))
        self.assertIn("unknown ACB (bought before the data) — cost understated",
                      text)
        self.assertIn("unclassified — review / add a ticker.map T1135 "
                      "line", text)
        self.assertIn("T1135 instructions", text)

    @rule(R.CA_RPT_01)
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
