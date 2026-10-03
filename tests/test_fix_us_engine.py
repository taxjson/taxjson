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


# ---------------------------------------------------------------- A2-0065
def _spin_rows(alloc, qty_received=20.0):
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.corp_actions import CorporateAction, resolve_event
    ev = CorporateAction(
        date="2025-04-01", time="09:30:00", action_type="spinoff",
        source_symbol="PAR.US", source_isin="", target_symbol="SPN.US",
        target_isin="", ratio_new=1, ratio_old=5, qty_disposed=0.0,
        qty_received=qty_received, fmv=0.0, currency="USD",
        target_currency="USD", account="margin", event_id="ev-spin")
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, "tax_free_355", country="usa",
                             hints={"allocated_acb": alloc})
    return [TaxTransaction(**r) for r in rows]


def _parent_lots():
    return [tx("BUYSELL", "2023-01-10", "PAR.US", 50, 200.0),
            tx("BUYSELL", "2025-03-01", "PAR.US", 50, 1800.0)]


def _engine(book, **kw):
    from taxjson.lib.core import USATaxRules
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = USATaxRules().compute_gains(list(book), per_account_basis=True,
                                        **kw)
    r["_stderr"] = err.getvalue()
    return r


def _lots(res, symbol):
    return [i for i in res["inventory"] if i["symbol"] == symbol]


class TestSpinoff355PerLot(unittest.TestCase):
    """A2-0065: Reg. §1.358-2 — every parent share gives up the same
    fraction of its own basis; one spun-off block per parent block with
    the parent's holding period (§1223(1)); never a §301(c)(3) gain."""

    @rule("US-CORP-07")
    def test_basis_by_fraction_and_tacked_blocks(self):
        book = _parent_lots() + _spin_rows(400.0) + [
            tx("BUYSELL", "2025-06-02", "SPN.US", -20, 600.0),
            tx("BUYSELL", "2025-06-02", "PAR.US", -100, 3000.0)]
        r = _engine(book)
        spn = sorted((t for t in r["transactions"]
                      if t["symbol"] == "SPN.US"),
                     key=lambda t: t["acquired_date"])
        self.assertEqual([(round(t["qty"]), round(t["cost"], 2), t["term"],
                           t["acquired_date"]) for t in spn],
                         [(10, 40.0, "LONG_TERM", "2023-01-10"),
                          (10, 360.0, "SHORT_TERM", "2025-03-01")])
        par = sorted((t for t in r["transactions"]
                      if t["symbol"] == "PAR.US"),
                     key=lambda t: t["acquired_date"])
        self.assertEqual([round(t["cost"], 2) for t in par],
                         [160.0, 1440.0])
        self.assertFalse(any(t.get("deemed") for t in r["transactions"]))

    @rule("US-CORP-07")
    def test_no_deemed_gain_and_an_allocation_beyond_basis_is_capped(self):
        r = _engine(_parent_lots() + _spin_rows(600.0))
        self.assertFalse(any(t.get("deemed") for t in r["transactions"]))
        self.assertNotIn("§301(c)(3)", r["_stderr"])
        r = _engine(_parent_lots() + _spin_rows(2500.0))
        self.assertIn("capped at the basis", r["_stderr"])
        self.assertFalse(any(t.get("deemed") for t in r["transactions"]))
        par = _lots(r, "PAR.US")[0]
        spn = _lots(r, "SPN.US")[0]
        self.assertAlmostEqual(par["total_cost"], 0.0, places=2)
        self.assertAlmostEqual(spn["total_cost"], 2000.0, places=2)

    @rule("US-CORP-07")
    def test_spun_off_shares_are_not_a_wash_replacement(self):
        # A when-issued SPN loss five days before the distribution.
        book = _parent_lots() + [
            tx("BUYSELL", "2025-03-20", "SPN.US", 10, 500.0),
            tx("BUYSELL", "2025-03-27", "SPN.US", -10, 400.0)] \
            + _spin_rows(400.0)
        r = _engine(book)
        loss = [t for t in r["transactions"] if t["symbol"] == "SPN.US"][0]
        self.assertAlmostEqual(loss["gain"], -100.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule("US-CORP-07")
    def test_no_parent_lots_falls_back_with_a_warning(self):
        r = _engine(_spin_rows(400.0))
        self.assertIn("finds no long PAR.US lots", r["_stderr"])


# ---------------------------------------------------------------- A2-0066
def _boot_rows(boot=400.0, fmv_per_share=32.0):
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.corp_actions import CorporateAction, resolve_event
    ev = CorporateAction(
        date="2025-06-20", time="09:30:00", action_type="merger",
        source_symbol="OLD.US", source_isin="", target_symbol="NEW.US",
        target_isin="", ratio_new=1, ratio_old=2, qty_disposed=100.0,
        qty_received=50.0, fmv=0.0, currency="USD", target_currency="USD",
        account="margin", event_id="ev-boot")
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, "reorg_368_boot", country="usa",
                             hints={"cash_boot": boot,
                                    "fmv_per_share": fmv_per_share})
    return [TaxTransaction(**r) for r in rows]


def _old_lots():
    return [tx("BUYSELL", "2023-01-10", "OLD.US", 50, 200.0),
            tx("BUYSELL", "2025-03-01", "OLD.US", 50, 1800.0)]


class TestBoot356PerBlock(unittest.TestCase):
    """A2-0066: Reg. §1.356-1(b) / Rev. Rul. 68-23 — realized and
    recognized gain per block, never a loss; new basis per block = old
    basis − boot share + recognized; holding period tacked."""

    @rule("US-CORP-05")
    def test_per_block_gain_and_no_loss_row(self):
        r = _engine(_old_lots() + _boot_rows())
        rows = sorted((t for t in r["transactions"]
                       if t["symbol"] == "OLD.US"),
                      key=lambda t: t["acquired_date"])
        # Lot A: realized 1000 − 200 = 800, recognized min(800, 200);
        # lot B: realized 1000 − 1800 < 0, recognized 0.
        self.assertEqual([(round(t["gain"], 2), t["term"]) for t in rows],
                         [(200.0, "LONG_TERM"), (0.0, "SHORT_TERM")])
        self.assertFalse(any(t["gain"] < -0.005 for t in rows))
        self.assertEqual([round(t["proceeds"], 2) for t in rows],
                         [200.0, 200.0])
        self.assertAlmostEqual(r["summary"]["total_gain"], 200.0, places=2)
        new = _lots(r, "NEW.US")[0]
        self.assertAlmostEqual(new["total_cost"], 1800.0, places=2)
        self.assertAlmostEqual(new["qty"], 50.0)

    @rule("US-CORP-05")
    def test_new_blocks_keep_the_old_blocks_dates(self):
        book = _old_lots() + _boot_rows() + [
            tx("BUYSELL", "2025-09-02", "NEW.US", -50, 2000.0)]
        r = _engine(book)
        sold = sorted((t for t in r["transactions"]
                       if t["symbol"] == "NEW.US"),
                      key=lambda t: t["acquired_date"])
        self.assertEqual([(round(t["qty"]), round(t["cost"], 2),
                           t["acquired_date"], t["term"]) for t in sold],
                         [(25, 200.0, "2023-01-10", "LONG_TERM"),
                          (25, 1600.0, "2025-03-01", "SHORT_TERM")])

    @rule("US-CORP-05")
    def test_new_shares_are_not_a_wash_replacement(self):
        # A NEW.US loss in the window of the exchange is not washed by
        # shares received in a §356 exchange (§1091(a)).
        book = _old_lots() + [
            tx("BUYSELL", "2025-06-01", "NEW.US", 10, 500.0),
            tx("BUYSELL", "2025-06-05", "NEW.US", -10, 400.0)] \
            + _boot_rows()
        r = _engine(book)
        loss = [t for t in r["transactions"] if t["symbol"] == "NEW.US"][0]
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule("US-CORP-05")
    def test_missing_old_lots_are_named(self):
        r = _engine(_old_lots()[:1] + _boot_rows())
        self.assertIn("have no basis in the books", r["_stderr"])


if __name__ == "__main__":
    unittest.main()
