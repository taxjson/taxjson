"""Regression tests for the low-round `ibparse` audit findings (IB
statement parser, shared broker helpers, taxjson-brokerage, broker
detection, the transaction schema) and the overnight-session
item. Every fixture is synthetic: invented tickers and ISINs, fake
account ids marked pii-ok.

Formerly: test_fix_l_ibparse"""
import unittest

from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------- detection and taxjson-brokerage
from pathlib import Path as _P


class TestTransfersViewInBookValue(unittest.TestCase):
    """S027-06: an in-book RBC transfer shows its BOOK VALUE.

    Formerly: test_fix_l_ibparse.TestTransfersViewInBookValue"""

    def test_book_row_value_from_its_description(self):
        import argparse, contextlib, io, json, tempfile
        from taxjson.cli import listings as cli_listings
        with tempfile.TemporaryDirectory() as td:
            root = _P(td)
            (root / 'taxjson.toml').write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n'
                '[accounts.tfsa]\ntype = "sheltered"\ntransfers = true\n')
            (root / 'work').mkdir()
            (root / 'work' / 'tfsa_base.json').write_text(json.dumps(
                {'transactions': [{
                    'action': 'TRANSFER', 'date': '2025-04-01',
                    'symbol': 'QZD.TO', 'quantity': 2000, 'currency': 'CAD',
                    'net_amount': 0.0, 'account': 'tfsa',
                    'description': 'TFI QZD ETF BOOK VALUE 13,579.24'}]}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli_listings.cmd_transfers_view(argparse.Namespace(
                    dir=str(root), account=None, json=True))
        rows = json.loads(out.getvalue())['transfers']
        self.assertEqual([r['value'] for r in rows], [13579.24])


if __name__ == '__main__':
    unittest.main()
