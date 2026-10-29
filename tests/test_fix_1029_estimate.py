"""The 2026-10-28 worktree audit's estimate items (fix round 2026-10-29).

TJ-0367  the federal foreign tax credit stops at the form T2209 limit
         (basic federal tax x the foreign share of net income); the
         rest goes to the provincial credit (form T2036), limited by
         the province's tax otherwise payable x the same share.
TJ-0368  the AMT's special foreign tax credit is s.127.54's: the
         greater of the regular credit and the lesser of 2/3 of the
         foreign tax and 20.5% of the foreign income — not the whole
         withholding; the minimum tax and the carryover close-year
         records follow.
TJ-0345  `estimate --json` names the foreign tax neutrally
         (`foreign_tax_creditable`, with `ftc_federal` and
         `ftc_provincial`); `ftc_assumed` stays as its old name.
TJ-0407  `instalments` no longer advises a --other-income flag it does
         not have.

The expected figures are worked by hand from the 2025 tables (federal
14.5% to 57,375, BPA 16,129; ON 5.05% to 52,886, BPA 12,747, DTC 10%;  # pii-ok
BC 5.06%, BPA 12,932, DTC 12%). Synthetic books only: made-up tickers
(ZZQ.US, XYZ.TO), the account `margin`.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib import amt_report as AR
from taxjson.lib import carryforward as CF
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


# A foreign dividend of 1000 with 150 withheld (15%).
_FOREIGN = ("BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0\n"
            "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 100 1000\n"
            "TAX 2025-06-02 10:00:00 ZZQ.US 10 CAD 15 150\n")


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


# --------------------------------------------------------------- TJ-0367
class TestFederalForeignTaxCreditLimit(unittest.TestCase):
    """Other income 20,000, a foreign dividend of 1,000 with 150 withheld,  # pii-ok
    Ontario 2025 (the audit's case)."""

    @rule("CA-EST-FTC")
    def test_the_t2209_limit_and_the_t2036_rest(self):
        r = _ca(other_income=20000.0, foreign_div=1000.0,
                actual_withheld=150.0)
        t = r["trace_with"]
        # Taxable = net income = 21,000. Federal: 21,000 x 14.5% =  # pii-ok
        # 3,045.00 - BPA 16,129 x 14.5% (2,338.705) = 706.295 basic.  # pii-ok
        self.assertAlmostEqual(t["fed_gross"] - t["fed_bpa"], 706.295,
                               places=6)
        # T2209: 706.295 x 1,000 / 21,000 = 33.6331 < 150.  # pii-ok
        self.assertAlmostEqual(t["ftc_share"], 1000 / 21000, places=12)
        self.assertAlmostEqual(t["fed_ftc_limit"], 706.295 / 21, places=6)
        self.assertAlmostEqual(t["fed_ftc"], 706.295 / 21, places=6)
        self.assertEqual(r["tax_with"]["federal"], 672.66)
        # T2036: 150 - 33.6331 = 116.3669 left; ON basic (21,000 -  # pii-ok
        # 12,747) x 5.05% = 416.7765, no surtax or DTC, x 1/21 =  # pii-ok
        # 19.8465. ON = 416.7765 - 19.8465 + OHP 60 = 456.93.
        self.assertAlmostEqual(t["fed_ftc_unused"], 150 - 706.295 / 21,
                               places=6)
        self.assertAlmostEqual(t["prov_ftc"], 416.7765 / 21, places=6)
        self.assertEqual(r["tax_with"]["provincial"], 456.93)
        # Other income alone: 20,000 x 14.5% - 2,338.705 = 561.295;  # pii-ok
        # ON (20,000 - 12,747) x 5.05% = 366.2765, no OHP at 20,000:  # pii-ok
        # 927.5715. 672.6619 + 456.9300 - 927.5715 = 202.02.
        self.assertEqual(r["tax_base"]["total"], 927.57)
        self.assertEqual(r["estimated_tax"], 202.02)
        # The JSON names what was counted and what each credit took.
        self.assertEqual(r["foreign_tax_creditable"], 150.0)
        self.assertEqual(r["ftc_federal"], 33.63)
        self.assertEqual(r["ftc_provincial"], 19.85)
        note = " ".join(r["notes"])
        self.assertIn("federal tax takes 33.63 (form T2209", note)
        self.assertIn("ON tax 19.85 (form T2036", note)
        self.assertIn("96.52 is not credited", note)

    @rule("CA-EST-FTC")
    def test_a_high_earner_keeps_the_whole_credit(self):
        # Other income 200,000: basic federal tax on 201,000 is  # pii-ok
        # 43,199.79 - BPA 15,642.04 x 14.5% = 40,931.70; x 1/201 =  # pii-ok
        # 203.64 > 150: the whole 150 is credited federally.
        r = _ca(other_income=200000.0, foreign_div=1000.0,
                actual_withheld=150.0)
        t = r["trace_with"]
        self.assertAlmostEqual(t["fed_ftc_limit"], 203.64, delta=0.01)
        self.assertEqual(t["fed_ftc"], 150.0)
        self.assertEqual(t["fed_ftc_unused"], 0.0)
        self.assertEqual(t["prov_ftc"], 0.0)
        self.assertFalse(any("form T2209" in n for n in r["notes"]))

    @rule("CA-EST-FTC")
    def test_the_share_is_of_net_income_less_line_25300(self):
        # 10,000 of gains offset by 10,000 of losses of other years: net  # pii-ok
        # income 26,000 (the 5,000 deducted at line 25300 below it), the  # pii-ok
        # T2209 / T2036 net income 21,000 — the same credits as above.  # pii-ok
        r = _ca(realized=10000.0, other_losses=10000.0,
                other_income=20000.0, foreign_div=1000.0,
                actual_withheld=150.0)
        t = r["trace_with"]
        self.assertEqual(t["net_income"], 26000.0)
        self.assertAlmostEqual(t["ftc_share"], 1000 / 21000, places=12)
        self.assertAlmostEqual(t["fed_ftc"], 706.295 / 21, places=6)
        self.assertAlmostEqual(t["prov_ftc"], 416.7765 / 21, places=6)

    @rule("CA-EST-FTC")
    def test_ontario_and_bc_tax_otherwise_payable(self):
        # Other income 20,000, eligible dividends 1,000 (grossed 1,380),  # pii-ok
        # foreign 1,000 with 150 withheld: taxable 22,380, share  # pii-ok
        # 1,000 / 22,380. Federal: 22,380 x 14.5% = 3,245.10 - 2,338.705  # pii-ok
        # = 906.395 before the DTC (207.27324): line 42900 699.12176;
        # T2209 basic = 699.12176 + 207.27324 = 906.395, x share =
        # 40.50022; federal 658.62154; 109.49978 left for T2036.
        kw = dict(other_income=20000.0, eligible_div=1000.0,
                  foreign_div=1000.0, actual_withheld=150.0)
        on = _ca(**kw)
        t = on["trace_with"]
        self.assertAlmostEqual(t["fed_ftc"], 906.395 / 22.38, places=6)
        self.assertAlmostEqual(on["tax_with"]["federal"],
                               699.12176 - 906.395 / 22.38, delta=0.005)
        self.assertAlmostEqual(t["fed_ftc_unused"],
                               150 - 906.395 / 22.38, places=6)
        # ON (T2036 note 4: line 81 with the DTC at 0): basic (22,380 -  # pii-ok
        # 12,747) x 5.05% = 486.4665, BEFORE the 138.00 DTC; x share =  # pii-ok
        # 21.73666.
        self.assertAlmostEqual(t["prov_ftc_limit"], 486.4665 / 22.38,
                               places=6)
        self.assertAlmostEqual(t["prov_ftc"], 486.4665 / 22.38, places=6)
        # BC (BC428 line 68, after its DTC): (22,380 x 5.06% = 1,132.428  # pii-ok
        # - 12,932 x 5.06% = 654.3592) - 1,380 x 12% = 312.4688; x share  # pii-ok
        # = 13.96197.
        bc = _ca(province="BC", **kw)
        tb = bc["trace_with"]
        self.assertAlmostEqual(tb["prov_ftc_limit"], 312.4688 / 22.38,
                               places=6)
        self.assertAlmostEqual(tb["prov_ftc"], 312.4688 / 22.38, places=6)


# --------------------------------------------------------------- TJ-0368
class TestSpecialForeignTaxCredit(unittest.TestCase):
    """Capital gains 400,000 offset by 400,000 of losses of other years  # pii-ok
    (full dollars), a foreign dividend of 1,000 with 150 withheld, no  # pii-ok
    other income, Ontario 2025 (the audit's case)."""

    KW = dict(realized=400000.0, other_losses=400000.0,
              foreign_div=1000.0, actual_withheld=150.0)

    @rule("CA-EST-AMT")
    def test_the_s127_54_credit(self):
        a = _ca(**self.KW)["amt"]
        # Adjusted taxable income 400,000 - 200,000 + 1,000 = 201,000;  # pii-ok
        # - 177,882 = 23,118 x 20.5% = 4,739.19. Net income 201,000:  # pii-ok
        # BPA 16,129 - 1,591 x 23,118 / 75,532 = 15,642.04, x 14.5% x 50%  # pii-ok
        # = 1,134.05.  # pii-ok
        self.assertEqual(a["gross_minimum"], 4739.19)
        self.assertEqual(a["bpa_credit"], 1134.05)
        # Regular credit 0 (no basic federal tax on 1,000 of taxable  # pii-ok
        # income); 2/3 x 150 = 100 < 20.5% x 1,000 = 205: 100.  # pii-ok
        self.assertEqual(a["ftc_regular"], 0.0)
        self.assertEqual(a["ftc_foreign_taxes"], 100.0)
        self.assertEqual(a["ftc_income_limit"], 205.0)
        self.assertEqual(a["ftc"], 100.0)
        # 4,739.19 - 1,134.05 - 100 = 3,505.14 (it was 3,455.14).  # pii-ok
        self.assertEqual(a["minimum_fed"], 3505.14)
        self.assertEqual(a["regular_fed"], 0.0)
        self.assertEqual(a["excess_fed"], 3505.14)
        # The carryover created is the additional tax of s.120.2(3) (T691
        # Part 7 line 14), not the excess (backlog TJ-0626): 3,605.1418  # pii-ok
        # less basic federal tax 0 and less (100 - 0) x 100 / 150 =
        # 66.6667: 3,538.4751 (it was the excess, 3,505.14).  # pii-ok
        self.assertEqual(a["carryover"]["created"], 3538.48)
        self.assertEqual(a["carryover"]["closing_by_year"],
                         {"2025": 3538.48})

    @rule("CA-EST-AMT")
    def test_the_greater_of_the_regular_credit_and_the_capped_two_thirds(
            self):
        prov = TE.CA_PROVINCES["ON"]
        kw = dict(realized=0.0, eligible_div=0.0, pil=0.0,
                  other_income=300000.0, other_losses=0.0,
                  regular_fed=0.0, prov=prov)
        # (a) the regular credit, 150, beats 2/3 x 150 = 100.
        a = TE._amt_canada(foreign_div=1000.0, ftc=150.0,
                           regular_ftc=150.0, **kw)
        self.assertEqual(a["ftc"], 150.0)
        # (b) capped at 20.5% of the foreign income: 2/3 x 150 = 100 >
        # 20.5% x 400 = 82.
        a = TE._amt_canada(foreign_div=400.0, ftc=150.0, **kw)
        self.assertEqual(a["ftc"], 82.0)
        # No foreign tax, no credit.
        a = TE._amt_canada(foreign_div=400.0, ftc=0.0, **kw)
        self.assertEqual(a["ftc"], 0.0)

    @rule("CA-AMT-01")
    def test_amt_shows_how_the_credit_was_built(self):
        r = _ca(**self.KW)
        lines = AR.render(AR.build(r, 2025, "CAD"), details=True)
        row = next(ln for ln in lines
                   if "Special foreign tax credit (s.127.54)" in ln)
        self.assertIn("-100.00", row)
        text = " ".join(" ".join(lines).split())     # any width
        self.assertIn("the greater of the regular credit 0.00 and the "
                      "lesser of 2/3 of the foreign tax (100.00) and "
                      "20.5% of the foreign income (205.00)", text)
        self.assertNotIn("[in full]", text)

    @rule("CA-CARRY-01", "CA-AMT-02")
    def test_close_year_records_the_corrected_carryover(self):
        r = _ca(**self.KW)
        mt = CF.record_block("canada", r, {})["minimum_tax"]
        # s.120.2(3)'s additional tax (TJ-0626), not the excess 3,505.14.  # pii-ok
        self.assertEqual(mt["created"], 3538.48)
        self.assertEqual(mt["closing_by_year"], {"2025": 3538.48})
        self.assertEqual(mt["closing"], 3538.48)

    @rule("CA-CARRY-01", "CA-AMT-01")
    def test_close_year_end_to_end(self):
        # The same case through the books: a 400,000 gain, 400,000 of  # pii-ok
        # losses of other years entered, and the foreign dividend.
        book = ("BUYSELL 2025-01-02 10:00:00 XYZ.TO 1000 CAD 100 100000 0\n"
                "BUYSELL 2025-03-03 10:00:00 XYZ.TO -1000 CAD 500 500000 0\n"
                + _FOREIGN)
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, book, "[estimate]\nother_income = 0\n"
                                       "other_losses = 400000\n")
            amt = json.loads(_ok(_cli(root, "amt", "--json")).stdout)
            self.assertEqual(amt["amt"]["ftc"], 100.0)
            self.assertEqual(amt["amt"]["minimum_fed"], 3505.14)
            _ok(_cli(root, "close-year", "--yes"))
            lock = json.loads((root / "filed" / "2025.json").read_text())
        mt = lock["carryforwards"]["minimum_tax"]
        # s.120.2(3)'s additional tax (TJ-0626), not the excess.
        self.assertEqual(mt["created"], 3538.48)
        self.assertEqual(mt["closing_by_year"], {"2025": 3538.48})


# --------------------------------------------------------------- TJ-0345
class TestForeignTaxKeys(unittest.TestCase):

    @rule("CA-EST-FTC")
    def test_estimate_json_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _FOREIGN)
            est = json.loads(_ok(_cli(root, "estimate", "--other-income",
                                      "20000", "--json")).stdout)["estimate"]
            text = _ok(_cli(root, "estimate", "--other-income",
                            "20000")).stdout
            details = _ok(_cli(root, "estimate", "--other-income", "20000",
                               "--details")).stdout
            trace = _ok(_cli(root, "estimate", "--other-income", "20000",
                             "--verbose")).stdout
        # The TAX rows' 150 is not assumed: the neutral key says what it
        # is; the old name stays for scripts that read it.
        self.assertEqual(est["foreign_tax_creditable"], 150.0)
        self.assertEqual(est["ftc_assumed"], 150.0)
        self.assertTrue(est["ftc_source"].startswith("actual TAX rows"))
        self.assertEqual(est["ftc_federal"], 33.63)
        self.assertEqual(est["ftc_provincial"], 19.85)
        self.assertEqual(est["tax_with"]["federal"], 672.66)
        self.assertIn("[FTC 53.48 of 150.00, from the TAX rows]", text)
        self.assertIn("credited: federal 33.63 (T2209), ON 19.85 (T2036)",
                      details)
        row = next(ln for ln in trace.splitlines()
                   if "FTC T2209 limit, tax 150.00" in ln)
        self.assertTrue(row.rstrip().endswith("-33.63"), row)
        row = next(ln for ln in trace.splitlines()
                   if "FTC not used federally (T2036)" in ln)
        self.assertTrue(row.rstrip().endswith("-19.85"), row)

    @rule("CA-EST-FTC")
    def test_an_assumed_credit_still_says_so(self):
        r = _ca(other_income=200000.0, foreign_div=1000.0)
        self.assertEqual(r["foreign_tax_creditable"], 150.0)
        self.assertEqual(r["ftc_federal"], 150.0)
        self.assertEqual(r["ftc_provincial"], 0.0)
        self.assertIn("assumed", r["ftc_source"])


# --------------------------------------------------------------- TJ-0407
class TestInstalmentsAdvice(unittest.TestCase):

    def test_the_assumed_zero_advice_names_no_missing_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _FOREIGN,
                            '[instalments]\nbasis = "current_year"\n')
            doc = json.loads(_ok(_cli(root, "instalments",
                                      "--json")).stdout)
            details = _ok(_cli(root, "instalments", "--details")).stdout
            # The flag the advice used to name is refused by the parser.
            r = _cli(root, "instalments", "--other-income", "0")
        self.assertEqual(r.returncode, 2)
        advice = " ".join(doc["assumed_zero"])
        self.assertNotIn("--other-income", advice)
        self.assertIn("set [estimate] other_income in taxjson.toml", advice)
        self.assertNotIn("--other-income", details)
        self.assertIn("[estimate] other_income", details)


if __name__ == "__main__":
    unittest.main()
