"""Tests for taxjson-convert-tt bi-directional conversion."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_convert_tt import (
    parse_tt_line, tx_to_tt_line, tt_to_json, json_to_tt_lines,
)


class TestParseTTLine(unittest.TestCase):
    def test_buysell(self):
        tx = parse_tt_line(
            "BUYSELL 2025-01-15 09:30:00 AAPL.US 100 USD 150.00 15000.00 5.00"
        )
        self.assertEqual(tx['action'], 'BUYSELL')
        self.assertEqual(tx['symbol'], 'AAPL.US')
        self.assertAlmostEqual(tx['quantity'], 100)
        self.assertEqual(tx['currency'], 'USD')
        self.assertAlmostEqual(tx['fee'], 5.0)

    def test_dividend(self):
        tx = parse_tt_line(
            "DIVIDEND 2025-03-15 09:30:00 X 0 USD 0.00 100.00"
        )
        self.assertEqual(tx['action'], 'DIVIDEND')
        self.assertAlmostEqual(tx['gross_amount'], 100.0)
        self.assertAlmostEqual(tx['net_amount'], 100.0)

    def test_comments_skipped(self):
        self.assertIsNone(parse_tt_line("# this is a comment"))
        self.assertIsNone(parse_tt_line(""))
        self.assertIsNone(parse_tt_line("GAIN 2025-01-15 ..."))  # not a tx action


class TestTxToTTLine(unittest.TestCase):
    def test_buysell_emits_canonical_field_order(self):
        tx = {
            'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
            'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
            'price': 150.0, 'net_amount': 15000.0, 'fee': 5.0,
        }
        line = tx_to_tt_line(tx)
        self.assertIsNotNone(line)
        parts = line.split()
        self.assertEqual(parts[0], 'BUYSELL')
        self.assertEqual(parts[3], 'AAPL.US')
        # Numeric fields formatted with explicit precision (qty/price %.8f,
        # totals %.5f) — matches the Perl scripts so old tools can re-ingest.
        self.assertIn('100.00000000', parts)
        self.assertIn('15000.00000', parts)

    def test_dividend_uses_gross_when_present(self):
        # parsers may set gross_amount as the pre-withholding figure; that's
        # the value `.tt` consumers expect on the total field.
        tx = {
            'action': 'DIVIDEND', 'date': '2025-03-15', 'time': '09:30:00',
            'symbol': 'X', 'quantity': 0, 'currency': 'USD',
            'price': 0, 'net_amount': 85.0, 'gross_amount': 100.0,
        }
        line = tx_to_tt_line(tx)
        self.assertIn('100.00000', line)
        # The withholding-NETTED value rides along as an optional 9th
        # column (2026-08 deep audit: without it a json→tt→json cycle
        # re-parsed net as gross and inflated income). Legacy consumers
        # ignore trailing columns; the total field is still the gross.
        self.assertTrue(line.rstrip().endswith('85.00000'), line)
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        back = parse_tt_line(line)
        self.assertAlmostEqual(back['gross_amount'], 100.0, places=2)
        self.assertAlmostEqual(back['net_amount'], 85.0, places=2)

    def test_interest(self):
        tx = {
            'action': 'INTEREST', 'date': '2025-01-02', 'time': '09:30:00',
            'currency': 'USD', 'net_amount': 385.77,
        }
        self.assertIn('385.77000', tx_to_tt_line(tx))

    def test_unknown_action_skipped(self):
        # OPENING_BALANCE etc. have no .tt representation — caller should
        # see None and skip rather than emit malformed output.
        tx = {'action': 'OPENING_BALANCE', 'date': '2025-01-01'}
        self.assertIsNone(tx_to_tt_line(tx))


class TestRoundTrip(unittest.TestCase):
    """tt → json → tt should preserve all tx-shaped data. Round-tripping is
    the contract that protects users who use the script as a translator
    between the legacy Perl pipeline and the Python flow."""

    SAMPLE_TT = """\
BUYSELL 2025-01-15 09:30:00 AAPL.US 100.00000000 USD 150.00000000 15000.00000 5.00000
BUYSELL 2025-03-15 14:00:00 AAPL.US -50.00000000 USD 175.00000000 8750.00000 5.00000
DIVIDEND 2025-04-01 09:30:00 AAPL.US 0.00000000 USD 0.00000000 100.00000
INTEREST 2025-01-02 09:30:00 USD 385.77000
"""

    def test_tt_to_json_to_tt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tt_path = Path(tmpdir) / "in.tt"
            tt_path.write_text(self.SAMPLE_TT)
            data = tt_to_json(tt_path, 'TestAcct')
            self.assertEqual(len(data['transactions']), 4)

            # Round-trip back to tt.
            json_path = Path(tmpdir) / "mid.json"
            json_path.write_text(json.dumps(data))
            lines = list(json_to_tt_lines(json_path))
            self.assertEqual(len(lines), 4)

            # Re-parse the emitted tt and check key fields survive.
            roundtrip_path = Path(tmpdir) / "out.tt"
            roundtrip_path.write_text("\n".join(lines) + "\n")
            data2 = tt_to_json(roundtrip_path, 'TestAcct')

            for orig, rt in zip(data['transactions'], data2['transactions']):
                self.assertEqual(orig['action'], rt['action'])
                self.assertEqual(orig['date'], rt['date'])
                self.assertEqual(orig.get('symbol'), rt.get('symbol'))
                self.assertAlmostEqual(
                    float(orig.get('quantity', 0)),
                    float(rt.get('quantity', 0)),
                    places=6,
                )
                self.assertAlmostEqual(
                    float(orig.get('net_amount', 0)),
                    float(rt.get('net_amount', 0)),
                    places=4,
                )


if __name__ == '__main__':
    unittest.main()
