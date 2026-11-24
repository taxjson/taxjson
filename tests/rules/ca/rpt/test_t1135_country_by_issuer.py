"""`tjs t1135`: a security's country by its issuer, not by its listing
(backlog TJ-4957, with TJ-4964, TJ-5048, TJ-5049).

The country is decided in order, and each row says by which
(`country_source`): a ticker.map T1135 line; the ISIN an export gives
the security (every row of an Interactive Brokers instrument, not only
a dividend) and tobase.map's issuer country; the same security's ISIN
in another account (learned, within the run); else the listing,
`assumed`. An ISIN that disagrees with the listing decides. A ticker
another security had later takes nothing from the first; a T1135 line
on any symbol of a renamed security decides it before and after the
change.

Every fixture is SYNTHETIC: invented tickers (SYN*, OLD, NEW, PRT),
made-up ISINs (`KYG000000001`, `AU000000SYN1`, `CA0000000001`),
invented account names.
"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.bin import taxjson_t1135 as T
from taxjson.bin.taxjson_t1135 import (build_report, main, render_brief,
                                       render_report)
from taxjson.lib import t1135_issuer as TI
from taxjson.project.settings import Settings

from _support.ca_t1135_walk import _r
from _support.ib_statement_rows import (FII_H, HEAD, TRADES_H, _fii_stock,
                                        _parse_ib, _trade)
import _paths

KY, AU, CA = "KYG000000001", "AU000000SYN1", "CA0000000001"
COST = 125000.0             # over the default threshold on its own


def buy(symbol, cost, day="2025-02-03", qty=100, account="ib", **kw):
    return _r("BUYSELL", day, symbol, qty, -cost, account=account, **kw)


def sell(symbol, proceeds, day, qty=100, account="ib", **kw):
    return _r("BUYSELL", day, symbol, -qty, proceeds, account=account, **kw)


def ren(old, new, day, account="ib"):
    """A ticker change."""
    return _r("SPLIT", day, old, 1, 0.0, symbol_new=new, account=account)


def split(symbol, ratio, day, account="ib", **kw):
    """A split with no ticker change, as the IB reader books one: the
    symbol again as symbol_new, the ratio as the quantity."""
    return _r("SPLIT", day, symbol, ratio, 0.0, symbol_new=symbol,
              account=account, **kw)


def report(rows, lines=(), year=2025, sheltered=None, **kw):
    """build_report over one taxable base file (no gains file: the
    income columns are not this module's), ticker.map T1135 `lines`
    read by the ticker.map parser itself; `sheltered`: a registered
    account's rows. Its warnings in `_stderr`."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "margin_base.json"
        base.write_text(json.dumps({"transactions": rows}))
        if sheltered is not None:
            reg = Path(td) / "rrsp_base.json"
            reg.write_text(json.dumps({"transactions": sheltered}))
            kw["sheltered_paths"] = [reg]
        overrides = {}
        if lines:
            m = Path(td) / "ticker.map"
            m.write_text("".join(f"{ln}\n" for ln in lines))
            overrides = T.load_overrides(m)
        kw.setdefault("today", "2026-06-01")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                mock.patch.object(T, "_DETAILS", True):
            rep = build_report([base], [], year, overrides, "CAD", **kw)
    rep["_stderr"] = " ".join(err.getvalue().split())
    return rep


def texts(rep):
    """(the default view, the --details view), each on one line."""
    return (" ".join(render_brief(rep).split()),
            " ".join(render_report(rep).split()))


def row(rep, symbol):
    return next(r for r in rep["properties"] if r["symbol"] == symbol)


def decision(rep, symbol):
    return next(e for e in rep["country_decisions"] if e["symbol"] == symbol)


# The proposal's statement: three instruments, none paying a dividend,
# and one with no ISIN anywhere.
BOOK = [buy("SYNK.US", 5000.0, isin=KY), buy("SYNA.TO", 6000.0, isin=AU),
        buy("SYNC.US", 7000.0, isin=CA), buy("SYNN.US", 800.0),
        buy("SYNT.TO", 900.0)]


class TestTheOrder(unittest.TestCase):
    @rule(R.CA_RPT_02)
    def test_the_isin_decides_over_the_listing(self):
        rep = report(BOOK)
        # SYNK on NYSE, a Cayman ISIN: CYM, not the listing's USA.
        self.assertEqual(
            {k: row(rep, "SYNK.US")[k] for k in (
                "country", "country_source", "country_source_account",
                "isin_country_code")},
            {"country": "CYM", "country_source": "isin",
             "country_source_account": "ib", "isin_country_code": "KY"})
        # SYNA on the TSX, an Australian ISIN and no dividend: on the form.
        self.assertEqual((row(rep, "SYNA.TO")["country"],
                          row(rep, "SYNA.TO")["max_cost"]), ("AUS", 6000.0))
        # SYNC on NYSE, a Canadian ISIN: off it.
        self.assertNotIn("SYNC.US", [r["symbol"] for r in rep["properties"]])
        self.assertEqual(decision(rep, "SYNC.US"), {
            "symbol": "SYNC.US", "country": "CAN", "foreign_property": False,
            "country_source": "isin", "country_source_account": "ib",
            "isin_country_code": "CA", "listing_country": "USA",
            "max_cost": 7000.0, "year_end_cost": 7000.0})
        self.assertEqual(rep["max_total_cost"], 5000.0 + 6000.0 + 800.0)
        self.assertEqual(sorted(rep["by_country"]), ["AUS", "CYM", "USA"])
        # Nothing is open: the ISIN decides.
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["country_review"], [])
        self.assertIs(rep["filing_required"], False)

    @rule(R.CA_RPT_02)
    def test_what_the_isin_overrules_is_named_with_its_line(self):
        rep = report(BOOK)
        self.assertEqual(rep["listing_overruled_symbols"],
                         ["SYNA.TO", "SYNC.US", "SYNK.US"])
        brief, full = texts(rep)
        # One `! ` line, the ones that move on or off the form first.
        self.assertIn("! 3 counted by ISIN, not listing: SYNA.TO (AUS), "
                      "SYNC.US (Canadian) +1 more — tjs t1135 --details",
                      brief)
        self.assertNotIn("COUNTED BY THE ISIN", brief)
        self.assertIn("COUNTED BY THE ISIN, NOT THE LISTING", full)
        self.assertIn("SYNA.TO: AUS by its ISIN (AU, ib); its listing says "
                      "Canadian. Cost: 6,000.00 CAD at most in the year. If "
                      "you know otherwise: `T1135 SYNA.TO CA` (or its "
                      "country of residence) in ticker.map.", full)
        self.assertIn("SYNC.US: not foreign property by its ISIN (CA, ib); "
                      "its listing says USA. Cost: 7,000.00 CAD at most in "
                      "the year. If you know otherwise: `T1135 SYNC.US USA`",
                      full)
        self.assertIn("SYNK.US: CYM by its ISIN (KY, ib); its listing says "
                      "USA.", full)
        # An ISIN that agrees with its listing is not named.
        rep = report([buy("SYNU.US", 800.0, isin="US0000000001")])
        self.assertEqual(rep["listing_overruled_symbols"], [])
        self.assertNotIn("counted by ISIN", texts(rep)[0])

    @rule(R.CA_RPT_02)
    def test_each_row_shows_what_decided_it(self):
        rep = report(BOOK + [buy("SYNM.US", 300.0)],
                     lines=["T1135 SYNM.US IRL"])
        brief, full = texts(rep)
        for text in (brief, full):
            self.assertIn("SYMBOL COUNTRY FROM CAT MAX COST IN YR", text)
            self.assertRegex(text, r"SYNA\.TO AUS ISIN 2 6,000\.00")
            self.assertRegex(text, r"SYNK\.US CYM ISIN 2 5,000\.00")
            self.assertRegex(text, r"SYNM\.US IRL line 2 300\.00")
            self.assertRegex(text, r"SYNN\.US USA assumed 2 800\.00")
        self.assertIn("MAX COST IN YR: the most held at once; FROM: what "
                      "decided the country.", brief)
        self.assertIn("SYNA.TO, SYNK.US: country from the ISIN in ib's "
                      "export", full)
        self.assertIn("SYNM.US: country from a ticker.map T1135 line "
                      "(`T1135 SYNM.US IRL`)", full)
        self.assertIn("SYNN.US: country assumed from the listing: no ISIN "
                      "in the exports", full)
        self.assertIn("An ISIN's first two letters say where the issuer is "
                      "registered; the form asks for its country of "
                      "residence", full)
        self.assertIn("A CUSIP alone names no country and is not read.",
                      full)

    @rule(R.CA_RPT_02)
    def test_a_security_with_no_isin_is_assumed(self):
        rep = report(BOOK)
        self.assertEqual(row(rep, "SYNN.US")["country_source"], "assumed")
        self.assertEqual(rep["assumed_symbols"], ["SYNN.US", "SYNT.TO"])
        self.assertEqual((rep["assumed_foreign_cost"],
                          rep["assumed_not_foreign_cost"]), (800.0, 900.0))
        # A Canadian listing assumed Canadian is listed too, with its
        # cost: the assumption that can hide a foreign company.
        self.assertEqual(decision(rep, "SYNT.TO"), {
            "symbol": "SYNT.TO", "country": "CAN", "foreign_property": False,
            "country_source": "assumed", "listing_country": "CAN",
            "max_cost": 900.0, "year_end_cost": 900.0})
        brief, full = texts(rep)
        self.assertIn("! 2 countries assumed from the listing (no ISIN): "
                      "cost 1,700.00 CAD — tjs t1135 --details", brief)
        self.assertNotIn("COUNTRY ASSUMED FROM THE LISTING", brief)
        self.assertIn("COUNTRY ASSUMED FROM THE LISTING (no ISIN in the "
                      "exports) SYMBOL ASSUMED ON THE FORM MAX COST IN YR "
                      "COST AT DEC 31", full)
        self.assertRegex(full, r"SYNN\.US USA yes 800\.00 800\.00")
        self.assertRegex(full, r"SYNT\.TO Canadian no 900\.00 900\.00")
        self.assertIn("2 in all: 800.00 CAD counted as foreign property, "
                      "900.00 CAD as Canadian (maximum costs summed).", full)
        # Assumed is not a review, nor a reason the verdict waits on.
        self.assertEqual(rep["country_review_symbols"], [])
        self.assertIs(rep["filing_required"], False)

    @rule(R.CA_RPT_02)
    def test_a_line_wins_over_all(self):
        # Over the ISIN, over tobase.map, in either direction.
        rep = report(BOOK, lines=["T1135 SYNK.US USA", "T1135 SYNA.TO CA",
                                  "T1135 SYNC.US BMU", "T1135 SYNT.TO GBR"],
                     issuer_countries={"SYNK.US": "CHE"})
        got = {e["symbol"]: (e["country"], e["country_source"])
               for e in rep["country_decisions"]}
        self.assertEqual(got, {"SYNK.US": ("USA", "override"),
                               "SYNA.TO": ("CAN", "override"),
                               "SYNC.US": ("BMU", "override"),
                               "SYNT.TO": ("GBR", "override"),
                               "SYNN.US": ("USA", "assumed")})
        self.assertEqual(row(rep, "SYNC.US")["country_line"],
                         "T1135 SYNC.US BMU")
        self.assertEqual(rep["listing_overruled_symbols"], [])
        self.assertEqual(rep["max_total_cost"],
                         5000.0 + 7000.0 + 800.0 + 900.0)

    @rule(R.CA_RPT_02)
    def test_tobase_map_gives_the_country_where_no_isin_does(self):
        rep = report([buy("SYNB.US", 800.0)],
                     issuer_countries={"SYNB.US": "BMU"})
        self.assertEqual((row(rep, "SYNB.US")["country"],
                          row(rep, "SYNB.US")["country_source"]),
                         ("BMU", "tobase_map"))
        self.assertRegex(texts(rep)[0], r"SYNB\.US BMU tobase\.map 2 ")
        # Not an assumption, and not named as overruling the listing.
        self.assertEqual(rep["assumed_symbols"], [])
        self.assertEqual(rep["listing_overruled_symbols"], [])

    @rule(R.CA_RPT_02)
    def test_the_isin_of_the_year_speaks_for_the_year(self):
        # An issuer that moved: a US ISIN on the 2023 rows, an Irish
        # one on the 2025 rows. Each year its own; no disagreement.
        rows = [buy("SYNM.US", 800.0, "2023-02-03", isin="US0000000001"),
                buy("SYNM.US", 100.0, "2025-02-03", 10, isin="IE0000000001")]
        self.assertEqual(row(report(rows), "SYNM.US")["country"], "IRL")
        self.assertEqual(row(report(rows, year=2023), "SYNM.US")["country"],
                         "USA")
        self.assertEqual(report(rows)["classification_conflicts"], [])
        # A year with no row of its own, between two that disagree:
        # nothing is picked, the earlier one neither.
        gap = report(rows, year=2024)
        self.assertEqual(row(gap, "SYNM.US")["country"], "??")
        self.assertEqual(gap["classification_conflicts"][0]["countries"],
                         ["IRL", "USA"])
        # Where one of the two is Canada it is walked both ways.
        for one, other in ((CA, "US0000000001"), ("US0000000001", CA)):
            rows = [buy("SYNC.US", COST, "2023-02-03", isin=one),
                    buy("SYNC.US", 100.0, "2025-03-03", 1, isin=other)]
            gap = report(rows, year=2024)
            self.assertEqual(gap["classification_status"], "unresolved")
            self.assertIsNone(gap["filing_required"])
            self.assertEqual((gap["max_total_cost_low"],
                              gap["max_total_cost_high"]), (0.0, COST))
        # With rows on one side only, that side speaks.
        rows = [buy("SYNM.US", 800.0, "2023-02-03", isin="IE0000000001")]
        self.assertEqual(row(report(rows, year=2024), "SYNM.US")["country"],
                         "IRL")

    @rule(R.CA_RPT_02)
    def test_an_option_keeps_its_own_listing(self):
        opt = "SYNK250620C00150000.US"
        rep = report(BOOK + [_r("BUYSELL", "2025-02-05", opt, 1, -400.0,
                                account="ib")])
        self.assertEqual((row(rep, opt)["country"],
                          row(rep, opt)["country_source"]),
                         ("USA", "listing"))
        self.assertRegex(texts(rep)[0], opt.replace(".", r"\.")
                         + r" USA listing 6 ")
        # Foreign property as its share is: nothing open.
        self.assertEqual(rep["classification_conflicts"], [])


class TestToReview(unittest.TestCase):
    @rule(R.CA_RPT_02)
    def test_an_xs_isin_is_never_a_guess(self):
        rep = report([buy("SYNX.US", 300.0, isin="XS0000000001")])
        self.assertEqual(row(rep, "SYNX.US")["country"], "??")
        self.assertEqual(row(rep, "SYNX.US")["country_review"],
                         "isin_no_country")
        self.assertEqual(rep["country_review"], [
            {"symbol": "SYNX.US", "reason": "isin_no_country", "lines": []}])
        self.assertEqual(rep["review_symbols"], ["SYNX.US"])
        brief, full = texts(rep)
        self.assertIn("! 1 to review for the country: SYNX.US (ISIN prefix "
                      "XS: no country) — tjs t1135 --details", brief)
        self.assertIn("SYNX.US: ISIN prefix XS: no country — add `T1135 "
                      "SYNX.US COUNTRY` to ticker.map once you know", full)
        self.assertRegex(brief, r"SYNX\.US \?\? review 2 300\.00")
        # Its line settles it.
        rep = report([buy("SYNX.US", 300.0, isin="XS0000000001")],
                     lines=["T1135 SYNX.US LUX"])
        self.assertEqual(rep["country_review"], [])
        self.assertEqual(row(rep, "SYNX.US")["country"], "LUX")

    @rule(R.CA_RPT_02)
    def test_a_depositary_receipt_takes_its_isin_and_is_named(self):
        for name, facts in (
                ("its type", {"security_type": "ADR"}),
                ("its name", {"security_name": "SYNTHETIC HOLDING-SPON ADR"}),
                ("a Canadian receipt",
                 {"security_name": "SYNTHETIC PLATFORMS INC-CDR"})):
            with self.subTest(said_by=name):
                rep = report([buy("SYNR.US", 400.0, isin="US0000000001",
                                  **facts)])
                # The receipt is the property held: its own ISIN's country.
                self.assertEqual((row(rep, "SYNR.US")["country"],
                                  row(rep, "SYNR.US")["country_source"]),
                                 ("USA", "isin"))
                self.assertEqual(row(rep, "SYNR.US")["country_review"],
                                 "depositary_receipt")
                self.assertEqual(rep["country_review"], [
                    {"symbol": "SYNR.US", "reason": "depositary_receipt",
                     "lines": ["T1135 SYNR.US USA"]}])
                # Named, never walked the other way: the verdict stands.
                self.assertEqual(rep["classification_conflicts"], [])
                self.assertIs(rep["filing_required"], False)
                brief, full = texts(rep)
                self.assertIn("! 1 to review for the country: SYNR.US "
                              "(depositary receipt) — tjs t1135 --details",
                              brief)
                self.assertIn("SYNR.US: depositary receipt: the company "
                              "behind it may be of another country — add "
                              "`T1135 SYNR.US USA` (to keep it), or its "
                              "line with the company's country to "
                              "ticker.map", full)
        self.assertIn("SYNR.US (depositary receipt: the company behind it "
                      "may be of another country)", rep["_stderr"])

    @rule(R.CA_RPT_02)
    def test_a_receipt_off_the_form_or_with_no_isin_is_named_too(self):
        # A receipt whose ISIN is Canadian: not foreign property by it,
        # and named (walked both ways: TestACanadianReceipt).
        rep = report([buy("SYNR.TO", 400.0, isin=CA,
                          security_name="SYNTHETIC PLATFORMS INC-CDR")])
        self.assertEqual(rep["properties"], [])
        self.assertEqual(rep["country_review"], [
            {"symbol": "SYNR.TO", "reason": "depositary_receipt",
             "lines": ["T1135 SYNR.TO CA"]}])
        # No ISIN: assumed from its listing, and named.
        rep = report([buy("SYNR.US", 400.0, security_type="ADR")])
        self.assertEqual(row(rep, "SYNR.US")["country_source"], "assumed")
        self.assertEqual(rep["country_review_symbols"], ["SYNR.US"])
        # A line ends it; a name that only holds the letters does not
        # start it.
        rep = report([buy("SYNR.US", 400.0, security_type="ADR")],
                     lines=["T1135 SYNR.US CHN"])
        self.assertEqual(rep["country_review"], [])
        for name in ("CADRE SYNTHETIC TRADRS CORP",
                     "SYNQ ADS-TEC ENERGY INC", "SYNQ CDR-LINK CORP"):
            rep = report([buy("SYNR.TO", COST, isin=CA, security_name=name)])
            self.assertEqual(rep["country_review"], [], name)
            self.assertEqual(rep["classification_conflicts"], [], name)
            self.assertIs(rep["filing_required"], False, name)
        for name in ("SYNTHETIC HOLDING SPON ADR", "SYNTHETIC INC-CDR",
                     "SYNQ LTD ADS", "SYNQ GDR (REG S)",
                     "SYNQ Depositary Shares", "SYNQ INC-CDR (CAD HEDGED)",
                     "SYNQ-CDR.", "SYNQ INC - ADR"):
            self.assertTrue(TI.says_receipt({"security_name": name}), name)
        for name in ("SYNQ-ADR HOLDINGS", "CADRE HOLDINGS"):
            self.assertFalse(TI.says_receipt({"security_name": name}), name)

    @rule(R.CA_RPT_02)
    def test_two_isins_of_one_security_in_the_year(self):
        # Two accounts' exports, two countries: nothing is picked.
        rep = report([buy("SYNK.US", 500.0, isin=KY),
                      buy("SYNK.US", 500.0, account="ib2",
                          isin="US0000000001")])
        self.assertEqual(row(rep, "SYNK.US")["country"], "??")
        self.assertEqual(
            rep["classification_conflicts"][0]["countries"], ["CYM", "USA"])
        self.assertEqual(rep["classification_conflicts"][0]["lines"], {
            "choices": ["T1135 SYNK.US CYM", "T1135 SYNK.US USA"]})
        # Two ISINs of one country agree.
        rep = report([buy("SYNK.US", 500.0, isin=KY),
                      buy("SYNK.US", 500.0, account="ib2",
                          isin="KYG000000019")])
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(row(rep, "SYNK.US")["country"], "CYM")


class TestACanadianReceipt(unittest.TestCase):
    """TJ-5105: a depositary receipt that is Canadian by its own ISIN
    (a Canadian issuer's receipt for a foreign company's shares) is not
    settled by that ISIN: off the form as counted, walked with it
    counted too."""

    CDR = "SYNTHETIC PLATFORMS INC-CDR"

    def book(self, cost, **kw):
        return [buy("SYNR.TO", cost, isin=CA, security_name=self.CDR, **kw)]

    @rule(R.CA_RPT_02)
    def test_a_large_holding_leaves_the_test_open(self):
        rep = report(self.book(COST))
        # Its country stays the receipt's ISIN's: off the form.
        self.assertEqual(rep["properties"], [])
        self.assertEqual(rep["max_total_cost"], 0.0)
        self.assertEqual((decision(rep, "SYNR.TO")["country"],
                          decision(rep, "SYNR.TO")["country_source"]),
                         ("CAN", "isin"))
        # ... and that is no verdict.
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_unresolved_symbols"],
                         ["SYNR.TO"])
        self.assertIsNone(rep["filing_required"])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, COST))
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "SYNR.TO", "kind": "depositary_receipt",
             "counted_as": "CAN", "issuer_country": None,
             "isin_country_code": "CA", "countries": ["CAN"],
             "affects": "total", "held_in_year": True,
             "symbols": {"SYNR.TO": "CAN"},
             "lines": {"foreign": None,
                       "not_foreign": ["T1135 SYNR.TO CA"]}}])
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertIn("=> NOT RESOLVED: below the 100,000.00 CAD "
                          "threshold as counted, but SYNR.TO may be foreign "
                          "property (a depositary receipt that is Canadian "
                          "by its own ISIN: the company behind it may be "
                          "foreign): with it the maximum is 125,000.00 CAD",
                          text)                             # pii-ok
            self.assertIn("Add `T1135 SYNR.TO COUNTRY` (foreign property; "
                          "COUNTRY: the country of the company behind the "
                          "receipt, its three-letter code) or `T1135 "
                          "SYNR.TO CA` (not foreign property) to ticker.map",
                          text)

    @rule(R.CA_RPT_02)
    def test_either_line_settles_it(self):
        rep = report(self.book(COST), lines=["T1135 SYNR.TO USA"])
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["country_review"], [])
        self.assertEqual(rep["max_total_cost"], COST)
        self.assertIs(rep["filing_required"], True)
        rep = report(self.book(COST), lines=["T1135 SYNR.TO CA"])
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["max_total_cost"], 0.0)
        self.assertIs(rep["filing_required"], False)

    @rule(R.CA_RPT_02)
    def test_a_small_holding_cannot_change_the_verdict(self):
        rep = report(self.book(900.0))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], False)
        for text in texts(rep):
            self.assertIn("no T1135 required", text)
            self.assertIn("Counting SYNR.TO the other way (a depositary "
                          "receipt and the company behind it) does not "
                          "change this (the maximum is 0.00 to 900.00 CAD).",
                          text)
        # Named for review all the same.
        self.assertEqual(rep["country_review_symbols"], ["SYNR.TO"])

    @rule(R.CA_RPT_02)
    def test_over_the_threshold_without_it_stays_required(self):
        rep = report(self.book(COST) + [buy("SYNU.US", COST, "2025-02-04")])
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], True)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (COST, 2 * COST))
        # The part depends on it: 125,000 or 250,000. pii-ok
        self.assertIsNone(rep["simplified_method_available"])
        self.assertIn("FILING REQUIRED", texts(rep)[0])

    @rule(R.CA_RPT_02)
    def test_a_receipt_with_a_foreign_isin_is_named_not_walked(self):
        rep = report([buy("SYNR.US", COST, isin="US0000000001",
                          security_type="ADR")])
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertIs(rep["filing_required"], True)
        self.assertEqual(rep["country_review_symbols"], ["SYNR.US"])


class TestLearned(unittest.TestCase):
    """A country an ISIN gave in one account, for the same security in
    the accounts whose exports carry none: within the run."""

    # Bought at ib (an ISIN), then at q (none); ib sold in 2024.
    ROWS = [buy("SYNK.US", 5000.0, "2023-02-03", isin=KY),
            buy("SYNK.US", 5000.0, "2024-02-03", account="q"),
            sell("SYNK.US", 6000.0, "2024-05-01", isin=KY)]

    @rule(R.CA_RPT_21)
    def test_it_keeps_its_country_at_the_other_broker(self):
        rep = report(self.ROWS)
        self.assertEqual(
            {k: row(rep, "SYNK.US")[k] for k in (
                "country", "country_source", "country_source_account",
                "isin_country_code")},
            {"country": "CYM", "country_source": "learned",
             "country_source_account": "ib", "isin_country_code": "KY"})
        brief, full = texts(rep)
        self.assertRegex(brief, r"SYNK\.US CYM learned 2 5,000\.00")
        self.assertIn("SYNK.US: country learned from the ISIN in ib's "
                      "export of the same security", full)
        self.assertIn("SYNK.US: CYM by its ISIN (KY, learned from ib); its "
                      "listing says USA.", full)
        self.assertEqual(rep["assumed_symbols"], [])

    @rule(R.CA_RPT_21)
    def test_held_in_both_it_is_the_isins_and_says_who_follows(self):
        rep = report(self.ROWS, year=2024)
        r = row(rep, "SYNK.US")
        self.assertEqual((r["country"], r["country_source"],
                          r["country_source_account"],
                          r["country_learned_accounts"]),
                         ("CYM", "isin", "ib", ["q"]))
        self.assertIn("SYNK.US: country from the ISIN in ib's export, for "
                      "q too", texts(rep)[1])

    @rule(R.CA_RPT_21)
    def test_not_across_a_ticker_another_company_uses_later(self):
        # Sold out everywhere in 2022; the ticker bought at q in 2025.
        rows = [buy("SYNK.US", 5000.0, "2022-02-03", isin=KY),
                sell("SYNK.US", 5000.0, "2022-05-01", isin=KY),
                buy("SYNK.US", 7000.0, "2025-03-03", account="q")]
        rep = report(rows)
        self.assertEqual((row(rep, "SYNK.US")["country"],
                          row(rep, "SYNK.US")["country_source"]),
                         ("USA", "assumed"))
        self.assertEqual(rep["assumed_symbols"], ["SYNK.US"])
        # The earlier holding's year still reads its own ISIN.
        self.assertEqual(row(report(rows, year=2022), "SYNK.US")["country"],
                         "CYM")

    @rule(R.CA_RPT_21)
    def test_an_earlier_holdings_isin_stays_in_play(self):
        # TJ-5106: SYNA.TO at ib in 2022 (an Australian ISIN), sold
        # out; bought at q in 2025. Not learned (the ticker may be
        # another company's), and not thrown away: walked both ways.
        rows = [buy("SYNA.TO", 5000.0, "2022-02-03", isin=AU),
                sell("SYNA.TO", 5000.0, "2022-05-02", isin=AU),
                buy("SYNA.TO", COST, "2025-03-03", account="q")]
        rep = report(rows)
        self.assertEqual(rep["properties"], [])
        self.assertEqual((decision(rep, "SYNA.TO")["country"],
                          decision(rep, "SYNA.TO")["country_source"],
                          decision(rep, "SYNA.TO")["country_review"]),
                         ("CAN", "assumed", "earlier_holding"))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, COST))
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "SYNA.TO", "kind": "earlier_holding",
             "counted_as": "CAN", "issuer_country": None,
             "isin_country_code": None, "countries": ["AUS"],
             "earlier": [{"account": "ib", "country": "AUS",
                          "isin_country_code": "AU", "first": "2022-02-03",
                          "last": "2022-05-02"}],
             "affects": "total", "held_in_year": True,
             "symbols": {"SYNA.TO": "CAN"},
             "lines": {"foreign": ["T1135 SYNA.TO AUS"],
                       "not_foreign": ["T1135 SYNA.TO CA"]}}])
        self.assertEqual(rep["country_review_symbols"], ["SYNA.TO"])
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertIn("=> NOT RESOLVED: below the 100,000.00 CAD "
                          "threshold as counted, but SYNA.TO may be foreign "
                          "property (another holding of the ticker carried "
                          "an ISIN that says otherwise: AUS (ib, 2022)): "
                          "with it the maximum is 125,000.00 CAD",
                          text)                             # pii-ok
        # Either line settles it.
        for line, total in (("T1135 SYNA.TO AUS", COST),
                            ("T1135 SYNA.TO CA", 0.0)):
            rep = report(rows, lines=[line])
            self.assertEqual(rep["classification_conflicts"], [])
            self.assertEqual(rep["max_total_cost"], total)
        # A small holding: the verdict holds, and it is still named.
        rep = report(rows[:2] + [buy("SYNA.TO", 900.0, "2025-03-03",
                                     account="q")])
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIn("Counting SYNA.TO the other way (another holding's "
                      "ISIN says otherwise) does not change this",
                      texts(rep)[0])

    @rule(R.CA_RPT_21)
    def test_the_mirror_a_canadian_isin_earlier(self):
        # SYNC.US at ib (a Canadian ISIN), sold out in 2023; bought at
        # q in 2025: counted (its listing), and not firmly.
        rows = [buy("SYNC.US", 5000.0, "2022-02-03", isin=CA),
                sell("SYNC.US", 6000.0, "2023-04-03", isin=CA),
                buy("SYNC.US", COST, "2025-02-03", account="q")]
        rep = report(rows)
        self.assertEqual(row(rep, "SYNC.US")["country"], "USA")
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, COST))
        self.assertIn("SYNC.US may not be foreign property (another "
                      "holding of the ticker carried an ISIN that says "
                      "otherwise: CAN (ib, 2022 to 2023))", texts(rep)[0])

    @rule(R.CA_RPT_21)
    def test_an_earlier_isin_of_another_foreign_country_is_named(self):
        # SYNK.US at ib in 2022 (a Cayman ISIN), sold out; bought at q
        # in 2025: foreign either way, so nothing is walked, and the
        # form lists property by country: named with both lines.
        rows = [buy("SYNK.US", 5000.0, "2022-02-03", isin=KY),
                sell("SYNK.US", 5000.0, "2022-05-02", isin=KY),
                buy("SYNK.US", 7000.0, "2025-03-03", account="q")]
        rep = report(rows)
        self.assertEqual((row(rep, "SYNK.US")["country"],
                          row(rep, "SYNK.US")["country_source"],
                          row(rep, "SYNK.US")["country_review"]),
                         ("USA", "assumed", "earlier_country"))
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertIs(rep["filing_required"], False)
        earlier = [{"account": "ib", "country": "CYM",
                    "isin_country_code": "KY", "first": "2022-02-03",
                    "last": "2022-05-02"}]
        self.assertEqual(row(rep, "SYNK.US")["country_earlier"], earlier)
        self.assertEqual(rep["country_review"], [
            {"symbol": "SYNK.US", "reason": "earlier_country",
             "earlier": earlier,
             "lines": ["T1135 SYNK.US CYM", "T1135 SYNK.US USA"]}])
        brief, full = texts(rep)
        self.assertIn("! 1 to review for the country: SYNK.US (another "
                      "ISIN before) — tjs t1135 --details", brief)
        self.assertIn("SYNK.US: another holding of the ticker carried an "
                      "ISIN of CYM (ib, 2022) — add `T1135 SYNK.US CYM` or "
                      "`T1135 SYNK.US USA` to ticker.map", full)
        # A line settles it; an earlier ISIN of the same country is
        # nothing to name.
        rep = report(rows, lines=["T1135 SYNK.US CYM"])
        self.assertEqual(rep["country_review"], [])
        rows = [buy("SYNU.US", 5000.0, "2022-02-03", isin="US0000000001"),
                sell("SYNU.US", 5000.0, "2022-05-02", isin="US0000000001"),
                buy("SYNU.US", 7000.0, "2025-03-03", account="q")]
        self.assertEqual(report(rows)["country_review"], [])

    @rule(R.CA_RPT_21)
    def test_bought_in_one_account_and_sold_in_another_is_flat(self):
        # Taxable accounts carry no transfer between them: bought at
        # ib, moved, sold at q in 2021. The pool is flat, the holding
        # over; the ticker bought in 2025 is not learned from it.
        rows = [buy("SYNC.US", 5000.0, "2020-02-03", isin=CA),
                sell("SYNC.US", 5000.0, "2021-02-03", account="q"),
                buy("SYNC.US", COST, "2025-02-03", account="q")]
        rep = report(rows)
        self.assertEqual((row(rep, "SYNC.US")["country"],
                          row(rep, "SYNC.US")["country_source"],
                          row(rep, "SYNC.US")["country_review"]),
                         ("USA", "assumed", "earlier_holding"))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        self.assertNotIn("no T1135 required", texts(rep)[0])

        def day(t):
            return t["date"]
        self.assertEqual([(x.start, x.end) for x in
                          TI.Holdings(rows, day).by_symbol["SYNC.US"]],
                         [("2020-02-03", "2021-02-03"),
                          ("2025-02-03", None)])

    @rule(R.CA_RPT_21)
    def test_a_plain_split_does_not_end_the_holding(self):
        # 100 bought, split 2 for 1, 100 sold: 100 remain at ib. The
        # other broker's shares are the same security: the ISIN's.
        rows = [buy("SYNA.TO", 250000.0, "2020-02-03", isin=AU),
                split("SYNA.TO", 2, "2021-03-01", isin=AU),
                sell("SYNA.TO", 130000.0, "2022-04-01", 100, isin=AU),
                buy("SYNA.TO", 1000.0, "2023-05-01", 10, account="q")]
        rep = report(rows)
        self.assertEqual((row(rep, "SYNA.TO")["country"],
                          row(rep, "SYNA.TO")["country_source"],
                          row(rep, "SYNA.TO")["country_learned_accounts"]),
                         ("AUS", "isin", ["q"]))
        self.assertEqual(rep["max_total_cost"], 126000.0)
        self.assertIs(rep["filing_required"], True)
        def day(t):
            return t["date"]
        self.assertEqual([(x.start, x.end) for x in
                          TI.Holdings(rows, day).by_symbol["SYNA.TO"]],
                         [("2020-02-03", None)])

    @rule(R.CA_RPT_21)
    def test_a_full_sale_after_a_split_ends_it(self):
        # 100 bought, split 2 for 1, all 200 sold: the holding ended,
        # so the ticker bought in 2025 is not learned from it (and its
        # earlier ISIN, which says otherwise, is walked).
        rows = [buy("SYNC.US", 5000.0, "2022-02-03", isin=CA),
                split("SYNC.US", 2, "2022-06-01", isin=CA),
                sell("SYNC.US", 6000.0, "2023-04-03", 200, isin=CA),
                buy("SYNC.US", COST, "2025-02-03", account="q")]
        def day(t):
            return t["date"]
        self.assertEqual([(x.start, x.end) for x in
                          TI.Holdings(rows, day).by_symbol["SYNC.US"]],
                         [("2022-02-03", "2023-04-03"),
                          ("2025-02-03", None)])
        rep = report(rows)
        self.assertEqual((row(rep, "SYNC.US")["country"],
                          row(rep, "SYNC.US")["country_source"]),
                         ("USA", "assumed"))
        self.assertEqual(rep["classification_status"], "unresolved")
        # Each account's own split row scales its own units, never
        # another's, however far apart the two rows are.
        both = [buy("SYNC.US", 500.0, "2022-02-03"),
                buy("SYNC.US", 500.0, "2022-02-04", account="q"),
                split("SYNC.US", 2, "2022-06-01"),
                split("SYNC.US", 2, "2022-06-11", account="q"),
                sell("SYNC.US", 600.0, "2023-04-03", 200),
                sell("SYNC.US", 600.0, "2023-04-04", 200, account="q")]
        self.assertEqual([(x.start, x.end) for x in
                          TI.Holdings(both, day).by_symbol["SYNC.US"]],
                         [("2022-02-03", "2023-04-04")])
        # With one row for both (a split recorded once) the report
        # reads the engine's pool, which splits every account's units.
        once = [buy("SYNC.US", 500.0, "2022-02-03", isin=CA)] + both[1:3] \
            + both[4:] + [buy("SYNC.US", COST, "2025-02-03", account="q")]
        rep = report(once)
        self.assertEqual(row(rep, "SYNC.US")["country_source"], "assumed")
        self.assertEqual(row(rep, "SYNC.US")["country_review"],
                         "earlier_holding")

    @rule(R.CA_RPT_21)
    def test_a_registered_sale_with_no_purchase_takes_nothing_away(self):
        # The registered account's file begins after its purchase: its
        # sale of 100 is not ib's 100.
        tax = [buy("SYNA.TO", COST, "2020-02-03", isin=AU),
               buy("SYNA.TO", 1000.0, "2023-05-01", 10, account="q")]
        rrsp = [sell("SYNA.TO", 9000.0, "2021-06-01", account="rrsp")]
        rep = report(tax, sheltered=rrsp)
        self.assertEqual((row(rep, "SYNA.TO")["country"],
                          row(rep, "SYNA.TO")["country_source"]),
                         ("AUS", "isin"))
        self.assertEqual(rep["max_total_cost"], 126000.0)
        self.assertIs(rep["filing_required"], True)
        # Nor does it keep a holding open: ib sold out in 2021, and the
        # ticker bought at q in 2025 is not learned from ib's ISIN.
        tax = [buy("SYNC.US", 5000.0, "2020-02-03", isin=CA),
               sell("SYNC.US", 5000.0, "2021-02-03", isin=CA),
               buy("SYNC.US", COST, "2025-02-03", account="q")]
        rrsp = [sell("SYNC.US", 900.0, "2019-06-03", account="rrsp")]
        rep = report(tax, sheltered=rrsp)
        self.assertEqual((row(rep, "SYNC.US")["country"],
                          row(rep, "SYNC.US")["country_source"]),
                         ("USA", "assumed"))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        # A registered account is walked on its own: its purchase does
        # not cover a taxable account's sale.
        tax = [buy("SYNC.US", 5000.0, "2020-02-03", isin=CA),
               sell("SYNC.US", 5000.0, "2021-02-03", isin=CA),
               buy("SYNC.US", COST, "2025-02-03", account="q")]
        rrsp = [buy("SYNC.US", 900.0, "2019-06-03", account="rrsp"),
                sell("SYNC.US", 900.0, "2019-09-03", account="rrsp")]
        rep = report(tax, sheltered=rrsp)
        self.assertEqual(row(rep, "SYNC.US")["country_source"], "assumed")

    @rule(R.CA_RPT_21)
    def test_a_registered_accounts_isin_is_learned_from(self):
        # Its row is dated while the taxable account holds the shares.
        rep = report([buy("SYNK.US", 5000.0, "2024-02-05", account="q")],
                     sheltered=[buy("SYNK.US", 900.0, "2024-06-03",
                                    account="rrsp", isin=KY)])
        r = row(rep, "SYNK.US")
        self.assertEqual((r["country"], r["country_source"],
                          r["country_source_account"]),
                         ("CYM", "isin", "rrsp"))
        self.assertEqual(r["country_learned_accounts"], ["q"])
        # Only the taxable account's shares are property of the form.
        self.assertEqual(r["max_cost"], 5000.0)

    @rule(R.CA_RPT_21)
    def test_a_registered_account_alone_carries_nothing_over(self):
        # The rrsp bought 100 (a Canadian ISIN) in 2020; its file lacks
        # a 1 for 10 reverse split, so the 10 it sold in 2021 leave 90
        # in its books for good. The taxable account buys in 2025: a
        # new holding, the rrsp's ISIN another holding's, walked.
        tax = [buy("SYNC.US", COST, "2025-02-03", account="q")]
        stale = [buy("SYNC.US", 900.0, "2020-06-03", account="rrsp", isin=CA),
                 sell("SYNC.US", 900.0, "2021-06-03", 10, account="rrsp",
                      isin=CA)]
        whole = stale[:1] + [split("SYNC.US", 0.1, "2021-03-01",
                                   account="rrsp")] + stale[1:]
        # ... and the same where the rrsp holds the shares to this day.
        held = stale[:1]
        for name, rrsp in (("stale", stale), ("control", whole),
                           ("still held", held)):
            with self.subTest(rrsp=name):
                rep = report(tax, sheltered=rrsp)
                self.assertEqual((row(rep, "SYNC.US")["country"],
                                  row(rep, "SYNC.US")["country_source"],
                                  row(rep, "SYNC.US")["country_review"]),
                                 ("USA", "assumed", "earlier_holding"))
                self.assertEqual(rep["classification_status"], "unresolved")
                self.assertIsNone(rep["filing_required"])
                self.assertEqual((rep["max_total_cost_low"],
                                  rep["max_total_cost_high"]), (0.0, COST))
                self.assertIn("SYNC.US may not be foreign property (another "
                              "holding of the ticker carried an ISIN that "
                              "says otherwise: CAN (rrsp, ", texts(rep)[0])
        # A registered ISIN that agrees with the listing names nothing.
        rep = report(tax, sheltered=[buy("SYNC.US", 900.0, "2020-06-03",
                                         account="rrsp",
                                         isin="US0000000001")])
        self.assertEqual(rep["country_review"], [])
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(row(rep, "SYNC.US")["country_source"], "assumed")
        self.assertIs(rep["filing_required"], True)

    @rule(R.CA_RPT_21)
    def test_nothing_is_saved(self):
        # The books are all it reads: no file is written beside them.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": self.ROWS}))
            with contextlib.redirect_stderr(io.StringIO()):
                build_report([base], [], 2025, {}, "CAD", today="2026-06-01")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["margin_base.json"])

    @rule(R.CA_RPT_21)
    def test_a_holding_is_one_security_until_it_is_flat(self):
        def day(t):
            return t["date"]
        moved = [buy("SYNK.US", 5000.0, "2024-02-03", isin=KY),
                 _r("TRANSFER", "2024-05-01", "SYNK.US", -100, 0.0,
                    account="ib"),
                 _r("TRANSFER", "2024-05-03", "SYNK.US", 100, 0.0,
                    account="q")]
        h = TI.Holdings(moved, day)
        # A transfer out, then in: the same holding continues.
        self.assertEqual([(x.start, x.end) for x in h.by_symbol["SYNK.US"]],
                         [("2024-02-03", None)])
        self.assertEqual(h.by_symbol["SYNK.US"][0].present(2025), {"q"})
        # An arrival long after is another holding.
        late = moved[:2] + [_r("TRANSFER", "2024-09-03", "SYNK.US", 100, 0.0,
                               account="q")]
        self.assertEqual([(x.start, x.end)
                          for x in TI.Holdings(late, day).by_symbol["SYNK.US"]],
                         [("2024-02-03", "2024-05-01"),
                          ("2024-09-03", None)])
        # A sale and a purchase in one day never part it.
        same_day = [buy("SYNK.US", 5000.0, "2024-02-03"),
                    sell("SYNK.US", 5000.0, "2024-03-01"),
                    buy("SYNK.US", 5000.0, "2024-03-01", account="q")]
        self.assertEqual(len(TI.Holdings(same_day, day).by_symbol["SYNK.US"]),
                         1)


class TestATickerChange(unittest.TestCase):
    """TJ-4964, TJ-5048, TJ-5049: a T1135 line on any symbol of a
    renamed security decides it before and after the change, and
    outranks tobase.map on every symbol of it; it does not cross a
    merger, nor reach a ticker bought again."""

    # Most of it sold before the change (TJ-4964 (a), TJ-5048).
    SOLD = [buy("OLD.TO", COST), sell("OLD.TO", 112500.0, "2025-03-03", 90),
            ren("OLD.TO", "NEW.TO", "2025-06-02")]

    @rule(R.CA_RPT_21)
    def test_a_line_on_the_later_symbol_reaches_back(self):
        rep = report(self.SOLD, lines=["T1135 NEW.TO AUS"])
        # The whole-chain figure, not the 12,500 left at the change.
        self.assertEqual(rep["max_total_cost"], COST)
        self.assertIs(rep["filing_required"], True)
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual((row(rep, "NEW.TO")["country"],
                          row(rep, "NEW.TO")["country_line"]),
                         ("AUS", "T1135 NEW.TO AUS"))
        # The same as the line on the earlier symbol gives.
        old = report(self.SOLD, lines=["T1135 OLD.TO AUS"])
        self.assertEqual(old["max_total_cost"], COST)
        self.assertEqual(row(old, "NEW.TO")["country_line"],
                         "T1135 OLD.TO AUS")
        # With no line, both symbols are assumed from their listing.
        none = report(self.SOLD)
        self.assertEqual(none["max_total_cost"], 0.0)
        self.assertEqual(none["assumed_symbols"], ["NEW.TO"])

    @rule(R.CA_RPT_21)
    def test_the_line_on_the_new_ticker_is_never_called_unused(self):
        # Nothing traded since the change: NEW.TO is only a row's
        # symbol_new, and the line the report names is on it.
        rows = [buy("OLD.TO", 60000.0, isin=AU),
                buy("OLD.TO", 65000.0, "2025-02-04", isin=CA),
                ren("OLD.TO", "NEW.TO", "2025-06-03")]
        named = report(rows)["classification_conflicts"][0]["lines"]
        self.assertEqual(named["foreign"], ["T1135 NEW.TO AUS"])
        rep = report(rows, lines=named["foreign"])
        self.assertEqual(rep["unused_overrides"], [])
        self.assertNotIn("matches no symbol", rep["_stderr"])
        self.assertEqual(rep["max_total_cost"], COST)
        # A line that names nothing in the books still is.
        rep = report(rows, lines=["T1135 NOPE.TO AUS"])
        self.assertEqual(rep["unused_overrides"], ["NOPE.TO"])

    @rule(R.CA_RPT_21)
    def test_each_symbols_own_line_decides_that_symbol(self):
        rep = report(self.SOLD, lines=["T1135 OLD.TO CA",
                                       "T1135 NEW.TO AUS"])
        # Not foreign property until the change: 12,500 after it. pii-ok
        self.assertEqual(rep["max_total_cost"], 12500.0)

    @rule(R.CA_RPT_21)
    def test_a_line_outranks_tobase_map_on_every_symbol(self):
        # TJ-4964 (c), TJ-5049: OLD.TO, its rows' ISIN Australian,
        # renamed NEWW.US, whose tobase.map issuer country is Bermuda.
        rows = [buy("OLD.TO", COST),
                _r("DIVIDEND", "2025-03-01", "OLD.TO", 0, 20.0,
                   type="dividend", issuer_country="AU", account="ib"),
                ren("OLD.TO", "NEWW.US", "2025-06-02")]
        tob = {"NEWW.US": "BMU"}
        rep = report(rows, lines=["T1135 OLD.TO CA"], issuer_countries=tob)
        self.assertEqual(rep["max_total_cost"], 0.0)
        self.assertEqual(rep["properties"], [])
        self.assertIs(rep["filing_required"], False)
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(decision(rep, "NEWW.US")["country_line"],
                         "T1135 OLD.TO CA")
        # With no line the two sources of one rank disagree: review.
        rep = report(rows, issuer_countries=tob)
        self.assertEqual(row(rep, "NEWW.US")["country"], "??")
        self.assertEqual(rep["classification_conflicts"][0]["countries"],
                         ["AUS", "BMU"])
        self.assertEqual(rep["max_total_cost"], COST)

    @rule(R.CA_RPT_21)
    def test_an_isin_follows_the_security_through_its_change(self):
        rows = [buy("OLD.TO", 5000.0, isin=AU),
                ren("OLD.TO", "NEW.TO", "2025-06-02")]
        rep = report(rows)
        self.assertEqual((row(rep, "NEW.TO")["country"],
                          row(rep, "NEW.TO")["country_source"]),
                         ("AUS", "isin"))
        self.assertEqual(rep["max_total_cost"], 5000.0)

    @rule(R.CA_RPT_21)
    def test_a_line_does_not_cross_a_merger(self):
        # TJ-4964 (b): OLD.TO merges into PRT.US, already held.
        rows = [buy("OLD.TO", 60000.0), buy("PRT.US", 70000.0, "2025-02-04"),
                ren("OLD.TO", "PRT.US", "2025-06-02")]
        rep = report(rows, lines=["T1135 OLD.TO CA"])
        self.assertEqual((row(rep, "PRT.US")["country"],
                          row(rep, "PRT.US")["country_source"]),
                         ("USA", "assumed"))
        # OLD.TO's cost joins the partner's pool at the merger.
        self.assertEqual(rep["max_total_cost"], 130000.0)
        self.assertEqual(rep["max_total_date"], "2025-06-02")
        # Nor an ISIN: the partner's shares are another security.
        rep = report([buy("OLD.TO", 600.0, isin=AU),
                      buy("PRT.US", 700.0, "2025-02-04"),
                      ren("OLD.TO", "PRT.US", "2025-06-02")])
        self.assertEqual(row(rep, "PRT.US")["country_source"], "assumed")
        # The partner's own line is the one it needs.
        rep = report(rows, lines=["T1135 OLD.TO CA", "T1135 PRT.US CA"])
        self.assertEqual(rep["max_total_cost"], 0.0)

    @rule(R.CA_RPT_21)
    def test_a_ticker_bought_again_is_another_security(self):
        rows = [buy("OLD.TO", 60000.0, "2024-02-03"),
                ren("OLD.TO", "NEW.TO", "2024-06-02"),
                buy("OLD.TO", 70000.0, "2025-03-03")]
        rep = report(rows, lines=["T1135 NEW.TO AUS"])
        # The line on NEW.TO reaches its own earlier OLD.TO (2024) ...
        self.assertEqual(report(rows, lines=["T1135 NEW.TO AUS"],
                                year=2024)["max_total_cost"], 60000.0)
        # ... not the OLD.TO bought again in 2025.
        self.assertEqual(rep["max_total_cost"], 60000.0)
        self.assertEqual([r["symbol"] for r in rep["properties"]],
                         ["NEW.TO"])
        self.assertEqual(decision(rep, "OLD.TO")["country_source"],
                         "assumed")
        # Its own line is the one it needs.
        rep = report(rows, lines=["T1135 NEW.TO AUS", "T1135 OLD.TO USA"])
        self.assertEqual(rep["max_total_cost"], 130000.0)

    @rule(R.CA_RPT_21)
    def test_two_isins_across_a_change_are_two_securities(self):
        def day(t):
            return t["date"]
        rows = [buy("OLD.TO", 600.0, isin=AU),
                ren("OLD.TO", "NEW.TO", "2025-06-02"),
                buy("NEW.TO", 100.0, "2025-07-02", 10, isin=CA)]
        h = TI.Holdings(rows, day)
        old, new = h.by_symbol["OLD.TO"][0], h.by_symbol["NEW.TO"][0]
        self.assertNotEqual(h.security(old), h.security(new))
        rep = report(rows, lines=["T1135 OLD.TO GBR"])
        # NEW.TO keeps its own ISIN's answer: the line does not cross.
        self.assertEqual(decision(rep, "NEW.TO")["country_source"], "isin")
        self.assertEqual(decision(rep, "NEW.TO")["country"], "CAN")
        # The same ISIN on both sides is one security, a merger too.
        rows[2] = buy("NEW.TO", 100.0, "2025-07-02", 10, isin=AU)
        h = TI.Holdings(rows, day)
        self.assertEqual(h.security(h.by_symbol["OLD.TO"][0]),
                         h.security(h.by_symbol["NEW.TO"][0]))


class TestTheJsonKeys(unittest.TestCase):
    NEW = ("country_decisions", "assumed_symbols", "assumed_foreign_cost",
           "assumed_not_foreign_cost", "listing_overruled_symbols",
           "country_review", "country_review_symbols")

    def doc(self, rows):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": rows}))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = main([str(base), "--year", "2025", "--json"])
        self.assertEqual(rc, 0, err.getvalue())
        return json.loads(out.getvalue())

    @rule(R.CA_RPT_02)
    def test_the_document_carries_what_decided_each_country(self):
        doc = self.doc(BOOK + [buy("SYNR.US", 400.0, isin="US0000000001",
                                   security_type="ADR")])
        self.assertEqual(doc["schema_version"], 2)
        for k in self.NEW:
            self.assertIn(k, doc)
        self.assertEqual({r["symbol"]: r["country_source"]
                          for r in doc["properties"]},
                         {"SYNA.TO": "isin", "SYNK.US": "isin",
                          "SYNN.US": "assumed", "SYNR.US": "isin"})
        self.assertEqual(doc["assumed_symbols"], ["SYNN.US", "SYNT.TO"])
        self.assertEqual(doc["country_review_symbols"], ["SYNR.US"])
        for e in doc["country_decisions"]:
            self.assertLessEqual(
                {"symbol", "country", "foreign_property", "country_source",
                 "listing_country", "max_cost", "year_end_cost"}, set(e))
        # Schema 2: the keys of the interim checks are gone (no symbol
        # is counted against its ISIN any more).
        for k in ("canadian_issuer_symbols", "foreign_issuer_symbols",
                  "foreign_issuer_countries", "deferred_wash_not_in_cost",
                  "full_history_wash"):
            self.assertNotIn(k, doc)

    @rule(R.CA_RPT_02)
    def test_the_keys_are_documented(self):
        page = (_paths.REPO / "docs" / "commands.md").read_text(
            encoding="utf-8")
        for k in self.NEW + ("country_source", "country_source_account",
                             "country_learned_accounts", "isin_country_code",
                             "country_line", "country_review"):
            self.assertIn(f"`{k}`", page, k)
        for value in ("override", "isin", "tobase_map", "learned",
                      "assumed", "listing", "custodian", "cash", "crypto",
                      "unclassified"):
            self.assertIn(f"`{value}`", page, value)

    @rule(R.CA_RPT_02)
    def test_every_row_says_its_source(self):
        cash = {"series": [{"currency": "USD", "account": "ib",
                            "country": "USA",
                            "points": [["2025-01-10", 400.0, 500.0]],
                            "unknown": False, "why": ""}]}
        rep = report(BOOK + [buy("SYNT.TO", 100.0, "2025-02-06", 10,
                                 account="abroad"),
                             _r("BUYSELL", "2025-02-07", "SYNCOIN", 1,
                                -50.0, account="ib"),
                             buy("SYNQ.ZZ", 60.0)],
                     cash=cash, custody={"abroad": "USA"})
        got = {(r["symbol"], r.get("custody_account")): r["country_source"]
               for r in rep["properties"]}
        self.assertEqual(got[("USD cash", None)], "cash")
        self.assertEqual(got[("SYNT.TO", "abroad")], "custodian")
        self.assertEqual(got[("SYNCOIN", None)], "crypto")
        self.assertEqual(got[("SYNQ.ZZ", None)], "unclassified")
        self.assertEqual({v for v in got.values()},
                         {"cash", "custodian", "crypto", "unclassified",
                          "isin", "assumed"})


class TestTheChecklistStep(unittest.TestCase):
    def step(self, rep):
        from taxjson.lib import checklist as CL
        ctx = mock.Mock()
        ctx.settings = Settings.from_table({"country": "canada"})
        ctx.year = 2025
        ctx.today = date(2026, 6, 1)
        ctx.sub.return_value = (0, json.dumps(
            {k: v for k, v in rep.items() if not k.startswith("_")}), "")
        return CL.d_t1135(ctx)

    @rule(R.CA_RPT_02)
    def test_assumed_countries_are_counted_and_never_attention(self):
        res = self.step(report(BOOK))
        self.assertEqual(res.status, "done")
        self.assertIn("; 2 countries assumed from the listing (`tjs t1135 "
                      "--details`)", res.detail)
        res = self.step(report(BOOK + [buy("SYNU.US", COST)]))
        self.assertEqual(res.status, "manual")
        self.assertIn("; 3 countries assumed from the listing", res.detail)
        # With an ISIN for each, nothing is said.
        res = self.step(report(BOOK[:3]))
        self.assertEqual(res.status, "done")
        self.assertNotIn("assumed", res.detail)

    @rule(R.CA_RPT_02)
    def test_over_the_threshold_filing_comes_first(self):
        rows = [buy("SYNR.US", 400.0, isin="US0000000001",
                    security_type="ADR"), buy("SYNU.US", COST)]
        res = self.step(report(rows))
        self.assertEqual(res.status, "manual")
        self.assertIn("file the T1135 (`tjs t1135` for the tables); 1 "
                      "security to review for the country first (`tjs "
                      "t1135 --details`)", res.detail)

    @rule(R.CA_RPT_02)
    def test_a_receipt_needs_attention_until_its_line(self):
        rows = [buy("SYNR.US", 400.0, isin="US0000000001",
                    security_type="ADR")]
        res = self.step(report(rows))
        self.assertEqual(res.status, "attention")
        self.assertIn("1 security to review for the T1135's country: "
                      "SYNR.US", res.detail)
        self.assertEqual(self.step(report(
            rows, lines=["T1135 SYNR.US USA"])).status, "done")


class TestTheIbStatement(unittest.TestCase):
    """The reader stamps each share row with the ISIN its statement's
    Financial Instrument Information section gives the instrument."""

    ACCT = "U1234567"

    def parsed(self, fii):
        body = "".join(
            _trade(sym, "2025-02-03, 10:00:00", 100, 10, -1000,
                   acct=self.ACCT, cur=cur)
            for sym, cur in (("SYNK", "USD"), ("SYNA", "CAD"),
                             ("SYNC", "USD"), ("SYNP", "USD"),
                             ("SYNR", "USD")))
        _, txs, _ = _parse_ib(HEAD + TRADES_H + body + FII_H + fii)
        return {t["symbol"]: t for t in txs}

    @rule(R.CA_RPT_02)
    def test_every_instrument_row_carries_its_isin(self):
        adr = ('Financial Instrument Information,Data,Stocks,"SYNR",'
               'SYNR HOLDING,990000005,US0000000001,,NYSE,1,,,ADR,,\n')
        got = self.parsed(
            _fii_stock("SYNK", KY, "990000001")
            + _fii_stock("SYNA", AU, "990000002", "TSE")
            + _fii_stock("SYNC", CA, "990000003")
            # A nine-character CUSIP is no ISIN: it names no country.
            + _fii_stock("SYNP", "999000104", "990000004") + adr)
        # No dividend was paid on any of them.
        self.assertEqual({s: t.get("isin") for s, t in got.items()},
                         {"SYNK.US": KY, "SYNA.TO": AU, "SYNC.US": CA,
                          "SYNP.US": None, "SYNR.US": "US0000000001"})
        self.assertEqual({s: t.get("security_type") for s, t in got.items()
                          if t.get("security_type")}, {"SYNR.US": "ADR"})
        # The issuer's country on a trade row stays the dividends' fact.
        self.assertEqual([t.get("issuer_country") for t in got.values()],
                         [None] * 5)
        # ... and the report reads them: the proposal's three answers.
        rows = [dict(t, account="ib", currency="CAD") for t in got.values()]
        rep = report(rows)
        self.assertEqual({e["symbol"]: (e["country"], e["country_source"])
                          for e in rep["country_decisions"]},
                         {"SYNK.US": ("CYM", "isin"),
                          "SYNA.TO": ("AUS", "isin"),
                          "SYNC.US": ("CAN", "isin"),
                          "SYNP.US": ("USA", "assumed"),
                          "SYNR.US": ("USA", "isin")})

    def stamps(self, fii, trades):
        body = "".join(_trade(sym, "2025-02-03, 10:00:00", 100, 10, -1000,
                              acct=self.ACCT, cur=cur)
                       for sym, cur in trades)
        _, txs, _ = _parse_ib(HEAD + TRADES_H + body + FII_H + fii)
        return {t["symbol"]: t.get("isin") for t in txs}

    @rule(R.CA_RPT_02)
    def test_an_instrument_of_another_market_is_not_the_rows(self):
        both = [("SYNT", "USD"), ("SYNT", "CAD")]
        blank = ('Financial Instrument Information,Data,Stocks,"SYNT",'
                 'SYNT CORP,{},,,{},1,,,COMMON,,\n')
        # Only the TSX instrument carries an ISIN (or is listed at all):
        # the US listing's rows take none of it.
        for fii in (_fii_stock("SYNT", CA, "990000001", "TSE")
                    + blank.format("990000002", "NYSE"),
                    _fii_stock("SYNT", CA, "990000001", "TSE")):
            self.assertEqual(self.stamps(fii, both),
                             {"SYNT.US": None, "SYNT.TO": CA})
        # The mirror.
        fii = blank.format("990000001", "TSE") \
            + _fii_stock("SYNT", "US0000000001", "990000002", "NYSE")
        self.assertEqual(self.stamps(fii, both),
                         {"SYNT.US": "US0000000001", "SYNT.TO": None})
        # Each on its own market.
        fii = _fii_stock("SYNT", CA, "990000001", "TSE") \
            + _fii_stock("SYNT", "US0000000001", "990000002", "NYSE")
        self.assertEqual(self.stamps(fii, both),
                         {"SYNT.US": "US0000000001", "SYNT.TO": CA})

    @rule(R.CA_RPT_02)
    def test_two_isins_of_one_ticker_on_one_market(self):
        # An ISIN that changed inside the statement: both are stamped,
        # and the report reads each.
        fii = _fii_stock("SYNT", "US0000000001", "990000001") \
            + _fii_stock("SYNT", "US0000000002", "990000002")
        body = _trade("SYNT", "2025-02-03, 10:00:00", 100, 10, -1000,
                      acct=self.ACCT, cur="USD")
        _, txs, _ = _parse_ib(HEAD + TRADES_H + body + FII_H + fii)
        # `isin` is exactly one ISIN: with two it is left out.
        self.assertEqual([(t.get("isin"), t.get("isins")) for t in txs],
                         [(None, ["US0000000001", "US0000000002"])])
        # Two of one country decide by it.
        rep = report([buy("SYNT.US", 800.0,
                          isins=["US0000000001", "US0000000002"])])
        self.assertEqual((row(rep, "SYNT.US")["country"],
                          row(rep, "SYNT.US")["country_source"]),
                         ("USA", "isin"))
        # Two that disagree on being foreign property: both ways.
        rep = report([buy("SYNT.US", COST, isins=[CA, "US0000000002"])])
        self.assertEqual(row(rep, "SYNT.US")["country"], "??")
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_conflicts"][0]["countries"],
                         ["CAN", "USA"])

    @rule(R.CA_RPT_02)
    def test_a_row_with_no_instrument_line_carries_none(self):
        got = self.parsed(_fii_stock("SYNK", KY, "990000001"))
        self.assertEqual(got["SYNK.US"].get("isin"), KY)
        self.assertNotIn("isin", got["SYNA.TO"])


if __name__ == "__main__":
    unittest.main()
