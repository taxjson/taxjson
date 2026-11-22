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


class TestMerge2ReconcileSigned(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestMerge2ReconcileSigned"""
    def test_charge_plus_refund_nets(self):
        from taxjson.bin.taxjson_merge2 import reconcile_dividend_tax
        from taxjson.model.transaction import TaxTransaction
        desc = "AAA(US1) Cash Dividend USD 2.50 per Share (Ordinary Dividend)"
        div = TaxTransaction(action="DIVIDEND", date="2026-03-15",
                             time="09:30:00", symbol="AAA.US", quantity=100,
                             price=2.5, currency="USD", net_amount=250.0,
                             gross_amount=250.0, account="m", description=desc)
        charge = TaxTransaction(action="TAX", date="2026-03-15",
                                time="09:30:00", symbol="AAA.US", quantity=0,
                                price=0.0, currency="USD", net_amount=37.5,
                                account="m", description=desc + " - US Tax")
        refund = TaxTransaction(action="TAX", date="2026-03-15",
                                time="09:30:00", symbol="AAA.US", quantity=0,
                                price=0.0, currency="USD", net_amount=-30.0,
                                account="m", description=desc + " - US Tax")
        reconcile_dividend_tax([div, charge, refund])
        # Net tax = 7.50 → dividend net 242.50 (abs() said 182.50).
        self.assertAlmostEqual(div.net_amount, 242.50, places=2)


if __name__ == "__main__":
    unittest.main()
