"""Unit tests for lib/corporate_timeline.py — the one definition of
split/rename arithmetic. Includes cross-checks that SplitTimeline.factor /
alias_factor reproduce what the OLD inline Canada (_window_splits +
_to_loss_units) and OLD inline US (split_schedule + _rep_units_factor)
implementations computed on shared scenarios."""

import unittest

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import (
    SplitTimeline,
    cumulative_factor,
    normalize_symbol_new,
    split_event_key,
)


def split(symbol, date, ratio, symbol_new='', account='A', time='00:00:00',
          date_settle=''):
    return TaxTransaction(action='SPLIT', date=date, symbol=symbol,
                          quantity=ratio, symbol_new=symbol_new,
                          account=account, time=time,
                          date_settle=date_settle)


def buy(symbol, date, qty=10, account='A'):
    return TaxTransaction(action='BUYSELL', date=date, symbol=symbol,
                          quantity=qty, account=account)


class TestNormalizeSymbolNew(unittest.TestCase):
    def test_empty_and_same_and_whitespace_are_no_rename(self):
        self.assertEqual(normalize_symbol_new('AAPL.US', ''), '')
        self.assertEqual(normalize_symbol_new('AAPL.US', None), '')
        self.assertEqual(normalize_symbol_new('AAPL.US', 'AAPL.US'), '')
        self.assertEqual(normalize_symbol_new('AAPL.US', '  '), '')

    def test_real_rename_passes_through(self):
        self.assertEqual(normalize_symbol_new('FB.US', 'META.US'), 'META.US')


class TestCumulativeFactor(unittest.TestCase):
    EVENTS = [('2025-03-10', 2.0), ('2025-06-10', 3.0)]

    def test_boundary_from_exclusive(self):
        # Split exactly ON from_date is baked into from_date quantities.
        self.assertEqual(
            cumulative_factor(self.EVENTS, '2025-03-10', '2025-04-01'), 1.0)

    def test_boundary_to_inclusive(self):
        # Split exactly ON to_date counts.
        self.assertEqual(
            cumulative_factor(self.EVENTS, '2025-03-01', '2025-03-10'), 2.0)

    def test_multi_split_cumulation(self):
        self.assertEqual(
            cumulative_factor(self.EVENTS, '2025-01-01', '2025-12-31'), 6.0)

    def test_backward_is_reciprocal(self):
        self.assertAlmostEqual(
            cumulative_factor(self.EVENTS, '2025-12-31', '2025-01-01'),
            1.0 / 6.0)

    def test_reverse_split(self):
        ev = [('2025-05-01', 0.1)]                 # 1-for-10
        self.assertAlmostEqual(
            cumulative_factor(ev, '2025-04-01', '2025-06-01'), 0.1)
        self.assertAlmostEqual(
            cumulative_factor(ev, '2025-06-01', '2025-04-01'), 10.0)

    def test_same_date_is_identity(self):
        self.assertEqual(
            cumulative_factor(self.EVENTS, '2025-03-10', '2025-03-10'), 1.0)

    def test_zero_product_backward_guard(self):
        # A zero ratio must not divide — both engines guarded with
        # `if f else` fallbacks; the shared definition preserves that.
        ev = [('2025-05-01', 0.0)]
        self.assertEqual(
            cumulative_factor(ev, '2025-06-01', '2025-04-01'), 1.0)


class TestTimelineBuildAndFactor(unittest.TestCase):
    def test_ratio_zero_rows_are_not_recorded(self):
        tl = SplitTimeline.from_transactions([split('X.US', '2025-05-01', 0.0)])
        self.assertEqual(tl.factor('X.US', '2025-01-01', '2025-12-31'), 1.0)

    def test_migrated_schedule_semantics(self):
        # FB splits 2:1, then renames to META, then META splits 3:1.
        tl = SplitTimeline.from_transactions([
            split('FB.US', '2025-02-01', 2.0),
            split('FB.US', '2025-03-01', 1.0, symbol_new='META.US'),
            split('META.US', '2025-06-01', 3.0),
        ])
        # Queries under the OLD name see nothing (US engine semantics:
        # the schedule migrated onto the target at the rename).
        self.assertEqual(tl.factor('FB.US', '2025-01-01', '2025-12-31'), 1.0)
        # The target carries the whole chain: 2 * 1 * 3.
        self.assertEqual(tl.factor('META.US', '2025-01-01', '2025-12-31'), 6.0)

    def test_alias_factor_spans_chain_from_any_member(self):
        tl = SplitTimeline.from_transactions([
            split('FB.US', '2025-02-01', 2.0),
            split('FB.US', '2025-03-01', 1.0, symbol_new='META.US'),
            split('META.US', '2025-06-01', 3.0),
        ])
        for sym in ('FB.US', 'META.US'):
            self.assertEqual(tl.alias_factor(sym, '2025-01-01', '2025-12-31'),
                             6.0)

    def test_canonical_chain(self):
        tl = SplitTimeline.from_transactions([
            split('A.US', '2025-01-10', 1.0, symbol_new='B.US'),
            split('B.US', '2025-02-10', 1.0, symbol_new='C.US'),
        ])
        reps = {tl.canonical(s) for s in ('A.US', 'B.US', 'C.US')}
        self.assertEqual(len(reps), 1)
        self.assertEqual(tl.canonical('UNRELATED.US'), 'UNRELATED.US')

    def test_rename_with_falsy_ratio_still_unions(self):
        # Both engines union rename pairs regardless of ratio truthiness.
        tl = SplitTimeline.from_transactions([
            split('A.US', '2025-01-10', 0.0, symbol_new='B.US'),
        ])
        self.assertEqual(tl.canonical('A.US'), tl.canonical('B.US'))

    def test_date_of_selects_the_basis(self):
        # Canada builds on settle-sort dates; the same row must land at a
        # different date under date_of.
        row = split('X.US', '2025-05-01', 2.0, date_settle='2025-05-03')
        us_style = SplitTimeline.from_transactions([row])
        ca_style = SplitTimeline.from_transactions(
            [row], date_of=lambda t: t.date_settle or t.date)
        self.assertEqual(us_style.factor('X.US', '2025-05-01', '2025-05-02'),
                         1.0)   # (from, to] excludes the 05-01 trade date
        self.assertEqual(ca_style.factor('X.US', '2025-05-01', '2025-05-03'),
                         2.0)   # settle date 05-03 is inside (05-01, 05-03]

    def test_splits_between(self):
        tl = SplitTimeline.from_transactions([
            split('X.US', '2025-03-10', 2.0),
            split('X.US', '2025-06-10', 3.0),
        ])
        self.assertEqual(tl.splits_between('X.US', '2025-03-10', '2025-06-10'),
                         [('2025-06-10', 3.0)])


class TestDedupe(unittest.TestCase):
    def test_symbol_new_spellings_collapse(self):
        # IB stamps symbol_new == symbol, RBC/Questrade leave '' — same event.
        txs = [split('X.US', '2025-05-01', 2.0, symbol_new='X.US'),
               split('X.US', '2025-05-01', 2.0, symbol_new='')]
        self.assertEqual(len(SplitTimeline.dedupe(txs)), 1)

    def test_real_rename_target_distinguishes(self):
        txs = [split('X.US', '2025-05-01', 2.0, symbol_new=''),
               split('X.US', '2025-05-01', 2.0, symbol_new='Y.US')]
        self.assertEqual(len(SplitTimeline.dedupe(txs)), 2)

    def test_shared_seen_across_lists(self):
        # The engines share one `seen` across taxable/sheltered/affiliated.
        seen = set()
        first = SplitTimeline.dedupe([split('X.US', '2025-05-01', 2.0)], seen)
        second = SplitTimeline.dedupe([split('X.US', '2025-05-01', 2.0)], seen)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_per_account_mode_keeps_one_per_account(self):
        txs = [split('X.US', '2025-05-01', 2.0, account='A'),
               split('X.US', '2025-05-01', 2.0, account='A'),
               split('X.US', '2025-05-01', 2.0, account='B')]
        self.assertEqual(len(SplitTimeline.dedupe(txs, per_account=True)), 2)
        self.assertEqual(len(SplitTimeline.dedupe(txs)), 1)

    def test_non_splits_pass_through(self):
        txs = [buy('X.US', '2025-05-01'), buy('X.US', '2025-05-01')]
        self.assertEqual(len(SplitTimeline.dedupe(txs)), 2)


# --------------------------------------------------------------------------
# Cross-checks against the OLD inline implementations (verbatim ports of the
# replaced code), on shared scenarios including a rename mid-chain and a
# reverse split.

def _old_canada_to_loss_units(q, at_date, loss_sort, window_splits):
    f = 1.0
    if at_date > loss_sort:
        for d, r in window_splits:
            if loss_sort < d <= at_date:
                f *= r
        return q / f if f else q
    if at_date < loss_sort:
        for d, r in window_splits:
            if at_date < d <= loss_sort:
                f *= r
        return q * f
    return q


def _old_us_build_and_factor(events, sym, from_date, to_date):
    split_schedule = {}
    for (s, date, ratio, symbol_new) in events:
        if ratio:
            split_schedule.setdefault(s, []).append((date, float(ratio)))
        target = (symbol_new or '').strip()
        if target and target != s and s in split_schedule:
            split_schedule.setdefault(target, []).extend(split_schedule.pop(s))
    if from_date == to_date:
        return 1.0
    lo, hi = ((from_date, to_date) if from_date < to_date
              else (to_date, from_date))
    f = 1.0
    for d, r in split_schedule.get(sym, []):
        if lo < d <= hi:
            f *= r
    if from_date < to_date:
        return f
    return 1.0 / f if f else 1.0


class TestOldImplementationParity(unittest.TestCase):
    SCENARIO = [
        # (symbol, date, ratio, symbol_new)
        ('SSL.TO', '2025-02-10', 2.0, ''),
        ('SSL.TO', '2025-03-15', 0.0625, 'RGLD.US'),   # reverse + rename
        ('RGLD.US', '2025-07-01', 3.0, 'RGLD.US'),     # IB-style symbol_new
    ]

    def _timeline(self):
        txs = [split(s, d, r, symbol_new=n) for s, d, r, n in self.SCENARIO]
        return SplitTimeline.from_transactions(txs)

    def test_us_factor_parity(self):
        tl = self._timeline()
        dates = ['2025-01-01', '2025-02-10', '2025-02-11', '2025-03-15',
                 '2025-06-30', '2025-07-01', '2025-12-31']
        for sym in ('SSL.TO', 'RGLD.US'):
            for a in dates:
                for b in dates:
                    self.assertAlmostEqual(
                        tl.factor(sym, a, b),
                        _old_us_build_and_factor(self.SCENARIO, sym, a, b),
                        places=12,
                        msg=f"factor({sym}, {a}, {b})")

    def test_canada_to_loss_units_parity(self):
        tl = self._timeline()
        # Old Canada: events are ALL splits of the alias class (both
        # tickers), settle-sort dates (same as trade dates here).
        window_splits = [(d, float(r)) for _s, d, r, _n in self.SCENARIO if r]
        loss_sort = '2025-03-20'
        for q in (100.0, 7.0):
            for at in ('2025-01-05', '2025-02-10', '2025-02-11',
                       '2025-03-20', '2025-07-01', '2025-08-01'):
                old = _old_canada_to_loss_units(q, at, loss_sort,
                                                window_splits)
                new = q * tl.alias_factor('SSL.TO', at, loss_sort)
                self.assertAlmostEqual(new, old, places=9,
                                       msg=f"to_loss_units({q}, {at})")




class TestRadarPriority(unittest.TestCase):
    """The radar's tie-break ladder is hosted in corporate_timeline (its
    private copy is what let engine ordering fixes miss it). Pins the
    historical semantics — ADJUST first, then ASSIGN/SPLIT, buys, sells —
    and that wash_radar consumes THIS ladder, not a local one."""

    def _tx(self, action, qty):
        from taxjson.lib.core import TaxTransaction
        return TaxTransaction(action=action, date="2026-01-05",
                              symbol="AAA.TO", quantity=qty, price=1.0,
                              net_amount=float(qty), account="margin")

    def test_ladder_values(self):
        from taxjson.lib.corporate_timeline import radar_priority
        self.assertEqual(radar_priority(self._tx("ADJUST", 0)), 0)
        self.assertEqual(radar_priority(self._tx("ASSIGN", 1)), 1)
        self.assertEqual(radar_priority(self._tx("SPLIT", 0)), 1)
        self.assertEqual(radar_priority(self._tx("BUYSELL", 10)), 2)
        self.assertEqual(radar_priority(self._tx("BUYSELL", -10)), 3)

    def test_wash_radar_uses_the_shared_ladder(self):
        from taxjson.bin import taxjson_wash_radar as radar
        from taxjson.lib.corporate_timeline import radar_priority
        self.assertIs(radar.get_tx_priority, radar_priority)




class TestSameDayChainedRenameOrder(unittest.TestCase):
    """Schedule migration must land on the union ROOT: with positional
    migration, processing B->C before A->B stranded A's ratio under B
    while canonical(A) was C — factor() returned 3.0 instead of 6.0
    depending on INPUT ORDER, corrupting §1091 unit conversion."""

    def _tl(self, order):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.corporate_timeline import SplitTimeline
        ab = TaxTransaction(action="SPLIT", date="2026-03-01",
                            time="09:00:00", symbol="A.US",
                            symbol_new="B.US", quantity=2.0,
                            currency="USD", account="m")
        bc = TaxTransaction(action="SPLIT", date="2026-03-01",
                            time="09:00:00", symbol="B.US",
                            symbol_new="C.US", quantity=3.0,
                            currency="USD", account="m")
        txs = [ab, bc] if order == "ab_first" else [bc, ab]
        return SplitTimeline.from_transactions(txs)

    def test_factor_is_order_independent(self):
        for order in ("ab_first", "bc_first"):
            tl = self._tl(order)
            self.assertEqual(tl.canonical("A.US"), "C.US", order)
            self.assertAlmostEqual(
                tl.factor(tl.canonical("A.US"), "2026-01-01",
                          "2026-12-31"), 6.0,
                msg=f"input order {order} changed the cumulative "
                    f"factor")


if __name__ == "__main__":
    unittest.main()
