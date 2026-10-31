"""Fix round 7 of 2026-10-29, the estimate group.

TJ-0719  a minimum tax carryover is recovered up to s.120.2(1)(b)'s
         limit: basic federal tax before the foreign tax credit less
         the minimum amount of s.127.51. The estimate used the regular
         federal tax after the foreign tax credit, so a carryover the
         law allows lapsed.
TJ-0653  the line 40427 credit is the nominal amount recovered; the
         tax falls by less when the lower tax limits the foreign tax
         credit, and that part was called the credit (federal) or an
         Alberta surtax (provincial).

The expected figures are worked by hand from the 2025 tables (federal
14.5% to 57,375, BPA 16,129; AB 6% to 60,000, BPA 22,323, factor 35%).  # pii-ok
Synthetic books only: made-up tickers, the accounts `a`, `b`.
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
                other_income=100000.0, other_losses=0.0, province="ON",
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


def _project(root: Path, books: dict, *, estimate: str = "",
             maptext: str = "", province: str = "ON") -> Path:
    """A Canadian 2025 project with taxable accounts: `books` maps
    (account, file name) to its text."""
    accts = sorted({a for a, _f in books})
    (root / "taxjson.toml").write_text(
        "[settings]\ncountry = \"canada\"\nyear = 2025\n"
        f"province = \"{province}\"\nbase_currency = \"CAD\"\n"
        "source_currencies = []\n"
        "local_timezone = \"America/Toronto\"\n\n"
        + "".join(f"[accounts.{a}]\ntype = \"taxable\"\n\n"
                  for a in accts)
        + "[estimate]\nother_income = 100000\n" + estimate)
    (root / "ticker.map").write_text(maptext)
    for (a, f), text in books.items():
        d = root / "inputs" / a
        d.mkdir(parents=True, exist_ok=True)
        (d / f).write_text(text)
    return root


def _ab_foreign(carry):
    """TJ-0719/TJ-0653's fixture: AB, other income 30000, foreign
    dividends 30000 with 4500 withheld."""
    return _ca(other_income=30000.0, foreign_div=30000.0,
               actual_withheld=4500.0, province="AB",
               amt_carryover=carry,
               amt_carryover_source="[estimate] amt_carryover")


# ------------------------------------------------------------ TJ-0719
class TestRecoveryLimitBeforeForeignCredit(unittest.TestCase):

    @rule("CA-AMT-04")
    def test_limit_is_basic_federal_tax_less_minimum_amount(self):
        r = _ab_foreign({2018: 10000.0})
        a = r["amt"]
        c = a["carryover"]
        self.assertEqual(a["basic_fed"], 6518.8)
        self.assertEqual(a["minimum_amount"], 0.0)
        self.assertEqual(a["recovery_limit"], 6518.8)
        self.assertEqual(c["limit"], 6518.8)
        self.assertEqual(c["recovered_by_year"], {"2018": 6518.8})
        self.assertEqual(c["lapsing_by_year"], {"2018": 3481.2})
        self.assertEqual(r["estimated_tax_with_amt"], 366.29)
        # The AMT headroom (after both foreign tax credits) is still
        # what it was: how far regular tax is over the minimum.
        self.assertEqual(a["headroom"], 3259.4)

    @rule("CA-AMT-04")
    def test_no_foreign_credit_limit_unchanged(self):
        # With no foreign tax credit the two figures agree.
        r = _ca(other_income=200000.0, amt_carryover={2024: 900.5})
        a = r["amt"]
        self.assertAlmostEqual(a["recovery_limit"],
                               a["basic_fed"] - a["minimum_amount"],
                               delta=0.011)
        self.assertEqual(a["carryover"]["recovered_federal"], 900.5)

    @rule("CA-AMT-04")
    def test_binding_year_recovers_nothing(self):
        r = _ca(other_income=0.0, realized=400000.0,
                amt_carryover={2024: 1000.0})
        a = r["amt"]
        self.assertTrue(a["binding"])
        self.assertEqual(a["recovery_limit"], 0.0)
        self.assertEqual(a["carryover"]["recovered_by_year"], {})

    def test_no_carryover_note_names_the_limit(self):
        r = _ab_foreign(None)
        text = " ".join(r["notes"])
        self.assertIn("6,518.80", text)
        self.assertIn("s.120.2(1)(b)", text)


# ------------------------------------------------------------ TJ-0653
class TestNominalRecoveryCredit(unittest.TestCase):

    @rule("CA-AMT-05")
    def test_nominal_credit_and_ftc_interaction_apart(self):
        r = _ab_foreign({2024: 500.0})
        c = r["amt"]["carryover"]
        self.assertEqual(c["recovered_by_year"], {"2024": 500.0})
        # Line 40427: the nominal amount, not the federal tax drop.
        self.assertEqual(c["recovered_federal"], 500.0)
        self.assertEqual(c["recovered_federal_tax"], 250.0)
        self.assertEqual(c["recovered_provincial_credit"], 175.0)
        # Alberta has no surtax: none is reported.
        self.assertEqual(c["recovered_provincial_surtax"], 0.0)
        self.assertEqual(c["recovered_provincial"], 353.98)
        self.assertEqual(c["recovered_credits_reduced"], 71.02)
        self.assertAlmostEqual(
            c["recovered_federal"] + c["recovered_provincial_credit"]
            + c["recovered_provincial_surtax"]
            - c["recovered_credits_reduced"], c["recovered"], delta=0.011)
        self.assertEqual(c["closing"], 0.0)
        text = "\n".join(AR.render(AR.build(r, 2025, "CAD"),
                                   details=True))
        self.assertNotIn("surtax it saves", text)
        fed = next(ln for ln in text.splitlines()
                   if "Recovered: federal (line 40427)" in ln)
        self.assertIn("500.00", fed)
        self.assertIn("foreign tax credit it limits", text)
        note = next(n for n in r["notes"]
                    if n.startswith("Minimum tax carryover applied"))
        self.assertIn("500.00 federal", note)
        self.assertNotIn("surtax", note)
        self.assertIn("71.02", note)

    @rule("CA-AMT-05")
    def test_ontario_surtax_is_the_surtax_itself(self):
        r = _ca(other_income=200000.0, amt_carryover={2024: 900.5})
        c = r["amt"]["carryover"]
        self.assertEqual(c["recovered_federal"], 900.5)
        self.assertGreater(c["recovered_provincial_surtax"], 0.0)
        self.assertEqual(c["recovered_credits_reduced"], 0.0)
        self.assertAlmostEqual(c["recovered_provincial_credit"]
                               + c["recovered_provincial_surtax"],
                               c["recovered_provincial"], delta=0.011)

    def test_close_year_records_the_nominal_credit(self):
        r = _ab_foreign({2024: 500.0})
        blk = CF.record_block("canada", r, {"amt_carryover": "x"})
        mt = blk["minimum_tax"]
        self.assertEqual(mt["recovered_federal"], 500.0)
        self.assertEqual(mt["recovered_federal_tax"], 250.0)
        self.assertEqual(mt["recovered_provincial_surtax"], 0.0)
        self.assertIsNone(CF.block_problem(blk))
        text = "\n".join(AR.render_recorded(2025, "filed/2025.json", mt))
        self.assertNotIn("surtax it saves", text)
        self.assertIn("foreign tax credit it limits", text)


# ------------------------------------------------------------ TJ-0727
_IB_HEAD = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers Canada Inc\n"
            "Dividends,Header,Currency,Account,Date,Description,Amount\n")


class TestEligibleCompensationFloor(unittest.TestCase):

    @rule("CA-RPT-04")
    def test_negative_eligible_total_is_floored_at_zero(self):
        r = _ca(eligible_div=-50.0)
        self.assertEqual(r["estimated_tax"], 0.0)
        self.assertEqual(r["estimated_tax_with_amt"], 0.0)
        self.assertEqual(r["grossed_eligible"], 0.0)
        self.assertEqual(r["eligible_dividends"], 0.0)
        self.assertEqual(r["eligible_compensation_excess"], 50.0)
        line = next(x for x in r["amt"]["ati_lines"]
                    if x["label"].startswith("Eligible dividends"))
        self.assertEqual(line["amount"], 0.0)
        self.assertTrue(any("s.82(1)(a.1)" in n for n in r["notes"]))
        # The same as no dividend and no payment at all.
        self.assertEqual(r["tax_with"], _ca()["tax_with"])

    def test_positive_eligible_unchanged(self):
        r = _ca(eligible_div=50.0)
        self.assertEqual(r["grossed_eligible"], 69.0)
        self.assertEqual(r["eligible_compensation_excess"], 0.0)
        self.assertFalse(any("s.82(1)(a.1)" in n for n in r["notes"]))

    @rule("CA-RPT-04")
    def test_cli_short_compensation_above_receipts(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {
                ("a", "book.csv"): _IB_HEAD
                + "Dividends,Data,CAD,U1234567,2025-06-02,"
                  "ZZQ(CA9990000001) Cash Dividend CAD 1 per Share,50\n"
                + "Dividends,Data,CAD,U1234567,2025-07-02,"
                  "ZZR(CA9990000002) Payment in Lieu of Dividend CAD 1 "
                  "per Share,-100\n",
                ("a", "book.tt"):
                    "BUYSELL 2025-01-06 10:00:00 ZZR.TO -100 CAD 10 1000 0\n"
                    "BUYSELL 2026-01-06 10:00:00 ZZR.TO 100 CAD 10 1000 0\n"})
            _ok(_cli(root, "run", "--no-input"))
            doc = json.loads(_ok(_cli(root, "estimate", "--json")).stdout)
            e = doc["estimate"]
            self.assertEqual(e["estimated_tax"], 0.0)
            self.assertEqual(e["eligible_compensation_excess"], 50.0)
            text = _ok(_cli(root, "estimate")).stdout
            self.assertIn("compensation paid above the dividends is not "
                          "deducted", " ".join(text.split()))


# ------------------------------------------------------------ TJ-0726
_IB_LLC = ("Statement,Header,Field Name,Field Value\n"
           "Statement,Data,BrokerName,Interactive Brokers LLC\n"
           "Dividends,Header,Currency,Account,Date,Description,Amount\n"
           "Dividends,Data,CAD,U1234567,2025-06-02,ZZQ(CA9990000001) "
           "Cash Dividend CAD 1 per Share,100\n")


class TestIssuerCountryPerPayment(unittest.TestCase):
    """A continuing security whose issuer becomes nonresident: each
    dividend is Canadian or foreign by its own row's ISIN country."""

    def _estimate(self, extra: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {("a", "book.csv"): _IB_LLC + extra},
                            maptext="TOBASE ZZQ.US ZZQ.TO\n")
            _ok(_cli(root, "run", "--no-input"))
            doc = json.loads(_ok(_cli(root, "estimate", "--json")).stdout)
            plot = json.loads(_ok(_cli(root, "plot-tax", "--json")).stdout)
            return doc["estimate"], plot

    @rule("CA-INC-01")
    def test_later_year_foreign_row_leaves_the_year_alone(self):
        alone, _p = self._estimate("")
        later, plot = self._estimate(
            "Dividends,Data,CAD,U1234567,2026-01-05,ZZQ(US9990000001) "
            "Cash Dividend CAD 1 per Share,100\n")
        self.assertEqual(alone["estimated_tax"], 8.92)
        self.assertEqual(later["estimated_tax"], 8.92)
        self.assertEqual(later["foreign_tax_creditable"], 0.0)
        self.assertEqual(later["grossed_eligible"], 138.0)

    @rule("CA-INC-01")
    def test_same_year_payments_classified_apart(self):
        e, plot = self._estimate(
            "Dividends,Data,CAD,U1234567,2025-12-05,ZZQ(US9990000001) "
            "Cash Dividend CAD 1 per Share,100\n")
        self.assertEqual(e["estimated_tax"], 25.4)
        self.assertEqual(e["foreign_tax_creditable"], 15.0)
        self.assertEqual(e["grossed_eligible"], 138.0)
        # plot-tax runs the same estimator month by month.
        self.assertEqual(plot["months_total"], 25.4)
        self.assertEqual(plot["year_estimate"], 25.4)
        dec = next(r for r in plot["rows"] if str(r["month"]).endswith("12"))
        june = next(r for r in plot["rows"] if str(r["month"]).endswith("06"))
        self.assertEqual(june["tax"], 8.92)
        self.assertEqual(dec["tax"], 16.48)


# ------------------------------------------------------------ TJ-0715
def _book(sym_tax: str, withheld: int) -> str:
    return ("BUYSELL 2025-01-06 10:00:00 ZZS.US 10 CAD 10 100 0\n"
            "DIVIDEND 2025-06-02 10:00:00 ZZS.US 10 CAD 100 1000\n"
            f"TAX 2025-06-02 10:00:00 {sym_tax} 10 CAD {withheld / 10} "
            f"{withheld}\n")


class TestUnpairedWithholdingAcrossAccounts(unittest.TestCase):

    def _entries(self, rows, fbs):
        from taxjson.bin import taxjson_run as R
        return R._withholding_by_security(rows, fbs)

    @rule("CA-EST-FTC")
    def test_other_accounts_named_row_leaves_room(self):
        rows = {"a": [("2025-06-02", 150.0, "ZZS.US")],
                "b": [("2025-06-02", 100.0, "")]}
        fbs = {"a": {"ZZS.US": 1000.0}, "b": {"ZZS.US": 1000.0}}
        lim = TE.ca_withholding_limit(self._entries(rows, fbs))
        self.assertAlmostEqual(lim["creditable"], 250.0, places=6)
        self.assertAlmostEqual(lim["unpaired_counted"], 100.0, places=6)

    @rule("CA-EST-FTC")
    def test_security_ceiling_still_holds(self):
        # The named row takes the security's whole 15%: nothing is left
        # for the other account's unpaired row.
        rows = {"a": [("2025-06-02", 300.0, "ZZS.US")],
                "b": [("2025-06-02", 100.0, "")]}
        fbs = {"a": {"ZZS.US": 1000.0}, "b": {"ZZS.US": 1000.0}}
        lim = TE.ca_withholding_limit(self._entries(rows, fbs))
        self.assertAlmostEqual(lim["creditable"], 300.0, places=6)
        # Two unpaired accounts share what is left, never twice.
        rows = {"a": [("2025-06-02", 200.0, "ZZS.US")],
                "b": [("2025-06-02", 100.0, "")],
                "c": [("2025-06-02", 100.0, "")]}
        fbs = {"a": {"ZZS.US": 1000.0}, "b": {"ZZS.US": 1000.0},
               "c": {"ZZS.US": 1000.0}}
        lim = TE.ca_withholding_limit(self._entries(rows, fbs))
        self.assertAlmostEqual(lim["creditable"], 400.0, places=6)
        rows["a"] = [("2025-06-02", 400.0, "ZZS.US")]
        lim = TE.ca_withholding_limit(self._entries(rows, fbs))
        self.assertAlmostEqual(lim["creditable"], 450.0, places=6)

    @rule("CA-EST-FTC")
    def test_cli_named_and_unpaired_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {("a", "book.tt"): _book("ZZS.US", 150),
                                       ("b", "book.tt"): _book("CASH", 100)})
            _ok(_cli(root, "run", "--no-input"))
            e = json.loads(_ok(_cli(root, "estimate", "--json")).stdout)[
                "estimate"]
            self.assertEqual(e["foreign_tax_creditable"], 250.0)
            self.assertEqual(e["estimated_tax"], 379.6)


# ------------------------------------------------------------ TJ-0783
class TestPlotTaxUnknownCost(unittest.TestCase):

    def _plot(self, unknown: bool):
        rows = ("BUYSELL 2025-01-02 10:00:00 ZZT.TO 10 CAD 1 10 0\n"
                "BUYSELL 2025-03-03 10:00:00 ZZT.TO -10 CAD 2 20 0\n")
        if unknown:
            rows = ("OPENING 2025-01-02 ZZU.TO 100 cost=unknown\n"
                    "BUYSELL 2025-02-03 10:00:00 ZZU.TO -100 CAD 1 100 0\n"
                    + rows)
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {("a", "book.tt"): rows})
            _ok(_cli(root, "run", "--no-input"))
            js = _ok(_cli(root, "plot-tax", "--json"))
            text = _ok(_cli(root, "plot-tax"))
            est = json.loads(_ok(_cli(root, "estimate", "--json")).stdout)
            return json.loads(js.stdout), js.stderr, text.stdout, est

    def test_excluded_sale_is_disclosed(self):
        doc, err, text, est = self._plot(True)
        self.assertEqual(est["unknown_cost_routed"], 1)
        self.assertEqual(doc["unknown_cost_routed"], 1)
        self.assertFalse(doc["complete"])
        self.assertIn("routed to manual reporting", " ".join(err.split()))
        flat = " ".join(text.split())
        self.assertIn("1 sale(s) with an unknown cost NOT in these figures",
                      flat)
        self.assertIn("tjs form-export", flat)

    def test_complete_books_say_nothing(self):
        doc, err, text, _e = self._plot(False)
        self.assertEqual(doc["unknown_cost_routed"], 0)
        self.assertEqual(doc["unknown_cost_included"], 0)
        self.assertTrue(doc["complete"])
        self.assertNotIn("unknown cost", text)
        self.assertNotIn("unknown cost", err)


if __name__ == "__main__":
    unittest.main()
