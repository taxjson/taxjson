"""TJ-1993: a SPLIT row that renames a holding into a ticker the book already
holds merges the two pools and drops the source; the walk's tail (the trace,
the drain check, the row's pool snapshot) must go on with the pool the row
left in the book, the merged one. It went on with the dropped source, so a
denied loss's window listed the rename row with the dropped pool's quantity
and cost."""
import unittest

from _support.ca_engine_runs import _run, _split, _tt


def _window_rows(target, *, ratio=1.0, held=16, sold=8, trace=False):
    """SYQ.TO held (100 at 10); SYR.TO bought (`held` at 20), `sold` of them
    sold at a loss, the rest renamed into `target` at `ratio`; a replacement
    bought inside the window. Returns the denial, the window rows' pool
    figures by (date, action), and the loss's trace lines."""
    taxable = _tt(f"""
        BUYSELL 2025-01-10 10:00:00 SYQ.TO 100 CAD 10 1000
        BUYSELL 2025-02-10 10:00:00 SYR.TO {held} CAD 20 {20 * held}
    """)
    if sold:
        taxable += _tt(f"BUYSELL 2025-03-01 10:00:00 SYR.TO -{sold} CAD 10 {10 * sold}")
    taxable.append(_split('2025-03-03', 'SYR.TO', target, ratio, 'margin'))
    # The replacement, identical property through the rename's lineage.
    taxable += _tt(f"BUYSELL 2025-03-10 10:00:00 {target} 10 CAD 7 70")
    res, _ = _run(taxable, trace=trace)
    (loss,) = [g for g in res['transactions'] if g.get('wash_window')]
    rows = {(e['date'], e['action']): (e['pool_qty_after'], e['pool_acb_after'])
            for e in loss['wash_window']['transactions']}
    # The loss's window trace (the rows' lines) is on its wash-sale entry.
    lines = [ln for w in res['wash_sales'] for ln in (w.get('trace') or [])]
    return res['summary']['total_disallowed'], rows, lines


class TestRenameIntoAnExistingHolding(unittest.TestCase):
    def _assert_row(self, rows, key, qty, acb):
        self.assertAlmostEqual(rows[key][0], qty, places=6)
        self.assertAlmostEqual(rows[key][1], acb, places=2)

    def test_the_rename_row_shows_the_merged_pool(self):
        denied, rows, _ = _window_rows('SYQ.TO')
        self.assertAlmostEqual(denied, 80.0, places=2)
        self._assert_row(rows, ('2025-03-01', 'BUYSELL'), 8.0, 160.0)
        # 8 renamed shares at 160 joined the 100 held at 1,000.
        self._assert_row(rows, ('2025-03-03', 'SPLIT'), 108.0, 1160.0)

    def test_the_symptoms_one_for_sixteen(self):
        # TJ-1993's shape: 16 shares at 20 become 1 at 320, merged into
        # the 100 at 1,000; the row showed 1 share at 320.
        denied, rows, _ = _window_rows('SYQ.TO', ratio=1 / 16, held=32, sold=16)
        self.assertAlmostEqual(denied, 160.0, places=2)     # 10 new shares are 160 old
        self._assert_row(rows, ('2025-03-03', 'SPLIT'), 101.0, 1320.0)

    def test_a_rename_into_a_new_ticker_keeps_its_own_pool(self):
        denied, rows, trace = _window_rows('SYS.TO', trace=True)
        self.assertAlmostEqual(denied, 80.0, places=2)
        self._assert_row(rows, ('2025-03-03', 'SPLIT'), 8.0, 160.0)
        (line,) = [ln for ln in trace if '| SPLIT ' in ln]
        self.assertIn('RENAMED', line)          # the pool's key changed, its figures did not

    def test_a_flat_source_renamed_keeps_the_targets_rename_line(self):
        # Sold out, then renamed: the tail's drain fires on the flat target
        # and must not reset the trace that just took the SPLIT-RENAME line
        # (TJ-2346); a later sale of the new ticker shows it.
        taxable = _tt("""
            BUYSELL 2025-02-10 10:00:00 SYR.TO 16 CAD 20 320
            BUYSELL 2025-03-01 10:00:00 SYR.TO -16 CAD 20 320
        """)
        taxable.append(_split('2025-03-03', 'SYR.TO', 'SYS.TO', 1.0, 'margin'))
        taxable += _tt("""
            BUYSELL 2025-03-10 10:00:00 SYS.TO 10 CAD 7 70
            BUYSELL 2025-04-10 10:00:00 SYS.TO -10 CAD 9 90
        """)
        res, _ = _run(taxable, trace=True)
        (sale,) = [g for g in res['transactions'] if g.get('symbol') == 'SYS.TO']
        self.assertTrue(any('SPLIT-RENAME SYR.TO' in str(ln) for ln in sale['trace']),
                        [str(ln) for ln in sale['trace']])

    def test_a_sheltered_rename_moves_nothing_in_the_taxable_book(self):
        # The RRSP renames its SYR.TO into SYQ.TO; the taxable pools stay
        # apart (the split helper returns the row's own pool and symbol for
        # another scope), so the taxable SYQ.TO sale's cost is its own.
        taxable = _tt("""
            BUYSELL 2025-01-10 10:00:00 SYQ.TO 100 CAD 10 1000
            BUYSELL 2025-02-10 10:00:00 SYR.TO 16 CAD 20 320
            BUYSELL 2025-04-01 10:00:00 SYQ.TO -100 CAD 15 1500
        """)
        rrsp = _tt("BUYSELL 2025-02-10 10:00:00 SYR.TO 16 CAD 20 320", account='rrsp')
        rrsp.append(_split('2025-03-03', 'SYR.TO', 'SYQ.TO', 1.0, 'rrsp'))
        res, _ = _run(taxable, sheltered_transactions=rrsp)
        (sale,) = [g for g in res['transactions'] if g.get('symbol') == 'SYQ.TO']
        self.assertAlmostEqual(sale['gain'], 500.0, places=2)

    def test_a_rename_of_nothing_leaves_the_target_as_it_was(self):
        # The source sold out before the rename: the merged pool is the
        # target's own 100 at 1,000, and the tail's drain check, now on
        # the live target, finds a position and leaves it (TJ-2333).
        _denied, rows, _ = _window_rows('SYQ.TO', held=8, sold=8)
        self._assert_row(rows, ('2025-03-03', 'SPLIT'), 100.0, 1000.0)

    def test_the_trace_marks_the_rename_rows_pool_columns(self):
        # The row's trace line keeps the row's symbol and says RENAMED:
        # its pool columns are the merged target's (TJ-2335).
        _denied, _rows, trace = _window_rows('SYQ.TO', trace=True)
        (line,) = [ln for ln in trace if '| SPLIT ' in ln]
        self.assertIn('RENAMED', line)
        self.assertIn('| SYR.TO', line)
        self.assertIn('108.0000', line)


if __name__ == '__main__':
    unittest.main()
