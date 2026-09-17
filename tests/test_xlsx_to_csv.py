"""Tests for `taxjson-xlsx-to-csv`.

Round-trip: build a tiny xlsx with openpyxl, invoke the converter,
inspect the CSV output. Skips when the optional `xlsx` extras aren't
installed (pandas + openpyxl); they're optional because the tool is a
brokerage-CSV preprocessor for the small number of brokerages that
export only Excel."""
import csv
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import openpyxl
    import pandas as pd
    _XLSX_AVAILABLE = True
except ImportError:
    _XLSX_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_xlsx(rows, path: Path, sheet_name='Sheet1'):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for r in rows:
        ws.append(r)
    wb.save(path)


@unittest.skipUnless(_XLSX_AVAILABLE, "xlsx extras not installed (pandas + openpyxl)")
class TestXlsxToCsv(unittest.TestCase):
    def _run(self, xlsx_path, *extra):
        cmd = [sys.executable, '-m', 'taxjson.bin.xlsx_to_csv',
               str(xlsx_path), *extra]
        r = subprocess.run(cmd, cwd=REPO_ROOT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_round_trip_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / 'demo.xlsx'
            _write_xlsx([
                ['Date', 'Symbol', 'Quantity', 'Price'],
                ['2024-01-15', 'AAPL', 100, 185.00],
                ['2024-03-20', 'AAPL', 50, 170.00],
            ], xlsx)
            out = self._run(xlsx)
            rows = list(csv.reader(io.StringIO(out)))
            self.assertEqual(rows[0], ['Date', 'Symbol', 'Quantity', 'Price'])
            self.assertEqual(rows[1][:2], ['2024-01-15', 'AAPL'])
            # Numeric cells round-trip; exact whitespace/formatting
            # depends on pandas to_csv defaults but the values are
            # parseable as numbers.
            self.assertEqual(float(rows[1][2]), 100.0)
            self.assertEqual(float(rows[1][3]), 185.0)

    def test_strips_thousands_separator_commas(self):
        """The whole point of this tool: a `"18,500.00"` cell from
        the broker's pretty-printed XLSX must come out as `18500.00`
        in the CSV so the downstream parser doesn't see a malformed
        numeric. Without the stripping, the value would either get
        re-quoted with commas (breaking CSV column alignment) or be
        emitted as a 2-column garbled value."""
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / 'commas.xlsx'
            _write_xlsx([
                ['Symbol', 'Total'],
                ['AAPL', '18,500.00'],   # string with thousands sep
                ['MSFT', '2,000,000.50'],
            ], xlsx)
            out = self._run(xlsx)
            rows = list(csv.reader(io.StringIO(out)))
            # Header + two data rows; no extra columns from comma split.
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(rows[1]), 2)
            self.assertAlmostEqual(float(rows[1][1]), 18500.00, places=2)
            self.assertAlmostEqual(float(rows[2][1]), 2000000.50, places=2)

    def test_writes_to_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / 'in.xlsx'
            out_csv = Path(tmp) / 'out.csv'
            _write_xlsx([
                ['A', 'B'],
                [1, 2],
            ], xlsx)
            self._run(xlsx, '-o', str(out_csv))
            self.assertTrue(out_csv.exists())
            content = out_csv.read_text()
            self.assertIn('A,B', content)
            self.assertIn('1', content)

    def test_non_numeric_strings_pass_through_unchanged(self):
        """Description cells like a broker memo with a comma inside
        ("ACME, INC.") must NOT have their commas stripped."""
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / 'mixed.xlsx'
            _write_xlsx([
                ['Description', 'Total'],
                ['ACME, INC.', '500.00'],
            ], xlsx)
            out = self._run(xlsx)
            rows = list(csv.reader(io.StringIO(out)))
            # csv.reader correctly de-quotes "ACME, INC.".
            self.assertEqual(rows[1][0], 'ACME, INC.')
            self.assertAlmostEqual(float(rows[1][1]), 500.00, places=2)


if __name__ == '__main__':
    unittest.main()
