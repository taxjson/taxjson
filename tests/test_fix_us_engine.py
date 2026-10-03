"""US engine work deferred from the re-audit (owner request): a wash-sale
replacement sold before the loss in another taxable account (A2-0544),
the §355 spin-off per lot (A2-0065), §356 boot per block (A2-0066), and
custody moves between your own taxable accounts that carry the lots
(A2-0032 securities, A2-0003 crypto). Synthetic data only.
"""
import contextlib
import io
import unittest

from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx


def _gains(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res, **match):
    return [t for t in res["transactions"]
            if t.get("qty") and all(t.get(k) == v for k, v in match.items())]


# ---------------------------------------------------------------- A2-0544
def _sold_replacement_book(rep_buy="2025-03-03", rep_sell="2025-03-05",
                           loss="2025-03-10"):
    """margin buys 100 @50 and sells @30 (a 2,000 loss); account b buys
    100 @30 inside the window and sells them @31 BEFORE the loss."""
    return [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
               account="margin"),
            tx("BUYSELL", rep_buy, "XYZ.US", 100, 3000.0, account="b"),
            tx("BUYSELL", rep_sell, "XYZ.US", -100, 3100.0, account="b"),
            tx("BUYSELL", loss, "XYZ.US", -100, 3000.0, account="margin")]


class TestReplacementSoldBeforeTheLoss(unittest.TestCase):
    """A2-0544: §1091 has no still-held test — a purchase in another
    taxable account bought and sold before the loss still replaces it;
    the disallowed loss goes into that earlier sale's basis."""

    @rule("US-WASH-22")
    @rule_absent("US-WASH-22", country="canada")
    def test_us_disallows_and_moves_the_loss_canada_keeps_it(self):
        r = gains_both(_sold_replacement_book(), year=2025)
        us = r["usa"]
        loss = _sales(us, account="margin")[0]
        self.assertAlmostEqual(loss["disallowed_amount"], 2000.0, places=2)
        self.assertAlmostEqual(loss["gain"], 0.0, places=2)
        self.assertEqual(loss["wash_replacements"][0]["sold_before_loss"],
                         "2025-03-05")
        rep = _sales(us, account="b")[0]
        self.assertAlmostEqual(rep["cost"], 5000.0, places=2)
        self.assertAlmostEqual(rep["gain"], -1900.0, places=2)
        self.assertAlmostEqual(us["summary"]["total_gain"], -1900.0,
                               places=2)
        # Canada: the substituted property is not held at day 30
        # (s.54): no superficial loss.
        ca = r["canada"]
        self.assertAlmostEqual(ca["summary"]["total_disallowed"], 0.0)

    @rule("US-WASH-22")
    def test_holding_period_of_the_loss_shares_carries_over(self):
        # Loss shares held 2024-01-02 .. 2025-03-10 (433 days): the
        # replacement sold 2025-03-05 becomes long-term.
        book = _sold_replacement_book()
        book[0] = tx("BUYSELL", "2024-01-02", "XYZ.US", 100, 5000.0,
                     account="margin")
        us = _gains("usa", book, year=2025)
        rep = _sales(us, account="b")[0]
        self.assertEqual(rep["term"], "LONG_TERM")

    @rule("US-WASH-22")
    def test_partial_match_splits_the_earlier_sale(self):
        # b bought 200 and sold them; only 100 replace the loss.
        book = [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
                   account="margin"),
                tx("BUYSELL", "2025-03-03", "XYZ.US", 200, 6000.0,
                   account="b"),
                tx("BUYSELL", "2025-03-05", "XYZ.US", -200, 6200.0,
                   account="b"),
                tx("BUYSELL", "2025-03-10", "XYZ.US", -100, 3000.0,
                   account="margin")]
        us = _gains("usa", book, year=2025)
        rows = _sales(us, account="b")
        self.assertEqual(sorted(round(r["qty"]) for r in rows), [100, 100])
        bumped = [r for r in rows if r.get("wash_basis_added")]
        self.assertEqual(len(bumped), 1)
        self.assertAlmostEqual(bumped[0]["cost"], 5000.0, places=2)
        # b: +200 on its round trip, less the 2,000 moved into it;
        # margin's loss is disallowed.
        self.assertAlmostEqual(us["summary"]["total_gain"], -1800.0,
                               places=2)

    @rule("US-WASH-22")
    def test_replacement_sold_at_a_disallowed_loss_is_flagged(self):
        # b's own sale is a loss washed by b's rebuy: not matched again.
        book = [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
                   account="margin"),
                tx("BUYSELL", "2025-03-03", "XYZ.US", 100, 3000.0,
                   account="b"),
                tx("BUYSELL", "2025-03-05", "XYZ.US", -100, 2900.0,
                   account="b"),
                tx("BUYSELL", "2025-03-06", "XYZ.US", 100, 2900.0,
                   account="b"),
                tx("BUYSELL", "2025-03-10", "XYZ.US", -100, 3000.0,
                   account="margin")]
        us = _gains("usa", book, year=2025)
        self.assertIn("check this wash sale by hand", us["_stderr"])
        b_sale = _sales(us, account="b")[0]
        self.assertFalse(b_sale.get("wash_basis_added"))

    @rule("US-WASH-22")
    def test_filed_year_is_left_as_filed(self):
        book = _sold_replacement_book(rep_buy="2025-12-15",
                                      rep_sell="2025-12-17",
                                      loss="2026-01-05")
        unlocked = _gains("usa", book)
        rep = _sales(unlocked, account="b")[0]
        self.assertAlmostEqual(rep["gain"], -1900.0, places=2)
        self.assertIn("amend that return", unlocked["_stderr"])
        locked = _gains("usa", book, locked_years=(2025,))
        rep = _sales(locked, account="b")[0]
        self.assertAlmostEqual(rep["gain"], 100.0, places=2)
        self.assertFalse(rep.get("wash_basis_added"))
        moved = [t for t in locked["transactions"] if t.get("deemed")]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["date"], "2026-01-05")
        self.assertAlmostEqual(moved[0]["gain"], -2000.0, places=2)
        self.assertIn("ATTENTION: wash sale reaches a filed year",
                      locked["_stderr"])
        self.assertIn("1040-X", locked["_stderr"])
        # Year totals: 2025 as filed, 2026 nets the loss out.
        y25 = _gains("usa", book, locked_years=(2025,), year=2025)
        y26 = _gains("usa", book, locked_years=(2025,), year=2026)
        self.assertAlmostEqual(y25["summary"]["total_gain"], 100.0,
                               places=2)
        self.assertAlmostEqual(y26["summary"]["total_gain"], -2000.0,
                               places=2)

    @rule("US-WASH-22")
    def test_locked_year_flags_are_us_only(self):
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_filed import locked_year_flags
        from taxjson.lib.country import flag_country_problems
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "filed").mkdir()
            (root / "filed" / "2025.json").write_text('{"year": 2025}')
            self.assertEqual(locked_year_flags(root, {"country": "usa"}),
                             ["--locked-year", "2025"])
            self.assertEqual(locked_year_flags(root,
                                               {"country": "canada"}), [])
        self.assertTrue(flag_country_problems(
            "canada", {"--locked-year": [2025]}, tool="taxjson-gains"))


if __name__ == "__main__":
    unittest.main()
