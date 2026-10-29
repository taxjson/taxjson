"""Fix round 4 of 2026-10-29, the estimate group.

TJ-0626  the minimum tax carryover a year creates is its additional tax
         of ITA s.120.2(3) (T691 Part 7 line 14, carried to Part 8 line
         14): the minimum amount less basic federal tax and less the
         special foreign tax credit above the regular one times the
         foreign taxes of s.127.54(1) (2/3 of the non-business tax) over
         the foreign tax paid. It was the federal excess (Part 5 line
         11), which only equals it when the special credit is the
         regular one.
TJ-0592  (owner's decision: option b) no figure moves: the estimate names
         the foreign tax neither credit takes and the withholding above
         15% of the foreign dividends, says either may be deductible on
         line 23200 (s.20(12), s.20(11)) and that it does not deduct
         them, and points to [estimate] deductions; in a minimum-tax year
         too.

The expected figures are worked by hand from the 2025 tables (federal
14.5% to 57,375, 20.5% to 114,750, BPA 16,129 phased to 14,538 over  # pii-ok
177,882-253,414; ON 5.05% to 52,886, BPA 12,747; AMT 20.5% over  # pii-ok
177,882). Synthetic books only: made-up tickers (ZZQ.US, XYZ.TO), the
account `margin`.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tax_rules import rule

from taxjson.lib import amt_report as AR
from taxjson.lib import carryforward as CF
from taxjson.lib import checklist as CL
from taxjson.lib import tax_estimate as TE
from taxjson.lib import tax_logic as TL
from taxjson.lib.dispatch import python_module_argv

SRC = Path(__file__).resolve().parent.parent / "src"


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON",
                year=2025)
    args.update(kw)
    return TE.estimate_canada(**args)


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`taxjson -C root args...` from this tree's sources (width 0)."""
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


def _project(tmp: str, book: str, tail: str = "") -> Path:
    root = Path(tmp) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\n'
        'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        'source_currencies = []\nprovince = "ON"\n'
        '[accounts.margin]\ntype = "taxable"\n' + tail)
    (root / "inputs" / "margin" / "book.tt").write_text(book)
    _ok(_cli(root, "run", "--no-input"))
    return root


def _flat(text: str) -> str:
    """Any width: the words with single spaces."""
    return " ".join(text.split())


def _rule_text(rid: str) -> str:
    for _t, rules in TL.rule_sections("canada", {"country": "canada",
                                                 "year": 2025}):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated")


# A foreign dividend of 1,000 with 150 withheld; with a 400,000 gain  # pii-ok
# and 400,000 of other years' net capital losses below (TJ-0626).  # pii-ok
_FOREIGN = ("BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0\n"
            "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 100 1000\n"
            "TAX 2025-06-02 10:00:00 ZZQ.US 10 CAD 15 150\n")
_GAIN = ("BUYSELL 2025-01-02 10:00:00 XYZ.TO 1000 CAD 100 100000 0\n"
         "BUYSELL 2025-03-03 10:00:00 XYZ.TO -1000 CAD 500 500000 0\n")

# --------------------------------------------------------------- TJ-0626
# The backlog's case, ON 2025: a 400,000 gain offset by 400,000 of net  # pii-ok
# capital losses of other years, a foreign dividend of 1,000 with 150  # pii-ok
# withheld, no other income.
#   T691 line 103 (the minimum amount): adjusted taxable income 201,000  # pii-ok
#   - 177,882 = 23,118 x 20.5% = 4,739.19, less the basic minimum tax  # pii-ok
#   credit 15,642.0443 x 14.5% x 50% = 1,134.0482: 3,605.1418.  # pii-ok
#   Part 2 line 7 (basic federal tax): 0 (taxable income 1,000).  # pii-ok
#   Part 4 line 11 (the special credit): 2/3 x 150 = 100 (line 9),
#   less than 20.5% x 1,000; the regular credit (Part 3 line 7) is 0.  # pii-ok
#   Part 5 line 11 (the excess): 3,605.1418 - 100 - 0 = 3,505.1418.  # pii-ok
#   Part 7: line 8 = 0; line 11 = 100 - 0 = 100; line 12 = 100 x 100 /
#   150 = 66.6667; line 13 = 66.6667; line 14 = 3,605.1418 - 66.6667  # pii-ok
#   = 3,538.4751: the carryover created (Part 8 line 14), 33.33 above  # pii-ok
#   the excess.
_CASE = dict(realized=400000.0, other_losses=400000.0, foreign_div=1000.0,
             actual_withheld=150.0)


class TestCarryoverCreatedIsTheAdditionalTax(unittest.TestCase):

    @rule("CA-AMT-02", "CA-AMT-07")
    def test_the_backlog_case(self):
        r = _ca(**_CASE)
        a = r["amt"]
        self.assertTrue(a["binding"])
        self.assertEqual(a["minimum_amount"], 3605.14)
        self.assertEqual(a["basic_fed"], 0.0)
        self.assertEqual(a["ftc"], 100.0)
        # The tax of the year is unchanged: the excess and the top-up.
        self.assertEqual(a["excess_fed"], 3505.14)
        self.assertEqual(a["minimum_fed"], 3505.14)
        self.assertEqual(a["topup"], 4368.46)
        # What carries forward is T691 Part 7 line 14.
        self.assertEqual(a["additional_tax_s120_2"], 3538.48)
        self.assertEqual(a["carryforward"], 3538.48)
        c = a["carryover"]
        self.assertEqual(c["created"], 3538.48)
        self.assertEqual(c["closing_by_year"], {"2025": 3538.48})
        self.assertEqual(c["closing"], 3538.48)
        self.assertEqual(a["additional_tax_sftc_part"], 66.67)
        notes = _flat(" ".join(r["notes"]))
        self.assertIn("Minimum tax carryover created: 3,538.48, the "
                      "additional tax of s.120.2(3) (T691 Part 7 line 14): "
                      "the minimum amount 3,605.14 less basic federal tax "
                      "0.00 and less 66.67, 2/3 of the special foreign tax "
                      "credit above the regular one. The federal excess, "
                      "3,505.14, takes that credit off in full.", notes)

    @rule("CA-AMT-02")
    def test_every_province_carries_the_same_federal_figure(self):
        for prov in ("ON", "AB", "BC"):
            c = _ca(province=prov, **_CASE)["amt"]["carryover"]
            self.assertEqual(c["created"], 3538.48, prov)
        # Federal only (no supported province): the same.
        c = _ca(province=None, **_CASE)["amt"]["carryover"]
        self.assertEqual(c["created"], 3538.48)

    @rule("CA-AMT-02")
    def test_the_excess_when_the_special_credit_is_the_regular_one(self):
        # A 600,000 gain and a foreign dividend of 10,000 with 1,500  # pii-ok
        # withheld: the regular credit takes the whole 1,500 (basic  # pii-ok
        # federal tax x 10,000 / 310,000 is more), so the special credit  # pii-ok
        # is the regular one and the carryover is the excess.
        r = _ca(realized=600000.0, foreign_div=10000.0,
                actual_withheld=1500.0)
        a = r["amt"]
        self.assertTrue(a["binding"])
        self.assertEqual(a["ftc_regular"], 1500.0)
        self.assertEqual(a["ftc"], 1500.0)
        self.assertEqual(a["additional_tax_s120_2"], a["excess_fed"])
        self.assertEqual(a["carryover"]["created"], a["excess_fed"])
        self.assertFalse(any("Minimum tax carryover created" in n
                             for n in r["notes"]))

    @rule("CA-AMT-02")
    def test_no_carryover_when_amt_does_not_bind(self):
        a = _ca(other_income=20000.0, foreign_div=1000.0,
                actual_withheld=150.0)["amt"]
        self.assertFalse(a["binding"])
        self.assertEqual(a["carryforward"], 0.0)
        self.assertEqual(a["carryover"]["created"], 0.0)
        self.assertEqual(a["carryover"]["closing_by_year"], {})

    @rule("CA-AMT-02")
    def test_the_rule_says_so(self):
        text = _rule_text("CA-AMT-02")
        self.assertIn("s.120.2(3)", text)
        self.assertIn("T691 Part 7 line 14", text)
        self.assertNotIn("equal to the excess", text)
        self.assertIn("additional tax", _rule_text("CA-AMT-07"))

    @rule("CA-AMT-01", "CA-AMT-02")
    def test_amt_report(self):
        r = _ca(**_CASE)
        lines = AR.render(AR.build(r, 2025, "CAD"), details=True)
        row = next(ln for ln in lines if "=> AMT BINDS" in ln)
        self.assertIn("3,505.14", row)
        self.assertIn("[s.120.2(3): carries forward 3,538.48]", row)
        # The default view keeps it (it fits the width).
        short = AR.render(AR.build(r, 2025, "CAD"), details=False)
        row = next(ln for ln in short if "=> AMT BINDS" in ln)
        self.assertIn("[s.120.2(3): carries forward 3,538.48]", row)
        row = next(ln for ln in lines
                   if "Created this year (additional tax)" in ln)
        self.assertIn("3,538.48", row)
        row = next(ln for ln in lines if "Carried forward to 2026" in ln)
        self.assertIn("3,538.48", row)
        # Without foreign tax the binding row reads as before.
        r = _ca(realized=600000.0)
        lines = AR.render(AR.build(r, 2025, "CAD"), details=True)
        row = next(ln for ln in lines if "=> AMT BINDS" in ln)
        self.assertIn("[s.120.2(3): carries forward]", row)

    @rule("CA-AMT-02")
    def test_checklist_names_what_carries_forward(self):
        doc = {"amt": _ca(**_CASE)["amt"], "carry_sources": {}}
        ctx = SimpleNamespace(sub=lambda *a, **k: (0, json.dumps(doc), ""))
        res = CL.d_amt(ctx)
        self.assertIn("AMT binds: 3,505.14 federal additional tax; "
                      "3,538.48 carries forward 7 years (s.120.2(3))",
                      res.detail)
        doc = {"amt": _ca(realized=600000.0)["amt"], "carry_sources": {}}
        res = CL.d_amt(ctx)
        ex = doc["amt"]["excess_fed"]
        self.assertIn(f"AMT binds: {ex:,.2f} federal additional tax "
                      f"carries forward 7 years", res.detail)

    @rule("CA-CARRY-01", "CA-AMT-02")
    def test_close_year_records_the_additional_tax(self):
        mt = CF.record_block("canada", _ca(**_CASE), {})["minimum_tax"]
        self.assertEqual(mt["created"], 3538.48)
        self.assertEqual(mt["closing_by_year"], {"2025": 3538.48})
        self.assertEqual(mt["closing"], 3538.48)

    @rule("CA-CARRY-01", "CA-AMT-01", "CA-AMT-02")
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _GAIN + _FOREIGN,
                            "[estimate]\nother_income = 0\n"
                            "other_losses = 400000\n")
            amt = json.loads(_ok(_cli(root, "amt", "--json")).stdout)
            text = _ok(_cli(root, "estimate", "--details")).stdout
            _ok(_cli(root, "close-year", "--yes"))
            lock = json.loads((root / "filed" / "2025.json").read_text())
            recorded = _ok(_cli(root, "amt", "2025")).stdout
        a = amt["amt"]
        self.assertEqual(a["excess_fed"], 3505.14)
        self.assertEqual(a["carryover"]["created"], 3538.48)
        mt = lock["carryforwards"]["minimum_tax"]
        self.assertEqual(mt["created"], 3538.48)
        self.assertEqual(mt["closing_by_year"], {"2025": 3538.48})
        self.assertEqual(mt["closing"], 3538.48)
        block = text[text.index("MINIMUM TAX CARRYOVER"):].splitlines()
        row = next(ln for ln in block if "Carried forward" in ln)
        self.assertIn("3,538.48", row)
        self.assertIn("The carryover it creates (3,538.48: the additional "
                      "tax of s.120.2(3), T691 Part 7 line 14) is "
                      "creditable against REGULAR tax for 7 years",
                      _flat(text))
        row = next(ln for ln in recorded.splitlines()
                   if "Created this year (additional tax)" in ln)
        self.assertIn("3,538.48", row)


# --------------------------------------------------------------- TJ-0592
_DEDUCT = ("may be deductible on line 23200 (form T2209); the estimate "
           "does not deduct")
_WHERE = "a deduction you claim goes in [estimate] deductions."


class TestForeignTaxNotCreditedIsNamed(unittest.TestCase):

    @rule("CA-EST-FTC")
    def test_regular_year_not_credited(self):
        # The backlog's case: other income 20,000; the T2209 and T2036  # pii-ok
        # credits take 33.63 and 19.85 of the 150: 96.52 is left.
        r = _ca(other_income=20000.0, foreign_div=1000.0,
                actual_withheld=150.0)
        # No figure moves.
        self.assertEqual(r["ftc_federal"], 33.63)
        self.assertEqual(r["ftc_provincial"], 19.85)
        self.assertEqual(r["tax_with"]["federal"], 672.66)
        self.assertEqual(r["foreign_tax_not_credited"], 96.52)
        self.assertEqual(r["foreign_withholding_over_cap"], 0.0)
        self.assertEqual(r["amt"]["foreign_tax_not_credited"], 96.52)
        note = next(n for n in r["notes"]
                    if n.startswith("Foreign tax credit:"))
        self.assertIn("; 96.52 is not credited. The 96.52 neither credit "
                      "takes (ITA s.20(12)) " + _DEDUCT + " it: " + _WHERE,
                      note)

    @rule("CA-EST-FTC")
    def test_withholding_above_15_percent(self):
        # 300 withheld on 1,000: the credit counts 150 (15%); the other  # pii-ok
        # 150 is named with the 96.52 the credits leave.
        r = _ca(other_income=20000.0, foreign_div=1000.0,
                actual_withheld=300.0)
        self.assertEqual(r["foreign_tax_creditable"], 150.0)
        self.assertEqual(r["foreign_withholding_over_cap"], 150.0)
        self.assertEqual(r["tax_with"]["federal"], 672.66)
        note = next(n for n in r["notes"]
                    if n.startswith("Foreign tax credit:"))
        self.assertIn("The 96.52 neither credit takes (ITA s.20(12)) and "
                      "the 150.00 withheld above 15% of the foreign "
                      "dividends (ITA s.20(11)) " + _DEDUCT + " them: "
                      + _WHERE, note)

    @rule("CA-EST-FTC")
    def test_withholding_above_15_percent_all_else_credited(self):
        # Other income 200,000: the federal credit takes the whole 150,  # pii-ok
        # so the credit note is not printed; the withholding above 15%
        # is named on its own.
        r = _ca(other_income=200000.0, foreign_div=1000.0,
                actual_withheld=300.0)
        self.assertEqual(r["ftc_federal"], 150.0)
        self.assertEqual(r["foreign_tax_not_credited"], 0.0)
        self.assertFalse(any(n.startswith("Foreign tax credit:")
                             for n in r["notes"]))
        self.assertIn("Foreign tax not credited: the 150.00 withheld above "
                      "15% of the foreign dividends (ITA s.20(11)) "
                      + _DEDUCT + " it: " + _WHERE, r["notes"])

    @rule("CA-EST-FTC")
    def test_nothing_to_name(self):
        # Everything credited, nothing above 15%: no such note.
        r = _ca(other_income=200000.0, foreign_div=1000.0,
                actual_withheld=150.0)
        self.assertEqual(r["foreign_tax_not_credited"], 0.0)
        self.assertEqual(r["foreign_withholding_over_cap"], 0.0)
        self.assertFalse(any("23200" in n for n in r["notes"]))
        # The 15% assumption (no TAX rows) is never above 15%.
        r = _ca(other_income=200000.0, foreign_div=1000.0)
        self.assertEqual(r["foreign_withholding_over_cap"], 0.0)

    @rule("CA-EST-FTC", "CA-EST-AMT")
    def test_minimum_tax_year(self):
        # The TJ-0626 case with 200 withheld: the credit counts 150; the
        # special credit takes 100, ON tax nothing: 50 is not credited,
        # and 50 is above 15%. The regular year's note does not name a
        # deduction (its credits are not the return's).
        r = _ca(**dict(_CASE, actual_withheld=200.0))
        a = r["amt"]
        self.assertTrue(a["binding"])
        self.assertEqual(r["foreign_tax_not_credited"], 150.0)
        self.assertEqual(a["foreign_tax_not_credited"], 50.0)
        self.assertEqual(r["foreign_withholding_over_cap"], 50.0)
        regular = next(n for n in r["notes"]
                       if n.startswith("Foreign tax credit:"))
        self.assertNotIn("23200", regular)
        note = next(n for n in r["notes"]
                    if n.startswith("Foreign tax credit with AMT:"))
        self.assertIn("; 50.00 is not credited. The 50.00 neither credit "
                      "takes (ITA s.20(12)) and the 50.00 withheld above "
                      "15% of the foreign dividends (ITA s.20(11)) "
                      + _DEDUCT + " them: " + _WHERE, note)
        # No figure moves (the TJ-0626 case's own figures).
        self.assertEqual(a["excess_fed"], 3505.14)
        self.assertEqual(a["topup"], 4368.46)

    @rule("CA-EST-FTC")
    def test_assumptions_and_rule(self):
        for text in (TE.CA_ASSUMPTIONS, _rule_text("CA-EST-FTC")):
            flat = _flat(text)
            self.assertIn("line 23200", flat)
            self.assertIn("s.20(12)", flat)
            self.assertIn("s.20(11)", flat)
            self.assertIn("[estimate] deductions", flat)

    @rule("CA-EST-FTC")
    def test_estimate_details_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _FOREIGN)
            text = _ok(_cli(root, "estimate", "--other-income", "20000",
                            "--details")).stdout
            est = json.loads(_ok(_cli(root, "estimate", "--other-income",
                                      "20000", "--json")).stdout)
        flat = _flat(text)
        self.assertIn("96.52 is not credited. The 96.52 neither credit "
                      "takes (ITA s.20(12)) " + _DEDUCT + " it: " + _WHERE,
                      flat)
        self.assertIn("foreign tax that neither credit takes", flat)
        self.assertEqual(est["estimate"]["foreign_tax_not_credited"], 96.52)
        self.assertEqual(est["estimate"]["tax_with"]["federal"], 672.66)


if __name__ == "__main__":
    unittest.main()
