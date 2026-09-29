"""IB Dividends / Withholding Tax sections use strict header lookups.

Regression: these sections read columns via `header_map.get(key, 0)`,
which silently fell back to `row[0]` — the literal section name
('Dividends' / 'Withholding Tax') — when a column was missing from the
export, emitting a junk transaction (currency/date = the section name)
instead of skipping the row. They now use strict `header_map[key]`
lookups inside a try/except, matching the Trades section: a missing
column skipped the row with a stderr warning.

RE-PREMISED (2026-09 parse hardening): a skipped income row is still a
silent loss of income from the book. A missing required column now
FAILS the parse with a BrokerageParseError naming the section and the
column.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import IbBrokerage


def _parse(content: str):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(content)
        fname = f.name
    err = io.StringIO()
    with redirect_stderr(err):
        txs = IbBrokerage().parse_file(Path(fname))
    Path(fname).unlink()
    return txs, err.getvalue()


class TestIbStrictHeaderLookups(unittest.TestCase):
    def test_wellformed_dividend_still_parses(self):
        content = (
            'Dividends,Header,Currency,Account,Date,Description,Amount\n'
            'Dividends,Data,USD,U1,2025-01-15,AAPL(US0378331005) Cash Dividend,100.00\n'
        )
        txs, _ = _parse(content)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        self.assertEqual(divs[0]['currency'], 'USD')
        self.assertAlmostEqual(divs[0]['net_amount'], 100.00)

    def test_dividend_missing_currency_column_fails_the_parse(self):
        # Header omits 'Currency' — the old code emitted a row with
        # currency='Dividends', then skipped it; now the parse fails.
        content = (
            'Dividends,Header,Account,Date,Description,Amount\n'
            'Dividends,Data,U1,2025-01-15,AAPL(US0378331005) Cash Dividend,100.00\n'
        )
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(content)
        self.assertIn("'Dividends'", str(cm.exception))
        self.assertIn("'Currency'", str(cm.exception))

    def test_withholding_missing_amount_column_fails_the_parse(self):
        content = (
            'Withholding Tax,Header,Currency,Account,Date,Description\n'
            'Withholding Tax,Data,USD,U1,2025-01-15,AAPL(US0378331005) Tax\n'
        )
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(content)
        self.assertIn("'Withholding Tax'", str(cm.exception))
        self.assertIn("'Amount'", str(cm.exception))

if __name__ == '__main__':
    unittest.main()
