"""Fix round 5 of 2026-10-29, the estimate group.

TJ-0638  the foreign tax the estimate counts is each security's
         withholding up to 15% of that security's own foreign dividends
         (ITA s.126(7) "non-business-income tax" leaves out what
         s.20(11) makes deductible: the tax on income from a property
         above 15% of that property's income; form T2209). It capped
         all the withholding at 15% of all foreign dividends together,
         so excess withholding on one security took the room of
         another's dividends, a spin-off's deemed dividend in another
         account too. A security is summed across the taxable accounts
         that hold it; a TAX row the books cannot pair with a security
         counts up to 15% of its account's cash dividends that no TAX
         row names.

The expected figures are worked by hand from the 2025 tables (federal
14.5% to 57,375, 20.5% to 114,750, BPA 16,129; ON 5.05% to 52,886,  # pii-ok
9.15% to 105,775, BPA 12,747, surtax 20% over 5,710; the Ontario Health  # pii-ok
Premium 750 on both incomes). Synthetic books only: made-up tickers
(ZZQ.US, ZZR.US, ZZS.US, ZZT.US), the accounts `margin`, `ib`, `qt`.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib import tax_estimate as TE
from taxjson.lib import tax_logic as TL
from taxjson.lib.dispatch import python_module_argv

SRC = Path(__file__).resolve().parent.parent / "src"
DOCS = Path(__file__).resolve().parent.parent / "docs"


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
                other_income=100000.0, other_losses=0.0, province="ON",
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


def _project(tmp: str, books) -> Path:
    """A 2025 ON project; `books`: {account: .tt text}, all taxable."""
    root = Path(tmp) / "p"
    accounts = ""
    for acct, book in books.items():
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "book.tt").write_text(book)
        accounts += f'[accounts.{acct}]\ntype = "taxable"\n'
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\n'
        'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        'source_currencies = []\nprovince = "ON"\n' + accounts
        + '[estimate]\nother_income = 100000\n')
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


def _base_book(tmp: Path, acct: str, rows) -> None:
    (tmp / f"{acct}_base.json").write_text(json.dumps(
        {"transactions": rows}))


def _div(sym: str, amount: float, date: str = "2025-06-02") -> dict:
    return {"action": "DIVIDEND", "date": date, "symbol": sym,
            "net_amount": amount, "gross_amount": amount}


def _tax(sym: str, amount: float, date: str = "2025-06-02") -> dict:
    return {"action": "TAX", "date": date, "symbol": sym,
            "net_amount": amount, "gross_amount": amount}


def _spin(sym: str, amount: float, date: str = "2025-06-03") -> dict:
    return {"action": "DIVIDEND", "date": date, "symbol": sym,
            "net_amount": amount, "gross_amount": amount,
            "corp_election": "taxable_deemed_dividend",
            "corp_event_id": f"{date}-zzp-zzs", "type": "dividend"}


# The backlog's case: one account, ZZQ.US pays 1,000 with 300 withheld,  # pii-ok
# ZZR.US pays 1,000 with nothing withheld; other income 100,000.  # pii-ok
_TWO = ("BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0\n"
        "BUYSELL 2025-01-06 10:00:00 ZZR.US 10 CAD 10 100 0\n"
        "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 100 1000\n"
        "TAX 2025-06-02 10:00:00 ZZQ.US 10 CAD 30 300\n"
        "DIVIDEND 2025-06-02 10:00:00 ZZR.US 10 CAD 100 1000\n")

# By hand (taxable income 102,000 with, 100,000 alone):  # pii-ok
#   federal: 57,375 x 14.5% + 44,625 x 20.5% = 17,467.50 less the BPA  # pii-ok
#   credit 16,129 x 14.5% = 2,338.705: basic federal tax 15,128.795;  # pii-ok
#   the T2209 limit 15,128.795 x 2,000 / 102,000 = 296.64. The foreign  # pii-ok
#   tax counted is 150 (15% of ZZQ's 1,000) + 0 (ZZR: none withheld),  # pii-ok
#   all of it federal: 15,128.795 - 150 = 14,978.795; alone 14,718.795:  # pii-ok
#   260.00 more.
#   ON: 52,886 x 5.05% + 49,114 x 9.15% = 7,164.674 less 643.7235 =  # pii-ok
#   6,520.9505, surtax 20% x 810.9505 = 162.1901: 6,683.1406; alone  # pii-ok
#   6,337.9505 + 125.5901 = 6,463.5406: 219.60 more (the premium is  # pii-ok
#   750 both ways).
#   Estimated tax: 479.60. It was 329.60: 300 counted, 296.64 federal
#   and 3.36 Ontario.
_EST = 479.6
_EST_OLD = 329.6


class TestEachSecuritysOwnLimit(unittest.TestCase):

    @rule("CA-EST-FTC")
    def test_the_backlog_case(self):
        entries = [
            {"security": "ZZQ.US", "dividends": 1000.0, "withheld": 300.0,
             "assumed": 0.0},
            {"security": "ZZR.US", "dividends": 1000.0, "withheld": 0.0,
             "assumed": 0.0}]
        r = _ca(foreign_div=2000.0, actual_withheld=300.0,
                withholding_by_security=entries)
        self.assertEqual(r["foreign_tax_creditable"], 150.0)
        self.assertEqual(r["ftc_federal"], 150.0)
        self.assertEqual(r["ftc_provincial"], 0.0)
        self.assertEqual(r["foreign_withholding_over_cap"], 150.0)
        self.assertEqual(r["foreign_withholding_over_cap_by_security"],
                         {"ZZQ.US": 150.0})
        self.assertEqual(r["foreign_withholding_unpaired"],
                         {"withheld": 0.0, "counted": 0.0, "accounts": []})
        self.assertEqual(r["estimated_tax"], _EST)
        self.assertEqual(r["ftc_source"], "actual TAX rows (capped at 15% "
                         "of each security's foreign divs)")
        self.assertIn("Foreign tax not credited: the 150.00 withheld above "
                      "15% of each security's foreign dividends (ZZQ.US "
                      "150.00; ITA s.20(11)) may be deductible on line "
                      "23200 (form T2209); the estimate does not deduct "
                      "it: a deduction you claim goes in [estimate] "
                      "deductions.", r["notes"])
        # A caller with no pairing: all foreign dividends as one property
        # (the figures before the fix).
        old = _ca(foreign_div=2000.0, actual_withheld=300.0)
        self.assertEqual(old["foreign_tax_creditable"], 300.0)
        self.assertEqual(old["ftc_federal"], 296.64)
        self.assertEqual(old["ftc_provincial"], 3.36)
        self.assertEqual(old["estimated_tax"], _EST_OLD)
        self.assertEqual(old["foreign_withholding_over_cap_by_security"],
                         {})

    @rule("CA-EST-FTC", "CA-PLAN-08")
    def test_the_backlog_case_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": _TWO})
            est = json.loads(_ok(_cli(root, "estimate",
                                      "--json")).stdout)["estimate"]
            text = _ok(_cli(root, "estimate")).stdout
            details = _ok(_cli(root, "estimate", "--details")).stdout
            plot = json.loads(_ok(_cli(root, "plot-tax",
                                       "--json")).stdout)
        self.assertEqual(est["foreign_tax_creditable"], 150.0)
        self.assertEqual(est["ftc_federal"], 150.0)
        self.assertEqual(est["ftc_provincial"], 0.0)
        self.assertEqual(est["foreign_withholding_over_cap"], 150.0)
        self.assertEqual(est["foreign_withholding_over_cap_by_security"],
                         {"ZZQ.US": 150.0})
        self.assertEqual(est["estimated_tax"], _EST)
        self.assertIn("[FTC 150.00, from the TAX rows]", text)
        flat = _flat(details)
        self.assertIn("[FTC 150.00 — actual TAX rows (capped at 15% of "
                      "each security's foreign divs)]", flat)
        self.assertIn("the 150.00 withheld above 15% of each security's "
                      "foreign dividends (ZZQ.US 150.00; ITA s.20(11))",
                      flat)
        # plot-tax pairs the same way, month by month.
        self.assertEqual(plot["year_estimate"], _EST)
        self.assertEqual(plot["months_total"], _EST)

    @rule("CA-EST-FTC")
    def test_an_account_with_no_tax_row_lends_no_room(self):
        # ZZR in `qt`, whose books carry no TAX row: 15% assumed on it
        # fills its own room, so ZZQ's excess stays above the limit and
        # the figures are what the old cap gave (300).
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {
                "ib": ("BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0\n"
                       "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 100 1000\n"
                       "TAX 2025-06-02 10:00:00 ZZQ.US 10 CAD 30 300\n"),
                "qt": ("BUYSELL 2025-01-06 10:00:00 ZZR.US 10 CAD 10 100 0\n"
                       "DIVIDEND 2025-06-02 10:00:00 ZZR.US 10 CAD 100 1000\n"
                       )})
            est = json.loads(_ok(_cli(root, "estimate",
                                      "--json")).stdout)["estimate"]
        self.assertEqual(est["foreign_tax_creditable"], 300.0)
        self.assertEqual(est["foreign_tax_from_rows"], 150.0)
        self.assertEqual(est["foreign_tax_by_assumption"], 150.0)
        self.assertEqual(est["foreign_withholding_over_cap"], 150.0)
        self.assertEqual(est["foreign_withholding_over_cap_by_security"],
                         {"ZZQ.US": 150.0})
        self.assertEqual(est["estimated_tax"], _EST_OLD)

    @rule("CA-EST-FTC")
    def test_a_spin_off_in_another_account_lends_no_room(self):
        # `ib`: ZZQ.US 1,000 with 300 withheld. `qt`, no TAX row: a  # pii-ok
        # spin-off's deemed dividend of 1,000 on ZZS.US (paid in shares:  # pii-ok
        # none assumed). The old cap, 15% of 2,000, credited all 300.  # pii-ok
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            _base_book(w, "ib", [_div("ZZQ.US", 1000.0),
                                 _tax("ZZQ.US", 300.0)])
            _base_book(w, "qt", [_spin("ZZS.US", 1000.0)])
            fba = {"ib": 1000.0, "qt": 1000.0}
            fbs = {"ib": {"ZZQ.US": 1000.0}, "qt": {"ZZS.US": 1000.0}}
            kw = R._withholding_of_year(w, {"ib", "qt"}, 2025, fba, fbs)
            # Without the pairing, the totals as before.
            self.assertEqual(R._withholding_of_year(w, {"ib", "qt"}, 2025,
                                                    fba),
                             {"actual_withheld": 300.0,
                              "withheld_assumed": 0.0,
                              "foreign_div_noncash": 1000.0})
        self.assertEqual(kw["actual_withheld"], 300.0)
        self.assertEqual(kw["foreign_div_noncash"], 1000.0)
        self.assertEqual(kw["withholding_by_security"], [
            {"security": "ZZQ.US", "dividends": 1000.0, "withheld": 300.0,
             "assumed": 0.0},
            {"security": "ZZS.US", "dividends": 1000.0, "withheld": 0.0,
             "assumed": 0.0}])
        r = _ca(foreign_div=2000.0, **kw)
        self.assertEqual(r["foreign_tax_creditable"], 150.0)
        self.assertEqual(r["foreign_withholding_over_cap_by_security"],
                         {"ZZQ.US": 150.0})
        self.assertEqual(r["estimated_tax"], _EST)
        old = _ca(foreign_div=2000.0, actual_withheld=300.0,
                  foreign_div_noncash=1000.0)
        self.assertEqual(old["foreign_tax_creditable"], 300.0)

    @rule("CA-EST-FTC")
    def test_a_security_is_summed_across_accounts(self):
        # ZZQ.US in `ib` (1,000, 300 withheld) and in `rbc` (1,000, no  # pii-ok
        # withholding on it; its books' one TAX row is ZZT's): one
        # property, 15% of 2,000 = 300 is its limit; ZZT's 15 of 100.  # pii-ok
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            _base_book(w, "ib", [_div("ZZQ.US", 1000.0),
                                 _tax("ZZQ.US", 300.0)])
            _base_book(w, "rbc", [_div("ZZQ.US", 1000.0),
                                  _div("ZZT.US", 100.0),
                                  _tax("ZZT.US", 15.0)])
            fbs = {"ib": {"ZZQ.US": 1000.0},
                   "rbc": {"ZZQ.US": 1000.0, "ZZT.US": 100.0}}
            fba = {a: sum(v.values()) for a, v in fbs.items()}
            kw = R._withholding_of_year(w, {"ib", "rbc"}, 2025, fba, fbs)
        lim = TE.ca_withholding_limit(kw["withholding_by_security"])
        self.assertAlmostEqual(lim["creditable"], 315.0, places=9)
        self.assertEqual(lim["over_cap"], 0.0)
        r = _ca(foreign_div=2100.0, **kw)
        self.assertEqual(r["foreign_tax_creditable"], 315.0)
        self.assertEqual(r["foreign_withholding_over_cap"], 0.0)

    @rule("CA-EST-FTC")
    def test_the_assumption_alone_is_unchanged(self):
        # No TAX row anywhere: `qt`'s ZZR 300 in cash and a 500 deemed
        # dividend on ZZS: 15% x 300 = 45, as before (backlog TJ-0422).
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            _base_book(w, "qt", [_div("ZZR.US", 300.0),
                                 _spin("ZZS.US", 500.0)])
            fbs = {"qt": {"ZZR.US": 300.0, "ZZS.US": 500.0}}
            kw = R._withholding_of_year(w, {"qt"}, 2025, {"qt": 800.0},
                                        fbs)
        self.assertIsNone(kw["actual_withheld"])
        r = _ca(other_income=200000.0, foreign_div=800.0, **kw)
        self.assertEqual(r["foreign_tax_creditable"], 45.0)
        self.assertEqual(r["ftc_basis"], "assumed")
        self.assertEqual(r["foreign_withholding_over_cap"], 0.0)
        self.assertEqual(r["ftc_source"],
                         "assumed 15% of foreign dividends; none on 500.00 "
                         "of spin-off deemed dividends, paid in shares")

    @rule("CA-EST-FTC")
    def test_a_month_pairs_what_was_paid_by_its_end(self):
        # plot-tax's month: by May, ZZQ's dividend and TAX row are not
        # there yet, so nothing is paired.
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            _base_book(w, "margin", [_div("ZZR.US", 1000.0, "2025-03-03"),
                                     _div("ZZQ.US", 1000.0),
                                     _tax("ZZQ.US", 300.0)])
            nc = {}
            rows = R._withholding_rows(w, {"margin"}, 2025, None, nc)
        may = R._withholding_by_security(
            rows, {"margin": {"ZZR.US": 1000.0}}, "2025-05-31", nc)
        self.assertEqual(TE.ca_withholding_limit(may)["creditable"], 0.0)
        june = R._withholding_by_security(
            rows, {"margin": {"ZZR.US": 1000.0, "ZZQ.US": 1000.0}},
            "2025-06-30", nc)
        lim = TE.ca_withholding_limit(june)
        self.assertEqual((lim["creditable"], lim["over_cap"]),
                         (150.0, 150.0))


class TestTaxRowsWithNoSecurity(unittest.TestCase):
    """A TAX row the books cannot pair: no symbol, or a symbol with no
    foreign dividend in the year. It counts up to 15% of its account's
    cash dividends that no TAX row names, and the estimate says so."""

    def _kw(self, unpaired: float):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            _base_book(w, "margin", [
                _div("ZZQ.US", 1000.0), _tax("ZZQ.US", 300.0),
                _div("ZZR.US", 1000.0),
                # ZZR's withholding, booked with no symbol, in two rows:
                # one blank, one under a ticker with no dividend.
                _tax("", unpaired / 2), _tax("ZZX.US", unpaired / 2)])
            fbs = {"margin": {"ZZQ.US": 1000.0, "ZZR.US": 1000.0}}
            return R._withholding_of_year(w, {"margin"}, 2025,
                                          {"margin": 2000.0}, fbs)

    @rule("CA-EST-FTC")
    def test_within_the_room_left(self):
        # 40 unpaired: ZZR's 1,000 is named by no TAX row, room 150: all  # pii-ok
        # 40 counted. ZZQ's 300 still counts 150. Foreign tax 190, all
        # federal: the estimate is 479.60 - 40 = 439.60.
        kw = self._kw(40.0)
        self.assertEqual(kw["withholding_by_security"][-1],
                         {"security": "", "account": "margin",
                          "dividends": 1000.0, "withheld": 40.0,
                          "assumed": 0.0})
        r = _ca(foreign_div=2000.0, **kw)
        self.assertEqual(r["foreign_tax_creditable"], 190.0)
        self.assertEqual(r["ftc_federal"], 190.0)
        self.assertEqual(r["estimated_tax"], 439.6)
        self.assertEqual(r["foreign_withholding_over_cap"], 150.0)
        self.assertEqual(r["foreign_withholding_unpaired"],
                         {"withheld": 40.0, "counted": 40.0,
                          "accounts": ["margin"]})
        self.assertIn("Foreign tax withheld with no security: 40.00 of TAX "
                      "rows (margin) name no security, or one with no "
                      "foreign dividend in 2025, so the estimate cannot "
                      "pair them with their dividends; they count up to "
                      "15% of their account's foreign cash dividends that "
                      "no TAX row names: 40.00 counted.", r["notes"])

    @rule("CA-EST-FTC")
    def test_above_the_room_left(self):
        # 200 unpaired, room 150: 150 counted, 50 not. Foreign tax 300:
        # federal 296.64, ON 3.36, the old figures; 200 above the limit.
        r = _ca(foreign_div=2000.0, **self._kw(200.0))
        self.assertEqual(r["foreign_tax_creditable"], 300.0)
        self.assertEqual(r["ftc_federal"], 296.64)
        self.assertEqual(r["ftc_provincial"], 3.36)
        self.assertEqual(r["estimated_tax"], _EST_OLD)
        self.assertEqual(r["foreign_withholding_over_cap"], 200.0)
        self.assertEqual(r["foreign_withholding_over_cap_by_security"],
                         {"ZZQ.US": 150.0})
        self.assertEqual(r["foreign_withholding_unpaired"],
                         {"withheld": 200.0, "counted": 150.0,
                          "accounts": ["margin"]})
        notes = _flat(" ".join(r["notes"]))
        self.assertIn("no TAX row names: 150.00 counted, 50.00 not.", notes)
        self.assertIn("The 200.00 withheld above 15% of each security's "
                      "foreign dividends (ZZQ.US 150.00, TAX rows with no "
                      "security 50.00; ITA s.20(11)) may be deductible on "
                      "line 23200", notes)


class TestTheRuleSaysSo(unittest.TestCase):

    @rule("CA-EST-FTC")
    def test_rule_assumptions_and_doc(self):
        text = _flat(_rule_text("CA-EST-FTC"))
        for frag in ("of the foreign dividends of the security it was "
                     "withheld on", "s.126(7)", "s.20(11)",
                     "across the taxable accounts",
                     "A TAX row the books cannot pair"):
            self.assertIn(frag, text)
        flat = _flat(TE.CA_ASSUMPTIONS)
        self.assertIn("up to 15% of each security's foreign dividends",
                      flat)
        self.assertIn("a TAX row that names no security", flat)
        doc = _flat((DOCS / "tax-rules.md").read_text())
        self.assertIn("up to 15% of the foreign dividends of the security "
                      "it was withheld on", doc)

    @rule("CA-EST-FTC")
    def test_many_securities_named_largest_first(self):
        lim = TE.ca_withholding_limit([
            {"security": f"ZZ{c}.US", "dividends": 100.0,
             "withheld": 15.0 + i, "assumed": 0.0}
            for i, c in enumerate("ABCDE", start=1)])
        self.assertEqual(lim["over_cap"], 15.0)
        text = TE.ca_foreign_deduction_text(0.0, lim["over_cap"], lim)
        self.assertIn("(ZZE.US 5.00, ZZD.US 4.00, ZZC.US 3.00, 2 more; "
                      "ITA s.20(11))", text)


if __name__ == "__main__":
    unittest.main()
