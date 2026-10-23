"""Batch D regression pins (slip-audit): findings 93, 94, 95, 97 and 100
of the 2026-10 bug review.

Synthetic data only: made-up tickers (SAMP), issuers and accounts.
"""
import math
import unittest
from pathlib import Path

from _tmpfiles import private_tmpfile
from taxjson.lib import slip_audit as SA
from taxjson.lib.ib_dividends import Component, Payment


def _row(symbol="SAMP.US", day="2025-02-03", cur="USD", amt=10.0,
         action="DIVIDEND", tax=None, category="foreign", source="demo.csv",
         record=""):
    return SA.BookRow("margin", "id-" + symbol + day + action, action,
                      symbol, SA._root(symbol), cur, amt, amt, day,
                      tax or day, source, "99900001", category,
                      record=record)


def _pay(day, amt=10.0, cur="USD", symbol="SAMP"):
    return Payment(symbol=symbol, country="US", currency=cur, pay_date=day,
                   ex_date="", pil=False,
                   components=[Component("T5", "foreign", "Ordinary Dividend",
                                         amt, amt, 0, 0)])


def _ib(payments):
    return SA.Slip("report", "T5", "Synthetic broker", "margin", "CAD",
                   source="ib", payments=payments)


class TestRedateOwnListingFirst(unittest.TestCase):
    """93: a .tt ROC with no record= follows its own listing's
    distribution; another listing of the root only when its own has
    none in the window."""

    def test_other_listing_does_not_redate(self):
        roc = _row("SAMP.US", "2025-01-10", "USD", 10.0, "ADJUST",
                   category="roc", source="correction.tt")
        own = _row("SAMP.US", "2025-01-10", "USD", 100.0)
        other = _row("SAMP.TO", "2025-01-10", "CAD", 100.0,
                     tax="2024-12-31", record="2024-12-31")
        problems = []
        SA._redate_prior_roc([roc, own, other], 2025, problems)
        self.assertEqual(roc.tax_date, "2025-01-10")
        self.assertEqual(problems, [])

    def test_own_listing_prior_record_redates(self):
        roc = _row("SAMP.US", "2025-01-10", "USD", 10.0, "ADJUST",
                   category="roc", source="correction.tt")
        own = _row("SAMP.US", "2025-01-10", "USD", 100.0,
                   tax="2024-12-31", record="2024-12-31")
        other = _row("SAMP.TO", "2025-01-10", "CAD", 100.0)
        problems = []
        SA._redate_prior_roc([roc, own, other], 2025, problems)
        self.assertEqual(roc.tax_date, "2024-12-31")
        self.assertEqual(len(problems), 1)

    def test_interlisted_fallback_still_redates(self):
        # The ROC typed on the other listing of an interlisted fund: its
        # own listing paid nothing in the window.
        roc = _row("SAMP.US", "2025-01-10", "USD", 10.0, "ADJUST",
                   category="roc", source="correction.tt")
        other = _row("SAMP.TO", "2025-01-10", "CAD", 100.0,
                     tax="2024-12-31", record="2024-12-31")
        problems = []
        SA._redate_prior_roc([roc, other], 2025, problems)
        self.assertEqual(roc.tax_date, "2024-12-31")
        self.assertIn("record=2024-12-31", problems[0])
        self.assertIn("CA-INC-DATE-ROC-TRUST", problems[0])


class TestPaymentCurrency(unittest.TestCase):
    """94: a payment is never matched to a book row of another
    currency; the only candidate in another currency is a currency
    mismatch, not a match and not two missing payments."""

    def _match(self, pays, rows):
        issues = []
        out = SA._match_payments("margin", "report", [_ib(pays)], rows,
                                 2025, issues)
        return out, issues

    def test_other_currency_only_is_a_mismatch(self):
        out, issues = self._match([_pay("2025-02-03", 100.0, "USD")],
                                  [_row("SAMP.TO", cur="CAD", amt=100.0)])
        self.assertEqual(out["matched"], 0)
        self.assertEqual(out["missing_from_books"], [])
        self.assertEqual(out["missing_from_slip"], [])
        self.assertEqual(out["amount_differs"], [])
        self.assertEqual(out["currency_differs"], [{
            "symbol": "SAMP", "date": "2025-02-03", "currency": "USD",
            "slip": 100.0, "books": 100.0, "books_currency": "CAD",
            "books_date": "2025-02-03", "books_symbol": "SAMP.TO"}])
        self.assertEqual([i["kind"] for i in issues], ["currency-differs"])
        self.assertIn("IB's report: 100.00 USD; books: 100.00 CAD on "
                      "2025-02-03", issues[0]["text"])
        rep = {"year": 2025, "tolerance": 1.0, "sources": [],
               "annual_average": {}, "coverage": {"no_slip": [],
                                                  "slips_without_books": []},
               "accounts": [{"account": "margin", "groups": [{
                   "broker_account": None, "buckets": [],
                   "payments": out, "record_year": []}]}],
               "suggestions": {"capital_gains_dividends": [],
                               "tt_lines": [], "notes": []}}
        text = "\n".join(SA.render(rep, width_=120))
        self.assertIn("Currency differs: SAMP 2025-02-03 slip 100.00 USD, "
                      "books 100.00 CAD on 2025-02-03", text)

    def test_same_currency_still_preferred(self):
        out, issues = self._match(
            [_pay("2025-02-03", 100.0, "USD")],
            [_row("SAMP.TO", cur="CAD", amt=100.0),
             _row("SAMP.US", day="2025-02-04", cur="USD", amt=100.0)])
        self.assertEqual(out["matched"], 1)
        self.assertEqual(out["currency_differs"], [])
        self.assertEqual([e["currency"] for e in out["missing_from_slip"]],
                         ["CAD"])
        self.assertEqual([i["kind"] for i in issues], ["missing-from-slip"])


class TestSecuritiesPerCurrency(unittest.TestCase):
    """95: one security's CAD and USD slip amounts are two rows, two
    comparisons and two suggestions, never one sum in the last
    currency."""

    def _t3(self, cur, roc=10.0):
        return SA.Slip("slips.toml", "T3", "Synthetic fund", "margin", cur,
                       security="SAMP", amounts={"roc": roc})

    def test_two_currencies_two_rows(self):
        sugg, notes = {}, []
        out = SA._securities("margin", "report",
                             [self._t3("CAD"), self._t3("USD")], [], 2025,
                             1.0, [], sugg, notes)
        self.assertEqual([(r["currency"], r["slip"]) for r in out],
                         [("CAD", {"roc": 10.0}), ("USD", {"roc": 10.0})])
        self.assertEqual(sugg["margin"], [
            "ADJUST 2025-12-31 16:00:00 SAMP CAD -10.00 type=roc",
            "ADJUST 2025-12-31 16:00:00 SAMP USD -10.00 type=roc"])

    def test_each_currency_compared_with_its_own_rows(self):
        rows = [_row("SAMP.TO", "2025-06-30", "CAD", 100.0,
                     category="ca_div"),
                _row("SAMP.TO", "2025-06-30", "CAD", 10.0, "ADJUST",
                     category="roc", source="roc.tt")]
        sugg, notes = {}, []
        out = SA._securities("margin", "report",
                             [self._t3("CAD"), self._t3("USD")], rows,
                             2025, 1.0, [], sugg, notes, rows)
        self.assertEqual([(r["symbol"], r["currency"], r["books"]["roc"])
                          for r in out],
                         [("SAMP.TO", "CAD", 10.0), ("SAMP.TO", "USD", 0.0)])
        self.assertEqual(sugg["margin"], [
            "ADJUST 2025-12-31 16:00:00 SAMP.TO USD -10.00 type=roc"])

    def test_one_currency_unchanged(self):
        sugg, notes = {}, []
        out = SA._securities("margin", "report",
                             [self._t3("CAD", 6.0), self._t3("CAD", 4.0)],
                             [], 2025, 1.0, [], sugg, notes)
        self.assertEqual(out, [{"symbol": "SAMP", "currency": "CAD",
                                "slip": {"roc": 10.0},
                                "books": {"cg_div": 0.0, "roc": 0.0}}])
        self.assertEqual(sugg["margin"], [
            "ADJUST 2025-12-31 16:00:00 SAMP CAD -10.00 type=roc"])

    def test_security_with_no_amount_is_one_cad_row(self):
        t3 = SA.Slip("slips.toml", "T3", "Synthetic fund", "margin", "USD",
                     security="SAMP", amounts={"ca_div": 10.0})
        out = SA._securities("margin", "report", [t3], [], 2025, 1.0, [],
                             {}, [])
        self.assertEqual([(r["currency"], r["slip"]) for r in out],
                         [("CAD", {})])


class TestHeldAlreadyNoteDates(unittest.TestCase):
    """97: the conservative rule stays (a line the books may already
    hold is never suggested); the note names the row's pay date and
    record date, so a prior year's row shows as one."""

    def test_note_names_pay_and_record_date(self):
        div = _row("SAMP.TO", "2025-02-03", "CAD", 100.0, category="ca_div")
        old = _row("SAMP.TO", "2025-01-08", "CAD", 10.0, "ADJUST",
                   tax="2024-12-31", category="roc", source="prior.tt",
                   record="2024-12-31")
        t3 = SA.Slip("slips.toml", "T3", "Synthetic fund", "margin", "CAD",
                     security="SAMP", amounts={"roc": 10.0})
        sugg, notes = {}, []
        SA._suggest_roc("margin", "SAMP", "SAMP.TO", "CAD", 10.0, [div],
                        None, 2025, sugg, notes, "report", [t3],
                        acct_rows=[div, old])
        self.assertFalse([ln for ln in sugg["margin"] if "ADJUST" in ln])
        held = [n for n in notes if "already hold" in n]
        self.assertEqual(len(held), 1)
        self.assertIn("the books already hold ADJUST 10.00 CAD paid "
                      "2025-01-08, record date 2024-12-31 (prior.tt), not "
                      "counted against this slip", held[0])

    def test_note_without_record_date(self):
        div = _row("SAMP.TO", "2025-02-03", "CAD", 100.0, category="ca_div")
        old = _row("SAMP.TO", "2025-03-03", "CAD", 10.0, "ADJUST",
                   category="roc", source="other.tt")
        t3 = SA.Slip("slips.toml", "T3", "Synthetic fund", "margin", "CAD",
                     security="SAMP", amounts={"roc": 10.0})
        notes = []
        SA._suggest_roc("margin", "SAMP", "SAMP.TO", "CAD", 10.0, [div],
                        None, 2025, {}, notes, "report", [t3],
                        acct_rows=[div, old])
        held = [n for n in notes if "already hold" in n]
        self.assertIn("ADJUST 10.00 CAD paid 2025-03-03 (other.tt)",
                      held[0])
        self.assertNotIn("record date", held[0])


class TestAnnualAverageFinite(unittest.TestCase):
    """100: a non-finite rate is never averaged, nor accepted as the
    slips.toml [annual_average] override."""

    def test_infinite_cache_observation_dropped(self):
        cache = {"_boc": {"USDCAD": {"obs": {
            "2025-02-03": float("inf"), "2025-02-04": 1.5,
            "2025-02-05": float("nan"), "2025-02-06": "Infinity",
            "2025-02-07": -1.0, "2025-02-10": True}}}}
        r = SA.annual_average("USD", 2025, cache)
        self.assertEqual((r["rate"], r["observations"]), (1.5, 1))
        self.assertTrue(math.isfinite(r["rate"]))
        cache = {"_boc": {"USDCAD": {"obs": {"2025-02-03": float("inf")}}}}
        self.assertIsNone(SA.annual_average("USD", 2025, cache))

    def test_override_refuses_non_finite(self):
        for v in ("inf", "nan", '"' + "9" * 400 + '"'):
            with private_tmpfile("w", suffix=".toml", delete=False) as f:
                f.write(f"[annual_average]\nUSD = {v}\n")
            path = Path(f.name)
            with self.assertRaises(SA.SlipsError) as cm:
                SA.load_slips_file(path, {}, 2025)
            self.assertIn("[annual_average] USD", str(cm.exception))
            self.assertIn("is not an amount", str(cm.exception))
            path.unlink()
        with private_tmpfile("w", suffix=".toml", delete=False) as f:
            f.write("[annual_average]\nUSD = 1.35\n")
        path = Path(f.name)
        self.assertEqual(SA.load_slips_file(path, {}, 2025)[2],
                         {"USD": 1.35})
        path.unlink()


if __name__ == "__main__":
    unittest.main()
