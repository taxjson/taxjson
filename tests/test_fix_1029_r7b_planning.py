"""Backlog fix round 7 (wave B) of 2026-10-29, group `planning`: the
option-amendment review and the wash planning checks.

TJ-0679  option-boundary compared a lock's one figure for a series
         written twice on one day with each lot alone: the assigned lot
         looked still reported, and a T1-ADJ was asked for again.
TJ-0680  the already-amended test required a positive assigned premium:
         a write whose commission exceeds its premium (a net loss) was
         never seen as amended.
TJ-0681  US: a WASHED row counted a short-cover loss as one a purchase
         could still disallow; buy-check said UNSAFE with its date.
TJ-0775  a VIOLATION whose rescue date had passed hid what a loss sale
         today meets (sell-check SAFE* beside a registered account's
         recent buy), and buy-check called its ended window UNSAFE.
TJ-0899  the radar treated a share position under 0.01 units as absent,
         even a valuable fractional one bought in the window: no row,
         and sell-check said SAFE.
TJ-0803  watch told a short position (written option, shorted shares)
         that turned CLEAR to "sell at a loss (don't buy back)".
TJ-0713  the radar planned a TSX US-dollar unit on the Canadian calendar
         alone (the base-currency books dropped the trade currency): its
         rescue sell-by date was a day late around Juneteenth.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN, QZX), amounts under 1,000.
"""
import unittest
from datetime import date

from tax_rules import rule
from test_fix_backlog_radar import _check, _radar, _row

from taxjson.lib.core import TaxTransaction


def _T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD",
                             "account": "margin", **kw})


_PUT = "SYN250117P00001000.TO"


def _two_same_day_writes():
    """Two puts written on 2024-12-02 (10 and 20 net); the first is
    assigned in 2025, the second expires."""
    return [
        _T(date="2024-12-02 09:00:00", date_settle="2024-12-02",
           symbol=_PUT, quantity=-1, price=0.1, net_amount=10.0),
        _T(date="2024-12-02 10:00:00", date_settle="2024-12-02",
           symbol=_PUT, quantity=-1, price=0.2, net_amount=20.0),
        _T(action="ASSIGN", date="2025-01-17 10:00:00",
           date_settle="2025-01-17", symbol=_PUT, quantity=1, price=0,
           net_amount=0.0),
        _T(action="ASSIGN", date="2025-01-17 10:00:00",
           date_settle="2025-01-17", symbol="SYN.TO", quantity=100,
           price=1, net_amount=100.0),
        _T(date="2025-01-17 16:00:00", date_settle="2025-01-17",
           symbol=_PUT, quantity=1, price=0, net_amount=0.0),
    ]


def _negative_write():
    """One put written for 5 gross with 10 commission (net -5),
    assigned in 2025."""
    return [
        _T(date="2024-12-02", date_settle="2024-12-02", symbol=_PUT,
           quantity=-1, price=0.05, net_amount=-5.0, commission=10.0),
        _T(action="ASSIGN", date="2025-01-17 10:00:00",
           date_settle="2025-01-17", symbol=_PUT, quantity=1, price=0,
           net_amount=0.0),
        _T(action="ASSIGN", date="2025-01-17 10:00:00",
           date_settle="2025-01-17", symbol="SYN.TO", quantity=100,
           price=1, net_amount=100.0),
    ]


def _lock(*gains):
    from taxjson.lib.option_boundary import reported_premiums
    return reported_premiums({"dispositions": [
        {"symbol": _PUT, "date": "2024-12-02", "date_settle": "2024-12-02",
         "gain": g} for g in gains]})


class TestAmendedLockWholeDay(unittest.TestCase):
    TODAY = date(2026, 9, 29)

    def _assigned(self, book, premiums):
        from taxjson.lib.option_boundary import straddling
        rows = straddling(book, 2025, "grant", 2024, filed_years={2024},
                          today=self.TODAY, filed_premiums=premiums)
        return [r for r in rows if r["close_kind"] == "assignment"]

    @rule("CA-OPT-07")
    def test_tj0679_same_day_second_write_is_not_the_assigned_one(self):
        # The amended 2024 lock reports only the expired lot's 20.
        rows = self._assigned(_two_same_day_writes(), {2024: _lock(20.0)})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["action"].startswith("no amendment"),
                        rows[0]["action"])
        # The original lock (both premiums, 30) still asks for it.
        rows = self._assigned(_two_same_day_writes(),
                              {2024: _lock(10.0, 20.0)})
        self.assertTrue(rows[0]["action"].startswith(
            "T1-ADJ 2024: remove the 10.00 premium"), rows[0]["action"])

    @rule("CA-OPT-07")
    def test_tj0680_negative_net_premium_amended_lock(self):
        # The amended lock reports nothing for the write.
        rows = self._assigned(_negative_write(), {2024: _lock()})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["premium"], -5.0)
        self.assertTrue(rows[0]["action"].startswith("no amendment"),
                        rows[0]["action"])
        # The lock as first filed reports the -5: the T1-ADJ stays.
        rows = self._assigned(_negative_write(), {2024: _lock(-5.0)})
        self.assertTrue(rows[0]["action"].startswith(
            "T1-ADJ 2024: remove the -5.00 premium"), rows[0]["action"])


class TestWashedRowShortCover(unittest.TestCase):
    """TJ-0681: a long loss fully washed, then a short opened and
    covered at a loss: only a new short sale can replace that loss."""

    _ROWS = [_row("2026-08-28", "SYNQ.US", 10, 10.0, currency="USD"),
             _row("2026-10-05", "SYNQ.US", -10, 9.0, currency="USD"),
             _row("2026-10-06", "SYNQ.US", 10, 9.0, currency="USD"),
             _row("2026-10-08", "SYNQ.US", -20, 10.0, currency="USD"),
             _row("2026-10-20", "SYNQ.US", 10, 11.0, currency="USD")]

    @rule("US-WASH-05")
    def test_tj0681_buy_check_is_not_unsafe(self):
        radar = _radar("usa", self._ROWS, as_of="2026-10-29")
        r = radar["SYNQ.US"]
        self.assertEqual(r["category"], "WASHED", r["advisory"])
        self.assertIsNone(r["clears_at"], r["advisory"])
        self.assertNotIn("if you buy again", r["advisory"])
        self.assertEqual(r["short_cover_loss"]["date"], "2026-10-20")
        self.assertIn("new SHORT sale before", r["advisory"])
        out, code = _check("cmd_buy_check", radar, ["SYNQ.US"], usa=True)
        flat = " ".join(out.split())
        self.assertEqual(code, 0, out)
        self.assertNotIn("UNSAFE", flat)
        self.assertNotIn("Safe to buy from", flat)
        self.assertIn("covering a short (2026-10-20)", flat)


class TestSettledViolationForwardView(unittest.TestCase):
    """TJ-0775: on 2026-10-29 the taxable account still holds 100 after
    a 100-share loss settled 2026-09-29 (rescue by 10-28, passed); a
    TFSA bought 200 on 10-28 and holds them."""

    _TAX = [_row("2026-09-01", "SYNV.TO", 200, 1.0),
            _row("2026-09-29", "SYNV.TO", -100, 0.5)]
    _SHL = [_row("2026-10-28", "SYNV.TO", 200, 1.0, acct="tfsa")]

    def _radar(self, tax=None, shl=None):
        return _radar("canada", tax or self._TAX,
                      self._SHL if shl is None else shl,
                      as_of="2026-10-29")

    @rule("CA-PLAN-01")
    def test_tj0775_radar_keeps_the_violation_and_adds_the_forward_view(self):
        r = self._radar()["SYNV.TO"]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertTrue(r["deadline_passed"])
        self.assertEqual(r["clears_at"], "2026-10-28")
        self.assertIsNone(r["at_risk_qty"])
        fwd = r["forward"]
        self.assertEqual(fwd["category"], "LOCKED", fwd)
        self.assertEqual(fwd["at_risk_qty"], 100.0)
        self.assertIn("For a loss sale today: LOCKED:", r["advisory"])

    @rule("CA-PLAN-01")
    def test_tj0775_sell_check_is_unsafe(self):
        out, code = _check("cmd_sell_check", self._radar(), ["SYNV.TO"])
        flat = " ".join(out.split())
        self.assertEqual(code, 1, out)
        self.assertIn("SYNV.TO: UNSAFE", flat)
        self.assertIn("No sale can undo it now.", flat)
        self.assertIn("SYNV.TO: LOCKED: Recent buy", flat)

    @rule("CA-PLAN-01")
    def test_tj0775_buy_check_window_over(self):
        out, code = _check("cmd_buy_check", self._radar(), ["SYNV.TO"])
        flat = " ".join(out.split())
        self.assertEqual(code, 0, out)
        self.assertNotIn("UNSAFE", flat)
        self.assertIn("Its 30-day window has ended: a buy now does not "
                      "replace that loss.", flat)
        self.assertIn("LOCKED — no loss sale a buy can still replace",
                      flat)

    @rule("CA-PLAN-01")
    def test_tj0775_nothing_held_stays_as_before(self):
        # Nothing taxable left (A2-0368's case): no forward view.
        tax = [_row("2026-09-01", "SYNV.TO", 100, 1.0),
               _row("2026-09-29", "SYNV.TO", -100, 0.5)]
        r = self._radar(tax=tax)["SYNV.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertIsNone(r["forward"])
        self.assertNotIn("For a loss sale today", r["advisory"])
        out, code = _check("cmd_sell_check", self._radar(tax=tax),
                           ["SYNV.TO"])
        self.assertEqual(code, 0, out)
        self.assertIn("SAFE*", out)


class TestFractionalPositionInWindow(unittest.TestCase):
    """TJ-0899: 0.005 shares held taxable (cost 1000) and 0.005 bought
    by a registered account six days ago."""

    @rule("CA-PLAN-01")
    def test_tj0899_locked_and_sell_check_unsafe(self):
        tax = [_row("2026-06-01", "SYNF.TO", 0.005, 200000.0)]
        shl = [_row("2026-10-23", "SYNF.TO", 0.005, 100000.0, acct="rrsp")]
        radar = _radar("canada", tax, shl, as_of="2026-10-29")
        r = radar["SYNF.TO"]
        self.assertEqual(r["category"], "LOCKED", r["advisory"])
        self.assertAlmostEqual(r["at_risk_qty"], 0.005, 6)
        out, code = _check("cmd_sell_check", radar, ["SYNF.TO"])
        self.assertEqual(code, 1, out)
        self.assertIn("SYNF.TO: UNSAFE", out)

    @rule("CA-PLAN-01")
    def test_tj0899_old_dust_stays_quiet(self):
        # A residue with no purchase in the window is still dust.
        tax = [_row("2026-06-01", "SYNG.TO", 100.004, 10.0),
               _row("2026-07-01", "SYNG.TO", -100, 9.0)]
        r = _radar("canada", tax, as_of="2026-10-29")["SYNG.TO"]
        self.assertEqual(r["category"], "", r["advisory"])


class TestWatchShortCleared(unittest.TestCase):
    """TJ-0803: a short opened 2026-09-01; the radar of 09-02 (EXITABLE)
    is the watch baseline, the radar of 10-29 says CLEAR."""

    def _diff(self, sym):
        from taxjson.bin.taxjson_watch import diff_radar
        rows = [_row("2026-09-01", sym, -1, 10.0, currency="USD")]
        prev = _radar("usa", rows, as_of="2026-09-02")
        cur = _radar("usa", rows, as_of="2026-10-29")
        self.assertEqual(prev[sym]["category"], "EXITABLE")
        self.assertEqual(cur[sym]["category"], "CLEAR")
        ch = [c for c in diff_radar(prev, cur) if c["ticker"] == sym]
        self.assertEqual(len(ch), 1, ch)
        return ch[0]

    @rule("US-WASH-05")
    def test_tj0803_short_stock(self):
        ch = self._diff("SYNS.US")
        self.assertEqual(ch["kind"], "cleared")
        self.assertNotIn("safe to sell", ch["line"])
        self.assertIn("safe to cover at a loss", ch["line"])
        self.assertIn("do not short it again", ch["line"])

    @rule("US-WASH-05")
    def test_tj0803_written_call(self):
        ch = self._diff("SYNS261218C00010000.US")
        self.assertNotIn("safe to sell", ch["line"])
        self.assertIn("EXITABLE -> CLEAR", ch["line"])

    @rule("US-WASH-05")
    def test_tj0803_long_keeps_the_sell_wording(self):
        from taxjson.bin.taxjson_watch import diff_radar
        rows = [_row("2026-09-01", "SYNL.US", 10, 10.0, currency="USD")]
        prev = _radar("usa", rows, as_of="2026-09-02")
        cur = _radar("usa", rows, as_of="2026-10-29")
        ch = [c for c in diff_radar(prev, cur) if c["ticker"] == "SYNL.US"]
        self.assertIn("safe to sell at a loss (don't buy back for 30 days)",
                      ch[0]["line"])


class TestUsdUnitOnTheJointCalendar(unittest.TestCase):
    """TJ-0713: a loss on QZX.U.TO traded in US dollars settles
    2025-05-20 (window to 2025-06-19, Juneteenth); a rebuy keeps it a
    VIOLATION. A sale on 06-18 settles 06-20 on 'CAD+USD' days."""

    def _rows(self, trade_currency):
        rows = [_row("2025-04-01", "QZX.U.TO", 100, 10.0,
                     settle="2025-04-02"),
                _row("2025-05-16", "QZX.U.TO", -100, 9.0,
                     settle="2025-05-20"),
                _row("2025-05-26", "QZX.U.TO", 50, 9.0,
                     settle="2025-05-27")]
        if trade_currency:
            for r in rows:
                r["trade_currency"] = trade_currency
        return rows

    @rule("CA-PLAN-01")
    def test_tj0713_sell_by_on_the_joint_calendar(self):
        r = _radar("canada", self._rows("USD"), as_of="2025-06-02")
        r = r["QZX.U.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["clears_at"], "2025-06-17")
        self.assertEqual(r["losses"][0]["rebuy_at"], "2025-06-18")
        # A row traded in Canadian dollars keeps the TSX's days.
        r = _radar("canada", self._rows(None), as_of="2025-06-02")
        self.assertEqual(r["QZX.U.TO"]["clears_at"], "2025-06-18")

    def test_tj0713_conversion_keeps_the_trade_currency(self):
        from decimal import Decimal

        from taxjson.bin.taxjson_convert_currency import convert_transaction
        t = _T(date="2025-05-16", date_settle="2025-05-20",
               symbol="QZX.U.TO", quantity=-100, price=9.0,
               net_amount=900.0, currency="USD")
        c = convert_transaction(t, "CAD",
                                {"USD": {"2025-05-20": Decimal("1.4")}},
                                None)
        self.assertEqual((c.currency, c.trade_currency), ("CAD", "USD"))
        self.assertEqual(c.id, t.id)
        self.assertEqual(c.to_dict()["trade_currency"], "USD")
        # A row already in the base currency has none.
        same = convert_transaction(_T(date="2025-05-16", symbol="SYN.TO",
                                      quantity=1, net_amount=-5.0),
                                   "CAD", {}, None)
        self.assertNotIn("trade_currency", same.to_dict())


if __name__ == "__main__":
    unittest.main()
