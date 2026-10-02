"""Re-audit-2 fixes: the .tt converter, the schema validator, Canadian
venue suffixes, bare rename targets and partial trade cancellations
(fix list parsers-common-03 + A2-0635). Synthetic data only."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_convert_tt import (parse_tt_line, tt_to_json,
                                            tx_to_tt_line)


def _parse(line):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        tx = parse_tt_line(line, account_name="m", source="h.tt:1")
    return tx, err.getvalue()


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


class TestNegativeSellTotal(unittest.TestCase):
    """A2-0292, A2-0620, A2-0621, A2-0622, A2-0623, A2-1073, A2-1226,
    A2-1227: a sale whose commission exceeds its gross nets negative;
    the writers emit that signed total and the reader books it."""

    PENNY = {'action': 'BUYSELL', 'date': '2025-06-16',
             'time': '09:30:00', 'symbol': 'ZZZ250620C00050000.US',
             'quantity': -1.0, 'currency': 'USD', 'price': 0.01,
             'net_amount': -8.95, 'fee': 9.95}

    def test_json_tt_json_round_trip_keeps_negative_proceeds(self):
        line = tx_to_tt_line(dict(self.PENNY))
        back, err = _parse(line)
        self.assertAlmostEqual(back['net_amount'], -8.95, places=5)
        self.assertAlmostEqual(back['fee'], 9.95, places=5)
        self.assertEqual(err, "")

    def test_hand_typed_negative_total_with_its_fee_is_read(self):
        tx, err = _parse("BUYSELL 2025-06-16 09:30:00 "
                         "ZZZ250620C00050000.US -1 USD 0.01 -8.95 9.95")
        self.assertAlmostEqual(tx['net_amount'], -8.95)
        self.assertEqual(err, "")

    def test_cash_signed_negative_total_still_refused(self):
        # R1-117: the fee does not explain the sign -> a typed '-'.
        with self.assertRaises(ValueError):
            _parse("BUYSELL 2025-01-05 09:31:00 ABC.US -100 USD 50.00 "
                   "-4995.00 5.00")

    def test_negative_total_that_does_not_match_is_refused(self):
        with self.assertRaises(ValueError):
            _parse("BUYSELL 2025-06-16 09:30:00 ZZZ250620C00050000.US "
                   "-1 USD 0.01 -50.00 9.95")

    def test_zero_total_now_warns_about_the_lost_commission(self):
        # The old README advice (enter 0) left the excess commission out
        # of the loss.
        _, err = _parse("BUYSELL 2025-06-16 09:30:00 "
                        "ZZZ250620C00050000.US -1 USD 0.01 0 9.95")
        self.assertIn("-8.95", err)
        self.assertIn("out of the loss", err)

    def test_futures_negative_total_needs_its_size(self):
        with self.assertRaises(ValueError) as cm:
            _parse("BUYSELL 2025-06-16 09:30:00 F:CLN5.US -1 USD 0.001 "
                   "-4.00 5.00")
        self.assertIn("x1000", str(cm.exception))
        tx, _ = _parse("BUYSELL 2025-06-16 09:30:00 F:CLN5.US -1 USD "
                       "0.001 -4.00 5.00 x1000")
        self.assertAlmostEqual(tx['net_amount'], -4.0)

    def test_events_view_line_reimports(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        line = _tx_display_line(dict(self.PENNY))
        back, _ = _parse(line)
        self.assertAlmostEqual(back['net_amount'], -8.95)

    def test_events_view_carries_contract_size(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        fut = {'action': 'BUYSELL', 'date': '2025-04-01',
               'time': '09:30:00', 'symbol': 'F:CLK5.US', 'quantity': 1,
               'currency': 'USD', 'price': 60.0, 'net_amount': 2.5,
               'fee': 2.5, 'multiplier': 1000}
        line = _tx_display_line(fut)
        self.assertTrue(line.endswith(" x1000"), line)
        mini = {'action': 'BUYSELL', 'date': '2025-04-01',
                'time': '09:30:00', 'symbol': 'XYZ250620C00050000.US',
                'quantity': 1, 'currency': 'USD', 'price': 1.0,
                'net_amount': 11.0, 'fee': 1.0, 'multiplier': 10}
        self.assertTrue(_tx_display_line(mini).endswith(" x10"))
        back, _ = _parse(_tx_display_line(mini))
        self.assertEqual(back['multiplier'], 10.0)


if __name__ == '__main__':
    unittest.main()
