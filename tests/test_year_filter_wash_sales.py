"""Regression: `--year` filter must not zero out US wash_sales.

The Canada engine builds wash records with a nested `loss_tx` dict
(`{'loss_tx': v.to_dict(), ...}`). The US engine builds them flat
(`{'loss_tx_id': tx.id, 'date': tx.date, ...}`). Pre-fix, the filter
read `w.get('loss_tx', {})` then `dict.get('date', '')` — for US
records this resolved to an empty string, every entry's
`''.startswith(year)` was False, and `total_disallowed` summed to
zero on year-filtered US runs while per-gain `disallowed_amount`
stayed correct.

The fix uses `w.get('loss_tx', w)` so the fallback to `w` itself
exposes the flat `'date'` field. These tests pin both engine shapes."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_gains(txs, *args):
    """Invoke `taxjson-gains` against an in-memory transactions
    payload; return parsed JSON output."""
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / 'in.json'
        inp.write_text(json.dumps({'transactions': txs}))
        cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_gains',
               *args, str(inp)]
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


class TestYearFilterWashSalesUS(unittest.TestCase):
    def test_us_wash_sale_survives_year_filter(self):
        """A US wash-sale record (flat `'date'` field) must remain in
        `results['wash_sales']` and contribute to `total_disallowed`
        when `--year` matches its loss date."""
        # Classic §1091 short-cycle loss with a replacement in window.
        txs = [
            {'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
             'symbol': 'AAPL.US', 'quantity': -100, 'price': 100.0,
             'net_amount': 10000.0, 'currency': 'USD', 'account': 'M'},
            {'action': 'BUYSELL', 'date': '2025-02-10', 'time': '09:30:00',
             'symbol': 'AAPL.US', 'quantity': 100, 'price': 120.0,
             'net_amount': 12000.0, 'currency': 'USD', 'account': 'M'},
            {'action': 'BUYSELL', 'date': '2025-02-15', 'time': '09:30:00',
             'symbol': 'AAPL.US', 'quantity': -50, 'price': 110.0,
             'net_amount': 5500.0, 'currency': 'USD', 'account': 'M'},
        ]
        out = _run_gains(txs, '--country', 'usa', '--taxable',
                         '--year', '2025')
        # The buy-to-cover at 2/10 is a wash with the 2/15 short replacement.
        self.assertGreater(out['summary']['total_disallowed'], 1.0,
                           "US wash record survived --year filter")
        self.assertEqual(len(out['wash_sales']), 1)

    def test_canada_wash_sale_still_survives_year_filter(self):
        """Verify the Canada (nested `loss_tx`) path didn't regress."""
        txs = [
            {'action': 'BUYSELL', 'date': '2025-01-15',
             'symbol': 'SHOP.TO', 'quantity': 100, 'net_amount': 10000,
             'currency': 'CAD', 'account': 'Margin'},
            {'action': 'BUYSELL', 'date': '2025-02-15',
             'symbol': 'SHOP.TO', 'quantity': -100, 'net_amount': 8000,
             'currency': 'CAD', 'account': 'Margin'},
            # Replacement buy within 30 days.
            {'action': 'BUYSELL', 'date': '2025-02-25',
             'symbol': 'SHOP.TO', 'quantity': 100, 'net_amount': 8500,
             'currency': 'CAD', 'account': 'Margin'},
        ]
        out = _run_gains(txs, '--country', 'ca', '--taxable',
                         '--year', '2025')
        self.assertGreater(out['summary']['total_disallowed'], 1.0)
        self.assertEqual(len(out['wash_sales']), 1)


if __name__ == '__main__':
    unittest.main()
