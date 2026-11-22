"""Workflow audit 2026-10-22, batch 2: what the views claim
(~/workflow-audit-2026-10-22.md is the maintainer's report).

- Item 5: a sale of more shares than held, the engine closing part of it
  against a long, is NOT in the totals for the units it left short.
- Item 10: the checklist flags option_grant_timing_since only where
  option-boundary (and the run) warn: the books hold a written option.
- Item 12: an empty crypto (or other) account left from the scaffold:
  option-boundary and the checklist name the empty folder, never "run
  `tjs run`" (the run skips it, so the advice looped).
- Item 15: outside a project every command says there is none and how
  to start one or where to `cd`.

Every fixture is SYNTHETIC: account 99900001, made-up tickers ZZ*.

Formerly: test_fix_audit_1022_b
"""
import json
import tempfile
import unittest
from pathlib import Path


class TestEngineBookingWholeSale(unittest.TestCase):
    """Item 5, the unit: a 50-unit sale the engine closed 40 against a
    long position left 10 short — not "matched".

    Formerly: test_fix_audit_1022_b.TestEngineBookingWholeSale"""

    def _row(self, sales):
        from taxjson.lib import missing_history as MH
        c = MH.MissingHistoryCandidate(
            symbol="ZZOV.TO", account="margin", currency="CAD",
            first_negative_date="", peak_short=-10.0, end_position=-10.0,
            disposition_count=1, registered=False)
        return MH.MissingHistoryRow(candidate=c, affects_year=True,
                                    in_year_dispositions=1,
                                    in_year_proceeds=0.0,
                                    last_in_year_date="",
                                    in_year_short_sales=tuple(sales))

    def _booking(self, sales, long_qty):
        from taxjson.lib import first_run as FR
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "margin_gains.json").write_text(json.dumps({
                "transactions": [{"id": "s1", "symbol": "ZZOV.TO",
                                  "qty": long_qty, "direction": "LONG",
                                  "date": "2025-05-12",
                                  "date_settle": "2025-05-13"}],
                "inventory": []}))
            return FR.engine_booking(Path(td), [self._row(sales)], 2025)[
                ("ZZOV.TO", "margin")]

    def test_part_left_short_is_open(self):
        from taxjson.lib import first_run as FR
        self.assertEqual(self._booking([("s1", 10.0, 50.0)], 40),
                         FR.BOOKED_OPEN)

    def test_whole_sale_closed_long_is_matched(self):
        from taxjson.lib import first_run as FR
        # The engine read the day in another order: all 50 against longs.
        self.assertEqual(self._booking([("s1", 10.0, 50.0)], 50),
                         FR.BOOKED_MATCHED)


if __name__ == "__main__":
    unittest.main()
