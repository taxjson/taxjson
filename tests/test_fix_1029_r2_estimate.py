"""The 2026-10-29 audits' estimate and amt items (fix round 2, 2026-10-29).

TJ-0489  the estimate is incremental, so the AMT top-up it counts
         against the investment income is the whole return's less the
         one the other income alone owes: with no investment income the
         tax on it is 0 even when the other income owes minimum tax.
TJ-0436  the estimate's AMT CHECK names its figure as the tax on the
         investment income with AMT (and carryover), not "TOTAL WITH
         AMT", which read as the year's total tax.
TJ-0422  the foreign tax credit says which part the TAX rows hold and
         which is the 15% assumption (for an account whose books carry
         no TAX row), and no withholding is assumed on a spin-off's
         deemed dividend, which is paid in shares.

The expected figures are worked by hand from the 2025 tables (federal
14.5% to 57,375, 20.5% to 114,750, BPA 16,129; ON 5.05% to 52,886,  # pii-ok
9.15% to 105,775, BPA 12,747, surtax 20% over 5,710 and 36% over  # pii-ok
7,307, AMT factor 24.63%; AMT 20.5% over 177,882). Synthetic books
only: a made-up ticker (XYZ.TO), the account `margin`.
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


# --------------------------------------------------------------- TJ-0489
# Other income 400,000 with 350,000 of carrying charges (the audit's  # pii-ok
# case): regular taxable income 50,000; adjusted taxable income  # pii-ok
# 400,000 - 50% x 350,000 = 225,000.  # pii-ok
_HEAVY = dict(other_income=400000.0, carrying_charges=350000.0)
# The other income alone: federal 50,000 x 14.5% = 7,250 - BPA  # pii-ok
# 16,129 x 14.5% (2,338.705) = 4,911.295. Minimum: (225,000 -  # pii-ok
# 177,882) x 20.5% = 9,659.19 - 2,338.705 x 50% = 8,489.8375; excess  # pii-ok
# 3,578.5425. ON basic 50,000 x 5.05% - 12,747 x 5.05% = 1,881.2765;  # pii-ok
# + 3,578.5425 x 24.63% = 881.3950 stays under the 5,710 surtax  # pii-ok
# threshold. Top-up 3,578.5425 + 881.3950 = 4,459.9375.  # pii-ok
_BASE_TOPUP = 4459.94


class TestIncrementalMinimumTax(unittest.TestCase):

    @rule("CA-EST-AMT")
    def test_no_investment_income_owes_no_investment_tax(self):
        r = _ca(**_HEAVY)
        a = r["amt"]
        self.assertEqual(r["investment_income"], 0.0)
        self.assertEqual(r["estimated_tax"], 0.0)
        # The return's own top-up is still shown in full ...
        self.assertTrue(a["binding"])
        self.assertEqual(a["minimum_fed"], 8489.84)
        self.assertEqual(a["regular_fed"], 4911.29)
        self.assertEqual(a["excess_fed"], 3578.54)
        self.assertEqual(a["topup"], _BASE_TOPUP)
        # ... but the other income owes all of it: none is the
        # investment income's (it was 4,459.94).  # pii-ok
        self.assertEqual(a["base_topup"], _BASE_TOPUP)
        self.assertEqual(a["topup_attributed"], 0.0)
        self.assertEqual(r["estimated_tax_with_amt"], 0.0)
        # The carryover the year creates is the return's, unchanged.
        self.assertEqual(a["carryover"]["created"], 3578.54)

    @rule("CA-EST-AMT")
    def test_a_gain_is_charged_only_the_top_up_it_adds(self):
        # A 100,000 gain: taxable 100,000. Federal 57,375 x 14.5% +  # pii-ok
        # 42,625 x 20.5% = 17,057.50 - 2,338.705 = 14,718.795. ON basic  # pii-ok
        # 52,886 x 5.05% + 47,114 x 9.15% - 643.7235 = 6,337.9505,  # pii-ok
        # surtax (6,337.9505 - 5,710) x 20% = 125.5901, health premium  # pii-ok
        # 750: 7,213.5406. Alone: 4,911.295 + 1,881.2765 + 600 =  # pii-ok
        # 7,392.5715. Regular difference 14,539.7641.  # pii-ok
        r = _ca(realized=100000.0, **_HEAVY)
        a = r["amt"]
        self.assertEqual(r["tax_with"]["total"], 21932.34)
        self.assertEqual(r["tax_base"]["total"], 7392.57)
        self.assertEqual(r["estimated_tax"], 14539.76)
        # Minimum: (100,000 + 225,000 - 177,882) x 20.5% = 30,159.19 -  # pii-ok
        # 1,169.3525 = 28,989.8375; excess 14,271.0425. ON: x 24.63% =  # pii-ok
        # 3,514.9578; the surtax on 6,337.9505 + 3,514.9578 = 9,852.9083  # pii-ok
        # is 828.5817 + 916.5270 = 1,745.1087, 1,619.5186 more than  # pii-ok
        # 125.5901. Top-up 14,271.0425 + 3,514.9578 + 1,619.5186 =  # pii-ok
        # 19,405.5189.  # pii-ok
        self.assertEqual(a["excess_fed"], 14271.04)
        self.assertEqual(a["base_topup"], _BASE_TOPUP)
        self.assertEqual(a["topup_attributed"],
                         round(a["topup"] - _BASE_TOPUP, 2))
        # 14,539.7641 + 19,405.52 - 4,459.94 = 29,485.3441 (it was  # pii-ok
        # 33,945.28: the other income's 4,459.94 counted as well).  # pii-ok
        self.assertEqual(a["topup"], 19405.52)
        self.assertEqual(r["estimated_tax_with_amt"], 29485.34)

    @rule("CA-EST-AMT")
    def test_income_that_lifts_regular_tax_over_the_minimum(self):
        # 300,000 of payments in lieu (ordinary): taxable 350,000,  # pii-ok
        # adjusted 525,000. Federal 8,319.375 + 11,761.875 + 16,414.32 +  # pii-ok
        # 21,904.28 + 96,586 x 33% (31,873.38) = 90,273.23 - BPA  # pii-ok
        # 14,538 x 14.5% (2,108.01) = 88,165.22 > minimum (525,000 -  # pii-ok
        # 177,882) x 20.5% - 1,054.005 = 70,105.185: no top-up on the  # pii-ok
        # return, so the investment income REMOVES the other income's
        # 4,459.94 from the tax it is charged.  # pii-ok
        r = _ca(pil=300000.0, **_HEAVY)
        a = r["amt"]
        self.assertFalse(a["binding"])
        self.assertEqual(a["regular_fed"], 88165.22)
        # 70,105.185 to the cent (either way of the half).  # pii-ok
        self.assertAlmostEqual(a["minimum_fed"], 70105.185, delta=0.0051)
        self.assertEqual(a["topup"], 0.0)
        self.assertEqual(a["base_topup"], _BASE_TOPUP)
        self.assertEqual(a["topup_attributed"], -_BASE_TOPUP)
        self.assertEqual(r["estimated_tax_with_amt"],
                         round(r["estimated_tax"] - _BASE_TOPUP, 2))

    @rule("CA-EST-AMT")
    def test_no_minimum_tax_on_the_other_income_changes_nothing(self):
        # Other income 60,000, a 500,000 gain (the TJ-0436 case): the  # pii-ok
        # other income alone owes no minimum tax, so the figure is the
        # regular difference plus the whole top-up, as before.
        r = _ca(realized=500000.0, other_income=60000.0)
        a = r["amt"]
        self.assertTrue(a["binding"])
        self.assertEqual(a["base_topup"], 0.0)
        self.assertEqual(a["topup_attributed"], a["topup"])
        self.assertEqual(r["estimated_tax_with_amt"],
                         round(r["estimated_tax"] + a["topup"], 2))

    @rule("CA-AMT-01")
    def test_amt_says_the_other_income_owes_the_top_up(self):
        r = _ca(**_HEAVY)
        for details in (True, False):
            lines = AR.render(AR.build(r, 2025, "CAD"), details=details)
            row = next(ln for ln in lines
                       if "with AMT and carryover" in ln)
            self.assertIn(" 0.00  [less the other income's own top-up "
                          "4,459.94]", row)
        # Nothing to take off: no note.
        lines = AR.render(AR.build(_ca(realized=500000.0,
                                       other_income=60000.0), 2025,
                                   "CAD"))
        row = next(ln for ln in lines if "with AMT and carryover" in ln)
        self.assertNotIn("[", row)

    @rule("CA-EST-AMT", "CA-AMT-01")
    def test_estimate_and_amt_end_to_end(self):
        # A purchase only: no gain, no income in the year.
        book = "BUYSELL 2025-01-02 10:00:00 XYZ.TO 100 CAD 10 1000 0\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, book, "[estimate]\nother_income = 400000\n"
                                       "carrying_charges = 350000\n")
            est = json.loads(_ok(_cli(root, "estimate",
                                      "--json")).stdout)["estimate"]
            text = _ok(_cli(root, "estimate")).stdout
            amt = json.loads(_ok(_cli(root, "amt", "--json")).stdout)
        self.assertEqual(est["estimated_tax"], 0.0)
        self.assertEqual(est["estimated_tax_with_amt"], 0.0)
        self.assertEqual(amt["estimated_tax_with_amt"], 0.0)
        self.assertEqual(amt["amt"]["topup"], _BASE_TOPUP)
        self.assertEqual(amt["amt"]["base_topup"], _BASE_TOPUP)
        block = text[text.index("AMT CHECK"):].splitlines()
        row = next(ln for ln in block if "AMT TOP-UP" in ln)
        self.assertTrue(row.rstrip().endswith("4,459.94"), row)
        row = next(ln for ln in block
                   if "Top-up on other income alone" in ln)
        self.assertIn("-4,459.94  [owed without the investments]", row)
        # The investment income's tax with AMT: 0.00 (it was 4,459.94).  # pii-ok
        row = next(ln for ln in block if "WITH AMT" in ln)
        self.assertTrue(row.rstrip().endswith(" 0.00 CAD"), row)


# --------------------------------------------------------------- TJ-0436
class TestAmtCheckLabels(unittest.TestCase):

    @rule("CA-EST-AMT")
    def test_the_with_amt_line_names_the_investment_income(self):
        # The audit's case: other income 60,000 and a 500,000 gain.  # pii-ok
        book = ("BUYSELL 2025-01-10 10:00:00 XYZ.TO 1000 CAD 100 100000 0\n"
                "BUYSELL 2025-06-10 10:00:00 XYZ.TO -1000 CAD 600 600000 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, book)
            text = _ok(_cli(root, "estimate", "--other-income",
                            "60000")).stdout
            est = json.loads(_ok(_cli(root, "estimate", "--other-income",
                                      "60000", "--json")).stdout)["estimate"]
        a = est["amt"]
        # Taxable income 310,000: federal 8,319.375 + 11,761.875 +  # pii-ok
        # 16,414.32 + 21,904.28 + 56,586 x 33% (18,673.38) = 77,073.23  # pii-ok
        # - BPA 14,538 x 14.5% (2,108.01) = 74,965.22. Minimum: (560,000  # pii-ok
        # - 177,882) x 20.5% = 78,334.19 - 1,054.005 = 77,280.185; excess  # pii-ok
        # 2,314.965. ON: x 24.63% = 570.176 + the 56% surtax on it  # pii-ok
        # (319.299) = 889.475. Top-up 3,204.44.  # pii-ok
        self.assertEqual(a["regular_fed"], 74965.22)
        self.assertAlmostEqual(a["minimum_fed"], 77280.185, delta=0.0051)
        self.assertAlmostEqual(a["provincial_amt"], 889.475, delta=0.0051)
        self.assertEqual(a["topup"], 3204.44)
        self.assertEqual(est["estimated_tax"], 112462.24)
        block = text[text.index("AMT CHECK"):].splitlines()
        # The investment income's tax plus the top-up, named so: not
        # "TOTAL WITH AMT", which read as the return's total tax
        # (122,258.98 + 3,204.44 = 125,463.42).  # pii-ok
        self.assertNotIn("TOTAL WITH AMT", text)
        self.assertIn("  => ESTIMATED TAX ON INVESTMENT INCOME WITH AMT: "
                      "115,666.68 CAD", block)
        self.assertNotIn("125,463.42", text)
        # The federal and provincial parts in the amount column.
        top = next(ln for ln in block if "=> AMT TOP-UP" in ln)
        fed = next(ln for ln in block if ln.startswith("     federal "))
        on = next(ln for ln in block if ln.startswith("     ON "))
        self.assertTrue(fed.endswith(" 2,314.96") or fed.endswith(
            " 2,314.97"), fed)
        self.assertTrue(on.endswith(" 889.47") or on.endswith(" 889.48"),
                        on)
        self.assertEqual(len(top), len(fed))
        self.assertEqual(len(top), len(on))

    @rule("CA-EST-AMT", "CA-AMT-06")
    def test_the_carryover_line_names_the_investment_income(self):
        # No other income: alone, nothing to recover against. A 100,000  # pii-ok
        # gain: federal tax 50,000 x 14.5% - 2,338.705 = 4,911.295, no  # pii-ok
        # minimum (100,000 is under the exemption), so the 1,000 carried  # pii-ok
        # in from 2024 is recovered, all of it the investment income's.
        book = ("BUYSELL 2025-01-10 10:00:00 XYZ.TO 1000 CAD 100 100000 0\n"
                "BUYSELL 2025-06-10 10:00:00 XYZ.TO -1000 CAD 200 200000 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, book, "[estimate]\nother_income = 0\n"
                                       "amt_carryover = { 2024 = 1000 }\n")
            text = _ok(_cli(root, "estimate")).stdout
            est = json.loads(_ok(_cli(root, "estimate",
                                      "--json")).stdout)["estimate"]
        c = est["amt"]["carryover"]
        self.assertEqual(c["recovered_federal"], 1000.0)
        self.assertEqual(c["base_recovered"], 0.0)
        self.assertEqual(c["attributed"], c["recovered"])
        self.assertEqual(est["estimated_tax_with_amt"],
                         round(est["estimated_tax"] - c["recovered"], 2))
        self.assertNotIn("AFTER CARRYOVER", text)
        want = (f"  => ESTIMATED TAX ON INVESTMENT INCOME WITH AMT AND "
                f"CARRYOVER: {est['estimated_tax_with_amt']:,.2f} CAD")
        self.assertIn(want, text.splitlines())


# --------------------------------------------------------------- TJ-0422
# Foreign dividends of 1,000: 200 in `ib`, whose books withheld 30 (TAX  # pii-ok
# rows), and 800 in `qt`, whose books carry no TAX row: 300 paid in cash
# and 500 a spin-off's deemed dividend (paid in shares). The assumption:
# 15% x 300 = 45, none on the 500; the foreign tax 30 + 45 = 75 (it was
# 30 + 15% x 800 = 150, called "actual TAX rows").
def _base_book(tmp: Path, acct: str, rows) -> None:
    (tmp / f"{acct}_base.json").write_text(json.dumps(
        {"transactions": rows}))


def _spin(date: str, amount: float, symbol: str = "ZZS.US") -> dict:
    return {"action": "DIVIDEND", "date": date, "symbol": symbol,
            "net_amount": amount, "gross_amount": amount,
            "corp_election": "taxable_deemed_dividend",
            "corp_event_id": f"{date}-zzp-zzs", "type": "dividend"}


class TestAssumedWithholding(unittest.TestCase):

    @rule("CA-EST-FTC")
    def test_the_estimate_says_which_part_is_assumed(self):
        r = _ca(other_income=200000.0, foreign_div=1000.0,
                actual_withheld=75.0, withheld_assumed=45.0,
                foreign_div_noncash=500.0)
        self.assertEqual(r["foreign_tax_creditable"], 75.0)
        self.assertEqual(r["ftc_basis"], "tax_rows_and_assumed")
        self.assertEqual(r["foreign_tax_from_rows"], 30.0)
        self.assertEqual(r["foreign_tax_by_assumption"], 45.0)
        self.assertEqual(r["foreign_div_noncash"], 500.0)
        self.assertFalse(r["ftc_source"].startswith("actual"))
        self.assertEqual(
            r["ftc_source"],
            "TAX rows 30.00 + 45.00 assumed (15% of the foreign dividends "
            "of accounts with no TAX row; none on 500.00 of spin-off "
            "deemed dividends, paid in shares), capped at 15% of foreign "
            "divs")
        # All from the TAX rows: as before.
        r = _ca(other_income=200000.0, foreign_div=1000.0,
                actual_withheld=30.0)
        self.assertEqual(r["ftc_basis"], "tax_rows")
        self.assertEqual(r["foreign_tax_from_rows"], 30.0)
        self.assertEqual(r["foreign_tax_by_assumption"], 0.0)
        self.assertTrue(r["ftc_source"].startswith("actual TAX rows"))

    @rule("CA-EST-FTC")
    def test_no_tax_row_anywhere_assumes_none_on_the_spin_off(self):
        # `qt` alone: 15% x (800 - 500) = 45, not 120.
        r = _ca(other_income=200000.0, foreign_div=800.0,
                foreign_div_noncash=500.0)
        self.assertEqual(r["foreign_tax_creditable"], 45.0)
        self.assertEqual(r["ftc_basis"], "assumed")
        self.assertEqual(r["foreign_tax_by_assumption"], 45.0)
        self.assertEqual(r["foreign_tax_from_rows"], 0.0)
        self.assertEqual(r["ftc_source"],
                         "assumed 15% of foreign dividends; none on 500.00 "
                         "of spin-off deemed dividends, paid in shares")
        # The credit is the regular federal one in full (the T2209 limit
        # at 200,000 of other income is far above it): 45 off federal  # pii-ok
        # tax, so the estimate is 75 higher than with the old 120.
        old = _ca(other_income=200000.0, foreign_div=800.0)
        self.assertEqual(old["foreign_tax_creditable"], 120.0)
        self.assertEqual(r["ftc_federal"], 45.0)
        self.assertAlmostEqual(r["estimated_tax"] - old["estimated_tax"],
                               75.0, delta=0.011)

    @rule("CA-EST-FTC")
    def test_the_books_spin_off_takes_no_assumption(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            _base_book(w, "ib", [
                {"action": "DIVIDEND", "date": "2025-03-03",
                 "symbol": "ZZQ.US", "net_amount": 200.0},
                {"action": "TAX", "date": "2025-06-02", "symbol": "ZZQ.US",
                 "net_amount": 30.0}])
            _base_book(w, "qt", [
                {"action": "DIVIDEND", "date": "2025-03-03",
                 "symbol": "ZZR.US", "net_amount": 300.0},
                _spin("2025-06-03", 500.0),
                # A Canadian issuer's spin-off is not a foreign dividend,
                # and another year's is not this year's.
                _spin("2025-07-02", 400.0, "ZZC.TO"),
                _spin("2024-06-03", 999.0)])
            fba = {"ib": 200.0, "qt": 800.0}
            got = R._withholding_of_year(w, {"ib", "qt"}, 2025, fba)
            self.assertEqual(got, {"actual_withheld": 75.0,
                                   "withheld_assumed": 45.0,
                                   "foreign_div_noncash": 500.0})
            # The old reader's total is the new total.
            self.assertEqual(R._actual_withholding(w, {"ib", "qt"}, 2025,
                                                   fba), 75.0)
            # plot-tax's months: by May, the cash dividend only (300 of  # pii-ok
            # qt's), no TAX row yet; by June, the spin-off as well.
            nc = {}
            rows = R._withholding_rows(w, {"ib", "qt"}, 2025, fba, nc)
            self.assertEqual(R._withholding_split(
                rows, {"ib": 200.0, "qt": 300.0}, "2025-05-31", nc),
                (45.0, 45.0, 0.0))
            self.assertEqual(R._withholding_split(
                rows, fba, "2025-06-30", nc), (75.0, 45.0, 500.0))
            # No TAX row anywhere: no recorded figure, the assumption
            # and the spin-off still apart.
            got = R._withholding_of_year(w, {"qt"}, 2025, {"qt": 800.0})
            self.assertEqual(got, {"actual_withheld": None,
                                   "withheld_assumed": 45.0,
                                   "foreign_div_noncash": 500.0})

    @rule("CA-EST-FTC")
    def test_the_row_names_the_assumed_part(self):
        # Two accounts through `run`: `ib` with its TAX row, `qt` with
        # none (no spin-off: the .tt has no corporate action).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            for a in ("ib", "qt"):
                (root / "inputs" / a).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = []\nprovince = "ON"\n'
                '[accounts.ib]\ntype = "taxable"\n'
                '[accounts.qt]\ntype = "taxable"\n')
            (root / "inputs" / "ib" / "book.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0\n"
                "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 20 200\n"
                "TAX 2025-06-02 10:00:00 ZZQ.US 10 CAD 3 30\n")
            (root / "inputs" / "qt" / "book.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZR.US 10 CAD 10 100 0\n"
                "DIVIDEND 2025-06-02 10:00:00 ZZR.US 10 CAD 30 300\n")
            _ok(_cli(root, "run", "--no-input"))
            args = ("estimate", "--other-income", "200000")
            est = json.loads(_ok(_cli(root, *args,
                                      "--json")).stdout)["estimate"]
            text = _ok(_cli(root, *args)).stdout
            details = _ok(_cli(root, *args, "--details")).stdout
            trace = _ok(_cli(root, *args, "--verbose")).stdout
        # 30 from the TAX rows + 15% x 300 = 45 assumed.
        self.assertEqual(est["foreign_tax_creditable"], 75.0)
        self.assertEqual(est["foreign_tax_from_rows"], 30.0)
        self.assertEqual(est["foreign_tax_by_assumption"], 45.0)
        self.assertIn("[FTC 75.00: TAX rows 30.00 + assumed 15% 45.00]",
                      text)
        self.assertNotIn("from the TAX rows", text)
        flat = " ".join(details.split())
        # Capped at 15% of each security's own (backlog TJ-0638).
        self.assertIn("[FTC 75.00 — TAX rows 30.00 + 45.00 assumed (15% of "
                      "the foreign dividends of accounts with no TAX row), "
                      "capped at 15% of each security's foreign divs]", flat)
        row = next(ln for ln in trace.splitlines()
                   if "FTC rows+assumed, max 15% x 500.00" in ln)
        self.assertTrue(row.rstrip().endswith("-75.00"), row)

    @rule("CA-EST-FTC")
    def test_the_demo(self):
        # The audit's case, `tjs init --demo` (CAD at the audit's rates;
        # the suite's rate cache moves the cents): foreign dividends
        # 808.65 — ib's MSFT 102.22 (two TAX rows, 15.35), questrade's
        # AAPL 24.69 (no TAX row: 15% assumed, 3.70) and margin's SPNCO
        # 681.75, the PARNT spin-off's deemed dividend (paid in shares:
        # none assumed). The credit was 121.30, "from the TAX rows".
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp) / "demo"
            _ok(_cli(Path(tmp), "init", "--demo", str(top)))
            year = top / "2024"
            _ok(_cli(year, "run", "--no-input"))
            args = ("estimate", "--other-income", "80000")
            est = json.loads(_ok(_cli(year, *args,
                                      "--json")).stdout)["estimate"]
            text = _ok(_cli(year, *args)).stdout
            book = json.loads((year / "work" / "margin_base.json")
                              .read_text())["transactions"]
            ib = json.loads((year / "work" / "ib_base.json")
                            .read_text())["transactions"]
        spin = [t for t in book
                if t.get("corp_election") == "taxable_deemed_dividend"
                and t.get("action") == "DIVIDEND"]
        self.assertEqual(len(spin), 1)
        noncash = round(spin[0]["net_amount"], 2)
        self.assertGreater(noncash, 600)
        self.assertEqual(est["foreign_div_noncash"], noncash)
        withheld = sum(t["net_amount"] for t in ib
                       if t.get("action") == "TAX"
                       and str(t.get("date")).startswith("2024"))
        self.assertGreater(withheld, 10)
        self.assertEqual(est["ftc_basis"], "tax_rows_and_assumed")
        # The TAX rows count up to 15% of MSFT's own dividends (backlog
        # TJ-0638): the broker rounds each 15% up to the cent, so the
        # two rows are a cent above it, and that cent is not counted.
        msft = sum(t["gross_amount"] for t in ib
                   if t.get("action") == "DIVIDEND"
                   and t.get("symbol") == "MSFT.US"
                   and str(t.get("date")).startswith("2024"))
        self.assertAlmostEqual(est["foreign_tax_from_rows"],
                               min(withheld, 0.15 * msft), delta=0.011)
        self.assertAlmostEqual(est["foreign_withholding_over_cap"],
                               max(0.0, withheld - 0.15 * msft),
                               delta=0.006)
        # The parts foot as printed.
        rows, asm = (est["foreign_tax_from_rows"],
                     est["foreign_tax_by_assumption"])
        self.assertGreater(asm, 0)
        self.assertEqual(est["foreign_tax_creditable"], round(rows + asm, 2))
        # Nothing assumed on the spin-off: the whole credit is less
        # than 15% of it alone.
        self.assertLess(est["foreign_tax_creditable"], 0.15 * noncash)
        self.assertIn(f"[FTC {est['foreign_tax_creditable']:,.2f}: TAX rows "
                      f"{rows:,.2f} + assumed 15% {asm:,.2f}]", text)


if __name__ == "__main__":
    unittest.main()
