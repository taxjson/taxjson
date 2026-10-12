"""Mutation pins for lib/option_boundary.py (audit G1-0).

`taxjson option-boundary` (CA-OPT-07) and the expired-open check: each
test kills mutants that survived the whole suite in the 2026-09-30
mutation round. Books are hand-built TaxTransaction lists; `today` is
always passed so no result depends on the run date.
"""
import unittest
from datetime import date

from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_boundary import (_filed_on_close, expired_open,
                                         straddling, write_lots)
from tax_rules import rule

OPT = "Q260116C00050000.TO"          # expires 2026-01-16
OLD = "Q251219C00050000.TO"          # expires 2025-12-19
TODAY = date(2026, 9, 30)


def T(action="BUYSELL", **kw):
    base = {"action": action, "currency": "CAD", "account": "margin",
            "price": 1.0, "time": "10:00:00"}
    base.update(kw)
    if "date_settle" not in base:
        base["date_settle"] = base["date"]
    return TaxTransaction(**base)


def write(d, q=-1, net=399.0, sym=OPT, **kw):
    return T(date=d, symbol=sym, quantity=q, net_amount=net, **kw)


def buy(d, q=1, net=101.0, sym=OPT, price=1.0, **kw):
    return T(date=d, symbol=sym, quantity=q, net_amount=net, price=price,
             **kw)


def rows_of(book, year=2025, timing="grant", since=2025, **kw):
    return straddling(book, year, timing, since, today=TODAY, **kw)


class TestWriteLotWalk(unittest.TestCase):
    @rule("CA-OPT-07")
    def test_stock_rows_are_not_option_lots(self):
        # m899: only OPTION rows are walked — a short stock sale is not
        # a written option.
        book = [T(date="2025-12-10", symbol="Q.TO", quantity=-100,
                  net_amount=5000.0, price=50.0)]
        self.assertEqual(rows_of(book), [])
        self.assertEqual(write_lots(book), [])

    @rule("CA-OPT-07")
    def test_rows_carry_their_account(self):
        # m958: the lot keeps the write's account.
        rows = rows_of([write("2025-12-10")])
        self.assertEqual([(r["account"], r["close_kind"]) for r in rows],
                         [("margin", "open")])

    def test_zero_quantity_row_is_not_a_buy_back(self):
        # m900: a 0-quantity option row (a fee line) closes nothing.
        book = [write("2025-12-10"),
                buy("2026-01-05", q=0, net=1.0, price=0.0)]
        lots = write_lots(book)
        self.assertEqual(lots[0].open_units, 1.0)
        self.assertEqual(lots[0].closes, [])

    def test_opening_balance_is_counted_once(self):
        # m832: a phantom long of 2 then a sale of 4 writes 2.
        book = [T("OPENING_BALANCE", date="2025-01-02", symbol=OPT,
                  quantity=2, net_amount=0.0, price=0.0),
                write("2025-12-10", q=-4, net=1600.0)]
        lots = write_lots(book)
        self.assertEqual([(l.units, l.premium) for l in lots],
                         [(2.0, 800.0)])

    @rule("CA-OPT-07")
    def test_split_scales_and_renames_the_open_write(self):
        # m859 (x ratio), m830 (the SPLIT row is not also a trade),
        # m929 (a reverse split's ratio < 1 applies): 2 written, a 1:2
        # reverse split leaves 1, renamed; the buy-back of 1 under the
        # new name closes it in 2026.
        new = "Q260116C00100000.TO"
        book = [write("2025-12-10", q=-2, net=800.0),
                T("SPLIT", date="2025-12-20", symbol=OPT, quantity=0.5,
                  net_amount=0.0, price=0.0, symbol_new=new),
                buy("2026-01-05", q=1, net=50.0, sym=new)]
        lots = write_lots(book)
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].open_units, 0.0)
        self.assertEqual([(c.kind, c.units, c.paid) for c in lots[0].closes],
                         [("buy-back", 1.0, 50.0)])
        # The position is flat after it: a later long bought and sold
        # again is no write.
        book += [buy("2026-01-06", q=1, net=60.0, sym=new),
                 write("2026-01-07", q=-1, net=70.0, sym=new)]
        self.assertEqual(len(write_lots(book)), 1)

    def test_split_without_a_ratio_only_renames(self):
        # m901/m902: a SPLIT row with no positive ratio is a rename
        # (ratio 1).
        new = "Q260116C00060000.TO"
        book = [write("2025-12-10", q=-3, net=900.0),
                T("SPLIT", date="2025-12-20", symbol=OPT, quantity=0.0,
                  net_amount=0.0, price=0.0, symbol_new=new),
                buy("2026-01-05", q=3, net=30.0, sym=new)]
        lots = write_lots(book)
        self.assertEqual([c.units for c in lots[0].closes], [3.0])
        self.assertEqual(lots[0].open_units, 0.0)

    def test_split_of_an_untraded_symbol_creates_no_position(self):
        # m930: pos.pop(sym, 0.0): a later sale is a write, not the
        # close of an invented long.
        book = [T("SPLIT", date="2025-06-01", symbol=OPT, quantity=2.0,
                  net_amount=0.0, price=0.0),
                write("2025-12-10")]
        self.assertEqual([l.units for l in write_lots(book)], [1.0])

    @rule("CA-OPT-07")
    def test_expiry_needs_both_zero_price_and_zero_total(self):
        # m937, m997: a close at price 0 that still cost a commission,
        # and a priced close with no total, are buy-backs; only
        # price 0 AND total 0 is an expiry.
        book = [write("2025-12-10", q=-3, net=1200.0),
                buy("2026-01-05", q=1, net=1.25, price=0.0),
                buy("2026-01-06", q=1, net=0.0, price=2.0),
                buy("2026-01-16", q=1, net=0.0, price=0.0)]
        closes = write_lots(book)[0].closes
        self.assertEqual([(c.kind, c.paid) for c in closes],
                         [("buy-back", 1.25), ("buy-back", 0.0),
                          ("expiry", 0.0)])

    def test_assignment_needs_a_stock_trade_of_the_underlying(self):
        # m856: only the underlying's BUYSELL/ASSIGN rows make an
        # assignment physical; a dividend on it does not (the engine's
        # taxable_stock_symbols test).
        book = [write("2025-12-10"),
                T("DIVIDEND", date="2025-12-15", symbol="Q.TO",
                  quantity=100, net_amount=25.0, price=0.0),
                T("ASSIGN", date="2026-01-16", symbol=OPT, quantity=1,
                  net_amount=0.0, price=0.0)]
        self.assertEqual(write_lots(book)[0].closes[0].kind,
                         "cash-settled")


class TestBrokerClosing(unittest.TestCase):
    """A sale coded IB C with no long in the data is not a write."""

    def _book(self):
        return [write("2025-12-10", open_close="C", broker_basis="2.50")]

    @rule("CA-OPT-07")
    def test_one_attention_row_naming_the_basis(self):
        # m841 (no regular row after it), m980 (the IB Basis), m942
        # (paid 0), m967 (premium in cents).
        rows = rows_of([write("2025-12-10", net=399.005, open_close="C",
                              broker_basis="2.50")])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["close_kind"], "broker-closing")
        self.assertIn("IB code C, IB Basis 2.50", r["where"])
        self.assertEqual((r["paid"], r["premium"]), (0.0, 399.0))
        self.assertTrue(r["attention"])

    def test_expired_open_flags_the_unbacked_close(self):
        # m891: the position before the first row is 0, so a C-coded
        # sale of 1 cannot be backed.
        out = expired_open([write("2025-10-10", sym=OLD, open_close="C")],
                           2025, today=TODAY)
        self.assertEqual([(o["symbol"], o["side"], o["broker_closing"])
                          for o in out], [(OLD, "written", True)])


    def test_unbacked_close_bought_back_in_the_year_is_not_listed(self):
        # m841: a lot opened and closed in the same year straddles
        # nothing.
        rows = rows_of([write("2025-10-10", open_close="C"),
                        buy("2025-11-10")])
        self.assertEqual(rows, [])


class TestFiledLockTiming(unittest.TestCase):
    def test_lock_without_a_timing_record_is_unknown(self):
        # m824: a lock that predates the record says nothing.
        self.assertIsNone(_filed_on_close(2025, {2025: {"x": 1}}))
        self.assertIsNone(_filed_on_close(2025, {}))
        self.assertTrue(_filed_on_close(
            2025, {2025: {"option_premium_timing": "close"}}))

    def test_grant_lock_with_a_later_since_kept_the_year_on_close(self):
        # m837: grant timing from 2026 left 2025's writes on close.
        rec = {"option_premium_timing": "grant", "option_grant_since": 2026}
        self.assertTrue(_filed_on_close(2025, {2025: rec}))
        self.assertFalse(_filed_on_close(2026, {2026: rec}))
        self.assertFalse(_filed_on_close(
            2025, {2025: {"option_premium_timing": "grant"}}))

    @rule("CA-OPT-07")
    def test_unrecorded_lock_does_not_claim_a_missed_premium(self):
        rows = rows_of([write("2025-12-10")],
                       filed_timing={2025: {"note": "old lock"}})
        self.assertFalse(rows[0]["attention"])


class TestCloseTimingText(unittest.TestCase):
    """Close timing: the instruction names the T1-ADJ only when the
    write year was filed and is not a transition year (m998-m1007),
    and every amount is in cents (m972/m973/m975/m991, m947)."""

    def _book(self):
        return [write("2025-12-10", q=-3, net=1000.0),
                buy("2026-01-05", q=1, net=100.004),
                buy("2026-01-16", q=1, net=0.0, price=0.0)]

    @rule("CA-OPT-07")
    def test_filed_year_adds_the_t1_adj(self):
        rows = rows_of(self._book(), timing="close", since=None,
                       filed_years={2025})
        by = {r["close_kind"]: r for r in rows}
        self.assertTrue(by["expiry"]["action"].endswith(
            "; 2025 was filed: T1-ADJ 2025"))
        self.assertTrue(by["buy-back"]["action"].endswith(
            "; 2025 was filed: T1-ADJ 2025"))
        self.assertEqual(by["buy-back"]["premium"], 333.33)
        self.assertEqual(by["buy-back"]["paid"], 100.0)
        # The third unit is open at the end of the books (2025-12-10).
        rows = rows_of([write("2025-12-10", q=-3, net=1000.0)],
                       timing="close", since=None)
        by = {r["close_kind"]: r for r in rows}
        self.assertEqual(by["open"]["premium"], 1000.0)
        self.assertEqual(by["open"]["paid"], 0.0)
        rows = rows_of([write("2025-12-10", q=-3, net=1000.004)],
                       timing="close", since=None)
        by = {r["close_kind"]: r for r in rows}
        self.assertEqual(by["open"]["premium"], 1000.0)
        self.assertEqual(by["open"]["action"],
                         "the Act puts 1,000.00 in 2025 (s.49(1)) — enable "
                         "grant timing with option_grant_timing_since = "
                         "2025 to correct")

    @rule("CA-OPT-07")
    def test_unfiled_year_has_no_t1_adj(self):
        rows = rows_of(self._book(), timing="close", since=None)
        for r in rows:
            self.assertNotIn("was filed", r["action"])

    @rule("CA-OPT-07")
    def test_transition_year_has_no_t1_adj(self):
        rows = rows_of(self._book(), year=2026, timing="grant", since=2026,
                       filed_years={2025}, filed_timing={
                           2025: {"option_premium_timing": "close"}})
        rows = [r for r in rows if r["close_kind"] != "expired?"]
        self.assertEqual(sorted(r["close_kind"] for r in rows),
                         ["buy-back", "expiry"])
        for r in rows:
            self.assertNotIn("was filed", r["action"])
            self.assertIn("transition", r["action"])

    @rule("CA-OPT-07")
    def test_missing_expiry_row_premium_in_cents(self):
        # m991: a past-expiry lot still open.
        rows = rows_of([write("2025-10-10", q=-3, net=1000.0, sym=OLD)])
        self.assertEqual([(r["close_kind"], r["premium"]) for r in rows],
                         [("expired?", 1000.0)])
        rows = rows_of([write("2025-10-10", q=-3, net=1000.004, sym=OLD)])
        self.assertEqual(rows[0]["premium"], 1000.0)


class TestExpiredOpen(unittest.TestCase):
    def test_accounts_are_kept_apart(self):
        # m886: a long in one account does not offset a write in another.
        book = [buy("2025-10-10", sym=OLD, account="a"),
                write("2025-10-10", sym=OLD, account="b")]
        out = expired_open(book, 2025, today=TODAY)
        self.assertEqual(sorted((o["account"], o["side"]) for o in out),
                         [("a", "long"), ("b", "written")])

    def test_zero_quantity_row_opens_nothing(self):
        # m922.
        out = expired_open([buy("2025-10-10", sym=OLD, q=0, net=1.0)],
                           2025, today=TODAY)
        self.assertEqual(out, [])

    def test_only_past_expiries_are_flagged(self):
        # m853: a contract expiring after the cutoff is simply open.
        book = [write("2025-10-10", sym=OPT), write("2025-10-10", sym=OLD)]
        out = expired_open(book, 2025, today=date(2025, 12, 31))
        self.assertEqual([o["symbol"] for o in out], [OLD])

    def test_expiry_on_the_cutoff_is_flagged(self):
        # m896: books to Dec 31, a Dec 31 expiry with no row is missing.
        sym = "Q251231C00050000.TO"
        out = expired_open([write("2025-10-10", sym=sym)], 2025,
                           today=TODAY)
        self.assertEqual([o["expiry"] for o in out], ["2025-12-31"])

    def test_split_moves_the_position_and_its_closing_flag(self):
        # L438-L445 (no coverage before): the renamed, rescaled
        # position is the one reported, with the broker-closing flag;
        # a fractional long after a reverse split stays "long" (m992).
        new = "Q251219C00100000.TO"
        book = [write("2025-10-10", sym=OLD, q=-2, open_close="C"),
                T("SPLIT", date="2025-10-20", symbol=OLD, quantity=0.5,
                  net_amount=0.0, price=0.0, symbol_new=new),
                buy("2025-10-10", sym="Q251219P00050000.TO", account="x"),
                T("SPLIT", date="2025-10-20", symbol="Q251219P00050000.TO",
                  account="x", quantity=0.5, net_amount=0.0, price=0.0)]
        out = {o["symbol"]: o for o in expired_open(book, 2025,
                                                    today=TODAY)}
        self.assertEqual(set(out), {new, "Q251219P00050000.TO"})
        self.assertEqual(out[new]["quantity"], -1.0)
        self.assertTrue(out[new]["broker_closing"])
        p = out["Q251219P00050000.TO"]
        self.assertEqual((p["quantity"], p["side"], p["broker_closing"]),
                         (0.5, "long", False))


class TestExpiredOpenSplits(unittest.TestCase):
    def test_rename_without_a_ratio_keeps_the_position_and_account(self):
        # m923/m924 (no positive ratio = 1), m925 (the account stays).
        new = "Q251219C00060000.TO"
        book = [write("2025-10-10", sym=OLD, q=-2),
                T("SPLIT", date="2025-10-20", symbol=OLD, quantity=0.0,
                  net_amount=0.0, price=0.0, symbol_new=new)]
        out = expired_open(book, 2025, today=TODAY)
        self.assertEqual([(o["account"], o["symbol"], o["quantity"])
                          for o in out], [("margin", new, -2.0)])

    def test_split_of_an_untraded_symbol_opens_nothing(self):
        # m950.
        book = [T("SPLIT", date="2025-10-20", symbol=OLD, quantity=2.0,
                  net_amount=0.0, price=0.0)]
        self.assertEqual(expired_open(book, 2025, today=TODAY), [])


if __name__ == "__main__":
    unittest.main()
