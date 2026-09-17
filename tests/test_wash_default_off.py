"""Tests for the wash-sale default-OFF behavior.

Matrix:
  - no flags (sheltered default)             → wash-sale OFF
  - --taxable                                → wash-sale ON
  - --taxable --no-wash                      → wash-sale OFF (override)
  - --no-wash alone (without --taxable)      → wash-sale OFF (still off)

Sheltered accounts (RRSP/TFSA/RESP/LIRA) don't have taxable losses to
disallow, so applying CRA's ITA 54 superficial-loss rule on those
inputs would produce false "disallowed" entries that confuse the
sanity-check reports.
"""
import io
import json
import sys as _sys
import unittest
from unittest.mock import patch


def _superficial_loss_fixture():
    """Construct a minimal scenario that produces a superficial loss
    when wash detection is on: buy 100 @ $50, sell 100 @ $40 (-$1000),
    rebuy 100 @ $42 within 30 days. Without wash detection: -$1000
    realized loss. With wash detection: $0 (fully disallowed,
    rolled into the replacement lot's basis)."""
    return [
        {'action': 'BUYSELL', 'date': '2025-03-01', 'symbol': 'AAPL.US',
         'quantity': 100, 'price': 50.0, 'net_amount': -5000.0,
         'account': 'X', 'currency': 'USD'},
        {'action': 'BUYSELL', 'date': '2025-06-15', 'symbol': 'AAPL.US',
         'quantity': -100, 'price': 40.0, 'net_amount': 4000.0,
         'account': 'X', 'currency': 'USD'},
        # Rebuy within 30 days of the loss → triggers superficial-loss.
        {'action': 'BUYSELL', 'date': '2025-06-25', 'symbol': 'AAPL.US',
         'quantity': 100, 'price': 42.0, 'net_amount': -4200.0,
         'account': 'X', 'currency': 'USD'},
    ]


def _run_gains_cli(transactions, *, taxable=False, no_wash=False, year=None):
    from taxjson.bin.taxjson_gains import main
    input_data = {'transactions': transactions}
    stdin_buf = io.StringIO(json.dumps(input_data))
    stdout_buf = io.StringIO()
    argv = ['taxjson-gains', '--country', 'canada']
    if year:
        argv.extend(['--year', str(year)])
    if taxable:
        argv.append('--taxable')
    if no_wash:
        argv.append('--no-wash')
    with patch.object(_sys, 'argv', argv), \
         patch.object(_sys, 'stdin', stdin_buf), \
         patch.object(_sys, 'stdout', stdout_buf):
        main()
    return json.loads(stdout_buf.getvalue())


class TestWashSaleDefaultOff(unittest.TestCase):
    def test_default_no_flag_wash_off(self):
        """Default invocation (sheltered assumed): the -$1000 loss is
        reported in full; no wash-sale entries."""
        result = _run_gains_cli(_superficial_loss_fixture(), year=2025)
        loss_entries = [t for t in result['transactions']
                        if t.get('symbol') == 'AAPL.US' and t.get('gain', 0) < 0]
        self.assertEqual(len(loss_entries), 1)
        self.assertAlmostEqual(loss_entries[0]['gain'], -1000.0, delta=1)
        # No wash sale records.
        self.assertEqual(result.get('wash_sales', []), [])
        self.assertEqual(result['summary'].get('total_disallowed', 0), 0)

    def test_taxable_enables_wash_detection(self):
        """--taxable: the same loss is wash-disallowed, gain becomes $0,
        a wash_sales entry appears."""
        result = _run_gains_cli(_superficial_loss_fixture(), year=2025, taxable=True)
        loss_entries = [t for t in result['transactions']
                        if t.get('symbol') == 'AAPL.US' and t.get('action') != 'DIVIDEND']
        self.assertEqual(len(loss_entries), 1)
        # Reported gain is zero (fully disallowed).
        self.assertAlmostEqual(loss_entries[0]['gain'], 0.0, places=2)
        # Wash sale recorded.
        self.assertGreaterEqual(len(result.get('wash_sales', [])), 1)
        self.assertAlmostEqual(result['summary']['total_disallowed'], 1000.0, delta=1)

    def test_taxable_with_no_wash_disables_again(self):
        """--taxable --no-wash: explicit opt-out, raw loss reported."""
        result = _run_gains_cli(
            _superficial_loss_fixture(), year=2025, taxable=True, no_wash=True,
        )
        loss_entries = [t for t in result['transactions']
                        if t.get('symbol') == 'AAPL.US' and t.get('gain', 0) < 0]
        self.assertAlmostEqual(loss_entries[0]['gain'], -1000.0, delta=1)
        self.assertEqual(result.get('wash_sales', []), [])

    def test_no_wash_without_taxable_still_off(self):
        """--no-wash by itself is redundant (wash is already off) but
        shouldn't error or change behavior."""
        result = _run_gains_cli(
            _superficial_loss_fixture(), year=2025, no_wash=True,
        )
        loss_entries = [t for t in result['transactions']
                        if t.get('symbol') == 'AAPL.US' and t.get('gain', 0) < 0]
        self.assertAlmostEqual(loss_entries[0]['gain'], -1000.0, delta=1)
        self.assertEqual(result.get('wash_sales', []), [])


if __name__ == '__main__':
    unittest.main()
