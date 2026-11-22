"""`tjs t1135` while a holding's ISIN and its listing disagree on its
being foreign property (backlog TJ-4956, TJ-4958).

The books count a symbol by its listing. When the rows' ISIN
(`issuer_country`, stamped by the IB reader) says otherwise, the symbol
was named in a warning and the verdict printed regardless: "no T1135
required" over a Toronto-listed holding of a foreign issuer whose cost
alone was over the threshold. Now the test is open (`--json`
`classification_status` "unresolved", `filing_required` null, the text
NOT RESOLVED with the ticker.map lines that settle it) whenever counting
the named holdings the other way would move the total across the
threshold, in either direction; it says the result is the same either
way when it is; and a holding foreign under either country (the total
cannot move) is a warning and a symbol to review.

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
from taxjson.lib import out as _out
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


class TestAForeignIsinOnACanadianListing(unittest.TestCase):
    """TJ-4956: FGN.TO, an Australian ISIN, counted as Canadian."""

    @rule(R.CA_RPT_02)
    def test_a_cost_over_the_threshold_leaves_the_test_open(self):
        rep = report(book("FGN.TO", COST, "AU"))
        # The figures stay as counted: nothing is reclassified.
        self.assertEqual(rep["max_total_cost"], 0.0)
        self.assertIs(rep["over_threshold_as_counted"], False)
        self.assertIs(rep["total_settled"], True)
        self.assertEqual(rep["properties"], [])
        # ... and no key says they are a verdict.
        self.assertIsNone(rep["filing_required"])
        self.assertIsNone(rep["simplified_method_available"])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_unresolved_symbols"],
                         ["FGN.TO"])
        self.assertEqual(rep["classification_unknown_cost_symbols"], [])
        self.assertEqual(rep["max_total_cost_low"], 0.0)
        self.assertEqual(rep["max_total_cost_high"], COST)
        self.assertEqual(rep["max_total_cost_high_at_least"], COST)
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "FGN.TO", "counted_as": "CAN",
             "issuer_country": "AUS", "isin_country_code": "AU",
             "affects": "total", "held_in_year": True,
             "symbols": {"FGN.TO": "CAN"},
             "lines": {"foreign": ["T1135 FGN.TO AUS"],
                       "not_foreign": ["T1135 FGN.TO CA"]}}])
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertNotIn("FILING REQUIRED", text)
            self.assertIn(f"=> NOT RESOLVED: below the {THR} threshold as "
                          "counted, but FGN.TO may be foreign property (a "
                          "foreign ISIN on a Canadian listing): with it "
                          f"the maximum is {money(COST)} CAD", text)
            self.assertIn("Add `T1135 FGN.TO AUS` (foreign property) or "
                          "`T1135 FGN.TO CA` (not foreign property) to "
                          "ticker.map, then `tjs run` and `tjs t1135`.",
                          text)

    @rule(R.CA_RPT_02)
    def test_the_lines_named_give_the_figures_named(self):
        rows = book("FGN.TO", COST, "AU")
        open_ = report(rows)
        lines = conflict(open_, "FGN.TO")["lines"]
        rep = report(rows, as_overrides(lines["foreign"]))
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["max_total_cost"],
                         open_["max_total_cost_high"])
        self.assertIs(rep["filing_required"], True)
        self.assertIs(rep["simplified_method_available"], True)
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
        rep = report(book("FGN.TO", SMALL, "AU"))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertEqual(rep["classification_unresolved_symbols"], [])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, SMALL))
        self.assertIs(rep["filing_required"], False)
        for text in texts(rep):
            self.assertIn("no T1135 required", text)
            self.assertNotIn("NOT RESOLVED", text)
            self.assertIn("Counting FGN.TO the other way (ISIN and listing "
                          "disagree) does not change this (the maximum is "
                          f"0.00 to {money(SMALL)} CAD).", text)

    @rule(R.CA_RPT_02)
    def test_already_required_holds_either_way(self):
        rep = report(book("FGN.TO", SMALL, "AU",
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
        # 249,500 as counted (Part A); with FGN.TO, over 250,000. pii-ok
        rep = report(book("FGN.TO", SMALL, "AU",
                          more=[buy("QZX.US", 249500.0, "2025-02-04")]))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], True)
        self.assertIsNone(rep["simplified_method_available"])
        for text in texts(rep):
            self.assertIn("Part A or Part B open (it depends on how FGN.TO "
                          "is counted", text)
            self.assertNotIn("Simplified method (Part A) available", text)
            self.assertNotIn("simplified method (Part A)", text)

    @rule(R.CA_RPT_02)
    def test_the_sum_with_other_property_decides(self):
        # Neither alone is over; together they are.
        rep = report(book("FGN.TO", 60000.0, "AU",
                          more=[buy("QZX.US", 60000.0, "2025-02-04")]))
        self.assertIsNone(rep["filing_required"])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_high"], 120000.0)

    @rule(R.CA_RPT_02)
    def test_a_cost_the_books_do_not_give_leaves_it_open(self):
        rep = report([unknown("FGN.TO"), div("FGN.TO", "AU")])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["max_total_cost_high"])
        self.assertEqual(rep["max_total_cost_high_at_least"], 0.0)
        self.assertEqual(rep["classification_unknown_cost_symbols"],
                         ["FGN.TO"])
        for text in texts(rep):
            self.assertIn("FGN.TO may be foreign property (a foreign ISIN "
                          "on a Canadian listing): with it the maximum is "
                          "at least 0.00 CAD, plus FGN.TO at a cost the "
                          "books do not give", text)
            self.assertNotIn("no T1135 required", text)

    @rule(R.CA_RPT_02)
    def test_several_open_and_one_of_unknown_cost_keeps_the_known_part(self):
        rep = report(book("FGN.TO", 60000.0, "AU")
                     + [unknown("QZA.TO"), div("QZA.TO", "US")])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_unresolved_symbols"],
                         ["FGN.TO", "QZA.TO"])
        self.assertIsNone(rep["max_total_cost_high"])
        self.assertEqual(rep["max_total_cost_high_at_least"], 60000.0)
        for text in texts(rep):
            self.assertIn("FGN.TO, QZA.TO may be foreign property (a "
                          "foreign ISIN on a Canadian listing): with them "
                          f"the maximum is at least {money(60000)} CAD, "
                          "plus QZA.TO at a cost the books do not give",
                          text)
            self.assertIn("Add `T1135 FGN.TO AUS` (foreign property) or "
                          "`T1135 FGN.TO CA` (not foreign property); "
                          "`T1135 QZA.TO USA` (foreign property) or "
                          "`T1135 QZA.TO CA` (not foreign property) to "
                          "ticker.map", text)

    @rule(R.CA_RPT_02)
    def test_more_symbols_than_the_default_view_names(self):
        rows = (book("FGN.TO", 40000.0, "AU") + book("QZA.TO", 40000.0, "US")
                + book("QZB.TO", 40000.0, "IE"))
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "unresolved")
        brief, full = texts(rep)
        self.assertIn("FGN.TO, QZA.TO +1 more may be foreign property",
                      brief)
        self.assertIn("with them the maximum is", brief)
        self.assertIn("; and the lines of 1 more (tjs t1135 --details) to "
                      "ticker.map", brief)
        self.assertNotIn("T1135 QZB.TO", brief.split("! ")[0])
        for ln in ("`T1135 FGN.TO AUS`", "`T1135 QZA.TO USA`",
                   "`T1135 QZB.TO IRL`", "`T1135 QZB.TO CA`"):
            self.assertIn(ln, full)
        # Every line named, together, gives the figure named.
        every = [x for c in rep["classification_conflicts"]
                 for x in c["lines"]["foreign"]]
        self.assertEqual(report(rows, as_overrides(every))["max_total_cost"],
                         rep["max_total_cost_high"])

    @rule(R.CA_RPT_02)
    def test_a_holding_sold_before_the_year_decides_nothing(self):
        rows = [buy("FGN.TO", COST, "2023-02-03"),
                div("FGN.TO", "AU", "2023-06-17"),
                sell("FGN.TO", COST, "2023-09-01")]
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertIs(conflict(rep, "FGN.TO")["held_in_year"], False)
        self.assertEqual(rep["max_total_cost_high"], 0.0)
        self.assertIs(rep["filing_required"], False)
        for text in texts(rep):
            self.assertIn("no T1135 required", text)
            self.assertNotIn("the other way", text)

    @rule(R.CA_RPT_19)
    def test_a_holding_only_short_decides_nothing(self):
        rep = report([sell("FGN.TO", COST, "2025-02-03"),
                      div("FGN.TO", "AU")])
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertIs(conflict(rep, "FGN.TO")["held_in_year"], False)

    @rule(R.CA_RPT_20)
    def test_with_a_cash_row_it_is_the_sum_that_decides(self):
        rep = report(book("FGN.TO", 60000.0, "AU"), cash=cash_doc(60000.0))
        self.assertEqual(rep["max_total_cost"], 60000.0)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_high"], 120000.0)
        self.assertIn("=> NOT RESOLVED: below", texts(rep)[0])

    @rule(R.CA_RPT_20)
    def test_cash_of_unknown_cost_keeps_its_line_and_this_is_added(self):
        rep = report(book("FGN.TO", COST, "AU"),
                     cash=cash_doc(0.0, unknown_cost=True))
        self.assertIs(rep["total_settled"], False)
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertIn("=> NOT SETTLED: below the", text)
            self.assertIn("Also not resolved: FGN.TO may be foreign "
                          "property (a foreign ISIN on a Canadian "
                          "listing): with it the maximum is at least "
                          f"{money(COST)} CAD.", text)
            self.assertIn("Add `T1135 FGN.TO AUS`", text)
            self.assertNotIn("no T1135 required", text)

    @rule(R.CA_RPT_13)
    def test_an_open_year_keeps_its_so_far(self):
        rep = report(book("FGN.TO", COST, "AU"), today="2025-09-01")
        self.assertIs(rep["year_complete"], False)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertIn(f"=> NOT RESOLVED: below the {THR} threshold as "
                          "counted so far (books through 2025-06-17), but "
                          "FGN.TO may be foreign property", text)
            self.assertIn(f"with it the maximum so far is {money(COST)} "
                          "CAD", text)
            self.assertIn("then `tjs run` and `tjs t1135`; the test runs "
                          "to Dec 31, re-run after it.", text)

    @rule(R.CA_RPT_02)
    def test_a_custodian_abroad_already_counts_it(self):
        # Held with a custodian outside Canada it is foreign property
        # under either reading: the same total.
        rep = report(book("FGN.TO", COST, "AU"), custody={"margin": "USA"})
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (COST, COST))
        self.assertIs(rep["filing_required"], True)
        self.assertIn(f"does not change this (the maximum stays "
                      f"{money(COST)} CAD).", texts(rep)[0])

    @rule(R.CA_RPT_15)
    def test_required_either_way_with_a_cost_unknown_leaves_the_part_open(
            self):
        rep = report([unknown("FGN.TO"), div("FGN.TO", "AU"),
                      buy("QZX.US", 240000.0)])
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIsNone(rep["max_total_cost_high"])
        self.assertIs(rep["filing_required"], True)
        self.assertIsNone(rep["simplified_method_available"])
        for text in texts(rep):
            self.assertIn("FILING REQUIRED", text)
            self.assertIn("Part A or Part B open (it depends on how FGN.TO "
                          "is counted", text)
            self.assertIn("Counting FGN.TO the other way (ISIN and listing "
                          "disagree) does not change this.", text)


class TestAnIsinPrefixThatIsNoCountry(unittest.TestCase):
    """XS (an international security), EU, ZZ: nothing to write after
    `T1135 SYMBOL`, and ticker.map refuses the prefix."""

    @rule(R.CA_RPT_02)
    def test_the_line_is_given_by_its_shape(self):
        for prefix in ("XS", "EU", "ZZ"):
            with self.subTest(prefix=prefix):
                rep = report(book("FGN.TO", COST, prefix))
                c = conflict(rep, "FGN.TO")
                self.assertIsNone(c["issuer_country"])
                self.assertEqual(c["isin_country_code"], prefix)
                self.assertEqual(c["lines"], {
                    "foreign": None, "not_foreign": ["T1135 FGN.TO CA"]})
                self.assertEqual(rep["classification_status"],
                                 "unresolved")
                self.assertEqual(rep["max_total_cost_high"], COST)
                for text in (*texts(rep), rep["_stderr"]):
                    self.assertNotIn(f"T1135 FGN.TO {prefix}", text)
                    self.assertIn(
                        "`T1135 FGN.TO COUNTRY` (", text)
                    self.assertIn(
                        "COUNTRY: the issuer's country, its three-letter "
                        f"code; the ISIN's prefix {prefix} names no "
                        "country", text)
                self.assertIn(f"FGN.TO (ISIN prefix {prefix}: no country)",
                              rep["_stderr"])

    @rule(R.CA_RPT_02)
    def test_on_a_foreign_listing_it_is_still_to_review(self):
        rep = report(book("FGN.US", SMALL, "XS"))
        self.assertEqual(rep["review_symbols"], ["FGN.US"])
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "FGN.US", "counted_as": "USA",
             "issuer_country": None, "isin_country_code": "XS",
             "affects": "country", "held_in_year": True,
             "lines": {"as_counted": ["T1135 FGN.US USA"], "other": None}}])
        self.assertIn("FGN.US (ISIN prefix XS: no country, counted under "
                      "USA)", rep["_stderr"])
        self.assertIn("`T1135 FGN.US COUNTRY` (", rep["_stderr"])
        self.assertIn("`T1135 FGN.US USA` to keep it", rep["_stderr"])
        for text in (*texts(rep), rep["_stderr"]):
            self.assertNotIn("T1135 FGN.US XS", text)


class TestATickerChange(unittest.TestCase):
    """The security is one through its ticker changes: the other
    reading covers the whole year, and the line named is the one that
    does."""

    # Most of it sold before the change; the ISIN on the new symbol.
    UP = [buy("OLD.TO", COST), sell("OLD.TO", 112500.0, "2025-03-03", 90),
          ren("OLD.TO", "NEW.TO", "2025-04-01"), div("NEW.TO", "AU")]
    DOWN = [buy("OLDW.US", COST), sell("OLDW.US", 112500.0, "2025-03-03", 90),
            ren("OLDW.US", "NEWW.US", "2025-04-01"), div("NEWW.US", "CA")]

    @rule(R.CA_RPT_02)
    def test_a_foreign_isin_after_the_change_reaches_back(self):
        rep = report(self.UP)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_high"], COST)
        self.assertIsNone(rep["filing_required"])
        c = conflict(rep, "NEW.TO")
        self.assertEqual(c["symbols"], {"NEW.TO": "CAN", "OLD.TO": "CAN"})
        self.assertEqual(c["lines"], {"foreign": ["T1135 OLD.TO AUS"],
                                      "not_foreign": ["T1135 OLD.TO CA"]})
        for text in texts(rep):
            self.assertIn("Add `T1135 OLD.TO AUS` (foreign property; "
                          "OLD.TO: NEW.TO before its ticker change) or "
                          "`T1135 OLD.TO CA` (not foreign property) to "
                          "ticker.map", text)
        self.assertIn("add `T1135 OLD.TO AUS` (OLD.TO: NEW.TO before its "
                      "ticker change) to ticker.map once confirmed, or "
                      "`T1135 OLD.TO CA`", rep["_stderr"])
        # Exactly the line named gives the figure named.
        other = report(self.UP, as_overrides(c["lines"]["foreign"]))
        self.assertEqual(other["max_total_cost"], COST)
        self.assertIs(other["filing_required"], True)
        self.assertEqual(other["classification_conflicts"], [])
        same = report(self.UP, as_overrides(c["lines"]["not_foreign"]))
        self.assertEqual(same["max_total_cost"], rep["max_total_cost_low"])
        self.assertIs(same["filing_required"], False)
        self.assertEqual(same["classification_conflicts"], [])

    @rule(R.CA_RPT_02)
    def test_a_canadian_isin_after_the_change_reaches_back(self):
        rep = report(self.DOWN)
        self.assertEqual(rep["max_total_cost"], COST)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_low"], 0.0)
        self.assertIsNone(rep["filing_required"])
        c = conflict(rep, "NEWW.US")
        self.assertEqual(c["lines"], {"foreign": ["T1135 OLDW.US USA"],
                                      "not_foreign": ["T1135 OLDW.US CA"]})
        self.assertIn("Add `T1135 OLDW.US CA` (not foreign property; "
                      "OLDW.US: NEWW.US before its ticker change) or "
                      "`T1135 OLDW.US USA` (foreign property) to "
                      "ticker.map", texts(rep)[0])
        other = report(self.DOWN, as_overrides(c["lines"]["not_foreign"]))
        self.assertEqual(other["max_total_cost"], 0.0)
        self.assertIs(other["filing_required"], False)
        self.assertEqual(other["classification_conflicts"], [])
        same = report(self.DOWN, as_overrides(c["lines"]["foreign"]))
        self.assertEqual(same["max_total_cost"], COST)
        self.assertIs(same["filing_required"], True)
        self.assertEqual(same["classification_conflicts"], [])

    @rule(R.CA_RPT_02)
    def test_a_line_before_the_change_is_not_reopened_after_it(self):
        for rows, ov, total in ((self.UP, {"OLD.TO": None}, 0.0),
                                (self.UP, {"OLD.TO": "AUS"}, COST),
                                (self.DOWN, {"OLDW.US": "USA"}, COST),
                                (self.DOWN, {"OLDW.US": None}, 0.0)):
            with self.subTest(ov=ov):
                rep = report(rows, ov)
                self.assertEqual(rep["classification_conflicts"], [])
                self.assertEqual(rep["classification_status"], "resolved")
                self.assertEqual(rep["foreign_issuer_symbols"], [])
                self.assertEqual(rep["canadian_issuer_symbols"], [])
                self.assertEqual(rep["max_total_cost"], total)
                self.assertNotIn("ISIN", rep["_stderr"])

    @rule(R.CA_RPT_02)
    def test_an_isin_before_the_change_follows_it(self):
        rows = [buy("OLD.TO", COST), div("OLD.TO", "AU", "2025-03-01"),
                ren("OLD.TO", "NEW.TO", "2025-04-01")]
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(conflict(rep, "OLD.TO")["lines"]["foreign"],
                         ["T1135 OLD.TO AUS"])
        self.assertEqual(rep["max_total_cost_high"], COST)


def settled_by(testcase, rows, rep, symbol, **kw):
    """Feed each line named for `symbol` back: the foreign one gives
    the highest maximum named, the other the lowest, and neither leaves
    a conflict."""
    lines = conflict(rep, symbol)["lines"]
    for key, figure in (("foreign", rep["max_total_cost_high"]),
                        ("not_foreign", rep["max_total_cost_low"])):
        after = report(rows, as_overrides(lines[key]), **kw)
        testcase.assertEqual(after["max_total_cost"], figure, key)
        testcase.assertEqual(after["classification_conflicts"], [], key)
        testcase.assertEqual(after["classification_status"], "resolved")
        testcase.assertIs(after["filing_required"], figure > 100000.0, key)


class TestATickerChangeAcrossTheBorder(unittest.TestCase):
    """A ticker change from a foreign listing to a Canadian one (or
    back): as counted is neither reading, the security foreign for part
    of the year. Both whole-year readings are walked."""

    @rule(R.CA_RPT_02)
    def test_a_canadian_isin_then_a_canadian_listing(self):
        rows = [buy("OLDW.US", 60000.0), div("OLDW.US", "CA", "2025-02-20"),
                ren("OLDW.US", "NEW.TO", "2025-04-01"),
                buy("NEW.TO", 60000.0, "2025-07-02")]
        rep = report(rows)
        # Counted: foreign until the change, 60,000 at most. pii-ok
        self.assertEqual(rep["max_total_cost"], 60000.0)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, 120000.0))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        c = conflict(rep, "OLDW.US")
        self.assertIs(c["held_in_year"], True)
        self.assertEqual(c["symbols"], {"NEW.TO": "CAN", "OLDW.US": "USA"})
        self.assertEqual(c["lines"], {"foreign": ["T1135 OLDW.US USA"],
                                      "not_foreign": ["T1135 OLDW.US CA"]})
        for text in texts(rep):
            self.assertNotIn("no T1135 required", text)
            self.assertIn("OLDW.US may be foreign property (ISIN and "
                          "listings disagree through its ticker change): "
                          f"with it the maximum is {money(120000)} CAD",
                          text)
            self.assertIn("Add `T1135 OLDW.US USA` (foreign property) or "
                          "`T1135 OLDW.US CA` (not foreign property) to "
                          "ticker.map", text)
        settled_by(self, rows, rep, "OLDW.US")

    @rule(R.CA_RPT_02)
    def test_a_foreign_isin_then_a_foreign_listing(self):
        rows = [buy("OLD.TO", COST), div("OLD.TO", "AU", "2025-02-20"),
                ren("OLD.TO", "NEWW.US", "2025-04-01")]
        rep = report(rows)
        # Counted: foreign from the change on, so over; not foreign at
        # all under the other line.
        self.assertEqual(rep["max_total_cost"], COST)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, COST))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        self.assertEqual(conflict(rep, "OLD.TO")["lines"], {
            "foreign": ["T1135 OLD.TO AUS"],
            "not_foreign": ["T1135 OLD.TO CA"]})
        for text in texts(rep):
            self.assertNotIn("FILING REQUIRED", text)
            self.assertNotIn("does not change this", text)
            self.assertIn("OLD.TO may not be foreign property (ISIN and "
                          "listings disagree through its ticker change): "
                          "without it the maximum is 0.00 CAD", text)
        settled_by(self, rows, rep, "OLD.TO")

    @rule(R.CA_RPT_02)
    def test_a_canadian_isin_after_the_change_to_a_foreign_listing(self):
        rows = [buy("OLD.TO", 60000.0), ren("OLD.TO", "NEWW.US", "2025-04-01"),
                div("NEWW.US", "CA"), buy("QZX.US", 60000.0, "2025-01-10"),
                sell("QZX.US", 60000.0, "2025-03-03")]
        rep = report(rows)
        # Counted: QZX.US alone, then NEWW.US alone; foreign all year
        # the two overlap.
        self.assertEqual(rep["max_total_cost"], 60000.0)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (60000.0, 120000.0))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        self.assertEqual(conflict(rep, "NEWW.US")["lines"], {
            "foreign": ["T1135 OLD.TO USA"],
            "not_foreign": ["T1135 OLD.TO CA"]})
        brief = texts(rep)[0]
        self.assertNotIn("no T1135 required", brief)
        self.assertIn("Add `T1135 OLD.TO USA` (foreign property; OLD.TO: "
                      "NEWW.US before its ticker change) or `T1135 OLD.TO "
                      "CA` (not foreign property) to ticker.map", brief)
        settled_by(self, rows, rep, "NEWW.US")

    @rule(R.CA_RPT_02)
    def test_an_isin_that_agrees_with_its_own_listing_only(self):
        # CA on the Toronto symbol; the US symbol before it has none.
        rows = [buy("OLDW.US", COST), ren("OLDW.US", "NEW.TO", "2025-04-01"),
                div("NEW.TO", "CA")]
        rep = report(rows)
        self.assertEqual(rep["canadian_issuer_symbols"], [])
        self.assertEqual(rep["foreign_issuer_symbols"], [])
        self.assertEqual(rep["max_total_cost"], COST)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIsNone(rep["filing_required"])
        self.assertEqual(conflict(rep, "NEW.TO")["lines"], {
            "foreign": ["T1135 OLDW.US USA"],
            "not_foreign": ["T1135 OLDW.US CA"]})
        self.assertIn("1 symbol(s) carry an ISIN that disagrees with a "
                      "listing they had before or after a ticker change: "
                      "NEW.TO (CAN; NEW.TO counted under CAN, OLDW.US "
                      "counted under USA)", rep["_stderr"])
        self.assertIn("NEW.TO may not be foreign property (ISIN and "
                      "listings disagree through its ticker change): "
                      "without it the maximum is 0.00 CAD", texts(rep)[0])
        settled_by(self, rows, rep, "NEW.TO")


class TestLongerChains(unittest.TestCase):
    @rule(R.CA_RPT_02)
    def test_two_ticker_changes(self):
        rows = [buy("OLD.TO", COST), sell("OLD.TO", 112500.0, "2025-03-03", 90),
                ren("OLD.TO", "MID.TO", "2025-04-01"),
                ren("MID.TO", "NEW.TO", "2025-05-01"), div("NEW.TO", "AU")]
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_high"], COST)
        self.assertEqual(conflict(rep, "NEW.TO")["lines"]["foreign"],
                         ["T1135 OLD.TO AUS"])
        settled_by(self, rows, rep, "NEW.TO")
        # A line for the symbol in the middle is one before the ISIN's:
        # it decides the last symbol too.
        mid = report(rows, {"MID.TO": None})
        self.assertEqual(mid["classification_conflicts"], [])

    @rule(R.CA_RPT_02)
    def test_a_merger_has_two_first_symbols(self):
        rows = [buy("QZA.TO", 60000.0), buy("QZB.TO", 60000.0),
                ren("QZA.TO", "NEW.TO", "2025-04-01"),
                ren("QZB.TO", "NEW.TO", "2025-04-01"), div("NEW.TO", "AU")]
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_high"], 120000.0)
        self.assertEqual(conflict(rep, "NEW.TO")["lines"], {
            "foreign": ["T1135 QZA.TO AUS", "T1135 QZB.TO AUS"],
            "not_foreign": ["T1135 QZA.TO CA", "T1135 QZB.TO CA"]})
        self.assertIn("Add `T1135 QZA.TO AUS` and `T1135 QZB.TO AUS` "
                      "(foreign property; QZA.TO, QZB.TO: NEW.TO before "
                      "its ticker change) or `T1135 QZA.TO CA` and `T1135 "
                      "QZB.TO CA` (not foreign property) to ticker.map",
                      texts(rep)[0])
        settled_by(self, rows, rep, "NEW.TO")

    @rule(R.CA_RPT_02)
    def test_a_ticker_bought_again_after_its_change(self):
        # OLD.TO becomes NEW.TO; a later OLD.TO is bought. The line is
        # on OLD.TO, the security's first symbol, and counts both (the
        # cautious side: the report names what the line gives).
        rows = [buy("OLD.TO", 60000.0), ren("OLD.TO", "NEW.TO", "2025-04-01"),
                div("NEW.TO", "AU"), buy("OLD.TO", 60000.0, "2025-07-02")]
        rep = report(rows)
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["max_total_cost_high"], 120000.0)
        settled_by(self, rows, rep, "NEW.TO")


class TestACostTheBooksDoNotGive(unittest.TestCase):
    """A foreign property counted at an unknown ACB understates the
    total: below the threshold is then no verdict either."""

    ROWS = [unknown("FGN.TO"), div("FGN.TO", "AU")]

    @rule(R.CA_RPT_12)
    def test_the_line_named_leads_to_not_settled_not_to_a_no(self):
        open_ = report(self.ROWS)
        self.assertEqual(open_["classification_status"], "unresolved")
        rep = report(self.ROWS, as_overrides(
            conflict(open_, "FGN.TO")["lines"]["foreign"]))
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["unknown_acb_symbols"], ["FGN.TO"])
        self.assertIs(rep["total_settled"], True)
        self.assertIsNone(rep["filing_required"])
        self.assertIsNone(rep["simplified_method_available"])
        brief, full = texts(rep)
        for text in (brief, full):
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
        rep = report(book("QZW.US", COST, "CA"), cash=self.CASH)
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


class TestACanadianIsinOnAForeignListing(unittest.TestCase):
    """The mirror: QZW.US, a Canadian ISIN, counted as foreign."""

    @rule(R.CA_RPT_02)
    def test_required_only_by_it_is_open(self):
        rep = report(book("QZW.US", COST, "CA"))
        self.assertEqual(rep["max_total_cost"], COST)
        self.assertIs(rep["over_threshold_as_counted"], True)
        self.assertIsNone(rep["filing_required"])
        self.assertIsNone(rep["simplified_method_available"])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_unresolved_symbols"],
                         ["QZW.US"])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, COST))
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "QZW.US", "counted_as": "USA",
             "issuer_country": "CAN", "isin_country_code": "CA",
             "affects": "total", "held_in_year": True,
             "symbols": {"QZW.US": "USA"},
             "lines": {"foreign": ["T1135 QZW.US USA"],
                       "not_foreign": ["T1135 QZW.US CA"]}}])
        for text in texts(rep):
            self.assertNotIn("FILING REQUIRED", text)
            self.assertIn(f"=> NOT RESOLVED: over the {THR} threshold as "
                          "counted, but QZW.US may not be foreign property "
                          "(a Canadian ISIN on a foreign listing): without "
                          "it the maximum is 0.00 CAD", text)
            self.assertIn("Add `T1135 QZW.US CA` (not foreign property) or "
                          "`T1135 QZW.US USA` (foreign property) to "
                          "ticker.map, then `tjs run` and `tjs t1135`.",
                          text)

    @rule(R.CA_RPT_02)
    def test_the_lines_named_give_the_figures_named(self):
        rows = book("QZW.US", COST, "CA")
        open_ = report(rows)
        lines = conflict(open_, "QZW.US")["lines"]
        rep = report(rows, as_overrides(lines["not_foreign"]))
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["max_total_cost"], open_["max_total_cost_low"])
        self.assertIs(rep["filing_required"], False)
        self.assertIn("no T1135 required", texts(rep)[0])
        rep = report(rows, as_overrides(lines["foreign"]))
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertEqual(rep["max_total_cost"], open_["max_total_cost_high"])
        self.assertIs(rep["filing_required"], True)
        self.assertIn("FILING REQUIRED", texts(rep)[0])

    @rule(R.CA_RPT_02)
    def test_required_without_it_holds(self):
        rep = report(book("QZW.US", SMALL, "CA",
                          more=[buy("QZX.US", COST, "2025-02-04")]))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], True)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (COST, COST + SMALL))
        brief = texts(rep)[0]
        self.assertIn("FILING REQUIRED", brief)
        self.assertIn("Counting QZW.US the other way", brief)

    @rule(R.CA_RPT_02)
    def test_below_with_it_holds(self):
        rep = report(book("QZW.US", SMALL, "CA"))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], False)
        self.assertIn("no T1135 required", texts(rep)[0])

    @rule(R.CA_RPT_15)
    def test_the_part_that_depends_on_it_is_open(self):
        # Over 250,000 as counted; without QZW.US, under it. pii-ok
        rep = report(book("QZW.US", 60000.0, "CA",
                          more=[buy("QZX.US", 200000.0, "2025-02-04")]))
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertIs(rep["filing_required"], True)
        self.assertIsNone(rep["simplified_method_available"])
        self.assertIn("Part A or Part B open (it depends on how QZW.US is "
                      "counted)", texts(rep)[0])

    @rule(R.CA_RPT_02)
    def test_both_kinds_at_once(self):
        # As counted QZW.US alone is over; the lowest reading drops it,
        # the highest adds FGN.TO.
        rep = report(book("QZW.US", COST, "CA")
                     + book("FGN.TO", SMALL, "AU"))
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertEqual(rep["classification_unresolved_symbols"],
                         ["QZW.US"])
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (0.0, COST + SMALL))

    @rule(R.CA_RPT_20)
    def test_over_with_cash_of_unknown_cost_gives_no_firm_figure(self):
        rep = report(book("QZW.US", COST, "CA"),
                     cash=cash_doc(0.0, unknown_cost=True))
        self.assertIs(rep["total_settled"], False)
        self.assertIsNone(rep["filing_required"])
        for text in texts(rep):
            self.assertNotIn("FILING REQUIRED", text)
            self.assertIn("without it the maximum is at least 0.00 CAD; "
                          "NOT SETTLED either: some cash's cost is unknown "
                          "— tjs fx-cash --ledger v2", text)

    @rule(R.CA_RPT_12)
    def test_another_cost_unknown_gives_no_firm_figure(self):
        rep = report(book("QZW.US", COST, "CA") + [unknown("QZX.US")])
        self.assertEqual(rep["classification_status"], "unresolved")
        self.assertIn("without it the maximum is at least 0.00 CAD",
                      texts(rep)[0])

    @rule(R.CA_RPT_02)
    def test_a_custodian_abroad_counts_it_either_way(self):
        rep = report(book("QZW.US", COST, "CA"), custody={"margin": "USA"})
        self.assertEqual(rep["classification_status"], "verdict_holds")
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (COST, COST))
        self.assertIs(rep["filing_required"], True)


class TestForeignUnderEitherCountry(unittest.TestCase):
    """TJ-4958: FGN.US, an Irish ISIN: foreign either way."""

    @rule(R.CA_RPT_02)
    def test_it_is_warned_about_and_to_review(self):
        rep = report(book("FGN.US", COST, "IE"))
        self.assertIn("1 symbol(s) counted under one foreign country carry "
                      "another's ISIN: FGN.US (IRL, counted under USA)",
                      rep["_stderr"])
        self.assertIn("Add `T1135 FGN.US IRL` to ticker.map once "
                      "confirmed, or `T1135 FGN.US USA` to keep it.",
                      rep["_stderr"])
        self.assertEqual(rep["review_symbols"], ["FGN.US"])
        self.assertEqual(rep["classification_conflicts"], [
            {"symbol": "FGN.US", "counted_as": "USA",
             "issuer_country": "IRL", "isin_country_code": "IE",
             "affects": "country", "held_in_year": True,
             "lines": {"as_counted": ["T1135 FGN.US USA"],
                       "other": ["T1135 FGN.US IRL"]}}])
        # The total is the same under either country: a verdict.
        self.assertEqual(rep["classification_status"], "resolved")
        self.assertIs(rep["filing_required"], True)
        self.assertEqual((rep["max_total_cost_low"],
                          rep["max_total_cost_high"]), (COST, COST))
        self.assertEqual(rep["properties"][0]["country"], "USA")
        brief, full = texts(rep)
        self.assertIn("FILING REQUIRED", brief)
        acts = _out.act_lines(render_brief(rep))
        self.assertTrue(any("FGN.US (IRL)" in a and "another country's "
                            "ISIN than counted" in a for a in acts), acts)
        self.assertIn("FGN.US: IRL, counted under USA", full)

    @rule(R.CA_RPT_02)
    def test_a_line_for_it_settles_it(self):
        lines = conflict(report(book("FGN.US", COST, "IE")),
                         "FGN.US")["lines"]
        for which, country in (("other", "IRL"), ("as_counted", "USA")):
            with self.subTest(which=which):
                rep = report(book("FGN.US", COST, "IE"),
                             as_overrides(lines[which]))
                self.assertEqual(rep["classification_conflicts"], [])
                self.assertEqual(rep["review_symbols"], [])
                self.assertEqual(rep["properties"][0]["country"], country)
                self.assertEqual(rep["max_total_cost"], COST)
                self.assertNotIn("ISIN", rep["_stderr"])

    @rule(R.CA_RPT_02)
    def test_the_listings_own_country_is_not_named(self):
        rep = report(book("FGN.US", COST, "US"))
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["review_symbols"], [])

    @rule(R.CA_RPT_02)
    def test_the_issuers_country_from_tobase_map_is_compared(self):
        rep = report(book("FGN.US", COST, "IE"),
                     issuer_countries={"FGN.US": "IRL"})
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertEqual(rep["properties"][0]["country"], "IRL")

    @rule(R.CA_RPT_02)
    def test_a_canadian_listing_tobase_map_counts_abroad_is_worded_truly(
            self):
        # tobase.map gives FGN.TO's issuer a country: no "foreign
        # listing" is claimed of a .TO symbol.
        rep = report(book("FGN.TO", SMALL, "IE"),
                     issuer_countries={"FGN.TO": "USA"})
        self.assertEqual(conflict(rep, "FGN.TO")["affects"], "country")
        for text in (*texts(rep), rep["_stderr"]):
            self.assertNotIn("on a foreign listing", text)
        self.assertIn("counted under one foreign country carry another's "
                      "ISIN: FGN.TO (IRL, counted under USA)",
                      rep["_stderr"])

    @rule(R.CA_RPT_02)
    def test_a_holding_with_no_row_in_the_year_is_not_named(self):
        rows = [buy("FGN.US", COST, "2023-02-03"),
                div("FGN.US", "IE", "2023-06-17"),
                sell("FGN.US", COST, "2023-09-01")]
        rep = report(rows)
        self.assertEqual(rep["classification_conflicts"], [])
        self.assertNotIn("ISIN", rep["_stderr"])


class TestEveryLineNamedIsOneTickerMapTakes(unittest.TestCase):
    BOOKS = {
        "up": book("FGN.TO", COST, "AU"),
        "down": book("QZW.US", COST, "CA"),
        "holds": book("FGN.TO", SMALL, "AU"),
        "renamed": TestATickerChange.UP,
        "renamed down": TestATickerChange.DOWN,
        "third": book("FGN.US", SMALL, "IE"),
        "no country": book("FGN.TO", COST, "XS"),
        "no country, third": book("FGN.US", SMALL, "EU"),
        "several": (book("FGN.TO", 40000.0, "AU")
                    + book("QZA.TO", 40000.0, "US")
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
                self.assertTrue(listed, name)
                if "no country" not in name:
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
                {"transactions": book("FGN.TO", COST, "AU")}))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = main([str(base), "--year", "2025", "--json"])
        self.assertEqual(rc, 0, err.getvalue())
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["classification_status"], "unresolved")
        self.assertEqual(doc["classification_unresolved_symbols"],
                         ["FGN.TO"])
        self.assertIsNone(doc["filing_required"])
        self.assertIsNone(doc["simplified_method_available"])
        self.assertIs(doc["over_threshold_as_counted"], False)
        self.assertIs(doc["total_settled"], True)
        self.assertEqual(doc["max_total_cost"], 0.0)
        self.assertEqual(doc["max_total_cost_high"], COST)
        self.assertEqual(doc["classification_conflicts"][0]["lines"],
                         {"foreign": ["T1135 FGN.TO AUS"],
                          "not_foreign": ["T1135 FGN.TO CA"]})


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
                (book("FGN.TO", COST, "AU"),
                 "FGN.TO may be foreign property (ISIN and listing "
                 "disagree), and the result (below the threshold as "
                 "counted) depends on it — add the `T1135` line `tjs "
                 "t1135` names to ticker.map, then `tjs run`"),
                (book("QZW.US", COST, "CA"),
                 "QZW.US may not be foreign property (ISIN and listing "
                 "disagree), and the result (over the threshold as "
                 "counted) depends on it")):
            with self.subTest(symbol=rows[0]["symbol"]):
                res = self.step(report(rows))
                self.assertEqual(res.status, "attention")
                self.assertIn("the T1135 test is not resolved: " + words,
                              res.detail)
                self.assertEqual(res.cmd, "tjs t1135")

    @rule(R.CA_RPT_02)
    def test_several_symbols_read_as_several(self):
        res = self.step(report(book("FGN.TO", 40000.0, "AU")
                               + book("QZA.TO", 40000.0, "US")
                               + book("QZB.TO", 40000.0, "IE")))
        self.assertEqual(res.status, "attention")
        self.assertIn("FGN.TO, QZA.TO +1 more may be foreign property (ISIN "
                      "and listing disagree)", res.detail)
        self.assertIn("add the `T1135` lines `tjs t1135` names",
                      res.detail)

    @rule(R.CA_RPT_02)
    def test_it_stands_when_the_verdict_holds(self):
        self.assertEqual(self.step(report(book("FGN.TO", SMALL, "AU"))).status,
                         "done")
        rep = report(book("QZW.US", SMALL, "CA",
                          more=[buy("QZX.US", COST, "2025-02-04")]))
        self.assertEqual(self.step(rep).status, "manual")

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
