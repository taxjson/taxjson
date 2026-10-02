"""Re-audit-2 test pins (tests-pins-02): the wash radar's dates, amounts,
quantities and country gates, each pinned by a test that fails when the
code it guards is reverted (a surviving mutant in the audit).

  A2-0181 / A2-0187 / A2-0188 / A2-1580  safe dates (+31 days), rescue
          deadlines, countdowns, the crossing-zero leftover (its ACB and
          its acquisition), held_any on a short, call-rescue holders,
          crypto same-day settlement
  A2-0189 / A2-0190 / A2-1579  the US verdict is the US engine's: FIFO
          per account, multi-lot amounts, disallowed / basis / IRA /
          remaining dollars, the replacement list and denied units
  A2-0525 / A2-1574  own-pool proration of a sale that crosses zero,
          the flip side's ACB, a zero-value TRANSFER-in's average cost
  A2-0543 a return of capital on an empty pool; the ACB a rename carries
  A2-0500 / A2-0545 / A2-0546 / A2-0863 / A2-0943 / A2-1595  every
          country gate and wording branch, both directions (titles,
          LOCKED / EXITABLE / RISK texts, _sl / _sl_adj / _reg, the US
          engine-record gate, the US short trigger, the lag-split gate)
  A2-0942 a US long call is a note: the row stays COOLING
  A2-0944 day +30 after the loss is inside the window (VIOLATION)
  A2-1616 a US call bought exactly 30 days from the loss gets its NOTE

The radar runs in process (taxjson_wash_radar.main) on synthetic books
(fake accounts only).
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent


def _row(d, sym, qty, px, acct="margin", action="BUYSELL", settle=None,
         currency="USD", rid=None, net=None, **kw):
    r = {"action": action, "date": d, "date_settle": settle or d,
         "time": "10:00:00", "symbol": sym, "quantity": qty, "price": px,
         "net_amount": (-qty * px if net is None else net),
         "currency": currency, "account": acct, "commission": 0.0}
    if rid:
        r["id"] = rid
    r.update(kw)
    return r


def _run(country, taxable, sheltered=None, as_of="2026-05-20",
         json_out=True, extra=()):
    from taxjson.bin import taxjson_wash_radar as R
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "margin_base.json"
        tp.write_text(json.dumps({"transactions": taxable}))
        argv = ["taxjson-wash-radar", "--country", country, "--date", as_of,
                "--all", "--taxable", str(tp), *extra]
        if sheltered:
            sp = Path(d) / "sheltered_base.json"
            sp.write_text(json.dumps({"transactions": sheltered}))
            argv += ["--sheltered", str(sp)]
        if json_out:
            argv.append("--json")
        out, old = io.StringIO(), sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                R.main()
        finally:
            sys.argv = old
    return json.loads(out.getvalue()) if json_out else out.getvalue()


def _rows(*a, **kw):
    doc = _run(*a, **kw)
    return {r["ticker"]: r for sec in doc["sections"] for r in sec["rows"]}


A = "AAA.US"


# ------------------------------------------------ Canada: dates, quantities
@rule("CA-PLAN-01")
class TestCaRadarDatesAndQuantities(unittest.TestCase):
    """A2-0181, A2-0187, A2-0188, A2-1580."""

    def test_locked_safe_date_follows_the_latest_sheltered_buy(self):
        # rrsp bought 04-25, tfsa 05-15, both still held: safe after
        # 05-15 + 31 days, not after the earlier buy.
        r = _rows("canada", [_row("2026-01-02", A, 100, 50)],
                  [_row("2026-04-25", A, 10, 45, "rrsp"),
                   _row("2026-05-15", A, 10, 45, "tfsa")])[A]
        self.assertEqual(r["category"], "LOCKED")
        self.assertEqual(r["clears_at"], "2026-06-15")
        self.assertEqual(r["clears_in_at_generation"], "2026-06-15 (26d)")
        self.assertEqual(r["at_risk_qty"], 20.0)
        self.assertIn("before 2026-06-15 is a superficial loss for up to "
                      "20 of your 100 shares", r["advisory"])

    def test_rescue_deadline_follows_the_earliest_open_loss(self):
        # Two losses (50 units on 05-01, 100 on 05-12) that share the
        # 05-05 rebuy of 100 still held: both are open violations, and
        # the deadline is the EARLIER loss's (day 30 of 05-01).
        book = [_row("2026-01-02", A, 300, 50),
                _row("2026-05-01", A, -50, 40),
                _row("2026-05-05", A, 100, 41),
                _row("2026-05-12", A, -100, 40)]
        r = _rows("canada", book)[A]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertEqual(r["clears_at"], "2026-05-28")
        self.assertEqual(r["settle_deadline"], "2026-05-31")
        self.assertEqual(r["clears_in_at_generation"], "2026-05-28 (8d)")
        self.assertEqual(r["denied_qty"], 100.0)
        self.assertEqual(r["rescue"], [{"account": "", "holder": "taxable",
                                        "qty": 250.0, "symbol": A}])

    def test_crossing_zero_leftover_long_is_a_replacement(self):
        # A buy that covers a short and crosses into a long position:
        # the 30 long units it opens replace the 05-01 loss.
        book = [_row("2026-01-02", A, 100, 50),
                _row("2026-05-01", A, -100, 40),
                _row("2026-05-05", A, -50, 40),
                _row("2026-05-12", A, 80, 41)]
        r = _rows("canada", book)[A]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["denied_qty"], 30.0)
        self.assertIn("Sell 30.0000 shares (taxable 30)", r["advisory"])

    def test_crossing_zero_sale_short_leftover_cost_and_held_short(self):
        # Hold 100 @50, sell 150 @40 (closes 100, opens a 50 short at
        # the PRORATED proceeds), buy 30 @41: the cover of 30 is a loss of
        # 30 x 1 = 30 on top of the 1000 -> 1030 over 130 units; the
        # remaining short (20) is a position, so BLOCKED, not COOLING.
        book = [_row("2026-04-01", A, 100, 50),
                _row("2026-05-01", A, -150, 40),
                _row("2026-05-05", A, 30, 41)]
        r = _rows("canada", book)[A]
        self.assertEqual(r["category"], "BLOCKED", r["advisory"])
        self.assertAlmostEqual(r["recent_loss"], 1030.0, places=4)
        self.assertAlmostEqual(r["recent_loss_qty"], 130.0, places=4)
        self.assertEqual(r["clears_at"], "2026-06-05")
        self.assertIn("$7.9231 of it for each unit bought back",
                      r["advisory"])

    def test_exitable_safe_date_and_countdown(self):
        r = _rows("canada", [_row("2026-05-15", A, 100, 50)])[A]
        self.assertEqual(r["category"], "EXITABLE")
        self.assertEqual(r["clears_at"], "2026-06-15")
        self.assertEqual(r["clears_in_at_generation"], "2026-06-15 (26d)")
        self.assertIn("a PARTIAL loss sale before 2026-06-15 is "
                      "superficial", r["advisory"])

    def test_cooling_countdown(self):
        r = _rows("canada", [_row("2026-04-01", A, 100, 50),
                             _row("2026-05-01", A, -100, 40)])[A]
        self.assertEqual(r["category"], "COOLING")
        self.assertEqual(r["clears_in_at_generation"], "2026-06-01 (12d)")
        self.assertEqual((r["recent_loss"], r["recent_loss_qty"]),
                         (1000.0, 100.0))

    def test_sheltered_caveat_safe_date(self):
        # rrsp bought 05-04 and sold out 05-08: CAUTION until 05-04 + 31.
        r = _rows("canada", [_row("2026-01-02", A, 100, 50)],
                  [_row("2026-05-04", A, 10, 45, "rrsp"),
                   _row("2026-05-08", A, -10, 46, "rrsp")])[A]
        self.assertEqual(r["category"], "CAUTION")
        self.assertEqual(r["clears_at"], "2026-06-04")
        self.assertEqual(r["clears_in_at_generation"], "2026-06-04 (15d)")

    def test_two_lot_loss_sale_is_a_violation_in_canada(self):
        # The own-pool money sign (a BUY's cost as a magnitude, books
        # spell it either sign): 200 sold @40 from an average of 55.
        book = [_row("2026-01-02", A, 100, 50, rid="x1"),
                _row("2026-02-02", A, 100, 60, rid="x2"),
                _row("2026-05-01", A, -200, 40, rid="x3"),
                _row("2026-05-10", A, 50, 41, rid="x4")]
        r = _rows("canada", book)[A]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["denied_qty"], 50.0)


@rule("CA-PLAN-02")
class TestCaRadarCallRescue(unittest.TestCase):
    """A2-0187: the rescue of a loss a long call replaces names the
    taxable holder (no account) and no registered-account clause."""

    def test_long_call_rescue_holder(self):
        book = [_row("2026-04-01", "III.US", 100, 50),
                _row("2026-05-01", "III.US", -100, 40),
                _row("2026-05-05", "III260918C00045000.US", 2, 3)]
        r = _rows("canada", book)["III.US"]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertEqual(r["denied_qty"], 100.0)
        self.assertEqual(r["rescue"], [{
            "account": "", "call": True, "holder": "taxable", "qty": 2.0,
            "symbol": "III260918C00045000.US"}])
        self.assertIn("Sell 2 long call contract(s) (taxable "
                      "III260918C00045000.US 2)", r["advisory"])
        self.assertNotIn("PERMANENTLY", r["advisory"])

    def test_mixed_holders_rescue(self):
        book = [_row("2026-04-01", A, 100, 50),
                _row("2026-05-01", A, -100, 40),
                _row("2026-05-06", A, 20, 41)]
        r = _rows("canada", book, [_row("2026-05-05", A, 30, 41, "ira")])[A]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertEqual(r["rescue"], [
            {"account": "ira", "holder": "sheltered", "qty": 30.0,
             "symbol": A},
            {"account": "", "holder": "taxable", "qty": 20.0, "symbol": A}])
        self.assertIn("denied PERMANENTLY unless that account sells too",
                      r["advisory"])


@rule("CA-PLAN-01")
class TestCaRadarCryptoSameDay(unittest.TestCase):
    """A2-0187 (R1-240): a crypto rescue sale may trade on the settle
    bound itself."""

    def test_crypto_deadline_is_the_settle_bound(self):
        book = [_row("2026-04-01", "BTC", 0.5, 60000),
                _row("2026-05-01", "BTC", -0.3, 50000),
                _row("2026-05-05", "BTC", 0.1, 51000)]
        r = _rows("canada", book)["BTC"]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertEqual(r["clears_at"], "2026-05-31")
        self.assertIn("by 2026-05-31 (it settles the same day)",
                      r["advisory"])


# ------------------------------------------- Canada: own-pool arithmetic
def _cad(i, d, q, net, action="BUYSELL", sym="XYZ.TO", **kw):
    r = dict(id=i, date=d, date_settle=d, time="10:00:00", action=action,
             symbol=sym, quantity=q, price=abs(net / q) if q else 0.0,
             net_amount=net, commission=0.0, currency="CAD",
             account="margin")
    r.update(kw)
    return r


@rule("CA-PLAN-01")
class TestCaRadarOwnPool(unittest.TestCase):
    """A2-0525, A2-1574, A2-0543 (the radar's own pool, no --gains)."""

    def test_crossing_sale_proceeds_are_prorated(self):
        # Long 100 @10, sell 150 @9: the loss is 100 on the 100 closed
        # (not a gain from netting all 150 units' proceeds); the rebuy
        # replaces it.
        book = [_cad("b1", "2026-08-03", 100, -1000.0),
                _cad("s1", "2026-09-10", -150, 1350.0),
                _cad("c1", "2026-09-11", 50, -450.0),
                _cad("b2", "2026-09-25", 100, -900.0)]
        r = _rows("canada", book, as_of="2026-09-28")["XYZ.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["denied_qty"], 100.0)

    def test_flip_side_short_opens_at_prorated_proceeds(self):
        # Long 100 @10, sell 150 @10 (a 50 short at 500), cover 50 @12:
        # a 100 loss; a re-short leaves a position -> BLOCKED.
        book = [_cad("b1", "2026-08-03", 100, -1000.0),
                _cad("s1", "2026-08-10", -150, 1500.0),
                _cad("c1", "2026-09-21", 50, -600.0),
                _cad("s2", "2026-09-25", -10, 120.0)]
        r = _rows("canada", book, as_of="2026-09-28")["XYZ.TO"]
        self.assertEqual(r["category"], "BLOCKED", r["advisory"])
        self.assertAlmostEqual(r["recent_loss"], 100.0, places=4)
        self.assertAlmostEqual(r["recent_loss_qty"], 50.0, places=4)

    def test_zero_value_transfer_in_carries_the_average_cost(self):
        # 100 @10 plus 50 transferred in at no stated value: 150 at an
        # average of 10, sold @11 -> a GAIN, so the rebuy is only a
        # recent buy (EXITABLE), never a VIOLATION.
        book = [_cad("b1", "2026-08-03", 100, -1000.0),
                _cad("x1", "2026-08-10", 50, 0.0, action="TRANSFER"),
                _cad("s1", "2026-09-21", -150, 1650.0),
                _cad("b2", "2026-09-25", 10, -90.0)]
        r = _rows("canada", book, as_of="2026-09-28")["XYZ.TO"]
        self.assertEqual(r["category"], "EXITABLE", r["advisory"])

    def test_roc_on_an_empty_pool_does_not_touch_the_next_position(self):
        book = [_cad("a", "2026-01-05", 100, -2000.0, sym="ER.TO"),
                _cad("b", "2026-02-02", -100, 2100.0, sym="ER.TO"),
                _cad("c", "2026-02-20", 0, -300.0, action="ADJUST",
                     sym="ER.TO"),
                _cad("d", "2026-03-02", 100, -2000.0, sym="ER.TO"),
                _cad("e", "2026-09-08", -100, 1800.0, sym="ER.TO"),
                _cad("f", "2026-09-10", 100, -1800.0, sym="ER.TO")]
        r = _rows("canada", book, as_of="2026-09-20")["ER.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["denied_qty"], 100.0)

    def test_rename_carries_the_pool_cost(self):
        book = [_cad("a", "2026-01-05", 100, -2000.0, sym="OLD.TO"),
                _cad("b", "2026-05-01", 1, 0.0, action="SPLIT",
                     sym="OLD.TO", symbol_new="NEW.TO"),
                _cad("c", "2026-09-08", -100, 1500.0, sym="NEW.TO"),
                _cad("d", "2026-09-10", 100, -1500.0, sym="NEW.TO")]
        r = _rows("canada", book, as_of="2026-09-20")["NEW.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["denied_qty"], 100.0)

    def test_day_30_after_the_loss_is_inside_the_window(self):
        # A2-0944: loss 03-03, rebuy on day +30 (04-02), still held.
        book = [_cad("a", "2025-01-06", 100, -2000.0),
                _cad("b", "2025-03-03", -100, 1000.0),
                _cad("c", "2025-04-02", 50, -500.0)]
        r = _rows("canada", book, as_of="2025-04-02")["XYZ.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["denied_qty"], 50.0)


# --------------------------------------------------- US: engine amounts
@rule("US-PLAN-01")
class TestUsRadarEngineAmounts(unittest.TestCase):
    """A2-0181, A2-0189, A2-0190, A2-1579: the US verdict and its
    amounts are the US engine's (FIFO per account, every account)."""

    def test_fifo_per_account_basis(self):
        # B's own lot @30 sold @40 is a GAIN; pooled with A's @50 it
        # would be a loss.
        book = [_row("2026-01-02", A, 10, 50, "A"),
                _row("2026-01-05", A, 10, 30, "B"),
                _row("2026-05-01", A, -10, 40, "B")]
        r = _rows("usa", book)[A]
        self.assertEqual(r["category"], "CLEAR", r["advisory"])

    def test_multi_lot_loss_amounts(self):
        book = [_row("2026-01-02", A, 100, 50, rid="x1"),
                _row("2026-02-02", A, 100, 60, rid="x2"),
                _row("2026-05-01", A, -200, 40, rid="x3"),
                _row("2026-05-10", A, 50, 41, rid="x4")]
        r = _rows("usa", book)[A]
        self.assertEqual(r["category"], "WASHED")
        adv = r["advisory"]
        self.assertIn("the loss of $3000.00 on 2026-05-01", adv)
        self.assertIn("$500.00 disallowed", adv)
        self.assertIn("('margin' bought 50 on 2026-05-10)", adv)
        self.assertIn("$500.00 is added to the replacement's basis", adv)
        self.assertIn("The remaining $2500.00 of loss is disallowed too if "
                      "you buy again before 2026-06-01.", adv)
        self.assertNotIn("lost for good", adv)
        self.assertEqual(r["denied_qty"], 50.0)
        self.assertEqual(r["clears_at"], "2026-06-01")
        self.assertEqual(r["clears_in_at_generation"], "2026-06-01 (12d)")

    def test_two_taxable_accounts(self):
        book = [_row("2026-01-02", A, 100, 30, "cashB", rid="x1"),
                _row("2026-02-02", A, 100, 50, "margA", rid="x2"),
                _row("2026-05-01", A, -100, 40, "margA", rid="x3"),
                _row("2026-05-10", A, 100, 41, "margA", rid="x4")]
        r = _rows("usa", book)[A]
        self.assertEqual(r["category"], "WASHED", r["advisory"])
        self.assertIn("the loss of $1000.00 on 2026-05-01", r["advisory"])
        self.assertIn("$1000.00 disallowed", r["advisory"])
        self.assertNotIn("remaining", r["advisory"])
        self.assertIsNone(r["clears_at"])
        self.assertEqual(r["denied_qty"], 100.0)

    def test_cooling_amounts_from_the_engine(self):
        r = _rows("usa", [_row("2026-04-01", A, 100, 50),
                          _row("2026-05-01", A, -100, 40)])[A]
        self.assertEqual(r["category"], "COOLING")
        self.assertEqual((r["recent_loss"], r["recent_loss_qty"]),
                         (1000.0, 100.0))
        self.assertIn("Loss of $1000.0000 on 2026-05-01. Safe to re-enter "
                      "on 2026-06-01.", r["advisory"])
        self.assertEqual(r["clears_in_at_generation"], "2026-06-01 (12d)")

    def test_ira_replacement_is_lost_for_good_not_added_to_basis(self):
        r = _rows("usa", [_row("2026-04-01", A, 100, 50),
                          _row("2026-05-01", A, -100, 40)],
                  [_row("2026-05-05", A, 30, 41, "ira")])[A]
        adv = r["advisory"]
        self.assertEqual(r["category"], "WASHED")
        self.assertIn("$300.00 disallowed", adv)
        self.assertIn("(IRA 'ira' bought 30 on 2026-05-05)", adv)
        self.assertIn("$300.00 matched an IRA purchase and is lost for "
                      "good.", adv)
        self.assertNotIn("added to the replacement's basis", adv)
        self.assertIn("The remaining $700.00", adv)
        self.assertEqual(r["denied_qty"], 30.0)

    def test_taxable_and_ira_replacements_split_the_amount(self):
        r = _rows("usa", [_row("2026-04-01", A, 100, 50),
                          _row("2026-05-01", A, -100, 40),
                          _row("2026-05-06", A, 20, 41)],
                  [_row("2026-05-05", A, 30, 41, "ira")])[A]
        adv = r["advisory"]
        self.assertIn("$500.00 disallowed", adv)
        self.assertIn("(IRA 'ira' bought 30 on 2026-05-05; 'margin' bought "
                      "20 on 2026-05-06)", adv)
        self.assertIn("$200.00 is added to the replacement's basis", adv)
        self.assertIn("$300.00 matched an IRA purchase", adv)
        self.assertIn("The remaining $500.00", adv)
        self.assertEqual(r["denied_qty"], 50.0)

    def test_washed_safe_date_is_after_the_loss(self):
        r = _rows("usa", [_row("2026-04-01", A, 100, 50),
                          _row("2026-05-01", A, -100, 40),
                          _row("2026-05-10", A, 40, 41)])[A]
        self.assertEqual(r["category"], "WASHED")
        self.assertEqual(r["clears_at"], "2026-06-01")
        self.assertIn("$400.00 is added to the replacement's basis", r[
            "advisory"])
        self.assertIn("remaining $600.00", r["advisory"])

    def test_us_exitable_safe_date(self):
        r = _rows("usa", [_row("2026-05-15", A, 100, 50)])[A]
        self.assertEqual(r["category"], "EXITABLE")
        self.assertEqual(r["clears_at"], "2026-06-15")
        self.assertEqual(r["clears_in_at_generation"], "2026-06-15 (26d)")


# -------------------------------------------------- country gates
_W = {
    # a registered buy 10 days ago, still held
    "LOCKED": ([_row("2026-01-02", A, 100, 50)],
               [_row("2026-05-10", A, 10, 45, "ira")]),
    # a taxable buy 5 days ago; registered shares bought long before
    "EXIT": ([_row("2026-01-02", A, 100, 50),
              _row("2026-05-15", A, 10, 45)],
             [_row("2026-01-05", A, 10, 50, "ira")]),
    # registered shares, no buys in 30 days
    "RISK": ([_row("2026-01-02", A, 100, 50)],
             [_row("2026-01-05", A, 10, 50, "ira")]),
}


@rule("CA-PLAN-01")
@rule_absent("CA-PLAN-01", country="usa")
@rule("US-PLAN-01")
@rule_absent("US-PLAN-01", country="canada")
class TestRadarWordingByCountry(unittest.TestCase):
    """A2-0500, A2-0546, A2-0863, A2-0943, A2-1580, A2-1595: each
    country's law in its own report, never the other's."""

    def _both(self, name):
        t, s = _W[name]
        return {c: _rows(c, t, s)[A] for c in ("canada", "usa")}

    def test_locked(self):
        r = self._both("LOCKED")
        ca, us = r["canada"]["advisory"], r["usa"]["advisory"]
        for row in r.values():
            self.assertEqual(row["category"], "LOCKED")
            self.assertEqual(row["clears_at"], "2026-06-10")
        self.assertIn("Recent buy in SHELTERED account(s):", ca)
        self.assertIn("is a superficial loss for up to 10 of your 100", ca)
        self.assertIn("permanently denied unless the registered account "
                      "sells them within 30 days after your sale", ca)
        self.assertNotIn("IRA", ca)
        self.assertNotIn("wash sale", ca)
        self.assertIn("Recent buy in IRA(s):", us)
        self.assertIn("is a wash sale for up to 10 of your 100", us)
        self.assertIn("even if the IRA has sold", us)
        self.assertNotIn("superficial", us)
        self.assertNotIn("SHELTERED", us)
        self.assertNotIn("unless the registered account sells", us)

    def test_exitable_pre_window_holding(self):
        r = self._both("EXIT")
        ca, us = r["canada"]["advisory"], r["usa"]["advisory"]
        self.assertIn("a PARTIAL loss sale before 2026-06-15 is "
                      "superficial", ca)
        self.assertIn("Sheltered accounts hold 10 sh bought before the "
                      "window", ca)
        self.assertNotIn("IRA", ca)
        self.assertNotIn("wash sale", ca)
        self.assertIn("a PARTIAL loss sale before 2026-06-15 is a wash "
                      "sale", us)
        self.assertIn("IRAs hold 10 sh bought before the window", us)
        self.assertNotIn("superficial", us)
        self.assertNotIn("Sheltered", us)
        self.assertNotIn("DRIP", us)

    def test_risk_advisory_and_section_titles(self):
        r = self._both("RISK")
        ca, us = r["canada"]["advisory"], r["usa"]["advisory"]
        self.assertTrue(ca.startswith("RISK: Sellable at a loss NOW"))
        self.assertIn("sheltered accounts still hold", ca)
        self.assertIn("Pause DRIPs/sheltered adds", ca)
        self.assertNotIn("IRA", ca)
        self.assertNotIn("Rev. Rul.", ca)
        self.assertIn("an IRA still holds", us)
        self.assertIn("(Rev. Rul. 2008-5)", us)
        self.assertIn("Pause IRA buys/reinvestment", us)
        self.assertNotIn("sheltered", us.lower())
        self.assertNotIn("DRIP", us)
        t, s = _W["RISK"]
        titles = {c: {sec["category"]: sec["title"]
                      for sec in _run(c, t, s)["sections"]}
                  for c in ("canada", "usa")}
        self.assertEqual(titles["canada"]["RISK"],
                         "RISK — sellable now; sheltered still holds "
                         "(pause DRIPs 30 days after selling)")
        self.assertEqual(titles["usa"]["RISK"],
                         "RISK — sellable now; an IRA still holds (pause "
                         "IRA buys and dividend reinvestment 30 days after "
                         "selling)")
        self.assertEqual(titles["canada"]["VIOLATION"],
                         "VIOLATION — superficial loss; act to rescue the "
                         "loss")
        self.assertTrue(titles["usa"]["WASHED"].startswith("WASHED"))
        for c in ("canada", "usa"):
            for cat, title in titles[c].items():
                self.assertTrue(title.startswith(cat), (c, cat, title))
        txt = {c: _run(c, t, s, json_out=False) for c in ("canada", "usa")}
        self.assertIn("--- RISK — sellable now; sheltered still holds",
                      txt["canada"])
        self.assertNotIn("an IRA still holds", txt["canada"])
        self.assertIn("--- RISK — sellable now; an IRA still holds",
                      txt["usa"])


@rule("CA-PLAN-02")
@rule_absent("CA-PLAN-02", country="usa")
@rule("US-PLAN-02")
@rule_absent("US-PLAN-02", country="canada")
class TestRadarShortsAndCallsByCountry(unittest.TestCase):
    """A2-0500 / A2-0545 / A2-0942 / A2-1616."""

    def test_us_short_trigger_is_a_short_sale(self):
        # A held short with a recent short add: EXITABLE in the US
        # (§1091(e): the short sale is the trigger). Canada: a short sale
        # acquires nothing (CLEAR).
        def u(d, sym, q, net):
            return _row(d, sym, q, abs(net / q), net=net)
        book = [u("2026-03-02", "SHO.US", -100, 2000.0),
                u("2026-09-20", "SHO.US", -10, 230.0)]
        r = {c: _rows(c, book, as_of="2026-09-25")["SHO.US"]
             for c in ("canada", "usa")}
        self.assertEqual(r["usa"]["category"], "EXITABLE")
        self.assertIn("Recent short sale in 'margin' on 2026-09-20",
                      r["usa"]["advisory"])
        self.assertEqual(r["canada"]["category"], "CLEAR")

    def test_us_short_with_an_ira_buy_is_clear(self):
        # A long IRA buy never triggers a US short-cover loss; in Canada
        # it does (CA-SL-07).
        book = [_row("2026-03-02", "SHO.US", -100, 20)]
        ira = [_row("2026-05-10", "SHO.US", 10, 21, "ira")]
        r = {c: _rows(c, book, ira)["SHO.US"] for c in ("canada", "usa")}
        self.assertEqual(r["usa"]["category"], "CLEAR", r["usa"]["advisory"])
        self.assertEqual(r["canada"]["category"], "LOCKED")

    def test_us_long_call_leaves_the_row_cooling(self):
        call = "XYZ261218C00050000.US"
        book = [_row("2026-01-05", "XYZ.US", 100, 50),
                _row("2026-09-01", "XYZ.US", -100, 40),
                _row("2026-09-05", call, 1, 3)]
        r = {c: _rows(c, book, as_of="2026-09-10")["XYZ.US"]
             for c in ("canada", "usa")}
        self.assertEqual(r["usa"]["category"], "COOLING",
                         r["usa"]["advisory"])
        self.assertEqual(r["usa"]["clears_at"], "2026-10-02")
        self.assertIn("NOTE: a long call on these shares was bought inside "
                      "the window", r["usa"]["advisory"])
        self.assertEqual(r["canada"]["category"], "VIOLATION")

    def test_us_call_note_at_exactly_30_days(self):
        # A2-1616: a call bought on day +30 (trade dates) gets the
        # US-WASH-12 note; on day +31 it does not.
        call = "XYZ261218C00050000.US"
        for d, want in (("2026-08-02", True), ("2026-09-30", True),
                        ("2026-08-01", False)):
            book = [_row("2026-01-05", "XYZ.US", 100, 50),
                    _row(d, call, 1, 3),
                    _row("2026-09-01", "XYZ.US", -100, 40)]
            adv = _rows("usa", book, as_of="2026-09-30")["XYZ.US"][
                "advisory"]
            self.assertEqual("bought inside the window" in adv, want,
                             (d, adv))


@rule("US-PLAN-01")
class TestUsRadarGates(unittest.TestCase):
    """A2-0545: the US walk orders by trade date (no lag-split
    re-denomination) and keeps a short-cover loss as a note."""

    @staticmethod
    def _r(d, ds, q, px, net, action="BUYSELL", **kw):
        return _row(d, "XYZ.US", q, px, "m", action=action, settle=ds,
                    net=net, **kw)

    def test_sale_traded_before_a_split_that_settles_after_it(self):
        book = [self._r("2026-08-03", "2026-08-04", 100, 50.0, 5000.0),
                self._r("2026-09-17", "2026-09-21", -84, 40.0, 3360.0),
                self._r("2026-09-18", "2026-09-18", 2.0, 0.0, 0.0,
                        action="SPLIT", symbol_new="XYZ.US"),
                self._r("2026-09-22", "2026-09-23", 10, 21.0, 210.0)]
        r = _rows("usa", book, as_of="2026-10-01")["XYZ.US"]
        self.assertEqual(r["category"], "WASHED", r["advisory"])
        self.assertEqual(r["taxable_qty"], 42.0)
        self.assertIn("The remaining $790.00", r["advisory"])

    def test_short_cover_loss_note(self):
        book = [self._r("2026-08-03", "2026-08-04", -100, 40.0, 4000.0),
                self._r("2026-09-15", "2026-09-16", 100, 50.0, 5000.0),
                self._r("2026-09-22", "2026-09-23", 100, 49.0, 4900.0)]
        r = _rows("usa", book, as_of="2026-10-01")["XYZ.US"]
        self.assertEqual(r["category"], "EXITABLE", r["advisory"])
        self.assertIn("NOTE: the loss of $1000.0000 on covering a short on "
                      "2026-09-15 is disallowed only by a new SHORT sale "
                      "before 2026-10-16", r["advisory"])
        self.assertEqual(r["short_cover_loss"]["reshort_ok_from"],
                         "2026-10-16")




@rule("CA-DATE-02")
class TestRadarGainsCoverageOnTradeBasis(unittest.TestCase):
    """A2-0938: on a trade-date project (tax_date = "trade") the engine's
    gains file covers a Dec-31 trade that settles in January — the radar
    reads the engine's verdict for it, not its own pool."""

    def test_rows_for_uses_the_trade_date(self):
        from types import SimpleNamespace
        from taxjson.bin.taxjson_wash_radar import _EngineLosses
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_gains_wash.json"
            p.write_text(json.dumps({
                "summary": {"year": 2025, "tax_date_basis": "trade"},
                "metadata": {"account": "margin"},
                "transactions": [{"id": "s1", "qty": 100, "raw_gain": -300.0,
                                  "account": "margin", "direction": "LONG"}]}))
            eng = _EngineLosses.load([str(p)])
        tx = SimpleNamespace(account="margin", id="s1", date="2025-12-31",
                             date_settle="2026-01-02")
        rows = eng.rows_for(tx)
        self.assertIsNotNone(rows)
        self.assertEqual([r["raw_gain"] for r in rows], [-300.0])
        # A row traded in January is outside the 2025 trade-basis file.
        tx2 = SimpleNamespace(account="margin", id="s2", date="2026-01-02",
                              date_settle="2026-01-05")
        self.assertIsNone(eng.rows_for(tx2))


if __name__ == "__main__":
    unittest.main()
