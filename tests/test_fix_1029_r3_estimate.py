"""Fix round 3 of 2026-10-29, the estimate group.

TJ-0591  a year the minimum tax applies, form T2036 is the minimum-tax
         year's: Ontario's and Alberta's line 2 is the special foreign
         tax credit, Alberta's tax otherwise payable includes its
         additional tax for minimum tax purposes, and each province's
         additional tax is its factor of the federal figure its form
         names (ON: T691 Part 5 line 11; AB: Part 7 line 1 minus line
         2; BC: Part 7 line 14).
TJ-0618  a dividend's withholding in another currency is converted at
         its own day's rate before it comes off the dividend's net in
         the native-currency books (it was subtracted unconverted).
TJ-0612  a Canadian estimate with no close-year lock and nothing entered
         says its net capital losses carried in are 0, and why.

The expected figures are worked by hand from the 2025 tables (federal
14.5% to 57,375, 20.5% to 114,750, BPA 16,129 phased to 14,538 over  # pii-ok
177,882-253,414; ON 5.05% to 52,886, 9.15% to 105,775, BPA 12,747,  # pii-ok
DTC 10%, surtax 20% over 5,710 and 36% over 7,307, AMT factor 24.63%;
AB 8% to 60,000, AMT factor 35%; BC 5.06%, AMT factor 34.9%; AMT  # pii-ok
20.5% over 177,882). Synthetic books only: made-up tickers (ZZQ.US,  # pii-ok
XYZ.TO), the account `margin`.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib import amt_report as AR
from taxjson.lib import tax_estimate as TE
from taxjson.lib.dispatch import python_module_argv

SRC = Path(__file__).resolve().parent.parent / "src"


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON",
                year=2025)
    args.update(kw)
    return TE.estimate_canada(**args)


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs -C root args...` from this tree's sources (width 0)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


def _ok(r: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    if r.returncode != 0:
        raise AssertionError(f"rc {r.returncode}\n{r.stdout[-2000:]}\n"
                             f"{r.stderr[-2000:]}")
    return r


def _project(tmp: str, book: str, tail: str = "",
             province: str = "ON") -> Path:
    root = Path(tmp) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\n'
        'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        f'source_currencies = []\nprovince = "{province}"\n'
        '[accounts.margin]\ntype = "taxable"\n' + tail)
    (root / "inputs" / "margin" / "book.tt").write_text(book)
    _ok(_cli(root, "run", "--no-input"))
    return root


# --------------------------------------------------------------- TJ-0591
# The backlog's case: a 400,000 gain offset by 400,000 of net capital  # pii-ok
# losses of other years, a foreign dividend of 1,000 with 150 withheld,  # pii-ok
# no other income. Regular tax: taxable income 1,000, no federal or  # pii-ok
# provincial tax, no credit. AMT: adjusted taxable income 201,000;  # pii-ok
# (201,000 - 177,882) x 20.5% = 4,739.19; BPA 16,129 - 1,591 x 23,118 /  # pii-ok
# 75,532 = 15,642.0443, its credit x 14.5% x 50% = 1,134.0482: minimum  # pii-ok
# amount (T691 line 103) 3,605.1418. Special credit: 2/3 x 150 = 100  # pii-ok
# (< 20.5% x 1,000): net minimum tax 3,505.1418, all of it the excess.  # pii-ok
_CASE = dict(realized=400000.0, other_losses=400000.0, foreign_div=1000.0,
             actual_withheld=150.0)


class TestMinimumTaxYearProvincialCredit(unittest.TestCase):

    @rule("CA-EST-FTC", "CA-EST-AMT")
    def test_alberta(self):
        r = _ca(province="AB", **_CASE)
        a = r["amt"]
        self.assertTrue(a["binding"])
        self.assertEqual(a["minimum_amount"], 3605.14)
        self.assertEqual(a["basic_fed"], 0.0)
        self.assertEqual(a["excess_fed"], 3505.14)
        # AB428 line 67: (T691 Part 7 line 1 - line 2) x 35% =
        # 3,605.1418 x 35% = 1,261.7996 (it was the excess x 35%:  # pii-ok
        # 1,226.80).  # pii-ok
        self.assertEqual(a["provincial_amt_base_kind"], "before_ftc")
        self.assertEqual(a["provincial_amt_base"], 3605.14)
        self.assertEqual(a["provincial_amt"], 1261.8)
        # T2036: line 2 is the special credit, 100; line 3 = 150 - 100 =
        # 50; line 4 = (AB428 line 63 + line 68: 0 + 0 + 1,261.80) x  # pii-ok
        # 1,000 / 1,000 = 1,261.80: the credit is 50 (the regular  # pii-ok
        # year's is 0: no AB tax).
        self.assertEqual(a["provincial_ftc_regular"], 0.0)
        self.assertEqual(a["provincial_ftc_min_tax"], 50.0)
        self.assertEqual(a["provincial_ftc_change"], -50.0)
        # AB tax 1,261.7996 - 50 = 1,211.80; top-up 3,505.1418 +  # pii-ok
        # 1,211.7996 = 4,716.9414 (it was 4,731.94).  # pii-ok
        self.assertEqual(a["topup"], 4716.94)
        self.assertEqual(r["estimated_tax_with_amt"],
                         round(r["estimated_tax"] + 4716.9414, 2))
        notes = " ".join(r["notes"])
        self.assertIn("AB AMT = 35.00% of the minimum amount over basic "
                      "federal tax", notes)
        self.assertIn("Foreign tax credit with AMT: the special foreign tax "
                      "credit takes 100.00 of the 150.00 (T691 Part 4) and "
                      "AB tax 50.00 (form T2036 in a minimum-tax year: line "
                      "2 is the special credit", notes)
        self.assertNotIn("still starts from the regular", r["assumptions"])

    @rule("CA-EST-FTC", "CA-EST-AMT")
    def test_british_columbia(self):
        r = _ca(province="BC", **_CASE)
        a = r["amt"]
        # BC428 line 69: T691 Part 7 line 14 x 34.9%. Line 14 (s.120.2(3))
        # = 3,605.1418 - (basic federal tax 0 + (100 - 0) x 100 / 150) =  # pii-ok
        # 3,538.4751; x 34.9% = 1,234.9278 (it was the excess x 34.9%:  # pii-ok
        # 1,223.29).  # pii-ok
        self.assertEqual(a["additional_tax_s120_2"], 3538.48)
        self.assertEqual(a["provincial_amt_base_kind"], "s120_2")
        self.assertEqual(a["provincial_amt"], 1234.93)
        # T2036 line 2 is still T2209 line 3 (0); BC tax otherwise payable
        # excludes the additional tax: 0, so no credit either way.
        self.assertEqual(a["provincial_ftc_min_tax"], 0.0)
        self.assertEqual(a["provincial_ftc_change"], 0.0)
        # 3,505.1418 + 1,234.9278 = 4,740.0696.  # pii-ok
        self.assertEqual(a["topup"], 4740.07)
        notes = " ".join(r["notes"])
        self.assertIn("BC AMT = 34.90% of the federal additional tax of "
                      "s.120.2(3) (T691 Part 7 line 14; BC428 line 69)",
                      notes)

    @rule("CA-EST-FTC", "CA-EST-AMT")
    def test_ontario_unchanged_without_ontario_tax(self):
        # ON: Worksheet ON428 line 72 is 24.63% of T691 Part 5 line 11,
        # the excess: 863.3164; its tax otherwise payable (line 81 with
        # line 72 at 0) is 0, so no credit: as before.
        a = _ca(**_CASE)["amt"]
        self.assertEqual(a["provincial_amt_base_kind"], "excess")
        self.assertEqual(a["provincial_amt"], 863.32)
        self.assertEqual(a["provincial_ftc_min_tax"], 0.0)
        self.assertEqual(a["provincial_ftc_change"], 0.0)
        self.assertEqual(a["topup"], 4368.46)

    @rule("CA-EST-FTC", "CA-EST-AMT")
    def test_ontario_credit_against_its_additional_tax(self):
        # Eligible dividends 50,000 (grossed 69,000), a foreign dividend  # pii-ok
        # of 10,000 with 1,500 withheld, the same 400,000 gain and  # pii-ok
        # losses. Taxable income 79,000, net income 279,000: BPA 14,538.  # pii-ok
        # Federal 8,319.375 + 21,625 x 20.5% = 12,752.50 - 2,108.01 =  # pii-ok
        # 10,644.49, less the DTC 10,363.662: 280.828; T2209 limit  # pii-ok
        # 10,644.49 x 10 / 79 = 1,347.40, so the credit is 280.828.  # pii-ok
        # ON basic 2,670.743 + 26,114 x 9.15% - 643.7235 = 4,416.4505,  # pii-ok
        # no surtax, DTC 6,900: no ON tax, so no regular ON credit.  # pii-ok
        r = _ca(eligible_div=50000.0, foreign_div=10000.0,
                actual_withheld=1500.0, realized=400000.0,
                other_losses=400000.0)
        a = r["amt"]
        self.assertEqual(r["trace_with"]["prov_ftc"], 0.0)
        self.assertEqual(r["tax_with"]["provincial"], 750.0)   # the OHP
        # AMT: (260,000 - 177,882) x 20.5% = 16,834.19 - 1,054.005 =  # pii-ok
        # 15,780.185; special credit max(280.828, 2/3 x 1,500 = 1,000) =  # pii-ok
        # 1,000: excess 14,780.185. Line 72: x 24.63% = 3,640.3596 +  # pii-ok
        # the surtax on 4,416.4505 + 3,640.3596 = 8,056.8101 (469.3620 +  # pii-ok
        # 269.9316) = 4,379.6532.  # pii-ok
        self.assertEqual(a["excess_fed"], 14780.18)
        self.assertEqual(a["provincial_amt"], 4379.65)
        # T2036: line 2 = the special credit 1,000; line 3 = 500; line 4  # pii-ok
        # = (line 81 with lines 70 and 72 at 0: 4,416.4505) x 10 / 79 =  # pii-ok
        # 559.0444: the credit is 500, off ON tax with line 72.
        self.assertEqual(a["provincial_ftc_min_tax"], 500.0)
        self.assertEqual(a["provincial_ftc_change"], -500.0)
        # 14,780.185 + 4,379.6532 - 500 = 18,659.8382 (it was 19,159.84).  # pii-ok
        self.assertEqual(a["topup"], 18659.84)
        lines = AR.render(AR.build(r, 2025, "CAD"), details=True)
        row = next(ln for ln in lines
                   if "ON foreign tax credit (T2036)" in ln)
        self.assertIn("-500.00", row)
        self.assertIn("[credit 500.00 with AMT, 0.00 in regular tax]", row)

    @rule("CA-EST-AMT")
    def test_without_foreign_tax_every_base_is_the_excess(self):
        for prov in ("ON", "AB", "BC"):
            a = _ca(province=prov, realized=500000.0)["amt"]
            self.assertTrue(a["binding"])
            self.assertEqual(a["provincial_amt_base"], a["excess_fed"])
            self.assertEqual(a["provincial_ftc_change"], 0.0)

    @rule("CA-EST-FTC")
    def test_a_regular_year_keeps_the_regular_credit(self):
        # Other income 20,000 (the TJ-0367 case): no minimum tax.  # pii-ok
        r = _ca(province="AB", other_income=20000.0, foreign_div=1000.0,
                actual_withheld=150.0)
        a = r["amt"]
        self.assertFalse(a["binding"])
        self.assertEqual(a["provincial_ftc_min_tax"],
                         round(r["trace_with"]["prov_ftc"], 2))
        self.assertEqual(a["provincial_ftc_change"], 0.0)
        self.assertEqual(a["provincial_amt"], 0.0)
        self.assertFalse(any("with AMT" in n for n in r["notes"]))

    @rule("CA-EST-FTC", "CA-EST-AMT", "CA-AMT-01")
    def test_estimate_and_amt_end_to_end(self):
        book = ("BUYSELL 2025-01-02 10:00:00 XYZ.TO 1000 CAD 100 100000 0\n"
                "BUYSELL 2025-03-03 10:00:00 XYZ.TO -1000 CAD 500 500000 0\n"
                "BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0\n"
                "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 100 1000\n"
                "TAX 2025-06-02 10:00:00 ZZQ.US 10 CAD 15 150\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, book, "[estimate]\nother_income = 0\n"
                                       "other_losses = 400000\n",
                            province="AB")
            text = _ok(_cli(root, "estimate")).stdout
            amt = json.loads(_ok(_cli(root, "amt", "--json")).stdout)
            report = _ok(_cli(root, "amt", "--details")).stdout
        self.assertEqual(amt["amt"]["provincial_amt"], 1261.8)
        self.assertEqual(amt["amt"]["provincial_ftc_min_tax"], 50.0)
        self.assertEqual(amt["amt"]["topup"], 4716.94)
        block = text[text.index("AMT CHECK"):].splitlines()
        row = next(ln for ln in block if "=> AMT TOP-UP" in ln)
        self.assertTrue(row.rstrip().endswith("4,716.94"), row)
        row = next(ln for ln in block if ln.startswith("     AB "))
        self.assertTrue(row.rstrip().endswith("1,261.80"), row)
        row = next(ln for ln in block if "AB FTC (T2036)" in ln)
        self.assertIn("-50.00  [credit 50.00 with AMT, 0.00 without]", row)
        row = next(ln for ln in report.splitlines()
                   if "AB foreign tax credit (T2036)" in ln)
        self.assertIn("-50.00", row)
        flat = " ".join(report.split())
        self.assertIn("35.00% of the minimum amount over basic federal tax, "
                      "before the foreign tax credits (T691 Part 7 line 1 "
                      "minus line 2; AB428 line 67) 3,605.14", flat)


# --------------------------------------------------------------- TJ-0618
# A .tt USD DIVIDEND of 10 with a same-day CAD TAX of 2.70. The suite's
# synthetic rate (tests/_hermetic): 1.35 CAD per USD, so the TAX is
# 2.00 USD and the dividend's native net 8.00 USD (it was 10 - 2.70 =
# 7.30, a USD and a CAD figure netted); in CAD 13.50 - 2.70 = 10.80.
_MIXED = ("BUYSELL 2025-01-06 10:00:00 SYN.US 100 USD 1 100 0\n"
          "DIVIDEND 2025-03-03 10:00:00 SYN.US 100 USD 0.1 10\n"
          "TAX 2025-03-03 10:00:00 SYN.US 0 CAD 0 2.7\n")


def _div_rows(path: Path):
    return {t["action"]: t
            for t in json.loads(path.read_text())["transactions"]
            if t.get("action") in ("DIVIDEND", "TAX")}


class TestWithholdingInAnotherCurrency(unittest.TestCase):

    def _pair(self, **tax):
        from taxjson.bin import taxjson_merge2 as M
        d = {"action": "DIVIDEND", "symbol": "SYN.US", "date": "2025-03-03",
             "currency": "USD", "quantity": 100.0, "price": 0.1,
             "gross_amount": 10.0, "net_amount": 10.0}
        t = {"action": "TAX", "symbol": "SYN.US", "date": "2025-03-03",
             "currency": "CAD", "quantity": 0.0, "price": 0.0,
             "gross_amount": 2.7, "net_amount": 2.7}
        t.update(tax)
        return M, d, t

    @rule("CA-INC-05")
    def test_converted_at_its_own_rate(self):
        M, d, t = self._pair()
        rates = {"USD": 1.35, "CAD": 1.0}
        res = M.reconcile_dividend_tax(
            [d, t], rate=lambda cur, day: rates.get(cur))
        # 2.70 CAD / 1.35 = 2.00 USD off the 10 USD dividend.
        self.assertAlmostEqual(d["net_amount"], 8.0, places=8)
        self.assertEqual(res, {"converted": 1, "unconverted": []})
        # The TAX row keeps its own currency and amount; its per-share
        # rate is in that currency.
        self.assertEqual((t["currency"], t["net_amount"]), ("CAD", 2.7))
        self.assertEqual((t["quantity"], t["price"]), (100.0, 0.027))

    @rule("CA-INC-05")
    def test_no_rate_leaves_it_out_of_the_net(self):
        M, d, t = self._pair()
        res = M.reconcile_dividend_tax([d, t])
        self.assertEqual(d["net_amount"], 10.0)        # not 7.30
        self.assertEqual(res["unconverted"],
                         [("SYN.US", "2025-03-03", "CAD", "USD")])
        # A rate missing for the day: the same.
        M, d, t = self._pair()
        res = M.reconcile_dividend_tax([d, t], rate=lambda cur, day: None)
        self.assertEqual(d["net_amount"], 10.0)
        self.assertEqual(len(res["unconverted"]), 1)

    @rule("CA-INC-05")
    def test_same_currency_withholding_unchanged(self):
        # A USD withholding of a USD dividend nets as before, rate or
        # none (a row without a currency counts as the dividend's).
        for cur in ("USD", ""):
            M, d, t = self._pair(currency=cur, net_amount=1.5,
                                 gross_amount=1.5)
            res = M.reconcile_dividend_tax([d, t])
            self.assertEqual(d["net_amount"], 8.5)
            self.assertEqual(res, {"converted": 0, "unconverted": []})

    @rule("CA-INC-05")
    def test_the_books_through_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "book.tt").write_text(_MIXED)
            _ok(_cli(root, "run", "--no-input"))
            work = root / "work"
            raw = _div_rows(work / "margin_raw.json")
            raw_base = _div_rows(work / "margin_raw_base.json")
            base = _div_rows(work / "margin_base.json")
            divs = _ok(_cli(root, "divs", "2025", "margin")).stdout
            # Idempotent: the fix-up again finds nothing to change and
            # does not rewrite the merge.
            from taxjson.bin import taxjson_run as R
            mtime = (work / "margin_raw.json").stat().st_mtime_ns
            self.assertEqual(R._raw_net_withholding(
                work / "margin_raw.json", work / "to_base.csv", "CAD"), [])
            self.assertEqual((work / "margin_raw.json").stat().st_mtime_ns,
                             mtime)
            # No rate file: the CAD withholding is left out, said once.
            notes = R._raw_net_withholding(
                work / "margin_raw.json", work / "missing.csv", "CAD")
            self.assertEqual(len(notes), 1)
            self.assertIn("SYN.US DIVIDEND 2025-03-03: no CAD/USD rate on "
                          "file", notes[0])
        self.assertEqual(raw["DIVIDEND"]["currency"], "USD")
        self.assertAlmostEqual(raw["DIVIDEND"]["net_amount"], 8.0, places=6)
        self.assertEqual(raw["DIVIDEND"]["gross_amount"], 10.0)
        self.assertEqual(raw["TAX"]["net_amount"], 2.7)
        # The native book converted agrees with the base book.
        self.assertAlmostEqual(raw_base["DIVIDEND"]["net_amount"], 10.8,
                               places=6)
        self.assertAlmostEqual(base["DIVIDEND"]["net_amount"], 10.8,
                               places=6)
        self.assertEqual(base["DIVIDEND"]["gross_amount"], 13.5)
        line = next(ln for ln in divs.splitlines()
                    if ln.startswith("DIVIDEND 2025-03-03"))
        self.assertTrue(line.rstrip().endswith(" 10.00 8.00"), line)


# --------------------------------------------------------------- TJ-0612
# A loss of 3,000 in 2024 and a gain of 5,000 in 2025 in the books, no  # pii-ok
# filed/2024.json: `tjs carryover` carries 3,000 in, while the estimate
# nets the gain against 0 (nothing entered, no lock) — and now says so.
_LOSS_THEN_GAIN = (
    "BUYSELL 2024-02-01 10:00:00 XYZ.TO 100 CAD 100 10000 0\n"
    "BUYSELL 2024-06-03 10:00:00 XYZ.TO -100 CAD 70 7000 0\n"
    "BUYSELL 2025-02-03 10:00:00 XYZ.TO 100 CAD 100 10000 0\n"
    "BUYSELL 2025-06-02 10:00:00 XYZ.TO -100 CAD 150 15000 0\n")


class TestCarriedInZeroSaysWhy(unittest.TestCase):

    @rule("CA-EST-LOSSES")
    def test_no_lock_and_nothing_entered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _LOSS_THEN_GAIN)
            text = _ok(_cli(root, "estimate")).stdout
            carry = _ok(_cli(root, "carryover")).stdout
            est = json.loads(_ok(_cli(root, "estimate",
                                      "--json")).stdout)["estimate"]
            # Entered (even 0): the figure is the input, no reason line.
            entered = _ok(_cli(root, "estimate", "--other-losses",
                               "0")).stdout
            given = _ok(_cli(root, "estimate", "--other-losses",
                             "3000")).stdout
        self.assertIn("3,000.00", carry)
        self.assertEqual(est["losses_opening"], 0.0)
        line = next(ln for ln in text.splitlines()
                    if "Net capital losses carried in:" in ln)
        self.assertEqual(" ".join(line.split()),
                         "Net capital losses carried in: 0.00 — no "
                         "close-year lock before 2025 and none entered: "
                         "see `tjs carryover`")
        self.assertNotIn("Net capital losses carried in", entered)
        line = next(ln for ln in given.splitlines()
                    if "Net capital losses carried in:" in ln)
        self.assertEqual(" ".join(line.split()),
                         "Net capital losses carried in: 3,000.00 — from "
                         "--other-losses")


if __name__ == "__main__":
    unittest.main()
