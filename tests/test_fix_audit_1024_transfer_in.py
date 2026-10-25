"""Audit 2026-10-24 (pass B), group transfer-in: lib/transfer_in decides
what a taxable account's books take as the cost of shares transferred
in, so a wrong reconciliation changes filed figures. Where the right
booking is uncertain the module now stops for reconciliation (an
ATTENTION naming the arrival) instead of booking a guess.

B03  a listing journal with no pair id cancelled two securities that
     shared a quantity and a day (ZZQ out, ZZR in): now only two
     listings of one root cancel; the other in leg stays an arrival.
B04  the coverage walk skipped .tt SPLIT lines: a pre-split purchase
     did not cover the post-split units that arrived.
B40  a stated book value of 0 was read as "no book value" and said so:
     it is kept out as no cost, said as a stated 0 (never a $0 lot).
B41  coverage was all-or-nothing: 10 .tt units and a 20-unit arrival
     made 30 in the books. Now the arrival is `partly_covered`: NOT
     booked, said as ATTENTION, until the rest is declared.
B42  a .tt sale later on the arrival day un-covered the arrival: a
     same-day sale consumes only when its time is earlier.
B50  a same-security transfer-out years later cancelled the arrival:
     the two legs of one move are within OWN_MOVE_DAYS days.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, settings_for
from _style import CapturedWidth

from taxjson.lib import transfer_in as TI

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tx(d, q, sym="ZZQ.US", action="BUYSELL", time="10:00:00", **kw):
    """One converted row (the audit's probe shape)."""
    return dict(date=d, date_settle=d, time=time, account="margin",
                symbol=sym, action=action, quantity=q, net_amount=0,
                currency="USD", **kw)


def _leg(d, q, sym="ZZQ.US", acct="margin", broker="generic", **kw):
    """One transfer sidecar row as transfer_in.arrivals takes it."""
    return (acct, broker, _tx(d, q, sym=sym, action="TRANSFER", **kw))


def _shift(day: str, days: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


@rule("CA-ACB-TRANSFER-BV")
class TestB03ListingJournalNeedsOneRoot(unittest.TestCase):
    """B03: Transfer-in reconciliation cancels unrelated securities
    solely by quantity and date."""

    def test_b03_two_securities_sharing_a_quantity_and_a_day_do_not_cancel(self):
        rows = [_leg("2026-01-01", -10, "ZZQ.US"),
                _leg("2026-01-02", 10, "ZZR.US")]
        found = TI.arrivals(rows)
        self.assertEqual([(a.symbol, a.quantity, a.status) for a in found],
                         [("ZZR.US", 10.0, "no_cost")])

    def test_b03_two_listings_of_one_root_still_cancel(self):
        # The listing journal the fallback is for: a US-dollar and a
        # Canadian-dollar line of one security, no ticker.map rule.
        rows = [_leg("2026-01-01", -10, "ZZQ.TO"),
                _leg("2026-01-02", 10, "ZZQ.US", book_value=100)]
        self.assertEqual(TI.arrivals(rows), [])
        # A USD unit class on a Canadian venue is the same root too.
        rows = [_leg("2026-01-01", -7, "ZZC.U.TO"),
                _leg("2026-01-01", 7, "ZZC.TO", book_value=70)]
        self.assertEqual(TI.arrivals(rows), [])
        # A class suffix is the root's own: two classes are two
        # securities (cross_listings.listing_root).
        rows = [_leg("2026-01-01", -7, "ZZC.A.TO"),
                _leg("2026-01-01", 7, "ZZC.B.TO")]
        self.assertEqual([a.symbol for a in TI.arrivals(rows)], ["ZZC.B.TO"])


@rule("CA-ACB-TRANSFER-BV")
class TestB04SplitsInTheTtHistory(unittest.TestCase):
    """B04: Transfer coverage ignores splits in hand-entered acquisition
    history."""

    def test_b04_a_tt_split_before_the_arrival_scales_the_purchase(self):
        found = TI.arrivals([_leg("2026-01-03", 20)])
        TI.mark_covered(found, {"margin": [_tx("2026-01-01", 10),
                                           _tx("2026-01-02", 2,
                                               action="SPLIT")]})
        self.assertTrue(found[0].covered)
        self.assertEqual(found[0].status, "covered")

    def test_b04_a_split_after_the_arrival_is_not_applied(self):
        found = TI.arrivals([_leg("2026-01-03", 20, book_value=200)])
        TI.mark_covered(found, {"margin": [_tx("2026-01-01", 10),
                                           _tx("2026-01-04", 2,
                                               action="SPLIT")]})
        self.assertFalse(found[0].covered)
        # 10 of the 20 are accounted for: partly covered, not booked.
        self.assertEqual((found[0].status, found[0].covered_units),
                         ("partly_covered", 10.0))
        self.assertEqual(TI.booked_rows(found), [])

    def test_b04_a_renaming_split_moves_the_units_to_the_new_symbol(self):
        # SPLIT old new ratio: the purchase under the old ticker, the
        # arrival under the new one.
        found = TI.arrivals([_leg("2026-01-03", 30, "ZZNEW.US")])
        TI.mark_covered(found, {"margin": [
            _tx("2026-01-01", 10, "ZZOLD.US"),
            dict(_tx("2026-01-02", 3, "ZZOLD.US", action="SPLIT"),
                 symbol_new="ZZNEW.US")]})
        self.assertTrue(found[0].covered)

    def test_b04_a_split_between_two_arrivals_scales_the_first_ones_units(self):
        # 10 .tt units cover a 10-unit arrival; a 2-for-1 split later
        # doubles THOSE units; a later 20-unit arrival is new shares.
        found = TI.arrivals([_leg("2025-01-10", 10, book_value=100),
                             _leg("2025-07-01", 20, book_value=300)])
        TI.mark_covered(found, {"margin": [_tx("2025-01-01", 10),
                                           _tx("2025-06-01", 2,
                                               action="SPLIT")]})
        self.assertEqual([(a.date, a.status) for a in found],
                         [("2025-01-10", "covered"),
                          ("2025-07-01", "book_value")])
        self.assertEqual([r["quantity"] for r in TI.booked_rows(found)],
                         [20.0])


@rule("CA-ACB-TRANSFER-BV")
class TestB40StatedZeroBookValue(unittest.TestCase):
    """B40: Transfer booking treats an explicitly stated zero basis as
    missing basis. PARTLY: a stated 0 is distinguished from a row that
    states none and said as what it is, but it is never booked as a $0
    lot (a broker's 0 is usually its placeholder for a cost it was not
    given): the shares stay out until the purchase is declared."""

    def test_b40_a_stated_zero_is_read_as_stated_and_never_booked(self):
        self.assertEqual(TI.stated_book_value({"book_value": 0}), 0.0)
        self.assertEqual(TI.stated_book_value({"book_value": "0.00"}), 0.0)
        self.assertIsNone(TI.stated_book_value({}))
        self.assertIsNone(TI.stated_book_value({"book_value": ""}))
        self.assertIsNone(TI.stated_book_value({"book_value": None}))
        self.assertIsNone(TI.stated_book_value({"book_value": -5}))
        self.assertIsNone(TI.stated_book_value({"book_value": "nan"}))
        self.assertEqual(TI.stated_book_value({"book_value": "12.5"}), 12.5)
        found = TI.arrivals([_leg("2026-01-03", 10, book_value=0)])
        self.assertEqual([(a.status, a.book_value, a.zero_book_value)
                          for a in found], [("no_cost", 0.0, True)])
        self.assertEqual(TI.booked_rows(found), [])
        lines = TI.attention_lines(found, "canada")
        self.assertEqual(len(lines), 1)
        self.assertIn("have NO cost in the books (10 ZZQ.US (2026-01-03))",
                      lines[0])
        self.assertIn("the row states a book value of 0", lines[0])
        self.assertIn("never booked as a $0 lot", lines[0])
        self.assertNotIn("states no book value", lines[0])
        # The console's one-line form is the no-cost one.
        from taxjson.lib.stage_msg import console_lines
        shown = console_lines("warning: ATTENTION: " + lines[0], width_=120,
                              concise=True)
        self.assertEqual(shown[0], "Warning: Transfer-in: margin: 1 with NO "
                                   "cost, kept out of the books (ZZQ.US) — "
                                   "`taxjson transfers`")

    def test_b40_a_row_stating_none_keeps_its_own_line(self):
        found = TI.arrivals([_leg("2026-01-03", 10, book_value=0),
                             _leg("2026-01-04", 5, "ZZR.US")])
        lines = TI.attention_lines(found, "canada")
        self.assertEqual(len(lines), 2)
        self.assertIn("(5 ZZR.US (2026-01-04)): the row states no book "
                      "value", lines[0])
        self.assertIn("(10 ZZQ.US (2026-01-03)): the row states a book "
                      "value of 0", lines[1])


@rule("CA-ACB-TRANSFER-BV")
class TestB41PartialCoverageIsNotBooked(unittest.TestCase):
    """B41: Partial acquisition history causes transfer booking to
    duplicate the known shares. PARTLY: the audit expected the rest (10)
    booked; the arrival is instead NOT booked and said as ATTENTION,
    since booking a pro-rata rest would guess which units the .tt lines
    are. The books hold the 10 known units, never 30."""

    def test_b41_partly_covered_arrival_is_not_booked_and_is_said(self):
        found = TI.arrivals([_leg("2026-01-03", 20, book_value=200)])
        TI.mark_covered(found, {"margin": [_tx("2026-01-01", 10)]})
        a = found[0]
        self.assertEqual((a.covered, a.status, a.covered_units,
                          a.book_value), (False, "partly_covered", 10.0,
                                          200.0))
        booked = TI.booked_rows(found)
        self.assertEqual(booked, [])
        self.assertEqual(10 + sum(r["quantity"] for r in booked), 10)
        lines = TI.attention_lines(found, "canada")
        self.assertEqual(len(lines), 1)
        self.assertIn("only PARTLY covered by your .tt purchases (20 ZZQ.US "
                      "(2026-01-03, 10 of 20 covered)): NOT booked",
                      lines[0])
        self.assertIn("Add the purchase of those units as a .tt BUYSELL",
                      lines[0])
        self.assertIn("or of all the transferred units, when the .tt "
                      "purchases are another holding", lines[0])
        from taxjson.lib.stage_msg import console_lines
        shown = console_lines("warning: ATTENTION: " + lines[0], width_=120,
                              concise=True)
        self.assertEqual(shown[0], "Warning: Transfer-in: margin: 1 only "
                                   "PARTLY covered by .tt purchases, NOT "
                                   "booked (ZZQ.US) — `taxjson transfers`")
        # The rest declared: covered, nothing booked, nothing said.
        TI.mark_covered(found, {"margin": [_tx("2026-01-01", 10),
                                           _tx("2026-01-02", 10)]})
        self.assertEqual((found[0].status, found[0].covered_units),
                         ("covered", 0.0))
        self.assertEqual(TI.attention_lines(found, "canada"), [])

    def test_b41_partial_units_are_consumed_once(self):
        # 15 .tt units: the first arrival (10) is covered, the second
        # (20) gets the 5 left — partly covered, not booked.
        found = TI.arrivals([_leg("2026-01-03", 10, book_value=100),
                             _leg("2026-02-03", 20, book_value=200)])
        TI.mark_covered(found, {"margin": [_tx("2026-01-01", 15)]})
        self.assertEqual([(a.status, a.covered_units) for a in found],
                         [("covered", 0.0), ("partly_covered", 5.0)])
        self.assertEqual(TI.booked_rows(found), [])

    def test_b41_the_run_says_it_and_books_nothing(self):
        # End to end: `taxjson run` and `taxjson transfers` on a
        # Questrade transfer row with a stated book value and a .tt
        # purchase of half the units.
        qh = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Activity Type,Account #,Account Type\n")
        row = ("2025-03-03,2025-03-03,TF6,QZX.TO,QZX CORP TRANSFER BOOK "
               "VALUE 900.00,20,0.00,0.00,0.00,0.00,CAD,Transfers,"
               "99900001,Margin\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                settings_for("canada", year=2025, source_currencies=[])
                + '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "qt.csv").write_text(qh + row)
            (root / "inputs" / "margin" / "start.tt").write_text(
                "BUYSELL 2024-01-10 09:30:00 QZX.TO 10 CAD 40 400 0\n")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("transfer-in: margin: 1 transfer-in(s) from "
                          "outside your books only PARTLY covered by your "
                          ".tt purchases (20 QZX.TO (2025-03-03, 10 of 20 "
                          "covered)): NOT booked", r.stdout)
            self.assertFalse((root / "work" /
                              "margin_transfer_costs.json").exists())
            # The closing summary counts it as kept out with no cost.
            self.assertIn("kept out with no cost", r.stdout)
            j = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual([t["arrival"] for t in j["transfers"]],
                             ["PARTLY covered"])
            t = cli(root, "transfers").stdout
            self.assertIn("PARTLY_covered", t)
            self.assertIn("only partly covered by .tt purchases, NOT "
                          "booked (QZX.TO)", t)
            # The other units declared: covered, quiet.
            (root / "inputs" / "margin" / "start.tt").write_text(
                "BUYSELL 2024-01-10 09:30:00 QZX.TO 10 CAD 40 400 0\n"
                "BUYSELL 2024-02-10 09:30:00 QZX.TO 10 CAD 45 450 0\n")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("transfer-in:", r.stdout)
            j = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual([t["arrival"] for t in j["transfers"]],
                             [".tt covers"])


@rule("CA-ACB-TRANSFER-BV")
class TestB42SameDaySaleAfterTheArrival(unittest.TestCase):
    """B42: Transfer coverage subtracts a sale occurring later on the
    arrival day."""

    def test_b42_a_sale_later_on_the_arrival_day_consumes_nothing(self):
        found = TI.arrivals([_leg("2026-01-03", 10, book_value=100)])
        TI.mark_covered(found, {"margin": [
            _tx("2026-01-01", 10), _tx("2026-01-03", -10, time="15:00:00")]})
        self.assertTrue(found[0].covered)
        self.assertEqual(TI.booked_rows(found), [])

    def test_b42_a_sale_earlier_on_the_arrival_day_consumes(self):
        found = TI.arrivals([_leg("2026-01-03", 10, book_value=100)])
        TI.mark_covered(found, {"margin": [
            _tx("2026-01-01", 10), _tx("2026-01-03", -10, time="08:00:00")]})
        self.assertFalse(found[0].covered)
        self.assertEqual(found[0].status, "book_value")

    def test_b42_a_sale_the_day_before_consumes_and_a_time_is_needed_to_order_a_day(self):
        found = TI.arrivals([_leg("2026-01-03", 10, book_value=100)])
        TI.mark_covered(found, {"margin": [
            _tx("2026-01-01", 10), _tx("2026-01-02", -10, time="15:00:00")]})
        self.assertFalse(found[0].covered)
        # The arrival's row carries no time: a same-day sale cannot be
        # placed before it, so it is taken as the arrived shares' sale.
        leg = _leg("2026-01-03", 10, book_value=100)
        leg[2].pop("time")
        found = TI.arrivals([leg])
        TI.mark_covered(found, {"margin": [
            _tx("2026-01-01", 10), _tx("2026-01-03", -10, time="08:00:00")]})
        self.assertTrue(found[0].covered)

    def test_b42_a_same_day_purchase_covers_whatever_its_time(self):
        # The documented fix is a purchase "dated on or before the
        # transfer": its time never matters.
        found = TI.arrivals([_leg("2026-01-03", 10, book_value=100)])
        TI.mark_covered(found, {"margin": [
            _tx("2026-01-03", 10, time="16:00:00")]})
        self.assertTrue(found[0].covered)


@rule("CA-ACB-TRANSFER-BV")
class TestB50DepartureYearsLaterIsNotTheSource(unittest.TestCase):
    """B50: A future external departure erases the original transfer-in
    acquisition."""

    def test_b50_a_departure_years_later_does_not_cancel_the_arrival(self):
        found = TI.arrivals([_leg("2020-01-02", 10, book_value=100),
                             _leg("2026-01-02", -10)])
        self.assertEqual([(a.status, a.quantity, a.date) for a in found],
                         [("book_value", 10.0, "2020-01-02")])
        booked = TI.booked_rows(found)
        self.assertEqual(len(booked), 1)
        self.assertEqual((booked[0]["date"], booked[0]["quantity"],
                          booked[0]["net_amount"]), ("2020-01-02", 10.0, 100.0))

    def test_b50_the_two_legs_of_one_move_cancel_within_the_window(self):
        self.assertEqual(TI.OWN_MOVE_DAYS, 10)
        day = "2025-03-02"
        for gap in (0, 1, 6, TI.OWN_MOVE_DAYS):
            for sign in (1, -1):
                rows = [_leg(day, -10, acct="qa"),
                        _leg(_shift(day, sign * gap), 10, acct="qb",
                             book_value=100)]
                self.assertEqual(TI.arrivals(rows), [], (gap, sign))
        for gap, sign in ((TI.OWN_MOVE_DAYS + 1, -1), (400, 1), (400, -1)):
            rows = [_leg(day, -10, acct="qa"),
                    _leg(_shift(day, sign * gap), 10, acct="qb",
                         book_value=100)]
            self.assertEqual([a.account for a in TI.arrivals(rows)],
                             ["qb"], (gap, sign))
        # Audit 2026-10-25 diff-engine#4: an in leg 11 days AFTER the
        # out leg, nothing else a candidate, is a slower move of your
        # own (transfer_in.slow_pairs; test_fix_audit_1025_transfers).
        rows = [_leg(day, -10, acct="qa"),
                _leg(_shift(day, TI.OWN_MOVE_DAYS + 1), 10, acct="qb",
                     book_value=100)]
        self.assertEqual(TI.arrivals(rows), [])


if __name__ == "__main__":
    unittest.main()
