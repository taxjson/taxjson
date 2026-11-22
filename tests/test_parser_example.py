"""The example parser the scaffolding tool sends (TJ-2354): it parses its
own shape through the template, with the counts a converted parser gives."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.parsers.example import SampleBrokerage

_CSV = """Date,Action,Symbol,Quantity,Price,Amount,Currency
2025-03-03,BUY,SAMPLF,10,12.50,-125.00,CAD
2025-04-04,DIV,SAMPLF,0,0,3.20,CAD
2025-05-05,SELL,SAMPLF,10,13.00,130.00,CAD
2025-05-05,FEE,SAMPLF,0,0,-1.00,CAD
,TOTAL,,,,7.20,
"""


class TestSampleParser(unittest.TestCase):
    def test_the_example_parses_its_own_shape(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "sample.csv"
            f.write_text(_CSV, encoding="utf-8")
            p = SampleBrokerage()
            with redirect_stderr(io.StringIO()) as err:
                out = p.parse_file(f)
        self.assertEqual([t["action"] for t in out], ["BUYSELL", "DIVIDEND", "BUYSELL"])
        self.assertEqual([t["quantity"] for t in out if t["action"] == "BUYSELL"], [10.0, -10.0])
        self.assertTrue(all(t["symbol"] == "SAMPLF.TO" for t in out))
        self.assertEqual((p._rows_seen, p._rows_consumed), (5, 3))
        self.assertEqual(p.skip_classes(), ({"totals footer": 1}, {}, {"action FEE": 1}))
        self.assertIn("action FEE", err.getvalue())


if __name__ == "__main__":
    unittest.main()
