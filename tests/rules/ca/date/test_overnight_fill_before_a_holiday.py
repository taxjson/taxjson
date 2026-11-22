"""Re-audit-2 conformance gaps: a mutant of each rule below survived the
whole suite in the re-audit's conformance pass. Each test here fails on
that mutant and passes on the real code. Synthetic data only.

Formerly: test_fix_a2_spec_gaps
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestOvernightFillBeforeAHoliday(unittest.TestCase):
    """CA-DATE-SESSION / US-DATE-SESSION (mutant SESSION-holiday): an
    overnight-session fill trades on the next NYSE trading day — a
    holiday is skipped, not only a weekend.

    Formerly: test_fix_a2_spec_gaps.TestOvernightFillBeforeAHoliday"""

    def _one(self):
        from _support.ib_statement_rows import _parse_ib, _trade
        from _support.ib_income import _trades
        # Thursday 2026-07-02 20:30 ET; Friday 2026-07-03 is the NYSE's
        # Independence Day holiday (July 4 is a Saturday).
        _, txs, _ = _parse_ib(_trades(
            _trade('QZN', '2026-07-02, 20:30:00', 10, 10.0, -100.0,
                   cur='USD')))
        self.assertEqual(len(txs), 1)
        return txs[0]

    @rule(R.CA_DATE_SESSION)
    def test_canada(self):
        t = self._one()
        self.assertEqual((t["date"], t["date_settle"]),
                         ("2026-07-06", "2026-07-07"))

    @rule(R.US_DATE_SESSION)
    def test_usa(self):
        t = self._one()
        self.assertEqual(t["date"], "2026-07-06")


if __name__ == "__main__":
    unittest.main()
