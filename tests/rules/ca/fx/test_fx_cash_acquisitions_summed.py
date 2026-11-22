"""Re-audit-2 test pins (tests-pins-04/05/06, reports group): labels and
accumulators that held at the audit but that no test failed on when
reverted. Each test names the finding and the mutant it kills.

  A2-0880 / A2-1585  fees-sum's per-brokerage 'non-option' line, its
                     $/UNIT header and 'Non-option vs option fees:'
                     heading; `tjs sum`'s NON-OPT column; the tx
                     views' 'TOTAL STAKING (crypto, ordinary income):'
  A2-1555            fx-cash's per-currency ACQUIRED column sums
  A2-0533            `tjs sum`'s summarize_gains / summarize_income
                     accumulators (2+ rows per ticker and term, 2
                     withholding rows, an 'other' row, all-zero rows)
  A2-0861 / A2-0906 / A2-0907
                     run_gains: the Canadian stock-dividend line and the
                     Canadian option summary keys stay out of a US book;
                     no note for a plain purchase; fees after a non-trade
                     row; by_ticker after a tainted row and with two
                     dividends; a manual-reporting row has no gain/cost
  A2-0865 / A2-0866 / A2-1532
                     checklist: a stale wash file makes audit and
                     form-export 'attention'; the t5-t3 step names the
                     TAXABLE line; the US slip and margin-interest words

All data is synthetic.

Formerly: test_fix_a2_pins2_reports
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


# ------------------------------------------------------------- fx-cash
class TestFxCashAcquired(unittest.TestCase):
    """A2-1555: `stat["acquired"] += flow` — two acquisitions of US
    dollars in the year both count.

    Formerly: test_fix_a2_pins2_reports.TestFxCashAcquired"""

    @rule(R.CA_FX_07)
    def test_two_acquisitions_sum(self):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        from _support.fx_cash_rows import _rate_of, _tx
        txs = [_tx("DIVIDEND", "2026-01-10", "USD", 1000.0,
                   gross_amount=1000.0),
               _tx("BUYSELL", "2026-02-10", "USD", 500.0, qty=-5.0),
               _tx("BUYSELL", "2026-03-10", "USD", -300.0, qty=3.0)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        usd = doc["per_currency"]["USD"]
        self.assertAlmostEqual(usd["acquired"], 1500.0)
        self.assertAlmostEqual(usd["disposed"], 300.0)


if __name__ == "__main__":
    unittest.main()
