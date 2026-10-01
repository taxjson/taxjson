"""Low-round parsers2 fixes: ticker.map tools.

S052-23 (the DELETE audit note signs cash by direction), S052-24 (the
warning names the DELETE line), S053-00 (two map sources refused),
S035-06 (merge2 --map help describes the keyword format).
Synthetic data only.
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.bin.taxjson_ticker_map import apply_drops


def _drops(txs, drops):
    buf = io.StringIO()
    with patch.object(sys, 'stderr', buf):
        kept = apply_drops(txs, drops)
    return kept, buf.getvalue()


class TestDeleteNote(unittest.TestCase):

    def test_cash_is_signed_by_direction(self):
        txs = [{'action': 'BUYSELL', 'symbol': 'JUNK.TO', 'quantity': 100,
                'net_amount': 1000.0},
               {'action': 'BUYSELL', 'symbol': 'JUNK.TO', 'quantity': -100,
                'net_amount': 1200.0}]
        _, err = _drops(txs, {'JUNK.TO'})
        self.assertIn('net cash +200.00', err)
        self.assertNotIn('2200', err)

    def test_warning_names_the_delete_line(self):
        _, err = _drops([{'action': 'BUYSELL', 'symbol': 'QZH.TO',
                          'quantity': 10, 'net_amount': 100.0}], {'QZH.TO'})
        self.assertIn('`DELETE QZH.TO`', err)
        self.assertNotIn('DROP line', err)


class TestTickerMapTool(unittest.TestCase):

    def test_two_map_sources_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            books = d / 'b.json'
            books.write_text(json.dumps({'transactions': []}))
            (d / 'a.map').write_text('GLOBAL ZZQ.US POS.TO\n')
            (d / 'b.map').write_text('GLOBAL ZZQ.US FLAG.TO\n')
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_ticker_map',
                 str(books), str(d / 'a.map'), '--map', str(d / 'b.map')],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('not both', r.stderr)

    def test_merge2_map_help_names_the_keywords(self):
        r = subprocess.run([sys.executable, '-m', 'taxjson.bin.taxjson_merge2',
                            '--help'], capture_output=True, text=True)
        help_text = ' '.join(r.stdout.split())
        self.assertIn('GLOBAL|TOBASE|JOURNAL FROM TO', help_text)
        self.assertNotIn('whitespace-separated `from to`', help_text)


if __name__ == '__main__':
    unittest.main()
