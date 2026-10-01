"""Owner-decisions round, area engine-forms.

S069-23: a call on an ADJUSTED option series (root + digit, e.g. ZZS1)
or an option on the same FUTURES contract bought in a loss's window is
flagged for a manual check (warn-only, both countries), the way a
warrant is (CA-SL-14 / US-WASH-14).

R1-40: the Canada Schedule 3 shows a written option's premium GROSS as
proceeds with its write commission as an outlay (gain unchanged).
"""
from __future__ import annotations

import unittest

from taxjson.lib import country as C
from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx


def _book(loss_sym, opt_sym, qty, opt_date="2025-10-06"):
    return [tx("BUYSELL", "2025-09-02", loss_sym, qty, 6000,
               settle="2025-09-03"),
            tx("BUYSELL", "2025-10-01", loss_sym, -qty, 5000,
               settle="2025-10-02"),
            tx("BUYSELL", opt_date, opt_sym, 1, 300,
               settle="2025-10-07", price=3.0)]


def gains_one(country, book, **req):
    import contextlib
    import copy
    import io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)), [], [],
                        req=GainsRequest(country=country, taxable=True,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _rules(res):
    return [w["rule"] for w in res.get("option_replacement_warnings") or []]


class TestUnresolvedOptionReplacementFlag(unittest.TestCase):
    """S069-23."""

    @rule("CA-SL-15")
    @rule_absent("CA-SL-15", country="usa")
    @rule("US-WASH-15")
    @rule_absent("US-WASH-15", country="canada")
    def test_adjusted_series_call_is_flagged_in_both(self):
        r = gains_both(_book("ZZS.US", "ZZS1251219C00050000.US", 100),
                       year=2025)
        for c in C.COUNTRIES:
            self.assertEqual(_rules(r[c]), ["adjusted_option_vs_loss"], c)
            self.assertIn("adjusted_option_vs_loss", r[c]["_stderr"])
            self.assertIn("review it by hand", r[c]["_stderr"])
            # Warn-only: nothing denied, the loss stands.
            self.assertEqual(r[c]["summary"]["total_disallowed"], 0, c)
        self.assertIn("superficial", r["canada"]["_stderr"])
        self.assertNotIn("§1091", r["canada"]["_stderr"])
        self.assertIn("wash sale", r["usa"]["_stderr"])
        self.assertNotIn("superficial", r["usa"]["_stderr"])

    @rule("CA-SL-15")
    @rule_absent("CA-SL-15", country="usa")
    @rule("US-WASH-15")
    @rule_absent("US-WASH-15", country="canada")
    def test_futures_option_after_a_futures_loss_is_flagged(self):
        r = gains_both(_book("F:CLG6.US", "F:CL260114C00060000.US", 1),
                       year=2025)
        for c in C.COUNTRIES:
            self.assertEqual(_rules(r[c]), ["futures_option_vs_loss"], c)
            self.assertEqual(r[c]["summary"]["total_disallowed"], 0, c)
        # The US note says commodity futures are usually outside §1091.
        self.assertIn("§1256", r["usa"]["_stderr"])
        self.assertNotIn("§1256", r["canada"]["_stderr"])

    @rule("CA-SL-15")
    def test_futures_prefix_spellings_match(self):
        r = gains_one(C.CANADA,
                      _book("/CLG6.US", "F:CLG6260114C00060000.US", 1),
                      year=2025)
        self.assertEqual(_rules(r), ["futures_option_vs_loss"])

    @rule("CA-SL-15")
    def test_not_flagged_outside_window_puts_or_other_roots(self):
        cases = [
            ("ZZS.US", "ZZS1251219C00050000.US", 100, "2025-11-20"),
            ("ZZS.US", "ZZS1251219P00050000.US", 100, "2025-10-06"),
            ("ZZS.US", "ZZT1251219C00050000.US", 100, "2025-10-06"),
            ("ZZS.US", "ZZS1251219C00050000.TO", 100, "2025-10-06"),
            ("F:CLG6.US", "F:NG260114C00060000.US", 1, "2025-10-06"),
        ]
        for loss, opt, q, d in cases:
            book = _book(loss, opt, q, d)
            book[-1].date_settle = d
            r = gains_one(C.CANADA, book, year=2025)
            self.assertEqual(_rules(r), [], (loss, opt, d))

    @rule("CA-SL-05")
    def test_standard_root_call_is_still_enforced_not_double_flagged(self):
        r = gains_one(C.CANADA,
                      _book("ZZS.US", "ZZS251219C00050000.US", 100),
                      year=2025)
        self.assertEqual(_rules(r), [])
        self.assertGreater(r["summary"]["total_disallowed"], 0)

    @rule("US-WASH-12")
    def test_us_standard_root_keeps_its_own_warning_only(self):
        r = gains_one(C.USA,
                      _book("ZZS.US", "ZZS251219C00050000.US", 100),
                      year=2025)
        self.assertEqual(_rules(r), ["call_vs_share_loss"])

    def test_buy_to_close_of_a_written_adjusted_call_is_not_flagged(self):
        book = _book("ZZS.US", "ZZS1251219C00050000.US", 100)
        book.insert(1, tx("BUYSELL", "2025-09-20",
                          "ZZS1251219C00050000.US", -1, 250,
                          settle="2025-09-22", price=2.5))
        r = gains_both(book, year=2025)
        for c in C.COUNTRIES:
            self.assertEqual(_rules(r[c]), [], c)


if __name__ == "__main__":
    unittest.main()
