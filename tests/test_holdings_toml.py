"""Tests for `taxjson-export --holdings-toml`.

The TOML holdings export is a machine-readable handoff for live-pricing
and trading tools. The contract:

  - Output is valid TOML that round-trips through a parser.
  - A [meta] table carries schema_version, generated_at, source, and
    (when --account-name is given) the account.
  - One [[holding]] per open position; zero-quantity positions dropped.
  - Equity vs option is tagged via asset_type; options carry broken-out
    underlying / right / strike / expiry plus contract_multiplier.
  - total_cost is the source of truth; cost_per_share is derived.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

REPO_ROOT = Path(__file__).resolve().parent.parent


def _export_toml(inventory, *extra_args):
    """Run taxjson-export --holdings-toml on a gains-shaped JSON whose
    inventory section is `inventory`; return the parsed TOML dict."""
    with tempfile.TemporaryDirectory() as tmp:
        gains = Path(tmp) / 'gains.json'
        gains.write_text(json.dumps({'inventory': inventory}))
        cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
               '--holdings-toml', *extra_args, str(gains)]
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return tomllib.loads(r.stdout), r.stdout


@unittest.skipIf(tomllib is None,
                 "no TOML reader (Python < 3.11 without `tomli`)")
class TestHoldingsToml(unittest.TestCase):
    def test_valid_toml_with_meta(self):
        doc, _ = _export_toml([
            {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD'},
        ], '--account-name', 'margin')
        self.assertEqual(doc['schema_version'], '1.2')
        self.assertEqual(doc['meta']['account'], 'margin')
        self.assertIn('generated_at', doc['meta'])
        self.assertEqual(doc['meta']['holdings_count'], 1)

    def test_base_currency_fields_embedded(self):
        # A USD holding whose base-currency (CAD) companion inventory is
        # supplied via --base-gains carries base_currency / base_total_cost /
        # base_cost_per_share alongside the native figures.
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp) / 'native.json'
            native.write_text(json.dumps({'inventory': [
                {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
                 'currency': 'USD'},
            ]}))
            base = Path(tmp) / 'base.json'
            base.write_text(json.dumps({'inventory': [
                {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 9350.12,
                 'currency': 'CAD'},
            ]}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--holdings-toml', '--base-gains', str(base), str(native)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            h = tomllib.loads(r.stdout)['holding'][0]
        self.assertEqual(h['currency'], 'USD')
        self.assertEqual(h['total_cost'], 6793.45)
        self.assertEqual(h['base_currency'], 'CAD')
        self.assertEqual(h['base_total_cost'], 9350.12)
        self.assertAlmostEqual(h['base_cost_per_share'], 9350.12 / 35.0,
                               places=4)

    def test_base_currency_mismatch_skips_base_fields(self):
        # If a base bucket is NOT in the expected base currency (e.g. a failed
        # FX conversion left it in the source currency), --base-currency makes
        # the exporter skip the base_* fields rather than mislabel them.
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp) / 'native.json'
            native.write_text(json.dumps({'inventory': [
                {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
                 'currency': 'USD'},
            ]}))
            base = Path(tmp) / 'base.json'
            # Bucket is still USD — conversion did not happen.
            base.write_text(json.dumps({'inventory': [
                {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
                 'currency': 'USD'},
            ]}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--holdings-toml', '--base-gains', str(base),
                   '--base-currency', 'CAD', str(native)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            h = tomllib.loads(r.stdout)['holding'][0]
        self.assertNotIn('base_total_cost', h)
        self.assertNotIn('base_currency', h)

    def test_trades_events_embedded(self):
        # --trades attaches each holding's native-currency acquisition/sell
        # events ({date, action, qty, price}), date-sorted, BUYSELL/ASSIGN only.
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / 'gains.json'
            inv.write_text(json.dumps({'inventory': [
                {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
                 'currency': 'USD'}]}))
            raw = Path(tmp) / 'raw.json'
            raw.write_text(json.dumps({'transactions': [
                {'action': 'BUYSELL', 'date': '2025-03-01', 'symbol': 'AEM.US',
                 'quantity': 20, 'price': 100.0, 'currency': 'USD'},
                {'action': 'BUYSELL', 'date': '2025-06-01', 'symbol': 'AEM.US',
                 'quantity': -5, 'price': 120.0, 'currency': 'USD'},
                {'action': 'BUYSELL', 'date': '2025-02-01', 'symbol': 'AEM.US',
                 'quantity': 20, 'price': 90.0, 'currency': 'USD'},
                {'action': 'DIVIDEND', 'date': '2025-04-01', 'symbol': 'AEM.US',
                 'quantity': 0, 'net_amount': 5},          # excluded
                {'action': 'BUYSELL', 'date': '2025-05-01', 'symbol': 'XYZ.US',
                 'quantity': 10, 'price': 50.0, 'currency': 'USD'},  # other sym
            ]}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--holdings-toml', '--trades', str(raw), str(inv)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            h = tomllib.loads(r.stdout)['holding'][0]
        tr = h['trades']
        self.assertEqual(len(tr), 3)  # 2 buys + 1 sell; dividend & other excluded
        self.assertEqual([str(t['date']) for t in tr],
                         ['2025-02-01', '2025-03-01', '2025-06-01'])  # date-sorted
        self.assertEqual((tr[0]['action'], tr[0]['qty'], tr[0]['price']),
                         ('BUY', 20.0, 90.0))
        self.assertEqual((tr[2]['action'], tr[2]['qty'], tr[2]['price']),
                         ('SELL', 5.0, 120.0))  # native price, abs qty

    def test_trades_only_current_position(self):
        # Bought 10, sold all 10 (position flat), then re-bought 4 and sold 1.
        # Only the live round (the re-buy of 4 and the sell of 1) should appear.
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / 'gains.json'
            inv.write_text(json.dumps({'inventory': [
                {'symbol': 'AEM.US', 'qty': 3.0, 'total_cost': 300.0,
                 'currency': 'USD'}]}))
            raw = Path(tmp) / 'raw.json'
            raw.write_text(json.dumps({'transactions': [
                {'action': 'BUYSELL', 'date': '2025-01-01', 'symbol': 'AEM.US',
                 'quantity': 10, 'price': 50.0, 'currency': 'USD'},
                {'action': 'BUYSELL', 'date': '2025-02-01', 'symbol': 'AEM.US',
                 'quantity': -10, 'price': 60.0, 'currency': 'USD'},  # flat here
                {'action': 'BUYSELL', 'date': '2025-03-01', 'symbol': 'AEM.US',
                 'quantity': 4, 'price': 70.0, 'currency': 'USD'},
                {'action': 'BUYSELL', 'date': '2025-04-01', 'symbol': 'AEM.US',
                 'quantity': -1, 'price': 80.0, 'currency': 'USD'},
            ]}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--holdings-toml', '--trades', str(raw), str(inv)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            tr = tomllib.loads(r.stdout)['holding'][0]['trades']
        self.assertEqual([str(t['date']) for t in tr],
                         ['2025-03-01', '2025-04-01'])  # pre-flat round excluded
        self.assertEqual(tr[0]['price'], 70.0)

    def test_trades_absent_without_flag(self):
        doc, _ = _export_toml([
            {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD'}])
        self.assertNotIn('trades', doc['holding'][0])

    def test_base_fields_absent_without_base_gains(self):
        # Without --base-gains the holding has no base_* keys (back-compat).
        doc, _ = _export_toml([
            {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD'},
        ])
        self.assertNotIn('base_total_cost', doc['holding'][0])

    def test_equity_holding_fields(self):
        doc, _ = _export_toml([
            {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD'},
        ])
        h = doc['holding'][0]
        self.assertEqual(h['symbol'], 'AEM.US')
        self.assertEqual(h['asset_type'], 'equity')
        self.assertEqual(h['quantity'], 35.0)
        self.assertEqual(h['currency'], 'USD')
        self.assertEqual(h['total_cost'], 6793.45)
        self.assertAlmostEqual(h['cost_per_share'], 6793.45 / 35.0, places=4)
        # An equity carries no option-only fields.
        self.assertNotIn('strike', h)

    def test_option_holding_broken_out(self):
        doc, _ = _export_toml([
            {'symbol': 'BCE260116C00046000.TO', 'qty': 64.0,
             'total_cost': 11977.8, 'currency': 'CAD'},
        ])
        h = doc['holding'][0]
        self.assertEqual(h['asset_type'], 'option')
        self.assertEqual(h['underlying'], 'BCE.TO')
        self.assertEqual(h['right'], 'call')
        self.assertEqual(h['strike'], 46.0)
        # tomllib parses a TOML local date into a date object.
        self.assertEqual(h['expiry'].isoformat(), '2026-01-16')
        self.assertEqual(h['contract_multiplier'], 100)
        self.assertEqual(h['quantity'], 64.0)

    def test_zero_quantity_dropped(self):
        doc, _ = _export_toml([
            {'symbol': 'AEM.US', 'qty': 0.0, 'total_cost': 0.0, 'currency': 'USD'},
            {'symbol': 'ENB.TO', 'qty': 900.0, 'total_cost': 873.0, 'currency': 'CAD'},
        ])
        symbols = [h['symbol'] for h in doc['holding']]
        self.assertEqual(symbols, ['ENB.TO'])

    def test_short_position_keeps_sign(self):
        doc, _ = _export_toml([
            {'symbol': 'XYZ.US', 'qty': -100.0, 'total_cost': -5000.0,
             'currency': 'USD'},
        ])
        h = doc['holding'][0]
        self.assertEqual(h['quantity'], -100.0)
        # cost_per_share = total_cost / qty → positive for a short.
        self.assertGreater(h['cost_per_share'], 0)

    def test_account_omitted_when_not_given(self):
        doc, _ = _export_toml([
            {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD'},
        ])
        self.assertNotIn('account', doc['meta'])
        self.assertNotIn('account', doc['holding'][0])

    def test_dust_holding_dropped(self):
        """A sub-fractional residue (corp-action ratio / float noise) is
        dropped by the default dust threshold; a real holding survives."""
        doc, _ = _export_toml([
            {'symbol': 'AAUC.TO', 'qty': -3.333333e-05, 'total_cost': 0.0007,
             'currency': 'CAD'},
            {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
             'currency': 'USD'},
        ])
        self.assertEqual([h['symbol'] for h in doc['holding']], ['AEM.US'])

    def test_dust_threshold_zero_keeps_everything(self):
        doc, _ = _export_toml([
            {'symbol': 'AAUC.TO', 'qty': -3.333333e-05, 'total_cost': 0.0007,
             'currency': 'CAD'},
        ], '--dust-threshold', '0')
        self.assertEqual(len(doc['holding']), 1)

    def test_map_nets_norberts_gambit(self):
        """A --map entry folds the DLR.US leg into DLR.TO; the offsetting
        quantities then net to zero and the position drops out, while an
        unrelated holding is untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            gains = Path(tmp) / 'gains.json'
            gains.write_text(json.dumps({'inventory': [
                {'symbol': 'DLR.TO', 'qty': -10000.0, 'total_cost': -140000.0,
                 'currency': 'CAD'},
                {'symbol': 'DLR.US', 'qty': 10000.0, 'total_cost': 102000.0,
                 'currency': 'USD'},
                {'symbol': 'AEM.US', 'qty': 35.0, 'total_cost': 6793.45,
                 'currency': 'USD'},
            ]}))
            mapfile = Path(tmp) / 'ticker.map'
            mapfile.write_text("JOURNAL DLR.US DLR.TO\n")
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--holdings-toml', '--map', str(mapfile), str(gains)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = tomllib.loads(r.stdout)
            syms = [h['symbol'] for h in doc['holding']]
            self.assertNotIn('DLR.TO', syms)   # netted to zero → dropped
            self.assertNotIn('DLR.US', syms)
            self.assertEqual(syms, ['AEM.US'])  # unrelated holding kept


@unittest.skipIf(tomllib is None,
                 "no TOML reader (Python < 3.11 without `tomli`)")
class TestTomlInput(unittest.TestCase):
    """taxjson-export accepts a holdings TOML snapshot as input (detected
    by the .toml extension), not just gains JSON — so the watchlist
    exports can run straight off `reports/*_holdings.toml`. A `[[holding]]`
    table's `quantity` is adapted to the `qty` the tool consumes."""

    def _run(self, toml_text, *args):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'h.toml'
            p.write_text(toml_text)
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   *args, str(p)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

    def test_platform_export_reads_toml(self):
        out = self._run(
            '[[holding]]\nsymbol = "AEM.US"\nasset_type = "equity"\n'
            'quantity = 35.0\ncurrency = "USD"\ntotal_cost = 6793.45\n',
            '--seekingalpha')
        self.assertEqual(out.strip(), 'AEM')

    def test_report_reads_toml_quantity_and_cost(self):
        out = self._run(
            '[[holding]]\nsymbol = "ENB.TO"\nquantity = 900.0\n'
            'currency = "CAD"\ntotal_cost = 873.0\n',
            '--report')
        self.assertIn('ENB.TO', out)
        self.assertIn('873.00', out)

    def test_short_filter_uses_toml_quantity_sign(self):
        # The sign of `quantity` must survive the TOML→inventory adapt,
        # or --short/--long would silently filter the wrong positions.
        out = self._run(
            '[[holding]]\nsymbol = "XYZ.US"\nquantity = -100.0\n'
            'currency = "USD"\ntotal_cost = -5000.0\n'
            '[[holding]]\nsymbol = "ABC.US"\nquantity = 50.0\n'
            'currency = "USD"\ntotal_cost = 5000.0\n',
            '--seekingalpha', '--short')
        self.assertEqual(out.strip(), 'XYZ')


if __name__ == '__main__':
    unittest.main()
