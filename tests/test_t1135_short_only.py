"""A position held only short in the year is not a T1135 property row
(tax-logic CA-RPT-19): a written option bought back or left to expire,
a short sale covered. It is named; a position held long at any time in
the year keeps its row.

Every fixture is SYNTHETIC: invented tickers (QZ*), amounts under 1,000.
"""
import unittest

from tax_rules import rule
from taxjson.bin.taxjson_t1135 import render_brief, render_report
from taxjson.lib import out as _out
from test_fix_a2_pins2_t1135 import _r, _report, _walk

CALL = "QZA250620C00050000.US"


def _gain(symbol, date, gain, qty=1):
    return {"action": "BUYSELL", "symbol": symbol, "date": date,
            "date_settle": date, "account": "margin", "qty": qty,
            "gain": gain}


def _gains(*rows):
    return {"margin_gains_wash.json": {"transactions": list(rows)}}


class TestHeldOnlyShort(unittest.TestCase):

    @rule("CA-RPT-19")
    def test_a_written_option_bought_back_is_not_a_row(self):
        rep = _report([_r("BUYSELL", "2025-02-03", CALL, -1, 300.0),
                       _r("BUYSELL", "2025-03-03", CALL, 1, -100.0),
                       _r("BUYSELL", "2025-02-03", "QZA.US", 10, -500.0)],
                      _gains(_gain(CALL, "2025-03-03", 200.0)))
        self.assertEqual([p["symbol"] for p in rep["properties"]],
                         ["QZA.US"])
        self.assertEqual(rep["short_only_symbols"], [CALL])
        self.assertEqual(rep["by_country"]["USA"]["gain"], 0.0)
        brief = render_brief(rep)
        act = [ln for ln in _out.act_lines(brief) if CALL in ln]
        self.assertEqual(len(act), 1)
        self.assertIn("held only short: an obligation, not a row", act[0])
        self.assertLessEqual(len(act[0]), 100)
        self.assertIn("Held only short in 2025 (" + CALL + ")",
                      " ".join(render_report(rep).split()))

    @rule("CA-RPT-19")
    def test_a_short_sale_covered_and_one_written_last_year(self):
        # The short sale opens and closes in the year; the option was
        # written in 2024 and bought back in 2025 (the Jan-1 state).
        rep = _report([_r("BUYSELL", "2025-04-01", "QZS.US", -10, 900.0),
                       _r("BUYSELL", "2025-05-01", "QZS.US", 10, -700.0),
                       _r("BUYSELL", "2024-11-04", CALL, -2, 600.0),
                       _r("BUYSELL", "2025-02-03", CALL, 2, -200.0)],
                      _gains(_gain("QZS.US", "2025-05-01", 200.0),
                             _gain(CALL, "2025-02-03", 400.0)))
        self.assertEqual(rep["properties"], [])
        self.assertEqual(rep["short_only_symbols"], [CALL, "QZS.US"])
        self.assertEqual(rep["max_total_cost"], 0.0)

    @rule("CA-RPT-19")
    def test_a_position_held_long_at_any_time_keeps_its_row(self):
        rep = _report([_r("BUYSELL", "2025-02-03", "QZL.US", 10, -500.0),
                       _r("BUYSELL", "2025-03-03", "QZL.US", -20, 1200.0),
                       _r("BUYSELL", "2025-04-01", "QZL.US", 10, -500.0)],
                      _gains(_gain("QZL.US", "2025-03-03", 100.0),
                             _gain("QZL.US", "2025-04-01", 100.0)))
        self.assertEqual([(p["symbol"], p["gain"]) for p in rep["properties"]],
                         [("QZL.US", 200.0)])
        self.assertEqual(rep["short_only_symbols"], [])

    @rule("CA-RPT-19")
    def test_a_rename_keeps_the_long_history(self):
        w = _walk([_r("BUYSELL", "2025-02-03", "QZO.US", 10, -500.0),
                   _r("SPLIT", "2025-03-03", "QZO.US", 1, 0.0,
                      symbol_new="QZQ.US"),
                   _r("BUYSELL", "2025-04-01", "QZQ.US", -20, 1200.0),
                   _r("BUYSELL", "2025-05-01", "QZQ.US", 10, -500.0),
                   _r("BUYSELL", "2025-06-02", "QZR.US", -5, 400.0),
                   _r("SPLIT", "2025-07-02", "QZR.US", 1, 0.0,
                      symbol_new="QZT.US"),
                   _r("BUYSELL", "2025-08-01", "QZT.US", 5, -300.0)])
        self.assertEqual(w["short_only_symbols"], ["QZR.US", "QZT.US"])


if __name__ == "__main__":
    unittest.main()
