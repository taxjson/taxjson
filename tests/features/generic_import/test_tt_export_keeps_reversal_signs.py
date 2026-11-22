"""Regression tests for the round-2 audit fixes (holes found by the
adversarial re-audit of the tier-1/2 fixes, plus the pre-validated tier-3
engine fixes):

- Split dedup key normalizes symbol_new (IB stamps it = symbol, RBC/Questrade
  stamp '' — same event, different spelling).
- US §1091: replacement records stay in their own trade-date units, converted
  at match time (a split no longer double-scales post-split replacements; a
  split BETWEEN the loss and the replacement is handled too).
- Canada: bal_at_end / acquired_qty / loss qty are compared in loss-date
  units (a reverse split no longer shrinks the disallowance 10x).
- US engine gates tainted (phantom) losses out of §1091 like Canada does
  (phantom short lots have proceeds=0, so covering always booked bogus loss).
- synthesize_openings follows rename chains and anchors a day before first
  activity (a midnight SPLIT can no longer sort ahead of the opening).
- Radar: keeps ALL in-window losses (an older active VIOLATION survives a
  newer loss); triggers are direction-matched (no "sell more" advice on
  shorts); duplicate same-account SPLIT rows apply once.
- .tt export keeps DIVIDEND/TAX signs (reversals round-trip); merge2's
  dividend/tax reconciliation nets signed TAX legs.
- gen-phantoms year scope keeps a pair whose only in-year activity is the
  covering BUY of a phantom short.

Formerly: test_audit_round2_fixes
"""
import unittest


class TestTtExportSigned(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestTtExportSigned"""
    def test_reversal_dividend_and_tax_round_trip(self):
        from taxjson.lib.tt_format import tx_to_tt_line, parse_tt_line
        div = {"action": "DIVIDEND", "date": "2026-03-20", "time": "09:30:00",
               "symbol": "AAA.US", "quantity": -1000.0, "price": 0.25,
               "currency": "USD", "net_amount": -250.0, "gross_amount": -250.0}
        tax = {"action": "TAX", "date": "2026-03-20", "time": "09:30:00",
               "symbol": "AAA.US", "quantity": 0.0, "price": 0.0,
               "currency": "USD", "net_amount": -37.5, "gross_amount": 0.0}
        for tx, expected in ((div, -250.0), (tax, -37.5)):
            line = tx_to_tt_line(tx)
            self.assertIsNotNone(line)
            back = parse_tt_line(line, account_name="m")
            self.assertAlmostEqual(back["net_amount"], expected, places=2,
                                   msg=f"sign lost round-tripping: {line}")


if __name__ == "__main__":
    unittest.main()
