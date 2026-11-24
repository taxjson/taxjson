"""`tjs div-estimate`: a payer whose own history cannot settle its
schedule is projected quarterly, shown as assumed, until the books
contradict it (the owner's request of 2026-11-22; backlog TJ-4976).

Cannot settle: one to three payments, the latest in the last 12 months,
each alone on a quarterly date counted back from the latest, where the
frequency reading found no grid or a semi-annual one resting on a single
six-month gap. Contradicted: a quarterly date with no payment of any kind
in the books, between the payments or overdue after the latest, on which
the books show the shares held; or an overdue date nothing explains. A
date past the end of an account's books is not checked, and the row says
so: one such date is a missing payment (late), two drop the assumption.
A history that shows a schedule is read as before.

Synthetic payers only (made-up tickers, amounts under 1,000). Planning
numbers (CA-PLAN-06 / US-PLAN-07)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from tax_rules import rule
from taxjson.lib import div_estimate as DE
from taxjson.lib.json_contract import SCHEMAS
from taxjson.rules import ids as R

from _qa_project import flat, project as qa_project, tj
from _support.view_div_estimate import _div, _est, _pos, _trade

_ASSUMED_ONE = ("assumed quarterly: 1 payment(s) in the books, none "
                "contradicts it (over-projected if it pays less often, or "
                "if its only payment was a special)")
_LATE = "1 expected payment not in the books"
_ASSUMED_TWO = ("assumed quarterly: 2 payment(s) in the books, none "
                "contradicts it (over-projected if it pays less often)")


def _row(rows, sym="ZZA.TO", qty=100, books_to="2026-06-30", **kw):
    """The estimate's row of `sym`. `books_to`: the margin account's
    books run to that date (a trade of another security on it), so they
    can speak for the quarterly dates before it; None: they end at the
    last of `rows`."""
    if books_to:
        rows = rows + [_trade(books_to, "ZZX.TO", 1)]
    return _est(rows, [_pos(sym, qty)], **kw)["rows"][0]


def _divs(dates, sym="ZZA.TO", qty=100, per=0.5, **kw):
    return [("margin", _div(d, sym, qty, per, **kw)) for d in dates]


class TestASinglePayment(unittest.TestCase):

    @rule(R.CA_PLAN_06)
    def test_four_payments_in_twelve_months_at_the_latest_amount(self):
        rows = [_trade("2026-05-01", "ZZA.TO", 100)]
        rows += _divs(["2026-06-10"])
        r = _row(rows)                                  # as of 2026-07-01
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", True))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["flags"], [_ASSUMED_ONE])
        self.assertEqual([(p["pay_date"], p["amount"])
                          for p in r["projected"]],
                         [("2026-09-10", 50.0), ("2026-12-10", 50.0),
                          ("2027-03-10", 50.0), ("2027-06-10", 50.0)])
        self.assertEqual(r["window_amount_base"], 200.0)   # it was 50.00
        self.assertEqual(r["rest_of_year_base"], 100.0)
        self.assertEqual((r["next_pay_date"], r["next_amount_base"]),
                         ("2026-09-10", 50.0))
        self.assertIsNone(r["median_gap_days"])
        self.assertEqual(r["payments"], 1)

    def test_no_trade_row_in_the_books_contradicts_nothing(self):
        r = _row(_divs(["2026-06-10"]))
        self.assertTrue(r["frequency_assumed"])
        self.assertEqual(r["window_amount_base"], 200.0)

    def test_a_quarterly_date_passed_with_the_shares_held_contradicts(self):
        """Paid in February, held since December, nothing in May: the
        payer skipped a quarter. One payment, pro rata, as before."""
        rows = [_trade("2025-12-01", "ZZA.TO", 100)]
        rows += _divs(["2026-02-10"])
        r = _row(rows)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("1 payment", False))
        self.assertEqual(r["status"], "irregular")
        self.assertEqual(r["flags"], ["single payment"])
        self.assertEqual(r["projected"], [])
        self.assertEqual(r["window_amount_base"], 50.0)

    def test_a_date_inside_the_grace_is_still_to_come(self):
        """11 days after the quarterly date: not overdue yet (15 days)."""
        rows = [_trade("2025-12-01", "ZZA.TO", 100)]
        rows += _divs(["2026-03-20"])
        r = _row(rows)
        self.assertTrue(r["frequency_assumed"])
        self.assertEqual((r["status"], r["next_pay_date"]),
                         ("ok", "2026-06-20"))

    def test_an_overdue_date_nothing_explains_contradicts(self):
        """No trade row of the payer, in books that run past the May
        date: nothing explains the missing payment, so the payer is not
        assumed quarterly. An assumed row never carries "expected
        payment(s) not in the books" (it did: late, then stopped at
        0.00 beside "none contradicts it")."""
        for paid in ("2026-02-10", "2025-11-10", "2025-07-02"):
            with self.subTest(paid=paid):
                r = _row(_divs([paid]))
                self.assertEqual((r["frequency"], r["frequency_assumed"]),
                                 ("1 payment", False))
                self.assertEqual((r["status"], r["flags"]),
                                 ("irregular", ["single payment"]))
                self.assertEqual(r["window_amount_base"], 50.0)

    def test_a_date_after_the_books_end_is_not_checked(self):
        """Bought in December, paid in February, and the account's last
        row is that payment: the books cannot say what May brought. Not
        a contradiction, but not free: one such date is a missing
        payment, as a settled payer's (late, still projected), and the
        row says where the books end."""
        rows = [_trade("2025-12-01", "ZZA.TO", 100)] + _divs(["2026-02-10"])
        note = "the books of margin end 2026-02-10: later dates not checked"
        r = _row(rows, books_to=None)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", True))
        self.assertEqual((r["status"], r["flags"]),
                         ("late", [_ASSUMED_ONE, note, _LATE]))
        self.assertEqual(r["next_pay_date"], "2026-08-10")
        self.assertEqual(r["window_amount_base"], 200.0)
        doc = _est(rows, [_pos("ZZA.TO", 100)])
        self.assertIn("! 1 payer(s) to check: ZZA.TO", DE.render(doc).text())
        self.assertIn(
            "Check: " + "; ".join([_ASSUMED_ONE, note, _LATE]) + ".",
            flat(DE.render(doc, details=True).text()))
        # No trade row of the payer either: the same.
        r = _row(_divs(["2026-02-10"]), books_to=None)
        self.assertEqual(r["flags"], [_ASSUMED_ONE, note, _LATE])
        # Books that run to within the grace of the date (15 days) do
        # not reach it; a day more and they do.
        r = _row(rows, books_to="2026-05-24")
        self.assertEqual(r["flags"], [
            _ASSUMED_ONE,
            "the books of margin end 2026-05-24: later dates not checked",
            _LATE])
        r = _row(rows, books_to="2026-05-25")
        self.assertEqual((r["frequency"], r["frequency_assumed"],
                          r["flags"]), ("1 payment", False,
                                        ["single payment"]))

    def test_two_dates_after_the_books_end_drop_the_assumption(self):
        """A quiet account whose last row is the payer's one dividend,
        eleven months back: three quarterly dates nobody checked. Not
        assumed (it read: quarterly, ok, 200.00); the row is read as
        before and keeps the line that says why."""
        rows = [_trade("2025-07-01", "ZZA.TO", 100)] + _divs(["2025-08-10"])
        note = "the books of margin end 2025-08-10: later dates not checked"
        for n, trade_rows in ((0, rows), (1, rows[1:])):
            with self.subTest(trade_rows_left_out=n):
                doc = _est(trade_rows, [_pos("ZZA.TO", 100)])
                r = doc["rows"][0]
                self.assertEqual(
                    (r["frequency"], r["frequency_assumed"], r["status"]),
                    ("1 payment", False, "irregular"))
                self.assertEqual(r["flags"], [note, "single payment"])
                self.assertEqual(r["window_amount_base"], 50.0)
                self.assertIn("Check: " + note + "; single payment.",
                              flat(DE.render(doc, details=True).text()))
        # Two dates passed: the same. One of them not held, by the
        # books: one unchecked date, late.
        r = _row(rows[:1] + _divs(["2025-12-10"]), books_to=None)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("1 payment", False))
        r = _row(rows[:1] + [_trade("2026-01-05", "ZZA.TO", -100),
                             _trade("2026-03-25", "ZZA.TO", 100)]
                 + _divs(["2025-12-10"]), books_to=None)
        self.assertEqual((r["frequency_assumed"], r["status"]),
                         (True, "late"))

    def test_the_books_reach_whatever_the_as_of_date(self):
        """Held since December, paid in February, the account's next
        row in August. As of July the books do cover May (a row after
        the as-of date shows they run on): held, nothing paid,
        contradicted, as it is as of September. (It read the books as
        ending in February.)"""
        rows = [_trade("2025-12-01", "ZZA.TO", 100)] + _divs(["2026-02-10"])
        rows.append(_trade("2026-08-05", "ZZX.TO", 1))
        for as_of in ("2026-07-01", "2026-09-01"):
            with self.subTest(as_of=as_of):
                r = _row(rows, books_to=None, as_of=as_of)
                self.assertEqual(
                    (r["frequency"], r["frequency_assumed"], r["flags"]),
                    ("1 payment", False, ["single payment"]))

    def test_a_rename_before_inside_and_after_the_days_held(self):
        """The May date's 45 days begin on 26 March. Renamed before
        them, inside them or after the date: the position was held all
        along (old name and new added together), nothing paid:
        contradicted."""
        for renamed in ("2026-03-01", "2026-04-15", "2026-05-10",
                        "2026-05-20"):
            with self.subTest(renamed=renamed):
                rows = [_trade("2025-12-01", "ZZO.TO", 100)]
                rows += _divs(["2026-02-10"], sym="ZZO.TO")
                rows.append(("margin", {
                    "action": "SPLIT", "date": renamed, "symbol": "ZZO.TO",
                    "symbol_new": "ZZA.TO", "quantity": 1.0,
                    "account": "margin"}))
                r = _row(rows)
                self.assertEqual(
                    (r["payments"], r["frequency"], r["frequency_assumed"]),
                    (1, "1 payment", False))

    def test_a_move_between_the_owners_accounts(self):
        """Transferred from margin to the TFSA inside the May date's 45
        days: the position stayed held. Out and in on one day, or the
        in three days later (in transit: up to 5 days with no shares in
        any book still count). Ten days apart is a stretch not held."""
        def row(arrived, tfsa_to="2026-06-30"):
            rows = [_trade("2025-12-01", "ZZA.TO", 100)]
            rows += _divs(["2026-02-10"])
            rows += [_trade("2026-04-20", "ZZA.TO", -100, action="TRANSFER"),
                     _trade(arrived, "ZZA.TO", 100, acct="tfsa",
                            action="TRANSFER")]
            if tfsa_to:
                rows.append(_trade(tfsa_to, "ZZX.TO", 1, acct="tfsa"))
            return _est(rows + [_trade("2026-06-30", "ZZX.TO", 1)],
                        [_pos("ZZA.TO", 100, acct="tfsa")])["rows"][0]
        for arrived in ("2026-04-20", "2026-04-23", "2026-04-25"):
            with self.subTest(arrived=arrived):
                r = row(arrived)
                self.assertEqual((r["frequency"], r["frequency_assumed"]),
                                 ("1 payment", False))
        r = row("2026-04-30")
        self.assertEqual((r["frequency_assumed"], r["status"], r["flags"]),
                         (True, "ok", [_ASSUMED_ONE]))
        # The TFSA's books end at the transfer: the May date is past
        # them, unchecked.
        r = row("2026-04-23", tfsa_to=None)
        self.assertEqual((r["frequency_assumed"], r["status"]),
                         (True, "late"))
        self.assertEqual(r["flags"], [
            _ASSUMED_ONE,
            "the books of tfsa end 2026-04-23: later dates not checked",
            _LATE])

    def test_as_of_a_date_in_the_past(self):
        """`--date` before later payments: the history and the holdings
        are the ones known on that date."""
        rows = [_trade("2025-12-01", "ZZA.TO", 100)]
        rows += _divs(["2026-02-10", "2026-05-10"])
        r = _row(rows, as_of="2026-03-01")
        self.assertEqual((r["payments"], r["frequency_assumed"],
                          r["flags"]), (1, True, [_ASSUMED_ONE]))
        self.assertEqual(r["next_pay_date"], "2026-05-10")
        r = _row(rows, as_of="2026-07-01")
        self.assertEqual((r["payments"], r["frequency"],
                          r["frequency_assumed"]), (2, "quarterly", False))

    def test_sold_after_the_date_was_held_on_it(self):
        """Long from 45 days before the May date through it, sold ten
        days later: entitled, and nothing was paid. Sold five days
        before it: not held."""
        def rows(sold):
            return [_trade("2025-12-01", "ZZA.TO", 100),
                    _trade(sold, "ZZA.TO", -100),
                    _trade("2026-06-15", "ZZA.TO", 100)
                    ] + _divs(["2026-02-10"])
        self.assertEqual(_row(rows("2026-05-20"))["frequency"], "1 payment")
        self.assertEqual(_row(rows("2026-05-10"))["frequency"], "quarterly")
        r = _row(rows("2026-05-05"))
        self.assertEqual((r["frequency_assumed"], r["status"]),
                         (True, "ok"))

    def test_the_twelve_months(self):
        """Paid 364 days before the date: in the 12 months. 365: not."""
        trades = [_trade("2025-06-01", "ZZA.TO", 100),
                  _trade("2025-07-20", "ZZA.TO", -100),
                  _trade("2026-06-15", "ZZA.TO", 100)]
        r = _row(trades + _divs(["2025-07-02"]))
        self.assertEqual((r["frequency_assumed"], r["status"],
                          r["next_pay_date"]), (True, "ok", "2026-07-02"))
        r = _row(trades + _divs(["2025-07-01"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"],
                          r["status"]), ("1 payment", False, "stopped"))

    def test_a_date_passed_while_not_held_is_no_missing_payment(self):
        """Paid in November, sold in December, bought back in June: the
        February and May dates passed with the shares not held, by the
        books. No payment was due: still assumed quarterly, from the
        next date, and nothing is called missing."""
        rows = [_trade("2025-10-01", "ZZA.TO", 100),
                _trade("2025-12-01", "ZZA.TO", -100),
                _trade("2026-06-15", "ZZA.TO", 100)]
        r = _row(rows + _divs(["2025-11-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", True))
        self.assertEqual((r["status"], r["flags"]), ("ok", [_ASSUMED_ONE]))
        self.assertEqual([p["pay_date"] for p in r["projected"]],
                         ["2026-08-10", "2026-11-10", "2027-02-10",
                          "2027-05-10"])
        self.assertEqual(r["window_amount_base"], 200.0)

    def test_no_payment_in_the_last_twelve_months_is_not_assumed(self):
        rows = [_trade("2025-05-01", "ZZA.TO", 100),
                _trade("2025-07-01", "ZZA.TO", -100),
                _trade("2026-06-15", "ZZA.TO", 100)]
        r = _row(rows + _divs(["2025-06-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("1 payment", False))
        self.assertEqual(r["status"], "stopped")
        self.assertEqual(r["flags"], ["single payment",
                                      "no payment in the last 12 months"])

    def test_held_in_another_account_counts(self):
        """One account sold before the quarterly date; another held the
        payer through it and was paid nothing: contradicted."""
        rows = [_trade("2025-12-01", "ZZA.TO", 100),
                _trade("2026-03-01", "ZZA.TO", -100),
                _trade("2026-06-15", "ZZA.TO", 100),
                _trade("2025-12-01", "ZZA.TO", 40, acct="tfsa"),
                _trade("2026-06-30", "ZZX.TO", 1, acct="tfsa")]
        r = _row(rows + _divs(["2026-02-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("1 payment", False))

    def test_a_short_position_is_not_held(self):
        rows = [_trade("2025-12-01", "ZZA.TO", -40, acct="other"),
                _trade("2026-06-15", "ZZA.TO", 100)]
        r = _row(rows + _divs(["2026-02-10"]))
        self.assertTrue(r["frequency_assumed"])
        self.assertEqual(r["status"], "ok")


class TestTwoOrThreePayments(unittest.TestCase):

    def test_a_quarter_apart_is_quarterly_as_before(self):
        r = _row(_divs(["2026-03-10", "2026-06-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", False))
        self.assertEqual(r["flags"], [])
        self.assertEqual(r["window_amount_base"], 200.0)

    def test_six_months_apart_the_quarter_between_not_held(self):
        rows = [_trade("2025-11-01", "ZZA.TO", 100),
                _trade("2026-01-15", "ZZA.TO", -100),
                _trade("2026-05-01", "ZZA.TO", 100)]
        rows += _divs(["2025-12-10", "2026-06-10"])
        r = _row(rows)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", True))
        self.assertEqual(r["flags"], [_ASSUMED_TWO])
        self.assertEqual(r["next_pay_date"], "2026-09-10")
        self.assertEqual(r["window_amount_base"], 200.0)   # it was 100.00
        self.assertEqual(r["median_gap_days"], 182.0)
        # No trade row of the payer in the books: the same.
        r = _row(_divs(["2025-12-10", "2026-06-10"]))
        self.assertTrue(r["frequency_assumed"])

    def test_six_months_apart_the_quarter_between_held(self):
        """Held through March with no payment: semi-annual, as before."""
        rows = [_trade("2025-11-01", "ZZA.TO", 100)]
        rows += _divs(["2025-12-10", "2026-06-10"])
        r = _row(rows)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("semi-annual", False))
        self.assertEqual((r["status"], r["flags"]), ("ok", []))
        self.assertEqual(r["next_pay_date"], "2026-12-10")
        self.assertEqual(r["window_amount_base"], 100.0)

    def test_the_date_between_paid_by_a_row_the_schedule_leaves_out(self):
        """Held through March, and the books hold a March payment the
        schedule cannot use: a row with no share count in the broker's
        statements, or one paid in another currency. The payer did not
        skip March (it read: held, no payment, semi-annual)."""
        base = [_trade("2025-11-01", "ZZA.TO", 100)]
        base += _divs(["2025-12-10", "2026-06-10"])
        bare = _div("2026-03-10", "ZZA.TO", 0, 0, no_share_count="no-accrual")
        bare.update(gross_amount=15.0, net_amount=15.0)
        r = _row(base + [("margin", bare)])
        self.assertEqual((r["payments"], r["frequency"],
                          r["frequency_assumed"]), (2, "quarterly", True))
        self.assertEqual(r["flags"], [
            "the 2026-03-10 payment of 15.00 CAD has no share count in the "
            "broker's statements: left out of the per-share figures",
            _ASSUMED_TWO])
        self.assertEqual(r["window_amount_base"], 200.0)   # 0.50 a share
        r = _row(base + _divs(["2026-03-10"], per=0.4, cur="USD"))
        self.assertEqual((r["payments"], r["frequency"],
                          r["frequency_assumed"]), (2, "quarterly", True))
        self.assertEqual(r["flags"], [
            "payments in USD too: estimated from the CAD ones",
            _ASSUMED_TWO])
        # A row 30 days from the March date is not its payment.
        r = _row(base + _divs(["2026-04-09"], per=0.4, cur="USD"))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("semi-annual", False))

    def test_the_grids_tolerance(self):
        """22 days off its quarterly date (204 days before the latest):
        on the grid. 23 days (205): off it, read as before."""
        r = _row(_divs(["2025-11-18", "2026-06-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", True))
        r = _row(_divs(["2025-11-17", "2026-06-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("semi-annual", False))

    def test_held_for_part_of_the_days_around_the_date_is_not_held(self):
        """Bought five days before the March date: a March payment's
        record date could have been before the purchase."""
        rows = [_trade("2025-11-01", "ZZA.TO", 100),
                _trade("2026-01-15", "ZZA.TO", -100),
                _trade("2026-03-05", "ZZA.TO", 100)]
        rows += _divs(["2025-12-10", "2026-06-10"])
        self.assertTrue(_row(rows)["frequency_assumed"])

    def test_held_under_the_name_before_a_rename(self):
        """The quarter between was held under the old symbol."""
        rows = [_trade("2025-11-01", "ZZO.TO", 100)]
        rows += _divs(["2025-12-10"], sym="ZZO.TO")
        rows.append(("margin", {"action": "SPLIT", "date": "2026-04-01",
                                "symbol": "ZZO.TO", "symbol_new": "ZZA.TO",
                                "quantity": 1.0, "account": "margin"}))
        rows += _divs(["2026-06-10"])
        r = _row(rows)
        self.assertEqual(r["payments"], 2)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("semi-annual", False))

    def test_a_reused_old_ticker_stays_the_old_tickers(self):
        """Backlog TJ-5052: ZZO.TO renamed ZZA.TO; ZZA sold, then an
        unrelated security trades under ZZO.TO while ZZA is not held. Its
        holding is no evidence for ZZA's quarters: the estimate is the
        one without it (assumed quarterly), and the balances after the
        rename stay ZZO.TO's. It read ZZA as held all along: one
        payment, irregular."""
        rows = [_trade("2024-01-02", "ZZO.TO", 100),
                ("margin", _div("2024-03-15", "ZZO.TO", 100, 0.5)),
                ("margin", {"action": "SPLIT", "date": "2024-04-01",
                            "symbol": "ZZO.TO", "symbol_new": "ZZA.TO",
                            "quantity": 1.0, "account": "margin"}),
                _trade("2024-04-15", "ZZA.TO", -100),
                _trade("2025-07-02", "ZZA.TO", 100),
                ("margin", _div("2025-09-15", "ZZA.TO", 100, 0.5))]
        reused = [_trade("2024-05-01", "ZZO.TO", 100),
                  _trade("2025-05-01", "ZZO.TO", -100)]
        got = []
        for books in (rows, rows + reused):
            doc = _est(books, [_pos("ZZA.TO", 100)], as_of="2025-12-20")
            r = doc["rows"][0]
            got.append((r["frequency"], r["frequency_assumed"], r["status"],
                        r["window_amount_base"]))
        self.assertEqual(got[0][:2], ("quarterly", True))
        self.assertEqual(got[1], got[0])
        held = DE.payments(rows + reused, as_of="2025-12-20").held
        self.assertNotIn(("margin", [("2024-05-01", 100.0),
                                     ("2025-05-01", 0.0)]),
                         held["ZZA.TO"])
        self.assertEqual(held["ZZO.TO"], [("margin", [("2024-05-01", 100.0),
                                                      ("2025-05-01", 0.0)])])
        # A second rename of the reused ticker carries its own part on.
        again = rows + reused + [
            ("margin", {"action": "SPLIT", "date": "2025-06-01",
                        "symbol": "ZZO.TO", "symbol_new": "ZZB.TO",
                        "quantity": 1.0, "account": "margin"})]
        held = DE.payments(again, as_of="2025-12-20").held
        self.assertNotIn("ZZO.TO", held)
        self.assertEqual(held["ZZB.TO"][0], ("margin", [("2024-05-01", 100.0),
                                                        ("2025-05-01", 0.0),
                                                        ("2025-06-01", 0.0)]))
        self.assertEqual(held["ZZA.TO"][0], ("margin", [("2024-01-02", 100.0),
                                                        ("2024-04-01", 0.0)]))

    def test_nine_months_apart(self):
        divs = _divs(["2025-09-10", "2026-06-10"])
        rows = [_trade("2025-08-01", "ZZA.TO", 100),
                _trade("2025-10-01", "ZZA.TO", -100),
                _trade("2026-05-01", "ZZA.TO", 100)]
        r = _row(rows + divs)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", True))
        self.assertEqual(r["window_amount_base"], 200.0)
        # Held throughout: no grid, pro rata from the 12 months' 1.00.
        r = _row(rows[:1] + divs)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("irregular", False))
        self.assertEqual(r["flags"], ["irregular schedule"])
        self.assertEqual(r["window_amount_base"], 100.0)

    def test_a_month_apart_is_not_assumed(self):
        r = _row(_divs(["2026-05-10", "2026-06-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("monthly", False))

    def test_off_the_quarterly_dates_is_not_assumed(self):
        """136 days apart: 44 days off the quarterly date, past the
        reading's tolerance (about 23)."""
        r = _row(_divs(["2026-01-25", "2026-06-10"]))
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("irregular", False))

    def test_three_on_quarterly_dates_one_six_month_gap(self):
        """June 2026, December 2025 and September 2024 (a 15-month gap):
        the reading is semi-annual from one gap. Not held in between:
        assumed quarterly."""
        divs = _divs(["2024-09-10", "2025-12-10", "2026-06-10"])
        r = _row(divs)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", True))
        r = _row([_trade("2024-08-01", "ZZA.TO", 100)] + divs)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("semi-annual", False))


class TestASettledPayerIsReadAsBefore(unittest.TestCase):
    """The schedule the history shows, with no trade row in the books
    (nothing says a date was held) and with the shares held throughout:
    the same reading, never assumed."""

    CASES = (
        ("quarterly", ["2025-09-10", "2025-12-10", "2026-03-10",
                       "2026-06-10"], 200.0),
        ("monthly", [DE.add_months("2025-07-10", k) for k in range(12)],
         600.0),
        ("semi-annual", ["2025-06-10", "2025-12-10", "2026-06-10"], 100.0),
        ("annual", ["2025-06-10", "2026-06-10"], 50.0),
        ("weekly", ["2026-06-03", "2026-06-10", "2026-06-17",
                    "2026-06-24"], 2650.0),
    )

    def test_each_frequency(self):
        for name, dates, window in self.CASES:
            for held in (False, True):
                with self.subTest(frequency=name, held=held):
                    rows = _divs(dates)
                    if held:
                        rows.insert(0, _trade("2024-01-02", "ZZA.TO", 100))
                    r = _row(rows)
                    self.assertEqual(
                        (r["frequency"], r["frequency_assumed"],
                         r["status"], r["flags"]), (name, False, "ok", []))
                    self.assertEqual(r["window_amount_base"], window)

    def test_a_special_after_a_settled_schedule(self):
        rows = _divs(["2025-09-10", "2025-12-10", "2026-03-10",
                      "2026-06-10"]) + _divs(["2026-06-24"], per=2.0)
        r = _row(rows)
        self.assertEqual((r["frequency"], r["frequency_assumed"]),
                         ("quarterly", False))
        self.assertEqual(r["window_amount_base"], 200.0)


class TestTheDatingRules(unittest.TestCase):

    @rule(R.CA_PLAN_06)
    def test_a_canadian_trusts_record_date(self):
        """One distribution, paid 7 October on a 30 September record
        date: the assumed January payment has a December record date,
        so it counts in this tax year (CA-INC-DATE-TRUST)."""
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules.from_settings({"country": "canada"})
        rows = _divs(["2026-10-07"], sym="ZZT.TO", per=0.1,
                     record_date="2026-09-30", income_label="distribution")
        doc = _est(rows, [_pos("ZZT.TO", 100)], as_of="2026-11-01",
                   rules=rules)
        r = doc["rows"][0]
        self.assertTrue(r["frequency_assumed"])
        self.assertEqual([(p["pay_date"], p["tax_date"])
                          for p in r["projected"]][:2],
                         [("2027-01-07", "2026-12-31"),
                          ("2027-04-07", "2027-03-31")])
        self.assertEqual(doc["totals"]["rest_of_year"]["taxable"], 10.0)
        self.assertEqual(doc["totals"]["window"]["taxable"], 40.0)

    @rule(R.US_PLAN_07)
    def test_a_us_project_pay_date_and_listed_ric(self):
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules.from_settings({
            "country": "usa", "ric_january_dividends": ["ZZF.US"]})
        rows = _divs(["2025-10-10"], sym="ZZF.US", qty=10, per=1.0,
                     cur="USD")
        rows += _divs(["2025-10-10"], sym="ZZU.US", qty=10, per=1.0,
                      cur="USD")
        doc = _est(rows, [_pos("ZZF.US", 10), _pos("ZZU.US", 10)],
                   as_of="2025-12-01", year=2025, rules=rules,
                   fx=lambda c: 1.0)
        by = {r["symbol"]: r for r in doc["rows"]}
        self.assertTrue(all(r["frequency_assumed"] for r in by.values()))
        self.assertEqual([(p["pay_date"], p["tax_date"])
                          for p in by["ZZF.US"]["projected"]][0],
                         ("2026-01-10", "2025-12-31"))
        self.assertEqual([(p["pay_date"], p["tax_date"])
                          for p in by["ZZU.US"]["projected"]][0],
                         ("2026-01-10", "2026-01-10"))
        self.assertEqual(doc["totals"]["rest_of_year"]["taxable"], 10.0)
        self.assertEqual(doc["totals"]["window"]["taxable"], 80.0)


def _doc():
    """ZZA: one payment (assumed). ZZD: four quarterly payments. ZZH:
    one payment and a skipped quarter while held (not assumed)."""
    rows = _divs(["2026-06-10"])
    rows += _divs(["2025-09-15", "2025-12-15", "2026-03-15", "2026-06-15"],
                  sym="ZZD.TO")
    rows += [_trade("2025-12-01", "ZZH.TO", 100)]
    rows += _divs(["2026-02-10"], sym="ZZH.TO")
    return _est(rows, [_pos("ZZA.TO", 100), _pos("ZZD.TO", 100),
                       _pos("ZZH.TO", 100, acct="margin",
                            group="sheltered")])


class TestTheTextAndTheJson(unittest.TestCase):

    def test_the_table_the_legend_and_the_check_item(self):
        text = DE.render(_doc()).text()
        cells = {ln.split()[0]: ln.split()[2] for ln in text.splitlines()
                 if ln.startswith("ZZ")}
        self.assertEqual(cells, {"ZZA.TO": "quarterly?",
                                 "ZZD.TO": "quarterly", "ZZH.TO": "1"})
        self.assertIn("FREQ quarterly?: assumed, too few payments in the "
                      "books to tell.", flat(text))
        self.assertIn("! 2 payer(s) to check: ZZA.TO, ZZH.TO", text)

    def test_no_legend_line_without_an_assumed_row(self):
        doc = _est(_divs(["2025-09-15", "2025-12-15", "2026-03-15",
                          "2026-06-15"]), [_pos("ZZA.TO", 100)])
        text = DE.render(doc).text()
        self.assertNotIn("quarterly?", text)
        self.assertNotIn("assumed", text)

    def test_details(self):
        text = flat(DE.render(_doc(), details=True).text())
        self.assertIn(
            "ZZA.TO (margin): 1 payment(s), quarterly (assumed); last "
            "2026-06-10 at 0.5 CAD/share, 12-month 0.5/share; projected: "
            "2026-09-10 50.00 CAD; 2026-12-10 50.00 CAD; 2027-03-10 50.00 "
            "CAD; 2027-06-10 50.00 CAD. Check: " + _ASSUMED_ONE + ".", text)
        self.assertIn("ZZD.TO (margin): 4 payment(s), quarterly, typical "
                      "gap 91 days; last", text)
        self.assertIn("ZZH.TO (margin): 1 payment(s); last 2026-02-10",
                      text)
        how = text.split("HOW IT IS ESTIMATED")[1]
        self.assertIn(
            "Too few payments to tell (one to three, each on a quarterly "
            "date counted back from the latest, the latest in the last 12 "
            "months): quarterly, assumed (FREQ quarterly?), until the books "
            "contradict it: a quarterly date passed with the shares held "
            "and no payment of any kind in the books.", how)
        self.assertIn("is over-projected until then.", how)
        self.assertIn("No grid, and not assumed quarterly: pro rata", how)

    def test_no_typical_gap_beside_an_assumed_schedule(self):
        """Two payments 182 days apart, assumed quarterly: the text does
        not print the gap; `--json` keeps it (it is the data's)."""
        doc = _est(_divs(["2025-12-10", "2026-06-10"]),
                   [_pos("ZZA.TO", 100)])
        self.assertEqual(doc["rows"][0]["median_gap_days"], 182.0)
        text = flat(DE.render(doc, details=True).text())
        self.assertIn("ZZA.TO (margin): 2 payment(s), quarterly (assumed); "
                      "last 2026-06-10", text)
        self.assertNotIn("typical gap", text)

    def test_a_narrow_terminal(self):
        # (72: a width the suite sets elsewhere; test_extract_support caps
        # how many constants one variable takes across the tests)
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "72"}):
            text = DE.render(_doc()).text()
            full = DE.render(_doc(), details=True).text()
        self.assertLessEqual(max(map(len, full.splitlines())), 72, full)
        cells = {ln.split()[0]: ln.split()[2] for ln in text.splitlines()
                 if ln.startswith("ZZ")}
        self.assertEqual(cells["ZZA.TO"], "quarterly?")
        self.assertIn("quarterly (assumed)", flat(full))

    def test_the_json_key_and_the_totals(self):
        doc = _doc()
        self.assertEqual(doc["schema_version"], SCHEMAS["div-estimate"])
        self.assertEqual(SCHEMAS["div-estimate"], 1)
        self.assertEqual({r["symbol"]: r["frequency_assumed"]
                          for r in json.loads(json.dumps(doc))["rows"]},
                         {"ZZA.TO": True, "ZZD.TO": False, "ZZH.TO": False})
        # Taxable: ZZA's four assumed payments and ZZD's four of 50.00;
        # sheltered: ZZH's 12-month 0.50/share, pro rata.
        self.assertEqual(doc["totals"]["window"],
                         {"taxable": 400.0, "sheltered": 50.0})
        self.assertEqual(doc["totals"]["rest_of_year"]["taxable"], 200.0)


class TestAProjectsBooks(unittest.TestCase):
    """End to end: the holdings on a past date come from the books' own
    trade rows."""

    def _run(self, country, cur, sfx):
        new, old = f"ZZA.{sfx}", f"ZZH.{sfx}"
        tt = [f"BUYSELL 2024-08-01 10:00:00 {new} 100 {cur} 20.00 2000",
              f"DIVIDEND 2024-09-16 16:00:00 {new} 100 {cur} 0.50 50.00",
              f"BUYSELL 2024-01-02 10:00:00 {old} 100 {cur} 20.00 2000",
              f"DIVIDEND 2024-03-15 16:00:00 {old} 100 {cur} 0.50 50.00"]
        with tempfile.TemporaryDirectory() as tmp:
            root = qa_project(tmp, "p", {"margin/m.tt": "\n".join(tt) + "\n"},
                              country=country)
            tj(root, "run", "--no-input")
            doc = json.loads(tj(root, "div-estimate", "--date",
                                "2024-10-20", "--json").stdout)
            by = {r["symbol"]: r for r in doc["rows"]}
            self.assertEqual(
                (by[new]["frequency"], by[new]["frequency_assumed"],
                 by[new]["next_pay_date"], by[new]["window_amount"]),
                ("quarterly", True, "2024-12-16", 200.0))
            # Held since January, paid once in March: June and September
            # passed with no payment.
            self.assertEqual(
                (by[old]["frequency"], by[old]["frequency_assumed"],
                 by[old]["window_amount"]), ("1 payment", False, 50.0))
            text = tj(root, "div-estimate", "--date", "2024-10-20").stdout
            self.assertIn("quarterly?", text)
            self.assertIn(f"! 2 payer(s) to check: {new}, {old}", text)

    def test_books_that_end_before_the_date(self):
        """The account's only rows are a purchase and one dividend; as
        of five months later the May date is past the books' end."""
        tt = ["BUYSELL 2024-01-02 10:00:00 ZZA.TO 100 CAD 20.00 2000",
              "DIVIDEND 2024-02-12 16:00:00 ZZA.TO 100 CAD 0.50 50.00"]
        with tempfile.TemporaryDirectory() as tmp:
            root = qa_project(tmp, "p", {"margin/m.tt": "\n".join(tt) + "\n"})
            tj(root, "run", "--no-input")
            doc = json.loads(tj(root, "div-estimate", "--date",
                                "2024-07-01", "--json").stdout)
            r = doc["rows"][0]
            self.assertEqual(
                (r["frequency"], r["frequency_assumed"], r["status"],
                 r["next_pay_date"], r["window_amount"]),
                ("quarterly", True, "late", "2024-08-12", 200.0))
            self.assertEqual(r["flags"], [
                _ASSUMED_ONE,
                "the books of margin end 2024-02-12: later dates not "
                "checked", _LATE])
            # Eleven months on, three dates unchecked: not assumed, and
            # the row keeps the line that says why.
            doc = json.loads(tj(root, "div-estimate", "--date",
                                "2025-01-20", "--json").stdout)
            r = doc["rows"][0]
            self.assertEqual((r["frequency"], r["frequency_assumed"]),
                             ("1 payment", False))
            self.assertEqual(r["flags"], [
                "the books of margin end 2024-02-12: later dates not "
                "checked", "single payment"])
            text = flat(tj(root, "div-estimate", "--date", "2024-07-01",
                           "--details").stdout)
            self.assertIn("the books of margin end 2024-02-12: later dates "
                          "not checked; " + _LATE + ".", text)

    @rule(R.CA_PLAN_06)
    def test_a_canadian_project(self):
        self._run("canada", "CAD", "TO")

    @rule(R.US_PLAN_07)
    def test_a_us_project(self):
        self._run("usa", "USD", "US")


if __name__ == "__main__":
    unittest.main()
