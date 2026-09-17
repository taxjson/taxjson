"""`position_start_date` on the inventory section + holdings TOML.

This field surfaces the earliest open of the CURRENT continuous run of
holding a security. When a position is opened, fully closed, then
re-opened, the field reports the date of the SECOND open — not the
first — so a user can look up the price at actual entry.

Engine semantics:
  - Canada (ACB): one blended pool per symbol. Field is seeded on the
    trade that brings the pool from zero to non-zero, preserved across
    same-direction adds, cleared on drain-to-zero, and reset on a
    cross-zero flip (long → short or vice versa). SPLIT preserves it
    (the position is continuous through the corporate action).
  - US (FIFO): per-lot tracking. Field is min(lot.date) across the
    remaining lots. After FIFO closes consume the oldest lots, the
    field naturally moves forward to whichever lot is now oldest.

The TOML export:
  - emits a bare TOML local date (YYYY-MM-DD) so tomllib parses it as
    a date object, and aggregating the same symbol across multiple
    input files keeps the earliest contribution.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import CanadaTaxRules, USATaxRules, TaxTransaction

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

REPO_ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# Canada (ACB) — single-pool semantics
# ============================================================================
class TestCanadaPositionStart(unittest.TestCase):
    def _inv(self, txs):
        result = CanadaTaxRules().compute_gains(txs)
        return {h['symbol']: h for h in result['inventory']}

    def test_first_buy_seeds_start_date(self):
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, net_amount=10000, currency='USD',
                           account='M'),
        ])
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-01-15')

    def test_subsequent_buys_preserve_start_date(self):
        """ACB pool is a continuous run — additional same-direction
        buys do NOT advance the position-start date."""
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, net_amount=10000, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2024-06-20', symbol='AAPL',
                           quantity=50, net_amount=6000, currency='USD',
                           account='M'),
        ])
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-01-15')

    def test_drain_then_reopen_uses_second_open_date(self):
        """The user's stated requirement: opened, closed, re-opened ⇒
        position_start_date is the SECOND open."""
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, net_amount=10000, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2024-03-15', symbol='AAPL',
                           quantity=-100, net_amount=11000, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2024-09-01', symbol='AAPL',
                           quantity=50, net_amount=6000, currency='USD',
                           account='M'),
        ])
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-09-01')

    def test_full_close_no_reopen_drops_from_inventory(self):
        """A drained pool isn't in the inventory section at all (qty
        filter), so position_start_date doesn't surface — verified by
        absence."""
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, net_amount=10000, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2024-03-15', symbol='AAPL',
                           quantity=-100, net_amount=11000, currency='USD',
                           account='M'),
        ])
        self.assertNotIn('AAPL', inv)

    def test_long_to_short_flip_resets_start_date(self):
        """A SELL that exceeds the long pool closes everything and
        opens a short. The new short's start is the flip-trade date,
        not the original long's date."""
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, net_amount=10000, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2024-04-10', symbol='AAPL',
                           quantity=-150, net_amount=16500, currency='USD',
                           account='M'),
        ])
        # 50-share short remaining.
        self.assertLess(inv['AAPL']['qty'], 0)
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-04-10')

    def test_split_preserves_start_date(self):
        """Corporate-action SPLIT rescales qty but the position is
        continuous; position_start_date must NOT advance."""
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=10, net_amount=1000, currency='USD',
                           account='M'),
            TaxTransaction(action='SPLIT', date='2024-05-20', symbol='AAPL',
                           quantity=10.0, currency='USD', account='M'),
        ])
        self.assertEqual(inv['AAPL']['qty'], 100)
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-01-15')

    def test_opening_balance_seeds_start_date(self):
        """OPENING_BALANCE (phantom pre-data-window holdings from
        --incomplete-history) seeds the field with the OB date.
        Approximate by construction — the real entry pre-dates the
        data window — but the OB date is the best available signal."""
        inv = self._inv([
            TaxTransaction(action='OPENING_BALANCE', date='2023-12-31',
                           symbol='AAPL', quantity=100, currency='USD',
                           account='M'),
        ])
        self.assertEqual(inv['AAPL']['position_start_date'], '2023-12-31')

    def test_split_rename_merge_keeps_earliest_position_start(self):
        """SPLIT-with-`symbol_new` (the SSL.TO → RGLD.US class of
        corporate event) merges the source pool into the target.
        If the user happens to hold both pre-merger, the combined
        position's start is the EARLIER of the two — same convention
        as `last_acq_date` merging at the same site."""
        inv = self._inv([
            # Older position on the soon-to-be-renamed source.
            TaxTransaction(action='BUYSELL', date='2024-01-15',
                           symbol='SSL.TO', quantity=100, price=10.0,
                           net_amount=1000.0, currency='CAD', account='M'),
            # Newer position on the target ticker.
            TaxTransaction(action='BUYSELL', date='2024-06-01',
                           symbol='RGLD.TO', quantity=50, price=200.0,
                           net_amount=10000.0, currency='CAD', account='M'),
            # The merger renames SSL.TO → RGLD.TO at a 1-for-16 ratio.
            TaxTransaction(action='SPLIT', date='2024-09-01',
                           symbol='SSL.TO', symbol_new='RGLD.TO',
                           quantity=0.0625, currency='CAD', account='M'),
        ])
        # Source ticker is gone; target absorbed it.
        self.assertNotIn('SSL.TO', inv)
        self.assertIn('RGLD.TO', inv)
        # Combined position carries the EARLIER position-start date.
        self.assertEqual(inv['RGLD.TO']['position_start_date'], '2024-01-15')


# ============================================================================
# US (FIFO) — per-lot semantics
# ============================================================================
class TestUSAPositionStart(unittest.TestCase):
    def _inv(self, txs):
        result = USATaxRules().compute_gains(txs)
        return {h['symbol']: h for h in result['inventory']}

    def test_first_buy_seeds_start_date(self):
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, price=100.0, net_amount=10000,
                           currency='USD', account='M'),
        ])
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-01-15')

    def test_two_lots_returns_earliest(self):
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, price=100.0, net_amount=10000,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2024-03-20', symbol='AAPL',
                           quantity=100, price=120.0, net_amount=12000,
                           currency='USD', account='M'),
        ])
        # min(lot.date) across remaining lots.
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-01-15')

    def test_fifo_close_consumes_oldest_lot_advances_start_date(self):
        """US-specific: SELL closes the OLDEST lot FIFO. After the
        oldest lot is gone, position_start_date naturally advances to
        the next-oldest remaining lot — even though no drain-to-zero
        happened. Different from Canada's continuous-pool semantic."""
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, price=100.0, net_amount=10000,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2024-03-20', symbol='AAPL',
                           quantity=100, price=120.0, net_amount=12000,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2024-06-10', symbol='AAPL',
                           quantity=-100, price=130.0, net_amount=13000,
                           currency='USD', account='M'),
        ])
        # Lot 1 (Jan) was consumed; lot 2 (Mar) is now the oldest.
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-03-20')

    def test_drain_then_reopen_uses_second_open_date(self):
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, price=100.0, net_amount=10000,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2024-03-15', symbol='AAPL',
                           quantity=-100, price=110.0, net_amount=11000,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2024-09-01', symbol='AAPL',
                           quantity=50, price=120.0, net_amount=6000,
                           currency='USD', account='M'),
        ])
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-09-01')

    def test_long_to_short_flip_uses_flip_date(self):
        inv = self._inv([
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=100, price=100.0, net_amount=10000,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2024-04-10', symbol='AAPL',
                           quantity=-150, price=110.0, net_amount=16500,
                           currency='USD', account='M'),
        ])
        self.assertLess(inv['AAPL']['qty'], 0)
        self.assertEqual(inv['AAPL']['position_start_date'], '2024-04-10')


# ============================================================================
# taxjson-export holdings.toml — position_start_date emission + aggregation
# ============================================================================
@unittest.skipIf(tomllib is None,
                 "no TOML reader (Python < 3.11 without `tomli`)")
class TestHoldingsTomlPositionStart(unittest.TestCase):
    def _export(self, inventory, *extra_args):
        with tempfile.TemporaryDirectory() as tmp:
            gains = Path(tmp) / 'gains.json'
            gains.write_text(json.dumps({'inventory': inventory}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--holdings-toml', *extra_args, str(gains)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return tomllib.loads(r.stdout), r.stdout

    def test_emits_bare_toml_date(self):
        doc, stdout = self._export([
            {'symbol': 'AAPL.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD', 'position_start_date': '2024-01-15'},
        ])
        h = doc['holding'][0]
        # tomllib parses a TOML local date into a date object.
        self.assertEqual(h['position_start_date'].isoformat(), '2024-01-15')
        # And the emitted line is bare (no quotes), so it round-trips.
        self.assertIn('position_start_date = 2024-01-15', stdout)

    def test_omits_field_when_missing(self):
        doc, stdout = self._export([
            {'symbol': 'AAPL.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD'},
        ])
        h = doc['holding'][0]
        self.assertNotIn('position_start_date', h)
        self.assertNotIn('position_start_date', stdout)

    def test_aggregation_keeps_earliest(self):
        """When multiple inputs contribute to the same symbol bucket
        (e.g. cross-account aggregation in `export.sh`), the earlier
        position_start_date wins."""
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / 'a.json'
            b = Path(tmp) / 'b.json'
            a.write_text(json.dumps({'inventory': [{
                'symbol': 'AAPL.US', 'qty': 50.0, 'total_cost': 5000.0,
                'currency': 'USD', 'position_start_date': '2024-03-20',
            }]}))
            b.write_text(json.dumps({'inventory': [{
                'symbol': 'AAPL.US', 'qty': 100.0, 'total_cost': 10000.0,
                'currency': 'USD', 'position_start_date': '2024-01-15',
            }]}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--holdings-toml', str(a), str(b)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = tomllib.loads(r.stdout)
            h = next(x for x in doc['holding'] if x['symbol'] == 'AAPL.US')
            self.assertEqual(h['position_start_date'].isoformat(), '2024-01-15')


if __name__ == '__main__':
    unittest.main()
