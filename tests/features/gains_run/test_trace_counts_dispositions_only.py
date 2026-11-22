"""TJ-2784: a gains run's trace file counts its dispositions only.

The run's `transactions` carry its income rows (DIVIDEND, DIVIDEND_IN_LIEU)
beside the dispositions; the header's count and the per-symbol Sales left out
only DIVIDEND, so a payment in lieu counted as a sale.

Formerly: test_fix_trace_income_rows"""
import re
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_gains import write_traces_file
from taxjson.lib.json_input import INCOME_ROW_ACTIONS, is_disposition
from taxjson.lib.pipeline import _trade_gain_total


def _rows():
    return [
        {"symbol": "SAMPLD.US", "date": "2025-11-14", "qty": 10.0, "gain": 14.85,
         "disallowed_amount": 0.0, "id": "a1"},
        {"symbol": "SAMPLD.US", "date": "2025-09-30", "action": "DIVIDEND_IN_LIEU",
         "qty": 0.0, "gain": 0.0, "id": "a2"},
        {"symbol": "SAMPLD.US", "date": "2025-06-30", "action": "DIVIDEND",
         "qty": 0.0, "gain": 0.0, "id": "a3"},
        {"symbol": "F:SAMPLMH6.US", "date": "2025-03-10", "qty": 1.0, "gain": 4.05,
         "disallowed_amount": 0.0, "id": "a4"},
    ]


class TestTheTraceCountsDispositionsOnly(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "m_gains.traces"
        write_traces_file({"transactions": _rows(), "summary": {"total_gain": 18.9}}, path,
                          country="canada", input_path="m_base.json", year="2025",
                          tax_date_basis="settle")
        self.text = path.read_text(encoding="utf-8")

    def test_the_header_counts_two_dispositions(self):
        self.assertRegex(self.text, r"# dispositions +2\n")

    def test_the_per_symbol_table_counts_one_sale_of_sampld(self):
        sales = {m.group(1): int(m.group(2))
                 for m in re.finditer(r"^# (\S+) +(\d+) +[\d.,]+ ", self.text, re.M)}
        self.assertEqual(sales, {"F:SAMPLMH6.US": 1, "SAMPLD.US": 1})
        self.assertRegex(self.text, r"# TOTAL +2 ")


class TestWhatADispositionIs(unittest.TestCase):
    def test_the_income_rows_are_not(self):
        self.assertEqual(set(INCOME_ROW_ACTIONS), {"DIVIDEND", "DIVIDEND_IN_LIEU"})
        self.assertEqual([is_disposition(r) for r in _rows()], [True, False, False, True])

    def test_the_total_takes_the_dispositions(self):
        rows = _rows()
        rows[1]["gain"] = 5.0          # an income row's gain never reaches the total
        self.assertAlmostEqual(_trade_gain_total(rows), 18.9)


if __name__ == "__main__":
    unittest.main()
