"""US engine work deferred from the re-audit (owner request): a wash-sale
replacement sold before the loss in another taxable account (A2-0544),
the §355 spin-off per lot (A2-0065), §356 boot per block (A2-0066), and
custody moves between your own taxable accounts that carry the lots
(A2-0032 securities, A2-0003 crypto). Synthetic data only.

Formerly: test_fix_us_engine
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


# ---------------------------------------------------------------- A2-0065
def _spin_rows(alloc, qty_received=20.0):
    from taxjson.model.transaction import TaxTransaction
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
    from taxjson.engine.us.taxrules import USATaxRules
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
    the parent's holding period (§1223(1)); never a §301(c)(3) gain.

    Formerly: test_fix_us_engine.TestSpinoff355PerLot"""

    @rule(R.US_CORP_07)
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

    @rule(R.US_CORP_07)
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

    @rule(R.US_CORP_07)
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

    @rule(R.US_CORP_07)
    def test_no_parent_lots_falls_back_with_a_warning(self):
        r = _engine(_spin_rows(400.0))
        self.assertIn("finds no long PAR.US lots", r["_stderr"])


# ---------------------------------------------------------------- A2-0066
def _boot_rows(boot=400.0, fmv_per_share=32.0):
    from taxjson.model.transaction import TaxTransaction
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
    basis − boot share + recognized; holding period tacked.

    Formerly: test_fix_us_engine.TestBoot356PerBlock"""

    @rule(R.US_CORP_05)
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

    @rule(R.US_CORP_05)
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

    @rule(R.US_CORP_05)
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

    @rule(R.US_CORP_05)
    def test_missing_old_lots_are_named(self):
        r = _engine(_old_lots()[:1] + _boot_rows())
        self.assertIn("have no basis in the books", r["_stderr"])


class TestCorpCashKeptOnPerLotRows(unittest.TestCase):
    """The per-lot §356 and §355 rows keep the corp_cash evidence main
    stamps on corp-action rows (fx-cash, A2-1014).

    Formerly: test_fix_us_engine.TestCorpCashKeptOnPerLotRows"""

    @rule(R.US_CORP_05)
    def test_boot_sell_row_carries_the_cash(self):
        rows = _boot_rows(boot=400.0)
        sell = next(r for r in rows if r.quantity < 0)
        self.assertEqual(sell.corp_cash, "400 USD")
        self.assertEqual(sell.type, "reorg_356")

    @rule(R.US_CORP_07)
    def test_spinoff_rows_are_typed_and_keep_their_fields(self):
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.corp_actions import CorporateAction, resolve_event
        ev = CorporateAction(
            date="2025-04-01", time="09:30:00", action_type="spinoff",
            source_symbol="PAR.US", source_isin="", target_symbol="SPN.US",
            target_isin="", ratio_new=1, ratio_old=3, qty_disposed=0.0,
            qty_received=20.5, fmv=0.0, currency="USD",
            target_currency="USD", account="margin", event_id="ev-cil",
            cash_in_lieu=12.0, cash_in_lieu_currency="USD")
        with contextlib.redirect_stderr(io.StringIO()):
            rows = resolve_event(ev, "tax_free_355", country="usa",
                                 hints={"allocated_acb": 400.0})
        self.assertTrue(all(r["type"] == "spinoff_355" for r in rows))
        self.assertTrue(any(r.get("corp_cash") for r in rows), rows)
        [TaxTransaction(**r) for r in rows]


if __name__ == "__main__":
    unittest.main()
