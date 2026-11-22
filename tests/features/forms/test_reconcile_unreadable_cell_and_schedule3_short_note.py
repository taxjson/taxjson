"""Re-audit-2 test pins, group G2 (parsers and message fixes).

Each test pins a fix that held on the audited commit but that no test
failed on when reverted (A2-0176, A2-0521, A2-0538, A2-0897, A2-1569,
A2-1582, A2-1612, A2-0894, A2-1597). Every fixture is SYNTHETIC: fake
account ids, invented tickers and amounts.

Formerly: test_fix_a2_pins3_g2
"""
import tempfile
import unittest


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


import os


class TestReconcileSlipsUnreadableCellWording(unittest.TestCase):
    """A2-1597 hunk d038ef7_022 (taxjson_reconcile_slips.py): the text
    report's NOT RECONCILED line says 'an unreadable cell' (cost and
    quantity cells too), not 'unreadable proceeds'.

    Formerly: test_fix_a2_pins3_g2.TestReconcileSlipsUnreadableCellWording"""

    def test_unreadable_cost_says_cell(self):
        from taxjson.bin import taxjson_reconcile_slips as RS
        from _support.filing_bin_reports import _gains, _slip, _sell, _run
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, [_sell(qty=-200, proceeds=18000.0, cost=18000.0,
                                  commission=0.0)])
            s = _slip(td, 'symbol,quantity,proceeds,cost\n'
                          'AAPL,100,9000.00,nan\nAAPL,100,9000.00,9000.00\n')
            rc, out, err = _run(RS.main, ['--country', 'canada', str(s),
                                          '--gains', str(g)])
        self.assertEqual(rc, 1, out + err)
        self.assertIn('NOT RECONCILED: 1 slip row(s) had an unreadable '
                      'cell', out)


class TestSchedule3ShortNote(unittest.TestCase):
    """A2-1597 hunk 9bb6bb9_029 (taxjson_form_export.py build_schedule3):
    a short row's note says which side PROCEEDS and ACB are.

    Formerly: test_fix_a2_pins3_g2.TestSchedule3ShortNote"""

    def test_short_note_names_the_sides(self):
        from taxjson.bin import taxjson_form_export as FE
        from taxjson.lib import tax_forms
        from _support.filing_bin_reports import _g
        rep = tax_forms.build_schedule3([
            _g(symbol='XYZ250620P00040000.TO', qty=-4.0, proceeds=0.0,
               cost=-400.0, gain=400.0, direction='SHORT', grant=True)],
            2025)
        text = ' '.join(FE.render_schedule3(rep, 2025, 'CAD').split())
        self.assertIn('includes short position(s) — PROCEEDS is the short '
                      'sale or write, ACB the cover', text)


if __name__ == '__main__':
    unittest.main()
