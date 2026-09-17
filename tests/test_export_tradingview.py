"""Tests for `taxjson-export --tradingview` output ordering.

TradingView sorts an imported watchlist by the full `EXCHANGE:TICKER`
string, which scatters `TSX:ABC` (under T) away from a bare US `ABC`
(under A). The export sorts --tradingview output by the bare ticker,
prefix ignored, so the imported watchlist needs no manual re-sort.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestTradingViewSort(unittest.TestCase):
    def test_sorts_by_bare_ticker_ignoring_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            gains = Path(tmp) / 'gains.json'
            gains.write_text(json.dumps({'inventory': [
                {'symbol': 'CNQ.TO', 'qty': 10, 'total_cost': 100,
                 'currency': 'CAD'},
                {'symbol': 'ABT.US', 'qty': 10, 'total_cost': 100,
                 'currency': 'USD'},
                {'symbol': 'BCE.TO', 'qty': 10, 'total_cost': 100,
                 'currency': 'CAD'},
                {'symbol': 'BSY.US', 'qty': 10, 'total_cost': 100,
                 'currency': 'USD'},
            ]}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--tradingview', str(gains)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            # Bare US tickers interleave with TSX:-prefixed ones by the
            # symbol after the colon: ABT, BCE, BSY, CNQ.
            self.assertEqual(r.stdout.strip().splitlines(),
                             ['ABT', 'TSX:BCE', 'BSY', 'TSX:CNQ'])

    def _run_with_map(self, map_text, inventory):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'tv_exchange.map').write_text(map_text)
            gains = tmp / 'gains.json'
            gains.write_text(json.dumps({'inventory': inventory}))
            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                   '--tradingview', str(gains)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip().splitlines()

    def test_qualified_map_key_disambiguates_dual_listing(self):
        """An `OR.US NYSE` entry prefixes only the US listing; the TSX
        listing has no `.TO` entry so it falls through to the TSX
        default — a dual-listed name appears as both NYSE:OR and TSX:OR
        instead of collapsing onto one prefix."""
        out = self._run_with_map('OR.US NYSE\n', [
            {'symbol': 'OR.US', 'qty': 10, 'total_cost': 100,
             'currency': 'USD'},
            {'symbol': 'OR.TO', 'qty': 10, 'total_cost': 100,
             'currency': 'CAD'},
        ])
        self.assertEqual(out, ['NYSE:OR', 'TSX:OR'])

    def test_bare_map_key_still_applies_to_all_listings(self):
        """Back-compat: a bare key (no extension) keeps applying to
        every listing — both OR.US and OR.TO take the NYSE prefix, then
        collapse to one entry via the formatted-ticker dedup. (Without
        the bare key the TSX listing would surface as a second TSX:OR.)"""
        out = self._run_with_map('OR NYSE\n', [
            {'symbol': 'OR.US', 'qty': 10, 'total_cost': 100,
             'currency': 'USD'},
            {'symbol': 'OR.TO', 'qty': 10, 'total_cost': 100,
             'currency': 'CAD'},
        ])
        self.assertEqual(out, ['NYSE:OR'])


if __name__ == '__main__':
    unittest.main()
