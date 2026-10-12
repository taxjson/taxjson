"""Tax-year relevance of missing-acquisition (negative-holding) positions.

Pins assess_tax_year_relevance: a short position only matters for a tax year
when it has a sale IN that year drawing from the short/phantom state. Shorts
from other years - or ones that have since drained back to a known basis -
are reported as not-relevant so the user can ignore them.
"""
import unittest

from taxjson.lib.core import TaxTransaction
from taxjson.lib.missing_history import (
    detect_missing_history, assess_tax_year_relevance, detect_zero_basis_acquisitions,
    detect_corp_action_links,
)


def _tx(action, date, symbol, qty, net=0.0, account="margin", currency="USD",
        price=0.0, description=""):
    return TaxTransaction(action=action, date=date, time="09:30:00",
                          symbol=symbol, quantity=qty, net_amount=net,
                          account=account, currency=currency, price=price,
                          description=description)


def _assess(txs, year):
    cands = detect_missing_history(txs)
    return {(r.candidate.symbol, r.candidate.account): r
            for r in assess_tax_year_relevance(txs, cands, year)}


class TestAssess(unittest.TestCase):
    def test_in_year_short_sale_affects_year(self):
        # Truncated history: first event is a sale that goes negative in 2025.
        txs = [_tx("BUYSELL", "2025-06-01", "AAPL.US", -10, 2000.0)]
        r = _assess(txs, 2025)[("AAPL.US", "margin")]
        self.assertTrue(r.affects_year)
        self.assertEqual(r.in_year_dispositions, 1)
        self.assertAlmostEqual(r.in_year_proceeds, 2000.0)
        self.assertEqual(r.last_in_year_date, "2025-06-01")

    def test_other_year_short_not_relevant(self):
        # Goes short only in 2026 -> a candidate, but irrelevant to 2025.
        txs = [_tx("BUYSELL", "2026-03-01", "MSFT.US", -5, 1500.0)]
        r = _assess(txs, 2025)[("MSFT.US", "margin")]
        self.assertFalse(r.affects_year)
        self.assertEqual(r.in_year_dispositions, 0)

    def test_drained_then_clean_in_year_sale_not_relevant(self):
        # Short in 2024, bought back to flat, then a CLEAN 2025 sale (basis
        # known) -> the 2025 sale doesn't draw from the phantom state.
        txs = [
            _tx("BUYSELL", "2024-05-01", "NVDA.US", -10, 1000.0),  # phantom short
            _tx("BUYSELL", "2024-06-01", "NVDA.US", 10, 1100.0),   # drain to 0
            _tx("BUYSELL", "2025-02-01", "NVDA.US", 20, 4000.0),   # fresh buy
            _tx("BUYSELL", "2025-09-01", "NVDA.US", -5, 1500.0),   # clean sale
        ]
        r = _assess(txs, 2025)[("NVDA.US", "margin")]
        self.assertFalse(r.affects_year, "post-drain clean 2025 sale must not flag 2025")

    def test_multiple_in_year_sales_sum_proceeds(self):
        txs = [
            _tx("BUYSELL", "2025-03-01", "GDX.US", -20, 3000.0),
            _tx("BUYSELL", "2025-04-01", "GDX.US", -26, 1811.03),
        ]
        r = _assess(txs, 2025)[("GDX.US", "margin")]
        self.assertEqual(r.in_year_dispositions, 2)
        self.assertAlmostEqual(r.in_year_proceeds, 4811.03, places=2)

    def test_no_year_scope_marks_all_relevant(self):
        txs = [_tx("BUYSELL", "2026-03-01", "MSFT.US", -5, 1500.0)]
        r = _assess(txs, None)[("MSFT.US", "margin")]
        self.assertTrue(r.affects_year)          # no year => can't rule out
        self.assertEqual(r.in_year_dispositions, 1)


def _zero(txs, year):
    return {(r.symbol, r.account): r
            for r in detect_zero_basis_acquisitions(txs, year)}


class TestZeroBasisAcquisitions(unittest.TestCase):
    def test_merger_received_at_zero_then_sold_is_flagged(self):
        # Shares received through a merger at $0, later sold.
        txs = [
            _tx("BUYSELL", "2025-08-11", "ORMQ.US", 24, 0.0, price=0.0,
                description="MGR - ORMAND CORPORATION SHRS RECEIVED THRU MERGER"),
            _tx("BUYSELL", "2025-12-09", "ORMQ.US", -24, 4111.20, price=171.3),
        ]
        r = _zero(txs, 2025)[("ORMQ.US", "margin")]
        self.assertTrue(r.affects_year)
        self.assertEqual(r.zero_cost_qty, 24.0)
        self.assertTrue(r.looks_corp_action)
        self.assertEqual(r.in_year_dispositions, 1)
        self.assertAlmostEqual(r.in_year_proceeds, 4111.20, places=2)

    def test_zero_cost_but_never_sold_not_flagged(self):
        # Received at $0 and still held — no realized gain yet, so not flagged.
        txs = [_tx("BUYSELL", "2025-08-11", "ORMQ.US", 24, 0.0,
                   description="MGR shares received")]
        self.assertNotIn(("ORMQ.US", "margin"), _zero(txs, 2025))

    def test_clean_cost_basis_not_flagged(self):
        txs = [
            _tx("BUYSELL", "2025-01-02", "AAPL.US", 10, 1500.0, price=150.0),
            _tx("BUYSELL", "2025-09-01", "AAPL.US", -10, 1800.0, price=180.0),
        ]
        self.assertNotIn(("AAPL.US", "margin"), _zero(txs, 2025))

    def test_sold_in_other_year_not_relevant(self):
        txs = [
            _tx("BUYSELL", "2024-08-12", "ORMQ.US", 24, 0.0, description="MGR"),
            _tx("BUYSELL", "2026-03-02", "ORMQ.US", -24, 4800.0, price=200.0),
        ]
        r = _zero(txs, 2025)[("ORMQ.US", "margin")]
        self.assertFalse(r.affects_year)
        self.assertEqual(r.in_year_dispositions, 0)

    def test_clean_sale_before_zero_acq_not_counted(self):
        # A clean buy+sell, THEN a $0 acquisition still held: the earlier sale
        # didn't draw on the $0 basis, so the pair isn't flagged.
        txs = [
            _tx("BUYSELL", "2025-01-02", "XYZ.US", 10, 1000.0, price=100.0),
            _tx("BUYSELL", "2025-03-01", "XYZ.US", -10, 1200.0, price=120.0),
            _tx("BUYSELL", "2025-08-11", "XYZ.US", 5, 0.0, description="MGR"),
        ]
        self.assertNotIn(("XYZ.US", "margin"), _zero(txs, 2025))


class TestMergerLinks(unittest.TestCase):
    def _merger(self):
        return [
            _tx("BUYSELL", "2025-08-11", "K123456.US", -20, 0.0,
                description="MGR - KELVIN CORPORATION MERGER TO ORMAND "
                            "CORPORATION 1.2 NEW = 1 OLD"),
            _tx("BUYSELL", "2025-08-11", "ORMQ.US", 24, 0.0,
                description="MGR - ORMAND CORPORATION SHRS RECEIVED THRU MERGER"),
        ]

    def test_links_old_removal_to_new_receipt(self):
        links = detect_corp_action_links(self._merger())
        self.assertEqual(len(links), 1)
        l = links[0]
        self.assertEqual(l.old_symbol, "K123456.US")
        self.assertEqual(l.new_symbol, "ORMQ.US")
        self.assertEqual(l.old_company, "KELVIN CORPORATION")
        self.assertEqual(l.new_company, "ORMAND CORPORATION")
        self.assertAlmostEqual(l.ratio, 1.2)
        self.assertEqual(l.old_qty, 20.0)
        self.assertEqual(l.new_qty, 24.0)

    def test_name_match_pairs_correctly_with_two_same_day_mergers(self):
        txs = self._merger() + [
            _tx("BUYSELL", "2025-08-11", "T012345.US", -8, 0.0,
                description="MGR - FOO INC MERGER TO BAR INC 2 NEW = 1 OLD"),
            _tx("BUYSELL", "2025-08-11", "BAR.US", 16, 0.0,
                description="MGR - BAR INC SHRS RECEIVED THRU MERGER"),
        ]
        links = {l.old_symbol: l for l in detect_corp_action_links(txs)}
        self.assertEqual(links["K123456.US"].new_symbol, "ORMQ.US")
        self.assertEqual(links["T012345.US"].new_symbol, "BAR.US")  # not ORMQ

    def test_no_link_without_a_receipt(self):
        only_removal = [self._merger()[0]]
        self.assertEqual(detect_corp_action_links(only_removal), [])

    def test_priced_rows_are_not_treated_as_merger(self):
        # A normal (non-$0) buy/sell with 'merger' in the text isn't a link.
        txs = [
            _tx("BUYSELL", "2025-08-11", "K123456.US", -20, 100.0, price=5.0,
                description="KELVIN MERGER TO ORMAND"),
            _tx("BUYSELL", "2025-08-11", "ORMQ.US", 24, 2000.0, price=83.0,
                description="ORMAND SHRS RECEIVED THRU MERGER"),
        ]
        self.assertEqual(detect_corp_action_links(txs), [])


if __name__ == "__main__":
    unittest.main()
