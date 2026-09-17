"""Regression: IB Trades must emit a `description` field so
`taxjson-brokerage`'s `--security-overrides` can rewrite mislabeled
IB tickers.

Every other parser (RBC, Webull, Questrade, Coinbase, Kraken) emits
`description` on trade rows. IB Trades was the lone gap: the emit
dict at `ib_extractor.py` had no `description` key, and
`apply_security_override` keys on the description substring. Result:
adding an IB ticker to `ticker_extraction_overrides.txt` silently
no-op'd.
"""
import os
import subprocess
import sys
import tempfile
import unittest
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


_IB_TRADES_CSV = (
    'Statement,Header,Field Name,Field Value\n'
    'Statement,Data,BrokerName,Interactive Brokers\n'
    'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
    'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
    'Realized P/L,MTM P/L,Code\n'
    # A USD trade on a ticker that the user wants to remap via override.
    'Trades,Data,Order,Stocks,USD,DLR,"2025-05-01, 09:30:00",'
    '100,1.00,0,100,1,0,0,0,O\n'
)


class TestIbTradesDescriptionForOverrides(unittest.TestCase):
    def test_ib_trade_emits_description(self):
        """The raw Symbol value is preserved as `description` so
        security-overrides (which match on description substring)
        can fire on IB rows."""
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                          delete=False) as f:
            f.write(_IB_TRADES_CSV)
            fname = f.name
        try:
            txs = IbBrokerage().parse_file(Path(fname))
        finally:
            os.remove(fname)
        trade = next(t for t in txs if t.get('symbol', '').startswith('DLR'))
        # The raw IB Symbol (`DLR`) lives on `description` so an
        # override file keyed on "DLR" can match.
        self.assertEqual(trade.get('description'), 'DLR')

    def test_security_override_can_rewrite_ib_ticker(self):
        """End-to-end: a `ticker_extraction_overrides.txt` entry keyed
        on the raw IB symbol must rewrite the parsed ticker. Pre-fix,
        the IB row's missing description silently failed to match."""
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / 'ib.csv'
            csv_path.write_text(_IB_TRADES_CSV)
            ov_path = Path(tmp) / 'overrides.txt'
            # Rewrite USD `DLR` (which IB would suffix to `DLR.US`) to
            # `DLR.U.TO` — the real TSX-listed Global X US Dollar ETF
            # USD class. This is the canonical use case for security
            # overrides (the user's own example).
            ov_path.write_text('DLR | USD | DLR.U.TO\n')

            cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
                   '--brokerage', 'ib', '--account', 'Margin',
                   '--security-overrides', str(ov_path),
                   str(csv_path)]
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            syms = [t['symbol'] for t in out['transactions']]
            self.assertIn('DLR.U.TO', syms,
                          "Security override must rewrite the IB row")
            self.assertNotIn('DLR.US', syms,
                             "Pre-fix the override silently no-op'd "
                             "and the row kept the default .US suffix")


if __name__ == '__main__':
    unittest.main()
