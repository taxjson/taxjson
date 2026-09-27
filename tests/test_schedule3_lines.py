"""Schedule 3 line routing and row footing (2026-09 filing audit).

The 2025 Schedule 3 (Part 3) has line 4 publicly traded shares
(13199/13200), line 6 bonds, debentures, promissory notes and other
similar properties — options per T4037 — (15199/15300) and line 7
crypto-assets (15200/15301; before 2025 crypto went on 15199/15300).
Every exported row must foot: proceeds − ACB − outlays = allowed gain.
"""
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_form_export import (build_8949, build_schedule3,
                                             filing_lines, filing_parts_8949,
                                             filing_totals, main, mark_crypto,
                                             property_class)

REPO_ROOT = Path(__file__).resolve().parent.parent


def ent(symbol, proceeds, cost, gain=None, disallowed=0.0, commission=0.0,
        date="2025-06-10", qty=-10, direction="LONG", **extra):
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost,
         "gain": (proceeds - cost + disallowed) if gain is None else gain,
         "disallowed_amount": disallowed, "days_held": 30, "term": None,
         "direction": direction, "commission": commission, "fee": 0.0,
         "account": "margin", "is_option": False}
    e.update(extra)
    return e


SHARE = ent("SHOP.TO", 9000.0, 7000.0, commission=9.99)
OPTION = ent("AAPL250620C00200000.US", 450.0, 100.0, commission=1.30)
FUTURE = ent("F:CL250620P00053000.US", 800.0, 1000.0)
# NVDA-shaped superficial loss: net 1,954.12 after a 1.40 commission,
# cost 11,536.54, the whole 9,582.42 loss denied -> allowed gain 0.
DENIED = ent("NVDA.US", 1954.12, 11536.54, disallowed=9582.42,
             commission=1.40)
COIN = ent("BTC", 40000.0, 50000.0, account="kr1")


class TestRouting(unittest.TestCase):
    def test_property_classes(self):
        self.assertEqual(property_class(SHARE), "shares")
        self.assertEqual(property_class(OPTION), "option")
        self.assertEqual(property_class(FUTURE), "futures")
        self.assertEqual(property_class(mark_crypto([COIN])[0]), "crypto")
        self.assertEqual(property_class(COIN), "shares")   # unflagged

    def test_2025_lines_and_totals(self):
        rep = build_schedule3([SHARE, OPTION, FUTURE] + mark_crypto([COIN]),
                              2025)
        by = {r["symbol"]: r for r in rep["rows"]}
        self.assertEqual((by["SHOP.TO"]["line"], by["SHOP.TO"]["proceeds_line"],
                          by["SHOP.TO"]["gain_line"]), ("4", "13199", "13200"))
        self.assertEqual((by[OPTION["symbol"]]["line"],
                          by[OPTION["symbol"]]["proceeds_line"],
                          by[OPTION["symbol"]]["gain_line"]),
                         ("6", "15199", "15300"))
        self.assertEqual(by[FUTURE["symbol"]]["line"], "6")
        self.assertIn("futures", by[FUTURE["symbol"]]["notes"])
        self.assertEqual((by["BTC"]["line"], by["BTC"]["proceeds_line"],
                          by["BTC"]["gain_line"]), ("7", "15200", "15301"))
        t = rep["totals"]
        self.assertAlmostEqual(t["proceeds_13199"], 9009.99, places=2)
        self.assertAlmostEqual(t["gain_13200"], 2000.0, places=2)
        self.assertAlmostEqual(t["proceeds_15199"], 451.30 + 800.0, places=2)
        self.assertAlmostEqual(t["gain_15300"], 350.0 - 200.0, places=2)
        self.assertAlmostEqual(t["proceeds_15200"], 40000.0, places=2)
        self.assertAlmostEqual(t["gain_15301"], -10000.0, places=2)
        self.assertEqual([ln["line"] for ln in rep["lines"]], ["4", "6", "7"])
        self.assertEqual(rep["lines"][0]["title"],
                         "Part 3, line 4 (lines 13199/13200)")

    def test_2024_crypto_goes_with_other_properties(self):
        coin = mark_crypto([dict(COIN, date="2024-06-10",
                                 date_settle="2024-06-10")])
        rep = build_schedule3([OPTION] + coin, 2024)
        codes = {r["symbol"]: (r["proceeds_line"], r["gain_line"])
                 for r in rep["rows"]}
        self.assertEqual(codes["BTC"], ("15199", "15300"))
        self.assertNotIn("proceeds_15200", rep["totals"])
        self.assertEqual(len(rep["lines"]), 1)
        self.assertAlmostEqual(rep["totals"]["gain_15300"], 350.0 - 10000.0,
                               places=2)
        self.assertIn("crypto", rep["lines"][0]["label"])

    def test_year_inferred_from_dates(self):
        rep = build_schedule3(mark_crypto([COIN]))
        self.assertEqual(rep["rows"][0]["gain_line"], "15301")


class TestFooting(unittest.TestCase):
    def test_denied_row_foots_with_reduced_acb(self):
        rep = build_schedule3([DENIED], 2025)
        r = rep["rows"][0]
        self.assertAlmostEqual(r["proceeds"], 1955.52, places=2)
        self.assertAlmostEqual(r["outlays"], 1.40, places=2)
        self.assertAlmostEqual(r["gain"], 0.0, places=2)
        # Before the fix the row showed ACB 11,536.54 and did not foot.
        self.assertAlmostEqual(r["acb"], 1954.12, places=2)
        self.assertAlmostEqual(r["proceeds"] - r["acb"] - r["outlays"],
                               r["gain"], places=2)
        self.assertAlmostEqual(r["denied"], 9582.42, places=2)
        self.assertIn("ACB shown reduced", r["notes"])
        self.assertIn("replacement", r["notes"])

    def test_every_row_and_line_foots(self):
        ents = [SHARE, OPTION, FUTURE, DENIED,
                ent("SHOP.TO", 100.0, 300.0, disallowed=50.0,
                    date="2025-09-01"),
                ent("TSLA.US", -4000.0, -4500.0, gain=500.0,
                    direction="SHORT")] + mark_crypto([COIN])
        rep = build_schedule3(ents, 2025)
        for r in rep["rows"] + rep["lines"]:
            self.assertAlmostEqual(r["proceeds"] - r["acb"] - r["outlays"],
                                   r["gain"], places=2, msg=r)
        t = filing_totals(ents, 2025)
        self.assertAlmostEqual(t["proceeds"],
                               sum(ln["proceeds"] for ln in rep["lines"]),
                               places=2)
        self.assertAlmostEqual(t["gain"], rep["totals"]["gain_all"], places=2)
        self.assertEqual(filing_lines(ents, 2025), rep["lines"])

    def test_text_csv_json(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains_wash.json"
            g.write_text(json.dumps({"transactions": [SHARE, OPTION, DENIED]}))
            c = Path(td) / "kr1_gains.json"
            c.write_text(json.dumps({"transactions": [COIN]}))
            out_csv = Path(td) / "s3.csv"
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = main([str(g), str(c), "--crypto", str(c), "--form",
                           "schedule3", "--year", "2025", "--csv",
                           str(out_csv)])
            self.assertEqual(rc, 0)
            text = out.getvalue()
            self.assertNotIn("section 3", text)
            self.assertIn("PART 3, LINE 4 (LINES 13199/13200)", text)
            self.assertIn("PART 3, LINE 6 (LINES 15199/15300)", text)
            self.assertIn("PART 3, LINE 7 (LINES 15200/15301)", text)
            self.assertIn("Line 15301 (gain/loss): -10,000.00", text)
            with out_csv.open() as fh:
                rows = list(csv.DictReader(fh))
            by = {r["symbol"]: r for r in rows}
            self.assertEqual(by["BTC"]["proceeds_line"], "15200")
            self.assertEqual(by["NVDA.US"]["acb"], "1954.12")
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                main([str(g), "--crypto", str(c), "--form", "schedule3",
                      "--year", "2025", "--json"])
            rep = json.loads(out.getvalue())
            self.assertEqual([ln["line"] for ln in rep["lines"]],
                             ["4", "6", "7"])
            self.assertTrue(all("_crypto" not in r for r in rep["rows"]))


def _project(td, country="canada", crypto=False):
    root = Path(td)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{"USD" if country == "usa" else "CAD"}"\n'
        '[accounts.margin]\ntype = "taxable"\n'
        + ('[accounts.kr1]\ntype = "taxable"\ncrypto = true\n'
           if crypto else ""))
    (root / "work").mkdir()
    return root


def _cli(root, *a):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *a], cwd=REPO_ROOT,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


class TestSumReturnBlock(unittest.TestCase):
    def test_canada_rows_per_line_equal_form_export(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, crypto=True)
            (root / "work" / "margin_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025},
                 "transactions": [SHARE, OPTION, DENIED]}))
            (root / "work" / "kr1_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025}, "transactions": [COIN]}))
            r = _cli(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            f = json.loads(r.stdout)["filing"]
            lines = {ln["line"]: ln for ln in f["lines"]}
            self.assertEqual(set(lines), {"4", "6", "7"})
            self.assertEqual(lines["7"]["gain_code"], "15301")
            self.assertAlmostEqual(lines["7"]["gain"], -10000.0)
            fe = _cli(root, "form-export", "--json")
            self.assertEqual(fe.returncode, 0, fe.stderr)
            rep = json.loads(fe.stdout)
            self.assertEqual(f["lines"], rep["lines"])
            for ln in f["lines"]:
                self.assertAlmostEqual(
                    ln["proceeds"] - ln["acb"] - ln["outlays"], ln["gain"],
                    places=2)
            t = _cli(root, "sum")
            self.assertEqual(t.returncode, 0, t.stderr)
            self.assertIn("Line 4 shares & fund units (13199/13200)", t.stdout)
            self.assertIn("Line 6 options & other properties (15199/15300)",
                          t.stdout)
            self.assertIn("Line 7 crypto-assets (15200/15301)", t.stdout)
            # The old footer claimed COST *includes* the denied loss.
            self.assertNotIn("COST includes", t.stdout)
            self.assertIn("ACB is REDUCED", t.stdout)
            self.assertIn("fx-cash", t.stdout.split("FOR THE RETURN")[1])

    def test_us_block_is_8949_totals(self):
        us = [dict(ent("AAPL.US", 9000.0, 9600.0, disallowed=250.0),
                   term="SHORT_TERM"),
              dict(ent("MSFT.US", 20000.0, 15000.0, date="2025-08-01"),
                   term="LONG_TERM"),
              dict(ent("TSLA.US", -4000.0, -4500.0, gain=500.0,
                       direction="SHORT"), term="SHORT_TERM")]
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa")
            (root / "work" / "margin_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025}, "transactions": us}))
            r = _cli(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            f = json.loads(r.stdout)["filing"]
            rep = build_8949(us)
            want = {k: round(rep["part_I_totals"][k]
                             + rep["part_II_totals"][k], 2)
                    for k in ("proceeds", "cost", "adjustment", "gain")}
            self.assertEqual(f["totals"], want)
            self.assertEqual(f["parts_8949"], filing_parts_8949(us))
            t = _cli(root, "sum")
            self.assertIn("(g) ADJUSTMENT", t.stdout)
            self.assertIn("250.00", t.stdout.split("FOR THE RETURN")[1])
            self.assertIn("(d) − (e) + (g) = (h)", t.stdout)


if __name__ == "__main__":
    unittest.main()
