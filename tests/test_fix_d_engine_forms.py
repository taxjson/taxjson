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
        # Each country's own loss term (A2-0830): never the CRA's in the
        # US run.
        self.assertIn("superficial", r["canada"]["_stderr"])
        self.assertNotIn("superficial", r["usa"]["_stderr"])

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


def _grant_row(symbol="ZZQ250815C00082500.US", cost=-811.91, fee=5.41,
               commission=0.0, gain=None, date="2025-07-02"):
    return {"date": date, "date_settle": date, "symbol": symbol, "qty": 1.0,
            "proceeds": 0.0, "cost": cost,
            "gain": -cost if gain is None else gain, "raw_gain": -cost,
            "disallowed_amount": 0.0, "days_held": 0, "term": None,
            "direction": "SHORT", "commission": commission, "fee": fee,
            "account": "margin", "is_option": True, "grant": True}


class TestScheduleThreeWrittenPremiumGross(unittest.TestCase):
    """R1-40: a written option's premium is shown GROSS as proceeds and
    its write commission as an outlay; the gain is unchanged."""

    def _row(self, entries):
        from taxjson.bin.taxjson_form_export import build_schedule3
        rep = build_schedule3(entries, 2025)
        self.assertEqual(len(rep["rows"]), 1)
        return rep["rows"][0], rep

    @rule("CA-DISP-06")
    def test_grant_row_gross_premium_and_commission_outlay(self):
        r, _ = self._row([_grant_row()])
        self.assertAlmostEqual(r["proceeds"], 817.32)
        self.assertAlmostEqual(r["outlays"], 5.41)
        self.assertAlmostEqual(r["acb"], 0.0)
        self.assertAlmostEqual(r["gain"], 811.91)
        self.assertAlmostEqual(r["proceeds"] - r["acb"] - r["outlays"],
                               r["gain"], places=2)

    @rule("CA-DISP-06")
    def test_buyback_commission_stays_in_the_acb(self):
        # The buy-back row's fee is an acquisition cost (in the ACB): it
        # must not be added to proceeds or outlays.
        back = {"date": "2025-07-11", "date_settle": "2025-07-14",
                "symbol": "ZZQ250815C00082500.US", "qty": 1.0,
                "proceeds": -273.80, "cost": 0.0, "gain": -273.80,
                "raw_gain": -273.80, "disallowed_amount": 0.0,
                "days_held": 9, "term": None, "direction": "SHORT",
                "commission": 0.0, "fee": 1.05, "account": "margin",
                "is_option": True, "grant": False}
        r, _ = self._row([_grant_row(), back])
        self.assertAlmostEqual(r["proceeds"], 817.32)
        self.assertAlmostEqual(r["outlays"], 5.41)
        self.assertAlmostEqual(r["acb"], 273.80)
        self.assertAlmostEqual(r["gain"], 538.11)

    @rule("CA-DISP-06")
    def test_debit_write_shows_premium_and_commission(self):
        # Commission 5.00 above a 2.00 premium: net debit 3.00.
        r, _ = self._row([_grant_row(cost=3.0, fee=5.0, gain=-3.0)])
        self.assertAlmostEqual(r["proceeds"], 2.0)
        self.assertAlmostEqual(r["outlays"], 5.0)
        self.assertAlmostEqual(r["acb"], 0.0)
        self.assertAlmostEqual(r["gain"], -3.0)

    @rule("CA-DISP-06")
    def test_line_totals_and_filing_lines_agree(self):
        from taxjson.bin.taxjson_form_export import filing_lines
        rows = [_grant_row(), _grant_row(symbol="ZZQ250919P00070000.US",
                                         cost=-100.0, fee=1.0,
                                         commission=0.5)]
        lines = filing_lines(rows, 2025)
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0]["proceeds"], 817.32 + 101.5)
        self.assertAlmostEqual(lines[0]["outlays"], 5.41 + 1.5)
        self.assertAlmostEqual(lines[0]["gain"], 911.91)

    @rule("CA-DISP-06")
    def test_engine_grant_write_end_to_end(self):
        opt = "ZZQ250815C00082500.US"
        book = [tx("BUYSELL", "2025-07-01", opt, -1, 811.91,
                   settle="2025-07-02", price=8.1732, fee=5.41),
                tx("BUYSELL", "2025-07-11", opt, 1, 273.80,
                   settle="2025-07-14", price=2.738)]
        res = gains_one(C.CANADA, book, year=2025,
                        option_premium_timing="grant",
                        option_grant_since=2025)
        from taxjson.bin.taxjson_form_export import build_schedule3
        rep = build_schedule3(res["transactions"], 2025)
        r = rep["rows"][0]
        self.assertAlmostEqual(r["outlays"], 5.41, places=2)
        self.assertAlmostEqual(r["proceeds"], 817.32, places=2)
        self.assertAlmostEqual(r["gain"], 538.11, places=2)

    def test_reconcile_slips_gross_includes_the_write_commission(self):
        import json
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_reconcile_slips import load_computed
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_gains.json"
            p.write_text(json.dumps({"transactions": [_grant_row()]}))
            c = load_computed([p], 2025)
        rec = next(iter(c.values()))
        self.assertAlmostEqual(rec["proceeds_net"], 811.91)
        self.assertAlmostEqual(rec["proceeds_gross"], 817.32)


class TestIBTrustDistributionKeepsPayDate(unittest.TestCase):
    """S057-23 (documentation; owner decision 2026-10-01): an IB row on a
    Canadian trust has an accrual EX date but no record date and no
    'distribution' label, so a Canada project keeps its pay date."""

    @rule("CA-INC-DATE-TRUST")
    def test_ib_style_row_with_december_ex_date_stays_in_pay_year(self):
        book = [tx("BUYSELL", "2024-06-03", "ZZT.TO", 3000, 30000,
                   currency="CAD"),
                tx("DIVIDEND", "2025-01-08", "ZZT.TO", 0, 513.0,
                   gross_amount=513.0, type="dividend", currency="CAD",
                   ex_date="2024-12-31", issuer_country="CA",
                   description="ZZT(CA0000000000) Cash Dividend CAD 0.171 "
                               "per Share (Ordinary Dividend)")]
        got = [y for y in (2024, 2025)
               if any(t.get("action") == "DIVIDEND" for t in
                      gains_one(C.CANADA, book, year=y)["transactions"])]
        self.assertEqual(got, [2025])

    def test_tax_logic_says_why(self):
        from taxjson.lib import tax_logic as TL
        text = TL.catalog("canada")["CA-INC-DATE-TRUST"].text
        self.assertIn("IB prints no record date", text)
        self.assertIn("trust cannot be told from", text)


class TestAffiliatedWorkaroundDocumented(unittest.TestCase):
    """S004-08 (documentation; owner decision 2026-10-01): an affiliated
    person's account declared `sheltered` denies the loss for good."""

    @rule("CA-SL-04")
    def test_spouse_account_as_sheltered_denies_permanently(self):
        from taxjson.lib import tax_logic as TL
        self.assertIn('type = "sheltered"',
                      TL.catalog("canada")["CA-SL-04"].text)
        book = [tx("BUYSELL", "2025-01-02", "ZZE.TO", 100, 3000,
                   currency="CAD"),
                tx("BUYSELL", "2025-03-03", "ZZE.TO", -100, 1000,
                   currency="CAD")]
        spouse = [tx("BUYSELL", "2025-03-13", "ZZE.TO", 100, 1000,
                     currency="CAD", account="spouse")]
        import contextlib
        import copy
        import io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        with contextlib.redirect_stderr(io.StringIO()):
            res = run_gains(copy.deepcopy(book), copy.deepcopy(spouse), [],
                            req=GainsRequest(country=C.CANADA, taxable=True,
                                             year=2025))
        self.assertAlmostEqual(res["summary"]["total_disallowed"], 2000.0)
        (sale,) = [t for t in res["transactions"] if t.get("qty")]
        self.assertAlmostEqual(sale["permanently_disallowed"], 2000.0)


if __name__ == "__main__":
    unittest.main()
