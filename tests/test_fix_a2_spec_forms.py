"""Re-audit-2 conformance-spec fixes in the filing forms.

- A2-0166 / A2-1482: the 2024 Schedule 3 splits Part 3 into Period 1
  (January 1 to June 24, 2024: shares 10689/10690, bonds, crypto and
  other properties 10693/10694) and Period 2 (June 25 to December 31:
  13199/13200 and 15199/15300). Slip lines 17399/17599 vs 17400/17600.
- A2-0482 / A2-0829: from tax year 2025, Form 8949 reports digital-asset
  dispositions on boxes G/H/I (short-term) and J/K/L (long-term), apart
  from the securities' A/B/C and D/E/F.

All data is synthetic (invented tickers, fake account names).
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.bin import taxjson_filed
from taxjson.bin import taxjson_form_export as FE


def ca(symbol, proceeds, cost, date_, settle=None, gain=None, **extra):
    e = {"date": date_, "date_settle": settle or date_, "symbol": symbol,
         "qty": -10, "proceeds": proceeds, "cost": cost,
         "gain": (proceeds - cost) if gain is None else gain,
         "disallowed_amount": 0.0, "days_held": 30, "term": None,
         "direction": "LONG", "commission": 0.0, "fee": 0.0,
         "account": "margin", "currency": "CAD", "is_option": False}
    e.update(extra)
    return e


def us(symbol, proceeds, cost, date_="2025-05-02", term="SHORT_TERM",
       days_held=20, **extra):
    e = {"date": date_, "date_settle": date_, "symbol": symbol, "qty": -1,
         "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
         "disallowed_amount": 0.0, "days_held": days_held, "term": term,
         "direction": "LONG", "commission": 0.0, "fee": 0.0,
         "account": "margin", "currency": "USD", "is_option": False}
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


# A 2024 book: one symbol sold in each period, a call written in period 1
# (grant timing: a short row, premium as proceeds), a coin sold on Jun 10.
XYZ_P1 = ca("XYZ.US", 5000.0, 4000.0, "2024-03-15")
XYZ_P2 = ca("XYZ.US", 6000.0, 4000.0, "2024-08-15")
CALL_P1 = ca("XYZ240621C00050000.US", -0.0, -200.0, "2024-02-01",
             gain=200.0, direction="SHORT", is_option=True, grant=True)
COIN_P1 = ca("BTC", 900.0, 1000.0, "2024-06-10", account="kr1")


class TestSchedule3Periods2024(unittest.TestCase):
    """A2-0166, A2-1482."""

    @rule("CA-DISP-03")
    def test_period_one_codes_and_split_symbol(self):
        rep = FE.build_schedule3([XYZ_P1, XYZ_P2, CALL_P1]
                                 + FE.mark_crypto([COIN_P1]), 2024)
        got = sorted((r["symbol"], r["proceeds_line"], r["gain_line"],
                      r["proceeds"], r["gain"]) for r in rep["rows"])
        self.assertEqual(got, sorted([
            ("XYZ.US", "10689", "10690", 5000.0, 1000.0),
            ("XYZ.US", "13199", "13200", 6000.0, 2000.0),
            ("XYZ240621C00050000.US", "10693", "10694", 200.0, 200.0),
            ("BTC", "10693", "10694", 900.0, -100.0),
        ]))
        t = rep["totals"]
        self.assertEqual(t["proceeds_10689"], 5000.0)
        self.assertEqual(t["gain_10690"], 1000.0)
        self.assertEqual(t["proceeds_10693"], 1100.0)
        self.assertEqual(t["gain_10694"], 100.0)
        self.assertEqual(t["proceeds_13199"], 6000.0)
        self.assertEqual(t["gain_13200"], 2000.0)
        self.assertNotIn("gain_15300", t)
        self.assertEqual(t["gain_all"], 3100.0)
        # Period 1 lines first, each titled with its period.
        self.assertEqual([(ln["proceeds_code"], ln["period"])
                          for ln in rep["lines"]],
                         [("10689", 1), ("10693", 1), ("13199", 2)])
        self.assertIn("Period 1", rep["lines"][0]["title"])
        self.assertIn("June 24, 2024", rep["lines"][0]["label"])
        self.assertIn("Period 2", rep["lines"][2]["title"])

    @rule("CA-DISP-03")
    def test_period_boundary_follows_the_tax_date(self):
        # Traded Jun 24, settled Jun 25: the settle date (the default
        # Canadian disposition date) is in Period 2, the trade date in 1.
        e = ca("ABC.TO", 100.0, 50.0, "2024-06-24", settle="2024-06-25")
        settle = FE.build_schedule3([e], 2024)["rows"][0]
        trade = FE.build_schedule3([e], 2024, date_key="date")["rows"][0]
        self.assertEqual(settle["gain_line"], "13200")
        self.assertEqual(trade["gain_line"], "10690")
        last_p1 = ca("ABC.TO", 100.0, 50.0, "2024-06-24")
        self.assertEqual(FE.build_schedule3([last_p1], 2024)["rows"][0]
                         ["gain_line"], "10690")

    @rule("CA-DISP-03")
    def test_other_years_have_no_period_codes(self):
        for y in ("2023", "2025"):
            rep = FE.build_schedule3(
                [ca("XYZ.US", 5000.0, 4000.0, f"{y}-03-15")], int(y))
            self.assertEqual(rep["rows"][0]["gain_line"], "13200", y)
            self.assertNotIn("Period", rep["lines"][0]["title"])

    @rule("CA-DISP-03")
    def test_text_names_both_periods_and_the_slip_lines(self):
        rep = FE.build_schedule3([XYZ_P1, XYZ_P2], 2024)
        text = FE.render_schedule3(rep, 2024, "CAD")
        self.assertIn("Line 10689 (proceeds of disposition): 5,000.00", text)
        self.assertIn("Line 13200 (gain/loss): 2,000.00", text)
        self.assertIn("17399", text)
        self.assertIn("17599", text)
        self.assertIn("June 25", text)

    @rule("CA-DISP-03")
    def test_filing_lines_and_lock_carry_the_periods(self):
        lines = FE.filing_lines([XYZ_P1, XYZ_P2], 2024)
        self.assertEqual([ln["gain_code"] for ln in lines],
                         ["10690", "13200"])
        doc = {"summary": {"tax_date_basis": "settle"},
               "transactions": [XYZ_P1, XYZ_P2, CALL_P1]}
        agg = taxjson_filed.aggregates_from_gains(doc, year=2024)
        self.assertEqual(agg["form_lines"], {
            "10689": 5000.0, "10690": 1000.0, "10693": 200.0,
            "10694": 200.0, "13199": 6000.0, "13200": 2000.0})

    @rule("CA-DISP-03")
    def test_old_2024_lock_is_compared_on_period_two_codes(self):
        # A lock closed before the split put every 2024 disposition on
        # 13199/13200 and 15199/15300: the same dollars are no drift.
        old = {"year": 2024, "accounts": {"margin": {
            "realized": 3200.0, "form_lines": {
                "13199": 11000.0, "13200": 3000.0,
                "15199": 200.0, "15300": 200.0}}}}
        doc = {"summary": {"tax_date_basis": "settle"},
               "transactions": [XYZ_P1, XYZ_P2, CALL_P1]}
        cur = {"margin": taxjson_filed.aggregates_from_gains(doc,
                                                             year=2024)}
        notes = []
        self.assertEqual(taxjson_filed.diff_snapshot(old, cur, notes=notes),
                         [])
        self.assertEqual(len(notes), 1)
        self.assertIn("Period 1", notes[0])
        # A real change is still drift on the folded codes.
        moved = json.loads(json.dumps(old))
        moved["accounts"]["margin"]["form_lines"]["13200"] = 2900.0
        lines = taxjson_filed.diff_snapshot(moved, cur)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("form line 13200", lines[0])

    @rule("CA-DISP-03")
    def test_carryover_reads_period_one_gain_lines(self):
        from taxjson.bin.taxjson_carryover import lock_figure
        lock = {"year": 2024, "totals": {"realized": 3200.0},
                "accounts": {"margin": {"form_lines": {
                    "10689": 5000.0, "10690": 1000.0, "10693": 200.0,
                    "10694": 200.0, "13199": 6000.0, "13200": 2000.0}}}}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "2024.json"
            p.write_text(json.dumps(lock))
            fig = lock_figure(p, "canada")
        self.assertEqual(fig["source"], "form_lines")
        self.assertAlmostEqual(fig["figure"], 3200.0, places=2)


# A 2025 US book: a stock and a coin (crypto = true account) in each term.
STOCK_ST = us("AAA.US", 1200.0, 1000.0)
STOCK_LT = us("BBB.US", 3000.0, 2000.0, term="LONG_TERM", days_held=500)
COIN_ST = us("BTC", 5000.0, 4000.0, account="kr1")
COIN_LT = us("ETH", 2500.0, 3000.0, term="LONG_TERM", days_held=600,
             account="kr1")


class TestForm8949DigitalAssets(unittest.TestCase):
    """A2-0482, A2-0829."""

    def _rep(self, year=2025):
        coins = FE.mark_crypto([dict(COIN_ST, date=f"{year}-05-02",
                                     date_settle=f"{year}-05-02"),
                                dict(COIN_LT, date=f"{year}-05-02",
                                     date_settle=f"{year}-05-02")])
        stocks = [dict(STOCK_ST, date=f"{year}-05-02"),
                  dict(STOCK_LT, date=f"{year}-05-02")]
        return FE.build_8949(stocks + coins, year)

    @rule("US-RPT-11")
    def test_2025_coins_go_on_boxes_g_to_l(self):
        rep = self._rep()
        boxes = {r["description"].split()[-1]: r["boxes"]
                 for p in ("I", "II") for r in rep[f"part_{p}"]}
        self.assertEqual(boxes, {"AAA.US": "A/B/C", "BBB.US": "D/E/F",
                                 "BTC": "G/H/I", "ETH": "J/K/L"})
        groups = {(g["part"], g["boxes"]): g["totals"]["gain"]
                  for g in rep["groups"]}
        self.assertEqual(groups, {("I", "A/B/C"): 200.0,
                                  ("I", "G/H/I"): 1000.0,
                                  ("II", "D/E/F"): 1000.0,
                                  ("II", "J/K/L"): -500.0})
        # The part totals (to Schedule D) still cover every row.
        self.assertEqual(rep["part_I_totals"]["gain"], 1200.0)
        self.assertEqual(rep["part_II_totals"]["gain"], 500.0)

    @rule("US-RPT-11")
    def test_before_2025_coins_stay_on_a_to_f(self):
        rep = self._rep(2024)
        self.assertEqual({r["boxes"] for p in ("I", "II")
                          for r in rep[f"part_{p}"]}, {"A/B/C", "D/E/F"})
        self.assertEqual(len(rep["groups"]), 2)

    @rule("US-RPT-11")
    def test_text_and_sum_rows_name_the_boxes(self):
        rep = self._rep()
        text = FE.render_8949(rep, 2025, "USD")
        self.assertIn("BOX G, H OR I", text)
        self.assertIn("BOX J, K OR L", text)
        self.assertIn("Form 1099-DA", text)
        self.assertNotIn("Check the correct 8949 box (A/B/C, D/E/F) "
                         "against", text)
        parts = FE.filing_parts_8949(
            [STOCK_ST, STOCK_LT] + FE.mark_crypto([COIN_ST, COIN_LT]), 2025)
        self.assertEqual([(p["part"], p["boxes"], p["gain"]) for p in parts],
                         [("I", "A/B/C", 200.0), ("I", "G/H/I", 1000.0),
                          ("II", "D/E/F", 1000.0), ("II", "J/K/L", -500.0)])
        self.assertIn("digital assets", parts[1]["label"])

    @rule("US-RPT-11")
    def test_lock_keeps_the_digital_asset_boxes_apart(self):
        doc = {"summary": {"tax_date_basis": "trade"},
               "transactions": [COIN_ST, COIN_LT]}
        fl = taxjson_filed.aggregates_from_gains(doc, crypto=True,
                                                 year=2025)["form_lines"]
        self.assertEqual(fl["I_gain"], 1000.0)
        self.assertEqual(fl["I_da_gain"], 1000.0)
        self.assertEqual(fl["II_da_gain"], -500.0)
        # A lock written before the split recorded only the part totals:
        # the digital-asset keys it lacks are not drift.
        old = {"accounts": {"kr1": {"realized": 500.0, "form_lines": {
            k: v for k, v in fl.items() if "_da_" not in k}}}}
        cur = {"kr1": {"realized": 500.0, "form_lines": fl}}
        self.assertEqual(taxjson_filed.diff_snapshot(old, cur), [])

    @rule("US-RPT-11", "US-RPT-03")
    def test_txf_leaves_digital_assets_out_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains.json"
            g.write_text(json.dumps({"transactions": [STOCK_ST]}))
            k = Path(td) / "kr1_gains.json"
            k.write_text(json.dumps({"transactions": [COIN_ST]}))
            out = Path(td) / "o.txf"
            rc, _o, err = _main([str(g), str(k), "--crypto", str(k),
                                 "--form", "txf", "--country", "usa",
                                 "--year", "2025", "--out", str(out)])
            self.assertEqual(rc, 0, err)
            doc = out.read_text()
        self.assertIn("PAAA.US", doc.replace("P1 ", "P"))
        self.assertNotIn("BTC", doc)
        self.assertIn("1 digital-asset", err)
        self.assertIn("G-L", err)

    @rule("US-RPT-11")
    def test_cli_8949_with_a_crypto_book(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains.json"
            g.write_text(json.dumps({"transactions": [STOCK_ST]}))
            k = Path(td) / "kr1_gains.json"
            k.write_text(json.dumps({"transactions": [COIN_ST]}))
            rc, out, err = _main([str(g), str(k), "--crypto", str(k),
                                  "--form", "8949", "--country", "usa",
                                  "--year", "2025", "--json"])
        self.assertEqual(rc, 0, err)
        rep = json.loads(out)
        self.assertEqual([r["boxes"] for r in rep["part_I"]],
                         ["A/B/C", "G/H/I"])


if __name__ == "__main__":
    unittest.main()
