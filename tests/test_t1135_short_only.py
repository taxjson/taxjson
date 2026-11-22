"""A position held only short in the year is not a T1135 property row
(tax-logic CA-RPT-19): a written option bought back or left to expire,
a short sale covered. It is named; a position held long at any time in
the year keeps its row.

Every fixture is SYNTHETIC: invented tickers (QZ*), amounts under 1,000.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.bin.taxjson_t1135 import render_brief, render_report
from taxjson.lib import out as _out
from _support.ca_t1135_walk import _r, _report, _walk

CALL = "QZA250620C00050000.US"


def _gain(symbol, date, gain, qty=1):
    return {"action": "BUYSELL", "symbol": symbol, "date": date,
            "date_settle": date, "account": "margin", "qty": qty,
            "gain": gain}


def _gains(*rows):
    return {"margin_gains_wash.json": {"transactions": list(rows)}}


class TestHeldOnlyShort(unittest.TestCase):

    @rule(R.CA_RPT_19)
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

    @rule(R.CA_RPT_19)
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

    @rule(R.CA_RPT_19)
    def test_a_position_held_long_at_any_time_keeps_its_row(self):
        rep = _report([_r("BUYSELL", "2025-02-03", "QZL.US", 10, -500.0),
                       _r("BUYSELL", "2025-03-03", "QZL.US", -20, 1200.0),
                       _r("BUYSELL", "2025-04-01", "QZL.US", 10, -500.0)],
                      _gains(_gain("QZL.US", "2025-03-03", 100.0),
                             _gain("QZL.US", "2025-04-01", 100.0)))
        self.assertEqual([(p["symbol"], p["gain"]) for p in rep["properties"]],
                         [("QZL.US", 200.0)])
        self.assertEqual(rep["short_only_symbols"], [])

    @rule(R.CA_RPT_19)
    def test_a_short_beside_a_waiting_denied_loss_is_still_held_only_short(self):
        """TJ-3284: the OLDX.US loss (sold 2024-12-15, rebought 12-17,
        sold out 12-27) waits on the flat pool for the opening balance of
        NEWX.US, which a rename brings it into (2025-01-07, 01-08); the
        account is short OLDX.US, coded O, from 01-02 to 01-06 (+100 on
        the cover). The short is beside the wait, in the timeline's
        `shorts` part, not on the symbol's row (backlog TJ-2712): OLDX.US
        is still held only short in 2025 and its cover is no property
        row; NEWX.US keeps its row."""
        rows = [_r("BUYSELL", "2024-11-01", "OLDX.US", 100, -2000.0),
                _r("BUYSELL", "2024-12-15", "OLDX.US", -100, 1000.0),
                _r("BUYSELL", "2024-12-17", "OLDX.US", 100, -1000.0),
                _r("BUYSELL", "2024-12-27", "OLDX.US", -100, 1000.0),
                _r("BUYSELL", "2025-01-02", "OLDX.US", -100, 1000.0,
                   open_close="O"),
                _r("BUYSELL", "2025-01-06", "OLDX.US", 100, -900.0,
                   open_close="C"),
                _r("SPLIT", "2025-01-07", "OLDX.US", 1, 0.0,
                   symbol_new="NEWX.US"),
                _r("OPENING_BALANCE", "2025-01-08", "NEWX.US", 100, 0.0,
                   price=0.0),
                _r("BUYSELL", "2025-06-02", "NEWX.US", -100, 1200.0)]
        w = _walk(rows)
        self.assertEqual(w["short_only_symbols"], ["OLDX.US"])
        self.assertEqual(sorted(w["per_symbol"]), ["NEWX.US"])
        rep = _report(rows, _gains(_gain("OLDX.US", "2025-01-06", 100.0,
                                         qty=100),
                                   _gain("NEWX.US", "2025-06-02", 200.0,
                                         qty=-100)))
        self.assertEqual([(p["symbol"], p["gain"]) for p in rep["properties"]],
                         [("NEWX.US", 200.0)])
        self.assertEqual(rep["short_only_symbols"], ["OLDX.US"])
        self.assertEqual(rep["by_country"]["USA"]["gain"], 200.0)

    @rule(R.CA_RPT_19)
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
