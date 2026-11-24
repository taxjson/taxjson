"""`tjs t1135` while a holding's being foreign property is open (backlog
TJ-4956, TJ-4957, TJ-4963).

A share's country is decided in order (a T1135 line, the ISIN, the same
security's ISIN in another account, the listing: tests/rules/ca/rpt/
test_t1135_country_by_issuer.py). An ISIN that disagrees with the
listing decides, so that is no longer open. What is: a share whose
sources of one rank disagree (two ISINs in the year, an ISIN and
tobase.map), or whose ISIN's prefix is no country's, counted as foreign
property under `??`; and a listed option whose own listing and whose
share disagree, counted by its listing. While counting such a holding
the other way would move the total across the threshold the test is
open (`--json` `classification_status` "unresolved", `filing_required`
null, the text NOT RESOLVED with the ticker.map lines that settle it),
in either direction; the report says the result is the same either way
when it is; and a holding foreign under each reading (the total cannot
move) is a warning and a symbol to review.

Every fixture is SYNTHETIC: invented tickers (FGN, OLD, NEW, QZ*), an
invented account name.
"""
import contextlib
import io
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.bin import taxjson_t1135 as T
from taxjson.bin.taxjson_t1135 import (build_report, main, render_brief,
                                       render_report, verdict_keys)
from taxjson.project.settings import Settings

from _support.ca_t1135_walk import _r

COST = 125000.0             # over the default threshold on its own
SMALL = 900.0               # far below it
THR = f"{100000:,.2f} CAD"


def buy(symbol, cost, day="2025-02-03", qty=100):
    return _r("BUYSELL", day, symbol, qty, -cost)


def sell(symbol, proceeds, day, qty=100):
    return _r("BUYSELL", day, symbol, -qty, proceeds)


def div(symbol, issuer, day="2025-06-17"):
    """A dividend whose row carries the issuer's country as the ISIN
    gives it."""
    return _r("DIVIDEND", day, symbol, 0, 20.0, type="dividend",
              issuer_country=issuer)


def ren(old, new, day):
    """A ticker change."""
    return _r("SPLIT", day, old, 1, 0.0, symbol_new=new)


def unknown(symbol, day="2024-01-02"):
    """A holding whose cost the books do not give."""
    return _r("OPENING_BALANCE", day, symbol, 100, 0.0)


def book(symbol, cost, issuer, more=()):
    return [buy(symbol, cost), div(symbol, issuer), *more]


def torn(symbol, cost, more=(), one="AU", other="CA"):
    """A holding two of the year's rows give two countries for (an
    Australian ISIN and a Canadian one): nothing may be picked."""
    return [buy(symbol, cost), div(symbol, one),
            div(symbol, other, "2025-09-17"), *more]


OPT = "FGN250620C00150000.TO"       # a Montreal-listed call on FGN.TO
OPU = "QZC250620C00150000.US"       # a US-listed call on QZC.US


def opt(symbol, cost, day="2025-02-05"):
    """One long contract bought for `cost`."""
    return _r("BUYSELL", day, symbol, 1, -cost)


def cash_doc(cost, unknown_cost=False):
    """A cash document (lib/t1135_cash.cash_doc's shape): the account's
    US dollars held outside Canada."""
    return {"series": [{"currency": "USD", "account": "margin",
                        "country": "USA",
                        "points": [["2025-01-10", 400.0, cost]],
                        "unknown": unknown_cost,
                        "why": "no opening" if unknown_cost else ""}]}


def report(rows, overrides=None, **kw):
    """build_report over one base file (no gains file: the income
    columns are not this module's); its warnings in `_stderr`."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "margin_base.json"
        base.write_text(json.dumps({"transactions": rows}))
        kw.setdefault("today", "2026-06-01")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                mock.patch.object(T, "_DETAILS", True):
            rep = build_report([base], [], 2025, dict(overrides or {}),
                               "CAD", **kw)
    rep["_stderr"] = " ".join(err.getvalue().split())
    return rep


def texts(rep):
    """(the default view, the --details view), each on one line."""
    return (" ".join(render_brief(rep).split()),
            " ".join(render_report(rep).split()))


def money(v):
    return f"{v:,.2f}"


def conflict(rep, symbol):
    return next(c for c in rep["classification_conflicts"]
                if c["symbol"] == symbol)


def as_overrides(lines):
    """ticker.map T1135 lines as build_report's overrides, read by the
    ticker.map parser itself (a line it refuses fails the test)."""
    with tempfile.TemporaryDirectory() as td:
        m = Path(td) / "ticker.map"
        m.write_text("".join(f"{ln}\n" for ln in lines))
        return T.load_overrides(m)


def lines_named(text):
    """The `T1135 ...` lines a text tells the reader to add: those that
    are lines, not the shape of one (SYMBOL, COUNTRY)."""
    return [ln for ln in re.findall(r"`(T1135 [^`]+)`", text)
            if "SYMBOL" not in ln.split() and "COUNTRY" not in ln.split()]


class TestAShareItsSourcesDisagreeOn(unittest.TestCase):
    """FGN.TO, an Australian ISIN on one row of the year and a Canadian
    one on another: counted as foreign under `??`, the other reading
    walked."""

    @rule(R.CA_RPT_02)
    def test_a_cost_over_the_threshold_leaves_the_test_open(self):
        rep = report(torn("FGN.TO", COST))
        # Counted, the cautious way, under no country.
        self.assertEqual(rep["max_total_cost"], COST)
        self.assertIs(rep["over_threshold_as_counted"], True)
        self.assertEqual([(r["symbol"], r["country"], r["country_review"])
                          for r in rep["properties"]],
                         [("FGN.TO", "??", "sources_disagree")])
        # ... and no key says that is a verdict.
        self.assertIsNone(rep["filing_required"])
        self.assertIsNone(rep["simplified_method_available"])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_unresolved_symbols"],
                         ["FGN.TO"])
        self.assertEqual(rep["classification_unknown_cost_symbols"], [])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"],
                          rep["max_total_cost_high_at_least"]),
                         (0.0, COST, COST))
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "FGN.TO", "kind": "sources_disagree",
             "counted_as": "??", "issuer_country": None,
             "isin_country_code": None, "countries": ["AUS", "CAN"],
             "affects": "total", "held_in_year": True,
             "symbols": {"FGN.TO": "??"},
             "lines": {"foreign": ["T1135 FGN.TO AUS"],
                       "not_foreign": ["T1135 FGN.TO CA"]}}])
        self.assertEqual(rep["country_review_symbols"], ["FGN.TO"])
        self.assertEqual(rep["review_symbols"], ["FGN.TO"])
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertNotIn("FILING REQUIRED", text)
            self.assertIn(f"=> NOT RESOLVED: over the {THR} threshold as "
                          "counted, but FGN.TO may not be foreign property "
                          "(its sources disagree: AUS, CAN): without it "
                          "the maximum is 0.00 CAD", text)
            self.assertIn("Add `T1135 FGN.TO CA` (not foreign property) or "
                          "`T1135 FGN.TO AUS` (foreign property) to "
                          "ticker.map, then `tjs run` and `tjs t1135`.",
                          text)
            self.assertRegex(text, r"FGN\.TO \?\? review 2 ")
        self.assertIn("1 security to review for the T1135's country: "
                      "FGN.TO (its sources disagree: AUS, CAN)",
                      rep["_stderr"])

    @rule(R.CA_RPT_02)
    def test_the_lines_named_give_the_figures_named(self):
        rows = torn("FGN.TO", COST)
        open_ = report(rows)
        lines = conflict(open_, "FGN.TO")["lines"]
        rep = report(rows, as_overrides(lines["foreign"]))
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["country_review"], [])
        self.assertEqual(rep["max_total_cost"],
                         open_["max_total_cost_high"])
        self.assertIs(rep["filing_required"], True)
        self.assertIs(rep["simplified_method_available"], True)
        self.assertEqual(rep["properties"][0]["country"], "AUS")
        self.assertEqual(rep["properties"][0]["country_source"],
                         "override")
        self.assertIn("FILING REQUIRED", texts(rep)[0])
        rep = report(rows, as_overrides(lines["not_foreign"]))
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["max_total_cost"], open_["max_total_cost_low"])
        self.assertIs(rep["filing_required"], False)
        self.assertIsNone(rep["simplified_method_available"])
        brief = texts(rep)[0]
        self.assertIn("no T1135 required", brief)
        self.assertNotIn("NOT RESOLVED", brief)

    @rule(R.CA_RPT_02)
    def test_a_cost_far_below_cannot_change_the_verdict(self):
        rep = report(torn("FGN.TO", SMALL))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertEqual(rep["classification_unresolved_symbols"], [])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, SMALL))
        self.assertIs(rep["filing_required"], False)
        for text in texts(rep):
            self.assertIn("no T1135 required", text)
            self.assertIn("Counting FGN.TO the other way (its sources "
                          "disagree) does not change this (the maximum is "
                          f"0.00 to {money(SMALL)} CAD).", text)

    @rule(R.CA_RPT_02)
    def test_already_required_holds_either_way(self):
        rep = report(torn("FGN.TO", SMALL,
                          more=[buy("QZX.US", COST, "2025-02-04")]))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], True)
        self.assertIs(rep["simplified_method_available"], True)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (COST, COST + SMALL))
        brief = texts(rep)[0]
        self.assertIn("FILING REQUIRED", brief)
        self.assertIn("simplified method (Part A)", brief)
        self.assertIn("does not change this", brief)

    @rule(R.CA_RPT_15)
    def test_the_part_that_depends_on_it_is_open(self):
        # 250,400 as counted (Part B); without FGN.TO, under 250,000. pii-ok
        rep = report(torn("FGN.TO", SMALL,
                          more=[buy("QZX.US", 249500.0, "2025-02-04")]))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], True)
        self.assertIsNone(rep["simplified_method_available"])
        for text in texts(rep):
            self.assertIn("Part A or Part B open (it depends on how FGN.TO "
                          "is counted", text)
            self.assertNotIn("Detailed method (Part B) required", text)
            self.assertNotIn("detailed method (Part B)", text)

    @rule(R.CA_RPT_02)
    def test_the_sum_with_other_property_decides(self):
        # Neither alone is over; together they are.
        rep = report(torn("FGN.TO", 60000.0,
                          more=[buy("QZX.US", 60000.0, "2025-02-04")]))
        self.assertIsNone(rep["filing_required"])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (60000.0, 120000.0))

    @rule(R.CA_RPT_02)
    def test_more_symbols_than_the_default_view_names(self):
        rows = (torn("FGN.TO", 40000.0) + torn("QZA.TO", 40000.0, one="US")
                + torn("QZB.TO", 40000.0, one="IE"))
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "unresolved")
        brief, full = texts(rep)
        self.assertIn("FGN.TO, QZA.TO +1 more may not be foreign property",
                      brief)
        self.assertIn("without them the maximum is 0.00 CAD", brief)
        self.assertIn("; and the lines of 1 more (tjs t1135 --details) to "
                      "ticker.map", brief)
        self.assertNotIn("T1135 QZB.TO", brief.split("! ")[0])
        for ln in ("`T1135 FGN.TO AUS`", "`T1135 QZA.TO USA`",
                   "`T1135 QZB.TO IRL`", "`T1135 QZB.TO CA`"):
            self.assertIn(ln, full)
        # Every line named, together, gives the figure named.
        every = [x for c in rep["classification_conflicts"]
                 for x in c["lines"]["not_foreign"]]
        self.assertEqual(report(rows, as_overrides(every))["max_total_cost"],
                         rep["max_total_cost_low"])

    @rule(R.CA_RPT_02)
    def test_a_holding_sold_before_the_year_decides_nothing(self):
        rows = [buy("FGN.TO", COST, "2023-02-03"),
                div("FGN.TO", "AU", "2023-06-17"),
                div("FGN.TO", "CA", "2023-07-17"),
                sell("FGN.TO", COST, "2023-09-01")]
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["country_review"], [])
        self.assertIs(rep["filing_required"], False)
        for text in texts(rep):
            self.assertIn("no T1135 required", text)
            self.assertNotIn("the other way", text)

    @rule(R.CA_RPT_19)
    def test_a_holding_only_short_decides_nothing(self):
        rep = report([sell("FGN.TO", COST, "2025-02-03"),
                      div("FGN.TO", "AU"), div("FGN.TO", "CA", "2025-09-17")])
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertIs(conflict(rep, "FGN.TO")["held_in_year"], False)

    @rule(R.CA_RPT_20)
    def test_with_a_cash_row_it_is_the_sum_that_decides(self):
        rep = report(torn("FGN.TO", 60000.0), cash=cash_doc(60000.0))
        self.assertEqual(rep["max_total_cost"], 120000.0)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_low"], 60000.0)
        self.assertIn("=> NOT RESOLVED: over", texts(rep)[0])

    @rule(R.CA_RPT_20)
    def test_cash_of_unknown_cost_is_said_beside_it(self):
        rep = report(torn("FGN.TO", COST),
                     cash=cash_doc(0.0, unknown_cost=True))
        self.assertIs(rep["total_settled"], False)
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertIn(f"=> NOT RESOLVED: over the {THR} threshold as "
                          "counted, but FGN.TO may not be foreign property "
                          "(its sources disagree: AUS, CAN): without it "
                          "the maximum is at least 0.00 CAD; NOT SETTLED "
                          "either: some cash's cost is unknown — tjs "
                          "fx-cash --ledger v2", text)
            self.assertIn("Add `T1135 FGN.TO CA`", text)
            self.assertNotIn("FILING REQUIRED", text)

    @rule(R.CA_RPT_13)
    def test_an_open_year_keeps_its_so_far(self):
        rep = report(torn("FGN.TO", COST), today="2025-09-01")
        self.assertIs(rep["year_complete"], False)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertIn(f"=> NOT RESOLVED: over the {THR} threshold as "
                          "counted, but FGN.TO may not be foreign property",
                          text)
            self.assertIn("without it the maximum so far is 0.00 CAD", text)
            self.assertIn("then `tjs run` and `tjs t1135`.", text)

    @rule(R.CA_RPT_02)
    def test_a_custodian_abroad_counts_it_either_way(self):
        # Held with a custodian outside Canada it is foreign property
        # under either reading: the same total.
        rep = report(torn("FGN.TO", COST), custody={"margin": "USA"})
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (COST, COST))
        self.assertIs(rep["filing_required"], True)
        self.assertIn(f"does not change this (the maximum stays "
                      f"{money(COST)} CAD).", texts(rep)[0])

    @rule(R.CA_RPT_12)
    def test_a_cost_the_books_do_not_give_is_not_settled(self):
        rep = report([unknown("FGN.TO"), div("FGN.TO", "AU"),
                      div("FGN.TO", "CA", "2025-09-17")])
        self.assertEqual(rep["unknown_acb_symbols"], ["FGN.TO"])
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertIn(f"=> NOT SETTLED: below the {THR} threshold",
                          text)
            self.assertIn("the cost of FGN.TO is unknown", text)

    @rule(R.CA_RPT_02)
    def test_an_isin_and_tobase_map_that_disagree(self):
        # tobase.map says Bermuda, the rows' ISIN Ireland: foreign
        # under each, so the total stands and the country is open.
        rep = report(book("FGN.US", SMALL, "IE"),
                     issuer_countries={"FGN.US": "BMU"})
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "FGN.US", "kind": "sources_disagree",
             "counted_as": "??", "issuer_country": None,
             "isin_country_code": None, "countries": ["BMU", "IRL"],
             "affects": "country", "held_in_year": True,
             "lines": {"choices": ["T1135 FGN.US BMU",
                                   "T1135 FGN.US IRL"]}}])
        self.assertEqual(rep["properties"][0]["country"], "??")
        self.assertEqual(rep["max_total_cost"], SMALL)
        self.assertIs(rep["filing_required"], False)
        self.assertIn("! 1 to review for the country: FGN.US (its sources "
                      "disagree: BMU, IRL)", texts(rep)[0])
        self.assertIn("add `T1135 FGN.US BMU` or `T1135 FGN.US IRL` to "
                      "ticker.map", texts(rep)[1])
        # Either line settles it.
        rep = report(book("FGN.US", SMALL, "IE"),
                     as_overrides(["T1135 FGN.US IRL"]),
                     issuer_countries={"FGN.US": "BMU"})
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["properties"][0]["country"], "IRL")

    @rule(R.CA_RPT_02)
    def test_sources_that_agree_are_no_conflict(self):
        rep = report(book("FGN.US", SMALL, "BM"),
                     issuer_countries={"FGN.US": "BMU"})
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual((rep["properties"][0]["country"],
                          rep["properties"][0]["country_source"]),
                         ("BMU", "isin"))


class TestAnOptionAndItsShare(unittest.TestCase):
    """TJ-4963: an option keeps its own listing's country; where that
    and its share's classification disagree on being foreign property
    the test is worked out with the option counted each way."""

    # FGN.TO is Australian by its ISIN; its call is listed in Montreal.
    UP = [buy("FGN.TO", 60000.0), div("FGN.TO", "AU"), opt(OPT, 50000.0)]
    # QZC.US is Canadian by its ISIN; its call is listed in the US.
    DOWN = [buy("QZC.US", 1000.0), div("QZC.US", "CA"), opt(OPU, 120000.0)]

    @rule(R.CA_RPT_22)
    def test_a_montreal_option_on_a_foreign_share(self):
        rep = report(self.UP)
        # The share is on the form by its ISIN; the option is counted
        # by its own listing: off it.
        self.assertEqual(rep["max_total_cost"], 60000.0)
        self.assertEqual([r["symbol"] for r in rep["properties"]],
                         ["FGN.TO"])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_unresolved_symbols"], [OPT])
        self.assertIsNone(rep["filing_required"])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (60000.0, 110000.0))
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": OPT, "kind": "option", "underlying": "FGN.TO",
             "counted_as": "CAN", "issuer_country": "AUS",
             "isin_country_code": "AU", "affects": "total",
             "held_in_year": True, "symbols": {OPT: "CAN"},
             "lines": {"foreign": [f"T1135 {OPT} AUS"],
                       "not_foreign": [f"T1135 {OPT} CA"]}}])
        # An option conflict is open, not a security to review.
        self.assertEqual(rep["country_review"], [])
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertNotIn("does not change this", text)
            self.assertIn(f"=> NOT RESOLVED: below the {THR} threshold as "
                          f"counted, but {OPT} may be foreign property (an "
                          "option on a Canadian listing; its share FGN.TO "
                          "is foreign property): with it the maximum is "
                          f"{money(110000)} CAD", text)
            self.assertIn(f"Add `T1135 {OPT} AUS` (foreign property) or "
                          f"`T1135 {OPT} CA` (not foreign property) to "
                          "ticker.map", text)

    @rule(R.CA_RPT_22)
    def test_the_lines_on_the_option_settle_it(self):
        open_ = report(self.UP)
        lines = conflict(open_, OPT)["lines"]
        rep = report(self.UP, as_overrides(lines["foreign"]))
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["max_total_cost"], 110000.0)
        self.assertIs(rep["filing_required"], True)
        rep = report(self.UP, as_overrides(lines["not_foreign"]))
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["max_total_cost"], 60000.0)
        self.assertIs(rep["filing_required"], False)
        self.assertIn("no T1135 required", texts(rep)[0])

    @rule(R.CA_RPT_22)
    def test_a_us_option_on_a_canadian_share(self):
        rep = report(self.DOWN)
        # The share is off the form by its ISIN; the option is counted.
        self.assertEqual(rep["max_total_cost"], 120000.0)
        self.assertEqual([(r["symbol"], r["country"], r["country_source"])
                          for r in rep["properties"]],
                         [(OPU, "USA", "listing")])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, 120000.0))
        for text in texts(rep):
            self.assertNotIn("FILING REQUIRED", text)
            self.assertIn(f"=> NOT RESOLVED: over the {THR} threshold as "
                          f"counted, but {OPU} may not be foreign property "
                          "(an option on a foreign listing; its share "
                          "QZC.US is not foreign property): without it the "
                          "maximum is 0.00 CAD", text)
        lines = conflict(rep, OPU)["lines"]
        self.assertEqual(lines, {"foreign": [f"T1135 {OPU} USA"],
                                 "not_foreign": [f"T1135 {OPU} CA"]})
        low = report(self.DOWN, as_overrides(lines["not_foreign"]))
        self.assertEqual(low["max_total_cost"], 0.0)
        self.assertIs(low["filing_required"], False)

    @rule(R.CA_RPT_22)
    def test_the_verdict_stands_when_both_readings_agree(self):
        rep = report([buy("FGN.TO", SMALL), div("FGN.TO", "AU"),
                      opt(OPT, SMALL)])
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], False)
        for text in texts(rep):
            self.assertIn(f"Counting {OPT} the other way (an option and "
                          "its share disagree) does not change this (the "
                          f"maximum is {money(SMALL)} to "
                          f"{money(2 * SMALL)} CAD).", text)

    @rule(R.CA_RPT_22)
    def test_an_option_that_agrees_with_its_share_is_not_named(self):
        # A Montreal option on a Canadian share; a US one on a US share.
        rep = report([buy("FGN.TO", SMALL), opt(OPT, SMALL),
                      buy("QZC.US", SMALL), opt(OPU, SMALL)])
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["classification_status"], "resolved")

    @rule(R.CA_RPT_22)
    def test_a_written_or_expired_option_is_not_listed(self):
        # Written (an obligation, never a row), and one that expired
        # before the year: no entry, whatever its share is.
        old = "FGN230616C00150000.TO"
        rep = report([buy("FGN.TO", SMALL), div("FGN.TO", "AU"),
                      _r("BUYSELL", "2025-02-05", OPT, -1, 400.0),
                      _r("BUYSELL", "2023-02-05", old, 1, -400.0),
                      _r("BUYSELL", "2023-06-16", old, -1, 0.0)])
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["classification_status"], "resolved")

    @rule(R.CA_RPT_22)
    def test_the_options_of_an_open_share_are_walked_with_it(self):
        # The share's sources disagree: counted (as foreign) with its
        # option not (a Canadian listing); each is walked both ways.
        rep = report(torn("FGN.TO", 60000.0, more=[opt(OPT, 50000.0)]))
        self.assertEqual(rep["max_total_cost"], 60000.0)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, 110000.0))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(sorted(c["symbol"]
                                for c in rep["classification_conflicts"]),
                         ["FGN.TO", OPT])
        self.assertEqual(conflict(rep, OPT)["lines"]["foreign"],
                         [f"T1135 {OPT} AUS"])
        # Far below, the sentence is true with the option included.
        rep = report(torn("FGN.TO", SMALL, more=[opt(OPT, SMALL)]))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIn(f"Counting FGN.TO, {OPT} the other way (sources "
                      "disagree, options with their shares) does not "
                      f"change this (the maximum is 0.00 to "
                      f"{money(2 * SMALL)} CAD).", texts(rep)[0])


class TestAnIsinPrefixThatIsNoCountry(unittest.TestCase):
    """XS (an international security), EU, ZZ: nothing to write after
    `T1135 SYMBOL`, and never a guess."""

    @rule(R.CA_RPT_02)
    def test_on_a_canadian_listing_the_line_is_given_by_its_shape(self):
        for prefix in ("XS", "EU", "ZZ"):
            with self.subTest(prefix=prefix):
                rep = report(book("FGN.TO", COST, prefix))
                c = conflict(rep, "FGN.TO")
                self.assertEqual(c["kind"], "isin_no_country")
                self.assertIsNone(c["issuer_country"])
                self.assertEqual(c["isin_country_code"], prefix)
                self.assertEqual(c["affects"], "total")
                self.assertEqual(c["lines"], {
                    "foreign": None, "not_foreign": ["T1135 FGN.TO CA"]})
                self.assertEqual(rep["properties"][0]["country"], "??")
                self.assertEqual(rep["classification_status"],
                                 "unresolved")
                self.assertEqual((rep["max_total_cost_low"],
                                  rep["max_total_cost_high"]), (0.0, COST))
                for text in (*texts(rep), rep["_stderr"]):
                    self.assertNotIn(f"T1135 FGN.TO {prefix}", text)
                for text in texts(rep):
                    self.assertIn("`T1135 FGN.TO COUNTRY` (", text)
                    self.assertIn(
                        "COUNTRY: the issuer's country, its three-letter "
                        f"code; the ISIN's prefix {prefix} names no "
                        "country", text)
                self.assertIn(f"FGN.TO (ISIN prefix {prefix}: no country)",
                              rep["_stderr"])

    @rule(R.CA_RPT_02)
    def test_on_a_foreign_listing_the_total_stands(self):
        rep = report(book("FGN.US", SMALL, "XS"))
        self.assertEqual(rep["review_symbols"], ["FGN.US"])
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "FGN.US", "kind": "isin_no_country",
             "counted_as": "??", "issuer_country": None,
             "isin_country_code": "XS", "countries": [],
             "affects": "country", "held_in_year": True,
             "lines": {"choices": []}}])
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["properties"][0]["country"], "??")
        self.assertIn("FGN.US (ISIN prefix XS: no country)", rep["_stderr"])
        self.assertIn("add `T1135 FGN.US COUNTRY` to ticker.map",
                      texts(rep)[1])
        for text in (*texts(rep), rep["_stderr"]):
            self.assertNotIn("T1135 FGN.US XS", text)


class TestACostTheBooksDoNotGive(unittest.TestCase):
    """A foreign property counted at an unknown ACB understates the
    total: below the threshold is then no verdict either."""

    @rule(R.CA_RPT_12)
    def test_an_option_the_other_reading_adds_at_no_known_cost(self):
        rep = report([buy("FGN.TO", SMALL), div("FGN.TO", "AU"),
                      _r("OPENING_BALANCE", "2024-01-02", OPT, 1, 0.0)])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["max_total_cost_high"])
        self.assertEqual(rep["max_total_cost_high_at_least"], SMALL)
        self.assertEqual(rep["classification_unknown_cost_symbols"], [OPT])
        for text in texts(rep):
            self.assertIn(f"{OPT} may be foreign property (an option on a "
                          "Canadian listing; its share FGN.TO is foreign "
                          "property): with it the maximum is at least "
                          f"{money(SMALL)} CAD, plus {OPT} at a cost the "
                          "books do not give", text)
            self.assertNotIn("no T1135 required", text)

    @rule(R.CA_RPT_12)
    def test_the_line_named_leads_to_not_settled_not_to_a_no(self):
        rows = [unknown("FGN.TO"), div("FGN.TO", "AU")]
        rep = report(rows)
        # The ISIN decides: on the form, at a cost the books do not give.
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["unknown_acb_symbols"], ["FGN.TO"])
        self.assertIs(rep["total_settled"], True)
        self.assertIsNone(rep["filing_required"])
        self.assertIsNone(rep["simplified_method_available"])
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertIn(f"=> NOT SETTLED: below the {THR} threshold", text)
            self.assertIn("the cost of FGN.TO is unknown", text)
            self.assertIn("add its purchase (an older export, or an "
                          "OPENING line with its cost: tjs "
                          "find-missing-history), then `tjs run`", text)
        res = TestTheChecklistStep().step(rep)
        self.assertEqual(res.status, "attention")
        self.assertIn("the cost of FGN.TO is unknown — the T1135 total is "
                      "not settled", res.detail)
        self.assertEqual(res.cmd, "tjs find-missing-history")

    @rule(R.CA_RPT_12)
    def test_a_plain_foreign_holding_of_unknown_cost(self):
        rep = report([unknown("QZX.US")])
        self.assertIsNone(rep["filing_required"])
        self.assertIn("=> NOT SETTLED: below", texts(rep)[0])
        # In an open year it is said with its "so far".
        rep = report([unknown("QZX.US")], today="2025-09-01")
        self.assertIn(f"=> NOT SETTLED: below the {THR} threshold so far, "
                      "but the cost of QZX.US is unknown", texts(rep)[0])

    @rule(R.CA_RPT_15)
    def test_over_the_threshold_stays_required_and_the_part_open(self):
        rep = report([unknown("QZU.US"), buy("QZX.US", COST)])
        self.assertIs(rep["filing_required"], True)
        self.assertIsNone(rep["simplified_method_available"])
        for text in texts(rep):
            self.assertIn("FILING REQUIRED", text)
            self.assertIn("Part A or Part B open (the cost of QZU.US is "
                          "unknown", text)
        rep = report([unknown("QZU.US"), buy("QZX.US", 260000.0)])
        self.assertIs(rep["simplified_method_available"], False)
        self.assertIn("detailed method (Part B)", texts(rep)[0])


class TestCashInTransitAtTheYearEnd(unittest.TestCase):
    """The ledger computed and only where cash in transit was held is
    open: no cost is unknown, and nothing says one is."""

    CASH = {"series": [], "ledger_status": "computed",
            "accounts": {"margin": "USA"},
            "not_counted": [{"account": "margin", "currency": "USD",
                             "unknown": True, "year_end_transit": True,
                             "why": "100.00 USD in transit"}]}

    @rule(R.CA_RPT_20)
    def test_the_open_part_says_transit(self):
        rep = report(book("QZX.US", COST, "US"), cash=self.CASH)
        self.assertIs(rep["total_settled"], False)
        self.assertIs(rep["filing_required"], True)
        self.assertIsNone(rep["simplified_method_available"])
        for text in texts(rep):
            self.assertIn("Part A or Part B open (where cash in transit at "
                          "the year end was held is open", text)
            self.assertNotIn("some cash's cost is unknown", text)

    @rule(R.CA_RPT_20)
    def test_not_resolved_and_not_settled_says_transit(self):
        rep = report(torn("QZW.US", COST), cash=self.CASH)
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertIn("; NOT SETTLED either: where cash in transit at "
                          "the year end was held is open — tjs t1135 "
                          "--details", text)
            self.assertNotIn("some cash's cost is unknown", text)

    @rule(R.CA_RPT_20)
    def test_below_the_threshold_says_transit_in_both_views(self):
        rep = report(book("QZX.US", SMALL, "US"), cash=self.CASH)
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertIn("=> NOT SETTLED: below the", text)
            self.assertIn("where cash in transit at the year end was held "
                          "is open", text)
            self.assertNotIn("cash's cost is unknown", text)
            self.assertNotIn("the cost of some cash", text)


class TestEveryLineNamedIsOneTickerMapTakes(unittest.TestCase):
    BOOKS = {
        "torn": torn("FGN.TO", COST),
        "holds": torn("FGN.TO", SMALL),
        "option up": TestAnOptionAndItsShare.UP,
        "option down": TestAnOptionAndItsShare.DOWN,
        "third": book("FGN.US", SMALL, "IE"),
        "two abroad": torn("FGN.US", SMALL, one="IE", other="BM"),
        "no country": book("FGN.TO", COST, "XS"),
        "no country, abroad": book("FGN.US", SMALL, "EU"),
        "several": (torn("FGN.TO", 40000.0) + torn("QZA.TO", 40000.0)
                    + book("QZB.TO", 40000.0, "ZZ")),
    }

    @rule(R.CA_RPT_02)
    def test_each_is_read_back_by_the_parser(self):
        for name, rows in self.BOOKS.items():
            with self.subTest(book=name):
                rep = report(rows)
                named = [ln for text in (*texts(rep), rep["_stderr"])
                         for ln in lines_named(text)]
                listed = [ln for c in rep["classification_conflicts"]
                          for v in c["lines"].values() for ln in v or []]
                if "no country, abroad" not in name:
                    self.assertTrue(named, name)
                for ln in sorted(set(named + listed)):
                    # load_overrides stops on a line ticker.map refuses.
                    sym = ln.split()[1]
                    self.assertIn(sym, as_overrides([ln]), ln)


class TestTheKeysSayWhatTheTextSays(unittest.TestCase):
    def verdict(self, **kw):
        cls = {"classification_status": kw.pop("status", "resolved"),
               "max_total_cost_low": kw.pop("low", kw["max_total"]),
               "max_total_cost_high": kw.pop("high", kw["max_total"])}
        kw.setdefault("settled", True)
        kw.setdefault("complete", True)
        v = verdict_keys(threshold=100000.0, detailed=250000.0, cls=cls,
                         **kw)
        return (v["over_threshold_as_counted"], v["filing_required"],
                v["simplified_method_available"])

    @rule(R.CA_RPT_13)
    def test_filing_required_is_null_wherever_there_is_no_verdict(self):
        v = self.verdict
        self.assertEqual(v(max_total=500.0), (False, False, None))
        self.assertEqual(v(max_total=500.0, acb_known=False),
                         (False, None, None))
        self.assertEqual(v(max_total=125000.0, acb_known=False),
                         (True, True, None))
        self.assertEqual(v(max_total=260000.0, acb_known=False),
                         (True, True, False))
        self.assertEqual(v(max_total=500.0, complete=False),
                         (False, None, None))
        self.assertEqual(v(max_total=500.0, settled=False),
                         (False, None, None))
        self.assertEqual(v(max_total=500.0, status="unresolved",
                           high=125000.0), (False, None, None))
        self.assertEqual(v(max_total=125000.0, status="unresolved",
                           low=0.0), (True, None, None))
        # Over is certain once reached: an open year, cash of unknown
        # cost and a conflict that cannot take it back under leave it.
        self.assertEqual(v(max_total=125000.0), (True, True, True))
        self.assertEqual(v(max_total=125000.0, complete=False),
                         (True, True, None))
        self.assertEqual(v(max_total=125000.0, settled=False),
                         (True, True, None))
        self.assertEqual(v(max_total=125000.0, status="verdict_holds",
                           low=124000.0, high=126000.0), (True, True, True))

    @rule(R.CA_RPT_15)
    def test_the_part_is_null_until_it_is_decided(self):
        v = self.verdict
        self.assertEqual(v(max_total=260000.0), (True, True, False))
        self.assertEqual(v(max_total=260000.0, complete=False,
                           settled=False), (True, True, False))
        self.assertEqual(v(max_total=260000.0, status="verdict_holds",
                           low=240000.0), (True, True, None))
        self.assertEqual(v(max_total=240000.0, status="verdict_holds",
                           high=260000.0), (True, True, None))
        self.assertEqual(v(max_total=240000.0, status="verdict_holds",
                           high=None), (True, True, None))

    @rule(R.CA_RPT_13)
    def test_every_report_here_agrees_with_its_text(self):
        reps = [report(rows) for rows in
                TestEveryLineNamedIsOneTickerMapTakes.BOOKS.values()]
        reps += [
            report(book("QZX.US", SMALL, "US")),
            report(book("QZX.US", COST, "US")),
            report(book("QZX.US", SMALL, "US"), today="2025-09-01"),
            report(book("QZX.US", COST, "US"), today="2025-09-01"),
            report(book("QZX.US", SMALL, "US"),
                   cash=cash_doc(0.0, unknown_cost=True)),
            report(book("QZX.US", COST, "US"),
                   cash=cash_doc(0.0, unknown_cost=True)),
            report(book("QZX.US", 260000.0, "US"))]
        seen = set()
        for rep in reps:
            for text in texts(rep):
                said = (True if "FILING REQUIRED" in text
                        else False if "no T1135 required" in text else None)
                self.assertIs(rep["filing_required"], said, text[:300])
                part = (None if said is not True
                        or "Part A or Part B open" in text
                        else "Part B" not in text.split("PER PROPERTY")[0])
                self.assertIs(rep["simplified_method_available"], part,
                              text[:300])
            seen.add((rep["filing_required"],
                      rep["simplified_method_available"]))
        self.assertEqual(seen, {(None, None), (False, None), (True, True),
                                (True, False), (True, None)})

    @rule(R.CA_RPT_15)
    def test_an_open_part_says_why(self):
        rep = report(book("QZX.US", COST, "US"), today="2025-09-01")
        self.assertIn(f"=> T1135 FILING REQUIRED (over {THR}): Part A or "
                      f"Part B open (under {money(250000)} CAD so far)",
                      texts(rep)[0])
        rep = report(book("QZX.US", COST, "US"),
                     cash=cash_doc(0.0, unknown_cost=True))
        self.assertIn("Part A or Part B open (some cash's cost is unknown)",
                      texts(rep)[0])
        self.assertIn("=> Part A or Part B open (some cash's cost is "
                      "unknown; the detailed method from", texts(rep)[1])

    @rule(R.CA_RPT_02)
    def test_the_json_carries_them(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps(
                {"transactions": torn("FGN.TO", COST)}))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = main([str(base), "--year", "2025", "--json"])
        self.assertEqual(rc, 0, err.getvalue())
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(doc["classification_status"], "unresolved")
        self.assertEqual(doc["classification_unresolved_symbols"],
                         ["FGN.TO"])
        self.assertIsNone(doc["filing_required"])
        self.assertIsNone(doc["simplified_method_available"])
        self.assertIs(doc["over_threshold_as_counted"], True)
        self.assertIs(doc["total_settled"], True)
        self.assertEqual(doc["max_total_cost"], COST)
        self.assertEqual(doc["max_total_cost_low"], 0.0)
        self.assertEqual(doc["classification_conflicts"][0]["lines"],
                         {"foreign": ["T1135 FGN.TO AUS"],
                          "not_foreign": ["T1135 FGN.TO CA"]})
        # No key that starts with an underscore leaves the tool.
        self.assertEqual([k for c in doc["classification_conflicts"]
                          for k in c if k.startswith("_")], [])


class TestTheChecklistStep(unittest.TestCase):
    def step(self, rep, today=date(2026, 6, 1)):
        from taxjson.lib import checklist as CL
        ctx = mock.Mock()
        ctx.settings = Settings.from_table({"country": "canada"})
        ctx.year = 2025
        ctx.today = today
        ctx.sub.return_value = (0, json.dumps(
            {k: v for k, v in rep.items() if not k.startswith("_")}), "")
        return CL.d_t1135(ctx)

    @rule(R.CA_RPT_02)
    def test_it_is_neither_done_nor_file_it(self):
        for rows, words in (
                (TestAnOptionAndItsShare.UP,
                 f"{OPT} may be foreign property (`tjs t1135` says why), "
                 "and the result (below the threshold as counted) depends "
                 "on it — add the `T1135` line `tjs t1135` names to "
                 "ticker.map, then `tjs run`"),
                (torn("QZW.US", COST),
                 "QZW.US may not be foreign property (`tjs t1135` says "
                 "why), and the result (over the threshold as counted) "
                 "depends on it")):
            with self.subTest(symbol=rows[0]["symbol"]):
                res = self.step(report(rows))
                self.assertEqual(res.status, "attention")
                self.assertIn("the T1135 test is not resolved: " + words,
                              res.detail)
                self.assertEqual(res.cmd, "tjs t1135")

    @rule(R.CA_RPT_02)
    def test_several_symbols_read_as_several(self):
        res = self.step(report(torn("FGN.TO", 40000.0)
                               + torn("QZA.TO", 40000.0)
                               + torn("QZB.TO", 40000.0)))
        self.assertEqual(res.status, "attention")
        self.assertIn("FGN.TO, QZA.TO +1 more may not be foreign property "
                      "(`tjs t1135` says why)", res.detail)
        self.assertIn("add the `T1135` lines `tjs t1135` names",
                      res.detail)

    @rule(R.CA_RPT_22)
    def test_an_option_whose_reading_cannot_matter_leaves_it_done(self):
        # The verdict holds either way and nothing is to review: an
        # option conflict alone asks for nothing.
        rep = report([buy("FGN.TO", SMALL), div("FGN.TO", "AU"),
                      opt(OPT, SMALL)])
        self.assertEqual(self.step(rep).status, "done")
        rep = report([buy("QZC.US", SMALL), div("QZC.US", "CA"),
                      opt(OPU, SMALL), buy("QZX.US", COST, "2025-02-04")])
        self.assertEqual(self.step(rep).status, "manual")

    @rule(R.CA_RPT_02)
    def test_a_security_to_review_needs_attention(self):
        # Far below the threshold either way, and still a row whose
        # country nothing may be picked for.
        res = self.step(report(torn("FGN.TO", SMALL)))
        self.assertEqual(res.status, "attention")
        self.assertIn("1 security to review for the T1135's country: "
                      "FGN.TO — add the `T1135` line `tjs t1135 --details` "
                      "names for each to ticker.map, then `tjs run`",
                      res.detail)
        self.assertEqual(res.cmd, "tjs t1135 --details")

    @rule(R.CA_RPT_13)
    def test_a_null_filing_required_is_never_done(self):
        # An open year and cash of unknown cost, as before the key
        # could be null.
        rep = report(book("QZX.US", SMALL, "US"), today="2025-09-01")
        self.assertIsNone(rep["filing_required"])
        self.assertEqual(self.step(rep, date(2025, 9, 1)).status, "todo")
        rep = report(book("QZX.US", SMALL, "US"),
                     cash=cash_doc(0.0, unknown_cost=True))
        self.assertIsNone(rep["filing_required"])
        self.assertEqual(self.step(rep).status, "attention")


if __name__ == "__main__":
    unittest.main()
